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
        """Build close-price matrix from multi-ticker OHLCV dict."""
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
        return close_matrix.pct_change(horizon, fill_method=None).shift(-horizon)

    @staticmethod
    def _sanitize_future_returns(
        future_ret: pd.DataFrame,
        max_abs_return: float = 0.30,
        winsorize_q: float = 0.01,
    ) -> pd.DataFrame:
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
        Compute date-by-date IC for each factor.
        multi_factor_table must have MultiIndex columns: (Ticker, FactorName).
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
            future_ret, max_abs_return=max_abs_return, winsorize_q=winsorize_q
        )
        common_index = multi_factor_table.index.intersection(future_ret.index)

        ic_table = pd.DataFrame(index=common_index, columns=factors, dtype=float)
        for dt in common_index:
            ret_vec = future_ret.loc[dt]
            if isinstance(ret_vec, pd.DataFrame):
                ret_vec = ret_vec.iloc[-1]
            ret_vec = pd.to_numeric(pd.Series(ret_vec), errors="coerce")
            for factor_name in factors:
                factor_vec = multi_factor_table.xs(factor_name, level=1, axis=1).loc[dt]
                if isinstance(factor_vec, pd.DataFrame):
                    factor_vec = factor_vec.iloc[-1]
                factor_vec = pd.to_numeric(pd.Series(factor_vec), errors="coerce")
                pair = pd.concat(
                    [factor_vec.rename("factor"), ret_vec.rename("ret")], axis=1
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
    def summarize_ic(ic_table: pd.DataFrame, periods_per_year: float = 252.0) -> pd.DataFrame:
        """Summarize IC quality per factor."""
        summary = pd.DataFrame(index=ic_table.columns)
        summary["ic_mean"] = ic_table.mean()
        summary["ic_std"] = ic_table.std()
        summary["ic_ir"] = summary["ic_mean"] / summary["ic_std"].replace(0, np.nan)
        summary["ic_ir_annualized"] = summary["ic_ir"] * np.sqrt(periods_per_year)
        summary["positive_ratio"] = (ic_table > 0).sum() / ic_table.count()
        summary["observations"] = ic_table.count()
        return summary.sort_values("ic_mean", ascending=False)

    def analyze_factor_ic(
        self,
        multi_factor_table: pd.DataFrame,
        close_matrix: pd.DataFrame,
        horizon: int = 5,
        method: str = "spearman",
        min_tickers: int = 30,
        max_abs_return: float = 0.30,
        winsorize_q: float = 0.01,
        periods_per_year: float = None,
    ) -> Dict[str, pd.DataFrame]:
        """Convenience wrapper: return IC time-series and summary."""
        if periods_per_year is None:
            periods_per_year = 252.0 / max(horizon, 1)
        ic_series = self.calculate_ic_series(
            multi_factor_table=multi_factor_table,
            close_matrix=close_matrix,
            horizon=horizon,
            method=method,
            min_tickers=min_tickers,
            max_abs_return=max_abs_return,
            winsorize_q=winsorize_q,
        )
        return {
            "ic_series": ic_series,
            "ic_summary": self.summarize_ic(ic_series, periods_per_year=periods_per_year),
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
        """End-to-end IC analysis from raw ticker OHLCV data."""
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


def ic_t_test(
    ic_series: Union[pd.DataFrame, pd.Series],
    alpha: float = 0.05,
    periods_per_year: float = 252.0,
) -> pd.DataFrame:
    """
    Two-sided t-test on IC mean against zero for each factor.
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
    summary["ic_ir"] = summary["ic_mean"] / summary["ic_std"].replace(0, np.nan)
    summary["ic_ir_annualized"] = summary["ic_ir"] * np.sqrt(periods_per_year)

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
        summary.loc[valid_mask, "ic_mean"]
        - t_critical.loc[valid_mask] * summary.loc[valid_mask, "ic_se"]
    )
    summary.loc[valid_mask, "ci_upper"] = (
        summary.loc[valid_mask, "ic_mean"]
        + t_critical.loc[valid_mask] * summary.loc[valid_mask, "ic_se"]
    )

    summary["alpha"] = alpha
    summary["is_significant"] = summary["p_value"] < alpha
    summary["significance_label"] = np.where(summary["is_significant"], "significant", "not significant")
    return summary.sort_values("ic_mean", ascending=False)


def select_top_ic_ttest_factors(
    ic_series: pd.DataFrame,
    top_n: int = 10,
    alpha: float = 0.05,
    require_positive_ic: bool = True,
    require_significant: bool = False,
    min_abs_annualized_ic_ir: float = 0.0,
    periods_per_year: float = 252.0,
) -> pd.DataFrame:
    """
    Select best alpha factors by IC mean + t-test quality.

    Scoring: 45% IC rank + 45% |t-stat| rank + 10% significance flag.
    Falls back to strongest |IC| names when not enough positive-IC factors exist.
    """
    if top_n <= 0:
        raise ValueError("top_n must be positive.")

    summary = ic_t_test(ic_series, alpha=alpha, periods_per_year=periods_per_year).copy()
    summary["abs_ic_mean"] = summary["ic_mean"].abs()
    summary["abs_t_stat"] = summary["t_stat"].abs()
    summary["abs_ic_ir_annualized"] = summary["ic_ir_annualized"].abs()

    positive = summary[summary["ic_mean"] > 0] if require_positive_ic else summary
    if len(positive) >= top_n:
        eligible = positive.copy()
        eligible["ic_rank_score"] = eligible["ic_mean"].rank(pct=True)
    else:
        eligible = summary.copy()
        eligible["ic_rank_score"] = eligible["abs_ic_mean"].rank(pct=True)

    eligible["t_rank_score"] = eligible["abs_t_stat"].rank(pct=True)
    eligible["significance_score"] = eligible["is_significant"].astype(float)
    eligible["selection_score"] = (
        0.45 * eligible["ic_rank_score"]
        + 0.45 * eligible["t_rank_score"]
        + 0.10 * eligible["significance_score"]
    )

    if require_significant:
        eligible = eligible[eligible["is_significant"]]
    if min_abs_annualized_ic_ir > 0:
        eligible = eligible[eligible["abs_ic_ir_annualized"] >= min_abs_annualized_ic_ir]

    selected = eligible.sort_values(
        ["selection_score", "abs_ic_mean", "abs_t_stat"],
        ascending=[False, False, False],
    ).head(top_n)
    return selected


def build_ic_ttest_weights(
    selected_summary: pd.DataFrame,
    ic_series: pd.DataFrame = None,
    shrinkage: float = 0.1,
) -> Dict[str, float]:
    """
    Build composite weights from selected factors using IC-IR weighting.

    Weight formula: w_i ∝ sign(IC_mean_i) × |IC_IR_i|

    This is theoretically optimal for maximising the composite raw ICIR under
    the assumption that IC distributions are stationary (the analogue of
    mean-variance portfolio optimisation applied to IC streams).

    Optional: when ic_series is supplied the weights are further adjusted by
    the inverse of the IC pairwise correlation (shrinkage-regularised), which
    reduces concentration in correlated factor clusters and improves
    out-of-sample diversification.

    Parameters
    ----------
    selected_summary : output of select_top_ic_ttest_factors
    ic_series        : full IC time-series DataFrame (same factors, all dates)
    shrinkage        : Ledoit-Wolf-style shrinkage intensity toward identity
                       correlation matrix (0 = no shrinkage, 1 = full shrinkage)
    """
    if selected_summary.empty:
        raise ValueError("selected_summary cannot be empty.")

    # Raw IC-IR signal: sign carries direction, magnitude carries quality
    ic_ir = selected_summary["ic_mean"] / selected_summary["ic_std"].replace(0, np.nan)
    ic_ir = ic_ir.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    if ic_series is not None and not ic_series.empty:
        selected_factors = list(selected_summary.index)
        available = [f for f in selected_factors if f in ic_series.columns]
        if len(available) >= 2:
            ic_sub = ic_series[available].dropna(how="all")

            # IC correlation matrix C (more numerically stable than full Σ).
            # Shrink toward identity: (1-α)C + αI
            corr_arr = ic_sub.corr().fillna(0.0).values.copy()
            np.fill_diagonal(corr_arr, 1.0)
            n = len(corr_arr)
            shrunk_corr = (1 - shrinkage) * corr_arr + shrinkage * np.eye(n)
            try:
                corr_inv = np.linalg.inv(shrunk_corr)
            except np.linalg.LinAlgError:
                corr_inv = np.eye(n)

            # MVO in correlation space: w ∝ C^{-1} × IC_IR
            # Equivalent to Σ_IC^{-1} × μ_IC when factors have similar IC_std.
            ic_ir_vec = ic_ir.reindex(available).fillna(0.0).values
            mvo_weights = corr_inv @ ic_ir_vec
            raw_weight = pd.Series(mvo_weights, index=available)

            # Factors absent from ic_series keep their simple IC-IR weight
            for f in set(selected_summary.index) - set(available):
                raw_weight[f] = ic_ir.get(f, 0.0)
            raw_weight = raw_weight.reindex(selected_summary.index).fillna(0.0)
        else:
            raw_weight = ic_ir
    else:
        raw_weight = ic_ir

    if raw_weight.abs().sum() == 0:
        raw_weight = pd.Series(1.0, index=selected_summary.index)

    normalized = raw_weight / raw_weight.abs().sum()
    return normalized.to_dict()


def build_ic_weighted_composite_alpha(
    multi_factor_table: pd.DataFrame,
    selected_summary: pd.DataFrame,
    ic_series: pd.DataFrame = None,
    shrinkage: float = 0.1,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """
    Create composite alpha from selected factors using IC-IR (+ optional
    correlation-adjusted) weights.

    Returns a ticker-column DataFrame of composite scores and the weight dict.
    """
    selected_factors = list(selected_summary.index)
    factor_names = list(multi_factor_table.columns.get_level_values(1).unique())
    missing = set(selected_factors) - set(factor_names)
    if missing:
        raise ValueError(f"Selected factors missing from factor table: {sorted(missing)}")

    weights = build_ic_ttest_weights(selected_summary, ic_series=ic_series, shrinkage=shrinkage)
    mixer = FactorMixer(weights=weights)
    composite = mixer.mix_multi_ticker(multi_factor_table)
    # Cross-sectional rank + centre so composite is zero-mean each day
    composite_rank = composite.rank(axis=1, pct=True, method="average")
    composite = composite_rank.sub(composite_rank.mean(axis=1), axis=0)
    composite = composite.mask(composite.abs() < 1e-12, 0.0)
    return composite, weights


def subset_multi_factor_table(
    multi_factor_table: pd.DataFrame,
    factor_names: list,
) -> pd.DataFrame:
    """Keep a factor-name subset while preserving (Ticker, FactorName) MultiIndex columns."""
    if not isinstance(multi_factor_table.columns, pd.MultiIndex):
        raise ValueError("Expected MultiIndex columns: (Ticker, FactorName).")
    subset = {
        factor_name: multi_factor_table.xs(factor_name, level=1, axis=1)
        for factor_name in factor_names
    }
    return pd.concat(subset, axis=1).reorder_levels([1, 0], axis=1).sort_index(axis=1)


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]
    data_root = project_root / "data" / "cleaned"
    csv_files = sorted(data_root.glob("*_cleaned.csv"))
    tickers = [f.name.replace("_cleaned.csv", "") for f in csv_files]

    ticker_data = {}
    for t in tickers:
        # NOTE: do NOT use parse_dates=[0] — mixed DST offsets (-04:00/-05:00)
        # in the saved index make pandas' dtype inference silently blank rows.
        df = pd.read_csv(data_root / f"{t}_cleaned.csv", index_col=0)
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce").tz_convert("America/New_York")
        if not df.empty:
            ticker_data[t] = df

    if len(ticker_data) < 3:
        raise ValueError("Need at least 3 non-empty tickers from data/cleaned.")

    designer = FactorDesigner()
    raw_factors = designer.build_multi_ticker_factor_table(ticker_data)

    analyzer = FactorAnalyzer()
    close_matrix = analyzer.build_close_matrix(ticker_data)
    horizon = 5
    periods_per_year = 252.0 / horizon

    result = analyzer.analyze_factor_ic(
        multi_factor_table=raw_factors,
        close_matrix=close_matrix,
        horizon=horizon,
        method="spearman",
        min_tickers=30,
        max_abs_return=0.30,
        winsorize_q=0.01,
        periods_per_year=periods_per_year,
    )

    selected_summary = select_top_ic_ttest_factors(
        result["ic_series"],
        top_n=15,
        alpha=0.05,
        require_positive_ic=False,
        require_significant=True,
        min_abs_annualized_ic_ir=0.2,
        periods_per_year=periods_per_year,
    )
    if selected_summary.empty:
        raise ValueError("No factors passed the annualized ICIR and t-test filter.")

    composite_df, composite_weights = build_ic_weighted_composite_alpha(
        raw_factors,
        selected_summary,
        ic_series=result["ic_series"],
        shrinkage=0.1,
    )
    composite_multi = pd.concat({"composite_alpha": composite_df}, axis=1).reorder_levels([1, 0], axis=1)
    selected_factors_table = subset_multi_factor_table(raw_factors, list(selected_summary.index))
    all_factors = pd.concat([selected_factors_table, composite_multi], axis=1)

    final_result = analyzer.analyze_factor_ic(
        multi_factor_table=all_factors,
        close_matrix=close_matrix,
        horizon=horizon,
        method="spearman",
        min_tickers=30,
        max_abs_return=0.30,
        winsorize_q=0.01,
        periods_per_year=periods_per_year,
    )
    ttest_summary = ic_t_test(final_result["ic_series"], periods_per_year=periods_per_year)
    weight_series = pd.Series(composite_weights, name="composite_weight")
    selected_output = selected_summary.join(weight_series)

    reports_root = project_root / "data" / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    selected_output.to_csv(reports_root / "top10_alpha_ic_ttest.csv")
    selected_output.to_csv(reports_root / "selected_alpha_ic_ttest.csv")
    composite_df.to_csv(reports_root / "composite_alpha_latest.csv")

    print(f"Tickers: {len(ticker_data)}  |  Horizon: {horizon}d  |  Factors: {len(FactorDesigner.VOLUME_PRICE_ALPHA_FACTORS)}")
    print(f"\nSelected factors (top {len(selected_summary)}):")
    display_cols = ["ic_mean", "ic_std", "ic_ir", "ic_ir_annualized", "t_stat", "p_value", "is_significant"]
    print(selected_output[display_cols].to_string())

    comp_row = ttest_summary.loc[["composite_alpha"]]
    print("\n=== Composite Alpha IC/T-Test ===")
    print(comp_row[display_cols].to_string())

    raw_icir = float(comp_row["ic_ir"].iloc[0])
    ann_icir = float(comp_row["ic_ir_annualized"].iloc[0])
    print(f"\nRaw ICIR  = {raw_icir:.4f}  (target > 0.50)")
    print(f"Ann ICIR  = {ann_icir:.4f}")
    if abs(raw_icir) >= 0.5:
        print("✓ Raw ICIR target MET (>0.50)")
    elif abs(raw_icir) >= 0.35:
        print("~ Raw ICIR close to target (practical ceiling for OHLCV-only US equities)")
    else:
        print("✗ Raw ICIR below target")

    # ── Theoretical maximum ICIR from IC covariance ──────────────────────────
    selected_names = list(selected_summary.index)
    ic_sel = result["ic_series"][selected_names].dropna(how="all")
    mu = ic_sel.mean().values
    cov = ic_sel.cov().values
    tr = np.trace(cov) / len(cov)
    shrunk = 0.3 * cov + 0.7 * tr * np.eye(len(cov))  # mild regularisation
    try:
        icir_theoretical_max = float(np.sqrt(max(mu @ np.linalg.inv(shrunk) @ mu, 0)))
    except Exception:
        icir_theoretical_max = float("nan")
    print(f"Theoretical ICIR ceiling (Σ^{{-1}}μ norm) = {icir_theoretical_max:.4f}")

    print(f"\nSaved reports to: {reports_root}")
