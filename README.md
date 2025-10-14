# 🚀 Delta-Neutral Hedge Trading Bot (Minimal Version)

**Version:** 2.1 - Minimal Bot Package
**Status:** Production Ready
**Date:** October 13, 2025

---

## ✨ What Makes This Version Special

This is a **minimal bot package** designed for users who already have their own price feed service:

✅ **Trading bot only** - No price feed writers included
✅ **Exchange clients included** - Bybit & CoinDCX clients built-in
✅ **Price readers included** - Reads from your Redis database
✅ **Clean package structure** - No unnecessary dependencies
✅ **Location independent** - Works with any Redis service

**Perfect for teams with existing infrastructure** - Just bring your own price feed!

---

## 📦 What's Included

```
hedge_trade_standalone/
├── main.py                      ← Trading bot entry point
├── core/                        ← Core trading logic
│   ├── bot.py
│   ├── order_manager.py
│   ├── chunk_manager.py
│   ├── price_service.py
│   └── fee_reconciliation.py
├── exchange_clients/            ← Bundled exchange APIs
│   ├── bybit/
│   │   ├── bybit_spot_client.py
│   │   └── Bybit_ltp_ws_client.py
│   └── coindcx/
│       └── coindcx_futures.py
├── price_feed/                  ← Price READERS only
│   ├── LTP_fetch.py                ← Reads from Redis
│   ├── crypto_data_retriever.py    ← Redis helper
│   └── __init__.py
├── utils/                       ← Utilities
│   ├── db.py
│   ├── exceptions.py
│   ├── validators.py
│   └── precision_manager.py
├── config/                      ← Configuration
│   └── symbol_config.py
├── order_monitor.py             ← WebSocket order tracking
├── requirements.txt             ← Bot dependencies
├── .env.example                 ← Bot configuration template
├── postgresql_schema.sql        ← Database schema
├── REDIS_REQUIREMENT.md         ← ⚠️ READ THIS FIRST!
└── migrations/                  ← Database migrations
```

---

## 🏗️ Architecture: Minimal Bot + Your Price Feed

⚠️ **IMPORTANT:** This package contains ONLY the trading bot. You must provide your own price feed service.

```
┌──────────────────────────────────────────────────────────────┐
│  YOUR PRICE FEED SERVICE (Not Included)                     │
│  - Fetches prices from Bybit + CoinDCX                      │
│  - Writes to Redis (localhost:6379)                         │
│  - Can run from ANY location on your machine                │
└──────────────────────────────┬───────────────────────────────┘
                                │
                                ▼ (Redis: localhost:6379)
              ┌─────────────────────────────────┐
              │   Redis Database (Shared)       │
              │   - Key: crypto:BTC:ltp         │
              │   - Key: crypto:ETH:ltp         │
              │   - Key: crypto:SOL:ltp         │
              └──────────────┬──────────────────┘
                             │
                             │ (Bot reads from Redis)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  THIS TRADING BOT (Included)                                 │
│  Run: python3 main.py                                        │
│                                                               │
│  core/price_service.py → LTP_fetch.py → Redis               │
│        ↓                                                     │
│  Places orders on exchanges based on prices                  │
└──────────────────────────────────────────────────────────────┘

Summary:
- ⚠️ You provide price feed service (writes to Redis)
- ✅ This bot reads from Redis (trades based on prices)
- 🌐 Redis connects both services (location independent)
- 📖 See REDIS_REQUIREMENT.md for details
```

---

## 🎯 Quick Start (10 Minutes)

### Step 1: Prerequisites

**Required:**
- Python 3.8+
- PostgreSQL 14+
- Redis
- Bybit account + API keys
- CoinDCX account + API keys

**Install on macOS:**
```bash
brew install postgresql@14 redis python3
```

**Install on Linux:**
```bash
sudo apt update
sudo apt install postgresql-14 redis-server python3 python3-pip
```

### Step 2: ⚠️ IMPORTANT - Read This First

**Before proceeding, read:**
- [REDIS_REQUIREMENT.md](REDIS_REQUIREMENT.md) - **MUST READ** - Explains Redis requirements

**Summary:** This bot needs a price feed service writing to Redis. If you already have one, continue. If not, see REDIS_REQUIREMENT.md for details.

### Step 3: Install Python Dependencies

```bash
cd hedge_trade_standalone

# Install trading bot dependencies only
pip3 install -r requirements.txt
```

### Step 3: Set Up Database

```bash
# Create database and user
psql postgres <<EOF
CREATE DATABASE hedge_bot;
CREATE USER hedge_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE hedge_bot TO hedge_user;
\q
EOF

# Import schema
psql -h localhost -U hedge_user -d hedge_bot -f postgresql_schema.sql

# Run migrations
chmod +x migrate_database.sh
./migrate_database.sh
```

### Step 4: Configure Environment

```bash
# Copy template
cp .env.example .env

# Edit with your API keys
nano .env
```

**Add your credentials:**
```bash
# Exchange API Keys
BYBIT_API_KEY=your_bybit_key
BYBIT_API_SECRET=your_bybit_secret
COINDCX_API_KEY=your_coindcx_key
COINDCX_API_SECRET=your_coindcx_secret

# Database
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=hedge_bot
POSTGRES_USER=hedge_user
POSTGRES_PASSWORD=your_password

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# Bot Settings
TEST_MODE=false
MAX_SPREAD_PERCENT=0.2
USD_PER_CHUNK=50
```

### Step 5: Start Services and Verify Price Feed

**Start Prerequisites:**
```bash
# Start PostgreSQL and Redis
brew services start postgresql@14    # macOS
brew services start redis

# OR on Linux:
sudo service postgresql start
sudo service redis-server start
```

**Verify Your Price Feed Service:**
```bash
# Make sure YOUR price feed service is running
# (This is NOT included in this package)
./check_price_data.sh
# Should show prices for BTC, ETH, SOL
```

**Run the Trading Bot:**
```bash
python3 main.py
```

**Important:** Your price feed service MUST be running and writing to Redis before starting this bot.

---

## 📖 Usage Guide

### First Trade (Test with Small Amount)

```
$ python3 main.py

============================================================
DELTA-NEUTRAL HEDGE TRADING BOT
============================================================

Supported Cryptocurrencies:
  1. BTC
  2. ETH

Select coin (1-2): 2
✓ Selected: ETH

Enter quantity to trade: 0.008

Fetching real-time prices for ETH...
   Bybit ETH: $4,107.69
   CoinDCX ETH: $4,105.45
   Spread: 0.0545%

Spread is acceptable (< 0.2%). Proceed with trade? (y/n): y

[Bot executes trade...]
```

### Monitoring

```bash
# Watch logs
tail -f logs/orders_websocket_*.log

# Check database
psql -h localhost -U hedge_user -d hedge_bot

# Query orders
SELECT * FROM orders ORDER BY placed_at DESC LIMIT 10;
```

---

## 🔧 Configuration

### Symbol Configuration

Edit `config/symbol_config.py` to adjust:
- Trading pair settings
- Minimum quantities
- Price precision
- Tick sizes
- Fee rates

### Bot Behavior

Edit `.env` to adjust:
- `MAX_SPREAD_PERCENT` - Maximum acceptable spread (default: 0.2%)
- `USD_PER_CHUNK` - Chunk size in USD (default: $50)
- `TEST_MODE` - Enable test mode (default: false)

---

## 🏗️ Architecture

### Self-Contained Design

**No External Dependencies:**
```
Traditional Structure (FRAGMENTED):
├── hedge_trade_main/
├── client/  ← External
└── ltp_redis_fetcher/  ← External

Self-Contained Structure (ALL-IN-ONE):
└── hedge_trade_standalone/
    ├── core/
    ├── exchange_clients/  ← Bundled
    ├── price_feed/  ← Bundled
    └── utils/
```

**Benefits:**
- ✅ Single folder = entire bot
- ✅ No sys.path hacks
- ✅ Clean Python imports
- ✅ Easy to share
- ✅ No linking required

### Import Structure

```python
# Clean relative imports (no sys.path modifications)
from ..exchange_clients.bybit.bybit_spot_client import BybitSpotClient
from ..exchange_clients.coindcx.coindcx_futures import CoinDCXFutures
from ..price_feed.LTP_fetch import get_crypto_ltp
```

---

## 🧪 Testing

### Verify Setup

```bash
# Test database connection
python3 -c "from utils.db import Database; db = Database(); print('✅ Database OK')"

# Test price feed
python3 -c "from price_feed.LTP_fetch import get_crypto_ltp; print(get_crypto_ltp('ETH'))"

# Test exchange clients
python3 -c "from exchange_clients.bybit.bybit_spot_client import BybitSpotClient; print('✅ Bybit client OK')"
```

### First Test Trade

- Coin: ETH
- Amount: 0.008 ETH (minimum)
- Expected time: ~30 seconds
- Expected result: Both orders filled, hedge complete

---

## 🐛 Troubleshooting

### ImportError: No module named 'exchange_clients'

**Cause:** Running from wrong directory

**Fix:**
```bash
# Must run from hedge_trade_standalone directory
cd hedge_trade_standalone
python3 main.py
```

### Failed to fetch prices from Redis

**Cause:** Price feed service not running

**Fix:**
```bash
# Start price feed
cd price_feed
python3 crypto_data_retriever.py &

# Verify Redis
redis-cli ping  # Should return PONG
```

### Database connection failed

**Cause:** PostgreSQL not running or wrong credentials

**Fix:**
```bash
# Check PostgreSQL status
brew services list | grep postgresql  # macOS
sudo service postgresql status  # Linux

# Verify credentials in .env match database
psql -h localhost -U hedge_user -d hedge_bot -c "SELECT 1;"
```

### ModuleNotFoundError: No module named 'pybit'

**Cause:** Dependencies not installed

**Fix:**
```bash
pip3 install -r requirements.txt
```

---

## 📊 What's Different from Original

| Feature | Original Version | Standalone Version |
|---------|-----------------|-------------------|
| **Structure** | Multiple folders | Single folder |
| **Imports** | sys.path hacks | Clean relative imports |
| **Exchange Clients** | External dependency | Bundled inside |
| **Price Feed** | External dependency | Bundled inside |
| **Sharing** | Complex setup | Drop-in ready |
| **Dependencies** | External linking | Self-contained |

---

## 🚀 Sharing with Colleagues

### How to Share

**Option 1: ZIP File**
```bash
cd ..
zip -r hedge_bot_standalone.zip hedge_trade_standalone/ \
  -x "*.log" \
  -x "*/.env" \
  -x "*__pycache__*" \
  -x "*.pyc"
```

**Option 2: Git Repository**
```bash
cd hedge_trade_standalone
git init
git add .
git commit -m "Self-contained hedge trading bot"
git remote add origin <your_repo_url>
git push -u origin main
```

### What Your Colleague Needs

1. Extract the folder
2. Install Python dependencies: `pip install -r requirements.txt`
3. Set up PostgreSQL + Redis
4. Create `.env` with **their own** API keys
5. Run: `python3 main.py`

**That's it!** No code linking, no external dependencies.

---

## 📝 File Manifest

**Core Files (Required):**
- `main.py` - Entry point
- `order_monitor.py` - WebSocket order tracking
- `core/bot.py` - Main bot logic
- `core/order_manager.py` - Order placement
- `core/chunk_manager.py` - Trade chunking
- `core/price_service.py` - Price fetching

**Bundled Dependencies:**
- `exchange_clients/bybit/bybit_spot_client.py` - Bybit API client
- `exchange_clients/coindcx/coindcx_futures.py` - CoinDCX API client
- `price_feed/LTP_fetch.py` - Price fetcher
- `price_feed/crypto_data_retriever.py` - Redis data retriever

**Configuration:**
- `.env.example` - Configuration template
- `requirements.txt` - Python dependencies
- `postgresql_schema.sql` - Database schema
- `config/symbol_config.py` - Symbol settings

**Optional:**
- `migrations/` - Database migrations
- `setup.sh` - Setup helper script
- `start_prerequisites.sh` - Service starter

---

## ✅ Success Criteria

After setup, you should be able to:

- ✅ Run `python3 main.py` without errors
- ✅ See real-time prices fetched
- ✅ Place orders successfully
- ✅ See orders in database
- ✅ Complete first test trade
- ✅ Share folder with colleague (works instantly)

---

## 🆘 Support

**Documentation:**
- This README (complete guide)
- Inline code comments
- `.env.example` (configuration reference)

**Common Issues:**
- Check logs: `logs/orders_websocket_*.log`
- Database queries: `SELECT * FROM orders;`
- Price feed: `python3 price_feed/LTP_fetch.py` (test)

---

## 🎯 Next Steps

1. ✅ Complete setup (Steps 1-6)
2. ✅ Run first test trade (0.008 ETH)
3. ✅ Verify in database and exchange
4. ✅ Gradually increase trade size
5. ✅ Share with colleagues (they follow same steps)

---

**Version:** 2.0 (Self-Contained)
**Last Updated:** October 13, 2025
**Status:** Production Ready ✅

**Ready to trade!** 🚀
