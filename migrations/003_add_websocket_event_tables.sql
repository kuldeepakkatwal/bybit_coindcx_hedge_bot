-- Migration 003: Add WebSocket Event Tables (Immutable Audit Log)
-- Purpose: Create append-only tables to capture EVERY WebSocket event
--          Eliminates race conditions and provides complete order lifecycle tracking
-- Date: 2025-10-10
-- Author: Hedge Trading Bot Team
--
-- RATIONALE:
-- The existing 'orders' table uses UPDATE operations which can have race conditions:
-- - Bot places market order → Row updated with new order_id
-- - WebSocket tries to update old order_id → UPDATE fails or updates wrong row
--
-- Solution: Create immutable event log tables (INSERT-only, never UPDATE/DELETE)
-- - Every WebSocket message is a new INSERT
-- - No race conditions (INSERTs never block each other)
-- - Complete audit trail (all events preserved)
-- - Bot queries latest event by order_id (always accurate)
--
-- ROLLBACK: Run 003_rollback_websocket_event_tables.sql

-- ============================================================================
-- TABLE 1: Bybit Order Events (Immutable Audit Log)
-- ============================================================================
-- Captures ALL Bybit WebSocket 'order' and 'execution' events
-- APPEND-ONLY: Never UPDATE or DELETE from this table

CREATE TABLE IF NOT EXISTS bybit_order_events (
    -- Primary key (auto-increment)
    id SERIAL PRIMARY KEY,

    -- ========================================================================
    -- Order Identification
    -- ========================================================================
    order_id VARCHAR(100) NOT NULL,
    symbol VARCHAR(20) NOT NULL,                    -- BTCUSDT, ETHUSDT, SOLUSDT

    -- ========================================================================
    -- Event Classification
    -- ========================================================================
    event_type VARCHAR(20) NOT NULL,                -- NEW, FILLED, PARTIAL_FILLED, CANCELLED, REJECTED
    order_status VARCHAR(20),                       -- Raw from WebSocket: 'New', 'Filled', 'PartiallyFilled', etc.

    -- ========================================================================
    -- Order Details
    -- ========================================================================
    side VARCHAR(10),                               -- Buy, Sell
    order_type VARCHAR(20),                         -- Limit, Market
    price NUMERIC(20, 8),                           -- Order price (0 for market orders)
    qty NUMERIC(18, 8),                             -- Order quantity

    -- ========================================================================
    -- Execution Details (CRITICAL for fee reconciliation)
    -- ========================================================================
    cum_exec_qty NUMERIC(18, 8),                    -- Cumulative executed quantity (gross, before fees)
    cum_exec_fee NUMERIC(18, 8),                    -- Cumulative fee charged (THIS IS THE SHORTFALL WE NEED TO BUY)
    cum_exec_value NUMERIC(20, 8),                  -- Cumulative executed value in USDT
    avg_price NUMERIC(20, 8),                       -- Average fill price

    -- ========================================================================
    -- Order Metadata
    -- ========================================================================
    time_in_force VARCHAR(20),                      -- GTC, IOC, FOK, PostOnly
    reject_reason VARCHAR(100),                     -- For REJECTED status (e.g., 'EC_PostOnlyWillTakeLiquidity')

    -- ========================================================================
    -- Timestamps (Exchange vs Local)
    -- ========================================================================
    order_created_time BIGINT,                      -- Exchange timestamp (milliseconds since epoch)
    order_updated_time BIGINT,                      -- Exchange timestamp (milliseconds since epoch)
    event_received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,  -- Local timestamp when event received

    -- ========================================================================
    -- Raw WebSocket Payload (Complete Data Preservation)
    -- ========================================================================
    raw_payload JSONB,                              -- Complete WebSocket message (for debugging/future analysis)

    -- ========================================================================
    -- Chunk Linking (Connect to Trade)
    -- ========================================================================
    chunk_group_id UUID,                            -- Links to orders.chunk_group_id
    chunk_sequence INTEGER,                         -- Chunk number (1, 2, 3...)
    chunk_total INTEGER,                            -- Total chunks in this trade

    -- ========================================================================
    -- Indexes for Fast Queries
    -- ========================================================================
    CONSTRAINT bybit_order_events_order_id_idx UNIQUE (id)
);

-- Index for primary query: Get latest event for order_id
CREATE INDEX IF NOT EXISTS idx_bybit_events_order_id
    ON bybit_order_events(order_id, event_received_at DESC);

-- Index for chunk queries: Get all events for a trade
CREATE INDEX IF NOT EXISTS idx_bybit_events_chunk_group
    ON bybit_order_events(chunk_group_id, chunk_sequence);

-- Index for time-based queries: Get recent events
CREATE INDEX IF NOT EXISTS idx_bybit_events_timestamp
    ON bybit_order_events(event_received_at DESC);

-- Index for event type queries: Find all fills, rejections, etc.
CREATE INDEX IF NOT EXISTS idx_bybit_events_type
    ON bybit_order_events(event_type);

-- Index for JSONB payload queries (optional, for advanced analytics)
CREATE INDEX IF NOT EXISTS idx_bybit_events_payload
    ON bybit_order_events USING gin(raw_payload);


-- ============================================================================
-- TABLE 2: CoinDCX Order Events (Immutable Audit Log)
-- ============================================================================
-- Captures ALL CoinDCX WebSocket order events
-- APPEND-ONLY: Never UPDATE or DELETE from this table

CREATE TABLE IF NOT EXISTS coindcx_order_events (
    -- Primary key (auto-increment)
    id SERIAL PRIMARY KEY,

    -- ========================================================================
    -- Order Identification
    -- ========================================================================
    order_id VARCHAR(100) NOT NULL,
    pair VARCHAR(50) NOT NULL,                      -- B-BTC_USDT, B-ETH_USDT, B-SOL_USDT

    -- ========================================================================
    -- Event Classification
    -- ========================================================================
    event_type VARCHAR(20) NOT NULL,                -- open, filled, cancelled, partially_filled
    order_status VARCHAR(20),                       -- Raw from WebSocket: 'initial', 'open', 'filled', 'cancelled'

    -- ========================================================================
    -- Order Details
    -- ========================================================================
    side VARCHAR(10),                               -- buy, sell
    order_type VARCHAR(20),                         -- limit_order, market_order
    price NUMERIC(20, 8),                           -- Order price
    total_quantity NUMERIC(18, 8),                  -- Total order quantity
    remaining_quantity NUMERIC(18, 8),              -- Quantity remaining (for partial fills)

    -- ========================================================================
    -- Execution Details
    -- ========================================================================
    avg_price NUMERIC(20, 8),                       -- Average fill price
    fee_amount NUMERIC(18, 8),                      -- Fee charged (in quote currency)

    -- ========================================================================
    -- Timestamps
    -- ========================================================================
    event_received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,  -- Local timestamp when event received

    -- ========================================================================
    -- Raw WebSocket Payload (Complete Data Preservation)
    -- ========================================================================
    raw_payload JSONB,                              -- Complete WebSocket message

    -- ========================================================================
    -- Chunk Linking (Connect to Trade)
    -- ========================================================================
    chunk_group_id UUID,                            -- Links to orders.chunk_group_id
    chunk_sequence INTEGER,                         -- Chunk number (1, 2, 3...)
    chunk_total INTEGER,                            -- Total chunks in this trade

    -- ========================================================================
    -- Indexes for Fast Queries
    -- ========================================================================
    CONSTRAINT coindcx_order_events_order_id_idx UNIQUE (id)
);

-- Index for primary query: Get latest event for order_id
CREATE INDEX IF NOT EXISTS idx_coindcx_events_order_id
    ON coindcx_order_events(order_id, event_received_at DESC);

-- Index for chunk queries: Get all events for a trade
CREATE INDEX IF NOT EXISTS idx_coindcx_events_chunk_group
    ON coindcx_order_events(chunk_group_id, chunk_sequence);

-- Index for time-based queries: Get recent events
CREATE INDEX IF NOT EXISTS idx_coindcx_events_timestamp
    ON coindcx_order_events(event_received_at DESC);

-- Index for event type queries: Find all fills, rejections, etc.
CREATE INDEX IF NOT EXISTS idx_coindcx_events_type
    ON coindcx_order_events(event_type);

-- Index for JSONB payload queries (optional, for advanced analytics)
CREATE INDEX IF NOT EXISTS idx_coindcx_events_payload
    ON coindcx_order_events USING gin(raw_payload);


-- ============================================================================
-- Comments for Documentation
-- ============================================================================

COMMENT ON TABLE bybit_order_events IS
'Immutable audit log for all Bybit WebSocket order/execution events.
APPEND-ONLY: Never UPDATE or DELETE. Query latest event by order_id to get current status.
Eliminates race conditions and provides complete order lifecycle tracking.';

COMMENT ON COLUMN bybit_order_events.event_type IS
'Event classification: NEW (order placed), FILLED (order filled), PARTIAL_FILLED (partially filled),
CANCELLED (order cancelled), REJECTED (order rejected, e.g., post-only rejection)';

COMMENT ON COLUMN bybit_order_events.cum_exec_fee IS
'Cumulative fee charged by Bybit (THIS IS THE SHORTFALL).
Fee reconciliation reads this to calculate total shortage across all chunks.';

COMMENT ON COLUMN bybit_order_events.raw_payload IS
'Complete WebSocket message as JSONB for debugging and future analysis.
Enables post-trade analysis without relying on exchange API history.';

COMMENT ON TABLE coindcx_order_events IS
'Immutable audit log for all CoinDCX WebSocket order events.
APPEND-ONLY: Never UPDATE or DELETE. Query latest event by order_id to get current status.';


-- ============================================================================
-- Usage Examples (for documentation)
-- ============================================================================

-- Query 1: Get latest event for an order (PRIMARY USE CASE)
-- SELECT event_type, cum_exec_qty, cum_exec_fee, avg_price
-- FROM bybit_order_events
-- WHERE order_id = '2058081971938355712'
-- ORDER BY event_received_at DESC
-- LIMIT 1;

-- Query 2: Get complete order lifecycle
-- SELECT event_type, cum_exec_qty, cum_exec_fee, event_received_at
-- FROM bybit_order_events
-- WHERE order_id = '2058081971938355712'
-- ORDER BY event_received_at ASC;

-- Query 3: Check if order filled (used by bot to prevent double-fill)
-- SELECT event_type
-- FROM bybit_order_events
-- WHERE order_id = '2058081971938355712'
-- ORDER BY event_received_at DESC
-- LIMIT 1;
-- Result: event_type = 'FILLED' → Skip market order!

-- Query 4: Get all events for a trade (debugging)
-- SELECT order_id, event_type, cum_exec_qty, event_received_at
-- FROM bybit_order_events
-- WHERE chunk_group_id = '123e4567-e89b-12d3-a456-426614174000'
-- ORDER BY chunk_sequence, event_received_at;

-- Query 5: Detect double-fills (analytics)
-- SELECT order_id, COUNT(*) as fill_count
-- FROM bybit_order_events
-- WHERE chunk_group_id = '123e4567-e89b-12d3-a456-426614174000'
--   AND event_type = 'FILLED'
-- GROUP BY order_id
-- HAVING COUNT(*) > 1;

-- Query 6: Get total fees for reconciliation
-- SELECT SUM(cum_exec_fee) as total_fee
-- FROM bybit_order_events
-- WHERE chunk_group_id = '123e4567-e89b-12d3-a456-426614174000'
--   AND event_type = 'FILLED'
--   AND chunk_sequence <= (SELECT MAX(chunk_sequence) FROM bybit_order_events WHERE chunk_group_id = '123e4567-e89b-12d3-a456-426614174000');


-- ============================================================================
-- Migration Complete
-- ============================================================================
-- Tables created: bybit_order_events, coindcx_order_events
-- Status: Ready for OrderMonitor integration
-- Next step: Update OrderMonitor WebSocket handlers to INSERT into these tables
-- Rollback: Run 003_rollback_websocket_event_tables.sql
