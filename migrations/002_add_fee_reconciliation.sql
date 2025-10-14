-- Migration: Add Fee Reconciliation Table
-- Purpose: Track Bybit fee shortfall and reconciliation orders
-- Date: 2025-10-10

-- Table to track cumulative fee shortfall per trade (chunk_group_id)
CREATE TABLE IF NOT EXISTS fee_reconciliation (
    id SERIAL PRIMARY KEY,
    chunk_group_id UUID NOT NULL UNIQUE,
    symbol VARCHAR(20) NOT NULL,
    total_chunks INTEGER NOT NULL,
    completed_chunks INTEGER DEFAULT 0,

    -- Bybit fee tracking
    total_bybit_ordered NUMERIC(18, 8) DEFAULT 0,       -- Total cumExecQty across all chunks
    total_bybit_fee NUMERIC(18, 8) DEFAULT 0,           -- Total cumExecFee (shortfall)
    total_bybit_received NUMERIC(18, 8) DEFAULT 0,      -- Total net_received (cumExecQty - cumExecFee)

    -- Reconciliation order details
    reconciliation_needed BOOLEAN DEFAULT FALSE,        -- True if shortfall >= minOrderQty
    reconciliation_qty NUMERIC(18, 8),                  -- Rounded quantity for reconciliation order
    reconciliation_order_id VARCHAR(100),               -- Bybit order ID
    reconciliation_status VARCHAR(20) DEFAULT 'PENDING', -- PENDING, COMPLETED, SKIPPED_BELOW_MINIMUM
    reconciliation_fill_price NUMERIC(20, 8),           -- Market order fill price

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    reconciled_at TIMESTAMP WITH TIME ZONE,

    -- Metadata
    notes TEXT  -- Store additional info (e.g., "Residual $0.20 accepted as negligible")
);

-- Index for fast lookups by chunk_group_id
CREATE INDEX IF NOT EXISTS idx_fee_reconciliation_chunk_group
    ON fee_reconciliation(chunk_group_id);

-- Index for status queries
CREATE INDEX IF NOT EXISTS idx_fee_reconciliation_status
    ON fee_reconciliation(reconciliation_status);

-- Index for date-based analytics
CREATE INDEX IF NOT EXISTS idx_fee_reconciliation_created
    ON fee_reconciliation(created_at);

-- Comments for documentation
COMMENT ON TABLE fee_reconciliation IS 'Tracks Bybit maker fee shortfall and reconciliation orders per trade';
COMMENT ON COLUMN fee_reconciliation.total_bybit_fee IS 'Cumulative cumExecFee - this is what we need to buy back';
COMMENT ON COLUMN fee_reconciliation.reconciliation_qty IS 'Rounded to basePrecision and validated against minOrderQty';
COMMENT ON COLUMN fee_reconciliation.reconciliation_status IS 'PENDING=not started, COMPLETED=order filled, SKIPPED_BELOW_MINIMUM=too small to trade';
