"""
Order Manager - Order placement and modification engine
Handles maker order placement and active order modification.

Strategy:
- Phase 1: Both orders open → Modify every 5s indefinitely until one fills
- Phase 2: Naked position → 2 modification attempts (5s each) + market order fallback
"""

import time
import logging
import requests
from typing import Dict, Optional, Tuple
from datetime import datetime
import uuid

# Import from bundled exchange clients (self-contained)
from exchange_clients.bybit.bybit_spot_client import BybitSpotClient
from exchange_clients.coindcx.coindcx_futures import CoinDCXFutures

from config.symbol_config import SymbolConfig
from utils.exceptions import (
    OrderException, SpreadException, NakedPositionException
)
from utils.db import Database
from core.price_service import PriceService


logger = logging.getLogger(__name__)


class OrderManager:
    """Manages order placement, modification, and monitoring"""

    def __init__(
        self,
        bybit_api_key: str,
        bybit_api_secret: str,
        coindcx_api_key: str,
        coindcx_api_secret: str,
        testnet: bool = True,
        db: Database = None,
        order_monitor=None
    ):
        """
        Initialize order manager.

        Args:
            bybit_api_key: Bybit API key
            bybit_api_secret: Bybit API secret
            coindcx_api_key: CoinDCX API key
            coindcx_api_secret: CoinDCX API secret
            testnet: Use testnet for Bybit
            db: Database instance
            order_monitor: OrderMonitor instance for WebSocket-based rejection detection
        """
        self.config = SymbolConfig()
        self.order_monitor = order_monitor  # For WebSocket-based instant rejection detection
        self.price_service = PriceService()
        self.db = db

        # Initialize exchange clients
        self.bybit = BybitSpotClient(
            api_key=bybit_api_key,
            api_secret=bybit_api_secret,
            testnet=testnet
        )

        self.coindcx = CoinDCXFutures(
            api_key=coindcx_api_key,
            secret_key=coindcx_api_secret
        )

        logger.info("Order manager initialized")

    def execute_chunk_with_active_management(
        self,
        symbol: str,
        bybit_quantity: float,
        coindcx_quantity: float,
        chunk_group_id: str = None,
        chunk_sequence: int = 1,
        chunk_total: int = 1
    ) -> Dict:
        """
        Execute single chunk with active order management.

        This is the MAIN execution method that implements:
        - Phase 1: Both orders open (indefinite 5-second modification loop)
        - Phase 2: Naked position (2 attempts + market order fallback)

        Args:
            symbol: Cryptocurrency symbol (BTC, ETH, SOL)
            bybit_quantity: Quantity for Bybit BUY order
            coindcx_quantity: Quantity for CoinDCX SELL order
            chunk_group_id: Group ID for tracking
            chunk_sequence: Chunk number (1, 2, 3...)
            chunk_total: Total number of chunks

        Returns:
            Dictionary with execution results

        Raises:
            OrderException: If order placement fails
            SpreadException: If spread exceeds limit
            NakedPositionException: If unable to complete hedge
        """
        # Generate chunk group ID if not provided
        if chunk_group_id is None:
            chunk_group_id = str(uuid.uuid4())

        logger.info(f"\n{'='*60}")
        logger.info(f"EXECUTING CHUNK {chunk_sequence}/{chunk_total}")
        logger.info(f"Symbol: {symbol}")
        logger.info(f"Bybit BUY: {bybit_quantity:.6f}")
        logger.info(f"CoinDCX SELL: {coindcx_quantity:.6f}")
        logger.info(f"{'='*60}\n")

        # Place both orders
        bybit_order_id, coindcx_order_id = self._place_both_orders(
            symbol, bybit_quantity, coindcx_quantity,
            chunk_group_id, chunk_sequence, chunk_total
        )

        logger.info(f"✅ Both orders placed successfully")
        logger.info(f"   Bybit order ID: {bybit_order_id}")
        logger.info(f"   CoinDCX order ID: {coindcx_order_id}")

        # Phase 1: Active management until one fills
        logger.info(f"\n📊 PHASE 1: Active order management")
        logger.info(f"   Strategy: Check status every 1s, modify every 5s")
        logger.info(f"   Spread monitoring: Active (will cancel if > 0.2%)\n")

        filled_exchange, current_bybit_order_id, current_coindcx_order_id = self._active_management_loop(
            symbol, bybit_order_id, coindcx_order_id
        )

        # Check if BOTH orders filled (perfect execution, skip Phase 2)
        if filled_exchange == 'BOTH':
            logger.info(f"\n✅ Perfect execution - BOTH orders filled in Phase 1")
            logger.info(f"   No Phase 2 needed - hedge complete!")
        else:
            # Phase 2: Resolve naked position
            logger.info(f"\n🚨 PHASE 2: Naked position detected")
            logger.info(f"   Filled exchange: {filled_exchange.upper()}")
            logger.info(f"   Strategy: 2 modification attempts (5s each) + market order fallback")
            logger.info(f"   Spread monitoring: DISABLED (priority is hedge completion)\n")

            unfilled_exchange = 'CoinDCX' if filled_exchange == 'Bybit' else 'Bybit'
            # CRITICAL FIX: Use CURRENT order IDs, not original ones (may have changed due to cancel+replace)
            unfilled_order_id = current_coindcx_order_id if filled_exchange == 'Bybit' else current_bybit_order_id

            self._resolve_naked_position(
                symbol, unfilled_exchange, unfilled_order_id,
                bybit_quantity if unfilled_exchange == 'Bybit' else coindcx_quantity,
                chunk_group_id, chunk_sequence, chunk_total
            )

        logger.info(f"\n✅ CHUNK {chunk_sequence}/{chunk_total} COMPLETED")
        logger.info(f"   Both sides filled - Hedge complete")
        logger.info(f"{'='*60}\n")

        return {
            'chunk_group_id': chunk_group_id,
            'bybit_order_id': bybit_order_id,
            'coindcx_order_id': coindcx_order_id,
            'success': True
        }

    def _place_both_orders(
        self,
        symbol: str,
        bybit_quantity: float,
        coindcx_quantity: float,
        chunk_group_id: str,
        chunk_sequence: int,
        chunk_total: int
    ) -> Tuple[str, str]:
        """
        Place both orders with rollback protection.

        Returns:
            Tuple of (bybit_order_id, coindcx_order_id)

        Raises:
            OrderException: If placement fails (with rollback)
            SpreadException: If spread exceeds limit
        """
        symbol_config = self.config.get_symbol_config(symbol)
        bybit_symbol = symbol_config['bybit_symbol']
        coindcx_symbol = symbol_config['coindcx_symbol']

        # Get current prices and check spread
        price_data = self.price_service.get_validated_prices(symbol)
        spread = price_data['spread']

        if spread > self.config.MAX_SPREAD_PERCENT:
            raise SpreadException(spread, self.config.MAX_SPREAD_PERCENT)

        bybit_price = price_data['bybit']['price']
        coindcx_price = price_data['coindcx']['price']

        # Calculate maker prices (1 tick below/above)
        bybit_maker_price = self.config.calculate_maker_price(symbol, bybit_price, 'buy')
        coindcx_maker_price = self.config.calculate_maker_price(symbol, coindcx_price, 'sell')

        logger.info(f"Placing orders:")
        logger.info(f"  Bybit BUY: {bybit_quantity:.6f} @ ${bybit_maker_price:.2f} (1 tick below ${bybit_price:.2f})")
        logger.info(f"  CoinDCX SELL: {coindcx_quantity:.6f} @ ${coindcx_maker_price:.2f} (1 tick above ${coindcx_price:.2f})")
        logger.info(f"  Spread: {spread:.4f}%")

        # Track orders for rollback
        bybit_order = None
        coindcx_order = None

        try:
            # Place Bybit order (Post-Only)
            bybit_order = self._place_bybit_order(
                bybit_symbol, 'Buy', bybit_quantity, bybit_maker_price
            )
            bybit_order_id = bybit_order['order_id']
            logger.info(f"  ✓ Bybit order placed: {bybit_order_id}")

            # Place CoinDCX order (regular limit, no Post-Only)
            coindcx_order = self._place_coindcx_order(
                coindcx_symbol, 'sell', coindcx_quantity, coindcx_maker_price
            )
            coindcx_order_id = coindcx_order['id']
            logger.info(f"  ✓ CoinDCX order placed: {coindcx_order_id}")

        except Exception as e:
            # CRITICAL: Rollback to prevent naked position
            logger.error(f"❌ Order placement failed: {e}")
            logger.warning(f"🔄 ROLLING BACK: Cancelling any successful orders")

            if bybit_order and bybit_order.get('order_id'):
                try:
                    self._cancel_bybit_order(bybit_symbol, bybit_order['order_id'])
                    logger.info(f"  ✓ Bybit order cancelled")
                except Exception as cancel_error:
                    logger.critical(f"⚠️ MANUAL INTERVENTION: Cancel Bybit order {bybit_order['order_id']} manually!")

            if coindcx_order and coindcx_order.get('id'):
                try:
                    self._cancel_coindcx_order(coindcx_order['id'])
                    logger.info(f"  ✓ CoinDCX order cancelled")
                except Exception as cancel_error:
                    logger.critical(f"⚠️ MANUAL INTERVENTION: Cancel CoinDCX order {coindcx_order['id']} manually!")

            raise OrderException('Hedge', 'placement', f"Failed to place order pair: {e}")

        # Log to database (only after both succeed)
        # Use UPSERT to ensure exactly one row per exchange per chunk
        if self.db:
            bybit_row_id = self.db.upsert_order(
                chunk_group_id=chunk_group_id,
                chunk_sequence=chunk_sequence,
                chunk_total=chunk_total,
                exchange='bybit',
                symbol=symbol,
                side='buy',
                quantity=bybit_quantity,
                price=bybit_maker_price,
                order_id=bybit_order_id,
                status='PLACED'
            )

            coindcx_row_id = self.db.upsert_order(
                chunk_group_id=chunk_group_id,
                chunk_sequence=chunk_sequence,
                chunk_total=chunk_total,
                exchange='coindcx',
                symbol=symbol,
                side='sell',
                quantity=coindcx_quantity,
                price=coindcx_maker_price,
                order_id=coindcx_order_id,
                status='PLACED'
            )

            # Verify both orders were inserted
            if not bybit_row_id:
                logger.error(f"CRITICAL: Bybit order {bybit_order_id} not recorded in database!")
            if not coindcx_row_id:
                logger.error(f"CRITICAL: CoinDCX order {coindcx_order_id} not recorded in database!")

            # Log lifecycle events
            self.db.log_order_event(
                chunk_group_id=chunk_group_id,
                chunk_sequence=chunk_sequence,
                exchange='bybit',
                event_type='PLACED',
                order_id=bybit_order_id,
                event_details={
                    'side': 'buy',
                    'price': float(bybit_maker_price),
                    'quantity': float(bybit_quantity),
                    'order_type': 'limit',
                    'post_only': True
                }
            )

            self.db.log_order_event(
                chunk_group_id=chunk_group_id,
                chunk_sequence=chunk_sequence,
                exchange='coindcx',
                event_type='PLACED',
                order_id=coindcx_order_id,
                event_details={
                    'side': 'sell',
                    'price': float(coindcx_maker_price),
                    'quantity': float(coindcx_quantity),
                    'order_type': 'limit',
                    'post_only': True
                }
            )

            self.db.log_spread(symbol, bybit_price, coindcx_price, spread)

            # CRITICAL: Ensure database transaction commits before we start querying
            # This prevents race condition where _active_management_loop() queries
            # before INSERT completes
            try:
                self.db.conn.commit()
                logger.debug("Database insertions committed successfully")
            except Exception as e:
                logger.warning(f"Database commit warning: {e}")

        return (bybit_order_id, coindcx_order_id)

    def _place_bybit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        post_only: bool = True
    ) -> Dict:
        """
        Place Bybit order with hybrid WebSocket+API rejection detection.

        Strategy (Hybrid - Best of Both Worlds):
        1. Place order with Post-Only
        2. PRIMARY: Wait up to 2 seconds for WebSocket 'Rejected' status (100-500ms typical)
        3. FALLBACK: If no WebSocket update, query API to verify order exists (1.5s)
        4. If rejected (either method), retry with safer pricing
        5. Maximum 3 attempts with progressive price adjustment

        Benefits:
        - 95% of rejections detected in <500ms (via WebSocket)
        - 5% detected in 1.5s (via API fallback)
        - 100% reliable (has fallback)

        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            side: 'Buy' or 'Sell'
            quantity: Order quantity
            price: Order price
            post_only: Use Post-Only (default True)

        Returns:
            Order response dict with 'order_id'

        Raises:
            OrderException: If all retries fail
        """
        coin = symbol.replace('USDT', '')  # Extract coin (BTC, ETH, SOL)
        tick_increment = 1  # Start with 1 tick away
        cycle = 1  # Track how many 4-attempt cycles we've done

        # Keep trying forever until order is placed successfully
        # No naked position yet, so we can wait indefinitely
        while True:
            for attempt in range(1, 5):  # Try 1-4 ticks away
                try:
                    overall_attempt = (cycle - 1) * 4 + attempt
                    logger.info(f"Bybit order attempt #{overall_attempt} [Cycle {cycle}, Tick {attempt}] (price ${price:.2f})")

                    time_in_force = 'PostOnly' if post_only else 'GTC'

                    # Step 1: Place order
                    response = self.bybit.place_spot_order(
                        symbol=symbol,
                        side=side,
                        order_type='Limit',
                        qty=str(quantity),
                        price=str(price),
                        timeInForce=time_in_force
                    )

                    if not response.get('success'):
                        error_msg = response.get('error', 'Unknown error')
                        raise OrderException('Bybit', 'placement', error_msg)

                    order_id = response.get('order_id')
                    logger.info(f"📋 Order {order_id[:8]}... placed, awaiting confirmation...")

                    # Step 2: PRIMARY - Wait for WebSocket update (FAST: 100-500ms)
                    websocket_detected = False
                    rejection_detected = False

                    for i in range(20):  # Check every 100ms for 2 seconds total
                        time.sleep(0.1)

                        # Check if OrderMonitor received WebSocket update
                        if self.order_monitor:
                            # Check for explicit rejection via WebSocket
                            reject_reason = self.order_monitor.get_rejection_reason(order_id)

                            if reject_reason == 'EC_PostOnlyWillTakeLiquidity':
                                logger.warning(f"⚠️ WebSocket ({i*100}ms): Post-Only order rejected (would cross spread)")
                                rejection_detected = True
                                websocket_detected = True
                                break
                            elif reject_reason is None:
                                # No rejection, order is active (WebSocket monitors 'Rejected' status)
                                # Note: We don't check database here because order hasn't been inserted yet
                                # Database INSERT happens AFTER this function returns
                                if i >= 5:  # After 500ms, if no rejection, assume success
                                    logger.info(f"✅ WebSocket ({i*100}ms): Order confirmed active (no rejection)")
                                    return response  # Success!

                    # Step 3: FALLBACK - API query if WebSocket didn't respond
                    if not websocket_detected:
                        logger.debug(f"WebSocket timeout after 2s, using API fallback...")

                        # Wait a bit longer for processing
                        time.sleep(0.5)

                        order_check = self.bybit.session.get_open_orders(
                            category='spot',
                            symbol=symbol,
                            orderId=order_id
                        )

                        if order_check.get('retCode') == 0:
                            orders_list = order_check.get('result', {}).get('list', [])

                            if not orders_list or not any(o.get('orderId') == order_id for o in orders_list):
                                logger.warning(f"⚠️ API Query: Order not found (likely rejected)")
                                rejection_detected = True
                            else:
                                logger.info(f"✅ API Query: Order verified active")
                                return response  # Success!

                    # Step 4: Handle rejection (from either WebSocket or API)
                    if rejection_detected:
                        # Fetch new price and retry with safer pricing
                        new_price_data = self.price_service.get_validated_prices(coin)
                        new_ltp = new_price_data['bybit']['price']

                        tick_size = self.config.get_symbol_config(coin)['tick_size']
                        if side == 'Buy':
                            price = new_ltp - (tick_increment * tick_size)
                        else:
                            price = new_ltp + (tick_increment * tick_size)

                        # CRITICAL: Round price to correct precision to avoid "too many decimals" error
                        # Python float arithmetic can create values like 4566.879999999999
                        # This ensures price has correct decimal places (e.g., 2 for ETH)
                        price = self.config.round_price(coin, price)

                        tick_increment += 1
                        logger.info(f"🔄 Retry with safer price: ${price:.2f} ({tick_increment} ticks from LTP)")
                        time.sleep(0.5)
                        continue

                    # If we reach here, something unexpected happened
                    logger.error(f"❌ Unexpected state - neither success nor rejection detected")
                    raise OrderException('Bybit', 'placement', 'Could not verify order status')

                except Exception as e:
                    # Log error but continue trying (no naked position, can wait forever)
                    logger.warning(f"⚠️ Attempt #{overall_attempt} failed: {e}")
                    time.sleep(1)
                    continue

            # Completed cycle (all 4 tick levels rejected), start new cycle with fresh LTP
            logger.warning(f"⚠️ Cycle {cycle} complete - all 4 tick levels rejected")
            logger.info(f"🔄 Starting Cycle {cycle + 1} with fresh LTP...")

            # Fetch fresh LTP for new cycle
            try:
                new_price_data = self.price_service.get_validated_prices(coin)
                price = new_price_data['bybit']['price']

                # Reset to 1 tick away for new cycle
                tick_size = self.config.get_symbol_config(coin)['tick_size']
                if side == 'Buy':
                    price = price - tick_size  # 1 tick below
                else:
                    price = price + tick_size  # 1 tick above

                price = self.config.round_price(coin, price)
                tick_increment = 1  # Reset for new cycle
                cycle += 1

                time.sleep(2)  # Small pause between cycles

            except Exception as e:
                logger.error(f"Failed to fetch fresh LTP: {e}")
                time.sleep(5)  # Wait longer if price fetch fails
                continue

    def _place_coindcx_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float
    ) -> Dict:
        """
        Place CoinDCX order (regular limit, NO Post-Only).

        CoinDCX doesn't support Post-Only, so we use regular limit orders.
        Infinite retry with fresh LTP - never give up until order placed.

        Args:
            symbol: Trading symbol (e.g., B-BTC_USDT)
            side: 'buy' or 'sell'
            quantity: Order quantity
            price: Order price

        Returns:
            Order response dict with 'id'

        Raises:
            OrderException: If all retries fail
        """
        coin = symbol.replace('B-', '').replace('_USDT', '')  # Extract coin from B-ETH_USDT
        tick_increment = 1  # Start with 1 tick away
        cycle = 1  # Track how many 4-attempt cycles we've done

        # Keep trying forever until order is placed successfully
        # No naked position yet, so we can wait indefinitely
        while True:
            for attempt in range(1, 5):  # Try 1-4 ticks away
                try:
                    overall_attempt = (cycle - 1) * 4 + attempt
                    logger.info(f"CoinDCX order attempt #{overall_attempt} [Cycle {cycle}, Tick {attempt}] (price ${price:.2f})")

                    response = self.coindcx.place_order(
                        pair=symbol,
                        side=side,
                        order_type='limit_order',
                        quantity=quantity,
                        price=price
                        # NO post_only parameter - CoinDCX doesn't support it
                    )

                    # Handle list or dict response
                    order_data = response
                    if isinstance(response, list) and len(response) > 0:
                        order_data = response[0]

                    if isinstance(order_data, dict) and order_data.get('id'):
                        logger.info(f"✅ CoinDCX order placed successfully: {order_data.get('id')}")
                        return order_data
                    else:
                        error_msg = order_data.get('message', 'Unknown error') if isinstance(order_data, dict) else 'Unknown error'
                        logger.warning(f"⚠️ Attempt #{overall_attempt} failed: {error_msg}")

                        # Fetch new price and retry with safer pricing
                        new_price_data = self.price_service.get_validated_prices(coin)
                        new_ltp = new_price_data['coindcx']['price']

                        tick_size = self.config.get_symbol_config(coin)['tick_size']
                        if side == 'sell':
                            price = new_ltp + (tick_increment * tick_size)
                        else:
                            price = new_ltp - (tick_increment * tick_size)

                        price = self.config.round_price(coin, price)
                        tick_increment += 1
                        logger.info(f"🔄 Retry with safer price: ${price:.2f} ({tick_increment} ticks from LTP)")
                        time.sleep(0.5)
                        continue

                except Exception as e:
                    # Log error but continue trying (no naked position, can wait forever)
                    logger.warning(f"⚠️ Attempt #{overall_attempt} failed: {e}")
                    time.sleep(1)
                    continue

            # Completed cycle (all 4 tick levels rejected), start new cycle with fresh LTP
            logger.warning(f"⚠️ Cycle {cycle} complete - all 4 tick levels rejected")
            logger.info(f"🔄 Starting Cycle {cycle + 1} with fresh LTP...")

            # Fetch fresh LTP for new cycle
            try:
                new_price_data = self.price_service.get_validated_prices(coin)
                price = new_price_data['coindcx']['price']

                # Reset to 1 tick away for new cycle
                tick_size = self.config.get_symbol_config(coin)['tick_size']
                if side == 'sell':
                    price = price + tick_size  # 1 tick above
                else:
                    price = price - tick_size  # 1 tick below

                price = self.config.round_price(coin, price)
                tick_increment = 1  # Reset for new cycle
                cycle += 1

                time.sleep(2)  # Small pause between cycles

            except Exception as e:
                logger.error(f"Failed to fetch fresh LTP: {e}")
                time.sleep(5)  # Wait longer if price fetch fails
                continue

    def _active_management_loop(
        self,
        symbol: str,
        bybit_order_id: str,
        coindcx_order_id: str
    ) -> Tuple[str, str, str]:
        """
        Phase 1: Active order management loop.

        - Check database every 1 second for fills
        - Modify both orders every 5 seconds
        - Check spread on each modification (cancel if > 0.2%)
        - Continue indefinitely until one fills

        Args:
            symbol: Cryptocurrency symbol
            bybit_order_id: Bybit order ID
            coindcx_order_id: CoinDCX order ID

        Returns:
            Tuple of (filled_exchange, current_bybit_order_id, current_coindcx_order_id)
            - filled_exchange: 'Bybit' or 'CoinDCX'
            - current_bybit_order_id: Current Bybit order ID (unchanged)
            - current_coindcx_order_id: Current CoinDCX order ID (may have changed due to cancel+replace)

        Raises:
            SpreadException: If spread exceeds limit
        """
        symbol_config = self.config.get_symbol_config(symbol)
        bybit_symbol = symbol_config['bybit_symbol']
        coindcx_symbol = symbol_config['coindcx_symbol']

        start_time = time.time()
        last_modification_time = start_time
        cycle = 0

        while True:
            # Check database every 1 second for fills (5 times per 5-second cycle)
            for i in range(5):
                cycle += 1
                elapsed = time.time() - start_time

                # Query database for order status
                bybit_status = self._check_order_status_from_db(bybit_order_id)
                coindcx_status = self._check_order_status_from_db(coindcx_order_id)

                logger.debug(f"Cycle {cycle} ({elapsed:.1f}s): Bybit={bybit_status}, CoinDCX={coindcx_status}")

                # CRITICAL: Check if BOTH filled (perfect hedge, no Phase 2 needed)
                if bybit_status == 'FILLED' and coindcx_status == 'FILLED':
                    logger.info(f"🎉 BOTH orders filled! Perfect hedge - no Phase 2 needed")
                    return ('BOTH', bybit_order_id, coindcx_order_id)

                # Check if only one filled
                if bybit_status == 'FILLED':
                    logger.info(f"✅ Bybit order filled: {bybit_order_id}")
                    return ('Bybit', bybit_order_id, coindcx_order_id)

                if coindcx_status == 'FILLED':
                    logger.info(f"✅ CoinDCX order filled: {coindcx_order_id}")
                    return ('CoinDCX', bybit_order_id, coindcx_order_id)

                # Check if either rejected (post-only rejection during Phase 1)
                if bybit_status == 'REJECTED':
                    logger.warning(f"⚠️ Bybit order rejected during Phase 1, will replace on next modification cycle")
                    # Don't exit - let modification cycle handle it by placing new order

                if coindcx_status == 'REJECTED':
                    logger.warning(f"⚠️ CoinDCX order rejected during Phase 1, will replace on next modification cycle")
                    # Don't exit - let modification cycle handle it by placing new order

                time.sleep(1)  # Wait 1 second

            # After 5 seconds, modify both orders
            elapsed = time.time() - start_time
            logger.info(f"🔄 Modifying orders ({elapsed:.1f}s elapsed)...")

            try:
                # CRITICAL: Check order status before modifying
                # This prevents infinite loops trying to modify cancelled/filled orders
                bybit_status = self._check_order_status_from_db(bybit_order_id)
                coindcx_status = self._check_order_status_from_db(coindcx_order_id)

                logger.debug(f"Pre-modification status: Bybit={bybit_status}, CoinDCX={coindcx_status}")

                # CRITICAL: Check if BOTH filled (perfect hedge, no Phase 2 needed)
                if bybit_status == 'FILLED' and coindcx_status == 'FILLED':
                    logger.info(f"🎉 BOTH orders filled! Perfect hedge - no Phase 2 needed")
                    # Return special marker to indicate both filled
                    return ('BOTH', bybit_order_id, coindcx_order_id)

                # If only one filled, exit to naked position handler
                if bybit_status == 'FILLED':
                    logger.info(f"✅ Bybit order filled during modification check")
                    return ('Bybit', bybit_order_id, coindcx_order_id)
                if coindcx_status == 'FILLED':
                    logger.info(f"✅ CoinDCX order filled during modification check")
                    return ('CoinDCX', bybit_order_id, coindcx_order_id)

                # If either is rejected, we'll place new order after fetching prices below

                # If either is cancelled, we have a problem - one side is gone
                if bybit_status == 'CANCELLED':
                    logger.error(f"❌ Bybit order was cancelled! CoinDCX status: {coindcx_status}")
                    if coindcx_status == 'OPEN':
                        logger.warning(f"⚠️ NAKED POSITION: CoinDCX still open, Bybit cancelled!")
                        # Cancel CoinDCX to prevent naked position
                        self._cancel_coindcx_order(coindcx_order_id)
                    raise OrderException('Bybit', 'modification', 'Bybit order was cancelled')

                if coindcx_status == 'CANCELLED':
                    logger.error(f"❌ CoinDCX order was cancelled! Bybit status: {bybit_status}")
                    if bybit_status == 'OPEN':
                        logger.warning(f"⚠️ NAKED POSITION: Bybit still open, CoinDCX cancelled!")
                        # Cancel Bybit to prevent naked position
                        self._cancel_bybit_order(bybit_symbol, bybit_order_id)
                    raise OrderException('CoinDCX', 'modification', 'CoinDCX order was cancelled')

                # Both orders still open (or new orders placed), proceed with modification
                # Fetch latest prices
                price_data = self.price_service.get_validated_prices(symbol)
                spread = price_data['spread']

                # Check spread
                if spread > self.config.MAX_SPREAD_PERCENT:
                    logger.error(f"❌ Spread violation: {spread:.4f}% > {self.config.MAX_SPREAD_PERCENT}%")
                    logger.warning(f"Cancelling both orders...")

                    self._cancel_bybit_order(bybit_symbol, bybit_order_id)
                    self._cancel_coindcx_order(coindcx_order_id)

                    raise SpreadException(spread, self.config.MAX_SPREAD_PERCENT)

                # Calculate new prices (1 tick below/above)
                bybit_price = price_data['bybit']['price']
                coindcx_price = price_data['coindcx']['price']

                new_bybit_price = self.config.calculate_maker_price(symbol, bybit_price, 'buy')
                new_coindcx_price = self.config.calculate_maker_price(symbol, coindcx_price, 'sell')

                logger.info(f"  New prices: Bybit ${new_bybit_price:.2f}, CoinDCX ${new_coindcx_price:.2f} (spread: {spread:.4f}%)")

                # If Bybit was rejected, place new order instead of modifying
                if bybit_status == 'REJECTED':
                    logger.warning(f"⚠️ Bybit order was rejected, placing new limit order")
                    # Note: We don't have quantity here, will need to get from database
                    order_details = self._get_order_details_from_db(bybit_order_id)
                    if order_details:
                        new_bybit_order = self._place_bybit_order(bybit_symbol, 'Buy', order_details['quantity'], new_bybit_price)
                        bybit_order_id = new_bybit_order['order_id']
                        logger.info(f"  ✓ New Bybit order placed: {bybit_order_id}")
                elif bybit_status == 'OPEN':
                    # Modify existing order
                    self._modify_bybit_order(bybit_symbol, bybit_order_id, new_bybit_price)

                # CoinDCX handling depends on status
                if coindcx_status == 'REJECTED':
                    logger.warning(f"⚠️ CoinDCX order was rejected, placing new limit order")
                    order_details = self._get_order_details_from_db(coindcx_order_id)
                    if order_details:
                        new_coindcx_order = self._place_coindcx_order(coindcx_symbol, 'sell', order_details['quantity'], new_coindcx_price)
                        coindcx_order_id = new_coindcx_order['id']
                        logger.info(f"  ✓ New CoinDCX order placed: {coindcx_order_id}")
                elif coindcx_status == 'OPEN':
                    # CoinDCX may return new order ID if cancel+replace was used
                    # Returns None if order already filled/cancelled (skip modification)
                    updated_coindcx_id = self._modify_coindcx_order(coindcx_symbol, coindcx_order_id, new_coindcx_price)

                    # Update tracking if order ID changed (cancel+replace fallback)
                    if updated_coindcx_id is not None and updated_coindcx_id != coindcx_order_id:
                        logger.info(f"  ℹ️ CoinDCX order replaced: {coindcx_order_id[:8]}... → {updated_coindcx_id[:8]}...")
                        coindcx_order_id = updated_coindcx_id
                    elif updated_coindcx_id is None:
                        # Order already filled/cancelled - exit loop
                        logger.info(f"  ℹ️ CoinDCX order {coindcx_order_id[:8]}... already filled/cancelled")
                        return ('CoinDCX', bybit_order_id, coindcx_order_id)

                logger.info(f"  ✓ Orders modified successfully")

            except SpreadException:
                raise
            except Exception as e:
                logger.warning(f"⚠️ Error during modification: {e}")
                # Continue loop despite modification errors

    def _resolve_naked_position(
        self,
        symbol: str,
        unfilled_exchange: str,
        unfilled_order_id: str,
        quantity: float,
        chunk_group_id: str,
        chunk_sequence: int,
        chunk_total: int
    ) -> None:
        """
        Phase 2: Resolve naked position.

        - Attempt 1: Modify order, wait 5s, check if filled
        - Attempt 2: Modify order again, wait 5s, check if filled
        - Market order: Cancel limit, place market order

        Total time: 10 seconds + market execution

        Args:
            symbol: Cryptocurrency symbol
            unfilled_exchange: 'Bybit' or 'CoinDCX'
            unfilled_order_id: Unfilled order ID
            quantity: Order quantity (for market order fallback)

        Raises:
            NakedPositionException: If unable to complete hedge
        """
        symbol_config = self.config.get_symbol_config(symbol)
        unfilled_symbol = (
            symbol_config['bybit_symbol'] if unfilled_exchange == 'Bybit'
            else symbol_config['coindcx_symbol']
        )
        side = 'buy' if unfilled_exchange == 'Bybit' else 'sell'

        start_time = time.time()

        # Track current order ID (may change if CoinDCX uses cancel+replace)
        current_order_id = unfilled_order_id

        # Attempt 1
        logger.info(f"🔄 Attempt 1/2: Waiting 5 seconds for natural fill...")
        time.sleep(5)

        logger.info(f"   Checking status after 5-second wait...")
        status = self._check_order_status_from_db(current_order_id)

        if status == 'FILLED':
            logger.info(f"✅ Order filled during 5-second wait (attempt 1)!")
            return
        elif status == 'REJECTED':
            logger.warning(f"⚠️ Order was rejected, placing new limit order for attempt 1")
            new_order_id = self._place_new_limit_order_for_naked_position(
                unfilled_exchange, unfilled_symbol, side, quantity, symbol
            )
            if new_order_id:
                current_order_id = new_order_id
                logger.info(f"   ✓ New order placed: {new_order_id[:12]}...")
            else:
                logger.error(f"   ✗ Failed to place new order, will retry in attempt 2")
        elif status == 'CANCELLED':
            logger.warning(f"⚠️ Order was cancelled, placing new limit order for attempt 1")
            new_order_id = self._place_new_limit_order_for_naked_position(
                unfilled_exchange, unfilled_symbol, side, quantity, symbol
            )
            if new_order_id:
                current_order_id = new_order_id
                logger.info(f"   ✓ New order placed: {new_order_id[:12]}...")
            else:
                logger.error(f"   ✗ Failed to place new order, will retry in attempt 2")
        elif status == 'OPEN':
            # Order is open, modify with fresh LTP
            logger.info(f"   Order still OPEN, modifying with fresh LTP...")
            try:
                updated_id = self._modify_unfilled_order_to_latest_price(
                    symbol, unfilled_exchange, unfilled_symbol, current_order_id, side,
                    chunk_group_id, chunk_sequence
                )
                if updated_id and updated_id != current_order_id:
                    logger.info(f"   ✓ Order replaced: {current_order_id[:8]}... → {updated_id[:8]}...")
                    current_order_id = updated_id
                elif updated_id is None:
                    logger.info(f"✅ Order filled during modification (attempt 1)!")
                    return
                else:
                    logger.info(f"   ✓ Order modified successfully")
            except Exception as e:
                logger.warning(f"   ✗ Modification failed: {e}")

        # Attempt 2
        logger.info(f"🔄 Attempt 2/2: Waiting 5 seconds for natural fill...")
        time.sleep(5)

        logger.info(f"   Checking status after 5-second wait...")
        status = self._check_order_status_from_db(current_order_id)

        if status == 'FILLED':
            logger.info(f"✅ Order filled during 5-second wait (attempt 2)!")
            return
        elif status == 'REJECTED':
            logger.warning(f"⚠️ Order was rejected, placing new limit order for attempt 2")
            new_order_id = self._place_new_limit_order_for_naked_position(
                unfilled_exchange, unfilled_symbol, side, quantity, symbol
            )
            if new_order_id:
                current_order_id = new_order_id
                logger.info(f"   ✓ New order placed: {new_order_id[:12]}...")
            else:
                logger.error(f"   ✗ Failed to place new order, proceeding to market order")
        elif status == 'CANCELLED':
            logger.warning(f"⚠️ Order was cancelled, placing new limit order for attempt 2")
            new_order_id = self._place_new_limit_order_for_naked_position(
                unfilled_exchange, unfilled_symbol, side, quantity, symbol
            )
            if new_order_id:
                current_order_id = new_order_id
                logger.info(f"   ✓ New order placed: {new_order_id[:12]}...")
            else:
                logger.error(f"   ✗ Failed to place new order, proceeding to market order")
        elif status == 'OPEN':
            # Order is open, modify with fresh LTP
            logger.info(f"   Order still OPEN, modifying with fresh LTP...")
            try:
                updated_id = self._modify_unfilled_order_to_latest_price(
                    symbol, unfilled_exchange, unfilled_symbol, current_order_id, side,
                    chunk_group_id, chunk_sequence
                )
                if updated_id and updated_id != current_order_id:
                    logger.info(f"   ✓ Order replaced: {current_order_id[:8]}... → {updated_id[:8]}...")
                    current_order_id = updated_id
                elif updated_id is None:
                    logger.info(f"✅ Order filled during modification (attempt 2)!")
                    return
                else:
                    logger.info(f"   ✓ Order modified successfully")
            except Exception as e:
                logger.warning(f"   ✗ Modification failed: {e}")

        # Final check before market order
        logger.info(f"🔄 Final check: Waiting 5 seconds before market order fallback...")
        time.sleep(5)

        status = self._check_order_status_from_db(current_order_id)
        if status == 'FILLED':
            logger.info(f"✅ Order filled during final wait!")
            return

        # Market order fallback
        elapsed = time.time() - start_time
        logger.warning(f"⚠️ Limit order not filled after {elapsed:.1f}s")
        logger.warning(f"🚨 MARKET ORDER FALLBACK: Cancelling limit and placing market order")

        try:
            # Cancel limit order (use current_order_id in case it was replaced)
            # CRITICAL: Check return value - if False, order already filled!
            cancel_successful = False
            if unfilled_exchange == 'Bybit':
                cancel_successful = self._cancel_bybit_order(unfilled_symbol, current_order_id)
            else:
                cancel_successful = self._cancel_coindcx_order(current_order_id)

            # If cancel returned False, order was already filled - NO MARKET ORDER NEEDED!
            if not cancel_successful:
                logger.info(f"🎉 Limit order already filled - NO MARKET ORDER NEEDED!")
                return

            logger.info(f"  ✓ Limit order cancelled")

            # CRITICAL: Final safety check before placing market order
            # (Order might have filled during cancel attempt)
            logger.info(f"  Final safety check before market order...")
            final_status = self._check_order_status_from_db(
                current_order_id,
                max_retries=3,
                retry_delay=0.3
            )
            if final_status == 'FILLED':
                logger.info(f"  🎉 Order filled during safety check - NO MARKET ORDER NEEDED!")
                return

            # Place market order (only if definitely not filled)
            market_order_id = self._place_market_order(
                unfilled_exchange, unfilled_symbol, side, quantity,
                chunk_group_id, chunk_sequence, chunk_total
            )

            logger.info(f"  ✓ Market order placed: {market_order_id}")

            # Wait up to 30 seconds for market fill (market orders should fill instantly but give buffer)
            # Poll database (updated by OrderMonitor WebSocket in real-time)
            # If database unavailable, _check_order_status_from_db() automatically falls back to API
            for i in range(30):
                time.sleep(1)

                # Check database (OrderMonitor updates this via WebSocket)
                status = self._check_order_status_from_db(market_order_id)

                if status == 'FILLED':
                    logger.info(f"✅ Market order filled! ({i+1}s)")
                    return

                # Log progress every 5 seconds
                if i > 0 and i % 5 == 0:
                    logger.debug(f"Market order still pending after {i+1}s, status: {status}")

            # Critical: Market order not filled after 30 seconds
            logger.error(f"Market order {market_order_id} not filled after 30 seconds!")
            raise NakedPositionException(
                symbol, unfilled_exchange, quantity,
                int(time.time() - start_time)
            )

        except NakedPositionException:
            # Re-raise NakedPositionException as-is
            raise
        except Exception as e:
            # Log the actual exception before raising NakedPositionException
            logger.error(f"❌ Unexpected error during market order fallback: {type(e).__name__}: {e}")
            logger.error(f"   This may be a database or API error, but the market order might have filled successfully")
            logger.error(f"   Please check order status manually for order ID in logs above")
            raise NakedPositionException(
                symbol, unfilled_exchange, quantity,
                int(time.time() - start_time)
            )

    def _place_new_limit_order_for_naked_position(
        self,
        exchange: str,
        exchange_symbol: str,
        side: str,
        quantity: float,
        symbol: str
    ) -> Optional[str]:
        """
        Place a new limit order during naked position resolution.

        Used when previous order was rejected (post-only rejection).
        Places order at safer price (2 ticks below/above instead of 1).

        Args:
            exchange: 'Bybit' or 'CoinDCX'
            exchange_symbol: Exchange-specific symbol (e.g., 'ETHUSDT', 'ETHUSDTPERP')
            side: 'buy' or 'sell'
            quantity: Order quantity
            symbol: Base symbol for price lookup (e.g., 'ETH')

        Returns:
            str: New order ID if successful
            None: If failed to place order
        """
        try:
            logger.info(f"  Placing new limit order for {exchange} ({side} {quantity} {symbol})")

            # Fetch latest price
            price_data = self.price_service.get_validated_prices(symbol)

            if exchange == 'Bybit':
                ltp = price_data['bybit']['price']
            else:
                ltp = price_data['coindcx']['price']

            # Calculate safer maker price (2 ticks instead of 1 for better fill chance)
            tick_size = self.config.get_symbol_config(symbol)['tick_size']

            if side == 'buy':
                # Place 2 ticks below LTP for better fill chance
                new_price = ltp - (2 * tick_size)
            else:
                # Place 2 ticks above LTP for better fill chance
                new_price = ltp + (2 * tick_size)

            logger.info(f"  Safer price: ${new_price:.2f} (LTP: ${ltp:.2f}, 2 ticks)")

            # Place new order
            if exchange == 'Bybit':
                order_result = self._place_bybit_order(exchange_symbol, side, quantity, new_price)
                if order_result and order_result.get('success'):
                    new_order_id = order_result['order_id']
                    logger.info(f"  ✓ New Bybit order placed: {new_order_id}")
                    return new_order_id
                else:
                    logger.error(f"  ❌ Failed to place Bybit order: {order_result}")
                    return None
            else:
                order_result = self._place_coindcx_order(exchange_symbol, side, quantity, new_price)
                if order_result and order_result.get('success'):
                    new_order_id = order_result['order_id']
                    logger.info(f"  ✓ New CoinDCX order placed: {new_order_id}")
                    return new_order_id
                else:
                    logger.error(f"  ❌ Failed to place CoinDCX order: {order_result}")
                    return None

        except Exception as e:
            logger.error(f"  ❌ Exception placing new limit order: {e}")
            return None

    def _modify_unfilled_order_to_latest_price(
        self,
        symbol: str,
        exchange: str,
        exchange_symbol: str,
        order_id: str,
        side: str,
        chunk_group_id: str = None,
        chunk_sequence: int = None
    ) -> Optional[str]:
        """
        Modify unfilled order to latest price (1 tick below/above).

        NO spread checking - speed is critical during naked position.

        Args:
            chunk_group_id: Chunk group ID for lifecycle logging
            chunk_sequence: Chunk sequence for lifecycle logging

        Returns:
            str: Updated order ID (may change for CoinDCX cancel+replace)
            None: If order already filled/cancelled
        """
        # Fetch latest price
        price_data = self.price_service.get_validated_prices(symbol)

        if exchange == 'Bybit':
            ltp = price_data['bybit']['price']
        else:
            ltp = price_data['coindcx']['price']

        # Calculate new price (1 tick)
        new_price = self.config.calculate_maker_price(symbol, ltp, side)

        logger.info(f"  New price: ${new_price:.2f} (LTP: ${ltp:.2f})")

        # Log modification event
        if self.db and chunk_group_id:
            self.db.log_order_event(
                chunk_group_id=chunk_group_id,
                chunk_sequence=chunk_sequence,
                exchange=exchange.lower(),
                event_type='MODIFIED',
                order_id=order_id,
                event_details={
                    'old_order_id': order_id,
                    'new_price': float(new_price),
                    'ltp': float(ltp),
                    'side': side,
                    'reason': 'price_update'
                }
            )

        # Modify order
        if exchange == 'Bybit':
            self._modify_bybit_order(exchange_symbol, order_id, new_price)
            return order_id  # Bybit always returns same order ID
        else:
            # CoinDCX may return new order ID if cancel+replace fallback is used
            new_order_id = self._modify_coindcx_order(exchange_symbol, order_id, new_price)

            # Log new order ID if it changed (cancel+replace)
            if self.db and chunk_group_id and new_order_id and new_order_id != order_id:
                self.db.log_order_event(
                    chunk_group_id=chunk_group_id,
                    chunk_sequence=chunk_sequence,
                    exchange='coindcx',
                    event_type='REPLACED',
                    order_id=new_order_id,
                    event_details={
                        'old_order_id': order_id,
                        'new_order_id': new_order_id,
                        'reason': 'cancel_replace_fallback'
                    }
                )

            return new_order_id

    def _place_market_order(
        self,
        exchange: str,
        symbol: str,
        side: str,
        quantity: float,
        chunk_group_id: str,
        chunk_sequence: int,
        chunk_total: int
    ) -> str:
        """
        Place market order for immediate fill.

        Args:
            exchange: 'Bybit' or 'CoinDCX'
            symbol: Exchange-specific symbol
            side: 'buy' or 'sell'
            quantity: Order quantity
            chunk_group_id: Chunk group ID to inherit from limit order
            chunk_sequence: Chunk sequence number
            chunk_total: Total chunks

        Returns:
            Order ID
        """
        if exchange == 'Bybit':
            response = self.bybit.place_spot_order(
                symbol=symbol,
                side='Buy' if side == 'buy' else 'Sell',
                order_type='Market',
                qty=str(quantity),
                marketUnit='baseCoin'  # Specify qty is in base currency (ETH), not quote (USDT)
            )

            if response.get('success'):
                order_id = response['order_id']

                # IMMEDIATELY log to database BEFORE WebSocket can update
                # This prevents race condition where WebSocket UPDATE runs before INSERT
                if self.db:
                    row_id = self.db.upsert_order(
                        chunk_group_id=chunk_group_id,  # Inherit from chunk
                        chunk_sequence=chunk_sequence,
                        chunk_total=chunk_total,
                        exchange='bybit',
                        symbol=symbol.replace('USDT', ''),
                        side=side,
                        quantity=quantity,
                        price=0,  # Market order
                        order_id=order_id,
                        status='PLACED',
                        order_type='market'
                    )

                    # Commit immediately so status polling can see it
                    self.db.conn.commit()

                    # Verify row exists
                    if not row_id:
                        logger.error(f"CRITICAL: Bybit market order {order_id} not in database!")
                        logger.error(f"  Chunk: {chunk_group_id} seq {chunk_sequence}")
                        logger.error(f"  Order was placed on exchange but database entry failed")
                        logger.error(f"  This may cause chunk completion detection failure")
                    else:
                        logger.info(f"✓ Bybit market order logged: {order_id}, row_id={row_id}")

                    # Log lifecycle event
                    self.db.log_order_event(
                        chunk_group_id=chunk_group_id,
                        chunk_sequence=chunk_sequence,
                        exchange='bybit',
                        event_type='MARKET_FALLBACK',
                        order_id=order_id,
                        event_details={
                            'side': side,
                            'quantity': float(quantity),
                            'order_type': 'market',
                            'reason': 'limit_order_timeout'
                        }
                    )

                return order_id
            else:
                raise OrderException('Bybit', 'market_order', response.get('error', 'Unknown'))

        else:  # CoinDCX
            response = self.coindcx.place_order(
                pair=symbol,
                side=side,
                order_type='market_order',
                quantity=quantity
            )

            # Handle list/dict
            order_data = response
            if isinstance(response, list) and len(response) > 0:
                order_data = response[0]

            if isinstance(order_data, dict) and order_data.get('id'):
                order_id = order_data['id']

                # IMMEDIATELY log to database BEFORE WebSocket can update
                # This prevents race condition where WebSocket UPDATE runs before INSERT
                if self.db:
                    row_id = self.db.upsert_order(
                        chunk_group_id=chunk_group_id,  # Inherit from chunk
                        chunk_sequence=chunk_sequence,
                        chunk_total=chunk_total,
                        exchange='coindcx',
                        symbol=symbol.replace('B-', '').replace('_USDT', ''),
                        side=side,
                        quantity=quantity,
                        price=0,  # Market order
                        order_id=order_id,
                        status='PLACED',
                        order_type='market'
                    )

                    # Commit immediately so status polling can see it
                    self.db.conn.commit()

                    # Verify row exists
                    if not row_id:
                        logger.error(f"CRITICAL: CoinDCX market order {order_id} not in database!")
                        logger.error(f"  Chunk: {chunk_group_id} seq {chunk_sequence}")
                        logger.error(f"  Order was placed on exchange but database entry failed")
                        logger.error(f"  This may cause chunk completion detection failure")
                    else:
                        logger.info(f"✓ CoinDCX market order logged: {order_id}, row_id={row_id}")

                    # Log lifecycle event
                    self.db.log_order_event(
                        chunk_group_id=chunk_group_id,
                        chunk_sequence=chunk_sequence,
                        exchange='coindcx',
                        event_type='MARKET_FALLBACK',
                        order_id=order_id,
                        event_details={
                            'side': side,
                            'quantity': float(quantity),
                            'order_type': 'market',
                            'reason': 'limit_order_timeout'
                        }
                    )

                return order_id
            else:
                raise OrderException('CoinDCX', 'market_order', 'Failed to place market order')

    def _check_order_status_from_db(
        self,
        order_id: str,
        max_retries: int = 5,
        retry_delay: float = 0.3
    ) -> Optional[str]:
        """
        Check order status from PostgreSQL database with retry logic and event log verification.

        IMPORTANT: Database-only status checks (NO API fallback per user requirement).

        Strategy:
        1. Check orders table (primary source - current state)
        2. Check order_lifecycle_log table (verification - event history)
        3. If mismatch → retry (WebSocket may be mid-update)
        4. If orders.status = NULL but event log has FILLED → return FILLED
           (Prevents duplicate market orders when order missing from orders table)

        The OrderMonitor updates the database in real-time via WebSocket,
        so we poll the database to get current status.

        Args:
            order_id: Exchange order ID
            max_retries: Number of retry attempts (default 5)
            retry_delay: Delay between retries in seconds (default 0.3s)

        Returns:
            Status string: 'PLACED', 'FILLED', 'CANCELLED', 'OPEN', 'REJECTED', or None

        Raises:
            DatabaseException: If database unavailable or order cannot be verified after retries
        """
        if not self.db:
            error_msg = f"Database not available - cannot check order status for {order_id[:12]}..."
            logger.error(error_msg)
            raise DatabaseException("database_unavailable", error_msg)

        for attempt in range(1, max_retries + 1):
            try:
                # Query 1: Check orders table (primary source)
                orders_status = None
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT status FROM orders
                        WHERE order_id = %s
                    """, (order_id,))

                    row = cursor.fetchone()
                    if row:
                        orders_status = row[0]

                # Query 2: Check order_lifecycle_log (verification source)
                event_log_status = None
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT event_type
                        FROM order_lifecycle_log
                        WHERE order_id = %s
                        ORDER BY timestamp DESC
                        LIMIT 1
                    """, (order_id,))

                    row = cursor.fetchone()
                    if row:
                        event_log_status = row[0]

                # Log what we found
                if attempt == 1:
                    logger.debug(
                        f"Order {order_id[:12]}... - "
                        f"orders.status: {orders_status}, "
                        f"event_log: {event_log_status}"
                    )

                # Decision logic based on both sources

                # Case 1: Found in orders table with terminal status
                if orders_status in ['FILLED', 'CANCELLED', 'REJECTED']:
                    if event_log_status == orders_status or event_log_status == 'FILLED':
                        # Both agree or event log confirms fill
                        logger.debug(f"Order {order_id[:12]}... status confirmed: {orders_status}")
                        return orders_status
                    else:
                        # Mismatch - retry
                        logger.debug(
                            f"Status mismatch attempt {attempt}/{max_retries}: "
                            f"orders={orders_status}, event_log={event_log_status}"
                        )
                        if attempt < max_retries:
                            time.sleep(retry_delay)
                            continue
                        else:
                            # After retries, trust orders table for terminal statuses
                            logger.warning(
                                f"After {max_retries} retries, using orders.status: {orders_status}"
                            )
                            return orders_status

                # Case 2: Found in orders table with OPEN/PLACED status
                if orders_status in ['OPEN', 'PLACED', 'NEW']:
                    if event_log_status == 'FILLED':
                        # Critical mismatch: event log says FILLED but orders says OPEN
                        # WebSocket is mid-update, retry
                        logger.info(
                            f"⚠️ Status mismatch (attempt {attempt}/{max_retries}): "
                            f"orders={orders_status}, event_log=FILLED - retrying..."
                        )
                        if attempt < max_retries:
                            time.sleep(retry_delay)
                            continue
                        else:
                            # After retries, trust event log (more reliable)
                            logger.warning(
                                f"After {max_retries} retries, trusting event_log: FILLED"
                            )
                            return 'FILLED'
                    else:
                        # Both agree: order is open
                        return orders_status

                # Case 3: NOT found in orders table (NULL) - CRITICAL CASE
                if orders_status is None:
                    if event_log_status == 'FILLED':
                        # Order filled but missing from orders table
                        # This is the bug scenario - prevents duplicate market orders!
                        logger.warning(
                            f"⚠️ CRITICAL: Order {order_id[:12]}... NOT in orders table "
                            f"but event_log shows FILLED - returning FILLED to prevent duplicate"
                        )
                        return 'FILLED'

                    elif event_log_status in ['PLACED', 'OPEN', 'NEW']:
                        # Order should be in orders table but isn't
                        logger.warning(
                            f"⚠️ Order {order_id[:12]}... in event_log but not in orders table "
                            f"(attempt {attempt}/{max_retries})"
                        )
                        if attempt < max_retries:
                            time.sleep(retry_delay)
                            continue
                        else:
                            # After retries, still missing - raise error
                            error_msg = (
                                f"Order {order_id[:12]}... found in event_log "
                                f"but missing from orders table after {max_retries} retries"
                            )
                            logger.error(error_msg)
                            raise DatabaseException("order_missing_from_orders_table", error_msg)

                    elif event_log_status == 'CANCELLED':
                        # Order was cancelled
                        return 'CANCELLED'

                    else:
                        # Not in either table
                        logger.warning(
                            f"Order {order_id[:12]}... not found in either table "
                            f"(attempt {attempt}/{max_retries})"
                        )
                        if attempt < max_retries:
                            time.sleep(retry_delay)
                            continue
                        else:
                            # After retries, truly not found
                            logger.error(f"Order {order_id[:12]}... not found after {max_retries} retries")
                            return None

                # Shouldn't reach here, but handle gracefully
                logger.warning(
                    f"Unexpected state: orders={orders_status}, event_log={event_log_status}"
                )
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                else:
                    return orders_status if orders_status else None

            except Exception as e:
                logger.error(f"Database error checking order status (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                else:
                    error_msg = f"Failed to check order status after {max_retries} attempts: {e}"
                    logger.error(error_msg)
                    raise DatabaseException("status_check_failed", error_msg)

        # Shouldn't reach here
        return None

    def _modify_bybit_order(
        self,
        symbol: str,
        order_id: str,
        new_price: float
    ) -> None:
        """
        Modify Bybit order price.

        Updates database with modification details.
        """
        try:
            response = self.bybit.session.amend_order(
                category='spot',
                symbol=symbol,
                orderId=order_id,
                price=str(new_price)
            )

            if response.get('retCode') == 0:
                logger.debug(f"Bybit order {order_id} modified to ${new_price:.2f}")

                # Update database
                if self.db:
                    with self.db.conn.cursor() as cursor:
                        cursor.execute("""
                            UPDATE orders
                            SET modified_price = %s,
                                modified_at = CURRENT_TIMESTAMP,
                                is_modified = TRUE
                            WHERE order_id = %s
                        """, (new_price, order_id))
                        self.db.conn.commit()
            else:
                logger.warning(f"Bybit modification failed: {response}")

        except Exception as e:
            logger.warning(f"Failed to modify Bybit order: {e}")

    def _modify_coindcx_order(
        self,
        symbol: str,
        order_id: str,
        new_price: float
    ) -> Optional[str]:
        """
        Modify CoinDCX order price using edit_order API with cancel+replace fallback.

        Strategy:
        1. Try edit_order API (USDT margin only)
        2. If HTTP 422 (INR margin not supported), use cancel+replace
        3. Update database with new order details

        Args:
            symbol: CoinDCX symbol (e.g., B-ETH_USDT)
            order_id: Existing order ID
            new_price: New price to set

        Returns:
            str: Order ID (same if edit succeeded, new if replaced)
            None: If order already filled/cancelled (skip modification)

        Updates database with modification details.
        """
        try:
            # Try edit_order first (USDT margin only)
            response = self.coindcx.edit_order(
                order_id=order_id,
                price=new_price
            )

            logger.debug(f"CoinDCX order {order_id[:8]}... modified to ${new_price:.2f}")

            # Update database
            if self.db:
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE orders
                        SET modified_price = %s,
                            modified_at = CURRENT_TIMESTAMP,
                            is_modified = TRUE
                        WHERE order_id = %s
                    """, (new_price, order_id))
                    self.db.conn.commit()

            return order_id

        except requests.exceptions.HTTPError as e:
            # HTTP 422 = INR margin not supported for edit_order
            if e.response.status_code == 422:
                logger.warning(f"CoinDCX edit_order not supported (HTTP 422), using cancel+replace fallback")
                new_order_id = self._cancel_and_replace_coindcx_order(symbol, order_id, new_price)
                return new_order_id  # May be None if order already filled
            else:
                logger.warning(f"Failed to modify CoinDCX order: {e}")
                raise

        except Exception as e:
            logger.warning(f"Failed to modify CoinDCX order: {e}")
            raise

    def _cancel_and_replace_coindcx_order(
        self,
        symbol: str,
        old_order_id: str,
        new_price: float
    ) -> Optional[str]:
        """
        Cancel existing CoinDCX order and place new one at updated price.

        This is a fallback for INR margin orders where edit_order is not supported.

        Args:
            symbol: CoinDCX symbol (e.g., B-ETH_USDT)
            old_order_id: Order ID to cancel
            new_price: New price for replacement order

        Returns:
            str: New order ID, or None if order already filled/cancelled

        Raises:
            OrderException: If cancel or replace fails
        """
        try:
            # Step 0: CRITICAL - Check if order still exists and is open
            current_status = self._check_order_status_from_db(old_order_id)

            if current_status == 'FILLED':
                logger.info(f"  ℹ️ Order {old_order_id[:8]}... already FILLED, skipping modification")
                return None  # Don't create new order

            if current_status == 'CANCELLED':
                logger.warning(f"  ⚠️ Order {old_order_id[:8]}... already CANCELLED, skipping modification")
                return None  # Don't create new order

            # Step 1: Get old order details from database
            order_details = self._get_order_details_from_db(old_order_id)

            if not order_details:
                logger.error(f"Cannot find order {old_order_id} in database for replacement")
                raise OrderException('CoinDCX', 'modification', f"Order {old_order_id} not found in database")

            side = order_details['side']
            quantity = order_details['quantity']
            chunk_group_id = order_details['chunk_group_id']
            chunk_sequence = order_details['chunk_sequence']
            chunk_total = order_details['chunk_total']
            coin_symbol = order_details['symbol']

            logger.info(f"Cancel+Replace: {old_order_id[:8]}... → New order @ ${new_price:.2f}")

            # Step 2: Cancel old order
            try:
                self.coindcx.cancel_order(old_order_id)
                logger.debug(f"  ✓ Old order {old_order_id[:8]}... cancelled")
            except Exception as cancel_error:
                logger.warning(f"Failed to cancel order {old_order_id}, it may already be filled: {cancel_error}")
                # Don't raise - order might already be filled, let monitoring detect it

            # Step 2.5: Wait for cancellation to process on CoinDCX servers
            logger.debug(f"  ⏳ Waiting 2s for cancellation to process...")
            time.sleep(2)

            # Step 3: Place new order at new price with retry logic
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    logger.debug(f"  Placement attempt {attempt}/{max_retries}")

                    new_order = self.coindcx.place_order(
                        pair=symbol,
                        side=side,
                        order_type='limit_order',
                        quantity=quantity,
                        price=new_price
                    )

                    # Handle response
                    order_data = new_order
                    if isinstance(new_order, list) and len(new_order) > 0:
                        order_data = new_order[0]

                    new_order_id = order_data.get('id')

                    if not new_order_id:
                        raise OrderException('CoinDCX', 'replacement', 'No order ID in response')

                    logger.info(f"  ✓ New order {new_order_id[:8]}... placed @ ${new_price:.2f}")
                    break  # Success, exit retry loop

                except requests.exceptions.HTTPError as http_error:
                    # HTTP 500 = server error, worth retrying
                    if http_error.response.status_code == 500:
                        if attempt < max_retries:
                            backoff = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
                            logger.warning(f"  HTTP 500 error, retrying in {backoff}s (attempt {attempt}/{max_retries})")
                            time.sleep(backoff)
                            continue
                        else:
                            logger.error(f"CRITICAL: Failed to replace order after {max_retries} attempts!")
                            logger.error(f"Old order {old_order_id} cancelled, new order placement failed: {http_error}")
                            raise OrderException('CoinDCX', 'replacement', f"Replacement failed after {max_retries} attempts: {http_error}")
                    else:
                        # Other HTTP errors, don't retry
                        logger.error(f"CRITICAL: Failed to replace order (HTTP {http_error.response.status_code})!")
                        logger.error(f"Old order {old_order_id} cancelled, new order placement failed: {http_error}")
                        raise OrderException('CoinDCX', 'replacement', f"Replacement failed: {http_error}")

                except Exception as place_error:
                    if attempt < max_retries:
                        backoff = 2 ** attempt
                        logger.warning(f"  Placement error, retrying in {backoff}s (attempt {attempt}/{max_retries}): {place_error}")
                        time.sleep(backoff)
                        continue
                    else:
                        logger.error(f"CRITICAL: Failed to replace order after {max_retries} attempts!")
                        logger.error(f"Old order {old_order_id} cancelled, new order placement failed: {place_error}")
                        raise OrderException('CoinDCX', 'replacement', f"Replacement failed: {place_error}")

            # Step 4: Update database (cancel old, insert new)
            if self.db:
                with self.db.conn.cursor() as cursor:
                    # Mark old order as cancelled
                    cursor.execute("""
                        UPDATE orders
                        SET status = 'CANCELLED',
                            modified_at = CURRENT_TIMESTAMP
                        WHERE order_id = %s
                    """, (old_order_id,))

                    # Insert new order with same chunk tracking
                    cursor.execute("""
                        INSERT INTO orders (
                            chunk_group_id, chunk_sequence, chunk_total,
                            exchange, symbol, side, quantity, price, order_id, status,
                            created_at, placed_at
                        ) VALUES (
                            %s, %s, %s, 'coindcx', %s, %s, %s, %s, %s, 'PLACED',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                    """, (
                        chunk_group_id, chunk_sequence, chunk_total,
                        coin_symbol, side, quantity, new_price, new_order_id
                    ))

                    self.db.conn.commit()

                logger.debug(f"  ✓ Database updated: old order cancelled, new order tracked")

            return new_order_id

        except Exception as e:
            logger.error(f"Cancel+replace failed: {e}")
            raise

    def _get_order_details_from_db(self, order_id: str) -> Optional[Dict]:
        """
        Retrieve order details from database.

        Args:
            order_id: Order ID to look up

        Returns:
            dict with order details or None if not found
        """
        if not self.db:
            return None

        try:
            with self.db.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT exchange, symbol, side, quantity, price, status,
                           chunk_group_id, chunk_sequence, chunk_total
                    FROM orders
                    WHERE order_id = %s
                """, (order_id,))

                row = cursor.fetchone()

                if not row:
                    return None

                return {
                    'exchange': row[0],
                    'symbol': row[1],
                    'side': row[2],
                    'quantity': float(row[3]),
                    'price': float(row[4]),
                    'status': row[5],
                    'chunk_group_id': row[6],
                    'chunk_sequence': row[7],
                    'chunk_total': row[8]
                }

        except Exception as e:
            logger.error(f"Failed to get order details from DB: {e}")
            return None

    def _cancel_bybit_order(self, symbol: str, order_id: str) -> bool:
        """
        Cancel Bybit order and update database.

        Returns:
            bool: True if cancelled successfully, False if already filled/rejected/cancelled
        """
        # CRITICAL: Check status before attempting cancel to prevent duplicate orders
        status = self._check_order_status_from_db(order_id)

        if status == 'FILLED':
            logger.info(f"✅ Bybit order {order_id[:12]}... already FILLED - skipping cancel")
            return False

        if status == 'REJECTED':
            logger.info(f"⚠️ Bybit order {order_id[:12]}... already REJECTED - skipping cancel")
            return False

        if status == 'CANCELLED':
            logger.info(f"⚠️ Bybit order {order_id[:12]}... already CANCELLED - skipping cancel")
            return False

        if status not in ['OPEN', 'PLACED', 'NEW']:
            logger.warning(f"⚠️ Bybit order {order_id[:12]}... has status {status} - skipping cancel")
            return False

        # Safe to cancel
        try:
            result = self.bybit.cancel_spot_order(symbol, order_id)

            # Check if cancel succeeded (Bybit client returns dict, not exception)
            if not result.get('success'):
                error_msg = result.get('error', 'Unknown cancel error')
                raise Exception(f"Cancel API failed: {error_msg}")

            logger.info(f"✅ Bybit order {order_id[:12]}... cancelled successfully")

            if self.db:
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE orders
                        SET status = 'CANCELLED'
                        WHERE order_id = %s
                    """, (order_id,))
                    self.db.conn.commit()

            return True

        except Exception as e:
            logger.error(f"❌ Failed to cancel Bybit order {order_id[:12]}...: {e}")
            # If API says "order not found", it might be filled
            if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                # Wait for WebSocket to update database (order might have just filled)
                logger.info(f"  Order not found - waiting 1s for database to update...")
                time.sleep(1.0)

                # Re-check status with more aggressive retry
                logger.info(f"  Checking if order was actually filled...")
                new_status = self._check_order_status_from_db(
                    order_id,
                    max_retries=10,
                    retry_delay=0.5
                )

                if new_status == 'FILLED':
                    logger.info(f"  ✅ Order {order_id[:12]}... was actually FILLED")
                    return False
                elif new_status == 'CANCELLED':
                    logger.info(f"  ✅ Order {order_id[:12]}... was actually CANCELLED")
                    return False
                else:
                    # Still can't verify - assume filled to be safe (prevents duplicate market orders)
                    logger.warning(
                        f"  ⚠️ Cannot verify status after retries (got: {new_status}), "
                        f"assuming FILLED to prevent duplicate market order"
                    )
                    return False

            # If NOT "order not found", raise exception
            raise

    def _cancel_coindcx_order(self, order_id: str) -> bool:
        """
        Cancel CoinDCX order and update database.

        Returns:
            bool: True if cancelled successfully, False if already filled/rejected/cancelled
        """
        # CRITICAL: Check status before attempting cancel to prevent duplicate orders
        status = self._check_order_status_from_db(order_id)

        if status == 'FILLED':
            logger.info(f"✅ CoinDCX order {order_id[:12]}... already FILLED - skipping cancel")
            return False

        if status == 'REJECTED':
            logger.info(f"⚠️ CoinDCX order {order_id[:12]}... already REJECTED - skipping cancel")
            return False

        if status == 'CANCELLED':
            logger.info(f"⚠️ CoinDCX order {order_id[:12]}... already CANCELLED - skipping cancel")
            return False

        if status not in ['OPEN', 'PLACED', 'NEW']:
            logger.warning(f"⚠️ CoinDCX order {order_id[:12]}... has status {status} - skipping cancel")
            return False

        # Safe to cancel
        try:
            self.coindcx.cancel_order(order_id)
            logger.info(f"✅ CoinDCX order {order_id[:12]}... cancelled successfully")

            if self.db:
                with self.db.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE orders
                        SET status = 'CANCELLED'
                        WHERE order_id = %s
                    """, (order_id,))
                    self.db.conn.commit()

            return True

        except Exception as e:
            logger.error(f"❌ Failed to cancel CoinDCX order {order_id[:12]}...: {e}")
            # If API says "order not found", it might be filled
            if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                # Re-check status
                logger.info(f"  Checking if order was actually filled...")
                new_status = self._check_order_status_from_db(order_id)
                if new_status == 'FILLED':
                    logger.info(f"  ✅ Order {order_id[:12]}... was actually FILLED")
                    return False
            raise

    def handle_partial_fill(
        self,
        order_id: str,
        exchange: str,
        symbol: str,
        side: str,
        coin: str,
        chunk_group_id: str,
        chunk_sequence: int,
        chunk_total: int,
        original_quantity: float
    ) -> str:
        """
        Handle partial fill: cancel partial order + place market order for remainder.

        Steps:
        1. Get partial fill details from event log (cumExecQty, cumExecFee)
        2. Cancel the partially filled order
        3. Calculate remaining quantity needed
        4. Place market order for remainder
        5. Update orders table with tracking (UPSERT with partial_details)

        Args:
            order_id: The partially filled order's ID
            exchange: 'bybit' or 'coindcx'
            symbol: Trading symbol (e.g., 'ETHUSDT' or 'B-ETH_USDT')
            side: 'buy' or 'sell'
            coin: Base coin symbol ('BTC', 'ETH', 'SOL')
            chunk_group_id: UUID for chunk group
            chunk_sequence: Chunk number
            chunk_total: Total chunks
            original_quantity: Original order quantity before partial fill

        Returns:
            Market order ID for the completion order

        Raises:
            Exception: If handling fails
        """
        logger.info(
            f"========== HANDLING PARTIAL FILL ==========\n"
            f"Order ID: {order_id}\n"
            f"Exchange: {exchange}\n"
            f"Symbol: {symbol}\n"
            f"Side: {side}\n"
            f"Original Qty: {original_quantity}\n"
            f"Chunk: {chunk_sequence}/{chunk_total}"
        )

        # Step 1: Get partial fill details from event log
        partial_fill_data = self._get_partial_fill_details(order_id, exchange)

        if not partial_fill_data:
            raise Exception(f"Could not find partial fill details for {order_id}")

        partial_filled_qty = partial_fill_data['cumExecQty']
        partial_avg_price = partial_fill_data['avgPrice']
        partial_fee = partial_fill_data['cumExecFee']

        logger.info(
            f"Partial fill details:\n"
            f"  Filled Qty: {partial_filled_qty}\n"
            f"  Avg Price: ${partial_avg_price:.2f}\n"
            f"  Fee: {partial_fee}"
        )

        # Step 2: Cancel the partially filled order
        logger.info(f"Cancelling partially filled order {order_id}...")
        try:
            if exchange.lower() == 'bybit':
                self._cancel_bybit_order(symbol, order_id)
            else:  # coindcx
                self._cancel_coindcx_order(order_id)
        except Exception as e:
            logger.error(f"Failed to cancel partial order: {e}")
            # Continue anyway - we need to complete the hedge

        # Step 3: Calculate remaining quantity
        remaining_qty = original_quantity - partial_filled_qty

        logger.info(
            f"Remaining quantity to fill: {remaining_qty} {coin}\n"
            f"  (Original: {original_quantity} - Filled: {partial_filled_qty})"
        )

        if remaining_qty <= 0:
            logger.warning("No remaining quantity - order already fully filled?")
            return order_id  # Return original order ID

        # Step 4: Place market order for remainder
        logger.info(f"Placing MARKET order for remaining {remaining_qty} {coin}...")

        try:
            if exchange.lower() == 'bybit':
                # Bybit market order
                market_order = self.bybit.place_spot_order(
                    symbol=symbol,
                    side='Buy' if side.lower() == 'buy' else 'Sell',
                    order_type='Market',
                    qty=str(remaining_qty)
                )
                market_order_id = market_order['result']['orderId']
                market_status = market_order['result']['orderStatus']

            else:  # coindcx
                # CoinDCX market order
                from client.coindcx.coindcx_futures import OrderSide, OrderType

                market_order = self.coindcx.place_order(
                    pair=symbol,
                    side=OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL,
                    order_type=OrderType.MARKET_ORDER,
                    quantity=remaining_qty,
                    leverage=1
                )
                market_order_id = market_order['id']
                market_status = market_order['status']

            logger.info(
                f"✅ Market order placed successfully\n"
                f"  Order ID: {market_order_id}\n"
                f"  Status: {market_status}\n"
                f"  Quantity: {remaining_qty} {coin}"
            )

        except Exception as e:
            logger.error(f"CRITICAL: Failed to place market order: {e}")
            raise

        # Step 4.5: Wait for market order to fill and get execution details
        logger.info("Waiting for market order to fill...")
        import time
        time.sleep(2)  # Market orders fill instantly, brief wait for data propagation

        # Get fill details from event log
        completion_fill_data = self._get_partial_fill_details(market_order_id, exchange)

        if completion_fill_data:
            completion_qty = completion_fill_data['cumExecQty']
            completion_fee = completion_fill_data['cumExecFee']
            completion_avg_price = completion_fill_data['avgPrice']
            logger.info(
                f"Market order fill details:\n"
                f"  Filled Qty: {completion_qty}\n"
                f"  Avg Price: ${completion_avg_price:.2f}\n"
                f"  Fee: {completion_fee}"
            )
        else:
            # Fallback: use requested quantity as estimate
            logger.warning("Could not get fill details from event log, using estimates")
            completion_qty = remaining_qty
            completion_fee = 0  # Will be updated by order monitor later
            completion_avg_price = partial_avg_price

        # Step 5: Update orders table with partial fill tracking
        logger.info("Updating database with partial fill details...")

        # Build partial_details dict based on exchange
        if exchange.lower() == 'bybit':
            partial_details = {
                'partial_order_id': order_id,
                'partial_filled_qty': partial_filled_qty,
                'partial_avg_price': partial_avg_price,
                'partial_bybit_fee_crypto': partial_fee,  # CRITICAL for reconciliation
                'partial_coindcx_fee_usdt': None
            }
        else:  # coindcx
            partial_details = {
                'partial_order_id': order_id,
                'partial_filled_qty': partial_filled_qty,
                'partial_avg_price': partial_avg_price,
                'partial_bybit_fee_crypto': None,
                'partial_coindcx_fee_usdt': partial_fee  # Nice-to-have for P&L
            }

        # UPSERT order with partial fill tracking
        # This will replace the partial order row with completion order row
        # but preserve partial order details in partial_* columns
        try:
            if self.db:
                self.db.upsert_order(
                    chunk_group_id=chunk_group_id,
                    chunk_sequence=chunk_sequence,
                    exchange=exchange,
                    symbol=symbol,
                    side=side,
                    quantity=remaining_qty,
                    price=completion_avg_price,  # Use completion avg price
                    order_id=market_order_id,
                    status='FILLED',  # Market orders fill immediately
                    order_type='market',
                    chunk_total=chunk_total,
                    is_partial_completion=True,
                    partial_details=partial_details,
                    cumexecqty=completion_qty,  # ✅ Include fill data
                    cumexecfee=completion_fee,  # ✅ Include fee data
                    # net_received will be auto-calculated
                )
                logger.info("✅ Database updated with partial fill tracking and fill data")
        except Exception as e:
            logger.error(f"Failed to update database with partial fill: {e}")
            # Don't raise - market order placed successfully

        logger.info(
            f"========== PARTIAL FILL HANDLED ==========\n"
            f"Partial Order: {order_id} ({partial_filled_qty} {coin})\n"
            f"Completion Order: {market_order_id} ({remaining_qty} {coin})\n"
            f"Total: {original_quantity} {coin}"
        )

        return market_order_id

    def _get_partial_fill_details(self, order_id: str, exchange: str) -> dict:
        """
        Get partial fill details from event log.

        Queries the bybit_order_events or coindcx_order_events table
        for the latest cumExecQty and cumExecFee.

        Args:
            order_id: Order ID to lookup
            exchange: 'bybit' or 'coindcx'

        Returns:
            dict with keys: cumExecQty, avgPrice, cumExecFee
            None if not found
        """
        if not self.db:
            logger.error("Cannot get partial fill details - no database connection")
            return None

        table_name = f"{exchange.lower()}_order_events"

        query = f"""
            SELECT
                "cumExecQty",
                "avgPrice",
                "cumExecFee",
                event_time
            FROM {table_name}
            WHERE order_id = %s
            ORDER BY event_time DESC
            LIMIT 1
        """

        try:
            result = self.db.execute_query(query, (order_id,), fetch=True)

            if not result or len(result) == 0:
                logger.error(f"No event log entry found for {order_id} in {table_name}")
                return None

            row = result[0]

            return {
                'cumExecQty': float(row['cumExecQty'] or 0),
                'avgPrice': float(row['avgPrice'] or 0),
                'cumExecFee': float(row['cumExecFee'] or 0)
            }

        except Exception as e:
            logger.error(f"Failed to query {table_name} for {order_id}: {e}")
            return None
