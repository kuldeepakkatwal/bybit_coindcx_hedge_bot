"""
Input Validation Utilities for Hedge Trading Bot
"""

from datetime import datetime, timedelta
from typing import Tuple, Optional
from .exceptions import ValidationException, PriceDataException


class Validators:
    """Collection of validation functions for trading bot"""

    @staticmethod
    def validate_quantity(quantity: float, min_quantity: float, symbol: str) -> None:
        """
        Validate order quantity meets minimum requirements.

        Args:
            quantity: Order quantity to validate
            min_quantity: Minimum allowed quantity
            symbol: Cryptocurrency symbol

        Raises:
            ValidationException: If quantity is invalid
        """
        if quantity <= 0:
            raise ValidationException(
                "quantity",
                quantity,
                "Quantity must be positive"
            )

        if quantity < min_quantity:
            raise ValidationException(
                "quantity",
                quantity,
                f"Quantity below minimum {min_quantity} for {symbol}"
            )

    @staticmethod
    def validate_usd_amount(usd_amount: float) -> None:
        """
        Validate USD amount for trading.

        Args:
            usd_amount: USD amount to validate

        Raises:
            ValidationException: If amount is invalid
        """
        if usd_amount <= 0:
            raise ValidationException(
                "usd_amount",
                usd_amount,
                "USD amount must be positive"
            )

        if usd_amount < 10:
            raise ValidationException(
                "usd_amount",
                usd_amount,
                "USD amount too small (minimum $10)"
            )

    @staticmethod
    def validate_spread(
        spread: float,
        max_spread: float,
        sanity_check: float = 5.0
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate spread is within acceptable range.

        Args:
            spread: Calculated spread percentage
            max_spread: Maximum allowed spread
            sanity_check: Upper sanity limit for spread

        Returns:
            Tuple of (is_valid, warning_message)
        """
        # Sanity check - spread should never be this high
        if abs(spread) > sanity_check:
            return False, (
                f"Spread {spread:.4f}% exceeds sanity limit {sanity_check}%. "
                f"Possible price data error."
            )

        # Normal spread validation
        if abs(spread) > max_spread:
            return False, (
                f"Spread {spread:.4f}% exceeds maximum {max_spread}%. "
                f"Spread too wide for safe trading."
            )

        return True, None

    @staticmethod
    def validate_price_freshness(
        timestamp_str: str,
        max_age_seconds: int = 10,
        exchange: str = "Unknown"
    ) -> None:
        """
        Validate price data is fresh (not stale).

        Args:
            timestamp_str: ISO format timestamp string
            max_age_seconds: Maximum allowed age in seconds
            exchange: Exchange name for error reporting

        Raises:
            PriceDataException: If price data is stale
        """
        try:
            # Parse timestamp
            price_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

            # Calculate age
            now = datetime.now(price_time.tzinfo)
            age = (now - price_time).total_seconds()

            if age > max_age_seconds:
                raise PriceDataException(
                    exchange,
                    f"Price data stale: {age:.1f}s old (max {max_age_seconds}s)"
                )

        except ValueError as e:
            raise PriceDataException(
                exchange,
                f"Invalid timestamp format: {timestamp_str}"
            )

    @staticmethod
    def validate_price_data(price_data: dict, exchange: str) -> None:
        """
        Validate price data structure and content.

        Args:
            price_data: Price data dictionary
            exchange: Exchange name

        Raises:
            PriceDataException: If price data is invalid
        """
        # Check if data exists
        if not price_data:
            raise PriceDataException(exchange, "No price data available")

        # Check for LTP
        ltp = price_data.get('ltp')
        if ltp is None:
            raise PriceDataException(exchange, "Missing LTP (Last Traded Price)")

        # Validate LTP is numeric and positive
        try:
            ltp_float = float(ltp)
            if ltp_float <= 0:
                raise PriceDataException(
                    exchange,
                    f"Invalid LTP: {ltp} (must be positive)"
                )
        except (ValueError, TypeError):
            raise PriceDataException(
                exchange,
                f"Invalid LTP format: {ltp}"
            )

        # Check for timestamp
        timestamp = price_data.get('timestamp')
        if not timestamp:
            raise PriceDataException(exchange, "Missing timestamp")

    @staticmethod
    def calculate_spread(bybit_price: float, coindcx_price: float) -> float:
        """
        Calculate spread percentage.
        Formula: |coindcx - bybit| / bybit × 100

        Args:
            bybit_price: Bybit spot price
            coindcx_price: CoinDCX futures price

        Returns:
            Spread percentage
        """
        if bybit_price <= 0:
            raise ValidationException(
                "bybit_price",
                bybit_price,
                "Bybit price must be positive"
            )

        spread = abs(coindcx_price - bybit_price) / bybit_price * 100
        return spread

    @staticmethod
    def validate_chunk_size(chunk_usd: float) -> None:
        """
        Validate chunk size is reasonable.

        Args:
            chunk_usd: Chunk size in USD

        Raises:
            ValidationException: If chunk size is invalid
        """
        if chunk_usd <= 0:
            raise ValidationException(
                "chunk_usd",
                chunk_usd,
                "Chunk size must be positive"
            )

        if chunk_usd < 10:
            raise ValidationException(
                "chunk_usd",
                chunk_usd,
                "Chunk size too small (minimum $10)"
            )

        if chunk_usd > 10000:
            raise ValidationException(
                "chunk_usd",
                chunk_usd,
                "Chunk size too large (maximum $10,000)"
            )

    @staticmethod
    def validate_symbol(symbol: str, supported_symbols: list) -> None:
        """
        Validate cryptocurrency symbol is supported.

        Args:
            symbol: Symbol to validate
            supported_symbols: List of supported symbols

        Raises:
            ValidationException: If symbol is not supported
        """
        symbol = symbol.upper()
        if symbol not in supported_symbols:
            raise ValidationException(
                "symbol",
                symbol,
                f"Unsupported symbol. Supported: {', '.join(supported_symbols)}"
            )

    @staticmethod
    def validate_remainder_choice(choice: int) -> None:
        """
        Validate remainder handling choice.

        Args:
            choice: Choice number (1-4)

        Raises:
            ValidationException: If choice is invalid
        """
        if choice not in [1, 2, 3, 4]:
            raise ValidationException(
                "remainder_choice",
                choice,
                "Choice must be 1, 2, 3, or 4"
            )
