"""
Enhanced Bot - Main orchestrator for delta-neutral hedge trading
Coordinates all components and provides interactive user interface.
"""

import logging
import threading
import time
import uuid
from typing import Optional
from datetime import datetime

from config.symbol_config import SymbolConfig
from utils.exceptions import (
    SpreadException, ValidationException, HedgeTradingException
)
from utils.validators import Validators
from utils.db import Database
from core.price_service import PriceService
from core.chunk_manager import ChunkManager
from core.order_manager import OrderManager

# Import OrderMonitor from same package (self-contained)
try:
    from order_monitor import OrderMonitor
    ORDER_MONITOR_AVAILABLE = True
except ImportError as e:
    print(f"⚠️  OrderMonitor not available: {e}")
    ORDER_MONITOR_AVAILABLE = False


logger = logging.getLogger(__name__)


class EnhancedBot:
    """Main bot orchestrator for hedge trading"""

    def __init__(
        self,
        bybit_api_key: str,
        bybit_api_secret: str,
        coindcx_api_key: str,
        coindcx_api_secret: str,
        testnet: bool = True,
        db: Database = None
    ):
        """
        Initialize Enhanced Bot.

        Args:
            bybit_api_key: Bybit API key
            bybit_api_secret: Bybit API secret
            coindcx_api_key: CoinDCX API key
            coindcx_api_secret: CoinDCX API secret
            testnet: Use testnet for Bybit
            db: Database instance
        """
        self.config = SymbolConfig()
        self.validators = Validators()
        self.db = db

        # Initialize services
        self.price_service = PriceService()
        self.chunk_manager = ChunkManager()

        # Reset orders table on startup (clean slate for each session)
        if db:
            self._reset_orders_table()

        # Initialize OrderMonitor FIRST (needed by OrderManager for WebSocket-based rejection detection)
        self.order_monitor = None
        self.order_monitor_thread = None

        if ORDER_MONITOR_AVAILABLE and db:
            try:
                print("\n🔄 Starting OrderMonitor in background thread...")

                # Share chunk_manager instance for callbacks (critical for integration!)
                self.order_monitor = OrderMonitor(chunk_manager=self.chunk_manager)

                # Start monitor in daemon thread (won't block shutdown)
                self.order_monitor_thread = threading.Thread(
                    target=self._run_order_monitor,
                    daemon=True,
                    name="OrderMonitor"
                )
                self.order_monitor_thread.start()

                print("✅ OrderMonitor started - Real-time WebSocket monitoring active")
                print("   Database will be updated automatically via WebSocket\n")
            except Exception as e:
                print(f"⚠️  Failed to start OrderMonitor: {e}")
                print(f"   Continuing without real-time monitoring\n")
                self.order_monitor = None
        elif not ORDER_MONITOR_AVAILABLE:
            print("⚠️  OrderMonitor not available - Real-time order tracking disabled\n")
        elif not db:
            print("⚠️  Database not connected - OrderMonitor disabled\n")

        # Initialize OrderManager AFTER OrderMonitor (needs reference for WebSocket-based detection)
        self.order_manager = OrderManager(
            bybit_api_key=bybit_api_key,
            bybit_api_secret=bybit_api_secret,
            coindcx_api_key=coindcx_api_key,
            coindcx_api_secret=coindcx_api_secret,
            testnet=testnet,
            db=db,
            order_monitor=self.order_monitor  # Pass OrderMonitor reference for instant rejection detection
        )

        logger.info("Enhanced Bot initialized")

    def _reset_orders_table(self):
        """
        Reset orders table on bot startup (clean slate for each session).

        This clears the orders table so each bot session starts fresh.
        Event tables (bybit_order_events, coindcx_order_events) are PRESERVED
        for historical audit trail.
        """
        try:
            print("\n🔄 Resetting orders table for fresh session...")

            with self.db.conn.cursor() as cursor:
                # Count existing orders before reset
                cursor.execute("SELECT COUNT(*) FROM orders")
                old_count = cursor.fetchone()[0]

                # Clear orders table (NOT event tables - those are preserved!)
                cursor.execute("TRUNCATE TABLE orders CASCADE")
                self.db.conn.commit()

                print(f"✅ Orders table reset ({old_count} old orders cleared)")
                print(f"   Event tables preserved for audit trail")
                print(f"   Starting with clean slate for this session\n")

        except Exception as e:
            logger.warning(f"Failed to reset orders table: {e}")
            print(f"⚠️  Warning: Could not reset orders table: {e}")
            print(f"   Continuing with existing orders...\n")
            try:
                self.db.conn.rollback()
            except:
                pass

    def _run_order_monitor(self):
        """Run OrderMonitor in background thread"""
        try:
            self.order_monitor.monitor_loop()
        except Exception as e:
            logger.error(f"OrderMonitor crashed: {e}")
            print(f"\n⚠️  OrderMonitor stopped: {e}")

    def shutdown(self):
        """Clean shutdown of bot and background services"""
        print("\n🛑 Shutting down bot...")

        if self.order_monitor:
            try:
                self.order_monitor.running = False
                self.order_monitor.close()
                print("✅ OrderMonitor stopped")
            except Exception as e:
                print(f"⚠️  Error stopping OrderMonitor: {e}")

        if self.db:
            try:
                self.db.close()
                print("✅ Database connection closed")
            except Exception as e:
                print(f"⚠️  Error closing database: {e}")

        print("✅ Bot shutdown complete")

    def select_coin(self) -> str:
        """
        Interactive coin selection.

        Returns:
            Selected coin symbol (BTC/ETH)
        """
        supported_symbols = self.config.get_supported_symbols()

        print("\n" + "=" * 60)
        print("DELTA-NEUTRAL HEDGE TRADING BOT")
        print("=" * 60)
        print("\nSupported Cryptocurrencies:")
        for i, symbol in enumerate(supported_symbols, 1):
            print(f"  {i}. {symbol}")

        while True:
            try:
                choice = input(f"\nSelect coin (1-{len(supported_symbols)}): ").strip()
                choice_num = int(choice)

                if 1 <= choice_num <= len(supported_symbols):
                    selected_symbol = supported_symbols[choice_num - 1]
                    print(f"\n✓ Selected: {selected_symbol}")
                    return selected_symbol
                else:
                    print(f"❌ Invalid choice. Please enter 1-{len(supported_symbols)}")

            except ValueError:
                print("❌ Invalid input. Please enter a number.")
            except KeyboardInterrupt:
                print("\n\nOperation cancelled by user.")
                raise

    def _handle_quantity_remainder(
        self,
        symbol: str,
        quantity: float,
        min_quantity: float,
        precision: int
    ) -> float | None:
        """
        Handle quantity remainder - ask user to choose adjustment if needed.

        Args:
            symbol: Cryptocurrency symbol
            quantity: User's entered quantity
            min_quantity: Minimum chunk size
            precision: Decimal precision

        Returns:
            Adjusted quantity, or None if user wants to re-enter or cancel
        """
        # Calculate chunks and check for remainder
        chunks, remainder_info = self.chunk_manager.calculate_chunks(symbol, quantity)

        # No remainder - quantity is perfect
        if not remainder_info['has_remainder']:
            return quantity

        # Remainder exists - present options to user
        remainder = remainder_info['remainder']
        lower_amount = remainder_info['lower_amount']
        upper_amount = remainder_info['upper_amount']
        num_chunks = remainder_info['num_full_chunks']

        print(f"\n{'='*60}")
        print(f"⚠️  QUANTITY ADJUSTMENT NEEDED")
        print(f"{'='*60}")
        print(f"You entered: {quantity:.{precision}f} {symbol}")
        print(f"Minimum chunk size: {min_quantity:.{precision}f} {symbol}")
        print(f"")
        print(f"Tradeable amount: {lower_amount:.{precision}f} {symbol} ({num_chunks} chunks)")
        print(f"Remainder: {remainder:.{precision}f} {symbol} will NOT be traded")
        print(f"")
        print(f"Options:")
        print(f"  1. Trade {lower_amount:.{precision}f} {symbol} [{num_chunks} chunks]")
        print(f"  2. Trade {upper_amount:.{precision}f} {symbol} [{num_chunks + 1} chunks]")
        print(f"  3. Enter different amount")
        print(f"  4. Cancel")
        print(f"{'='*60}")

        while True:
            try:
                choice = input("\nYour choice (1-4): ").strip()

                if choice == '1':
                    print(f"✓ Adjusted to {lower_amount:.{precision}f} {symbol} (drop remainder)")
                    return lower_amount
                elif choice == '2':
                    print(f"✓ Adjusted to {upper_amount:.{precision}f} {symbol} (add chunk)")
                    return upper_amount
                elif choice == '3':
                    print("↩️  Re-enter quantity")
                    return None  # Signal to re-enter
                elif choice == '4':
                    print("❌ Cancelled")
                    raise KeyboardInterrupt()
                else:
                    print("❌ Invalid choice. Please enter 1-4")

            except ValueError:
                print("❌ Invalid input. Please enter 1-4")
            except KeyboardInterrupt:
                raise

    def get_trade_quantity(self, symbol: str) -> float:
        """
        Get trade quantity in crypto units from user.

        Args:
            symbol: Selected cryptocurrency symbol

        Returns:
            Quantity in crypto units (e.g., BTC, ETH)
        """
        symbol_config = self.config.get_symbol_config(symbol)
        min_quantity = symbol_config['min_quantity']
        precision = symbol_config['precision']

        while True:
            try:
                qty_str = input(
                    f"\nEnter {symbol} quantity to trade (minimum {min_quantity}): "
                ).strip()
                quantity = float(qty_str)

                # Round to precision
                quantity = round(quantity, precision)

                # Validate minimum
                if quantity < min_quantity:
                    print(f"❌ Quantity below minimum {min_quantity} {symbol}")
                    continue

                # Check for remainder
                adjusted_quantity = self._handle_quantity_remainder(
                    symbol, quantity, min_quantity, precision
                )

                if adjusted_quantity is None:
                    # User chose to cancel or enter different amount
                    continue

                print(f"✓ Final quantity: {adjusted_quantity:.{precision}f} {symbol}")
                return adjusted_quantity

            except ValueError:
                print("❌ Invalid quantity. Please enter a number.")
            except KeyboardInterrupt:
                print("\n\nOperation cancelled by user.")
                raise


    def validate_balances(self, symbol: str, total_quantity: float, bybit_price: float) -> bool:
        """
        Validate account balances are sufficient.

        Args:
            symbol: Cryptocurrency symbol
            total_quantity: Total quantity in crypto units
            bybit_price: Current Bybit price

        Returns:
            True if balances are sufficient
        """
        print("\n" + "-" * 60)
        print("Checking account balances...")
        print("-" * 60)

        try:
            # Calculate USD value needed
            total_usd = total_quantity * bybit_price

            # Check Bybit balance
            bybit_balance = self.order_manager.bybit.get_spot_balance(coin='USDT')
            if bybit_balance.get('success'):
                total_equity = float(bybit_balance.get('total_equity', 0))
                print(f"Bybit USDT Balance: ${total_equity:.2f}")
                print(f"Required for trade: ${total_usd:.2f}")

                if total_equity < total_usd:
                    print(f"❌ Insufficient Bybit balance")
                    return False
            else:
                print("⚠️ Unable to verify Bybit balance")

            # Check CoinDCX balance
            # Note: This would need proper implementation based on CoinDCX API
            print("✓ CoinDCX balance check skipped (implement based on API)")

            return True

        except Exception as e:
            logger.error(f"Error checking balances: {e}")
            print(f"⚠️ Balance check failed: {e}")
            return False

    def validate_spread_with_user(self, symbol: str) -> bool:
        """
        Check spread and get user confirmation if needed.

        Args:
            symbol: Cryptocurrency symbol

        Returns:
            True if spread is acceptable or user confirms override
        """
        print("\n" + "-" * 60)
        print("Checking current spread...")
        print("-" * 60)

        try:
            is_valid, spread, message = self.price_service.check_spread(symbol)

            print(f"Spread: {spread:.4f}%")
            print(message)

            if is_valid:
                return True

            # Spread exceeded - ask user
            print("\n⚠️ WARNING: Spread exceeds maximum safe threshold!")
            response = input("Continue anyway? This is risky! (yes/no): ").strip().lower()

            if response == 'yes':
                print("⚠️ Proceeding with wide spread (user override)")
                return True
            else:
                print("❌ Trade cancelled due to wide spread")
                return False

        except Exception as e:
            logger.error(f"Error checking spread: {e}")
            print(f"❌ Spread check failed: {e}")
            return False

    def execute_trade(
        self,
        symbol: str,
        total_quantity: float
    ) -> bool:
        """
        Execute the complete hedge trade.

        Args:
            symbol: Cryptocurrency symbol
            total_quantity: Total quantity in crypto units

        Returns:
            True if trade executed successfully
        """
        print("\n" + "=" * 60)
        print("EXECUTING HEDGE TRADE")
        print("=" * 60)

        try:
            # Get current prices
            print("\nFetching current prices...")
            price_data = self.price_service.get_validated_prices(symbol)

            bybit_price = price_data['bybit']['price']
            coindcx_price = price_data['coindcx']['price']
            spread = price_data['spread']

            print(f"Bybit: ${bybit_price:.2f}")
            print(f"CoinDCX: ${coindcx_price:.2f}")
            print(f"Spread: {spread:.4f}%")

            # Show chunk preview
            print(self.chunk_manager.preview_chunks(
                symbol, total_quantity, bybit_price, coindcx_price
            ))

            # Create chunk pairs
            print("\nCalculating chunks...")
            bybit_chunks, coindcx_chunks = self.chunk_manager.create_chunk_pairs(
                symbol=symbol,
                total_quantity=total_quantity
            )

            num_chunks = len(bybit_chunks)
            print(f"Total chunks: {num_chunks}")
            print(f"Bybit total: {sum(bybit_chunks):.3f} {symbol} (fee-compensated)")
            print(f"CoinDCX total: {sum(coindcx_chunks):.3f} {symbol}")

            # Execute chunks
            print("\n" + "-" * 60)
            print(f"Executing {num_chunks} chunk(s)...")
            print("-" * 60)

            # Generate group ID for entire trade
            trade_start_time = time.time()
            chunk_group_id = str(uuid.uuid4())

            # Log trade start
            if hasattr(self.order_manager, 'ws_logger') and self.order_manager.ws_logger:
                try:
                    self.order_manager.ws_logger.log_trade_start(
                        symbol=symbol,
                        quantity=total_quantity,
                        num_chunks=num_chunks,
                        chunk_group_id=chunk_group_id
                    )
                except Exception as e:
                    logger.warning(f"WebSocket logger trade_start failed: {e}")

            # Initialize fee reconciliation tracking for this trade
            if hasattr(self.order_manager, 'fee_reconciliation') and self.order_manager.fee_reconciliation:
                try:
                    self.order_manager.fee_reconciliation.initialize_trade_reconciliation(
                        chunk_group_id=chunk_group_id,
                        symbol=symbol,
                        total_chunks=num_chunks
                    )
                    logger.info("Fee reconciliation tracking initialized for this trade")
                except Exception as e:
                    logger.warning(f"Fee reconciliation initialization failed: {e}")

            for i, (bybit_qty, coindcx_qty) in enumerate(zip(bybit_chunks, coindcx_chunks), 1):
                try:
                    # Execute complete chunk with active management (Phase 1 + Phase 2)
                    result = self.order_manager.execute_chunk_with_active_management(
                        symbol=symbol,
                        bybit_quantity=bybit_qty,
                        coindcx_quantity=coindcx_qty,
                        chunk_group_id=chunk_group_id,
                        chunk_sequence=i,
                        chunk_total=num_chunks
                    )

                    print(f"\n✅ Chunk {i}/{num_chunks} completed successfully")
                    print(f"   Bybit order: {result['bybit_order_id']}")
                    print(f"   CoinDCX order: {result['coindcx_order_id']}")

                except SpreadException as e:
                    print(f"❌ Chunk {i} failed: {e}")
                    print("⚠️ Spread violation - trade halted for safety")
                    return False

                except Exception as e:
                    logger.error(f"Error executing chunk {i}: {e}")
                    print(f"❌ Chunk {i} failed: {e}")
                    return False

            # Log trade completion
            if hasattr(self.order_manager, 'ws_logger') and self.order_manager.ws_logger:
                try:
                    trade_duration = time.time() - trade_start_time
                    self.order_manager.ws_logger.log_trade_complete(
                        chunk_group_id=chunk_group_id,
                        total_duration=trade_duration,
                        summary="All chunks filled successfully"
                    )
                except Exception as e:
                    logger.warning(f"WebSocket logger trade_complete failed: {e}")

            # FINAL: Check fee reconciliation and place makeup order if needed
            if hasattr(self.order_manager, 'fee_reconciliation') and self.order_manager.fee_reconciliation:
                try:
                    print("\n" + "-" * 60)
                    print("FINAL STEP: Fee Reconciliation")
                    print("-" * 60)
                    self.order_manager.fee_reconciliation.check_and_reconcile(chunk_group_id)
                except Exception as e:
                    logger.error(f"Fee reconciliation failed: {e}")
                    print(f"⚠️  Warning: Fee reconciliation failed: {e}")
                    print(f"   Please check database for fee shortfall details")

            print("\n" + "=" * 60)
            print("✓ TRADE COMPLETED SUCCESSFULLY")
            print("=" * 60)
            return True

        except Exception as e:
            logger.error(f"Error executing trade: {e}")
            print(f"\n❌ Trade failed: {e}")
            return False

    def run(self) -> None:
        """
        Run the bot interactively.
        Main entry point for user interaction.
        """
        try:
            print("\n")
            logger.info("Starting Enhanced Bot")

            # Step 1: Select coin
            symbol = self.select_coin()

            # Step 2: Check spread first
            if not self.validate_spread_with_user(symbol):
                return

            # Step 3: Get current price for USD estimation
            price_data = self.price_service.get_validated_prices(symbol)
            bybit_price = price_data['bybit']['price']

            symbol_config = self.config.get_symbol_config(symbol)
            precision = symbol_config['precision']
            min_quantity = symbol_config['min_quantity']

            # Step 4: Get trade quantity
            total_quantity = self.get_trade_quantity(symbol)

            total_usd = total_quantity * bybit_price

            # Step 5: Validate balances
            if not self.validate_balances(symbol, total_quantity, bybit_price):
                print("\n❌ Insufficient balance. Please add funds and try again.")
                return

            # Step 6: Final confirmation
            print("\n" + "=" * 60)
            print("TRADE SUMMARY")
            print("=" * 60)
            print(f"Coin: {symbol}")
            print(f"Total Quantity: {total_quantity:.{precision}f} {symbol}")
            print(f"Estimated Value: ${total_usd:,.2f} USD")
            print(f"Chunk Size: {min_quantity:.{precision}f} {symbol} (exchange minimum)")

            # Calculate actual chunks (no remainder since handled in get_trade_quantity)
            chunks, remainder_info = self.chunk_manager.calculate_chunks(symbol, total_quantity)
            num_chunks = len(chunks)

            print(f"Number of Chunks: {num_chunks}")
            print("=" * 60)

            response = input("\nProceed with trade? (yes/no): ").strip().lower()

            if response != 'yes':
                print("\n❌ Trade cancelled by user")
                return

            # Step 7: Execute trade
            success = self.execute_trade(symbol, total_quantity)

            if success:
                print("\n🎉 Trade executed successfully!")
            else:
                print("\n❌ Trade execution failed")

        except KeyboardInterrupt:
            print("\n\n⚠️ Bot stopped by user")
            logger.info("Bot stopped by user (Ctrl+C)")

        except HedgeTradingException as e:
            print(f"\n❌ Trading error: {e}")
            logger.error(f"Trading error: {e}")

        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            logger.error(f"Unexpected error: {e}", exc_info=True)

        finally:
            logger.info("Enhanced Bot session ended")
