-- PostgreSQL Database Schema for Hedge Bot
-- Updated to match bot code requirements

-- Enable UUID extension for generating unique identifiers
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Table 1: Prices for order placement
CREATE TABLE IF NOT EXISTS prices (
    id SERIAL PRIMARY KEY,
    bybit_price NUMERIC(10,2) NOT NULL,
    coindcx_price NUMERIC(10,2) NOT NULL,
    spread_percent NUMERIC(5,4),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Table 2: Order details with chunk tracking
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(10) NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    symbol VARCHAR(20),                              -- ADDED: Track coin (BTC/ETH/SOL)
    side VARCHAR(10) NOT NULL,                       -- BUY/SELL
    price NUMERIC(10,2) NOT NULL,
    quantity NUMERIC(10,8) NOT NULL,
    status VARCHAR(20) DEFAULT 'PLACED',             -- PLACED/FILLED/CANCELLED
    order_type VARCHAR(20) DEFAULT 'limit',          -- ADDED: limit/market
    fill_price NUMERIC(10,2),
    placed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,  -- ADDED: Bot expects this
    filled_at TIMESTAMP WITH TIME ZONE,
    modified_price NUMERIC(10,2),
    modified_quantity NUMERIC(10,8),
    modified_at TIMESTAMP WITH TIME ZONE,
    is_modified BOOLEAN DEFAULT FALSE,
    chunk_group_id UUID,
    chunk_sequence INTEGER,
    chunk_total INTEGER
);

-- Table 3: Spread history for analytics
CREATE TABLE IF NOT EXISTS spread_history (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    bybit_price NUMERIC(20,8) NOT NULL,
    coindcx_price NUMERIC(20,8) NOT NULL,
    spread_percent NUMERIC(10,4) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_chunk_group ON orders(chunk_group_id);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);

CREATE INDEX IF NOT EXISTS idx_spread_history_symbol ON spread_history(symbol);
CREATE INDEX IF NOT EXISTS idx_spread_history_timestamp ON spread_history(timestamp);

-- Insert initial test prices
INSERT INTO prices (bybit_price, coindcx_price) VALUES (3200.00, 3205.00);
