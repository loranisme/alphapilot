import numpy as np
import pandas as pd
from typing import Dict, Optional
from pathlib import Path


class FactorDesigner:
    """Design and calculate common equity factors from OHLCV data."""

    def __init__(self, trading_days_per_year: int = 252):
        self.trading_days_per_year = trading_days_per_year

    @staticmethod
    def _validate_input(df: pd.DataFrame):
        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

    @staticmethod
    def _vwap(df: pd.DataFrame) -> pd.Series:
        # Daily VWAP proxy for daily-bar data (no intraday trade stream available).
        return (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4.0

    @staticmethod
    def _winsorize_series(series: pd.Series, lower_q: float = 0.01, upper_q: float = 0.99) -> pd.Series:
        lower = series.quantile(lower_q)
        upper = series.quantile(upper_q)
        return series.clip(lower=lower, upper=upper)

    def baseline_reversal_factor(self, df: pd.DataFrame) -> pd.Series:
        """
        基准 5 日反转因子：过去 5 天跌得越多的，未来越容易涨
        """
        self._validate_input(df)
        raw_reversal = -1.0 * df["Close"].pct_change(5)
        rolling_vol = df["Close"].pct_change(1).rolling(20).std()
        factor = raw_reversal / (rolling_vol + 1e-6)
        factor = self._winsorize_series(factor)
        return factor.rename("baseline_reversal")
    
    def alpha_vwap_reversion(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Distance to Volume-Weighted Average Price (VWAP).

        Logic: mean-reversion to medium-term VWAP.
        """
        self._validate_input(df)
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
        rolling_volume = df["Volume"].rolling(window=window).sum()
        rolling_vwap = (typical_price * df["Volume"]).rolling(window=window).sum() / (rolling_volume + 1e-12)
        deviation = -1.0 * (df["Close"] - rolling_vwap) / rolling_vwap
        factor = self._winsorize_series(deviation)
        return factor.rename("vwap_reversion")

    def alpha_mad_ratio(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Mean Absolute Deviation (MAD) Ratio Factor.
        """
        self._validate_input(df)
        mad = df["Close"].rolling(window=window).apply(
            lambda x: np.abs(x - x.mean()).mean(),
            raw=True
        )
        factor = mad / df["Close"].replace(0, np.nan)
        factor = self._winsorize_series(factor)
        return factor.rename("mad_ratio")

    def alpha_max_ret_penalty(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Lottery Ticket Anomaly (MAX Factor).

        Penalize stocks with high recent maximum daily returns.
        """
        self._validate_input(df)
        daily_ret = df["Close"].pct_change(1)
        max_ret = daily_ret.rolling(window=window).max()
        factor = -1.0 * max_ret
        factor = self._winsorize_series(factor)
        return factor.rename("max_ret_penalty")

    def alpha_night_day_spread(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Overnight vs Intraday Sentiment Spread.
        """
        self._validate_input(df)
        night_ret = (df["Open"] / df["Close"].shift(1)) - 1.0
        day_ret = (df["Close"] / df["Open"]) - 1.0
        spread = night_ret - day_ret
        smoothed_spread = spread.rolling(window=window).mean()
        factor = self._winsorize_series(smoothed_spread)
        return factor.rename("night_day_spread")

    def alpha_median_reversion(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Robust Median Reversion Factor.
        """
        self._validate_input(df)
        rolling_median = df["Close"].rolling(window=window).median()
        factor = rolling_median / df["Close"].replace(0, np.nan)
        factor = self._winsorize_series(factor)
        return factor.rename("median_reversion")

    def alpha_ts_rank_close(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Time-Series Price Rank Factor (Overbought/Oversold).
        """
        self._validate_input(df)
        ts_rank = df["Close"].rolling(window=window).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1],
            raw=False,
        )
        factor = -1.0 * ts_rank
        factor = self._winsorize_series(factor)
        return factor.rename("ts_rank_close")

    def alpha_vwap_rebound_exhaustion(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        VWAP Rebound Exhaustion Factor.
        """
        self._validate_input(df)
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
        daily_vwap = typical_price
        rolling_min_vwap = daily_vwap.rolling(window=window).min()
        idx_min_vwap = daily_vwap.rolling(window=window).apply(np.argmin, raw=True)
        days_since_min = (window - 1) - idx_min_vwap
        slope = (df["Close"] - rolling_min_vwap) / (days_since_min + 1)
        factor = -1.0 * slope
        factor = self._winsorize_series(factor)
        return factor.rename("vwap_rebound_exhaustion")
    

    def build_factor_table(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Build one dataframe containing selected alpha factors.
        """
        factors: Dict[str, pd.Series] = {
            "mad_ratio": self.alpha_mad_ratio(df),
            "max_ret_penalty": self.alpha_max_ret_penalty(df),
        }
        return pd.DataFrame(factors, index=df.index)

    def build_multi_ticker_factor_table(
        self,
        ticker_data: Dict[str, pd.DataFrame],
        apply_cross_sectional_rank: bool = True,
    ) -> pd.DataFrame:
        """
        Build factors for multiple tickers.
        Return columns as MultiIndex: (Ticker, FactorName).
        """
        factor_by_ticker: Dict[str, pd.DataFrame] = {}
        for ticker, df in ticker_data.items():
            local_df = df.sort_index()
            if local_df.index.has_duplicates:
                local_df = local_df[~local_df.index.duplicated(keep="last")]
            factor_by_ticker[ticker] = self.build_factor_table(df=local_df)

        multi_factor = pd.concat(factor_by_ticker, axis=1)
        if not apply_cross_sectional_rank:
            return multi_factor

        factor_names = list(multi_factor.columns.get_level_values(1).unique())
        ranked_panels: Dict[str, pd.DataFrame] = {}
        for factor_name in factor_names:
            panel = multi_factor.xs(factor_name, level=1, axis=1)
            # Cross-sectional rank per date across tickers, then center around 0.
            ranked_panels[factor_name] = panel.rank(axis=1, pct=True, method="average") - 0.5

        ranked_multi = pd.concat(ranked_panels, axis=1).reorder_levels([1, 0], axis=1).sort_index(axis=1)
        return ranked_multi


class FactorMixer:
    """Combine selected factors into one mega alpha."""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        default_weights = {
            "mad_ratio": 0.30,
            "max_ret_penalty": -0.70,
        }
        self.weights = weights or default_weights
        self._normalize_weights()

    def _normalize_weights(self):
        abs_sum = sum(abs(v) for v in self.weights.values())
        if abs_sum == 0:
            raise ValueError("Weights cannot all be zero.")
        self.weights = {k: v / abs_sum for k, v in self.weights.items()}

    @staticmethod
    def _cross_sectional_zscore(row: pd.Series) -> pd.Series:
        mean = row.mean()
        std = row.std()
        if pd.isna(std) or std == 0:
            return pd.Series(0.0, index=row.index)
        clipped = row.clip(mean - 3 * std, mean + 3 * std)
        clipped_std = clipped.std()
        if pd.isna(clipped_std) or clipped_std == 0:
            return pd.Series(0.0, index=row.index)
        return (clipped - clipped.mean()) / clipped_std

    def mix_single_ticker(self, factor_table: pd.DataFrame, output_name: str = "mega_alpha") -> pd.Series:
        required = set(self.weights.keys())
        missing = required - set(factor_table.columns)
        if missing:
            raise ValueError(f"Missing factor columns for mixing: {sorted(missing)}")

        # Single ticker has no cross-section; keep this helper as simple weighted sum.
        composite = pd.Series(0.0, index=factor_table.index, dtype=float)
        for factor_name, weight in self.weights.items():
            composite = composite + factor_table[factor_name].fillna(0.0) * weight
        return composite.rename(output_name)

    def mix_multi_ticker(self, multi_factor_table: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(multi_factor_table.columns, pd.MultiIndex):
            raise ValueError("Expected MultiIndex columns: (Ticker, FactorName).")

        tickers = list(multi_factor_table.columns.get_level_values(0).unique())
        factor_names = list(multi_factor_table.columns.get_level_values(1).unique())
        missing = set(self.weights.keys()) - set(factor_names)
        if missing:
            raise ValueError(f"Missing factor columns for mixing: {sorted(missing)}")

        # Cross-sectional z-score by date within each factor across tickers.
        z_by_factor = {}
        for factor_name in self.weights:
            factor_panel = multi_factor_table.xs(factor_name, level=1, axis=1)[tickers]
            z_by_factor[factor_name] = factor_panel.apply(self._cross_sectional_zscore, axis=1).fillna(0.0)

        composite = pd.DataFrame(0.0, index=multi_factor_table.index, columns=tickers, dtype=float)
        for factor_name, weight in self.weights.items():
            composite = composite + z_by_factor[factor_name] * weight
        return composite


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]
    data_root = project_root / "data" / "cleaned"
    csv_files = sorted(data_root.glob("*_cleaned.csv"))
    tickers = [f.name.replace("_cleaned.csv", "") for f in csv_files]
    if len(tickers) == 0:
        raise ValueError(f"No cleaned ticker csv found under: {data_root}")

    # Example usage with one ticker file
    first_ticker = tickers[0]
    data = pd.read_csv(data_root / f"{first_ticker}_cleaned.csv", index_col=0, parse_dates=[0])
    designer = FactorDesigner()
    factor_df = designer.build_factor_table(data)
    print(f"Single ticker demo: {first_ticker}")
    print(factor_df.tail(5))

    # Example usage with all available tickers in data/cleaned
    multi_data = {
        t: pd.read_csv(data_root / f"{t}_cleaned.csv", index_col=0, parse_dates=[0]) for t in tickers
    }
    print(f"Multi-ticker demo count: {len(tickers)}")
    multi_factor_df = designer.build_multi_ticker_factor_table(multi_data)
    print(multi_factor_df.tail(3))
    clean_corr_matrix = multi_factor_df.stack(level=0).corr()
    print("\n=== Correlation matrix of all factors (2x2) ===")
    print(clean_corr_matrix)

    mixer = FactorMixer()
    print("Single ticker mega alpha:")
    print(mixer.mix_single_ticker(factor_df).tail(3))
    print("Multi ticker mega alpha:")
    print(mixer.mix_multi_ticker(multi_factor_df).tail(3))
