#!/usr/bin/env python3
"""
Order Monitor with WebSocket
Monitors order status and updates database in real-time
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
import time
import json
from dotenv import load_dotenv
import threading
import asyncio
import warnings

# Suppress specific asyncio warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")

# Import from bundled exchange clients (self-contained)
from exchange_clients.bybit.bybit_spot_client import BybitSpotClient
from exchange_clients.coindcx.coindcx_futures import CoinDCXFutures

# Import WebSocket logger
from utils.websocket_order_logger import WebSocketOrderLogger

# Import ChunkManager from current package (not needed for bot integration)
# from enhanced_bot_copy import ChunkManager

load_dotenv()

class OrderMonitor:
    def __init__(self, chunk_manager=None):
        # Initialize database with connection timeout
        try:
            self.conn = psycopg2.connect(
                host=os.getenv('POSTGRES_HOST', 'localhost'),
                port=os.getenv('POSTGRES_PORT', 5432),
                database=os.getenv('POSTGRES_DB', 'hedge_bot'),
                user=os.getenv('POSTGRES_USER', 'hedge_user'),
                password=os.getenv('POSTGRES_PASSWORD', 'hedge_password'),
                connect_timeout=5  # 5 second connection timeout
            )
            self.conn.autocommit = False
            print("✅ PostgreSQL database connected for Order Monitor")
            
            # Check if modification columns exist (but don't try to create them)
            # This is faster than trying to alter the table every time
            try:
                with self.conn.cursor() as cursor:
                    cursor.execute("SELECT modified_price FROM orders LIMIT 1")
                print("✅ Modification columns already exist")
            except:
                print("⚠️  Modification columns not found - run schema setup first")
            
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            raise
        
        # Initialize exchange clients
        self.bybit_client = BybitSpotClient(
            api_key=os.getenv('BYBIT_API_KEY'),
            api_secret=os.getenv('BYBIT_API_SECRET'),
            testnet=os.getenv('BYBIT_TESTNET', 'false').lower() == 'true'
        )
        
        self.coindcx_client = CoinDCXFutures(
            api_key=os.getenv('COINDCX_API_KEY'),
            secret_key=os.getenv('COINDCX_API_SECRET')
        )
        
        self.running = True
        self.coindcx_websocket_active = False

        # In-memory cache for recent order rejections (for fast WebSocket-based detection)
        self.recent_rejections = {}  # {order_id: {'reason': 'EC_PostOnlyWillTakeLiquidity', 'timestamp': time.time()}}

        # Initialize WebSocket order logger
        try:
            log_dir = Path(__file__).parent / 'logs'
            self.ws_logger = WebSocketOrderLogger(log_dir=str(log_dir))
            print("✅ WebSocket Order Logger initialized")
        except Exception as e:
            print(f"⚠️  Warning: WebSocket logger initialization failed: {e}")
            self.ws_logger = None

        # Use provided chunk manager or create a new one
        if chunk_manager:
            self.chunk_manager = chunk_manager
            print("✅ Order Monitor initialized with external ChunkManager")
        else:
            self.chunk_manager = None
            print("✅ Order Monitor initialized (ChunkManager not required for bot integration)")

    # ========================================================================
    # Event Log Helper Methods (Immutable Audit Trail)
    # ========================================================================

    def _log_bybit_event_to_db(self, order_id: str, order_data: dict):
        """
        Log Bybit WebSocket event to immutable event log table.

        This method captures EVERY WebSocket message as an INSERT.
        APPEND-ONLY: Never updates or deletes from bybit_order_events.

        Args:
            order_id: Bybit order ID
            order_data: Complete order data from WebSocket
        """
        import json

        try:
            # Get chunk context from orders table (if exists)
            chunk_context = self._get_chunk_context(order_id)

            # Determine event type from order status
            event_type = order_data.get('orderStatus', 'UNKNOWN').upper()

            # Extract fields
            symbol = order_data.get('symbol', '')

            # INSERT into event log (FAST - never blocked)
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO bybit_order_events (
                        order_id, symbol, event_type, order_status,
                        side, order_type, price, qty,
                        cum_exec_qty, cum_exec_fee, cum_exec_value, avg_price,
                        time_in_force, reject_reason,
                        order_created_time, order_updated_time,
                        raw_payload,
                        chunk_group_id, chunk_sequence, chunk_total
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """, (
                    order_id,
                    symbol,
                    event_type,
                    order_data.get('orderStatus'),
                    order_data.get('side'),
                    order_data.get('orderType'),
                    float(order_data.get('price', 0)) if order_data.get('price') else None,
                    float(order_data.get('qty', 0)) if order_data.get('qty') else None,
                    float(order_data.get('cumExecQty', 0)) if order_data.get('cumExecQty') else None,
                    float(order_data.get('cumExecFee', 0)) if order_data.get('cumExecFee') else None,
                    float(order_data.get('cumExecValue', 0)) if order_data.get('cumExecValue') else None,
                    float(order_data.get('avgPrice', 0)) if order_data.get('avgPrice') else None,
                    order_data.get('timeInForce'),
                    order_data.get('rejectReason'),
                    int(order_data.get('createdTime', 0)) if order_data.get('createdTime') else None,
                    int(order_data.get('updatedTime', 0)) if order_data.get('updatedTime') else None,
                    json.dumps(order_data),  # Complete WebSocket payload
                    chunk_context.get('chunk_group_id'),
                    chunk_context.get('chunk_sequence'),
                    chunk_context.get('chunk_total')
                ))
                self.conn.commit()

            print(f"📝 Bybit event logged: {order_id[:8]}... → {event_type}")

        except Exception as e:
            # Don't fail WebSocket processing if event logging fails
            print(f"⚠️  Warning: Failed to log Bybit event for {order_id[:8]}...: {e}")
            try:
                self.conn.rollback()
            except:
                pass

    def _log_coindcx_event_to_db(self, order_id: str, order_data: dict):
        """
        Log CoinDCX WebSocket event to immutable event log table.

        This method captures EVERY WebSocket message as an INSERT.
        APPEND-ONLY: Never updates or deletes from coindcx_order_events.

        Args:
            order_id: CoinDCX order ID
            order_data: Complete order data from WebSocket
        """
        import json

        try:
            # Get chunk context from orders table (if exists)
            chunk_context = self._get_chunk_context(order_id)

            # Determine event type from status
            event_type = order_data.get('status', 'unknown').lower()

            # Extract fields
            pair = order_data.get('pair', '')

            # INSERT into event log (FAST - never blocked)
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO coindcx_order_events (
                        order_id, pair, event_type, order_status,
                        side, order_type, price, total_quantity, remaining_quantity,
                        avg_price, fee_amount,
                        raw_payload,
                        chunk_group_id, chunk_sequence, chunk_total
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """, (
                    order_id,
                    pair,
                    event_type,
                    order_data.get('status'),
                    order_data.get('side'),
                    order_data.get('order_type'),
                    float(order_data.get('price', 0)) if order_data.get('price') else None,
                    float(order_data.get('total_quantity', 0)) if order_data.get('total_quantity') else None,
                    float(order_data.get('remaining_quantity', 0)) if order_data.get('remaining_quantity') else None,
                    float(order_data.get('avg_price', 0)) if order_data.get('avg_price') else None,
                    float(order_data.get('fee_amount', 0)) if order_data.get('fee_amount') else None,
                    json.dumps(order_data),  # Complete WebSocket payload
                    chunk_context.get('chunk_group_id'),
                    chunk_context.get('chunk_sequence'),
                    chunk_context.get('chunk_total')
                ))
                self.conn.commit()

            print(f"📝 CoinDCX event logged: {order_id[:8]}... → {event_type}")

        except Exception as e:
            # Don't fail WebSocket processing if event logging fails
            print(f"⚠️  Warning: Failed to log CoinDCX event for {order_id[:8]}...: {e}")
            try:
                self.conn.rollback()
            except:
                pass

    def ensure_modification_columns(self):
        """Ensure modification tracking columns exist in the orders table"""
        try:
            with self.conn.cursor() as cursor:
                # Add modified_price column if it doesn't exist
                try:
                    cursor.execute("""
                        ALTER TABLE orders ADD COLUMN IF NOT EXISTS modified_price NUMERIC(10,2)
                    """)
                except:
                    pass  # Column might already exist
                
                # Add modified_quantity column if it doesn't exist
                try:
                    cursor.execute("""
                        ALTER TABLE orders ADD COLUMN IF NOT EXISTS modified_quantity NUMERIC(10,8)
                    """)
                except:
                    pass  # Column might already exist
                
                # Add modified_at column if it doesn't exist
                try:
                    cursor.execute("""
                        ALTER TABLE orders ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP WITH TIME ZONE
                    """)
                except:
                    pass  # Column might already exist
                
                # Add is_modified column if it doesn't exist
                try:
                    cursor.execute("""
                        ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_modified BOOLEAN DEFAULT FALSE
                    """)
                except:
                    pass  # Column might already exist
                
                self.conn.commit()
                print("✅ Modification tracking columns ensured")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Warning: Could not ensure modification columns: {e}")
    
    def update_order_modification(self, order_id, new_price, new_quantity):
        """Update order with new price/quantity when modification is detected"""
        try:
            with self.conn.cursor() as cursor:
                # First get the original price and quantity to store in modified columns
                cursor.execute("""
                    SELECT price, quantity FROM orders WHERE order_id = %s
                """, (order_id,))
                row = cursor.fetchone()
                
                if row:
                    original_price, original_quantity = row
                    
                    # Update main price/quantity columns with new values
                    # Store original values in modified_price/modified_quantity for tracking
                    cursor.execute("""
                        UPDATE orders 
                        SET price = %s, quantity = %s,
                            modified_price = %s, modified_quantity = %s, 
                            modified_at = NOW(), is_modified = TRUE
                        WHERE order_id = %s
                    """, (new_price, new_quantity, original_price, original_quantity, order_id))
                    
                    self.conn.commit()
                    print(f"✅ Order {order_id[:8]}... updated: ${original_price}→${new_price}, {original_quantity}→{new_quantity}")
                else:
                    print(f"⚠️  Order {order_id[:8]}... not found for modification tracking")
                    
        except Exception as e:
            self.conn.rollback()
            print(f"❌ Error updating order modification: {e}")
    
    def get_pending_orders(self):
        """Get pending orders from database"""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT exchange, order_id, side, price, quantity, is_modified, modified_price, modified_quantity
                    FROM orders 
                    WHERE status = 'PLACED'
                    ORDER BY placed_at DESC
                """)
                orders = cursor.fetchall()
                return orders
        except Exception as e:
            print(f"❌ Error getting pending orders: {e}")
            return []
    
    def update_order_status(self, order_id, status, fill_price=None, reject_reason=None):
        """
        Update order status in database and log lifecycle event.

        Args:
            order_id: Order ID to update
            status: New status (FILLED, CANCELLED, REJECTED, OPEN, etc.)
            fill_price: Fill price (for FILLED status)
            reject_reason: Rejection reason (for REJECTED status, e.g., 'EC_PostOnlyWillTakeLiquidity')
        """
        try:
            # First, get chunk context from the order
            chunk_info = None
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT chunk_group_id, chunk_sequence, exchange, side, quantity
                    FROM orders
                    WHERE order_id = %s
                """, (order_id,))
                row = cursor.fetchone()
                if row:
                    chunk_info = {
                        'chunk_group_id': row[0],
                        'chunk_sequence': row[1],
                        'exchange': row[2],
                        'side': row[3],
                        'quantity': row[4]
                    }

            # Update order status
            if fill_price:
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE orders
                        SET status = %s, fill_price = %s, filled_at = NOW()
                        WHERE order_id = %s
                    """, (status, fill_price, order_id))
            elif reject_reason:
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE orders
                        SET status = %s, reject_reason = %s, filled_at = NOW()
                        WHERE order_id = %s
                    """, (status, reject_reason, order_id))
            else:
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE orders
                        SET status = %s
                        WHERE order_id = %s
                    """, (status, order_id))

            # Log lifecycle event if we have chunk context
            if chunk_info and chunk_info['chunk_group_id']:
                event_details = {}
                if fill_price:
                    event_details['fill_price'] = float(fill_price)
                if reject_reason:
                    event_details['reject_reason'] = reject_reason
                if chunk_info.get('side'):
                    event_details['side'] = chunk_info['side']
                if chunk_info.get('quantity'):
                    event_details['quantity'] = float(chunk_info['quantity'])

                import json
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO order_lifecycle_log (
                            chunk_group_id, chunk_sequence, exchange, order_id,
                            event_type, event_details, timestamp
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        chunk_info['chunk_group_id'],
                        chunk_info['chunk_sequence'],
                        chunk_info['exchange'],
                        order_id,
                        status,  # FILLED, REJECTED, CANCELLED, etc.
                        json.dumps(event_details) if event_details else None
                    ))

            self.conn.commit()
            print(f"✅ Order {order_id[:8]}... updated to {status}")
        except Exception as e:
            self.conn.rollback()
            print(f"❌ Error updating order status: {e}")

    def update_order_status_with_fees(self, order_id, status, fill_price=None,
                                       cum_exec_qty=None, cum_exec_fee=None, net_received=None):
        """
        Update order status with fee information from WebSocket.

        This method captures ACTUAL fees charged by the exchange,
        enabling post-trade reconciliation strategy.

        Args:
            order_id: Order ID to update
            status: New status (should be 'FILLED')
            fill_price: Fill price
            cum_exec_qty: Cumulative executed quantity (gross) from WebSocket
            cum_exec_fee: Cumulative executed fee from WebSocket
            net_received: Net quantity received (cum_exec_qty - cum_exec_fee)
        """
        try:
            # First, get chunk context from the order
            chunk_info = None
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT chunk_group_id, chunk_sequence, exchange, side, quantity
                    FROM orders
                    WHERE order_id = %s
                """, (order_id,))
                row = cursor.fetchone()
                if row:
                    chunk_info = {
                        'chunk_group_id': row[0],
                        'chunk_sequence': row[1],
                        'exchange': row[2],
                        'side': row[3],
                        'quantity': row[4]
                    }

            # Update order with fee information
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE orders
                    SET status = %s, fill_price = %s, filled_at = NOW(),
                        cumExecFee = %s, cumExecQty = %s, net_received = %s
                    WHERE order_id = %s
                """, (status, fill_price, cum_exec_fee, cum_exec_qty, net_received, order_id))

            # Log lifecycle event with fee details
            if chunk_info and chunk_info['chunk_group_id']:
                event_details = {
                    'fill_price': float(fill_price) if fill_price else 0,
                    'side': chunk_info.get('side'),
                    'quantity': float(chunk_info.get('quantity', 0)),
                    'cum_exec_qty': float(cum_exec_qty) if cum_exec_qty else 0,
                    'cum_exec_fee': float(cum_exec_fee) if cum_exec_fee else 0,
                    'net_received': float(net_received) if net_received else 0
                }

                import json
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO order_lifecycle_log (
                            chunk_group_id, chunk_sequence, exchange, order_id,
                            event_type, event_details, timestamp
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        chunk_info['chunk_group_id'],
                        chunk_info['chunk_sequence'],
                        chunk_info['exchange'],
                        order_id,
                        status,
                        json.dumps(event_details)
                    ))

            self.conn.commit()

            # Log fee information for transparency
            if cum_exec_fee and net_received:
                print(f"✅ Order {order_id[:8]}... updated to {status}")
                print(f"   Gross filled: {cum_exec_qty:.8f}")
                print(f"   Fee charged: {cum_exec_fee:.8f}")
                print(f"   Net received: {net_received:.8f}")
            else:
                print(f"✅ Order {order_id[:8]}... updated to {status}")

        except Exception as e:
            self.conn.rollback()
            print(f"❌ Error updating order status with fees: {e}")

    def _store_recent_rejection(self, order_id, reason):
        """
        Store rejection reason in memory for quick lookup by OrderManager.

        This enables WebSocket-based instant rejection detection (100-500ms)
        instead of waiting for API query (1.5s).

        Args:
            order_id: Order ID that was rejected
            reason: Rejection reason from Bybit (e.g., 'EC_PostOnlyWillTakeLiquidity')
        """
        self.recent_rejections[order_id] = {
            'reason': reason,
            'timestamp': time.time()
        }

        # Clean up old rejections (>60 seconds) to prevent memory leak
        cutoff = time.time() - 60
        self.recent_rejections = {
            oid: data for oid, data in self.recent_rejections.items()
            if data['timestamp'] > cutoff
        }

    def get_rejection_reason(self, order_id):
        """
        Get rejection reason for an order if recently rejected.

        Used by OrderManager to detect post-only rejections instantly
        via WebSocket instead of waiting for API query.

        Args:
            order_id: Order ID to check

        Returns:
            str: Rejection reason (e.g., 'EC_PostOnlyWillTakeLiquidity') or None
        """
        rejection_data = self.recent_rejections.get(order_id)
        if rejection_data:
            return rejection_data.get('reason')
        return None

    def _get_chunk_context(self, order_id):
        """
        Get chunk context for an order from database.

        Used by WebSocket logger to include chunk information in logs.

        Args:
            order_id: Order ID to look up

        Returns:
            dict: Chunk context with chunk_group_id, chunk_sequence, chunk_total, symbol
                  Returns empty dict if order not found
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT chunk_group_id, chunk_sequence, chunk_total, symbol
                    FROM orders
                    WHERE order_id = %s
                """, (order_id,))
                row = cursor.fetchone()

                if row:
                    return {
                        'chunk_group_id': row[0],
                        'chunk_sequence': row[1],
                        'chunk_total': row[2],
                        'chunk_phase': 'UNKNOWN',  # Will be determined by bot
                        'symbol': row[3]
                    }
        except Exception as e:
            print(f"⚠️  Error getting chunk context for {order_id[:8]}...: {e}")

        return {
            'chunk_group_id': None,
            'chunk_sequence': None,
            'chunk_total': None,
            'chunk_phase': 'UNKNOWN',
            'symbol': None
        }

    def check_bybit_order(self, order_id):
        """Check Bybit order status via REST API"""
        try:
            # Get order details from exchange
            result = self.bybit_client.get_open_orders(symbol="ETHUSDT")
            if result and result.get('retCode') == 0:
                open_orders = result['result']['list']
                order_found = None
                for order in open_orders:
                    if order['orderId'] == order_id:
                        order_found = order
                        break
                
                if order_found:
                    # Order is still open, check for price/quantity modifications
                    current_price = float(order_found['price'])
                    current_qty = float(order_found['qty'])
                    
                    # Get original order details from database
                    with self.conn.cursor() as cursor:
                        cursor.execute("""
                            SELECT price, quantity FROM orders WHERE order_id = %s
                        """, (order_id,))
                        row = cursor.fetchone()
                        if row:
                            original_price, original_qty = row
                            # Check if price or quantity has been modified
                            if current_price != float(original_price) or current_qty != float(original_qty):
                                self.update_order_modification(order_id, current_price, current_qty)
                
                if not order_found:
                    # Order not open - likely filled
                    # Get order history to find fill price
                    history = self.bybit_client.get_order_history(symbol="ETHUSDT", limit=20)
                    if history and history.get('retCode') == 0:
                        for order in history['result']['list']:
                            if order['orderId'] == order_id and order['orderStatus'] == 'Filled':
                                fill_price = float(order['avgPrice'])
                                self.update_order_status(order_id, 'FILLED', fill_price)
                                return True
                    
                    # If we can't find fill price, just mark as filled
                    self.update_order_status(order_id, 'FILLED')
                    return True
        
        except Exception as e:
            print(f"❌ Error checking Bybit order {order_id[:8]}...: {e}")
        
        return False
    
    def check_coindcx_order(self, order_id):
        """Check CoinDCX order status via REST API"""
        # Skip REST API check if WebSocket is active
        if self.coindcx_websocket_active:
            return False
            
        try:
            # Check if order is still open
            open_orders = self.coindcx_client.get_orders(status="open", size=50)
            
            # If API returns empty list, it might be an API issue - don't change status
            if not open_orders:
                print(f"⚠️  CoinDCX API returned no orders - keeping order {order_id[:8]}... as PLACED")
                return False
            
            order_found = None
            for order in open_orders:
                if order.get('id') == order_id:
                    order_found = order
                    break
            
            if order_found:
                # Order is still open, check for price/quantity modifications
                current_price = float(order_found.get('price', 0))
                current_qty = float(order_found.get('total_quantity', 0))
                
                # Get original order details from database
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT price, quantity FROM orders WHERE order_id = %s
                    """, (order_id,))
                    row = cursor.fetchone()
                    if row:
                        original_price, original_qty = row
                        # Check if price or quantity has been modified
                        if current_price != float(original_price) or current_qty != float(original_qty):
                            self.update_order_modification(order_id, current_price, current_qty)
            
            if not order_found:
                # If not open, check if filled
                filled_orders = self.coindcx_client.get_orders(status="filled", size=50)
                if filled_orders:  # Only proceed if we get actual data
                    for order in filled_orders:
                        if order.get('id') == order_id:
                            fill_price = float(order.get('avg_price', order.get('price', 0)))
                            if fill_price > 0:
                                self.update_order_status(order_id, 'FILLED', fill_price)
                                return True
                
                # Only mark as cancelled if we get actual cancelled orders data
                cancelled_orders = self.coindcx_client.get_orders(status="cancelled", size=50)
                if cancelled_orders and any(o.get('id') == order_id for o in cancelled_orders):
                    self.update_order_status(order_id, 'CANCELLED')
                    return True
                else:
                    pass
                
                # If we can't get reliable data from API, don't change status
                print(f"⚠️  CoinDCX order {order_id[:8]}... status unclear - keeping as PLACED")
                return False
        
        except Exception as e:
            print(f"❌ Error checking CoinDCX order {order_id[:8]}...: {e}")
        
        return False

    def get_detailed_order_info(self, exchange, order_id, symbol="BTCUSDT"):
        """Get detailed order execution information from exchange API"""
        try:
            if exchange == "Bybit":
                # Get order history to find execution details
                result = self.bybit_client.get_order_history(symbol=symbol, limit=50)
                if result and result.get('retCode') == 0:
                    for order in result['result']['list']:
                        if order['orderId'] == order_id:
                            return {
                                'orderId': order['orderId'],
                                'symbol': order['symbol'],
                                'side': order['side'],
                                'orderType': order['orderType'],
                                'qty': float(order['qty']),
                                'price': float(order['price']),
                                'avgPrice': float(order['avgPrice']) if order['avgPrice'] else 0,
                                'cumExecQty': float(order['cumExecQty']),
                                'cumExecValue': float(order['cumExecValue']),
                                'cumExecFee': float(order['cumExecFee']),
                                'orderStatus': order['orderStatus'],
                                'timeInForce': order['timeInForce'],
                                'execType': order.get('execType', 'Unknown'),
                                'createdTime': order['createdTime'],
                                'updatedTime': order['updatedTime']
                            }

                # If not in history, try executions
                exec_result = self.bybit_client.get_executions(symbol=symbol, limit=50)
                if exec_result and exec_result.get('retCode') == 0:
                    for execution in exec_result['result']['list']:
                        if execution['orderId'] == order_id:
                            return {
                                'orderId': execution['orderId'],
                                'symbol': execution['symbol'],
                                'side': execution['side'],
                                'qty': float(execution['orderQty']),
                                'price': float(execution['orderPrice']),
                                'avgPrice': float(execution['execPrice']),
                                'cumExecQty': float(execution['execQty']),
                                'cumExecValue': float(execution['execValue']),
                                'cumExecFee': float(execution['execFee']),
                                'execType': execution.get('execType', 'Trade'),
                                'isMaker': execution.get('isMaker', False),
                                'createdTime': execution['execTime'],
                                'updatedTime': execution['execTime']
                            }

            elif exchange == "CoinDCX":
                # Get CoinDCX order details
                try:
                    filled_orders = self.coindcx_client.get_orders(status="filled", size=50)
                    if filled_orders:
                        for order in filled_orders:
                            if order.get('id') == order_id:
                                return {
                                    'orderId': order['id'],
                                    'pair': order['pair'],
                                    'side': order['side'],
                                    'qty': float(order['total_quantity']),
                                    'price': float(order['price']),
                                    'avgPrice': float(order.get('avg_price', order['price'])),
                                    'cumExecQty': float(order['total_quantity']) - float(order.get('remaining_quantity', 0)),
                                    'cumExecFee': float(order.get('fee_amount', 0)),
                                    'orderStatus': order['status'],
                                    'makerFee': float(order.get('maker_fee', 0)),
                                    'takerFee': float(order.get('taker_fee', 0)),
                                    'createdTime': order.get('created_at', 0),
                                    'updatedTime': order.get('updated_at', 0)
                                }
                except Exception as e:
                    print(f"⚠️ Error getting CoinDCX order details: {e}")

        except Exception as e:
            print(f"❌ Error getting detailed order info for {exchange} {order_id[:8]}...: {e}")

        return None

    def analyze_order_fill(self, exchange, order_id, expected_qty, expected_fee_rate, target_received_qty):
        """Analyze order fill details and compare against expectations"""
        print(f"\n📊 DETAILED ORDER FILL ANALYSIS")
        print(f"🔍 Analyzing {exchange} order {order_id[:8]}...")

        # Get detailed order information
        order_details = self.get_detailed_order_info(exchange, order_id)

        if not order_details:
            print(f"❌ Could not retrieve detailed order information")
            return

        print(f"✅ Order details retrieved successfully")
        print(f"📋 ORDER EXECUTION SUMMARY:")
        print(f"   Exchange: {exchange}")
        print(f"   Order ID: {order_id[:12]}...")
        print(f"   Symbol: {order_details.get('symbol', order_details.get('pair', 'Unknown'))}")
        print(f"   Side: {order_details.get('side', 'Unknown').upper()}")

        # Quantity Analysis
        ordered_qty = order_details.get('qty', 0)
        filled_qty = order_details.get('cumExecQty', 0)
        print(f"\n💰 QUANTITY ANALYSIS:")
        print(f"   Ordered Quantity: {ordered_qty:.6f}")
        print(f"   Filled Quantity: {filled_qty:.6f}")
        print(f"   Expected Quantity: {expected_qty:.6f}")
        print(f"   Target After Fees: {target_received_qty:.6f}")

        # Fee Analysis
        actual_fee = order_details.get('cumExecFee', 0)
        expected_fee = filled_qty * expected_fee_rate
        print(f"\n💳 FEE ANALYSIS:")
        print(f"   Actual Fee: {actual_fee:.8f}")
        print(f"   Expected Fee ({expected_fee_rate*100:.3f}%): {expected_fee:.8f}")

        # Calculate actual received (for Bybit BUY orders)
        if exchange == "Bybit" and order_details.get('side', '').upper() == "BUY":
            actual_received = filled_qty - actual_fee
            print(f"   Actual Received: {actual_received:.8f}")
            print(f"   Target Received: {target_received_qty:.8f}")

            quantity_diff = actual_received - target_received_qty
            print(f"   Difference: {quantity_diff:+.8f}")

            if abs(quantity_diff) < 0.0000001:
                print(f"   ✅ PERFECT MATCH - quantities align!")
            else:
                print(f"   ⚠️ QUANTITY MISMATCH detected")

        # Maker/Taker Analysis
        if 'isMaker' in order_details:
            maker_status = "MAKER" if order_details['isMaker'] else "TAKER"
            print(f"\n🎯 EXECUTION TYPE:")
            print(f"   Order Type: {maker_status}")
            if not order_details['isMaker']:
                print(f"   ⚠️ WARNING: Order executed as TAKER (higher fees)")
        elif 'execType' in order_details:
            print(f"\n🎯 EXECUTION TYPE:")
            print(f"   Execution Type: {order_details['execType']}")

        # Price Analysis
        order_price = order_details.get('price', 0)
        fill_price = order_details.get('avgPrice', 0)
        print(f"\n💲 PRICE ANALYSIS:")
        print(f"   Order Price: ${order_price:.2f}")
        print(f"   Fill Price: ${fill_price:.2f}")
        print(f"   Price Difference: ${fill_price - order_price:+.2f}")

        print(f"{'='*50}")

        return {
            'ordered_qty': ordered_qty,
            'filled_qty': filled_qty,
            'actual_fee': actual_fee,
            'expected_fee': expected_fee,
            'fill_price': fill_price,
            'order_price': order_price,
            'is_maker': order_details.get('isMaker'),
            'exec_type': order_details.get('execType')
        }

    def setup_bybit_websocket(self):
        """Setup Bybit WebSocket for real-time order updates"""
        try:
            def on_order_update(message):
                """Handle Bybit order updates"""
                try:
                    if message.get('topic') == 'order':
                        for order in message.get('data', []):
                            order_id = order.get('orderId')
                            status = order.get('orderStatus')

                            # ========================================================================
                            # STEP 1: Log to IMMUTABLE event table (NEW - prevents double-fill!)
                            # ========================================================================
                            if order_id:
                                try:
                                    self._log_bybit_event_to_db(order_id, order)
                                except Exception as log_err:
                                    print(f"⚠️  Event logging failed: {log_err}")

                            # ========================================================================
                            # STEP 2: Log complete WebSocket message (file logger)
                            # ========================================================================
                            # LOG COMPLETE WEBSOCKET MESSAGE FIRST (before any processing)
                            if self.ws_logger and order_id:
                                try:
                                    chunk_context = self._get_chunk_context(order_id)
                                    event_summary = f"Bybit order {order_id[:8]}... | Status: {status}"

                                    # Add details to summary based on status
                                    if status == 'Filled':
                                        avg_price = order.get('avgPrice', 0)
                                        cum_qty = order.get('cumExecQty', 0)
                                        cum_fee = order.get('cumExecFee', 0)
                                        event_summary += f" | Fill @ ${avg_price} | Qty: {cum_qty} | Fee: {cum_fee}"
                                    elif status == 'Rejected':
                                        reject_reason = order.get('rejectReason', 'Unknown')
                                        event_summary += f" | Reason: {reject_reason}"
                                    elif status == 'New':
                                        price = order.get('price', 0)
                                        qty = order.get('qty', 0)
                                        event_summary += f" | Price: ${price} | Qty: {qty}"

                                    self.ws_logger.log_websocket_event(
                                        exchange='bybit',
                                        websocket_message=message,  # Complete raw message
                                        chunk_context=chunk_context,
                                        event_summary=event_summary,
                                        order_id=order_id
                                    )
                                except Exception as log_err:
                                    print(f"⚠️  WebSocket logging failed: {log_err}")

                            if order_id and status:
                                # Check for price/quantity modifications
                                current_price = float(order.get('price', 0))
                                current_qty = float(order.get('qty', 0))
                                
                                # Get original order details from database
                                with self.conn.cursor() as cursor:
                                    cursor.execute("""
                                        SELECT price, quantity FROM orders WHERE order_id = %s
                                    """, (order_id,))
                                    row = cursor.fetchone()
                                    if row:
                                        original_price, original_qty = row
                                        # Check if price or quantity has been modified
                                        if current_price != float(original_price) or current_qty != float(original_qty):
                                            self.update_order_modification(order_id, current_price, current_qty)
                                
                                # Handle status updates
                                if status == 'Filled':
                                    fill_price = float(order.get('avgPrice', 0))
                                    cum_exec_qty = float(order.get('cumExecQty', 0))
                                    cum_exec_fee = float(order.get('cumExecFee', 0))
                                    net_received = cum_exec_qty - cum_exec_fee if cum_exec_qty and cum_exec_fee else None

                                    if fill_price > 0:
                                        # Try to use new method with fee data (backwards compatible)
                                        try:
                                            self.update_order_status_with_fees(
                                                order_id, 'FILLED', fill_price,
                                                cum_exec_qty, cum_exec_fee, net_received
                                            )
                                        except Exception as e:
                                            # If columns don't exist yet, fall back to old method
                                            if "does not exist" in str(e):
                                                self.update_order_status(order_id, 'FILLED', fill_price)
                                            else:
                                                raise  # Re-raise if it's a different error

                                        print(f"🔔 Bybit WebSocket: Order {order_id[:8]}... FILLED @ ${fill_price}")
                                        # Notify chunk manager about order update
                                        try:
                                            self.chunk_manager.on_order_update("Bybit", order_id, "FILLED", fill_price)
                                        except Exception as e:
                                            print(f"❌ Error notifying chunk manager: {e}")
                                elif status == 'Cancelled':
                                    self.update_order_status(order_id, 'CANCELLED')
                                    print(f"🔔 Bybit WebSocket: Order {order_id[:8]}... CANCELLED")
                                    # Notify chunk manager about order update
                                    try:
                                        self.chunk_manager.on_order_update("Bybit", order_id, "CANCELLED")
                                    except Exception as e:
                                        print(f"❌ Error notifying chunk manager: {e}")
                                elif status == 'Rejected':
                                    reject_reason = order.get('rejectReason', 'Unknown')
                                    self.update_order_status(order_id, 'REJECTED', reject_reason=reject_reason)
                                    print(f"🔔 Bybit WebSocket: Order {order_id[:8]}... REJECTED - {reject_reason}")

                                    # Store rejection reason in memory for quick lookup
                                    self._store_recent_rejection(order_id, reject_reason)

                                    # Notify chunk manager about order update
                                    try:
                                        self.chunk_manager.on_order_update("Bybit", order_id, "REJECTED", reject_reason=reject_reason)
                                    except Exception as e:
                                        print(f"❌ Error notifying chunk manager: {e}")
                                elif status == 'New':
                                    # Order successfully placed and active
                                    print(f"🔔 Bybit WebSocket: Order {order_id[:8]}... NEW (active)")
                                    # Update database status to indicate order is confirmed
                                    self.update_order_status(order_id, 'OPEN')
                except Exception as e:
                    print(f"❌ Error processing Bybit WebSocket message: {e}")
            
            def on_execution_update(message):
                """Handle Bybit execution (fill) updates"""
                try:
                    if message.get('topic') == 'execution':
                        for execution in message.get('data', []):
                            order_id = execution.get('orderId')
                            exec_price = execution.get('execPrice')
                            exec_qty = execution.get('execQty')
                            exec_fee = execution.get('execFee')

                            # ========================================================================
                            # STEP 1: Log to IMMUTABLE event table (NEW - prevents double-fill!)
                            # ========================================================================
                            if order_id:
                                try:
                                    self._log_bybit_event_to_db(order_id, execution)
                                except Exception as log_err:
                                    print(f"⚠️  Execution event logging failed: {log_err}")

                            # ========================================================================
                            # STEP 2: Log complete WebSocket message (file logger)
                            # ========================================================================
                            # LOG COMPLETE WEBSOCKET MESSAGE FIRST
                            if self.ws_logger and order_id:
                                try:
                                    chunk_context = self._get_chunk_context(order_id)
                                    event_summary = f"Bybit execution {order_id[:8]}... | Price: ${exec_price} | Qty: {exec_qty} | Fee: {exec_fee}"

                                    self.ws_logger.log_websocket_event(
                                        exchange='bybit',
                                        websocket_message=message,  # Complete raw message
                                        chunk_context=chunk_context,
                                        event_summary=event_summary,
                                        order_id=order_id
                                    )
                                except Exception as log_err:
                                    print(f"⚠️  WebSocket logging failed: {log_err}")

                            if order_id and exec_price:
                                fill_price = float(exec_price)
                                cum_exec_qty = float(exec_qty) if exec_qty else None
                                cum_exec_fee = float(exec_fee) if exec_fee else None
                                net_received = cum_exec_qty - cum_exec_fee if cum_exec_qty and cum_exec_fee else None

                                # Try to use new method with fee data (backwards compatible)
                                try:
                                    self.update_order_status_with_fees(
                                        order_id, 'FILLED', fill_price,
                                        cum_exec_qty, cum_exec_fee, net_received
                                    )
                                except Exception as e:
                                    # If columns don't exist yet, fall back to old method
                                    if "does not exist" in str(e):
                                        self.update_order_status(order_id, 'FILLED', fill_price)
                                    else:
                                        raise  # Re-raise if it's a different error

                                print(f"🔔 Bybit WebSocket: Order {order_id[:8]}... EXECUTED @ ${fill_price}")
                                # Notify chunk manager about order update
                                try:
                                    self.chunk_manager.on_order_update("Bybit", order_id, "FILLED", fill_price)
                                except Exception as e:
                                    print(f"❌ Error notifying chunk manager: {e}")
                except Exception as e:
                    print(f"❌ Error processing Bybit execution message: {e}")
            
            # Subscribe to both order and execution updates via WebSocket
            websocket_success = False
            if hasattr(self.bybit_client, 'subscribe_orders'):
                order_success = self.bybit_client.subscribe_orders(on_order_update)
                exec_success = False
                if hasattr(self.bybit_client, 'subscribe_executions'):
                    exec_success = self.bybit_client.subscribe_executions(on_execution_update)
                
                if order_success or exec_success:
                    topics = []
                    if order_success:
                        topics.append("orders")
                    if exec_success:
                        topics.append("executions")
                    print(f"🔗 Bybit WebSocket monitoring enabled for: {', '.join(topics)}")
                    websocket_success = True
            
            if not websocket_success:
                print("⚠️  Bybit WebSocket not available, using REST polling")
                
        except Exception as e:
            print(f"❌ Bybit WebSocket setup failed: {e}")
    
    async def setup_coindcx_websocket(self):
        """Setup CoinDCX WebSocket for real-time order updates"""
        try:
            async def on_order_update(data):
                """Handle CoinDCX order updates (async for WebSocket compatibility)"""
                try:
                    import json
                    if isinstance(data, dict) and 'data' in data:
                        orders = json.loads(data['data'])
                        for order in orders:
                            order_id = order.get('id')
                            status = order.get('status', '').lower()

                            # ========================================================================
                            # STEP 1: Log to IMMUTABLE event table (NEW - prevents double-fill!)
                            # ========================================================================
                            if order_id:
                                try:
                                    self._log_coindcx_event_to_db(order_id, order)
                                except Exception as log_err:
                                    print(f"⚠️  CoinDCX event logging failed: {log_err}")

                            # ========================================================================
                            # STEP 2: Log complete WebSocket message (file logger)
                            # ========================================================================
                            # LOG COMPLETE WEBSOCKET MESSAGE FIRST (before any processing)
                            if self.ws_logger and order_id:
                                try:
                                    chunk_context = self._get_chunk_context(order_id)
                                    event_summary = f"CoinDCX order {order_id[:8]}... | Status: {status}"

                                    # Add details to summary based on status
                                    if status == 'filled':
                                        avg_price = order.get('avg_price', 0)
                                        total_qty = order.get('total_quantity', 0)
                                        fee = order.get('fee_amount', 0)
                                        event_summary += f" | Fill @ ${avg_price} | Qty: {total_qty} | Fee: {fee}"
                                    elif status in ['initial', 'open']:
                                        price = order.get('price', 0)
                                        qty = order.get('total_quantity', 0)
                                        event_summary += f" | Price: ${price} | Qty: {qty}"

                                    self.ws_logger.log_websocket_event(
                                        exchange='coindcx',
                                        websocket_message=data,  # Complete raw message
                                        chunk_context=chunk_context,
                                        event_summary=event_summary,
                                        order_id=order_id
                                    )
                                except Exception as log_err:
                                    print(f"⚠️  WebSocket logging failed: {log_err}")

                            if order_id and status:
                                # Check for price/quantity modifications when order is open
                                if status in ['initial', 'open']:
                                    current_price = float(order.get('price', 0))
                                    current_qty = float(order.get('total_quantity', 0))

                                    # Get original order details from database
                                    with self.conn.cursor() as cursor:
                                        cursor.execute("""
                                            SELECT price, quantity FROM orders WHERE order_id = %s
                                        """, (order_id,))
                                        row = cursor.fetchone()

                                        if row:
                                            original_price, original_qty = row
                                            # Check if price or quantity has been modified
                                            if current_price != float(original_price) or current_qty != float(original_qty):
                                                self.update_order_modification(order_id, current_price, current_qty)
                                                print(f"🔄 CoinDCX WebSocket: Order {order_id[:8]}... MODIFIED to ${current_price} for {current_qty}")
                                        else:
                                            # Order not in database
                                            # DISABLED: No longer auto-insert orders from WebSocket
                                            # Bot now uses upsert_order() which handles inserts
                                            # This prevents duplicate rows for the same order
                                            print(f"ℹ️  CoinDCX WebSocket: New order {order_id[:8]}... detected (will be inserted by bot)")
                                
                                # Handle status updates
                                if status == 'filled':
                                    avg_price = float(order.get('avg_price', 0))
                                    if avg_price > 0:
                                        self.update_order_status(order_id, 'FILLED', avg_price)
                                        print(f"🔔 CoinDCX WebSocket: Order {order_id[:8]}... FILLED @ ${avg_price}")
                                        # Notify chunk manager about order update
                                        try:
                                            self.chunk_manager.on_order_update("CoinDCX", order_id, "FILLED", avg_price)
                                        except Exception as e:
                                            print(f"❌ Error notifying chunk manager: {e}")
                                elif status == 'cancelled':
                                    self.update_order_status(order_id, 'CANCELLED')
                                    print(f"🔔 CoinDCX WebSocket: Order {order_id[:8]}... CANCELLED")
                                    # Notify chunk manager about order update
                                    try:
                                        self.chunk_manager.on_order_update("CoinDCX", order_id, "CANCELLED")
                                    except Exception as e:
                                        print(f"❌ Error notifying chunk manager: {e}")
                                elif status in ['initial', 'open']:
                                    print(f"📋 CoinDCX WebSocket: Order {order_id[:8]}... status: {status.upper()}")
                except Exception as e:
                    print(f"❌ Error processing CoinDCX WebSocket message: {e}")
            
            # Connect to CoinDCX WebSocket
            await self.coindcx_client.connect_websocket()
            self.coindcx_client.on_order_update(on_order_update)
            
            print("🔗 CoinDCX WebSocket monitoring enabled for: orders")
            self.coindcx_websocket_active = True
            
        except Exception as e:
            print(f"❌ CoinDCX WebSocket setup failed: {e}")
            print("⚠️  Falling back to REST API polling for CoinDCX orders")
    
    def monitor_loop(self):
        """Main monitoring loop"""
        print("\n🔍 Starting order monitoring...")
        print("Press Ctrl+C to stop\n")
        
        # Setup WebSocket if available (with timeout)
        try:
            self.setup_bybit_websocket()
        except Exception as e:
            print(f"⚠️  Bybit WebSocket setup failed, continuing with REST polling: {e}")
        
        # Setup CoinDCX WebSocket in background thread with timeout
        coindcx_thread = None
        if hasattr(self.coindcx_client, 'connect_websocket'):
            def run_coindcx_websocket():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # Set a timeout for WebSocket connection
                    loop.run_until_complete(asyncio.wait_for(
                        self.setup_coindcx_websocket(), 
                        timeout=10.0
                    ))
                    # Keep the websocket running
                    while self.running:
                        try:
                            loop.run_until_complete(asyncio.sleep(1))
                        except Exception as e:
                            print(f"⚠️  CoinDCX WebSocket runtime error: {e}")
                            break
                except asyncio.TimeoutError:
                    print("⚠️  CoinDCX WebSocket connection timeout, falling back to REST polling")
                except Exception as e:
                    print(f"⚠️  CoinDCX WebSocket error: {e}")
                finally:
                    try:
                        loop.close()
                    except:
                        pass
            
            coindcx_thread = threading.Thread(target=run_coindcx_websocket, daemon=True)
            coindcx_thread.start()
        
        # Main monitoring loop with improved error handling
        while self.running:
            try:
                # Get pending orders from database with timeout
                pending_orders = self.get_pending_orders()
                
                if not pending_orders:
                    # Silently wait when no orders to monitor (no spam)
                    time.sleep(5)
                    continue
                
                print(f"🔍 Checking {len(pending_orders)} pending orders...")
                
                # Check each pending order with individual error handling
                for exchange, order_id, side, price, quantity, is_modified, modified_price, modified_quantity in pending_orders:
                    print(f"   Checking {exchange} {side} order {order_id[:8]}...")
                    
                    try:
                        if exchange == 'Bybit':
                            self.check_bybit_order(order_id)
                        elif exchange == 'CoinDCX':
                            self.check_coindcx_order(order_id)
                    except Exception as e:
                        print(f"❌ Error checking {exchange} order {order_id[:8]}...: {e}")
                        # Continue with other orders instead of stopping
                
                # Show current status
                try:
                    self.show_status()
                except Exception as e:
                    print(f"❌ Error showing status: {e}")
                
                # Wait before next check
                time.sleep(10)  # Check every 10 seconds
                
            except KeyboardInterrupt:
                print("\n🛑 Monitoring stopped by user")
                self.running = False
                break
            except Exception as e:
                print(f"❌ Monitor error: {e}")
                time.sleep(5)  # Wait before retry
    
    def show_status(self):
        """Show current order status"""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'PLACED' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN status = 'FILLED' THEN 1 ELSE 0 END) as filled,
                        SUM(CASE WHEN status = 'CANCELLED' THEN 1 ELSE 0 END) as cancelled,
                        SUM(CASE WHEN is_modified = TRUE THEN 1 ELSE 0 END) as modified
                    FROM orders
                """)
                
                row = cursor.fetchone()
                total, pending, filled, cancelled, modified = row
                
                print(f"📊 Status: {total} total | {pending or 0} pending | {filled or 0} filled | {cancelled or 0} cancelled | {modified or 0} modified")
                
                # Show recent activity
                cursor.execute("""
                    SELECT exchange, side, status, fill_price, filled_at, is_modified, modified_price, modified_quantity
                    FROM orders 
                    WHERE status IN ('FILLED', 'CANCELLED') OR is_modified = TRUE
                    ORDER BY COALESCE(filled_at, modified_at, placed_at) DESC
                    LIMIT 5
                """)
                
                recent = cursor.fetchall()
                if recent:
                    print("   Recent activity:")
                    for exchange, side, status, fill_price, filled_at, is_modified, modified_price, modified_quantity in recent:
                        fill_str = f"@ ${fill_price:.2f}" if fill_price else ""
                        time_str = filled_at.strftime('%Y-%m-%d %H:%M:%S') if filled_at else "Unknown"
                        modification_str = f" (MODIFIED to ${modified_price:.2f} x {modified_quantity})" if is_modified else ""
                        print(f"     {exchange} {side} {status} {fill_str}{modification_str} at {time_str}")
                
                print("-" * 50)
        except Exception as e:
            print(f"❌ Error showing status: {e}")
    
    def close(self):
        """Close connections gracefully"""
        self.running = False
        
        # Close database connection if it exists
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
                print("✅ Database connection closed")
            except Exception as e:
                print(f"⚠️  Error closing database connection: {e}")
        
        # Close WebSocket connections if they exist
        try:
            if hasattr(self.bybit_client, 'close_websocket'):
                self.bybit_client.close_websocket()
                print("✅ Bybit WebSocket connection closed")
        except Exception as e:
            print(f"⚠️  Error closing Bybit WebSocket: {e}")
            
        try:
            if hasattr(self.coindcx_client, 'close_websocket'):
                self.coindcx_client.close_websocket()
                print("✅ CoinDCX WebSocket connection closed")
        except Exception as e:
            print(f"⚠️  Error closing CoinDCX WebSocket: {e}")

def main():
    monitor = OrderMonitor()
    
    try:
        monitor.monitor_loop()
    except Exception as e:
        print(f"❌ Monitor error: {e}")
    finally:
        monitor.close()

if __name__ == "__main__":
    main()
