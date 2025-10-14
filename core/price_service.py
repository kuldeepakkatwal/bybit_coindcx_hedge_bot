"""
Price Service - Wrapper around LTP_fetch with validation
Fetches and validates cryptocurrency prices from both exchanges.
"""

import logging
from typing import Dict, Tuple

# Import from bundled price_feed module (self-contained)
from price_feed.LTP_fetch import get_crypto_ltp

from config.symbol_config import SymbolConfig
from utils.exceptions import PriceDataException
from utils.validators import Validators


logger = logging.getLogger(__name__)


class PriceService:
    """Service for fetching and validating cryptocurrency prices"""

    def __init__(self):
        """Initialize price service."""
        self.config = SymbolConfig()
        self.validators = Validators()

    def get_validated_prices(self, symbol: str) -> Dict:
        """
        Fetch and validate prices from both exchanges.

        Args:
            symbol: Cryptocurrency symbol (BTC/ETH)

        Returns:
            Dictionary with validated price data:
            {
                'symbol': str,
                'bybit': {'price': float, 'timestamp': str},
                'coindcx': {'price': float, 'timestamp': str},
                'spread': float,
                'funding_rate': {
                    'current': float,
                    'estimated': float,
                    'timestamp': str
                }
            }

        Raises:
            PriceDataException: If price data is invalid or stale
            ValidationException: If spread is invalid
        """
        symbol = symbol.upper()
        logger.info(f"Fetching prices for {symbol}")

        try:
            # Get raw price data from LTP_fetch
            raw_data = get_crypto_ltp(symbol)

            if not raw_data.get('success'):
                raise PriceDataException(
                    "Both",
                    f"Failed to fetch price data: {raw_data.get('error', 'Unknown error')}"
                )

            # Extract and validate Bybit data
            bybit_data = raw_data.get('bybit_data')
            if not bybit_data:
                raise PriceDataException("Bybit", "No Bybit price data available")

            self.validators.validate_price_data(bybit_data, "Bybit")

            # Validate Bybit price freshness
            if bybit_data.get('timestamp'):
                self.validators.validate_price_freshness(
                    bybit_data['timestamp'],
                    self.config.PRICE_FRESHNESS_SECONDS,
                    "Bybit"
                )

            # Extract and validate CoinDCX data
            coindcx_data = raw_data.get('coindcx_data')
            if not coindcx_data:
                raise PriceDataException("CoinDCX", "No CoinDCX price data available")

            self.validators.validate_price_data(coindcx_data, "CoinDCX")

            # Validate CoinDCX price freshness
            if coindcx_data.get('timestamp'):
                self.validators.validate_price_freshness(
                    coindcx_data['timestamp'],
                    self.config.PRICE_FRESHNESS_SECONDS,
                    "CoinDCX"
                )

            # Convert prices to float
            bybit_price = float(bybit_data['ltp'])
            coindcx_price = float(coindcx_data['ltp'])

            # Calculate spread
            spread = self.validators.calculate_spread(bybit_price, coindcx_price)

            # Validate spread sanity
            is_valid, warning = self.validators.validate_spread(
                spread,
                self.config.MAX_SPREAD_PERCENT,
                self.config.SPREAD_SANITY_PERCENT
            )

            if not is_valid:
                logger.warning(f"Spread validation warning: {warning}")
                # Don't raise exception here - let the bot decide

            # Extract funding rate data
            funding_rate = {
                'current': None,
                'estimated': None,
                'timestamp': None
            }

            if coindcx_data.get('current_funding_rate'):
                try:
                    funding_rate['current'] = float(coindcx_data['current_funding_rate'])
                except (ValueError, TypeError):
                    logger.warning("Invalid current funding rate format")

            if coindcx_data.get('estimated_funding_rate'):
                try:
                    funding_rate['estimated'] = float(coindcx_data['estimated_funding_rate'])
                except (ValueError, TypeError):
                    logger.warning("Invalid estimated funding rate format")

            if coindcx_data.get('funding_timestamp'):
                funding_rate['timestamp'] = coindcx_data['funding_timestamp']

            # Build validated response
            validated_data = {
                'symbol': symbol,
                'bybit': {
                    'price': bybit_price,
                    'timestamp': bybit_data.get('timestamp')
                },
                'coindcx': {
                    'price': coindcx_price,
                    'timestamp': coindcx_data.get('timestamp')
                },
                'spread': spread,
                'spread_warning': warning,
                'funding_rate': funding_rate
            }

            logger.info(
                f"Prices validated: {symbol} - "
                f"Bybit: ${bybit_price:.2f}, "
                f"CoinDCX: ${coindcx_price:.2f}, "
                f"Spread: {spread:.4f}%"
            )

            return validated_data

        except Exception as e:
            logger.error(f"Error fetching prices for {symbol}: {e}")
            raise

    def get_maker_prices(self, symbol: str) -> Tuple[float, float]:
        """
        Get maker order prices for both exchanges.

        Args:
            symbol: Cryptocurrency symbol

        Returns:
            Tuple of (bybit_maker_price, coindcx_maker_price)
        """
        price_data = self.get_validated_prices(symbol)

        bybit_price = price_data['bybit']['price']
        coindcx_price = price_data['coindcx']['price']

        # Calculate maker prices (buy below, sell above current price)
        # For hedge: buy on Bybit (spot), sell on CoinDCX (futures)
        bybit_maker = self.config.calculate_maker_price(symbol, bybit_price, 'buy')
        coindcx_maker = self.config.calculate_maker_price(symbol, coindcx_price, 'sell')

        logger.info(
            f"Maker prices: {symbol} - "
            f"Bybit: ${bybit_maker:.2f} (buy), "
            f"CoinDCX: ${coindcx_maker:.2f} (sell)"
        )

        return bybit_maker, coindcx_maker

    def check_spread(self, symbol: str, max_spread: float = None) -> Tuple[bool, float, str]:
        """
        Check if current spread is within acceptable range.

        Args:
            symbol: Cryptocurrency symbol
            max_spread: Maximum allowed spread (default from config)

        Returns:
            Tuple of (is_acceptable, spread_value, message)
        """
        if max_spread is None:
            max_spread = self.config.MAX_SPREAD_PERCENT

        price_data = self.get_validated_prices(symbol)
        spread = price_data['spread']

        is_valid, warning = self.validators.validate_spread(
            spread,
            max_spread,
            self.config.SPREAD_SANITY_PERCENT
        )

        if is_valid:
            message = f"Spread OK: {spread:.4f}% (max {max_spread}%)"
        else:
            message = warning or f"Spread {spread:.4f}% exceeds {max_spread}%"

        return is_valid, spread, message
