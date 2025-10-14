#!/bin/bash
# Start all prerequisites for the hedge trading bot

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                                                               ║"
echo "║    Starting Prerequisites for Hedge Trading Bot              ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Get the absolute path to the project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

echo "Project root: $PROJECT_ROOT"
echo ""

# Step 1: Check Redis
echo "1. Checking Redis..."
redis-cli ping > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "⚠️  Redis not running. Starting Redis..."
    redis-server --daemonize yes
    sleep 2
    redis-cli ping > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo "✓ Redis started"
    else
        echo "❌ Failed to start Redis"
        exit 1
    fi
else
    echo "✓ Redis is already running"
fi
echo ""

# Step 2: Start Price Monitoring System
echo "2. Starting Price Monitoring System..."
MONITOR_DIR="$PROJECT_ROOT/ltp_redis_fetcher/funding_profit_inr_ltp"

if [ ! -d "$MONITOR_DIR" ]; then
    echo "❌ Error: Monitoring system directory not found at:"
    echo "   $MONITOR_DIR"
    echo ""
    echo "   Please check the path and try again."
    exit 1
fi

echo "   Monitor directory: $MONITOR_DIR"
echo "   Starting crypto_monitor_launcher.py..."

cd "$MONITOR_DIR"

# Check if monitoring is already running
if pgrep -f "crypto_monitor_launcher.py" > /dev/null; then
    echo "✓ Price monitoring system is already running"
else
    # Start monitoring system in background
    nohup python3 crypto_monitor_launcher.py > crypto_monitor.log 2>&1 &
    MONITOR_PID=$!

    echo "   Waiting for monitoring system to start..."
    sleep 3

    # Check if it's running
    if ps -p $MONITOR_PID > /dev/null; then
        echo "✓ Price monitoring system started (PID: $MONITOR_PID)"
        echo "   Log file: $MONITOR_DIR/crypto_monitor.log"
    else
        echo "❌ Failed to start price monitoring system"
        echo "   Check log: $MONITOR_DIR/crypto_monitor.log"
        exit 1
    fi
fi
echo ""

# Step 3: Wait for fresh price data
echo "3. Waiting for fresh price data..."
echo "   This may take 10-15 seconds..."
sleep 10

# Check if we have fresh data
REDIS_TEST=$(redis-cli HGET bybit_spot:BTC ltp 2>/dev/null)
if [ -n "$REDIS_TEST" ]; then
    echo "✓ Price data available in Redis"
    echo "   BTC price: $REDIS_TEST"
else
    echo "⚠️  Price data not yet available. Please wait a few more seconds."
fi
echo ""

# Step 4: Summary
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                  PREREQUISITES READY                          ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "✓ Redis is running"
echo "✓ Price monitoring system is running"
echo ""
echo "You can now run the bot:"
echo "  cd $SCRIPT_DIR"
echo "  python3 main.py"
echo ""
echo "To check monitoring system logs:"
echo "  tail -f $MONITOR_DIR/crypto_monitor_launcher.log"
echo ""
echo "To stop monitoring system:"
echo "  pkill -f crypto_monitor_launcher"
