"""
Bybit WebSocket Client for Real-time Last Traded Price (LTP) Fetching
"""

import json
import threading
import time
import websocket
from typing import Dict, Callable, Optional, Any
from dataclasses import dataclass
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChannelType(Enum):
    SPOT = "spot"
    LINEAR = "linear"
    INVERSE = "inverse"
    OPTION = "option"


@dataclass
class TickerData:
    symbol: str
    last_price: str
    bid_price: str = ""
    ask_price: str = ""
    high_24h: str = ""
    low_24h: str = ""
    volume_24h: str = ""
    price_change_24h: str = ""
    timestamp: int = 0


class BybitWebSocketClient:
    def __init__(self,
                 testnet: bool = False,
                 channel_type: ChannelType = ChannelType.SPOT,
                 ping_interval: int = 20):
        self.testnet = testnet
        self.channel_type = channel_type
        self.ping_interval = ping_interval
        self.subscriptions: Dict[str, Callable] = {}
        self.ws: Optional[websocket.WebSocketApp] = None
        self.is_connected = False
        self._ping_thread: Optional[threading.Thread] = None
        self._stop_ping = False

        if testnet:
            self.ws_url = f"wss://stream-testnet.bybit.com/v5/public/{channel_type.value}"
        else:
            self.ws_url = f"wss://stream.bybit.com/v5/public/{channel_type.value}"

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)

            if data.get('op') == 'pong':
                logger.debug("Received pong")
                return

            if data.get('op') == 'subscribe':
                if data.get('success'):
                    logger.info(f"Subscribed: {data.get('args', [])}")
                return

            if data.get('topic', '').startswith('tickers.'):
                self._handle_ticker_message(data)

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def _handle_ticker_message(self, data: Dict[str, Any]):
        try:
            topic = data.get('topic', '')
            symbol = topic.replace('tickers.', '')

            if symbol in self.subscriptions:
                ticker_info = data.get('data', {})

                ticker_data = TickerData(
                    symbol=ticker_info.get('symbol', symbol),
                    last_price=ticker_info.get('lastPrice', '0'),
                    bid_price=ticker_info.get('bid1Price', ticker_info.get('bidPrice', '')),
                    ask_price=ticker_info.get('ask1Price', ticker_info.get('askPrice', '')),
                    high_24h=ticker_info.get('highPrice24h', ''),
                    low_24h=ticker_info.get('lowPrice24h', ''),
                    volume_24h=ticker_info.get('volume24h', ''),
                    price_change_24h=ticker_info.get('price24hPcnt', ''),
                    timestamp=data.get('ts', int(time.time() * 1000))
                )

                callback = self.subscriptions[symbol]
                callback(ticker_data)

        except Exception as e:
            logger.error(f"Error handling ticker: {e}")

    def _on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info(f"Connection closed")
        self.is_connected = False
        self._stop_ping = True

    def _on_open(self, ws):
        logger.info("Connection opened")
        self.is_connected = True

        if not self._ping_thread or not self._ping_thread.is_alive():
            self._stop_ping = False
            self._ping_thread = threading.Thread(target=self._ping_loop)
            self._ping_thread.daemon = True
            self._ping_thread.start()

        for symbol in self.subscriptions.keys():
            self._send_subscription(symbol)

    def _ping_loop(self):
        while not self._stop_ping and self.is_connected:
            try:
                if self.ws and self.is_connected:
                    ping_msg = json.dumps({
                        "req_id": f"ping_{int(time.time())}",
                        "op": "ping"
                    })
                    self.ws.send(ping_msg)
                time.sleep(self.ping_interval)
            except:
                break

    def _send_subscription(self, symbol: str):
        if not self.is_connected:
            return

        subscription_msg = json.dumps({
            "op": "subscribe",
            "args": [f"tickers.{symbol}"]
        })

        try:
            self.ws.send(subscription_msg)
            logger.info(f"Subscribed to {symbol}")
        except Exception as e:
            logger.error(f"Failed to subscribe: {e}")

    def subscribe_ticker(self, symbol: str, callback: Callable[[TickerData], None]):
        symbol = symbol.upper()
        self.subscriptions[symbol] = callback

        if self.is_connected:
            self._send_subscription(symbol)

    def start(self):
        if self.is_connected:
            return

        logger.info(f"Connecting to {self.ws_url}")

        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )

        self.ws.run_forever()

    def start_async(self):
        if self.is_connected:
            return

        def run_websocket():
            self.start()

        ws_thread = threading.Thread(target=run_websocket)
        ws_thread.daemon = True
        ws_thread.start()
        # No delay - return immediately for instant startup
        return ws_thread

    def stop(self):
        logger.info("Stopping WebSocket connection")
        self._stop_ping = True
        self.is_connected = False

        if self.ws:
            self.ws.close()