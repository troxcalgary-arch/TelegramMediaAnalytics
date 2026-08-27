import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.session_manager import (
    SessionBusyError,
    SingleProcessGuard,
    TelegramSessionManager,
)


class FakeService:
    instances = []

    def __init__(self, api_id, api_hash, phone, session_name):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_name = session_name
        self.disconnect_calls = 0
        self.__class__.instances.append(self)

    async def disconnect(self):
        self.disconnect_calls += 1


class FailingDisconnectService(FakeService):
    async def disconnect(self):
        self.disconnect_calls += 1
        raise RuntimeError("disconnect failed")


class TelegramSessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeService.instances.clear()
        self.manager = TelegramSessionManager(service_factory=FakeService)

    async def asyncTearDown(self):
        await self.manager.close_all()

    async def test_busy_session_is_rejected_and_reused_after_release(self):
        first = await self.manager.reserve("account", 1, "hash", "+1", "scan:1")

        with self.assertRaises(SessionBusyError):
            await self.manager.reserve("account", 1, "hash", "+1", "download:2")

        await self.manager.release("account", "scan:1")
        second = await self.manager.reserve("account", 1, "hash", "+1", "download:2")

        self.assertIs(first, second)
        self.assertEqual(1, len(FakeService.instances))

    async def test_different_sessions_can_be_reserved_concurrently(self):
        first, second = await asyncio.gather(
            self.manager.reserve("account-a", 1, "a", "+1", "task:a"),
            self.manager.reserve("account-b", 2, "b", "+2", "task:b"),
        )

        self.assertIsNot(first, second)
        self.assertEqual(2, len(FakeService.instances))

    async def test_simultaneous_reservations_allow_exactly_one_owner(self):
        results = await asyncio.gather(
            self.manager.reserve("account", 1, "hash", "+1", "task:a"),
            self.manager.reserve("account", 1, "hash", "+1", "task:b"),
            return_exceptions=True,
        )

        self.assertEqual(1, sum(isinstance(item, FakeService) for item in results))
        self.assertEqual(1, sum(isinstance(item, SessionBusyError) for item in results))

    async def test_changed_credentials_replace_idle_service(self):
        first = await self.manager.reserve("account", 1, "old", "+1", "auth:1")
        await self.manager.release("account", "auth:1")

        second = await self.manager.reserve("account", 2, "new", "+1", "auth:2")

        self.assertIsNot(first, second)
        self.assertGreaterEqual(first.disconnect_calls, 2)

    async def test_wrong_owner_cannot_release_active_session(self):
        await self.manager.reserve("account", 1, "hash", "+1", "task:right")
        await self.manager.release("account", "task:wrong")

        with self.assertRaises(SessionBusyError):
            await self.manager.reserve("account", 1, "hash", "+1", "task:next")

    async def test_disconnect_failure_still_releases_lock(self):
        manager = TelegramSessionManager(service_factory=FailingDisconnectService)
        await manager.reserve("account", 1, "hash", "+1", "task:first")
        with self.assertLogs("app.services.session_manager", level="ERROR"):
            await manager.release("account", "task:first")

        service = await manager.reserve("account", 1, "hash", "+1", "task:next")
        self.assertIsInstance(service, FailingDisconnectService)


class SingleProcessGuardTests(unittest.TestCase):
    def test_multiple_configured_workers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = SingleProcessGuard(Path(directory) / "process.lock")
            with patch.dict("os.environ", {"WEB_CONCURRENCY": "2"}):
                with self.assertRaises(RuntimeError):
                    guard.acquire()

    def test_second_guard_is_rejected_until_first_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "process.lock"
            first = SingleProcessGuard(path)
            second = SingleProcessGuard(path)
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            second.release()


if __name__ == "__main__":
    unittest.main()
