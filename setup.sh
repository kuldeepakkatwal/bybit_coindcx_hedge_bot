#!/bin/bash
# Setup script for Delta-Neutral Hedge Trading Bot

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                                                               ║"
echo "║    Delta-Neutral Hedge Trading Bot - Setup Script            ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check Python version
echo "1. Checking Python version..."
python3 --version
if [ $? -ne 0 ]; then
    echo "❌ Python 3 not found. Please install Python 3.8 or higher."
    exit 1
fi
echo "✓ Python found"
echo ""

# Check Redis
echo "2. Checking Redis..."
redis-cli ping > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "⚠️  Redis not running. Starting Redis..."
    redis-server --daemonize yes
    sleep 2
    redis-cli ping > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo "❌ Failed to start Redis. Please install and start Redis manually."
        echo "   macOS: brew install redis && redis-server"
        echo "   Ubuntu: sudo apt install redis-server && sudo service redis-server start"
    else
        echo "✓ Redis started"
    fi
else
    echo "✓ Redis is running"
fi
echo ""

# Check PostgreSQL (optional)
echo "3. Checking PostgreSQL (optional)..."
psql --version > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "⚠️  PostgreSQL not found (optional - bot can run without it)"
else
    echo "✓ PostgreSQL found"
fi
echo ""

# Install dependencies
echo "4. Installing Python dependencies..."
pip3 install psycopg2-binary > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✓ Dependencies installed"
else
    echo "⚠️  Some dependencies may need manual installation"
    echo "   Run: pip3 install -r requirements.txt"
fi
echo ""

# Check for .env file
echo "5. Checking configuration..."
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found"
    echo "   Copying .env.example to .env..."
    cp .env.example .env
    echo "✓ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env and add your API credentials!"
    echo "   Required variables:"
    echo "   - BYBIT_API_KEY"
    echo "   - BYBIT_API_SECRET"
    echo "   - COINDCX_API_KEY"
    echo "   - COINDCX_API_SECRET"
else
    echo "✓ .env file exists"
fi
echo ""

# Make scripts executable
echo "6. Setting permissions..."
chmod +x main.py
chmod +x tests/test_bot.py
chmod +x setup.sh
echo "✓ Permissions set"
echo ""

# Run tests
echo "7. Running tests..."
echo "   Would you like to run the test suite? (y/n)"
read -r response
if [[ "$response" == "y" || "$response" == "Y" ]]; then
    echo ""
    python3 tests/test_bot.py
    echo ""
fi

# Summary
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    SETUP COMPLETE                             ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your API credentials"
echo "2. Ensure Redis is running for price data"
echo "3. Run tests: python3 tests/test_bot.py"
echo "4. Start bot: python3 main.py"
echo ""
echo "For help, see README.md"
