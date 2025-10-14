"""
Chunk Manager - Quantity-based chunking with minimum chunk sizes
Handles splitting large orders into smaller chunks based on exchange minimums.
"""

import logging
from typing import List, Tuple
from config.symbol_config import SymbolConfig
from utils.validators import Validators
from utils.exceptions import ValidationException


logger = logging.getLogger(__name__)


class ChunkManager:
    """Manages order chunking based on minimum quantity requirements"""

    def __init__(self):
        """Initialize chunk manager."""
        self.config = SymbolConfig()
        self.validators = Validators()

    def calculate_chunks(
        self,
        symbol: str,
        total_quantity: float
    ) -> tuple[List[float], dict]:
        """
        Calculate chunks from total quantity using exchange minimum as chunk size.

        IMPORTANT: This method NO LONGER auto-adds remainders to the last chunk.
        If there's a remainder, it returns info and lets the caller decide.

        Example:
            BTC: min_quantity = 0.002
            If user enters 0.005 BTC:
                - Returns ([0.002, 0.002], {'has_remainder': True, 'remainder': 0.001, ...})
                - Caller decides: trade 0.004 or 0.006

        Args:
            symbol: Cryptocurrency symbol (BTC/ETH)
            total_quantity: Total quantity to trade (in crypto units)

        Returns:
            tuple: (chunks_list, remainder_info_dict)
                chunks_list: List of chunk quantities (each chunk = min_quantity exactly)
                remainder_info_dict: {
                    'has_remainder': bool,
                    'remainder': float,
                    'lower_amount': float (tradeable without remainder),
                    'upper_amount': float (next valid amount)
                }

        Raises:
            ValidationException: If total_quantity is below minimum or invalid
        """
        # Get symbol config
        symbol_config = self.config.get_symbol_config(symbol)
        precision = symbol_config['precision']
        min_quantity = symbol_config['min_quantity']

        # Round total quantity to precision
        total_quantity = round(total_quantity, precision)

        # Validate minimum quantity
        if total_quantity < min_quantity:
            raise ValidationException(
                "total_quantity",
                total_quantity,
                f"Total quantity {total_quantity} below minimum {min_quantity} for {symbol}"
            )

        # Calculate number of full chunks (each chunk = min_quantity)
        num_full_chunks = int(total_quantity / min_quantity)

        # Calculate remainder
        used_quantity = num_full_chunks * min_quantity
        remainder = total_quantity - used_quantity
        remainder = round(remainder, precision)

        logger.info(
            f"Chunking {symbol}: Total {total_quantity:.{precision}f} {symbol}, "
            f"Chunk size: {min_quantity:.{precision}f} {symbol}, "
            f"Full chunks: {num_full_chunks}, Remainder: {remainder:.{precision}f}"
        )

        # Create chunks (do NOT add remainder)
        chunks = [min_quantity] * num_full_chunks

        # Build remainder info
        remainder_info = {
            'has_remainder': remainder > 0,
            'remainder': remainder,
            'lower_amount': round(num_full_chunks * min_quantity, precision),
            'upper_amount': round((num_full_chunks + 1) * min_quantity, precision),
            'num_full_chunks': num_full_chunks
        }

        if remainder > 0:
            logger.warning(
                f"⚠️ Remainder detected: {remainder:.{precision}f} {symbol} will NOT be traded"
            )
            logger.info(
                f"   Lower amount: {remainder_info['lower_amount']:.{precision}f} "
                f"({num_full_chunks} chunks)"
            )
            logger.info(
                f"   Upper amount: {remainder_info['upper_amount']:.{precision}f} "
                f"({num_full_chunks + 1} chunks)"
            )

        logger.info(
            f"Chunks created: {len(chunks)} chunks totaling {sum(chunks):.{precision}f} {symbol}"
        )

        return chunks, remainder_info

    def calculate_total_value(
        self,
        chunks: List[float],
        price: float
    ) -> float:
        """
        Calculate total value of all chunks in USD.

        Args:
            chunks: List of chunk quantities
            price: Price per unit

        Returns:
            Total value in USD
        """
        total_quantity = sum(chunks)
        total_value = total_quantity * price
        return total_value

    def apply_bybit_fee_compensation(
        self,
        symbol: str,
        chunks: List[float]
    ) -> List[float]:
        """
        Apply Bybit fee compensation to all chunks.
        Compensated qty = qty / (1 - fee)

        This ensures we receive the exact quantity after fees are deducted.

        Args:
            symbol: Cryptocurrency symbol
            chunks: List of chunk quantities

        Returns:
            List of fee-compensated chunks
        """
        compensated_chunks = [
            self.config.apply_bybit_fee_compensation(symbol, chunk)
            for chunk in chunks
        ]

        original_total = sum(chunks)
        compensated_total = sum(compensated_chunks)

        logger.info(
            f"Bybit fee compensation applied: "
            f"{original_total:.6f} → {compensated_total:.6f} {symbol} "
            f"(+{((compensated_total - original_total) / original_total * 100):.3f}%)"
        )

        return compensated_chunks

    def create_chunk_pairs(
        self,
        symbol: str,
        total_quantity: float
    ) -> Tuple[List[float], List[float]]:
        """
        Create matched chunk pairs for both exchanges.

        Args:
            symbol: Cryptocurrency symbol
            total_quantity: Total quantity to trade (in crypto units)

        Returns:
            Tuple of (bybit_chunks, coindcx_chunks)
            - bybit_chunks: Fee-compensated quantities for Bybit BUY orders
            - coindcx_chunks: Regular quantities for CoinDCX SELL orders
        """
        # Calculate base chunks
        base_chunks, remainder_info = self.calculate_chunks(symbol, total_quantity)

        # If there's a remainder, this should have been handled by caller
        # This method assumes caller already adjusted quantity
        if remainder_info['has_remainder']:
            logger.warning(
                f"⚠️ Remainder {remainder_info['remainder']} detected - "
                f"this should have been handled by caller"
            )

        # Apply Bybit fee compensation (for BUY orders)
        bybit_chunks = self.apply_bybit_fee_compensation(symbol, base_chunks)

        # CoinDCX chunks are same as base (no fee compensation needed for SELL)
        coindcx_chunks = base_chunks.copy()

        logger.info(
            f"Chunk pairs created: {len(base_chunks)} pairs\n"
            f"  Bybit (BUY):  {sum(bybit_chunks):.6f} {symbol}\n"
            f"  CoinDCX (SELL): {sum(coindcx_chunks):.6f} {symbol}"
        )

        return bybit_chunks, coindcx_chunks

    def preview_chunks(
        self,
        symbol: str,
        total_quantity: float,
        bybit_price: float,
        coindcx_price: float
    ) -> str:
        """
        Generate a preview of how the quantity will be chunked.

        Args:
            symbol: Cryptocurrency symbol
            total_quantity: Total quantity to trade
            bybit_price: Current Bybit price
            coindcx_price: Current CoinDCX price

        Returns:
            Formatted preview string
        """
        try:
            chunks, remainder_info = self.calculate_chunks(symbol, total_quantity)
            bybit_chunks, coindcx_chunks = self.create_chunk_pairs(symbol, total_quantity)

            symbol_config = self.config.get_symbol_config(symbol)
            precision = symbol_config['precision']
            min_quantity = symbol_config['min_quantity']

            # Calculate values
            total_value = total_quantity * bybit_price
            value_per_chunk = min_quantity * bybit_price

            preview = f"\n{'='*60}\n"
            preview += f"CHUNK PREVIEW - {symbol}\n"
            preview += f"{'='*60}\n\n"
            preview += f"Total Quantity: {total_quantity:.{precision}f} {symbol}\n"
            preview += f"Total Value: ${total_value:,.2f} USD\n\n"
            preview += f"Chunk Size: {min_quantity:.{precision}f} {symbol} "
            preview += f"(~${value_per_chunk:,.2f} per chunk)\n"
            preview += f"Number of Chunks: {len(chunks)}\n\n"

            # Show first few and last chunk
            preview += f"Chunk Distribution:\n"
            if len(chunks) <= 5:
                for i, chunk in enumerate(chunks, 1):
                    preview += f"  Chunk {i}: {chunk:.{precision}f} {symbol}\n"
            else:
                for i in range(3):
                    preview += f"  Chunk {i+1}: {chunks[i]:.{precision}f} {symbol}\n"
                preview += f"  ... ({len(chunks) - 4} more chunks)\n"
                preview += f"  Chunk {len(chunks)}: {chunks[-1]:.{precision}f} {symbol}"
                if chunks[-1] != min_quantity:
                    preview += f" (includes remainder)\n"
                else:
                    preview += "\n"

            preview += f"\nTotal to Execute: {sum(chunks):.{precision}f} {symbol}\n"
            preview += f"{'='*60}\n"

            return preview

        except ValidationException as e:
            return f"\n❌ Error: {e}\n"

    def on_order_update(
        self,
        exchange: str,
        order_id: str,
        status: str,
        fill_price: float = None,
        reject_reason: str = None
    ):
        """
        Callback from OrderMonitor when order status changes.

        This method is called by OrderMonitor via WebSocket when orders update.
        Currently just logs the update - can be extended for advanced chunk tracking.

        Args:
            exchange: Exchange name (Bybit/CoinDCX)
            order_id: Order ID
            status: New order status (FILLED/CANCELLED/REJECTED/etc)
            fill_price: Fill price if available
            reject_reason: Rejection reason if status is REJECTED
        """
        price_str = f"@ ${fill_price:.2f}" if fill_price else ""
        reject_str = f" (Reason: {reject_reason})" if reject_reason else ""
        logger.info(
            f"Order update callback: {exchange} {order_id[:8]}... → {status} {price_str}{reject_str}"
        )
