import numpy as np
import pandas as pd
from typing import Dict, Tuple, Union
from pathlib import Path

try:
    from factor_design import FactorDesigner, FactorMixer
except ImportError:
    from factor_section.factor_design import FactorDesigner, FactorMixer


class FactorAnalyzer:
    """Analyze factor effectiveness with cross-sectional IC."""

    @staticmethod
    def build_close_matrix(ticker_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Build close-price matrix from multi-ticker OHLCV dict.
        Output columns are ticker symbols.
        """
        close_dict = {}
        for ticker, df in ticker_data.items():
            if "Close" not in df.columns:
                raise ValueError(f"Ticker {ticker} has no Close column.")
            close_series = df["Close"].sort_index()
            if close_series.index.has_duplicates:
                close_series = close_series[~close_series.index.duplicated(keep="last")]
            close_dict[ticker] = close_series
        return pd.DataFrame(close_dict)

    @staticmethod
    def _future_returns(close_matrix: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
        """Future return_{t->t+horizon} aligned to timestamp t."""
        return close_matrix.pct_change(horizon).shift(-horizon - 1)

    @staticmethod
    def detect_return_anomalies(
        close_matrix: pd.DataFrame,
        horizon: int = 1,
        max_abs_return: float = 0.30,
    ) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Detect abnormal returns that may come from limit-up/down style jumps or bad adjustment.
        """
        future_ret = close_matrix.pct_change(horizon).shift(-horizon)
        anomaly_mask = future_ret.abs() > max_abs_return
        anomaly_by_ticker = anomaly_mask.sum()
        return anomaly_mask, anomaly_by_ticker

    @staticmethod
    def _sanitize_future_returns(
        future_ret: pd.DataFrame,
        max_abs_return: float = 0.30,
        winsorize_q: float = 0.01,
    ) -> pd.DataFrame:
        """
        Clean future returns before IC:
        1) drop extreme absolute returns (possible bad adjustment/outlier).
        2) cross-sectional winsorize each date.
        """
        cleaned = future_ret.copy()
        cleaned = cleaned.mask(cleaned.abs() > max_abs_return)

        if winsorize_q is not None and 0 < winsorize_q < 0.5:
            lower = cleaned.quantile(winsorize_q, axis=1)
            upper = cleaned.quantile(1 - winsorize_q, axis=1)
            cleaned = cleaned.clip(lower=lower, upper=upper, axis=0)
        return cleaned

    def calculate_ic_series(
        self,
        multi_factor_table: pd.DataFrame,
        close_matrix: pd.DataFrame,
        horizon: int = 5,
        method: str = "spearman",
        min_tickers: int = 30,
        max_abs_return: float = 0.30,
        winsorize_q: float = 0.01,
    ) -> pd.DataFrame:
        """
        Calculate date-by-date IC for each factor.
        multi_factor_table columns must be MultiIndex: (Ticker, FactorName).
        """
        if not isinstance(multi_factor_table.columns, pd.MultiIndex):
            raise ValueError("multi_factor_table must have MultiIndex columns: (Ticker, FactorName).")

        tickers = list(multi_factor_table.columns.get_level_values(0).unique())
        factors = list(multi_factor_table.columns.get_level_values(1).unique())
        missing_prices = [t for t in tickers if t not in close_matrix.columns]
        if missing_prices:
            raise ValueError(f"Missing close prices for tickers: {missing_prices}")

        future_ret = self._future_returns(close_matrix[tickers], horizon=horizon)
        future_ret = self._sanitize_future_returns(
            future_ret,
            max_abs_return=max_abs_return,
            winsorize_q=winsorize_q,
        )
        common_index = multi_factor_table.index.intersection(future_ret.index)

        ic_table = pd.DataFrame(index=common_index, columns=factors, dtype=float)
        for dt in common_index:
            ret_vec = future_ret.loc[dt]
            if isinstance(ret_vec, pd.DataFrame):
                # Guard against duplicated timestamps returning a 2D slice.
                ret_vec = ret_vec.iloc[-1]
            ret_vec = pd.to_numeric(pd.Series(ret_vec), errors="coerce")
            for factor_name in factors:
                factor_vec = multi_factor_table.xs(factor_name, level=1, axis=1).loc[dt]
                if isinstance(factor_vec, pd.DataFrame):
                    # Guard against duplicated timestamps returning a 2D slice.
                    factor_vec = factor_vec.iloc[-1]
                factor_vec = pd.to_numeric(pd.Series(factor_vec), errors="coerce")
                pair = pd.concat(
                    [factor_vec.rename("factor"), ret_vec.rename("ret")],
                    axis=1,
                ).dropna()
                if len(pair) < min_tickers:
                    ic_table.loc[dt, factor_name] = np.nan
                    continue
                if pair["factor"].nunique() <= 1 or pair["ret"].nunique() <= 1:
                    ic_table.loc[dt, factor_name] = np.nan
                    continue
                ic_table.loc[dt, factor_name] = pair["factor"].corr(pair["ret"], method=method)
        return ic_table

    @staticmethod
    def summarize_ic(ic_table: pd.DataFrame) -> pd.DataFrame:
        """Summarize IC quality per factor."""
        summary = pd.DataFrame(index=ic_table.columns)
        summary["ic_mean"] = ic_table.mean()
        summary["ic_std"] = ic_table.std()
        summary["ic_ir"] = summary["ic_mean"] / summary["ic_std"].replace(0, np.nan)
        summary["positive_ratio"] = (ic_table > 0).sum() / ic_table.count()
        summary["observations"] = ic_table.count()
        return summary.sort_values("ic_mean", ascending=False)

    def analyze_factor_ic(
        self,
        multi_factor_table: pd.DataFrame,
        close_matrix: pd.DataFrame,
        horizon: int = 5,
        method: str = "pearson",
        min_tickers: int = 30,
        max_abs_return: float = 0.30,
        winsorize_q: float = 0.01,
    ) -> Dict[str, pd.DataFrame]:
        """Convenience wrapper: return IC time-series and summary."""
        factor_to_test = multi_factor_table
        ic_series = self.calculate_ic_series(
            multi_factor_table=factor_to_test,
            close_matrix=close_matrix,
            horizon=horizon,
            method=method,
            min_tickers=min_tickers,
            max_abs_return=max_abs_return,
            winsorize_q=winsorize_q,
        )
        return {
            "ic_series": ic_series,
            "ic_summary": self.summarize_ic(ic_series),
        }

    def analyze_factor_ic_from_ticker_data(
        self,
        ticker_data: Dict[str, pd.DataFrame],
        designer: FactorDesigner,
        horizon: int = 5,
        method: str = "spearman",
        min_tickers: int = 30,
        max_abs_return: float = 0.30,
        winsorize_q: float = 0.01,
    ) -> Dict[str, pd.DataFrame]:
        """
        End-to-end IC analysis from raw ticker OHLCV data.
        Uses all tickers provided in ticker_data.
        """
        multi_factor_table = designer.build_multi_ticker_factor_table(ticker_data)
        close_matrix = self.build_close_matrix(ticker_data)
        return self.analyze_factor_ic(
            multi_factor_table=multi_factor_table,
            close_matrix=close_matrix,
            horizon=horizon,
            method=method,
            min_tickers=min_tickers,
            max_abs_return=max_abs_return,
            winsorize_q=winsorize_q,
        )


def optimize_single_factor_ic(
    analyzer: FactorAnalyzer,
    designer: FactorDesigner,
    ticker_data: Dict[str, pd.DataFrame],
    factor_name: str,
    apply_cross_sectional_rank_options: list = None,
) -> Tuple[pd.DataFrame, float, pd.DataFrame]:
    """
    Find the best IC mean for one factor by testing preprocessing options.
    """
    if apply_cross_sectional_rank_options is None:
        apply_cross_sectional_rank_options = [True, False]

    close_matrix = analyzer.build_close_matrix(ticker_data)
    best_metric = -np.inf
    best_option = None
    best_summary = None

    for use_rank in apply_cross_sectional_rank_options:
        multi_factor = designer.build_multi_ticker_factor_table(
            ticker_data,
            apply_cross_sectional_rank=use_rank,
        )
        if factor_name not in multi_factor.columns.get_level_values(1):
            raise ValueError(f"factor_name {factor_name} not found in factor table.")

        single_factor = multi_factor.xs(factor_name, level=1, axis=1)
        single_factor_multi = pd.concat({factor_name: single_factor}, axis=1).reorder_levels([1, 0], axis=1)

        result = analyzer.analyze_factor_ic(
            multi_factor_table=single_factor_multi,
            close_matrix=close_matrix,
            horizon=1,
            method="spearman",
            min_tickers=3,
            max_abs_return=0.30,
            winsorize_q=0.01,
        )
        ic_mean = float(result["ic_summary"].loc[factor_name, "ic_mean"])
        if ic_mean > best_metric:
            best_metric = ic_mean
            best_option = use_rank
            best_summary = result["ic_summary"].copy()

    best_info = pd.DataFrame(
        {
            "factor": [factor_name],
            "best_apply_cross_sectional_rank": [best_option],
            "best_ic_mean": [best_metric],
        }
    )
    return best_info, best_metric, best_summary


def ic_t_test(
    ic_series: Union[pd.DataFrame, pd.Series],
    alpha: float = 0.05,
) -> pd.DataFrame:
    """
    Perform two-sided t-test on IC mean against zero for each factor.
    Returns robust summary with confidence interval and significance flag.
    """
    from scipy import stats

    if isinstance(ic_series, pd.Series):
        ic_series = ic_series.to_frame(name=ic_series.name or "factor")

    summary = pd.DataFrame(index=ic_series.columns)
    summary["ic_mean"] = ic_series.mean()
    summary["ic_std"] = ic_series.std(ddof=1)
    summary["ic_count"] = ic_series.count()
    summary["ic_se"] = summary["ic_std"] / np.sqrt(summary["ic_count"])
    summary["t_stat"] = summary["ic_mean"] / summary["ic_se"]

    valid_mask = summary["ic_count"] >= 2
    summary["p_value"] = np.nan
    summary.loc[valid_mask, "p_value"] = stats.t.sf(
        np.abs(summary.loc[valid_mask, "t_stat"]),
        df=summary.loc[valid_mask, "ic_count"] - 1,
    ) * 2

    summary["ci_lower"] = np.nan
    summary["ci_upper"] = np.nan
    t_critical = pd.Series(index=summary.index, dtype=float)
    t_critical.loc[valid_mask] = stats.t.ppf(
        1 - alpha / 2,
        df=summary.loc[valid_mask, "ic_count"] - 1,
    )
    summary.loc[valid_mask, "ci_lower"] = (
        summary.loc[valid_mask, "ic_mean"] - t_critical.loc[valid_mask] * summary.loc[valid_mask, "ic_se"]
    )
    summary.loc[valid_mask, "ci_upper"] = (
        summary.loc[valid_mask, "ic_mean"] + t_critical.loc[valid_mask] * summary.loc[valid_mask, "ic_se"]
    )

    summary["alpha"] = alpha
    summary["is_significant"] = summary["p_value"] < alpha
    summary["significance_label"] = np.where(summary["is_significant"], "significant", "not significant")
    return summary.sort_values("ic_mean", ascending=False)


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]
    data_root = project_root / "data" / "cleaned"
    csv_files = sorted(data_root.glob("*_cleaned.csv"))
    tickers = [f.name.replace("_cleaned.csv", "") for f in csv_files]

    ticker_data = {}
    for t in tickers:
        df = pd.read_csv(data_root / f"{t}_cleaned.csv", index_col=0, parse_dates=[0])
        if df.empty:
            continue
        ticker_data[t] = df

    if len(ticker_data) < 3:
        raise ValueError("Need at least 3 non-empty tickers from data/cleaned to run cross-sectional IC analysis.")

    designer = FactorDesigner()
    raw_factors = designer.build_multi_ticker_factor_table(ticker_data)
    mixer = FactorMixer()
    composite_df = mixer.mix_multi_ticker(raw_factors)
    composite_df = composite_df.rank(axis=1, pct=True, method="average") - 0.5
    composite_multi = pd.concat({"mega_alpha": composite_df}, axis=1).reorder_levels([1, 0], axis=1)
    all_factors = pd.concat([raw_factors, composite_multi], axis=1)

    analyzer = FactorAnalyzer()
    close_matrix = analyzer.build_close_matrix(ticker_data)
    result = analyzer.analyze_factor_ic(
        multi_factor_table=all_factors,
        close_matrix=close_matrix,
        horizon=5,
        method="spearman",
        min_tickers=30,
        max_abs_return=0.30,
        winsorize_q=0.01,
    )

    print(f"Tickers used for IC analysis: {len(ticker_data)}")
    print("IC Summary:")
    target_factors = [
        "mad_ratio",
        "max_ret_penalty",
        "mega_alpha",
    ]
    print(result["ic_summary"].loc[target_factors])
    print("\nIC T-Test Summary:")
    ttest_summary = ic_t_test(result["ic_series"])
    ttest_cols = [
        "ic_mean",
        "t_stat",
        "p_value",
        "ci_lower",
        "ci_upper",
        "is_significant",
        "significance_label",
    ]
    print(ttest_summary.loc[target_factors, ttest_cols])
