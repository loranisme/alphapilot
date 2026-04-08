import yfinance as yf
import pandas as pd
import numpy as np
import os
from typing import List

LARGE_CAP_100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B", "TSLA", "LLY",
    "AVGO", "JPM", "V", "XOM", "UNH", "MA", "PG", "COST", "JNJ", "HD",
    "MRK", "ABBV", "BAC", "KO", "PEP", "ADBE", "CRM", "CVX", "WMT", "NFLX",
    "AMD", "TMO", "ACN", "MCD", "DIS", "CSCO", "INTC", "LIN", "ABT", "CMCSA",
    "ORCL", "DHR", "WFC", "VZ", "TXN", "PM", "NEE", "RTX", "QCOM", "AMGN",
    "BMY", "UNP", "HON", "IBM", "INTU", "LOW", "CAT", "GS", "SPGI", "MS",
    "AMAT", "NOW", "BKNG", "BLK", "GE", "AXP", "PGR", "SYK", "DE", "TJX",
    "MDT", "GILD", "ADP", "SCHW", "ISRG", "PLD", "MMC", "LMT", "T", "C",
    "MO", "NKE", "VRTX", "ZTS", "CB", "ETN", "SO", "DUK", "COP", "SLB",
    "BDX", "EOG", "CL", "CSX", "REGN", "ELV", "FI", "AON", "CI", "APD",
]


def load_sp500_tickers(limit: int = 500) -> List[str]:
    """
    Load up to `limit` S&P 500 symbols from Wikipedia.
    Falls back to LARGE_CAP_100 if online fetch fails.
    """
    datahub_url = "https://datahub.io/core/s-and-p-500-companies/r/constituents.csv"
    wiki_url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        table = pd.read_csv(datahub_url)
        symbols = table["Symbol"].astype(str).str.strip().tolist()
        symbols = [s.replace(".", "-") for s in symbols]
        symbols = list(dict.fromkeys(symbols))
        if len(symbols) < limit:
            print(f"Warning: only found {len(symbols)} symbols from DataHub; requested {limit}.")
        return symbols[:limit]
    except Exception as e:
        print(f"Warning: failed to load S&P 500 list from DataHub: {e}")

    try:
        table = pd.read_html(wiki_url)[0]
        symbols = table["Symbol"].astype(str).str.strip().tolist()
        # Yahoo Finance uses '-' for some symbols (e.g., BRK.B -> BRK-B).
        symbols = [s.replace(".", "-") for s in symbols]
        symbols = list(dict.fromkeys(symbols))
        if len(symbols) < limit:
            print(f"Warning: only found {len(symbols)} symbols from S&P page; requested {limit}.")
        return symbols[:limit]
    except Exception as e:
        print(f"Warning: failed to load S&P 500 list from web: {e}")
        print("Fallback to LARGE_CAP_100 list.")
        return LARGE_CAP_100[: min(limit, len(LARGE_CAP_100))]

def collect_stock_data(tickers, start_date, end_date, min_days=1000):
    """
    Collect historical US stock data using yfinance.

    Parameters:
    - tickers: List of stock tickers, e.g., ['AAPL', 'GOOGL']
    - start_date: Start date in 'YYYY-MM-DD' format
    - end_date: End date in 'YYYY-MM-DD' format
    - min_days: Minimum number of trading days required

    Returns:
    - dict: Dictionary with ticker as key and DataFrame as value
    """
    data = {}
    for ticker in tickers:
        try:
            stock_data = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                progress=False,
            )
            if len(stock_data) < min_days:
                print(f"Warning: {ticker} has only {len(stock_data)} days, less than {min_days}")
            # Collect essential fields
            selected_data = stock_data[['Open', 'High', 'Low', 'Close', 'Volume']]
            selected_data.columns = selected_data.columns.get_level_values(0)  # Flatten MultiIndex to 'Open', 'High', etc.
            selected_data.columns.name = None
            if selected_data.empty or selected_data.dropna(how='all').empty:
                print(f"Skipping {ticker}: downloaded dataset is empty/all-NaN")
                continue
            # Set timezone
            selected_data.index = pd.to_datetime(selected_data.index).tz_localize('UTC').tz_convert('America/New_York')
            data[ticker] = selected_data
        except Exception as e:
            print(f"Error collecting data for {ticker}: {e}")

    # Data quality checks (simplified)
    for ticker, df in data.items():
        if df.isnull().sum().sum() > 0:
            print(f"Warning: {ticker} data contains NaN values.")
        if (df < 0).any().any():
            print(f"Warning: {ticker} data contains negative values.")

    return data

def collect_realtime_data(tickers):
    """
    Collect real-time US stock data (optional, requires yfinance).

    Parameters:
    - tickers: List of stock tickers

    Returns:
    - pandas.DataFrame: Real-time data
    """
    try:
        data = {}
        for ticker in tickers:
            ticker_obj = yf.Ticker(ticker)
            info = ticker_obj.info
            data[ticker] = {
                'current_price': info.get('currentPrice'),
                'change_percent': info.get('regularMarketChangePercent')
            }
        return pd.DataFrame(data).T
    except Exception as e:
        print(f"Error collecting real-time data: {e}")
        return pd.DataFrame()

if __name__ == "__main__":
    # Collect ~4 years of daily data for up to 500 tickers
    tickers = load_sp500_tickers(limit=500)
    end_date = pd.Timestamp.now().date().isoformat()
    start_date = (pd.Timestamp.now() - pd.DateOffset(years=4)).date().isoformat()
    historical_data = collect_stock_data(tickers, start_date, end_date, min_days=700)
    save_csv = True
    saved_tickers = []
    if save_csv:
        os.makedirs('data/raw', exist_ok=True)
        os.makedirs('data/reports', exist_ok=True)
        for ticker, df in historical_data.items():
            if df.empty:
                print(f"Skipping {ticker}: empty dataset")
                continue
            print(f"Saving {ticker}: columns {df.columns.tolist()}")
            df.to_csv(f'data/raw/{ticker}_20years.csv', float_format='%.2f', index=True)
            saved_tickers.append(ticker)
    else:
        saved_tickers = [t for t, df in historical_data.items() if not df.empty]
    # Output basic information, one per line
    total_tickers = len(saved_tickers)
    sample_df = next((historical_data[t] for t in saved_tickers), None)
    print(f"Requested tickers: {len(tickers)}")
    print(f"Saved tickers: {total_tickers}")
    print(f"Date range requested: {start_date} to {end_date}")
    if sample_df is not None:
        print(f"Shape per ticker: {sample_df.shape}")
        print(f"Date range in sample: {sample_df.index.min()} to {sample_df.index.max()}")
        print(f"Timezone: {sample_df.index.tz}")
    if save_csv:
        saved_files = ', '.join([f'data/raw/{ticker}_20years.csv' for ticker in saved_tickers[:10]])
        print(f"Saved to: {saved_files}")
    else:
        print("CSV saving disabled: data kept in memory for downstream cleaning/factor analysis.")
