"""
Bybit Spot Trading Client

A focused client for Bybit spot trading operations with WebSocket support.
Provides direct access to spot trading endpoints without unnecessary abstractions.
No database dependencies, no derivatives/futures functionality.
"""

import logging
from typing import Dict, Any, Optional, Callable
from pybit.unified_trading import HTTP, WebSocket


class BybitSpotClient:
    """
    A unified client for Bybit spot trading with real-time WebSocket support.
    
    Features:
    - Place and cancel spot orders
    - Check balances and order status
    - Real-time order and execution updates
    - WebSocket subscriptions for live data
    - No leverage, no derivatives - pure spot trading
    """
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        """
        Initialize the Unified Spot Trading Client.
        
        Args:
            api_key: Your Bybit API key
            api_secret: Your Bybit API secret
            testnet: Whether to use testnet (default True for safety)
        """
        self.logger = logging.getLogger(__name__)
        self.testnet = testnet
        self.category = "spot"  # Fixed to spot trading only
        
        # Initialize Bybit HTTP Client for REST API calls
        self.session = HTTP(
            testnet=self.testnet,
            api_key=api_key,
            api_secret=api_secret
        )
        
        # Initialize WebSocket for real-time updates
        self.ws = WebSocket(
            testnet=self.testnet,
            channel_type="private",
            api_key=api_key,
            api_secret=api_secret
        )
        
        self.logger.info(f"Unified Spot Client initialized ({'testnet' if testnet else 'mainnet'})")
    
    # ============= Trading Methods (REST API) =============
    
    def place_spot_order(self, 
                        symbol: str,
                        side: str,
                        order_type: str,
                        qty: str,
                        price: Optional[str] = None,
                        **kwargs) -> Dict[str, Any]:
        """
        Place a spot order on Bybit.
        
        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            side: Buy or Sell
            order_type: Limit or Market
            qty: Order quantity (in base currency)
            price: Order price (required for limit orders)
            **kwargs: Additional order parameters
            
        Returns:
            Response from Bybit API including order_id if successful
        """
        try:
            order_params = {
                'category': self.category,
                'symbol': symbol,
                'side': side,
                'orderType': order_type,
                'qty': qty
            }
            
            if order_type.upper() == "LIMIT" and price:
                order_params['price'] = price
            elif order_type.upper() == "LIMIT" and not price:
                self.logger.error("Price is required for limit orders")
                return {
                    'success': False,
                    'error': 'Price is required for limit orders'
                }
            
            # Add any additional parameters
            order_params.update(kwargs)
            
            response = self.session.place_order(**order_params)
            
            if response.get('retCode') == 0:
                order_id = response['result']['orderId']
                return {
                    'success': True,
                    'order_id': order_id,
                    'response': response
                }
            else:
                self.logger.error(f"Failed to place spot order. Response: {response}")
                return {
                    'success': False,
                    'error': response.get('retMsg', 'Unknown error'),
                    'response': response
                }
                
        except Exception as e:
            self.logger.error(f"Exception placing spot order: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def cancel_spot_order(self, 
                         symbol: str,
                         order_id: str) -> Dict[str, Any]:
        """
        Cancel an open spot order.
        
        Args:
            symbol: Trading pair symbol
            order_id: The order ID to cancel
            
        Returns:
            Response from Bybit API
        """
        try:
            response = self.session.cancel_order(
                category=self.category,
                symbol=symbol,
                orderId=order_id
            )
            
            if response.get('retCode') == 0:
                return {
                    'success': True,
                    'order_id': order_id,
                    'response': response
                }
            else:
                self.logger.error(f"Failed to cancel spot order. Response: {response}")
                return {
                    'success': False,
                    'error': response.get('retMsg', 'Unknown error'),
                    'response': response
                }
                
        except Exception as e:
            self.logger.error(f"Exception cancelling spot order: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_open_orders(self, 
                       symbol: Optional[str] = None,
                       order_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get open spot orders.
        
        Args:
            symbol: Symbol to filter orders (optional)
            order_id: Specific order ID to query (optional)
            
        Returns:
            Order information from Bybit
        """
        try:
            params = {'category': self.category}
            
            if order_id:
                params['orderId'] = order_id
            if symbol:
                params['symbol'] = symbol
                
            response = self.session.get_open_orders(**params)
            
            if response.get('retCode') == 0:
                orders = response['result']['list']
                return {
                    'success': True,
                    'orders': orders,
                    'response': response
                }
            else:
                self.logger.error(f"Failed to get open orders. Response: {response}")
                return {
                    'success': False,
                    'error': response.get('retMsg', 'Unknown error'),
                    'response': response
                }
                
        except Exception as e:
            self.logger.error(f"Exception getting open orders: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_order_history(self, 
                         symbol: Optional[str] = None,
                         limit: int = 50) -> Dict[str, Any]:
        """
        Get spot order history.
        
        Args:
            symbol: Symbol to filter orders (optional)
            limit: Number of orders to retrieve (default 50)
            
        Returns:
            Historical order information from Bybit
        """
        try:
            params = {
                'category': self.category,
                'limit': limit
            }
            
            if symbol:
                params['symbol'] = symbol
                
            response = self.session.get_order_history(**params)
            
            if response.get('retCode') == 0:
                orders = response['result']['list']
                return {
                    'success': True,
                    'orders': orders,
                    'response': response
                }
            else:
                self.logger.error(f"Failed to get order history. Response: {response}")
                return {
                    'success': False,
                    'error': response.get('retMsg', 'Unknown error'),
                    'response': response
                }
                
        except Exception as e:
            self.logger.error(f"Exception getting order history: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_spot_balance(self, coin: Optional[str] = None, account_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get spot wallet balance.
        
        Args:
            coin: Specific coin to check balance (optional, e.g., 'BTC', 'USDT')
            account_type: Account type to check (optional, e.g., 'UNIFIED', 'FUND', 'SPOT', 'CONTRACT')
                         If not specified, defaults to 'UNIFIED' for testnet, 'SPOT' for mainnet
            
        Returns:
            Spot wallet balance information
        """
        try:
            # Allow user to specify account type, or use defaults
            if account_type:
                params = {'accountType': account_type}
            else:
                # Default behavior: UNIFIED for testnet, SPOT for mainnet
                params = {'accountType': 'UNIFIED' if self.testnet else 'SPOT'}
            
            if coin:
                params['coin'] = coin
                
            response = self.session.get_wallet_balance(**params)
            
            if response.get('retCode') == 0:
                balance_data = response['result']['list'][0] if response['result']['list'] else {}
                
                # Extract coin balances
                coins = balance_data.get('coin', [])
                return {
                    'success': True,
                    'coins': coins,
                    'total_equity': balance_data.get('totalEquity', '0'),
                    'response': response
                }
            else:
                self.logger.error(f"Failed to get spot balance. Response: {response}")
                return {
                    'success': False,
                    'error': response.get('retMsg', 'Unknown error'),
                    'response': response
                }
                
        except Exception as e:
            self.logger.error(f"Exception getting spot balance: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get current ticker/price information for a symbol.
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            
        Returns:
            Ticker information including current price
        """
        try:
            response = self.session.get_tickers(
                category=self.category,
                symbol=symbol
            )
            
            if response.get('retCode') == 0:
                tickers = response['result']['list']
                if tickers:
                    ticker = tickers[0]
                    return {
                        'success': True,
                        'symbol': symbol,
                        'last_price': ticker.get('lastPrice'),
                        'bid': ticker.get('bid1Price'),
                        'ask': ticker.get('ask1Price'),
                        'volume_24h': ticker.get('volume24h'),
                        'response': response
                    }
                else:
                    return {
                        'success': False,
                        'error': f'No ticker data for {symbol}'
                    }
            else:
                self.logger.error(f"Failed to get ticker. Response: {response}")
                return {
                    'success': False,
                    'error': response.get('retMsg', 'Unknown error'),
                    'response': response
                }
                
        except Exception as e:
            self.logger.error(f"Exception getting ticker: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    # ============= WebSocket Methods (Real-time Updates) =============
    
    def subscribe_orders(self, callback: Callable[[Dict], None]) -> bool:
        """
        Subscribe to real-time spot order updates.
        
        Args:
            callback: Function to call when order update is received
            
        Returns:
            True if subscription successful
        """
        try:
            # Subscribe to order topic with direct callback
            self.ws.subscribe(topic="order", callback=callback)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to subscribe to orders: {e}")
            return False
    
    def subscribe_executions(self, callback: Callable[[Dict], None]) -> bool:
        """
        Subscribe to real-time execution (trade fill) updates.
        
        Args:
            callback: Function to call when execution update is received
            
        Returns:
            True if subscription successful
        """
        try:
            # Subscribe to execution topic with direct callback
            self.ws.subscribe(topic="execution", callback=callback)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to subscribe to executions: {e}")
            return False
    
    def subscribe_wallet(self, callback: Callable[[Dict], None]) -> bool:
        """
        Subscribe to real-time wallet/balance updates.
        
        Args:
            callback: Function to call when wallet update is received
            
        Returns:
            True if subscription successful
        """
        try:
            # Subscribe to wallet topic with direct callback
            self.ws.subscribe(topic="wallet", callback=callback)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to subscribe to wallet: {e}")
            return False
    
    def subscribe_custom(self, 
                        topic: str,
                        callback: Callable[[Dict], None]) -> bool:
        """
        Subscribe to a custom topic with raw message handling.
        
        Args:
            topic: WebSocket topic to subscribe to
            callback: Function to call when message is received
            
        Returns:
            True if subscription successful
        """
        try:
            self.ws.subscribe(topic=topic, callback=callback)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to subscribe to {topic}: {e}")
            return False
    
    def unsubscribe(self, topic: str) -> bool:
        """
        Unsubscribe from a topic.
        
        Args:
            topic: Topic to unsubscribe from
            
        Returns:
            True if unsubscription successful
        """
        try:
            self.ws.unsubscribe(topic=topic)
            return True
                
        except Exception as e:
            self.logger.error(f"Failed to unsubscribe from {topic}: {e}")
            return False
    
    def start_websocket(self):
        """
        Start the WebSocket connection for real-time updates.
        
        The pybit WebSocket manages its own thread internally.
        """
        # pybit WebSocket runs in its own thread automatically
        # No action needed as WebSocket starts on first subscription
    
    def stop_websocket(self):
        """Stop the WebSocket connection."""
        self.ws.exit()
    
    def disconnect(self):
        """Gracefully disconnect all connections."""
        # Stop WebSocket
        self.stop_websocket()