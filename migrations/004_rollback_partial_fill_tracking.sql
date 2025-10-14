-- ============================================================================
-- Rollback: 004_rollback_partial_fill_tracking.sql
-- Description: Remove partial fill tracking columns from orders table
-- Date: 2025-10-11
-- Author: Trading Operations Team
-- ============================================================================

-- WARNING: This rollback will DELETE the following columns and their data:
--   • is_partial_fill_completion
--   • partial_order_id
--   • partial_filled_qty
--   • partial_avg_price
--   • partial_bybit_fee_crypto
--   • partial_coindcx_fee_usdt
--
-- IMPORTANT: If you have active trades with partial fills, you will lose
-- the partial fill tracking data. The event tables will still have the
-- complete history, but the orders table won't have the aggregated view.

-- ============================================================================
-- Drop Indexes
-- ============================================================================

DROP INDEX IF EXISTS idx_orders_partial_fill;
DROP INDEX IF EXISTS idx_orders_partial_order_id;

RAISE NOTICE '✅ Dropped partial fill indexes';

-- ============================================================================
-- Drop Columns
-- ============================================================================

ALTER TABLE orders DROP COLUMN IF EXISTS is_partial_fill_completion;
ALTER TABLE orders DROP COLUMN IF EXISTS partial_order_id;
ALTER TABLE orders DROP COLUMN IF EXISTS partial_filled_qty;
ALTER TABLE orders DROP COLUMN IF EXISTS partial_avg_price;
ALTER TABLE orders DROP COLUMN IF EXISTS partial_bybit_fee_crypto;
ALTER TABLE orders DROP COLUMN IF EXISTS partial_coindcx_fee_usdt;

RAISE NOTICE '✅ Dropped 6 partial fill tracking columns';

-- ============================================================================
-- Verification
-- ============================================================================

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

    IF col_count = 0 THEN
        RAISE NOTICE '✅ All partial fill tracking columns removed successfully';
    ELSE
        RAISE WARNING '⚠️  Expected 0 columns, found %', col_count;
    END IF;
END $$;

-- ============================================================================
-- Rollback Complete
-- ============================================================================

RAISE NOTICE '';
RAISE NOTICE '========================================';
RAISE NOTICE '  Rollback 004 Complete';
RAISE NOTICE '========================================';
RAISE NOTICE 'Removed partial fill tracking from orders table';
RAISE NOTICE '';
RAISE NOTICE 'NOTE: Event tables (bybit_order_events, coindcx_order_events)';
RAISE NOTICE 'still contain complete history of all orders including partials.';
RAISE NOTICE '========================================';
