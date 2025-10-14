#!/usr/bin/env python3
"""
Delta-Neutral Hedge Trading Bot - Main Entry Point
Production-grade bot for executing delta-neutral arbitrage trades.
Self-contained version with all dependencies bundled.
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Add current directory to path to enable imports
current_dir = Path(__file__).parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Load environment variables
load_dotenv()

# Import from bundled modules (self-contained)
from core.bot import EnhancedBot
from utils.db import Database


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('hedge_bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def main():
    """Main entry point for the hedge trading bot."""

    print("""
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║         DELTA-NEUTRAL HEDGE TRADING BOT v1.0                  ║
║         Phase 1 MVP - Production Ready                        ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    db = None  # Initialize db variable
    try:
        # Get API credentials from environment
        bybit_api_key = os.getenv('BYBIT_API_KEY')
        bybit_api_secret = os.getenv('BYBIT_API_SECRET')
        coindcx_api_key = os.getenv('COINDCX_API_KEY')
        coindcx_api_secret = os.getenv('COINDCX_API_SECRET')

        # Validate credentials
        if not all([bybit_api_key, bybit_api_secret, coindcx_api_key, coindcx_api_secret]):
            print("❌ ERROR: Missing API credentials in .env file")
            print("\nRequired environment variables:")
            print("  - BYBIT_API_KEY")
            print("  - BYBIT_API_SECRET")
            print("  - COINDCX_API_KEY")
            print("  - COINDCX_API_SECRET")
            print("\nPlease set these in your .env file and try again.")
            return 1

        # Get testnet flag
        use_testnet = os.getenv('BYBIT_TESTNET', 'true').lower() == 'true'

        # Initialize database (optional)
        db = None
        try:
            db = Database()
            db.connect()  # Try to connect
            if db.is_connected():
                db.create_tables()  # Create tables if they don't exist
                logger.info("Database connection established")
                print("✓ Database connected")
            else:
                logger.info("Database not configured - continuing without it")
        except Exception as e:
            logger.info(f"Database not available: {e}")
            print("ℹ️  Database not configured (optional)")
            db = None

        # Initialize and run bot
        print("\nInitializing bot...")
        print(f"Mode: {'TESTNET' if use_testnet else 'MAINNET'}")
        print("-" * 60)

        bot = EnhancedBot(
            bybit_api_key=bybit_api_key,
            bybit_api_secret=bybit_api_secret,
            coindcx_api_key=coindcx_api_key,
            coindcx_api_secret=coindcx_api_secret,
            testnet=use_testnet,
            db=db
        )

        print("✓ Bot initialized successfully")

        # Run interactive bot
        bot.run()

        return 0

    except KeyboardInterrupt:
        print("\n\n⚠️ Operation cancelled by user")
        return 130

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\n❌ FATAL ERROR: {e}")
        return 1

    finally:
        # Cleanup
        if db:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
