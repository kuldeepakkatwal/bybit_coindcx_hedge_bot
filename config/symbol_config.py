"""
Symbol Configuration for Hedge Trading Bot
Defines trading parameters for supported cryptocurrencies.
"""

import os
from typing import Dict, Any


class SymbolConfig:
    """Configuration for cryptocurrency trading pairs"""

    # Dynamic precision loaded from Bybit API at startup
    # Key: symbol (e.g., 'ETH'), Value: precision data from API
    _dynamic_precision: Dict[str, Dict[str, Any]] = {}

    # Symbol specifications from PRD
    # NOTE: Static precision values kept for fallback, but dynamic precision
    # from API takes priority (loaded via PrecisionManager at startup)
    SYMBOLS: Dict[str, Dict[str, Any]] = {
        'BTC': {
            'bybit_symbol': 'BTCUSDT',
            'coindcx_symbol': 'B-BTC_USDT',
            'precision': 6,              # Decimal places for quantity (Bybit basePrecision: 0.000001)
            'price_precision': 1,        # Decimal places for price
            'tick_size': 0.1,           # Minimum price increment
            'min_quantity': 0.002,      # Minimum order size
            'bybit_fee': 0.00065,       # Bybit maker fee (0.065%)
            'coindcx_fee': 0.0005,      # CoinDCX maker fee (0.05%)
        },
        'ETH': {
            'bybit_symbol': 'ETHUSDT',
            'coindcx_symbol': 'B-ETH_USDT',
            'precision': 6,              # Decimal places for quantity (Bybit basePrecision: 0.000001)
            'price_precision': 2,
            'tick_size': 0.01,
            'min_quantity': 0.008,       # CoinDCX minimum: 2232 INR (~$27 USD, ~0.007 ETH at $3800)
            'bybit_fee': 0.00065,
            'coindcx_fee': 0.0005,
        }
    }

    # Trading parameters
    DEFAULT_CHUNK_USD = 50          # Default chunk size in USD
    MAX_SPREAD_PERCENT = 0.2        # Maximum allowed spread (0.2%)
    PRICE_FRESHNESS_SECONDS = int(os.getenv('PRICE_FRESHNESS_SECONDS', '3600'))  # From .env
                                    # Note: 10s recommended for production, 3600s for testing
    SPREAD_SANITY_PERCENT = 5.0     # Sanity check for spread

    # Order modification parameters
    ORDER_POLL_INTERVAL = 1         # Poll order status every 1 second
    ORDER_MODIFY_INTERVAL = 5       # Modify orders every 5 seconds
    NAKED_POSITION_TIMEOUT = 15     # Max time for naked position (seconds)
    MODIFICATION_ATTEMPTS = 2       # Number of modification attempts
    ORDER_RETRY_ATTEMPTS = 5        # Number of order placement retries

    @classmethod
    def get_symbol_config(cls, symbol: str) -> Dict[str, Any]:
        """
        Get configuration for a specific symbol.

        Args:
            symbol: Cryptocurrency symbol (e.g., 'BTC', 'ETH')

        Returns:
            Symbol configuration dictionary

        Raises:
            ValueError: If symbol is not supported
        """
        symbol = symbol.upper()
        if symbol not in cls.SYMBOLS:
            raise ValueError(
                f"Unsupported symbol: {symbol}. "
                f"Supported symbols: {', '.join(cls.SYMBOLS.keys())}"
            )
        return cls.SYMBOLS[symbol]

    @classmethod
    def get_supported_symbols(cls) -> list:
        """Get list of supported symbols."""
        return list(cls.SYMBOLS.keys())

    @classmethod
    def set_dynamic_precision(cls, symbol: str, precision_data: Dict[str, Any]):
        """
        Set dynamic precision data for a symbol (loaded from Bybit API).

        This method is called at bot startup after fetching precision
        from Bybit API via PrecisionManager.

        Args:
            symbol: Cryptocurrency symbol (e.g., 'ETH', 'BTC')
            precision_data: Dict with keys:
                - basePrecision: int (number of decimal places)
                - minOrderQty: float
                - maxOrderQty: float
                - tickSize: float
        """
        cls._dynamic_precision[symbol] = precision_data

    @classmethod
    def get_dynamic_precision(cls, symbol: str) -> Dict[str, Any]:
        """
        Get dynamic precision data for a symbol.

        Returns:
            Dict with precision data, or None if not loaded
        """
        return cls._dynamic_precision.get(symbol)

    @classmethod
    def has_dynamic_precision(cls, symbol: str) -> bool:
        """Check if dynamic precision is loaded for symbol."""
        return symbol in cls._dynamic_precision

    @classmethod
    def validate_quantity(cls, symbol: str, quantity: float) -> bool:
        """
        Validate if quantity meets minimum requirements.

        Args:
            symbol: Cryptocurrency symbol
            quantity: Order quantity

        Returns:
            True if quantity is valid, False otherwise
        """
        config = cls.get_symbol_config(symbol)
        return quantity >= config['min_quantity']

    @classmethod
    def round_quantity(cls, symbol: str, quantity: float) -> float:
        """
        Round quantity to correct precision.

        Uses dynamic precision from Bybit API if available,
        falls back to static config if not loaded yet.

        Args:
            symbol: Cryptocurrency symbol
            quantity: Raw quantity

        Returns:
            Rounded quantity
        """
        # Prefer dynamic precision from API
        if cls.has_dynamic_precision(symbol):
            precision = cls._dynamic_precision[symbol]['basePrecision']
        else:
            # Fallback to static config
            config = cls.get_symbol_config(symbol)
            precision = config['precision']

        return round(quantity, precision)

    @classmethod
    def round_price(cls, symbol: str, price: float) -> float:
        """
        Round price to correct precision.

        Args:
            symbol: Cryptocurrency symbol
            price: Raw price

        Returns:
            Rounded price
        """
        config = cls.get_symbol_config(symbol)
        return round(price, config['price_precision'])

    @classmethod
    def calculate_maker_price(cls, symbol: str, current_price: float, side: str) -> float:
        """
        Calculate maker order price (current_price ± multiple tick_sizes).

        Uses 5 ticks to ensure order stays in the book and doesn't cross spread.
        This prevents Post-Only rejection while still getting maker fees.

        Args:
            symbol: Cryptocurrency symbol
            current_price: Current market price
            side: 'buy' or 'sell'

        Returns:
            Maker order price
        """
        config = cls.get_symbol_config(symbol)
        tick_size = config['tick_size']

        # Use 1 tick for maker orders
        # This places the order just inside the spread without crossing it
        # ETH: 1 * $0.01 = $0.01 buffer
        # BTC: 1 * $0.10 = $0.10 buffer
        # If Post-Only rejected, will fetch new price and retry
        num_ticks = 1

        if side.lower() == 'buy':
            # Buy below current price (1 tick)
            maker_price = current_price - (tick_size * num_ticks)
        else:
            # Sell above current price (1 tick)
            maker_price = current_price + (tick_size * num_ticks)

        return cls.round_price(symbol, maker_price)

    @classmethod
    def apply_bybit_fee_compensation(cls, symbol: str, quantity: float) -> float:
        """
        NO FEE COMPENSATION - Post-Trade Reconciliation Strategy.

        Instead of trying to pre-compensate for fees (which gets lost to rounding
        for ETH due to 5 decimal precision), we now:

        1. Order the EXACT quantity requested (no compensation)
        2. Capture ACTUAL fees from WebSocket when order fills
        3. After all chunks complete, calculate total shortage
        4. Place single reconciliation order to buy the shortage

        This approach:
        ✅ Simpler - no complex pre-compensation math
        ✅ More accurate - uses actual fees from exchange
        ✅ Transparent - clear shortage amount at end
        ✅ Flexible - can skip reconciliation if shortage tiny

        Example for 0.012 ETH:
            OLD (pre-compensation):
                Order: 0.01201 ETH (trying to compensate)
                Receive: 0.01200219 ETH (over by 0.00000219)
                Issue: Precision rounding causes over-hedge

            NEW (post-reconciliation):
                Order: 0.012 ETH (exact)
                Receive: 0.0119922 ETH (WebSocket tells us fee was 0.0000078)
                After all chunks: Buy cumulative shortage in one order
                Result: Perfect hedge

        Args:
            symbol: Cryptocurrency symbol
            quantity: Original quantity

        Returns:
            Same quantity (no compensation applied)
        """
        import logging
        logger = logging.getLogger(__name__)

        # Get precision info for logging
        if cls.has_dynamic_precision(symbol):
            precision_info = f"{cls._dynamic_precision[symbol]['basePrecision']} decimals (from API)"
        else:
            config = cls.get_symbol_config(symbol)
            precision_info = f"{config['precision']} decimals (static)"

        logger.info(
            f"No fee compensation ({symbol}): POST-TRADE RECONCILIATION MODE\n"
            f"  Ordering exact quantity: {quantity:.8f} {symbol}\n"
            f"  Fees will be tracked from WebSocket\n"
            f"  Shortage will be reconciled after all chunks complete\n"
            f"  [{precision_info}]"
        )

        # Return exact quantity - no compensation
        return quantity
