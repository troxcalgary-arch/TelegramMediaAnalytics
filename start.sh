#!/bin/bash
# Telegram Media Analytics — Startup Script (Linux/macOS)

set -e

echo "=========================================="
echo "  Telegram Media Analytics — Setup & Run"
echo "=========================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found!"
    echo "   Install Python 3.9+ from https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Python $PYTHON_VERSION found"

# Check pip
if ! python3 -m pip --version &> /dev/null; then
    echo "❌ pip not found!"
    echo "   Run: python3 -m ensurepip --upgrade"
    exit 1
fi

# Create venv if not exists
if [ ! -d ".venv" ]; then
    echo ""
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
    echo "✅ Virtual environment created"
fi

# Activate venv
source .venv/bin/activate

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
pip install --no-cache-dir -q -r requirements.txt
echo "✅ Dependencies installed"

# Create .env if not exists
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  .env file not found!"
    echo "   Creating .env from template..."
    cat > .env << 'EOF'
# Telegram API credentials
# Get them at https://my.telegram.org
TG_API_ID=your_api_id_here
TG_API_HASH=your_api_hash_here
TG_PHONE=your_phone_number_here

# JWT secret key (change this in production)
PLACEOFPOWER_SECRET_KEY=change-this-to-a-random-string
EOF
    echo "✅ .env created"
    echo ""
    echo "📝 IMPORTANT: Edit .env file with your Telegram API credentials!"
    echo "   Get API credentials at: https://my.telegram.org"
    echo ""
    echo "   Press Enter after editing .env to continue..."
    read -r
fi

# Check if .env has real credentials
if grep -q "your_api_id_here" .env 2>/dev/null; then
    echo ""
    echo "❌ Please edit .env file first!"
    echo "   Open .env in any text editor and fill in your credentials."
    echo ""
    echo "   Press Enter to open .env for editing..."
    read -r
    if command -v nano &> /dev/null; then
        nano .env
    elif command -v vim &> /dev/null; then
        vim .env
    else
        echo "   Open .env manually and edit it."
    fi
fi

# Start the app
echo ""
echo "🚀 Starting Telegram Media Analytics..."
echo "   Open http://localhost:8000/telegram/web in your browser"
echo "   Press Ctrl+C to stop"
echo ""
python main.py
