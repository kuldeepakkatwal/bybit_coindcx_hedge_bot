#!/usr/bin/env python3
"""
WebSocket Order Logger
Logs complete WebSocket responses with chunk context for post-trade analysis.
NO API CALLS - Pure WebSocket data only.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class WebSocketOrderLogger:
    """
    Logs complete WebSocket messages with chunk context.

    Features:
    - Complete WebSocket data preservation (no filtering)
    - Chunk context tracking (group_id, sequence, phase)
    - Human-readable section headers
    - JSON format for easy parsing
    - Timestamped log files
    """

    def __init__(self, log_dir: str = 'logs'):
        """
        Initialize logger with timestamped log file.

        Args:
            log_dir: Directory for log files (default: 'logs')
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)

        # Create timestamped log file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_file = self.log_dir / f'orders_websocket_{timestamp}.log'

        # Initialize log file with header
        with open(self.log_file, 'w') as f:
            f.write(f"""{'='*80}
WebSocket Order Log
Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Purpose: Complete order lifecycle tracking via WebSocket updates
Format: JSON lines with section headers
{'='*80}

""")

        print(f"✅ WebSocket Order Logger initialized: {self.log_file}")

    def _write_header(self, text: str, level: int = 1):
        """
        Write section header to log file.

        Args:
            text: Header text
            level: Header level (1=major, 2=minor)
        """
        if level == 1:
            separator = '=' * 80
        else:
            separator = '-' * 80

        with open(self.log_file, 'a') as f:
            f.write(f"\n{separator}\n")
            f.write(f"{text}\n")
            f.write(f"{separator}\n\n")

    def _write_json(self, data: dict):
        """
        Write JSON object to log file (pretty-printed).

        Args:
            data: Dictionary to write as JSON
        """
        with open(self.log_file, 'a') as f:
            json.dump(data, f, indent=2, default=str)
            f.write('\n\n')

    def _write_line(self, text: str):
        """
        Write plain text line to log file.

        Args:
            text: Text to write
        """
        with open(self.log_file, 'a') as f:
            f.write(f"{text}\n")

    def log_trade_start(
        self,
        symbol: str,
        quantity: float,
        num_chunks: int,
        chunk_group_id: str
    ):
        """
        Log trade start header.

        Args:
            symbol: Cryptocurrency symbol (ETH, BTC, etc.)
            quantity: Total quantity to trade
            num_chunks: Number of chunks
            chunk_group_id: UUID for this trade
        """
        header = f"TRADE START | Symbol: {symbol} | Quantity: {quantity} {symbol} | Chunks: {num_chunks}"
        self._write_header(header, level=1)

        self._write_line(f"Group ID: {chunk_group_id}")
        self._write_line(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._write_line("")

    def log_chunk_start(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        chunk_total: int,
        quantity: float = None,
        symbol: str = None
    ):
        """
        Log chunk start header.

        Args:
            chunk_group_id: UUID for this trade
            chunk_sequence: Chunk number (1-based)
            chunk_total: Total number of chunks
            quantity: Chunk quantity (optional)
            symbol: Cryptocurrency symbol (optional)
        """
        qty_str = f" | Qty: {quantity} {symbol}" if quantity and symbol else ""
        header = f"CHUNK {chunk_sequence}/{chunk_total}{qty_str}"
        self._write_header(header, level=1)

        self._write_line(f"Group: {chunk_group_id}")
        self._write_line(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        self._write_line("")

    def log_websocket_event(
        self,
        exchange: str,
        websocket_message: dict,
        chunk_context: dict,
        event_summary: str,
        order_id: str = None,
        status_change: dict = None
    ):
        """
        Log complete WebSocket message with context.

        This is the core logging method that captures EVERYTHING from WebSocket.

        Args:
            exchange: 'bybit' or 'coindcx'
            websocket_message: COMPLETE raw WebSocket message (no filtering)
            chunk_context: Dict with:
                - chunk_group_id: UUID
                - chunk_sequence: int
                - chunk_total: int
                - chunk_phase: str (BOTH_ORDERS_OPEN, NAKED_BYBIT, etc.)
                - symbol: str (ETH, BTC, etc.)
            event_summary: Human-readable one-liner for quick scanning
            order_id: Order ID (optional, extracted from message if not provided)
            status_change: Dict with 'from' and 'to' status (optional)
        """
        # Determine event type from WebSocket data
        event_type = self._determine_event_type(exchange, websocket_message)

        # Build log entry
        log_entry = {
            'log_timestamp': datetime.now().isoformat(),
            'log_event_type': event_type,
            'event_summary': event_summary,
            'chunk_context': chunk_context,
            'exchange': exchange.lower(),
            'order_id': order_id,
            'websocket_message_complete': websocket_message
        }

        # Add status change if provided
        if status_change:
            log_entry['status_change'] = status_change

        # Write to log file
        self._write_json(log_entry)

    def _determine_event_type(self, exchange: str, message: dict) -> str:
        """
        Determine event type from WebSocket message.

        Args:
            exchange: 'bybit' or 'coindcx'
            message: WebSocket message

        Returns:
            Event type string (e.g., 'BYBIT_ORDER_FILLED')
        """
        exchange_upper = exchange.upper()

        if exchange == 'bybit':
            # Extract status from Bybit message
            data = message.get('data', [])
            if data and len(data) > 0:
                status = data[0].get('orderStatus', 'Unknown')

                if status == 'New':
                    return f'{exchange_upper}_ORDER_NEW'
                elif status == 'Filled':
                    return f'{exchange_upper}_ORDER_FILLED'
                elif status == 'PartiallyFilled':
                    return f'{exchange_upper}_ORDER_PARTIALLY_FILLED'
                elif status == 'Cancelled':
                    return f'{exchange_upper}_ORDER_CANCELLED'
                elif status == 'Rejected':
                    return f'{exchange_upper}_ORDER_REJECTED'
                else:
                    return f'{exchange_upper}_ORDER_UPDATE'

        elif exchange == 'coindcx':
            # Extract status from CoinDCX message
            # CoinDCX sends data as JSON string
            data_str = message.get('data', '[]')
            try:
                data = json.loads(data_str) if isinstance(data_str, str) else [data_str]
                if data and len(data) > 0:
                    status = data[0].get('status', 'unknown').lower()

                    if status in ['initial', 'open']:
                        return f'{exchange_upper}_ORDER_OPEN'
                    elif status == 'filled':
                        return f'{exchange_upper}_ORDER_FILLED'
                    elif status == 'cancelled':
                        return f'{exchange_upper}_ORDER_CANCELLED'
                    elif status == 'partially_filled':
                        return f'{exchange_upper}_ORDER_PARTIALLY_FILLED'
                    else:
                        return f'{exchange_upper}_ORDER_UPDATE'
            except:
                pass

        return f'{exchange_upper}_WEBSOCKET_UPDATE'

    def log_phase_change(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        phase_description: str
    ):
        """
        Log phase transition (e.g., entering naked position).

        Args:
            chunk_group_id: UUID for this trade
            chunk_sequence: Chunk number
            phase_description: Description of phase change
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        text = f"\n>>> {timestamp} | CHUNK {chunk_sequence} | {phase_description} <<<\n"
        self._write_line(text)

    def log_chunk_complete(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        chunk_total: int,
        duration: float,
        summary: str
    ):
        """
        Log chunk completion.

        Args:
            chunk_group_id: UUID for this trade
            chunk_sequence: Chunk number
            chunk_total: Total chunks
            duration: Chunk duration in seconds
            summary: Summary text (e.g., "Both filled", "Market fallback used")
        """
        header = f"CHUNK {chunk_sequence}/{chunk_total} COMPLETE | Duration: {duration:.1f}s | {summary}"
        self._write_header(header, level=1)

    def log_trade_complete(
        self,
        chunk_group_id: str,
        total_duration: float,
        summary: str = "All chunks filled successfully"
    ):
        """
        Log trade completion.

        Args:
            chunk_group_id: UUID for this trade
            total_duration: Total trade duration in seconds
            summary: Trade summary
        """
        header = f"TRADE COMPLETE | Total Duration: {total_duration:.1f}s | {summary}"
        self._write_header(header, level=1)

        self._write_line(f"Group ID: {chunk_group_id}")
        self._write_line(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._write_line("")

    def log_error(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        error_type: str,
        error_message: str,
        details: dict = None
    ):
        """
        Log error during trade execution.

        Args:
            chunk_group_id: UUID for this trade
            chunk_sequence: Chunk number
            error_type: Error type (e.g., 'SPREAD_VIOLATION', 'ORDER_PLACEMENT_FAILED')
            error_message: Human-readable error message
            details: Additional error details (optional)
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

        error_entry = {
            'log_timestamp': timestamp,
            'log_event_type': 'ERROR',
            'error_type': error_type,
            'error_message': error_message,
            'chunk_context': {
                'chunk_group_id': chunk_group_id,
                'chunk_sequence': chunk_sequence
            }
        }

        if details:
            error_entry['error_details'] = details

        self._write_line(f"\n❌ ERROR: {error_message}\n")
        self._write_json(error_entry)

    def log_market_fallback(
        self,
        chunk_group_id: str,
        chunk_sequence: int,
        exchange: str,
        reason: str
    ):
        """
        Log market order fallback event.

        Args:
            chunk_group_id: UUID for this trade
            chunk_sequence: Chunk number
            exchange: Exchange where market fallback occurred
            reason: Reason for fallback
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        text = f"\n🚨 {timestamp} | CHUNK {chunk_sequence} | MARKET ORDER FALLBACK ({exchange.upper()}) | Reason: {reason} 🚨\n"
        self._write_line(text)