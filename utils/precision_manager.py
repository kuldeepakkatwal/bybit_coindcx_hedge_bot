"""
Bybit Precision Manager
Fetches and caches instrument precision rules from Bybit API.
Uses JSON file for persistence to minimize API calls.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class PrecisionManager:
    """
    Manages Bybit instrument precision rules.

    Features:
    - Fetches precision from Bybit API
    - Caches in JSON file for persistence
    - Loads from cache on subsequent starts (no API call)
    - Supports manual refresh when needed

    Usage:
        manager = PrecisionManager(bybit_client)
        manager.load()  # Load from cache or API
        precision = manager.get_precision('ETH')
    """

    def __init__(self, bybit_client, cache_file='config/bybit_precision.json'):
        """
        Initialize PrecisionManager.

        Args:
            bybit_client: Bybit API client instance
            cache_file: Path to JSON cache file (relative to project root)
        """
        self.client = bybit_client
        self.cache_file = cache_file
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.last_updated: Optional[datetime] = None

    def load(self, force_refresh: bool = False) -> bool:
        """
        Load precision rules from cache or API.

        Priority:
        1. If force_refresh=True: Fetch from API
        2. If cache file exists and valid: Load from file
        3. Else: Fetch from API and create cache

        Args:
            force_refresh: Force API call even if cache exists

        Returns:
            True if successful, False otherwise
        """
        # Try loading from cache first
        if not force_refresh and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)

                self.cache = data['instruments']
                self.last_updated = datetime.fromisoformat(data['last_updated'])

                logger.info(f"✓ Loaded precision from cache ({self.cache_file})")
                logger.info(f"  Last updated: {self.last_updated.strftime('%Y-%m-%d %H:%M:%S')}")

                # Check if cache is stale (>7 days)
                age = datetime.now() - self.last_updated
                if age > timedelta(days=7):
                    logger.warning(
                        f"⚠️ Cache is {age.days} days old, consider refreshing "
                        f"(delete {self.cache_file} or call refresh())"
                    )

                return True

            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
                logger.info("Fetching from API instead...")

        # Fetch from API
        return self.refresh()

    def refresh(self, symbols: Optional[list] = None) -> bool:
        """
        Fetch fresh precision from Bybit API and save to cache.

        Args:
            symbols: List of symbols to fetch (default: ['BTC', 'ETH'])

        Returns:
            True if successful, False otherwise
        """
        if symbols is None:
            symbols = ['BTC', 'ETH']  # Default supported symbols

        logger.info("Fetching precision from Bybit API...")

        try:
            for symbol in symbols:
                bybit_symbol = f'{symbol}USDT'

                logger.info(f"  Fetching {bybit_symbol}...")

                # Call Bybit API to get instrument info
                response = self.client.session.get_instruments_info(
                    category='spot',
                    symbol=bybit_symbol
                )

                # Check response
                if response['retCode'] != 0:
                    raise Exception(f"API error: {response['retMsg']}")

                # Parse response
                instrument_list = response['result']['list']
                if not instrument_list:
                    raise Exception(f"No instrument data for {bybit_symbol}")

                instrument = instrument_list[0]
                lot_filter = instrument['lotSizeFilter']
                price_filter = instrument['priceFilter']

                # Extract precision from basePrecision string
                # Example: "0.00001" → 5 decimal places
                base_precision_str = lot_filter['basePrecision']
                if '.' in base_precision_str:
                    precision = len(base_precision_str.split('.')[1].rstrip('0'))
                else:
                    precision = 0

                # Store parsed data
                self.cache[symbol] = {
                    'basePrecision': precision,
                    'minOrderQty': float(lot_filter['minOrderQty']),
                    'maxOrderQty': float(lot_filter['maxOrderQty']),
                    'tickSize': float(price_filter['tickSize'])
                }

                logger.info(
                    f"    {symbol}: {precision} decimals, "
                    f"min: {lot_filter['minOrderQty']}, "
                    f"tick: {price_filter['tickSize']}"
                )

            # Save to cache file
            self.last_updated = datetime.now()
            self._save_to_file()

            logger.info(f"✓ Precision rules fetched from API ({len(symbols)} symbols)")
            return True

        except Exception as e:
            logger.error(f"Failed to fetch precision from API: {e}")

            # If we have existing cache, continue using it
            if self.cache:
                logger.warning("Using existing cache despite API failure")
                return True

            # No cache and API failed - cannot proceed
            raise Exception(
                f"Cannot load precision rules: API failed and no cache available. "
                f"Error: {e}"
            )

    def _save_to_file(self):
        """Save current cache to JSON file."""
        data = {
            'last_updated': self.last_updated.isoformat(),
            'instruments': self.cache
        }

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

        # Write to file with pretty formatting
        with open(self.cache_file, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"✓ Precision cache saved to {self.cache_file}")

    def get_precision(self, symbol: str) -> Dict[str, Any]:
        """
        Get precision rules for a specific symbol.

        Args:
            symbol: Symbol name (e.g., 'ETH', 'BTC')

        Returns:
            Dict with precision data:
            {
                'basePrecision': 5,
                'minOrderQty': 0.00029,
                'maxOrderQty': 4616.1353715,
                'tickSize': 0.01
            }

        Raises:
            ValueError: If symbol not loaded
        """
        if symbol not in self.cache:
            raise ValueError(
                f"Precision not loaded for {symbol}. "
                f"Available symbols: {list(self.cache.keys())}"
            )
        return self.cache[symbol]

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all loaded precision rules.

        Returns:
            Dict mapping symbol to precision data
        """
        return self.cache

    def is_loaded(self, symbol: str) -> bool:
        """Check if precision is loaded for a symbol."""
        return symbol in self.cache

    def get_cache_age(self) -> Optional[timedelta]:
        """
        Get age of current cache.

        Returns:
            timedelta if cache loaded, None otherwise
        """
        if self.last_updated:
            return datetime.now() - self.last_updated
        return None

    def __repr__(self):
        symbols = list(self.cache.keys())
        age = self.get_cache_age()
        age_str = f"{age.days}d {age.seconds//3600}h ago" if age else "not loaded"
        return f"<PrecisionManager: {len(symbols)} symbols, updated {age_str}>"
