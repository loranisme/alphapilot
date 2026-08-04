import numpy as np
import pandas as pd
from typing import Dict, Optional
from pathlib import Path


class FactorDesigner:
    """
    Design and calculate equity alpha factors from OHLCV data.

    Factors are organized into two regime-complementary families (see
    FACTOR_FAMILY below; the third family, value/quality fundamentals, lives
    in FundamentalFactorDesigner):
      A. Reversal / Microstructure – works best in choppy, high-vol, bear markets
      B. Momentum / Trend          – works best in trending bull markets

    Seven volatility/lottery-demand "penalty" factors (ivol, upside-vol,
    vol-of-vol, MAX, skewness, MAD-ratio, HL-dispersion) and three near-zero
    signals (RSI reversal, volume-surge reversal, trend-slope penalty) were
    removed: stratified backtesting on 2022-2025 data showed they predict
    returns with the WRONG sign (t-stats -3.4 to -4.9) because high-volatility
    mega-cap tech stocks dominated that period — the classic low-vol anomaly
    does not survive at a 5-day horizon in an AI-momentum regime.
    """

    VOLUME_PRICE_ALPHA_FACTORS = (
        # ── A. Reversal / Microstructure ─────────────────────────────────────
        "reversal_5d",               # 5-day return reversal (same-horizon canonical)
        "short_reversal_1d",         # 1-day return reversal (Jegadeesh 1990)
        "min_ret_reversal",          # Worst-return reversal: past crash → bounce
        "vwap_reversion",            # 20-day VWAP mean reversion
        "median_reversion",          # 40-day median reversion (short-term window)
        "ts_rank_close",             # 20-day price rank reversal (short-term oversold)
        "stoch_reversal",            # 14-period stochastic reversal (oversold bounce)
        "weekly_range_reversal",     # 5-day price-range position reversal
        "intraday_position",         # Low bar position (close near daily low) → bounce
        "amihud_illiq",              # Amihud (2002) illiquidity premium
        # ── B. Momentum / Trend ──────────────────────────────────────────────
        "momentum_126d",             # 6-month momentum, skip 1 week (J&T 1993)
        "momentum_63d",              # 3-month momentum, skip 1 week
        "price_volume_corr",         # Price-volume co-movement (speculative momentum)
    )

    # Maps each technical factor to its regime family. Used by the
    # walk-forward selector to guarantee at least one factor per family
    # survives selection, even in folds where one family's IS IC is weak.
    FACTOR_FAMILY: Dict[str, str] = {
        "reversal_5d": "reversal", "short_reversal_1d": "reversal",
        "min_ret_reversal": "reversal", "vwap_reversion": "reversal",
        "median_reversion": "reversal", "ts_rank_close": "reversal",
        "stoch_reversal": "reversal", "weekly_range_reversal": "reversal",
        "intraday_position": "reversal", "amihud_illiq": "reversal",
        "momentum_126d": "momentum", "momentum_63d": "momentum",
        "price_volume_corr": "momentum",
    }

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
        return (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4.0

    @staticmethod
    def _winsorize_series(series: pd.Series, lower_q: float = 0.01, upper_q: float = 0.99) -> pd.Series:
        lower = series.quantile(lower_q)
        upper = series.quantile(upper_q)
        return series.clip(lower=lower, upper=upper)

    # ── Reversal / Mean-reversion ─────────────────────────────────────────────

    def alpha_reversal_5d(self, df: pd.DataFrame) -> pd.Series:
        """
        5-day return reversal — same-horizon canonical reversal signal.

        Uses the exact same look-back window as the prediction horizon (5 days),
        capturing the strongest weekly negative autocorrelation documented in
        Jegadeesh (1990) and Lo & MacKinlay (1990).  Past 5-day losers tend to
        outperform over the next 5 days; hence: -pct_change(5).
        """
        self._validate_input(df)
        factor = -1.0 * df["Close"].pct_change(5)
        factor = self._winsorize_series(factor)
        return factor.rename("reversal_5d")

    def alpha_short_reversal_1d(self, df: pd.DataFrame, smooth_window: int = 3) -> pd.Series:
        """
        1-day return reversal, lightly smoothed over 3 days.

        Jegadeesh (1990): short-run negative autocorrelation in weekly returns.
        smooth_window reduces microstructure noise while preserving the signal.
        """
        self._validate_input(df)
        daily_ret = df["Close"].pct_change(1)
        factor = -1.0 * daily_ret.rolling(window=smooth_window, min_periods=1).mean()
        factor = self._winsorize_series(factor)
        return factor.rename("short_reversal_1d")

    def alpha_min_ret_reversal(
        self,
        df: pd.DataFrame,
        return_window: int = 8,
        lookback: int = 240,
    ) -> pd.Series:
        """
        Past-crash reversal: stocks with the worst rolling minimum return bounce back.

        Negated minimum so that HIGH factor value = stocks that experienced the
        worst historical drawdown → most likely to mean-revert upward.
        Previous bug: missing negation caused IC to appear negative.
        """
        self._validate_input(df)
        min_periods = max(return_window + 1, lookback // 2)
        rolling_ret = df["Close"].pct_change(return_window)
        # Negate: worst crash (most negative min) → highest factor → positive IC
        factor = -1.0 * rolling_ret.rolling(window=lookback, min_periods=min_periods).min()
        factor = self._winsorize_series(factor)
        return factor.rename("min_ret_reversal")

    def alpha_vwap_reversion(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """Distance below rolling VWAP — mean reversion to volume-weighted price."""
        self._validate_input(df)
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
        min_periods = max(2, window // 2)
        rolling_volume = df["Volume"].rolling(window=window, min_periods=min_periods).sum()
        rolling_vwap = (
            (typical_price * df["Volume"]).rolling(window=window, min_periods=min_periods).sum()
            / (rolling_volume + 1e-12)
        )
        deviation = -1.0 * (df["Close"] - rolling_vwap) / (rolling_vwap + 1e-12)
        factor = self._winsorize_series(deviation)
        return factor.rename("vwap_reversion")

    def alpha_median_reversion(self, df: pd.DataFrame, window: int = 40) -> pd.Series:
        """
        Short-term median reversion (40-day window).

        Price below the 40-day rolling median → oversold → positive expected return.
        Reduced from 240-day: long lookback captured momentum trends rather than
        mean reversion at the 5-day forward-return horizon.
        """
        self._validate_input(df)
        rolling_median = df["Close"].rolling(window=window, min_periods=window // 2).median()
        factor = rolling_median / (df["Close"].replace(0, np.nan) + 1e-12)
        factor = self._winsorize_series(factor)
        return factor.rename("median_reversion")

    def alpha_ts_rank_close(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Short-term time-series price rank reversal (20-day window).

        Stocks near their 20-day low (low rank) are short-term oversold and tend
        to bounce.  Reduced from 240-day: the longer window tracked the dominant
        momentum trend rather than short-term reversion at the 5-day horizon.
        """
        self._validate_input(df)
        ts_rank = df["Close"].rolling(window=window, min_periods=window // 2).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1],
            raw=False,
        )
        factor = -1.0 * ts_rank   # high factor = near 20-day low = oversold
        factor = self._winsorize_series(factor)
        return factor.rename("ts_rank_close")

    # ── Momentum ──────────────────────────────────────────────────────────────

    def alpha_momentum_126d(self, df: pd.DataFrame, formation: int = 126, skip: int = 5) -> pd.Series:
        """
        6-month price momentum, skipping the most recent week.

        Jegadeesh & Titman (1993): the classic 6-month formation period.
        Longer window captures trend continuation with lower noise than 3-month,
        and is largely orthogonal to short-term reversal signals.
        """
        self._validate_input(df)
        factor = df["Close"].pct_change(formation).shift(skip)
        factor = self._winsorize_series(factor.reindex(df.index))
        return factor.rename("momentum_126d")

    def alpha_momentum_63d(self, df: pd.DataFrame, formation: int = 63, skip: int = 5) -> pd.Series:
        """
        3-month price momentum, skipping the most recent week.

        Jegadeesh & Titman (1993): 6-to-12-month winners continue to win.
        Using 63-day (≈3 months) formation and 5-day skip to avoid
        contamination from short-term reversal.
        """
        self._validate_input(df)
        factor = df["Close"].pct_change(formation).shift(skip)
        factor = self._winsorize_series(factor.reindex(df.index))
        return factor.rename("momentum_63d")

    def alpha_stoch_reversal(self, df: pd.DataFrame, k_period: int = 14, smooth: int = 3) -> pd.Series:
        """
        Stochastic oscillator reversal (replaces wk52_high_ratio).

        %K = (Close - L14) / (H14 - L14), smoothed to %D.
        Oversold (%D < 50) → positive signal; overbought (%D > 50) → negative.
        George & Hwang 52w-high momentum works at 6-12 month horizons, NOT at
        the 5-day prediction horizon tested here where the contrarian effect
        dominates.  The stochastic oscillator is calibrated for the same horizon.

        Williams (1973): %K < 20 oversold / %K > 80 overbought.
        """
        self._validate_input(df)
        high_k = df["High"].rolling(window=k_period, min_periods=k_period // 2).max()
        low_k = df["Low"].rolling(window=k_period, min_periods=k_period // 2).min()
        stoch_k = (df["Close"] - low_k) / (high_k - low_k + 1e-12) * 100.0
        stoch_d = stoch_k.rolling(window=smooth, min_periods=2).mean()
        # Center at 50: oversold (below 50) → positive factor
        factor = -(stoch_d - 50.0)
        factor = self._winsorize_series(factor)
        return factor.rename("stoch_reversal")

    # ── Volume / Liquidity ────────────────────────────────────────────────────

    def alpha_weekly_range_reversal(self, df: pd.DataFrame, window: int = 5) -> pd.Series:
        """
        Weekly high-low range position reversal (replaces recent_volume_share_penalty).

        Measures where today's close sits within the past `window`-day price range.
        Stocks closing near the TOP of their weekly range are short-term overbought;
        stocks near the BOTTOM are oversold.

        factor = -(close - low_Nd) / (high_Nd - low_Nd) - 0.5
        High factor (close near weekly LOW) → expect bounce.
        Low factor  (close near weekly HIGH) → expect pullback.

        Replaces the near-zero multi-term volume formula that lacked edge.
        """
        self._validate_input(df)
        high_n = df["High"].rolling(window=window, min_periods=max(2, window // 2)).max()
        low_n = df["Low"].rolling(window=window, min_periods=max(2, window // 2)).min()
        position = (df["Close"] - low_n) / (high_n - low_n + 1e-12)
        # Invert and center: position=0 (at weekly low) → factor=0.5 (high, buy)
        factor = -(position - 0.5)
        factor = self._winsorize_series(factor)
        return factor.rename("weekly_range_reversal")

    def alpha_price_volume_corr(
        self,
        df: pd.DataFrame,
        lookback: int = 237,
        price_lag: int = 1,
    ) -> pd.Series:
        """
        Price-volume co-movement (speculative momentum), positive direction.

        corr(lag_price, volume) > 0 indicates volume confirming the price trend.
        Originally designed as a "penalty" (negative sign, fading speculative
        coupling), but stratified backtesting on 2022-2025 data showed this
        factor was consistently assigned a NEGATIVE weight by the walk-forward
        selector (selected in all 9 folds) — i.e. high price-volume correlation
        stocks actually OUTPERFORM in a momentum-driven regime. Sign flipped to
        match the empirically validated direction.
        """
        self._validate_input(df)
        price_proxy = np.log(df["High"].replace(0, np.nan)).shift(price_lag)
        volume_proxy = np.log1p(df["Volume"].replace(0, np.nan))
        corr = price_proxy.rolling(window=lookback, min_periods=lookback // 2).corr(volume_proxy)
        factor = self._winsorize_series(corr)
        return factor.rename("price_volume_corr")

    def alpha_amihud_illiq(self, df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Amihud (2002) illiquidity: mean(|return| / dollar_volume) × 1e8.

        Stocks with higher price impact per dollar traded earn a liquidity risk
        premium — positive expected return for higher illiquidity.
        """
        self._validate_input(df)
        abs_ret = df["Close"].pct_change(1).abs()
        dollar_vol = df["Volume"] * (df["Open"] + df["Close"]) / 2.0
        illiq = abs_ret / (dollar_vol.replace(0, np.nan) + 1e-12)
        illiq = illiq.replace([np.inf, -np.inf], np.nan)
        factor = illiq.rolling(window=window, min_periods=window // 2).mean()
        factor = np.log1p(factor * 1e8)
        factor = self._winsorize_series(factor)
        return factor.rename("amihud_illiq")

    # ── Intraday / Microstructure ─────────────────────────────────────────────

    def alpha_intraday_position(self, df: pd.DataFrame, window: int = 5) -> pd.Series:
        """
        Low intraday bar-position reversal: close near daily low → expect bounce.

        bar_pos = (Close - Low) / (High - Low); smoothed over `window` days.
        NEGATED so that a HIGH factor value = close near the DAILY LOW = selling
        exhaustion → positive expected return over the next 5 days.

        Previous bug: the non-negated version had IC < 0 (stocks closing near the
        daily HIGH underperform short-term, consistent with intraday overbought),
        but was being treated as a positive-IC factor.
        """
        self._validate_input(df)
        hl = df["High"] - df["Low"]
        bar_pos = (df["Close"] - df["Low"]) / (hl.replace(0, np.nan) + 1e-12)
        smoothed = bar_pos.rolling(window=window, min_periods=2).mean()
        # Negate: low bar position (selling at lows) → positive signal
        factor = self._winsorize_series(-1.0 * smoothed)
        return factor.rename("intraday_position")

    # ── Table builders ────────────────────────────────────────────────────────

    def build_factor_table(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Build one dataframe containing all alpha factors for a single ticker.
        Columns match VOLUME_PRICE_ALPHA_FACTORS exactly.
        """
        factors: Dict[str, pd.Series] = {
            # A. Reversal / Microstructure
            "reversal_5d":               self.alpha_reversal_5d(df),
            "short_reversal_1d":         self.alpha_short_reversal_1d(df),
            "min_ret_reversal":          self.alpha_min_ret_reversal(df),
            "vwap_reversion":            self.alpha_vwap_reversion(df, window=20),
            "median_reversion":          self.alpha_median_reversion(df),
            "ts_rank_close":             self.alpha_ts_rank_close(df),
            "stoch_reversal":            self.alpha_stoch_reversal(df),
            "weekly_range_reversal":     self.alpha_weekly_range_reversal(df),
            "intraday_position":         self.alpha_intraday_position(df),
            "amihud_illiq":              self.alpha_amihud_illiq(df),
            # B. Momentum / Trend
            "momentum_126d":             self.alpha_momentum_126d(df),
            "momentum_63d":              self.alpha_momentum_63d(df),
            "price_volume_corr":         self.alpha_price_volume_corr(df),
        }
        return pd.DataFrame(
            {name: factors[name] for name in self.VOLUME_PRICE_ALPHA_FACTORS},
            index=df.index,
        )

    def build_multi_ticker_factor_table(
        self,
        ticker_data: Dict[str, pd.DataFrame],
        apply_cross_sectional_rank: bool = True,
    ) -> pd.DataFrame:
        """
        Build factors for multiple tickers and return MultiIndex (Ticker, FactorName) columns.
        Optionally applies cross-sectional percentile rank + centering per date.
        """
        factor_by_ticker: Dict[str, pd.DataFrame] = {}
        for ticker, df in ticker_data.items():
            local_df = df.sort_index()
            if local_df.index.has_duplicates:
                local_df = local_df[~local_df.index.duplicated(keep="last")]
            factor_by_ticker[ticker] = self.build_factor_table(df=local_df)

        multi_factor = pd.concat(factor_by_ticker, axis=1, sort=True)
        if not apply_cross_sectional_rank:
            return multi_factor

        factor_names = list(multi_factor.columns.get_level_values(1).unique())
        ranked_panels: Dict[str, pd.DataFrame] = {}
        for factor_name in factor_names:
            panel = multi_factor.xs(factor_name, level=1, axis=1)
            ranked = panel.rank(axis=1, pct=True, method="average")
            centered_rank = ranked.sub(ranked.mean(axis=1), axis=0)
            ranked_panels[factor_name] = centered_rank.mask(centered_rank.abs() < 1e-12, 0.0)

        ranked_multi = pd.concat(ranked_panels, axis=1).reorder_levels([1, 0], axis=1).sort_index(axis=1)
        return ranked_multi


class FactorMixer:
    """Combine selected factors into one composite alpha."""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        default_weights = {factor_name: 1.0 for factor_name in FactorDesigner.VOLUME_PRICE_ALPHA_FACTORS}
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

        z_by_factor = {}
        for factor_name in self.weights:
            factor_panel = multi_factor_table.xs(factor_name, level=1, axis=1)[tickers]
            z_by_factor[factor_name] = factor_panel.apply(self._cross_sectional_zscore, axis=1).fillna(0.0)

        composite = pd.DataFrame(0.0, index=multi_factor_table.index, columns=tickers, dtype=float)
        for factor_name, weight in self.weights.items():
            composite = composite + z_by_factor[factor_name] * weight
        return composite


class FundamentalFactorDesigner:
    """
    Derive cross-sectional alpha signals from quarterly fundamental data.

    All factors are computed from the fundamental panel already aligned to the
    price calendar with the 45-business-day announcement lag (see
    ``fundamental_loader.load_fundamental_panel``).  The designer expects a
    dict: {ticker: DataFrame(index=price_dates, columns=fundamental_items)}.

    Factor catalogue (6 factors, all academically documented):
      1. earnings_yield       – TTM EPS / Close  (Graham, Fama-French HML proxy)
      2. book_to_market       – Stockholders Equity / Market Cap  (Fama-French 1992)
      3. roe_ttm              – TTM Net Income / Stockholders Equity  (Piotroski 2000)
      4. gross_profitability  – TTM Gross Profit / Total Assets  (Novy-Marx 2013)
      5. asset_growth_penalty – –(Total Assets YoY growth)  (Cooper et al. 2008)
      6. eps_momentum         – YoY TTM EPS growth rate  (Chan et al. 1996)
    """

    FUNDAMENTAL_ALPHA_FACTORS = (
        "earnings_yield",
        "book_to_market",
        "roe_ttm",
        "gross_profitability",
        "asset_growth_penalty",
        "eps_momentum",
    )

    # Family C: value/quality fundamentals — regime-stable across cycles,
    # complements the technical reversal (A) and momentum (B) families in
    # FactorDesigner.FACTOR_FAMILY.
    FACTOR_FAMILY: Dict[str, str] = {name: "value" for name in FUNDAMENTAL_ALPHA_FACTORS}

    def __init__(self, trading_days_per_year: int = 252):
        self.trading_days_per_year = trading_days_per_year

    @staticmethod
    def _winsorize(series: pd.Series, q: float = 0.01) -> pd.Series:
        lo, hi = series.quantile(q), series.quantile(1 - q)
        return series.clip(lo, hi)

    # ------------------------------------------------------------------
    # Per-factor builders
    # Each receives:
    #   fundamental : dict  ticker -> DataFrame(price_dates, fund_items)
    #   close       : DataFrame(price_dates, tickers)
    # Returns : DataFrame(price_dates, tickers)
    # ------------------------------------------------------------------

    def alpha_earnings_yield(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        """TTM EPS / Close price (value signal)."""
        rows = {}
        for ticker, fdf in fundamental.items():
            if ticker not in close.columns:
                continue
            # Net Income_TTM / shares outstanding as proxy for EPS_TTM
            ni = fdf.get("Net Income_TTM")
            shares = fdf.get("Share Issued")
            if shares is None:
                shares = fdf.get("Ordinary Shares Number")
            price = close[ticker]
            if ni is None or price is None:
                continue
            eps_ttm = ni / shares.replace(0, np.nan) if shares is not None else ni
            ey = eps_ttm / price.replace(0, np.nan)
            rows[ticker] = ey
        panel = pd.DataFrame(rows)
        return panel.apply(lambda col: self._winsorize(col.dropna()).reindex(panel.index))

    def alpha_book_to_market(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        """Stockholders Equity / (Close × SharesOutstanding)."""
        rows = {}
        for ticker, fdf in fundamental.items():
            if ticker not in close.columns:
                continue
            equity = fdf.get("Stockholders Equity")
            if equity is None:
                equity = fdf.get("Common Stock Equity")
            shares = fdf.get("Share Issued")
            if shares is None:
                shares = fdf.get("Ordinary Shares Number")
            if equity is None:
                continue
            price = close[ticker]
            if shares is not None:
                mktcap = price * shares.replace(0, np.nan)
            else:
                mktcap = price
            btm = equity / mktcap.replace(0, np.nan)
            rows[ticker] = btm
        panel = pd.DataFrame(rows)
        return panel.apply(lambda col: self._winsorize(col.dropna()).reindex(panel.index))

    def alpha_roe_ttm(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        """TTM Net Income / Average Stockholders Equity (profitability signal)."""
        rows = {}
        for ticker, fdf in fundamental.items():
            if ticker not in close.columns:
                continue
            ni = fdf.get("Net Income_TTM")
            equity = fdf.get("Stockholders Equity")
            if equity is None:
                equity = fdf.get("Common Stock Equity")
            if ni is None or equity is None:
                continue
            eq_avg = equity.rolling(2, min_periods=1).mean()
            roe = ni / eq_avg.replace(0, np.nan)
            rows[ticker] = roe
        panel = pd.DataFrame(rows)
        return panel.apply(lambda col: self._winsorize(col.dropna()).reindex(panel.index))

    def alpha_gross_profitability(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        """TTM Gross Profit / Total Assets (Novy-Marx 2013)."""
        rows = {}
        for ticker, fdf in fundamental.items():
            if ticker not in close.columns:
                continue
            gp = fdf.get("Gross Profit_TTM")
            assets = fdf.get("Total Assets")
            if gp is None or assets is None:
                continue
            gpa = gp / assets.replace(0, np.nan)
            rows[ticker] = gpa
        panel = pd.DataFrame(rows)
        return panel.apply(lambda col: self._winsorize(col.dropna()).reindex(panel.index))

    def alpha_asset_growth_penalty(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        """Negative year-over-year total asset growth (Cooper et al. 2008)."""
        rows = {}
        for ticker, fdf in fundamental.items():
            if ticker not in close.columns:
                continue
            assets = fdf.get("Total Assets")
            if assets is None:
                continue
            # yoy growth on quarterly-lagged series (252 business days ~ 1 year)
            ag = assets.pct_change(252)
            rows[ticker] = -ag
        panel = pd.DataFrame(rows)
        return panel.apply(lambda col: self._winsorize(col.dropna()).reindex(panel.index))

    def alpha_eps_momentum(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        """YoY TTM EPS (Net Income TTM) growth — earnings momentum (Chan et al. 1996)."""
        rows = {}
        for ticker, fdf in fundamental.items():
            if ticker not in close.columns:
                continue
            ni = fdf.get("Net Income_TTM")
            if ni is None:
                continue
            eps_mom = ni.pct_change(252)
            rows[ticker] = eps_mom
        panel = pd.DataFrame(rows)
        return panel.apply(lambda col: self._winsorize(col.dropna()).reindex(panel.index))

    # ------------------------------------------------------------------
    # Main builder
    # ------------------------------------------------------------------

    def build_factor_panels(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> Dict[str, pd.DataFrame]:
        """
        Compute all 6 fundamental factor panels.

        Parameters
        ----------
        fundamental : dict  ticker -> DataFrame(price_dates, fund_items)
        close : DataFrame(price_dates, tickers)

        Returns
        -------
        dict  factor_name -> DataFrame(price_dates, tickers)
        """
        builders = {
            "earnings_yield": self.alpha_earnings_yield,
            "book_to_market": self.alpha_book_to_market,
            "roe_ttm": self.alpha_roe_ttm,
            "gross_profitability": self.alpha_gross_profitability,
            "asset_growth_penalty": self.alpha_asset_growth_penalty,
            "eps_momentum": self.alpha_eps_momentum,
        }
        panels = {}
        for name, fn in builders.items():
            try:
                p = fn(fundamental, close)
                if p is not None and not p.empty:
                    panels[name] = p
            except Exception as exc:
                import warnings
                warnings.warn(f"FundamentalFactorDesigner.{name} failed: {exc}")
        return panels

    def build_multi_ticker_factor_table(
        self,
        fundamental: Dict[str, pd.DataFrame],
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Build a MultiIndex (Ticker, FactorName) factor table compatible with
        FactorAnalyzer.calculate_ic_series.

        Returns DataFrame with MultiIndex columns.
        """
        panels = self.build_factor_panels(fundamental, close)
        if not panels:
            return pd.DataFrame()

        tickers = list(close.columns)
        all_dates = close.index

        parts = []
        for factor_name, panel in panels.items():
            for ticker in tickers:
                if ticker in panel.columns:
                    s = panel[ticker].reindex(all_dates)
                    parts.append(pd.Series(s.values, index=all_dates, name=(ticker, factor_name)))

        if not parts:
            return pd.DataFrame()

        combined = pd.concat(parts, axis=1)
        combined.columns = pd.MultiIndex.from_tuples(combined.columns)
        return combined


def _read_cleaned_csv(path) -> pd.DataFrame:
    # NOTE: do NOT use parse_dates=[0] — mixed DST offsets (-04:00/-05:00)
    # in the saved index make pandas' dtype inference silently blank rows.
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True, errors="coerce").tz_convert("America/New_York")
    return df


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]
    data_root = project_root / "data" / "cleaned"
    csv_files = sorted(data_root.glob("*_cleaned.csv"))
    tickers = [f.name.replace("_cleaned.csv", "") for f in csv_files]
    if len(tickers) == 0:
        raise ValueError(f"No cleaned ticker csv found under: {data_root}")

    first_ticker = tickers[0]
    data = _read_cleaned_csv(data_root / f"{first_ticker}_cleaned.csv")
    designer = FactorDesigner()
    factor_df = designer.build_factor_table(data)
    print(f"Single ticker demo: {first_ticker}")
    print(f"Factors ({len(factor_df.columns)}): {list(factor_df.columns)}")
    print(factor_df.tail(3))

    multi_data = {
        t: _read_cleaned_csv(data_root / f"{t}_cleaned.csv") for t in tickers
    }
    print(f"\nMulti-ticker demo count: {len(tickers)}")
    multi_factor_df = designer.build_multi_ticker_factor_table(multi_data)
    print(multi_factor_df.tail(2))

    mixer = FactorMixer()
    print("\nSingle ticker composite alpha (tail 3):")
    print(mixer.mix_single_ticker(factor_df).tail(3))
    print("\nMulti ticker composite alpha (tail 2):")
    print(mixer.mix_multi_ticker(multi_factor_df).tail(2))
