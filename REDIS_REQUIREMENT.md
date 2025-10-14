# Redis Price Feed Requirement

**Package Type:** Minimal Bot (Price Feed Writers NOT Included)

---

## Overview

This hedge trading bot package contains **ONLY the trading bot** and price **readers**. You must have your own price feed service writing data to Redis.

### What's Included

✅ **Trading Bot** - Complete hedge trading system
✅ **Price Readers** - `LTP_fetch.py` + `crypto_data_retriever.py`
✅ **Exchange Clients** - Bybit + CoinDCX API wrappers
❌ **Price Feed Writers** - NOT included (you provide these)

---

## Redis Architecture

The bot expects a **separate price feed service** to be running that writes price data to Redis.

```
┌────────────────────────────────────────────────────┐
│  YOUR PRICE FEED SERVICE (Not Included)           │
│  - Fetches prices from Bybit WebSocket/API        │
│  - Fetches prices from CoinDCX WebSocket/API      │
│  - Writes to Redis (localhost:6379)               │
└────────────────────────────────────────────────────┘
                        │
                        ▼ (Redis)
              localhost:6379, db=0
                        │
                        ▼
┌────────────────────────────────────────────────────┐
│  THIS BOT (Included)                               │
│  - Reads prices from Redis via LTP_fetch.py       │
│  - Executes hedge trades                           │
│  - Manages orders                                  │
└────────────────────────────────────────────────────┘
```

---

## Redis Data Structure Expected

Your price feed service must write data in this format to Redis:

### Key Format
```
crypto:BTC:ltp
crypto:ETH:ltp
crypto:SOL:ltp
```

### Value Format (JSON)
```json
{
    "symbol": "BTC",
    "timestamp": "2025-10-13 16:45:30",
    "success": true,
    "bybit_data": {
        "ltp": 114892.7,
        "timestamp": "2025-10-13 16:45:29"
    },
    "coindcx_data": {
        "ltp": 114850.2,
        "timestamp": "2025-10-13 16:45:30",
        "current_funding_rate": 0.0001,
        "estimated_funding_rate": 0.00012,
        "funding_timestamp": "2025-10-13 16:00:00"
    }
}
```

### Required Fields

**Minimum required for bot to work:**
- `success`: `true` (boolean)
- `bybit_data.ltp`: Price as float
- `bybit_data.timestamp`: ISO timestamp string
- `coindcx_data.ltp`: Price as float
- `coindcx_data.timestamp`: ISO timestamp string

**Optional (bot will handle if missing):**
- `coindcx_data.current_funding_rate`: Float (for future features)
- `coindcx_data.estimated_funding_rate`: Float (for future features)
- `coindcx_data.funding_timestamp`: ISO timestamp string

---

## How the Bot Reads Prices

### Code Flow

```python
from price_feed.LTP_fetch import get_crypto_ltp

# Bot calls this function
data = get_crypto_ltp('BTC')

# Returns dictionary:
{
    'success': True,
    'bybit_data': {'ltp': 114892.7, 'timestamp': '...'},
    'coindcx_data': {'ltp': 114850.2, 'timestamp': '...'}
}
```

### Reader Implementation

The bot includes these files in `price_feed/`:
- **`LTP_fetch.py`** - Main function to fetch from Redis
- **`crypto_data_retriever.py`** - Redis connection helper
- **`__init__.py`** - Package marker

These files **READ from Redis** but do NOT write prices.

---

## Redis Connection Configuration

### Default Configuration

```python
# crypto_data_retriever.py expects:
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0
```

### If Your Redis Is Different

If your Redis runs on a different host/port, edit `crypto_data_retriever.py`:

```python
class CryptoDataRetriever:
    def __init__(self,
                 host='YOUR_REDIS_HOST',      # Change this
                 port=YOUR_REDIS_PORT,         # Change this
                 db=YOUR_REDIS_DB,             # Change this
                 password=None):
        # ...
```

---

## Verification Steps

Before running the trading bot, verify your price feed is working:

### 1. Check Redis is Running

```bash
redis-cli ping
# Expected: PONG
```

### 2. Check Price Data Exists

```bash
redis-cli GET crypto:BTC:ltp
redis-cli GET crypto:ETH:ltp
redis-cli GET crypto:SOL:ltp
# Should return JSON strings
```

### 3. Test Bot's Price Reader

```bash
cd hedge_trade_standalone
python3 -c "
from price_feed.LTP_fetch import get_crypto_ltp
import json
data = get_crypto_ltp('BTC')
print(json.dumps(data, indent=2))
"
```

**Expected output:**
```json
{
  "success": true,
  "symbol": "BTC",
  "timestamp": "2025-10-13 16:45:30",
  "bybit_data": {
    "ltp": 114892.7,
    "timestamp": "2025-10-13 16:45:29"
  },
  "coindcx_data": {
    "ltp": 114850.2,
    "timestamp": "2025-10-13 16:45:30",
    "current_funding_rate": 0.0001,
    "estimated_funding_rate": 0.00012,
    "funding_timestamp": "2025-10-13 16:00:00"
  }
}
```

### 4. Use Included Debug Script

```bash
./check_price_data.sh
# Will test all three coins (BTC, ETH, SOL)
```

---

## Troubleshooting

### Error: "Failed to fetch prices from Redis"

**Cause:** Redis not running or no data available

**Solution:**
```bash
# Start Redis
brew services start redis  # macOS
sudo systemctl start redis # Linux

# Verify your price feed service is running
ps aux | grep price_feed  # or whatever your service is called
```

### Error: "Price data is stale"

**Cause:** Your price feed hasn't updated in >10 seconds

**Solution:**
- Check your price feed service is running
- Verify exchange WebSocket connections
- Check network connectivity

### Error: "connection refused"

**Cause:** Redis host/port mismatch

**Solution:**
- Verify Redis host/port in `crypto_data_retriever.py`
- Test connection: `redis-cli -h HOST -p PORT ping`

### Error: "Invalid price data structure"

**Cause:** Your price feed writes data in different format

**Solution:**
- Check your Redis data format matches expected structure
- Run: `redis-cli GET crypto:BTC:ltp` to see actual format
- Update your price feed to match expected format (see above)

---

## Price Feed Service Requirements

If you need to implement a price feed service, here's what it should do:

### Minimum Viable Price Feed

```python
# Example price feed writer (pseudocode)
import redis
import json
import time
from exchange_api import BybitAPI, CoinDCXAPI

r = redis.Redis(host='localhost', port=6379, db=0)

while True:
    # Fetch prices from exchanges
    bybit_price = BybitAPI.get_btc_price()
    coindcx_price = CoinDCXAPI.get_btc_futures_price()

    # Build data structure
    data = {
        'success': True,
        'symbol': 'BTC',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'bybit_data': {
            'ltp': bybit_price,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        },
        'coindcx_data': {
            'ltp': coindcx_price,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
    }

    # Write to Redis
    r.set('crypto:BTC:ltp', json.dumps(data))

    # Update every 1 second
    time.sleep(1)
```

### Best Practices

1. **Update Frequency:** Every 1-2 seconds minimum
2. **Error Handling:** Set `success: false` if fetch fails
3. **Timestamp Accuracy:** Use current timestamp when writing
4. **Multiple Coins:** Write separate keys for BTC, ETH, SOL
5. **Monitoring:** Log any fetch failures
6. **WebSocket Preferred:** Real-time updates better than polling

---

## Package Location Independence

**Important:** Your price feed service can run from **any directory** on your machine. Redis is a network service - location doesn't matter.

### Example Setups

**Setup 1: Separate Directories**
```
/Users/you/services/price_feed/     ← Your price feed (anywhere)
/Users/you/trading/hedge_bot/       ← This bot (anywhere)
Both connect to: localhost:6379
```

**Setup 2: Shared Directory**
```
/Users/you/trading/
├── price_feed_service/    ← Your price feed
└── hedge_bot/             ← This bot
Both connect to: localhost:6379
```

**Setup 3: Different Machines (Advanced)**
```
Server A: 192.168.1.10
  - Redis server
  - Price feed service

Server B: 192.168.1.20
  - Trading bot
  - Connects to: 192.168.1.10:6379
```

All scenarios work as long as Redis host/port configuration matches.

---

## Summary

✅ **What You Need:**
- Redis running at localhost:6379 (or configure different host/port)
- Price feed service writing data to Redis in expected format
- This trading bot package

✅ **What This Package Provides:**
- Trading bot that reads from Redis
- Price readers (LTP_fetch.py + crypto_data_retriever.py)
- Exchange clients for order execution
- Order management and monitoring

❌ **What This Package Does NOT Provide:**
- Price feed writers (you provide these)
- Exchange price fetching services
- Redis server itself

---

## Quick Start Checklist

- [ ] Redis installed and running
- [ ] Price feed service running and writing to Redis
- [ ] Verified Redis data format matches expected structure
- [ ] Tested `./check_price_data.sh` - all coins return data
- [ ] Tested `python3 -c "from price_feed.LTP_fetch import get_crypto_ltp; print(get_crypto_ltp('BTC'))"`
- [ ] Configure bot's `.env` file with exchange API keys
- [ ] Run `python3 main.py` to start trading

---

**Status:** This is a minimal bot package for users who already have or will provide their own price feed infrastructure.

For questions about the expected Redis format or price reader implementation, see the code in `price_feed/LTP_fetch.py` and `price_feed/crypto_data_retriever.py`.