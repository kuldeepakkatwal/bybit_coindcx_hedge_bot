"""
Custom Exceptions for Hedge Trading Bot
"""


class HedgeTradingException(Exception):
    """Base exception for hedge trading bot"""
    pass


class SpreadException(HedgeTradingException):
    """
    Raised when spread exceeds maximum allowed threshold.
    Critical exception that should stop trading immediately.
    """

    def __init__(self, spread: float, max_spread: float, message: str = None):
        self.spread = spread
        self.max_spread = max_spread
        if message is None:
            message = (
                f"Spread violation: {spread:.4f}% exceeds maximum {max_spread:.4f}%. "
                f"Trading halted for safety."
            )
        super().__init__(message)


class OrderException(HedgeTradingException):
    """
    Raised when order placement or modification fails.
    """

    def __init__(self, exchange: str, operation: str, details: str, order_id: str = None):
        self.exchange = exchange
        self.operation = operation
        self.details = details
        self.order_id = order_id
        message = f"Order {operation} failed on {exchange}: {details}"
        if order_id:
            message += f" (Order ID: {order_id})"
        super().__init__(message)


class InsufficientBalanceException(HedgeTradingException):
    """
    Raised when account balance is insufficient for trading.
    """

    def __init__(self, exchange: str, required: float, available: float, currency: str = "USDT"):
        self.exchange = exchange
        self.required = required
        self.available = available
        self.currency = currency
        message = (
            f"Insufficient balance on {exchange}: "
            f"Required {required:.2f} {currency}, "
            f"Available {available:.2f} {currency}"
        )
        super().__init__(message)


class PriceDataException(HedgeTradingException):
    """
    Raised when price data is stale, missing, or invalid.
    """

    def __init__(self, exchange: str, issue: str):
        self.exchange = exchange
        self.issue = issue
        message = f"Price data error from {exchange}: {issue}"
        super().__init__(message)


class ValidationException(HedgeTradingException):
    """
    Raised when input validation fails.
    """

    def __init__(self, field: str, value, reason: str):
        self.field = field
        self.value = value
        self.reason = reason
        message = f"Validation failed for {field}={value}: {reason}"
        super().__init__(message)


class NakedPositionException(HedgeTradingException):
    """
    Raised when unable to close naked position within timeout.
    Critical exception indicating position risk.
    """

    def __init__(self, symbol: str, exchange: str, quantity: float, timeout: int):
        self.symbol = symbol
        self.exchange = exchange
        self.quantity = quantity
        self.timeout = timeout
        message = (
            f"Failed to close naked position: {quantity} {symbol} on {exchange} "
            f"within {timeout} seconds. Manual intervention may be required."
        )
        super().__init__(message)


class DatabaseException(HedgeTradingException):
    """
    Raised when database operations fail.
    """

    def __init__(self, operation: str, details: str):
        self.operation = operation
        self.details = details
        message = f"Database {operation} failed: {details}"
        super().__init__(message)
