#!/usr/bin/env python3
"""
Crypto Data Retriever
A simple utility to retrieve cryptocurrency data stored in Redis database by Bybit and CoinDCX monitors.
"""

import redis
import json
from typing import Dict, List, Optional, Union
from datetime import datetime
import time


class CryptoDataRetriever:
    def __init__(self, redis_host='localhost', redis_port=6379, redis_db=0):
        """
        Initialize the Crypto Data Retriever

        Args:
            redis_host (str): Redis server host
            redis_port (int): Redis server port
            redis_db (int): Redis database number
        """
        try:
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                decode_responses=True
            )
            # Test connection
            self.redis_client.ping()
            print(f"✅ Connected to Redis at {redis_host}:{redis_port}")
        except redis.ConnectionError:
            print(f"❌ Failed to connect to Redis at {redis_host}:{redis_port}")
            raise

    def get_crypto_data(self, symbol: str) -> Dict:
        """
        Retrieve all stored data for a specific cryptocurrency from both Bybit and CoinDCX

        Args:
            symbol (str): Cryptocurrency symbol (e.g., 'ETH', 'BTC', 'SOL')

        Returns:
            Dict: Contains all data for the cryptocurrency from both exchanges
        """
        symbol = symbol.upper()
        result = {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'bybit': {
                'spot_prices': [],
                'funding_rates': [],
                'latest_price': None,
                'latest_funding_rate': None
            },
            'coindcx': {
                'spot_prices': [],
                'latest_price': None
            },
            'combined_stats': {
                'total_price_updates': 0,
                'price_range': {'min': None, 'max': None},
                'latest_update': None
            }
        }

        # Get all Redis keys
        all_keys = self.redis_client.keys('*')

        # Filter and process keys related to the symbol
        symbol_keys = [key for key in all_keys if symbol in key.upper()]

        # Process different types of data
        for key in symbol_keys:
            try:
                data_type = self.redis_client.type(key)

                if data_type == 'string':
                    value = self.redis_client.get(key)
                    self._process_string_data(key, value, result)

                elif data_type == 'list':
                    values = self.redis_client.lrange(key, 0, -1)
                    self._process_list_data(key, values, result)

                elif data_type == 'zset':
                    values = self.redis_client.zrange(key, 0, -1, withscores=True)
                    self._process_zset_data(key, values, result)

                elif data_type == 'hash':
                    values = self.redis_client.hgetall(key)
                    self._process_hash_data(key, values, result)

            except Exception as e:
                print(f"Error processing key {key}: {e}")

        # Calculate combined statistics
        self._calculate_stats(result)

        return result

    def _process_string_data(self, key: str, value: str, result: Dict):
        """Process string type Redis data"""
        try:
            # Try to parse as JSON first
            parsed_data = json.loads(value)
            if 'bybit' in key.lower():
                if 'funding' in key.lower():
                    result['bybit']['latest_funding_rate'] = parsed_data
                else:
                    result['bybit']['latest_price'] = parsed_data
            elif 'coindcx' in key.lower():
                result['coindcx']['latest_price'] = parsed_data
        except json.JSONDecodeError:
            # Handle plain string values
            if 'bybit' in key.lower():
                if 'funding' in key.lower():
                    result['bybit']['latest_funding_rate'] = value
                else:
                    result['bybit']['latest_price'] = value
            elif 'coindcx' in key.lower():
                result['coindcx']['latest_price'] = value

    def _process_list_data(self, key: str, values: List[str], result: Dict):
        """Process list type Redis data"""
        processed_values = []
        for value in values:
            try:
                parsed = json.loads(value)
                processed_values.append(parsed)
            except json.JSONDecodeError:
                processed_values.append(value)

        if 'bybit' in key.lower():
            if 'funding' in key.lower():
                result['bybit']['funding_rates'] = processed_values
            else:
                result['bybit']['spot_prices'] = processed_values
        elif 'coindcx' in key.lower():
            result['coindcx']['spot_prices'] = processed_values

    def _process_zset_data(self, key: str, values: List[tuple], result: Dict):
        """Process sorted set type Redis data"""
        processed_values = []
        for value, score in values:
            try:
                parsed = json.loads(value)
                processed_values.append({'data': parsed, 'score': score})
            except json.JSONDecodeError:
                processed_values.append({'data': value, 'score': score})

        if 'bybit' in key.lower():
            if 'funding' in key.lower():
                result['bybit']['funding_rates'] = processed_values
            else:
                result['bybit']['spot_prices'] = processed_values
        elif 'coindcx' in key.lower():
            result['coindcx']['spot_prices'] = processed_values

    def _process_hash_data(self, key: str, values: Dict, result: Dict):
        """Process hash type Redis data"""
        if 'bybit' in key.lower():
            if 'funding' in key.lower():
                result['bybit']['latest_funding_rate'] = values
            else:
                result['bybit']['latest_price'] = values
        elif 'coindcx' in key.lower():
            result['coindcx']['latest_price'] = values

    def _calculate_stats(self, result: Dict):
        """Calculate combined statistics from all data"""
        all_prices = []
        latest_timestamp = None

        # Collect all price data
        for exchange_data in [result['bybit'], result['coindcx']]:
            for price_data in exchange_data['spot_prices']:
                if isinstance(price_data, dict):
                    if 'price' in price_data:
                        all_prices.append(float(price_data['price']))
                    if 'timestamp' in price_data:
                        ts = price_data['timestamp']
                        if latest_timestamp is None or ts > latest_timestamp:
                            latest_timestamp = ts
                elif isinstance(price_data, str):
                    try:
                        all_prices.append(float(price_data))
                    except ValueError:
                        pass

        # Add latest prices
        for exchange in ['bybit', 'coindcx']:
            latest = result[exchange]['latest_price']
            if latest:
                if isinstance(latest, dict) and 'price' in latest:
                    all_prices.append(float(latest['price']))
                elif isinstance(latest, str):
                    try:
                        all_prices.append(float(latest))
                    except ValueError:
                        pass

        # Calculate statistics
        if all_prices:
            result['combined_stats']['total_price_updates'] = len(all_prices)
            result['combined_stats']['price_range']['min'] = min(all_prices)
            result['combined_stats']['price_range']['max'] = max(all_prices)

        if latest_timestamp:
            result['combined_stats']['latest_update'] = latest_timestamp

    def get_all_symbols(self) -> List[str]:
        """
        Get all cryptocurrency symbols currently stored in the database

        Returns:
            List[str]: List of all cryptocurrency symbols
        """
        all_keys = self.redis_client.keys('*')
        symbols = set()

        # Extract symbols from Redis keys
        for key in all_keys:
            # Common patterns for crypto symbols
            parts = key.split('_')
            for part in parts:
                if part.upper() in ['BTC', 'ETH', 'SOL', 'BNB', 'DOGE', 'ADA', 'XRP', 'DOT', 'LINK', 'AVAX']:
                    symbols.add(part.upper())

        return sorted(list(symbols))

    def get_latest_prices(self) -> Dict[str, Dict]:
        """
        Get the latest prices for all cryptocurrencies

        Returns:
            Dict: Latest prices for all symbols
        """
        symbols = self.get_all_symbols()
        latest_prices = {}

        for symbol in symbols:
            data = self.get_crypto_data(symbol)
            latest_prices[symbol] = {
                'bybit': data['bybit']['latest_price'],
                'coindcx': data['coindcx']['latest_price']
            }

        return latest_prices

    def monitor_real_time(self, symbol: str, duration: int = 60):
        """
        Monitor real-time price updates for a specific cryptocurrency

        Args:
            symbol (str): Cryptocurrency symbol to monitor
            duration (int): Duration to monitor in seconds
        """
        symbol = symbol.upper()
        print(f"🔄 Monitoring {symbol} for {duration} seconds...")

        start_time = time.time()
        last_prices = {'bybit': None, 'coindcx': None}

        while time.time() - start_time < duration:
            data = self.get_crypto_data(symbol)

            # Check for price updates
            current_prices = {
                'bybit': data['bybit']['latest_price'],
                'coindcx': data['coindcx']['latest_price']
            }

            for exchange, price in current_prices.items():
                if price != last_prices[exchange]:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] {symbol} on {exchange.upper()}: {price}")
                    last_prices[exchange] = price

            time.sleep(1)

        print(f"✅ Monitoring completed for {symbol}")


def get_crypto_data(symbol: str) -> Dict:
    """
    Convenience function to get crypto data for a specific symbol

    Args:
        symbol (str): Cryptocurrency symbol (e.g., 'ETH', 'BTC')

    Returns:
        Dict: All data for the cryptocurrency from both exchanges
    """
    retriever = CryptoDataRetriever()
    return retriever.get_crypto_data(symbol)


def get_all_crypto_data() -> Dict[str, Dict]:
    """
    Convenience function to get data for all cryptocurrencies

    Returns:
        Dict: Data for all cryptocurrencies
    """
    retriever = CryptoDataRetriever()
    symbols = retriever.get_all_symbols()

    all_data = {}
    for symbol in symbols:
        all_data[symbol] = retriever.get_crypto_data(symbol)

    return all_data


if __name__ == "__main__":
    # Example usage
    try:
        retriever = CryptoDataRetriever()

        # Get data for ETH
        print("📊 Getting ETH data...")
        eth_data = retriever.get_crypto_data('ETH')
        print(f"ETH Data: {json.dumps(eth_data, indent=2)}")

        # Get all symbols
        print("\n📋 Available symbols:")
        symbols = retriever.get_all_symbols()
        print(symbols)

        # Get latest prices for all
        print("\n💰 Latest prices:")
        latest = retriever.get_latest_prices()
        for symbol, prices in latest.items():
            print(f"{symbol}: Bybit={prices['bybit']}, CoinDCX={prices['coindcx']}")

    except Exception as e:
        print(f"❌ Error: {e}")