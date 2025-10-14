"""
PostgreSQL Database Connection and Operations
Handles order tracking and trade history storage.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, Dict, List, Any
from datetime import datetime
import logging
from .exceptions import DatabaseException


logger = logging.getLogger(__name__)


class Database:
    """PostgreSQL database wrapper for hedge trading bot"""

    def __init__(
        self,
        host: str = None,
        port: int = None,
        database: str = None,
        user: str = None,
        password: str = None
    ):
        """
        Initialize database connection.

        Args:
            host: Database host (default from env DB_HOST)
            port: Database port (default from env DB_PORT)
            database: Database name (default from env DB_NAME)
            user: Database user (default from env DB_USER)
            password: Database password (default from env DB_PASSWORD)
        """
        self.host = host or os.getenv('DB_HOST', 'localhost')
        self.port = port or int(os.getenv('DB_PORT', '5432'))
        self.database = database or os.getenv('DB_NAME', 'hedge_trading')
        self.user = user or os.getenv('DB_USER', 'hedgebot')
        self.password = password or os.getenv('DB_PASSWORD', '')

        self.conn: Optional[psycopg2.extensions.connection] = None
        # Don't auto-connect - let caller handle connection errors
        self._connected = False

    def connect(self) -> None:
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
            self._connected = True
            logger.info(f"Connected to PostgreSQL database: {self.database}")
        except psycopg2.Error as e:
            self._connected = False
            raise DatabaseException("connection", str(e))

    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self._connected and self.conn is not None

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def execute_query(
        self,
        query: str,
        params: tuple = None,
        fetch: bool = False
    ) -> Optional[List[Dict]]:
        """
        Execute SQL query.

        Args:
            query: SQL query string
            params: Query parameters
            fetch: Whether to fetch results

        Returns:
            Query results if fetch=True, None otherwise

        Raises:
            DatabaseException: If query execution fails
        """
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, params)
                if fetch:
                    results = cursor.fetchall()
                    self.conn.commit()  # Commit even when fetching results
                    return results
                self.conn.commit()
                return None
        except psycopg2.Error as e:
            self.conn.rollback()
            raise DatabaseException("query execution", str(e))

    def insert_order(
        self,
        chunk_group_id: str,
        exchange: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_id: str,
        status: str = 'pending',
        order_type: str = 'limit',
        chunk_sequence: int = None,
        chunk_total: int = None
    ) -> int:
        """
        Insert new order into database.

        Args:
            chunk_group_id: Group ID for chunk execution
            exchange: Exchange name (bybit/coindcx)
            symbol: Trading symbol
            side: Order side (buy/sell)
            quantity: Order quantity
            price: Order price
            order_id: Exchange order ID
            status: Order status (default: pending)
            order_type: Order type (default: limit)
            chunk_sequence: Chunk sequence number (optional)
            chunk_total: Total number of chunks (optional)

        Returns:
            Database record ID

        Raises:
            DatabaseException: If insert fails
        """
        query = """
            INSERT INTO orders (
                chunk_group_id, exchange, symbol, side, quantity, price,
                order_id, status, order_type, created_at, chunk_sequence, chunk_total
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        params = (
            chunk_group_id, exchange, symbol, side, quantity, price,
            order_id, status, order_type, datetime.now(), chunk_sequence, chunk_total
        )

        try:
            result = self.execute_query(query, params, fetch=True)
            record_id = result[0]['id']
            logger.info(
                f"Order inserted: {exchange} {side} {quantity} {symbol} "
                f"@ {price} (ID: {record_id})"
            )
            return record_id
        except DatabaseException:
            raise

    def upsert_order(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        exchange: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_id: str,
        status: str = 'PLACED',
        order_type: str = 'limit',
        chunk_total: int = None,
        is_partial_completion: bool = False,
        partial_details: dict = None,
        cumexecqty: float = None,
        cumexecfee: float = None,
        net_received: float = None
    ) -> int:
        """
        Insert or update order for a specific chunk and exchange.

        Uses (chunk_group_id, chunk_sequence, exchange) as unique key.
        If a row with this key exists, updates the order_id, price, quantity, status.
        If no row exists, inserts a new row.

        This ensures exactly one row per exchange per chunk, even when orders
        are cancelled and replaced (like CoinDCX cancel+replace pattern).

        When handling partial fills, use is_partial_completion=True and provide
        partial_details to preserve the original partial order's data.

        Args:
            chunk_group_id: Group ID for chunk execution (can be None for market orders)
            chunk_sequence: Chunk sequence number
            exchange: Exchange name (bybit/coindcx)
            symbol: Trading symbol
            side: Order side (buy/sell)
            quantity: Order quantity
            price: Order price
            order_id: Exchange order ID
            status: Order status (default: PLACED)
            order_type: Order type (default: limit)
            chunk_total: Total number of chunks (optional)
            is_partial_completion: TRUE if this order completes a partial fill
            partial_details: Dict with keys:
                - partial_order_id: Original order ID that partially filled
                - partial_filled_qty: Quantity filled by partial order
                - partial_avg_price: Average fill price of partial order
                - partial_bybit_fee_crypto: Bybit fee in crypto (ETH/BTC)
                - partial_coindcx_fee_usdt: CoinDCX fee in USDT
            cumexecqty: Cumulative executed quantity (for filled orders)
            cumexecfee: Cumulative executed fee (for filled orders)
            net_received: Net quantity received after fees (auto-calculated if None)

        Returns:
            Database record ID

        Raises:
            DatabaseException: If upsert fails
        """
        # Extract partial fill details if provided
        if is_partial_completion and partial_details:
            partial_order_id = partial_details.get('partial_order_id')
            partial_filled_qty = partial_details.get('partial_filled_qty')
            partial_avg_price = partial_details.get('partial_avg_price')
            partial_bybit_fee = partial_details.get('partial_bybit_fee_crypto')
            partial_coindcx_fee = partial_details.get('partial_coindcx_fee_usdt')
        else:
            partial_order_id = None
            partial_filled_qty = None
            partial_avg_price = None
            partial_bybit_fee = None
            partial_coindcx_fee = None

        # Auto-calculate net_received if cumexecqty and cumexecfee are provided
        if net_received is None and cumexecqty is not None and cumexecfee is not None:
            net_received = cumexecqty - cumexecfee

        query = """
            INSERT INTO orders (
                chunk_group_id, chunk_sequence, chunk_total, exchange, symbol,
                side, quantity, price, order_id, status, order_type, created_at,
                is_partial_fill_completion, partial_order_id, partial_filled_qty,
                partial_avg_price, partial_bybit_fee_crypto, partial_coindcx_fee_usdt,
                cumexecqty, cumexecfee, net_received
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chunk_group_id, chunk_sequence, exchange)
            DO UPDATE SET
                order_id = EXCLUDED.order_id,
                price = EXCLUDED.price,
                quantity = EXCLUDED.quantity,
                status = EXCLUDED.status,
                order_type = EXCLUDED.order_type,
                is_partial_fill_completion = EXCLUDED.is_partial_fill_completion,
                partial_order_id = EXCLUDED.partial_order_id,
                partial_filled_qty = EXCLUDED.partial_filled_qty,
                partial_avg_price = EXCLUDED.partial_avg_price,
                partial_bybit_fee_crypto = EXCLUDED.partial_bybit_fee_crypto,
                partial_coindcx_fee_usdt = EXCLUDED.partial_coindcx_fee_usdt,
                cumexecqty = COALESCE(EXCLUDED.cumexecqty, orders.cumexecqty),
                cumexecfee = COALESCE(EXCLUDED.cumexecfee, orders.cumexecfee),
                net_received = COALESCE(EXCLUDED.net_received, orders.net_received),
                updated_at = NOW()
            RETURNING id
        """
        params = (
            chunk_group_id, chunk_sequence, chunk_total, exchange, symbol,
            side, quantity, price, order_id, status, order_type,
            is_partial_completion, partial_order_id, partial_filled_qty,
            partial_avg_price, partial_bybit_fee, partial_coindcx_fee,
            cumexecqty, cumexecfee, net_received
        )

        try:
            result = self.execute_query(query, params, fetch=True)

            if not result or len(result) == 0:
                logger.error(
                    f"CRITICAL: upsert_order returned no rows for "
                    f"{exchange} {order_id[:8]}... chunk {chunk_sequence}"
                )
                return None

            record_id = result[0]['id']
            logger.info(
                f"Order upserted: {exchange} {side} {quantity} {symbol} "
                f"@ {price} order_id={order_id[:8]}... (DB ID: {record_id})"
            )

            # Verify the record exists
            verify_query = "SELECT id FROM orders WHERE id = %s"
            verify_result = self.execute_query(verify_query, (record_id,), fetch=True)

            if not verify_result:
                logger.error(
                    f"CRITICAL: Upserted order {record_id} not found in database! "
                    f"Possible transaction rollback or race condition."
                )
                return None

            return record_id
        except DatabaseException as e:
            logger.error(f"Upsert failed for {exchange} {order_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during upsert for {exchange} {order_id}: {e}")
            return None

    def get_chunk_total_fees(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        exchange: str
    ) -> dict:
        """
        Get total fees for a chunk, including both partial and completion orders.

        When a partial fill occurs, there are 2 orders:
        1. The partial order (stored in partial_* columns)
        2. The completion order (current row)

        This method sums both to get the total fees paid for the chunk.

        Args:
            chunk_group_id: Group ID for chunk
            chunk_sequence: Chunk sequence number
            exchange: Exchange name (bybit/coindcx)

        Returns:
            dict with keys:
                - bybit_fee_crypto: Total Bybit fee in ETH/BTC (or 0)
                - coindcx_fee_usdt: Total CoinDCX fee in USDT (or 0)
                - is_partial_completion: Whether this chunk had a partial fill

        Raises:
            DatabaseException: If query fails
        """
        query = """
            SELECT
                cumexecfee,
                partial_bybit_fee_crypto,
                partial_coindcx_fee_usdt,
                is_partial_fill_completion
            FROM orders
            WHERE chunk_group_id = %s
              AND chunk_sequence = %s
              AND exchange = %s
        """
        params = (chunk_group_id, chunk_sequence, exchange)

        try:
            result = self.execute_query(query, params, fetch=True)

            if not result or len(result) == 0:
                logger.warning(
                    f"No order found for chunk {chunk_sequence} on {exchange}"
                )
                return {
                    'bybit_fee_crypto': 0,
                    'coindcx_fee_usdt': 0,
                    'is_partial_completion': False
                }

            row = result[0]

            # Current order fee
            current_fee = float(row.get('cumexecfee') or 0)

            # Partial order fees (if any)
            partial_bybit_fee = float(row.get('partial_bybit_fee_crypto') or 0)
            partial_coindcx_fee = float(row.get('partial_coindcx_fee_usdt') or 0)

            is_partial = row.get('is_partial_fill_completion', False)

            # For Bybit: fees are in crypto, stored in cumexecfee and partial_bybit_fee_crypto
            # For CoinDCX: fees are in USDT, stored in partial_coindcx_fee_usdt
            if exchange.lower() == 'bybit':
                total_bybit_fee = current_fee + partial_bybit_fee
                total_coindcx_fee = 0
            else:  # coindcx
                total_bybit_fee = 0
                total_coindcx_fee = partial_coindcx_fee

            logger.info(
                f"Chunk {chunk_sequence} {exchange} total fees: "
                f"Bybit={total_bybit_fee:.8f} crypto, "
                f"CoinDCX={total_coindcx_fee:.8f} USDT "
                f"(partial_fill={is_partial})"
            )

            return {
                'bybit_fee_crypto': total_bybit_fee,
                'coindcx_fee_usdt': total_coindcx_fee,
                'is_partial_completion': is_partial
            }

        except DatabaseException as e:
            logger.error(f"Failed to get chunk total fees: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting chunk total fees: {e}")
            return {
                'bybit_fee_crypto': 0,
                'coindcx_fee_usdt': 0,
                'is_partial_completion': False
            }

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_quantity: float = None,
        filled_price: float = None,
        exchange: str = None
    ) -> None:
        """
        Update order status in database.

        Args:
            order_id: Exchange order ID
            status: New status (filled/cancelled/rejected)
            filled_quantity: Filled quantity (for partial fills)
            filled_price: Average fill price
            exchange: Exchange name (optional, for filtering)

        Raises:
            DatabaseException: If update fails
        """
        query = """
            UPDATE orders
            SET status = %s,
                filled_quantity = COALESCE(%s, filled_quantity),
                filled_price = COALESCE(%s, filled_price),
                filled_at = CASE
                    WHEN %s = 'FILLED' AND filled_at IS NULL THEN CURRENT_TIMESTAMP
                    ELSE filled_at
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_id = %s
        """
        params = [status, filled_quantity, filled_price, status, order_id]

        if exchange:
            query += " AND exchange = %s"
            params.append(exchange)

        try:
            self.execute_query(query, tuple(params))
            logger.info(f"Order {order_id} status updated to: {status}")
        except DatabaseException:
            raise

    def get_order_status(self, order_id: str, exchange: str = None) -> Optional[Dict]:
        """
        Get order status from database.

        Args:
            order_id: Exchange order ID
            exchange: Exchange name (optional)

        Returns:
            Order record or None if not found
        """
        query = "SELECT * FROM orders WHERE order_id = %s"
        params = [order_id]

        if exchange:
            query += " AND exchange = %s"
            params.append(exchange)

        try:
            results = self.execute_query(query, tuple(params), fetch=True)
            return results[0] if results else None
        except DatabaseException:
            return None

    def get_chunk_orders(self, chunk_group_id: str) -> List[Dict]:
        """
        Get all orders for a chunk group.

        Args:
            chunk_group_id: Chunk group ID

        Returns:
            List of order records
        """
        query = """
            SELECT * FROM orders
            WHERE chunk_group_id = %s
            ORDER BY created_at
        """
        try:
            return self.execute_query(query, (chunk_group_id,), fetch=True) or []
        except DatabaseException:
            return []

    def log_spread(
        self,
        symbol: str,
        bybit_price: float,
        coindcx_price: float,
        spread_percent: float
    ) -> None:
        """
        Log spread history to database.

        Args:
            symbol: Trading symbol
            bybit_price: Bybit price
            coindcx_price: CoinDCX price
            spread_percent: Calculated spread percentage
        """
        query = """
            INSERT INTO spread_history (
                symbol, bybit_price, coindcx_price, spread_percent, timestamp
            )
            VALUES (%s, %s, %s, %s, %s)
        """
        params = (symbol, bybit_price, coindcx_price, spread_percent, datetime.now())

        try:
            self.execute_query(query, params)
            logger.debug(f"Spread logged: {symbol} {spread_percent:.4f}%")
        except DatabaseException:
            # Don't fail trade if spread logging fails
            logger.warning(f"Failed to log spread for {symbol}")

    def log_order_event(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        exchange: str,
        event_type: str,
        order_id: str = None,
        event_details: dict = None
    ) -> bool:
        """
        Log order lifecycle event for comprehensive audit trail.

        Event types:
        - PLACED: Order placed on exchange
        - MODIFIED: Order price/quantity updated
        - CANCELLED: Order cancelled
        - FILLED: Order filled
        - REJECTED: Order rejected (e.g., post-only)
        - MARKET_FALLBACK: Market order placed after limit timeout

        Args:
            chunk_group_id: Chunk group UUID
            chunk_sequence: Chunk sequence number
            exchange: 'bybit' or 'coindcx'
            event_type: Event type (see above)
            order_id: Exchange order ID (optional for some events)
            event_details: Additional details as JSON (price, quantity, reason, etc.)

        Returns:
            True if logged successfully, False otherwise
        """
        import json

        query = """
            INSERT INTO order_lifecycle_log (
                chunk_group_id, chunk_sequence, exchange, order_id,
                event_type, event_details, timestamp
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """

        # Convert event_details dict to JSON string for JSONB column
        event_details_json = json.dumps(event_details) if event_details else None

        params = (
            chunk_group_id,
            chunk_sequence,
            exchange,
            order_id,
            event_type,
            event_details_json
        )

        try:
            self.execute_query(query, params)
            self.conn.commit()
            logger.debug(
                f"Lifecycle log: chunk {chunk_sequence}/{chunk_group_id[:8]}..., "
                f"{exchange} {event_type} {order_id[:8] if order_id else 'N/A'}..."
            )
            return True
        except DatabaseException as e:
            # Don't fail trade if logging fails
            logger.warning(f"Failed to log order event: {e}")
            return False

    def create_tables(self) -> None:
        """
        Create necessary database tables if they don't exist.
        This is a fallback - ideally use postgresql_schema.sql
        """
        # Orders table
        orders_table = """
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                chunk_group_id VARCHAR(50),
                exchange VARCHAR(20) NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                side VARCHAR(10) NOT NULL,
                quantity DECIMAL(20, 8) NOT NULL,
                price DECIMAL(20, 8) NOT NULL,
                order_id VARCHAR(100) NOT NULL,
                status VARCHAR(20) NOT NULL,
                order_type VARCHAR(20) NOT NULL,
                filled_quantity DECIMAL(20, 8),
                filled_price DECIMAL(20, 8),
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP,
                chunk_sequence INTEGER,
                chunk_total INTEGER
            )
        """

        # Spread history table
        spread_table = """
            CREATE TABLE IF NOT EXISTS spread_history (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                bybit_price DECIMAL(20, 8) NOT NULL,
                coindcx_price DECIMAL(20, 8) NOT NULL,
                spread_percent DECIMAL(10, 4) NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )
        """

        # Add reject_reason column for post-only rejection tracking (if not exists)
        add_reject_reason = """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='orders' AND column_name='reject_reason'
                ) THEN
                    ALTER TABLE orders ADD COLUMN reject_reason VARCHAR(100);
                END IF;
            END $$;
        """

        # Add chunk_sequence column for chunk tracking (if not exists)
        add_chunk_sequence = """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='orders' AND column_name='chunk_sequence'
                ) THEN
                    ALTER TABLE orders ADD COLUMN chunk_sequence INTEGER;
                END IF;
            END $$;
        """

        # Add chunk_total column for chunk tracking (if not exists)
        add_chunk_total = """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='orders' AND column_name='chunk_total'
                ) THEN
                    ALTER TABLE orders ADD COLUMN chunk_total INTEGER;
                END IF;
            END $$;
        """

        # Add updated_at column (if not exists)
        add_updated_at = """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='orders' AND column_name='updated_at'
                ) THEN
                    ALTER TABLE orders ADD COLUMN updated_at TIMESTAMP;
                END IF;
            END $$;
        """

        # Migration: Drop old UNIQUE constraint and add new one
        drop_old_constraint = """
            DO $$
            BEGIN
                -- Drop old constraint if exists
                ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_exchange_order_id_key;
            EXCEPTION
                WHEN undefined_table THEN
                    -- Table doesn't exist yet, skip
                    NULL;
            END $$;
        """

        # Migration: Make chunk_group_id nullable (for market orders)
        make_chunk_id_nullable = """
            DO $$
            BEGIN
                -- Make chunk_group_id nullable
                ALTER TABLE orders ALTER COLUMN chunk_group_id DROP NOT NULL;
            EXCEPTION
                WHEN undefined_table THEN
                    -- Table doesn't exist yet, skip
                    NULL;
                WHEN undefined_column THEN
                    -- Column doesn't exist yet, skip
                    NULL;
            END $$;
        """

        # Order lifecycle log table for comprehensive audit trail
        lifecycle_log_table = """
            CREATE TABLE IF NOT EXISTS order_lifecycle_log (
                id SERIAL PRIMARY KEY,
                chunk_group_id VARCHAR(50) NOT NULL,
                chunk_sequence INTEGER NOT NULL,
                exchange VARCHAR(20) NOT NULL,
                order_id VARCHAR(100),
                event_type VARCHAR(50) NOT NULL,
                event_details JSONB,
                timestamp TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """

        # Create indexes for lifecycle log table
        lifecycle_log_indexes = """
            DO $$
            BEGIN
                -- Index for querying by chunk
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE indexname = 'idx_chunk_lifecycle'
                ) THEN
                    CREATE INDEX idx_chunk_lifecycle
                    ON order_lifecycle_log (chunk_group_id, chunk_sequence);
                END IF;

                -- Index for querying by order_id
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE indexname = 'idx_order_lifecycle'
                ) THEN
                    CREATE INDEX idx_order_lifecycle
                    ON order_lifecycle_log (order_id);
                END IF;

                -- Index for querying by event type
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE indexname = 'idx_event_type'
                ) THEN
                    CREATE INDEX idx_event_type
                    ON order_lifecycle_log (event_type);
                END IF;
            EXCEPTION
                WHEN undefined_table THEN
                    -- Table doesn't exist yet, skip
                    NULL;
            END $$;
        """

        # Add new UNIQUE constraint for chunks
        add_chunk_constraint = """
            DO $$
            BEGIN
                -- Add unique constraint on (chunk_group_id, chunk_sequence, exchange)
                -- This ensures exactly one row per exchange per chunk
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'orders_chunk_key'
                ) THEN
                    ALTER TABLE orders
                    ADD CONSTRAINT orders_chunk_key
                    UNIQUE(chunk_group_id, chunk_sequence, exchange);
                END IF;
            EXCEPTION
                WHEN undefined_table THEN
                    -- Table doesn't exist yet, skip
                    NULL;
            END $$;
        """

        try:
            self.execute_query(orders_table)
            self.execute_query(spread_table)
            self.execute_query(lifecycle_log_table)
            self.execute_query(lifecycle_log_indexes)
            self.execute_query(add_reject_reason)
            self.execute_query(add_chunk_sequence)
            self.execute_query(add_chunk_total)
            self.execute_query(add_updated_at)
            self.execute_query(drop_old_constraint)
            self.execute_query(make_chunk_id_nullable)
            self.execute_query(add_chunk_constraint)
            logger.info("Database tables created/verified")
        except DatabaseException as e:
            logger.error(f"Failed to create tables: {e}")
            raise

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
