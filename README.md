# Telegram Media Analytics

Web-based application for analyzing and downloading media from Telegram channels. Built with FastAPI + Telethon + SQLite.

## Features

- **Channel Scanning** — scan channels, groups, and forum topics for media files
- **Media Analytics** — statistics by author (video count, total size, last upload)
- **Bulk Download** — organized by channel → username with metadata files
- **Forum Topics** — support for topic IDs (e.g., `-1001911644885_1194`)
- **JWT Authentication** — persistent sessions with localStorage
- **Scan History** — track all scan operations with timestamps
- **Progress Overlays** — real-time progress bars with blur backdrop
- **Resizable Tables** — drag-to-resize columns with localStorage persistence

## Tech Stack

- Python 3.14, FastAPI, Telethon 1.44.0
- SQLAlchemy + SQLite
- JWT (python-jose), passlib
- Vanilla JS + flatpickr

## Quick Start

```bash
# Clone the repository
git clone https://github.com/troxcalgary-arch/TelegramMediaAnalytics.git
cd TelegramMediaAnalytics

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Telegram API credentials

# Run the application
python main.py
```

The app will be available at `http://localhost:8000/telegram/web`

## Environment Variables

Create a `.env` file with:

```
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
TG_PHONE=+your_phone_number
PLACEOFPOWER_SECRET_KEY=your_jwt_secret
```

Get API credentials at https://my.telegram.org

## Project Structure

```
app/
  main.py              — FastAPI application
  database.py          — SQLAlchemy engine
  routers/
    telegram.py        — API endpoints
  services/
    telegram_service.py — Telethon integration
  models/
    auth_models.py     — JWT, users
  templates/
    telegram.html      — main page
    history.html       — scan history page
  static/
    app.js             — frontend logic
    style.css          — styles
    bg.jpg             — background image
```

## License

MIT
