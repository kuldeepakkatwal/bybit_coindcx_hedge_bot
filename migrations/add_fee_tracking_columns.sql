-- Migration: Add Fee Tracking Columns to Orders Table
-- Date: 2025-10-08
-- Purpose: Track actual fees from WebSocket for post-trade reconciliation

-- Add columns to track execution fees
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cumExecFee NUMERIC(12,10);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cumExecQty NUMERIC(10,8);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS net_received NUMERIC(10,8);

-- Add helpful comments
COMMENT ON COLUMN orders.cumExecFee IS 'Actual fee charged by exchange (from WebSocket cumExecFee)';
COMMENT ON COLUMN orders.cumExecQty IS 'Gross quantity filled (from WebSocket cumExecQty)';
COMMENT ON COLUMN orders.net_received IS 'Net quantity received after fees (cumExecQty - cumExecFee)';

-- Verify columns were added
\d orders
