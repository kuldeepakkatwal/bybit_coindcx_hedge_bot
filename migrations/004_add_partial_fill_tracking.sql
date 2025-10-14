-- ============================================================================
-- Migration: 004_add_partial_fill_tracking.sql
-- Description: Add columns to track partial fill details for accurate
--              fee reconciliation and audit trail
-- Date: 2025-10-11
-- Author: Trading Operations Team
-- ============================================================================

-- Purpose:
-- When an order partially fills, we cancel it and place a market order for
-- the remainder. This creates 2 orders for the same chunk, but the orders
-- table's UNIQUE constraint allows only 1 row per (chunk_group_id,
-- chunk_sequence, exchange). The UPSERT replaces the partial order with the
-- completion order.
--
-- Problem: We lose the partial order's fee data, which is CRITICAL for Bybit
-- reconciliation (fees deducted from crypto quantity).
--
-- Solution: Add columns to store partial order details so we can calculate:
--   total_fees = partial_filled_fee + current_order_fee
--
-- This is mission-critical for Bybit where fees affect reconciliation, and
-- useful for CoinDCX for P&L analysis (fees in USDT).

-- ============================================================================
-- Add Partial Fill Tracking Columns
-- ============================================================================

ALTER TABLE orders ADD COLUMN IF NOT EXISTS
    is_partial_fill_completion BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN orders.is_partial_fill_completion IS
'TRUE if this order was placed to complete a partially filled order.
When TRUE, partial_* columns contain details of the original partial fill.';

-- ----------------------------------------------------------------------------
-- Common Fields (Both Exchanges)
-- ----------------------------------------------------------------------------

ALTER TABLE orders ADD COLUMN IF NOT EXISTS
    partial_order_id VARCHAR(100);

COMMENT ON COLUMN orders.partial_order_id IS
'Order ID of the original order that was partially filled (if applicable).
References the cancelled partial order before this completion order was placed.';

ALTER TABLE orders ADD COLUMN IF NOT EXISTS
    partial_filled_qty NUMERIC(18, 8);

COMMENT ON COLUMN orders.partial_filled_qty IS
'Quantity that was filled by the original partial order (gross amount).
For Bybit: Before fee deduction. For CoinDCX: Full amount (fees in USDT).
Example: Original order 0.006 ETH fills 0.003 ETH → stores 0.003';

ALTER TABLE orders ADD COLUMN IF NOT EXISTS
    partial_avg_price NUMERIC(10, 2);

COMMENT ON COLUMN orders.partial_avg_price IS
'Average fill price of the partial order (USD).
Used for P&L analysis and reconciliation calculations.';

-- ----------------------------------------------------------------------------
-- Bybit-Specific Fields (CRITICAL for reconciliation)
-- ----------------------------------------------------------------------------

ALTER TABLE orders ADD COLUMN IF NOT EXISTS
    partial_bybit_fee_crypto NUMERIC(18, 8);

COMMENT ON COLUMN orders.partial_bybit_fee_crypto IS
'Fee charged on the partial fill (in crypto: ETH/BTC). Bybit ONLY.
CRITICAL: Bybit deducts maker fees from received quantity, so we MUST
track partial fill fees for accurate reconciliation.

Example: Partial order fills 0.003 ETH with 0.00000195 ETH fee
→ stores 0.00000195

Total reconciliation = partial_bybit_fee_crypto + current_order_cumexecfee';

-- ----------------------------------------------------------------------------
-- CoinDCX-Specific Fields (For P&L analysis)
-- ----------------------------------------------------------------------------

ALTER TABLE orders ADD COLUMN IF NOT EXISTS
    partial_coindcx_fee_usdt NUMERIC(18, 8);

COMMENT ON COLUMN orders.partial_coindcx_fee_usdt IS
'Fee charged on the partial fill (in USDT). CoinDCX ONLY.
CoinDCX futures fees are charged in USDT, not deducted from crypto quantity.
This is less critical than Bybit fees but useful for accurate P&L tracking.

Example: Partial order fills 0.003 ETH with $0.65 USDT fee
→ stores 0.65

Total P&L cost = partial_coindcx_fee_usdt + current_order_fee_usdt';

-- ============================================================================
-- Add Indexes for Performance
-- ============================================================================

-- Index for querying partial fills (useful for analysis)
CREATE INDEX IF NOT EXISTS idx_orders_partial_fill
ON orders(is_partial_fill_completion)
WHERE is_partial_fill_completion = TRUE;

COMMENT ON INDEX idx_orders_partial_fill IS
'Partial index for fast queries of chunks that involved partial fills.
Used for performance analysis and debugging.';

-- Index for looking up original partial orders
CREATE INDEX IF NOT EXISTS idx_orders_partial_order_id
ON orders(partial_order_id)
WHERE partial_order_id IS NOT NULL;

COMMENT ON INDEX idx_orders_partial_order_id IS
'Index for fast lookup of completion orders by their original partial order ID.
Useful for tracing the relationship between partial and completion orders.';

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Verify columns were added
DO $$
DECLARE
    col_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO col_count
    FROM information_schema.columns
    WHERE table_name = 'orders'
      AND column_name IN (
          'is_partial_fill_completion',
          'partial_order_id',
          'partial_filled_qty',
          'partial_avg_price',
          'partial_bybit_fee_crypto',
          'partial_coindcx_fee_usdt'
      );

    IF col_count = 6 THEN
        RAISE NOTICE '✅ All 6 partial fill tracking columns added successfully';
    ELSE
        RAISE WARNING '⚠️  Expected 6 columns, found %', col_count;
    END IF;
END $$;

-- Verify indexes were created
DO $$
DECLARE
    idx_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO idx_count
    FROM pg_indexes
    WHERE tablename = 'orders'
      AND indexname IN (
          'idx_orders_partial_fill',
          'idx_orders_partial_order_id'
      );

    IF idx_count = 2 THEN
        RAISE NOTICE '✅ All 2 indexes created successfully';
    ELSE
        RAISE WARNING '⚠️  Expected 2 indexes, found %', idx_count;
    END IF;
END $$;

-- ============================================================================
-- Usage Examples
-- ============================================================================

-- Example 1: Query chunks with partial fills
/*
SELECT
    chunk_group_id,
    chunk_sequence,
    exchange,
    order_id AS completion_order_id,
    partial_order_id,
    partial_filled_qty,
    quantity AS completion_qty,
    partial_bybit_fee_crypto,
    cumexecfee AS completion_fee,
    (COALESCE(partial_bybit_fee_crypto, 0) + COALESCE(cumexecfee, 0)) AS total_fees
FROM orders
WHERE is_partial_fill_completion = TRUE
  AND exchange = 'bybit'
ORDER BY placed_at DESC
LIMIT 10;
*/

-- Example 2: Calculate reconciliation for a chunk with partial fill (Bybit)
/*
WITH chunk_fees AS (
    SELECT
        chunk_group_id,
        chunk_sequence,
        CASE
            WHEN is_partial_fill_completion THEN
                -- Sum partial + completion fees
                COALESCE(partial_bybit_fee_crypto, 0) + COALESCE(cumexecfee, 0)
            ELSE
                -- Single order fee
                COALESCE(cumexecfee, 0)
        END AS total_chunk_fee
    FROM orders
    WHERE exchange = 'bybit'
      AND chunk_group_id = 'your-uuid-here'
)
SELECT
    chunk_group_id,
    SUM(total_chunk_fee) AS total_bybit_fees_for_reconciliation
FROM chunk_fees
GROUP BY chunk_group_id;
*/

-- Example 3: Analyze partial fill rate per exchange
/*
SELECT
    exchange,
    COUNT(*) AS total_chunks,
    SUM(CASE WHEN is_partial_fill_completion THEN 1 ELSE 0 END) AS partial_fill_chunks,
    ROUND(
        100.0 * SUM(CASE WHEN is_partial_fill_completion THEN 1 ELSE 0 END) / COUNT(*),
        2
    ) AS partial_fill_rate_percent
FROM orders
WHERE placed_at >= NOW() - INTERVAL '7 days'
GROUP BY exchange;
*/

-- ============================================================================
-- Migration Complete
-- ============================================================================

RAISE NOTICE '';
RAISE NOTICE '========================================';
RAISE NOTICE '  Migration 004 Complete';
RAISE NOTICE '========================================';
RAISE NOTICE 'Added partial fill tracking to orders table:';
RAISE NOTICE '  • is_partial_fill_completion (flag)';
RAISE NOTICE '  • partial_order_id (reference)';
RAISE NOTICE '  • partial_filled_qty (quantity)';
RAISE NOTICE '  • partial_avg_price (price)';
RAISE NOTICE '  • partial_bybit_fee_crypto (Bybit fees in ETH/BTC)';
RAISE NOTICE '  • partial_coindcx_fee_usdt (CoinDCX fees in USDT)';
RAISE NOTICE '';
RAISE NOTICE 'Next steps:';
RAISE NOTICE '  1. Update db.py upsert_order() to accept partial details';
RAISE NOTICE '  2. Create handle_partial_fill() in order_manager.py';
RAISE NOTICE '  3. Update fee_reconciliation.py to sum partial fees';
RAISE NOTICE '========================================';
