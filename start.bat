@echo off
REM Telegram Media Analytics — Startup Script (Windows)

echo ==========================================
echo   Telegram Media Analytics — Setup ^& Run
echo ==========================================
echo.

REM Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ❌ Python not found!
    echo    Install Python 3.9+ from https://www.python.org/downloads/
    echo    IMPORTANT: Check "Add Python to PATH" during installation!
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PYVER=%%i
echo ✅ Python %PYVER% found

REM Check pip
python -m pip --version >nul 2>nul
if %errorlevel% neq 0 (
    echo ❌ pip not found!
    echo    Run: python -m ensurepip --upgrade
    pause
    exit /b 1
)

REM Create venv if not exists
if not exist ".venv" (
    echo.
    echo 📦 Creating virtual environment...
    python -m venv .venv
    echo ✅ Virtual environment created
)

REM Activate venv
call .venv\Scripts\activate.bat

REM Install dependencies
echo.
echo 📦 Installing dependencies...
set PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
pip install --no-cache-dir -q -r requirements.txt
echo ✅ Dependencies installed

REM Create .env if not exists
if not exist ".env" (
    echo.
    echo ⚠️  .env file not found!
    echo    Creating .env from template...
    (
        echo # Telegram API credentials
        echo # Get them at https://my.telegram.org
        echo TG_API_ID=your_api_id_here
        echo TG_API_HASH=your_api_hash_here
        echo TG_PHONE=your_phone_number_here
        echo.
        echo # JWT secret key ^(change this in production^)
        echo PLACEOFPOWER_SECRET_KEY=change-this-to-a-random-string
    ) > .env
    echo ✅ .env created
    echo.
    echo 📝 IMPORTANT: Edit .env file with your Telegram API credentials!
    echo    Get API credentials at: https://my.telegram.org
    echo.
    echo    Press Enter after editing .env to continue...
    pause
)

REM Check if .env has real credentials
findstr /C:"your_api_id_here" .env >nul 2>nul
if %errorlevel% equ 0 (
    echo.
    echo ❌ Please edit .env file first!
    echo    Open .env in Notepad and fill in your credentials.
    echo.
    echo    Press Enter to open .env for editing...
    pause
    notepad .env
)

REM Start the app
echo.
echo 🚀 Starting Telegram Media Analytics...
echo    Open http://localhost:8000/telegram/web in your browser
echo    Press Ctrl+C to stop
echo.
python main.py
pause
