-- Rollback Script for Migration 003
-- Purpose: Remove WebSocket event tables if needed
-- Date: 2025-10-10
--
-- USAGE: Run this script if you need to revert Migration 003
-- psql -U hedge_user -d hedge_bot -f 003_rollback_websocket_event_tables.sql

-- ============================================================================
-- WARNING: This will delete ALL WebSocket event data!
-- ============================================================================
-- Make sure you have a database backup before running this script.

-- Drop indexes first (faster drops)
DROP INDEX IF EXISTS idx_bybit_events_order_id;
DROP INDEX IF EXISTS idx_bybit_events_chunk_group;
DROP INDEX IF EXISTS idx_bybit_events_timestamp;
DROP INDEX IF EXISTS idx_bybit_events_type;
DROP INDEX IF EXISTS idx_bybit_events_payload;

DROP INDEX IF EXISTS idx_coindcx_events_order_id;
DROP INDEX IF EXISTS idx_coindcx_events_chunk_group;
DROP INDEX IF EXISTS idx_coindcx_events_timestamp;
DROP INDEX IF EXISTS idx_coindcx_events_type;
DROP INDEX IF EXISTS idx_coindcx_events_payload;

-- Drop tables
DROP TABLE IF EXISTS bybit_order_events CASCADE;
DROP TABLE IF EXISTS coindcx_order_events CASCADE;

-- Confirmation message
DO $$
BEGIN
    RAISE NOTICE 'Migration 003 rolled back successfully';
    RAISE NOTICE 'Tables dropped: bybit_order_events, coindcx_order_events';
    RAISE NOTICE 'The "orders" table remains unchanged (backward compatible)';
END $$;
