#!/bin/bash
# Check current price data freshness in Redis

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║              Price Data Freshness Check                      ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check Redis
echo "1. Checking Redis connection..."
redis-cli ping > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "❌ Redis not running"
    echo "   Run: redis-server"
    exit 1
fi
echo "✓ Redis is running"
echo ""

# Check BTC data
echo "2. Checking BTC price data..."
echo ""

echo "Bybit Spot BTC:"
echo "  LTP: $(redis-cli HGET bybit_spot:BTC ltp)"
echo "  Timestamp: $(redis-cli HGET bybit_spot:BTC timestamp)"
echo ""

echo "CoinDCX Futures BTC:"
echo "  LTP: $(redis-cli HGET coindcx_futures:BTC ltp)"
echo "  Timestamp: $(redis-cli HGET coindcx_futures:BTC timestamp)"
echo "  Current Funding: $(redis-cli HGET coindcx_futures:BTC current_funding_rate)"
echo ""

# Check monitoring system
echo "3. Checking monitoring system..."
if pgrep -f "crypto_monitor_launcher" > /dev/null; then
    PID=$(pgrep -f "crypto_monitor_launcher")
    echo "✓ Monitoring system is RUNNING (PID: $PID)"
else
    echo "❌ Monitoring system is NOT running"
    echo ""
    echo "Start it with:"
    echo "  ./start_prerequisites.sh"
    echo ""
    echo "Or manually:"
    echo "  cd ../ltp_redis_fetcher/funding_profit_inr_ltp"
    echo "  python3 crypto_monitor_launcher.py &"
fi
echo ""

# Calculate age
echo "4. Calculating data freshness..."
BYBIT_TS=$(redis-cli HGET bybit_spot:BTC timestamp)
if [ -n "$BYBIT_TS" ]; then
    echo "✓ Bybit data timestamp: $BYBIT_TS"

    # Python one-liner to calculate age
    AGE=$(python3 -c "
from datetime import datetime
import sys
try:
    ts_str = '$BYBIT_TS'.replace('Z', '+00:00')
    ts = datetime.fromisoformat(ts_str)
    now = datetime.now(ts.tzinfo)
    age = (now - ts).total_seconds()
    print(f'{age:.1f}')
except Exception as e:
    print('error')
" 2>/dev/null)

    if [ "$AGE" != "error" ]; then
        echo "  Age: ${AGE}s"

        # Check if fresh
        if (( $(echo "$AGE < 10" | bc -l) )); then
            echo "  ✓ Data is FRESH (< 10 seconds)"
        elif (( $(echo "$AGE < 60" | bc -l) )); then
            echo "  ⚠️  Data is slightly old but usable (< 60 seconds)"
        else
            echo "  ❌ Data is STALE (> 60 seconds)"
            echo "     The monitoring system may need restart"
        fi
    fi
else
    echo "❌ No Bybit data found in Redis"
fi
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "If data is stale:"
echo "  1. Check monitoring logs: tail -f ../ltp_redis_fetcher/funding_profit_inr_ltp/crypto_monitor_launcher.log"
echo "  2. Restart monitoring: pkill -f crypto_monitor && ./start_prerequisites.sh"
echo "  3. Wait 10-15 seconds for fresh data"
