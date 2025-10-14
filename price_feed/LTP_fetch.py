#!/usr/bin/env python3
"""
LTP Fetch Example for Crypto Symbols
Simple script to get Last Traded Price (LTP) data for any cryptocurrency from both Bybit and CoinDCX exchanges.
"""

from price_feed.crypto_data_retriever import CryptoDataRetriever
import json

def get_crypto_ltp(symbol):
    """
    Get cryptocurrency Last Traded Price from both exchanges

    Args:
        symbol (str): Cryptocurrency symbol (e.g., 'ETH', 'BTC', 'SOL')

    Returns:
        dict: LTP data for the symbol from Bybit and CoinDCX
    """
    symbol = symbol.upper()

    try:
        # Initialize the crypto data retriever
        retriever = CryptoDataRetriever()

        # Get crypto data for the specified symbol
        crypto_data = retriever.get_crypto_data(symbol)

        # Extract comprehensive data
        result = {
            'symbol': symbol,
            'timestamp': crypto_data['timestamp'],
            'bybit_data': {
                'ltp': None,
                'timestamp': None
            },
            'coindcx_data': {
                'ltp': None,
                'timestamp': None,
                'current_funding_rate': None,
                'estimated_funding_rate': None,
                'funding_timestamp': None
            },
            'success': True
        }

        # Extract Bybit data (LTP + timestamp)
        if crypto_data['bybit']['latest_price']:
            bybit_price = crypto_data['bybit']['latest_price']
            if isinstance(bybit_price, dict):
                result['bybit_data']['ltp'] = bybit_price.get('ltp')
                result['bybit_data']['timestamp'] = bybit_price.get('timestamp')
            elif isinstance(bybit_price, str):
                result['bybit_data']['ltp'] = bybit_price
            else:
                result['bybit_data']['ltp'] = bybit_price

        # Extract Bybit funding rate data
        if crypto_data['bybit']['latest_funding_rate']:
            bybit_funding = crypto_data['bybit']['latest_funding_rate']
            if isinstance(bybit_funding, dict):
                # If Bybit has funding data, we might want to include it
                if 'timestamp' in bybit_funding and not result['bybit_data']['timestamp']:
                    result['bybit_data']['timestamp'] = bybit_funding.get('timestamp')

        # Extract CoinDCX comprehensive data
        if crypto_data['coindcx']['latest_price']:
            coindcx_price = crypto_data['coindcx']['latest_price']
            if isinstance(coindcx_price, dict):
                result['coindcx_data']['ltp'] = coindcx_price.get('ltp')
                result['coindcx_data']['timestamp'] = coindcx_price.get('timestamp')
                # CoinDCX funding rates might be in the price data or separate
                result['coindcx_data']['current_funding_rate'] = coindcx_price.get('current_funding_rate')
                result['coindcx_data']['estimated_funding_rate'] = coindcx_price.get('estimated_funding_rate')
                result['coindcx_data']['funding_timestamp'] = coindcx_price.get('funding_timestamp')
            elif isinstance(coindcx_price, str):
                result['coindcx_data']['ltp'] = coindcx_price
            else:
                result['coindcx_data']['ltp'] = coindcx_price

        # Check for CoinDCX funding data in separate funding rate field
        # Look through all CoinDCX data for funding information
        for data_list in [crypto_data['coindcx']['spot_prices']]:
            if data_list:
                # Get the latest funding data if available
                latest_funding = data_list[-1] if isinstance(data_list, list) and data_list else data_list
                if isinstance(latest_funding, dict):
                    if 'current_funding_rate' in latest_funding and not result['coindcx_data']['current_funding_rate']:
                        result['coindcx_data']['current_funding_rate'] = latest_funding.get('current_funding_rate')
                    if 'estimated_funding_rate' in latest_funding and not result['coindcx_data']['estimated_funding_rate']:
                        result['coindcx_data']['estimated_funding_rate'] = latest_funding.get('estimated_funding_rate')
                    if 'funding_timestamp' in latest_funding and not result['coindcx_data']['funding_timestamp']:
                        result['coindcx_data']['funding_timestamp'] = latest_funding.get('funding_timestamp')

        return result

    except Exception as e:
        return {
            'symbol': symbol,
            'success': False,
            'error': str(e),
            'bybit_data': None,
            'coindcx_data': None
        }

def get_crypto_ltp_formatted(symbol):
    """
    Get crypto LTP data with additional formatting and analysis

    Args:
        symbol (str): Cryptocurrency symbol (e.g., 'ETH', 'BTC', 'SOL')

    Returns:
        dict: Enhanced LTP data with price analysis
    """
    symbol = symbol.upper()
    result = get_crypto_ltp(symbol)

    if result['success']:
        # Add price analysis if both prices are available
        bybit_ltp = result['bybit_data']['ltp'] if result['bybit_data'] else None
        coindcx_ltp = result['coindcx_data']['ltp'] if result['coindcx_data'] else None

        if bybit_ltp and coindcx_ltp:
            try:
                bybit_price = float(bybit_ltp)
                coindcx_price = float(coindcx_ltp)
                difference = abs(bybit_price - coindcx_price)
                percentage_diff = (difference / min(bybit_price, coindcx_price)) * 100

                result['price_analysis'] = {
                    'price_difference': round(difference, 2),
                    'percentage_difference': round(percentage_diff, 2),
                    'higher_exchange': 'bybit' if bybit_price > coindcx_price else 'coindcx' if coindcx_price > bybit_price else 'equal',
                    'difference_amount': round(difference, 2)
                }
            except ValueError:
                result['price_analysis'] = {
                    'error': 'Could not calculate price difference (non-numeric values)'
                }
        else:
            result['price_analysis'] = {
                'error': 'Insufficient data for price analysis'
            }

    return result

def get_multiple_crypto_ltp(symbols):
    """
    Get LTP data for multiple cryptocurrencies

    Args:
        symbols (list): List of cryptocurrency symbols

    Returns:
        dict: LTP data for all symbols
    """
    results = {}
    for symbol in symbols:
        results[symbol.upper()] = get_crypto_ltp(symbol)
    return results

def get_multiple_crypto_ltp_formatted(symbols):
    """
    Get LTP data for multiple cryptocurrencies with formatting

    Args:
        symbols (list): List of cryptocurrency symbols

    Returns:
        dict: LTP data with analysis for all symbols
    """
    results = {}
    for symbol in symbols:
        results[symbol.upper()] = get_crypto_ltp_formatted(symbol)
    return results

def print_crypto_ltp(symbol):
    """
    Print crypto LTP data in a formatted way (for display purposes only)

    Args:
        symbol (str): Cryptocurrency symbol (e.g., 'ETH', 'BTC', 'SOL')
    """
    result = get_crypto_ltp_formatted(symbol)
    symbol = symbol.upper()

    if result['success']:
        print(f"{symbol} Data Retrieved Successfully")
        print(f"Timestamp: {result['timestamp']}")

        # Bybit Data
        print("\nBybit Data:")
        if result['bybit_data'] and result['bybit_data']['ltp']:
            print(f"   LTP: {result['bybit_data']['ltp']} USDT")
            if result['bybit_data']['timestamp']:
                print(f"   Timestamp: {result['bybit_data']['timestamp']}")
        else:
            print(f"   No data available")

        # CoinDCX Data
        print("\nCoinDCX Data:")
        if result['coindcx_data'] and result['coindcx_data']['ltp']:
            print(f"   LTP: {result['coindcx_data']['ltp']} USDT")
            if result['coindcx_data']['timestamp']:
                print(f"   Timestamp: {result['coindcx_data']['timestamp']}")
            if result['coindcx_data']['current_funding_rate']:
                print(f"   Current Funding Rate: {result['coindcx_data']['current_funding_rate']}")
            if result['coindcx_data']['estimated_funding_rate']:
                print(f"   Estimated Funding Rate: {result['coindcx_data']['estimated_funding_rate']}")
            if result['coindcx_data']['funding_timestamp']:
                print(f"   Funding Timestamp: {result['coindcx_data']['funding_timestamp']}")
        else:
            print(f"   No data available")

        # Price analysis
        if 'price_analysis' in result and 'error' not in result['price_analysis']:
            analysis = result['price_analysis']
            print(f"\nPrice Analysis:")
            print(f"   Price Difference: {analysis['price_difference']} USDT")
            print(f"   Percentage Difference: {analysis['percentage_difference']}%")

            if analysis['higher_exchange'] == 'equal':
                print(f"   Prices are equal")
            else:
                print(f"   {analysis['higher_exchange'].title()} is higher by {analysis['difference_amount']} USDT")
        elif 'price_analysis' in result:
            print(f"\n{result['price_analysis']['error']}")
    else:
        print(f"Failed to retrieve {symbol} data")
        print(f"Error: {result['error']}")

def print_multiple_crypto_ltp(symbols):
    """
    Print LTP data for multiple cryptocurrencies (for display purposes only)

    Args:
        symbols (list): List of cryptocurrency symbols
    """
    print(f"Fetching LTP data for {len(symbols)} cryptocurrencies...\n")

    for symbol in symbols:
        print_crypto_ltp(symbol)
        print("-" * 40)

if __name__ == "__main__":
    # Example 1: Get comprehensive data for ETH
    print("Example 1: Getting comprehensive ETH data")
    eth_data = get_crypto_ltp('ETH')
    print("ETH data retrieved:", eth_data['success'])
    if eth_data['success']:
        print("Bybit LTP:", eth_data['bybit_data']['ltp'])
        print("Bybit Timestamp:", eth_data['bybit_data']['timestamp'])
        print("CoinDCX LTP:", eth_data['coindcx_data']['ltp'])
        print("CoinDCX Timestamp:", eth_data['coindcx_data']['timestamp'])
        print("CoinDCX Current Funding Rate:", eth_data['coindcx_data']['current_funding_rate'])
        print("CoinDCX Estimated Funding Rate:", eth_data['coindcx_data']['estimated_funding_rate'])

    print("\n" + "="*50 + "\n")

    # Example 2: Get formatted data with analysis
    print("Example 2: Getting ETH data with price analysis")
    eth_formatted = get_crypto_ltp_formatted('ETH')
    print("Data with analysis:", json.dumps(eth_formatted, indent=2))

    print("\n" + "="*50 + "\n")

    # Example 3: Get multiple symbols data
    print("Example 3: Getting multiple symbols data")
    crypto_symbols = ['BTC', 'ETH', 'SOL']
    multi_data = get_multiple_crypto_ltp(crypto_symbols)
    print("Retrieved data for symbols:", list(multi_data.keys()))

    print("\n" + "="*50 + "\n")

    # Example 4: Print functions (for display purposes)
    print("Example 4: Display functions (formatted output)")
    print_crypto_ltp('ETH')