"""
Fee Reconciliation Manager
Tracks Bybit maker fee shortfall and places reconciliation orders.

Problem:
- Bybit deducts maker fees from received quantity (cumExecQty - cumExecFee = net_received)
- CoinDCX futures fees are in USDT, not deducted from crypto quantity
- This creates a small short position equal to the Bybit fees

Solution:
- Track cumulative Bybit fees across all chunks in a trade
- After all chunks complete, buy the missing quantity on Bybit
- Ensures perfect hedge: Bybit total received = CoinDCX total sold
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class FeeReconciliationManager:
    """Manages Bybit fee tracking and reconciliation orders"""

    def __init__(self, db, bybit_client, precision_config_path: str):
        """
        Initialize fee reconciliation manager.

        Args:
            db: Database instance
            bybit_client: BybitSpotClient instance
            precision_config_path: Path to bybit_precision.json
        """
        self.db = db
        self.bybit = bybit_client

        # Load Bybit precision configuration
        with open(precision_config_path, 'r') as f:
            config_data = json.load(f)
            self.precision_config = config_data['instruments']

        logger.info("FeeReconciliationManager initialized")

    def initialize_trade_reconciliation(
        self,
        chunk_group_id: str,
        symbol: str,
        total_chunks: int
    ) -> None:
        """
        Initialize reconciliation tracking for a new trade.

        Args:
            chunk_group_id: Unique ID for this trade
            symbol: Cryptocurrency symbol (BTC, ETH, etc.)
            total_chunks: Total number of chunks in this trade
        """
        try:
            with self.db.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO fee_reconciliation
                    (chunk_group_id, symbol, total_chunks, completed_chunks)
                    VALUES (%s, %s, %s, 0)
                    ON CONFLICT (chunk_group_id) DO NOTHING
                """, (chunk_group_id, symbol, total_chunks))
                self.db.conn.commit()

            logger.info(f"Initialized fee reconciliation for trade {chunk_group_id[:8]}...")

        except Exception as e:
            logger.error(f"Failed to initialize fee reconciliation: {e}")
            self.db.conn.rollback()

    def record_bybit_fill(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        cumexecqty: float = None,
        cumexecfee: float = None
    ) -> None:
        """
        Record Bybit fill and accumulate fee shortfall.

        For partial fills, this method uses get_chunk_total_fees() to sum
        both the partial order fee and the completion order fee.

        Args:
            chunk_group_id: Trade group ID
            chunk_sequence: Chunk sequence number
            cumexecqty: Executed quantity from Bybit (optional - will query DB if None)
            cumexecfee: Fee deducted by Bybit (optional - will query DB if None)
        """
        try:
            # Get total fees for this chunk (includes partial fill if any)
            fee_data = self.db.get_chunk_total_fees(
                chunk_group_id=chunk_group_id,
                chunk_sequence=chunk_sequence,
                exchange='bybit'
            )

            total_fee = fee_data['bybit_fee_crypto']
            is_partial = fee_data['is_partial_completion']

            # If cumexecqty not provided, query from orders table
            if cumexecqty is None:
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT cumexecqty FROM orders
                        WHERE chunk_group_id = %s
                          AND chunk_sequence = %s
                          AND exchange = 'bybit'
                    """, (chunk_group_id, chunk_sequence))
                    result = cursor.fetchone()
                    if result:
                        cumexecqty = float(result[0] or 0)
                    else:
                        cumexecqty = 0

            # For partial fills: cumexecqty is only from completion order
            # Need to add partial order qty to get total ordered
            if is_partial:
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT partial_filled_qty FROM orders
                        WHERE chunk_group_id = %s
                          AND chunk_sequence = %s
                          AND exchange = 'bybit'
                    """, (chunk_group_id, chunk_sequence))
                    result = cursor.fetchone()
                    if result and result[0]:
                        partial_qty = float(result[0])
                        total_ordered = cumexecqty + partial_qty
                    else:
                        total_ordered = cumexecqty
            else:
                total_ordered = cumexecqty

            # Calculate net received
            net_received = total_ordered - total_fee

            # Update cumulative totals
            with self.db.conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE fee_reconciliation
                    SET total_bybit_ordered = total_bybit_ordered + %s,
                        total_bybit_fee = total_bybit_fee + %s,
                        total_bybit_received = total_bybit_received + %s,
                        completed_chunks = completed_chunks + 1
                    WHERE chunk_group_id = %s
                """, (total_ordered, total_fee, net_received, chunk_group_id))
                self.db.conn.commit()

            partial_note = " (includes partial fill)" if is_partial else ""
            logger.debug(
                f"Recorded Bybit fill for {chunk_group_id[:8]}... chunk {chunk_sequence}: "
                f"Ordered={total_ordered:.8f}, Fee={total_fee:.8f}, "
                f"Received={net_received:.8f}{partial_note}"
            )

        except Exception as e:
            logger.error(f"Failed to record Bybit fill: {e}")
            self.db.conn.rollback()

    def check_and_reconcile(self, chunk_group_id: str) -> None:
        """
        Check if all chunks are complete and reconcile if needed.

        Args:
            chunk_group_id: Trade group ID to check
        """
        try:
            # Get reconciliation data
            with self.db.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT symbol, total_chunks, completed_chunks,
                           total_bybit_ordered, total_bybit_fee,
                           total_bybit_received
                    FROM fee_reconciliation
                    WHERE chunk_group_id = %s
                """, (chunk_group_id,))

                row = cursor.fetchone()

                if not row:
                    logger.warning(f"No reconciliation record found for {chunk_group_id}")
                    return

                symbol, total_chunks, completed, ordered, fee, received = row

                # Check if all chunks are complete
                if completed < total_chunks:
                    logger.debug(
                        f"Reconciliation pending: {completed}/{total_chunks} chunks complete"
                    )
                    return

            # All chunks complete - analyze reconciliation need
            logger.info(f"\n{'='*60}")
            logger.info(f"FEE RECONCILIATION ANALYSIS")
            logger.info(f"{'='*60}")
            logger.info(f"Trade: {chunk_group_id[:8]}...")
            logger.info(f"Symbol: {symbol}")
            logger.info(f"Chunks: {completed}/{total_chunks}")
            logger.info(f"\nBybit Summary:")
            logger.info(f"  Total Ordered:  {ordered:.8f} {symbol}")
            logger.info(f"  Total Fee:      {fee:.8f} {symbol}")
            logger.info(f"  Total Received: {received:.8f} {symbol}")
            logger.info(f"\nFee Shortfall: {fee:.8f} {symbol}")

            # Get precision and minimum order size
            precision = self._get_precision(symbol)
            min_qty = self._get_min_order_qty(symbol)

            # Round shortfall to exchange precision
            rounded_shortfall = self._round_to_precision(fee, symbol)

            logger.info(f"\nPrecision Analysis:")
            logger.info(f"  Base Precision: {precision} decimals")
            logger.info(f"  Rounded Shortfall: {rounded_shortfall:.{precision}f} {symbol}")
            logger.info(f"  Minimum Order: {min_qty:.8f} {symbol}")

            # Check if reconciliation order is needed
            if rounded_shortfall >= min_qty:
                # Place reconciliation order
                logger.info(f"\n✅ Shortfall above minimum - placing reconciliation order")
                self._place_reconciliation_order(
                    chunk_group_id, symbol, rounded_shortfall
                )
            else:
                # Below minimum - accept residual exposure
                price = self._get_current_price(symbol)
                residual_usd = rounded_shortfall * price if price else 0

                logger.info(f"\n⚠️  Shortfall below minimum order size")
                logger.info(f"  Residual Exposure: {rounded_shortfall:.{precision}f} {symbol}")
                logger.info(f"  Estimated Value: ${residual_usd:.2f} USD")

                if residual_usd < 1.00:
                    logger.info(f"  ✓ Accepting as negligible (<$1)")
                    notes = f"Residual ${residual_usd:.2f} accepted as negligible"
                else:
                    logger.warning(f"  ⚠️  Consider manual reconciliation or enable cumulative mode")
                    notes = f"Residual ${residual_usd:.2f} - below minimum to trade"

                # Mark as skipped
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE fee_reconciliation
                        SET reconciliation_needed = FALSE,
                            reconciliation_qty = %s,
                            reconciliation_status = 'SKIPPED_BELOW_MINIMUM',
                            completed_at = CURRENT_TIMESTAMP,
                            notes = %s
                        WHERE chunk_group_id = %s
                    """, (rounded_shortfall, notes, chunk_group_id))
                    self.db.conn.commit()

            logger.info(f"{'='*60}\n")

        except Exception as e:
            logger.error(f"Error during reconciliation check: {e}")
            self.db.conn.rollback()

    def _place_reconciliation_order(
        self,
        chunk_group_id: str,
        symbol: str,
        quantity: float
    ) -> None:
        """
        Place market BUY order on Bybit to reconcile fee shortfall.

        Args:
            chunk_group_id: Trade group ID
            symbol: Cryptocurrency symbol
            quantity: Quantity to buy (already rounded to precision)
        """
        try:
            # Get Bybit symbol format
            bybit_symbol = f"{symbol}USDT"

            logger.info(f"Placing reconciliation order:")
            logger.info(f"  Exchange: Bybit Spot")
            logger.info(f"  Type: MARKET BUY")
            logger.info(f"  Quantity: {quantity:.8f} {symbol}")

            # Place market order
            response = self.bybit.place_spot_order(
                symbol=bybit_symbol,
                side='Buy',
                order_type='Market',
                qty=str(quantity)
            )

            if response.get('success'):
                order_id = response.get('order_id')
                logger.info(f"✅ Reconciliation order placed: {order_id}")

                # Wait for fill (market orders fill almost instantly)
                time.sleep(2)

                # Get fill details
                fill_price = self._get_order_fill_price(bybit_symbol, order_id)

                # Update reconciliation record
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE fee_reconciliation
                        SET reconciliation_needed = TRUE,
                            reconciliation_qty = %s,
                            reconciliation_order_id = %s,
                            reconciliation_status = 'COMPLETED',
                            reconciliation_fill_price = %s,
                            completed_at = CURRENT_TIMESTAMP,
                            reconciled_at = CURRENT_TIMESTAMP,
                            notes = %s
                        WHERE chunk_group_id = %s
                    """, (
                        quantity,
                        order_id,
                        fill_price,
                        f"Market order filled @ ${fill_price:.2f}",
                        chunk_group_id
                    ))
                    self.db.conn.commit()

                logger.info(f"✅ Reconciliation complete")
                logger.info(f"  Fill Price: ${fill_price:.2f}")
                logger.info(f"  Total Cost: ${quantity * fill_price:.2f}")
                logger.info(f"  Hedge Status: PERFECT ✓")

            else:
                error = response.get('error', 'Unknown error')
                logger.error(f"❌ Reconciliation order failed: {error}")

                # Update status to FAILED
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE fee_reconciliation
                        SET reconciliation_status = 'FAILED',
                            notes = %s,
                            completed_at = CURRENT_TIMESTAMP
                        WHERE chunk_group_id = %s
                    """, (f"Order placement failed: {error}", chunk_group_id))
                    self.db.conn.commit()

                logger.error(f"⚠️  MANUAL INTERVENTION REQUIRED")
                logger.error(f"   You need to manually buy {quantity:.8f} {symbol} on Bybit")

        except Exception as e:
            logger.error(f"Exception placing reconciliation order: {e}")
            self.db.conn.rollback()

    def _get_precision(self, symbol: str) -> int:
        """Get base precision for symbol from config"""
        return self.precision_config[symbol]['basePrecision']

    def _get_min_order_qty(self, symbol: str) -> float:
        """Get minimum order quantity for symbol from config"""
        return self.precision_config[symbol]['minOrderQty']

    def _round_to_precision(self, quantity: float, symbol: str) -> float:
        """Round quantity to exchange precision"""
        precision = self._get_precision(symbol)
        return round(quantity, precision)

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol (for USD value calculation)"""
        try:
            # Import here to avoid circular dependency
            from .price_service import PriceService
            price_service = PriceService()
            bybit_price, _, _ = price_service.get_latest_prices(symbol)
            return bybit_price
        except Exception as e:
            logger.warning(f"Could not get current price for {symbol}: {e}")
            return None

    def _get_order_fill_price(self, symbol: str, order_id: str) -> float:
        """Get fill price for a completed order"""
        try:
            # Query order history
            response = self.bybit.get_order_history(symbol=symbol, limit=1)

            if response.get('success'):
                orders = response.get('orders', [])
                for order in orders:
                    if order.get('orderId') == order_id:
                        return float(order.get('avgPrice', 0))

            logger.warning(f"Could not get fill price for order {order_id}")
            return 0.0

        except Exception as e:
            logger.warning(f"Error getting fill price: {e}")
            return 0.0
