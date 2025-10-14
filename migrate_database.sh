#!/bin/bash
# Database Migration Script
# Recreates database with updated schema

set -e  # Exit on error

echo "🔄 Database Migration Script"
echo "=============================="
echo ""

# Database connection details
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_NAME="${POSTGRES_DB:-hedge_bot}"
DB_USER="${POSTGRES_USER:-hedge_user}"

echo "Database: $DB_NAME"
echo "User: $DB_USER"
echo "Host: $DB_HOST:$DB_PORT"
echo ""

# Warning
echo "⚠️  WARNING: This will DROP all existing tables!"
echo "   All data will be lost."
echo ""
read -p "Continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Migration cancelled."
    exit 0
fi

echo ""
echo "🗑️  Dropping existing tables..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME << SQL
DROP TABLE IF EXISTS spread_history CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS prices CASCADE;
SQL

echo "✅ Tables dropped"
echo ""

echo "📝 Creating new schema..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f postgresql_schema.sql

echo ""
echo "✅ Migration complete!"
echo ""
echo "Verify with:"
echo "  psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c '\\dt'"
echo "  psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c '\\d orders'"
