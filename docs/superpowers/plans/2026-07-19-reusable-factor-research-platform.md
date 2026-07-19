# Reusable Factor Research Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing factor research code into a reproducible platform with point-in-time data contracts, industry/size neutralization, purged evaluation, tradable portfolio backtests, and structured experiment outputs.

**Architecture:** Add a focused `research_platform` package beside the existing modules, then migrate behavior behind stable typed interfaces while keeping compatibility wrappers in `analysis_section/backtester.py`. Data flows one way from providers and contracts through preprocessing, evaluation, portfolio construction, and reporting; no evaluator writes files or downloads data.

**Tech Stack:** Python 3.11+, pandas 2.0+, NumPy 1.26+, SciPy 1.11+, PyYAML 6+, pytest 8+, stdlib `dataclasses`, `hashlib`, `json`, `argparse`, and `urllib`.

## Global Constraints

- Use only free public data sources in the first release.
- Preserve raw, standardized, and neutralized signal variants.
- Apply historical constituents and classifications by effective date.
- Purge at least `horizon` trading dates between train and test labels; support extra embargo.
- Generate signals at date `t`, execute at `t+1`, and evaluate through `t+h`.
- Never lower statistical or quality gates automatically to make results appear successful.
- Keep existing public entry points working through compatibility wrappers.
- Keep real-data tests separate from the default lightweight test suite.
- Commit only task-owned files; the worktree already contains unrelated user changes and generated data.

---

## File Map

- `pyproject.toml`: package metadata, dependencies, pytest markers, and CLI entry point.
- `research_platform/contracts.py`: validated market, universe, classification, factor, and experiment result objects.
- `research_platform/providers.py`: cached free constituent and SEC classification providers.
- `research_platform/preprocessing.py`: cross-sectional winsorization, standardization, and neutralization.
- `research_platform/evaluation.py`: IC and quantile evaluators plus purged fold generation.
- `research_platform/portfolio.py`: signal-to-weight conversion and cost-aware return simulation.
- `research_platform/config.py`: YAML experiment configuration with validation.
- `research_platform/experiment.py`: orchestration only; composes package services.
- `research_platform/reporting.py`: JSON, CSV, and Markdown output writers.
- `research_platform/cli.py`: command parsing and process exit status.
- `analysis_section/backtester.py`: compatibility imports/wrappers only for migrated APIs.
- `tester/test_contracts.py`, `tester/test_providers.py`, `tester/test_preprocessing.py`, `tester/test_evaluation_platform.py`, `tester/test_portfolio.py`, `tester/test_experiment.py`: focused unit and integration tests.
- `configs/research_platform_example.yaml`: reproducible default experiment.
- `README.md`: installation, data limitations, commands, and output contract.

---

### Task 1: Package Foundation and Validated Data Contracts

**Files:**
- Create: `pyproject.toml`
- Create: `research_platform/__init__.py`
- Create: `research_platform/contracts.py`
- Create: `tester/test_contracts.py`

**Interfaces:**
- Produces: `MarketDataBundle(ohlcv, tradable, source)`, `UniversePanel(membership, source, quality)`, `ClassificationPanel(classification, taxonomy, source, quality)`, `FactorPanel(raw, standardized, neutralized)`, and `ExperimentResult`.
- Consumes: pandas DataFrames indexed by monotonic, unique `DatetimeIndex` values.

- [ ] **Step 1: Write failing contract tests**

```python
import pandas as pd
import pytest
from research_platform.contracts import MarketDataBundle, UniversePanel

def test_market_bundle_rejects_duplicate_dates():
    idx = pd.to_datetime(["2024-01-02", "2024-01-02"])
    frame = pd.DataFrame({("AAPL", "Close"): [100.0, 101.0]}, index=idx)
    with pytest.raises(ValueError, match="unique"):
        MarketDataBundle(frame, source="fixture")

def test_universe_asof_uses_effective_date_without_lookahead():
    membership = pd.DataFrame(
        {"AAPL": [True, False], "MSFT": [False, True]},
        index=pd.to_datetime(["2024-01-02", "2024-02-01"]),
    )
    panel = UniversePanel(membership, source="fixture", quality="verified")
    assert panel.asof("2024-01-31").to_dict() == {"AAPL": True, "MSFT": False}
```

- [ ] **Step 2: Verify the tests fail**

Run: `python -m pytest tester/test_contracts.py -q`  
Expected: FAIL with `ModuleNotFoundError: No module named 'research_platform'`.

- [ ] **Step 3: Implement the package and contracts**

```python
# research_platform/contracts.py
from dataclasses import dataclass, field
from hashlib import sha256
import json
import pandas as pd

def _validate_frame(frame: pd.DataFrame, name: str) -> None:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"{name} index must be a DatetimeIndex")
    if not frame.index.is_unique:
        raise ValueError(f"{name} dates must be unique")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{name} dates must be sorted")

def dataframe_fingerprint(frame: pd.DataFrame) -> str:
    payload = pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    return sha256(payload).hexdigest()

@dataclass(frozen=True)
class MarketDataBundle:
    ohlcv: pd.DataFrame
    source: str
    tradable: pd.DataFrame | None = None
    def __post_init__(self):
        _validate_frame(self.ohlcv, "ohlcv")
    @property
    def fingerprint(self) -> str:
        return dataframe_fingerprint(self.ohlcv)

@dataclass(frozen=True)
class UniversePanel:
    membership: pd.DataFrame
    source: str
    quality: str
    def __post_init__(self):
        _validate_frame(self.membership, "membership")
    def asof(self, date) -> pd.Series:
        eligible = self.membership.loc[:pd.Timestamp(date)]
        if eligible.empty:
            raise KeyError(f"no universe snapshot on or before {date}")
        return eligible.iloc[-1].astype(bool)

@dataclass(frozen=True)
class ClassificationPanel:
    classification: pd.DataFrame
    taxonomy: str
    source: str
    quality: str

@dataclass(frozen=True)
class FactorPanel:
    raw: pd.DataFrame
    standardized: pd.DataFrame | None = None
    neutralized: pd.DataFrame | None = None

@dataclass
class ExperimentResult:
    metrics: dict = field(default_factory=dict)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
```

- [ ] **Step 4: Run focused and baseline tests**

Run: `python -m pytest tester/test_contracts.py -q`  
Expected: PASS.  
Run: `python -m pytest -q -k 'not RealDataIntegration and not IntegrationRealData'`  
Expected: at least the existing 93 tests plus new contract tests PASS.

- [ ] **Step 5: Commit the foundation**

```bash
git add pyproject.toml research_platform/__init__.py research_platform/contracts.py tester/test_contracts.py
git commit -m "feat: add research platform data contracts"
```

### Task 2: Free Point-in-Time Universe and Classification Providers

**Files:**
- Create: `research_platform/providers.py`
- Create: `tester/test_providers.py`

**Interfaces:**
- Consumes: current constituent rows and effective-dated add/remove events; SEC ticker JSON and submission metadata.
- Produces: `rebuild_membership(current, changes, dates) -> DataFrame`, `CachedJsonProvider.fetch(url, cache_name) -> dict`, and `classification_from_records(records) -> ClassificationPanel`.

- [ ] **Step 1: Write failing point-in-time reconstruction tests**

```python
import pandas as pd
from research_platform.providers import rebuild_membership

def test_rebuild_membership_reverses_changes_without_lookahead():
    dates = pd.to_datetime(["2024-01-31", "2024-02-29"])
    changes = pd.DataFrame([{"effective_date": "2024-02-01", "added": "NEW", "removed": "OLD"}])
    result = rebuild_membership({"NEW"}, changes, dates)
    assert result.loc[pd.Timestamp("2024-01-31"), "OLD"]
    assert not result.loc[pd.Timestamp("2024-01-31"), "NEW"]
    assert result.loc[pd.Timestamp("2024-02-29"), "NEW"]
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tester/test_providers.py -q`  
Expected: FAIL because `research_platform.providers` does not exist.

- [ ] **Step 3: Implement deterministic reconstruction and verified caching**

```python
def rebuild_membership(current: set[str], changes: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    events = changes.copy()
    events["effective_date"] = pd.to_datetime(events["effective_date"])
    members = set(current)
    snapshots = {}
    for date in sorted(pd.DatetimeIndex(dates), reverse=True):
        for row in events.loc[events["effective_date"] > date].sort_values("effective_date", ascending=False).itertuples():
            if isinstance(row.added, str): members.discard(row.added)
            if isinstance(row.removed, str): members.add(row.removed)
        snapshots[date] = set(members)
        events = events.loc[events["effective_date"] <= date]
    tickers = sorted(set().union(*snapshots.values(), current))
    return pd.DataFrame({d: [t in snapshots[d] for t in tickers] for d in snapshots}, index=tickers).T.sort_index()
```

Implement `CachedJsonProvider` with an explicit SEC-compatible `User-Agent`, atomic cache replacement, SHA-256 sidecar, and refusal to use a cache whose digest fails. Parse records into effective-dated classification rows; label SEC fallback rows as taxonomy `SIC` and quality `fallback`.

- [ ] **Step 4: Run provider tests without network access**

Run: `python -m pytest tester/test_providers.py -q`  
Expected: PASS using fixtures and temporary directories only.

- [ ] **Step 5: Commit providers**

```bash
git add research_platform/providers.py tester/test_providers.py
git commit -m "feat: add free point-in-time data providers"
```

### Task 3: Cross-Sectional Preprocessing and Industry Neutralization

**Files:**
- Create: `research_platform/preprocessing.py`
- Create: `tester/test_preprocessing.py`

**Interfaces:**
- Consumes: date-by-ticker factor panel, matching classification panel, optional market-cap panel.
- Produces: `standardize_panel(...)`, `neutralize_panel(...) -> NeutralizationResult(values, diagnostics)`.

- [ ] **Step 1: Write a failing synthetic exposure test**

```python
def test_neutralizer_removes_industry_and_size_exposure():
    tickers = [f"T{i}" for i in range(12)]
    industry = pd.Series(["A"] * 6 + ["B"] * 6, index=tickers)
    size = pd.Series(np.linspace(1, 3, 12), index=tickers)
    signal = 3.0 * (industry == "B").astype(float) + 2.0 * size
    result = neutralize_cross_section(signal, industry, size)
    assert abs(result.values.groupby(industry).mean().diff().iloc[-1]) < 1e-10
    assert abs(result.values.corr(np.log(size))) < 1e-10
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tester/test_preprocessing.py -q`  
Expected: FAIL because the neutralizer is missing.

- [ ] **Step 3: Implement date-local preprocessing**

```python
@dataclass(frozen=True)
class NeutralizationResult:
    values: pd.Series
    diagnostics: dict

def neutralize_cross_section(signal, industry, market_cap=None, min_names=10):
    frame = pd.concat({"signal": signal, "industry": industry}, axis=1)
    if market_cap is not None:
        frame["log_size"] = np.log(pd.to_numeric(market_cap, errors="coerce").where(lambda x: x > 0))
    frame = frame.dropna()
    if len(frame) < min_names:
        return NeutralizationResult(pd.Series(np.nan, index=signal.index), {"valid": False, "reason": "insufficient_names"})
    dummies = pd.get_dummies(frame["industry"], drop_first=True, dtype=float)
    columns = [pd.Series(1.0, index=frame.index, name="intercept"), dummies]
    if "log_size" in frame: columns.append(frame[["log_size"]])
    design = pd.concat(columns, axis=1).astype(float)
    beta, *_ = np.linalg.lstsq(design.to_numpy(), frame["signal"].to_numpy(), rcond=None)
    residual = frame["signal"] - design.to_numpy() @ beta
    residual = (residual - residual.mean()) / residual.std(ddof=0)
    output = pd.Series(np.nan, index=signal.index, dtype=float)
    output.loc[residual.index] = residual
    return NeutralizationResult(output, {"valid": True, "coverage": len(frame) / signal.notna().sum(), "rank": int(np.linalg.matrix_rank(design))})
```

Add panel wrappers that winsorize and standardize each date independently, never using full-sample moments.

- [ ] **Step 4: Verify exposure and regression tests**

Run: `python -m pytest tester/test_preprocessing.py -q`  
Expected: PASS, including missing classification, insufficient names, singular exposure, and raw-input immutability tests.

- [ ] **Step 5: Commit preprocessing**

```bash
git add research_platform/preprocessing.py tester/test_preprocessing.py
git commit -m "feat: add industry and size neutralization"
```

### Task 4: Purged Evaluation and Quantile Backtest Extraction

**Files:**
- Create: `research_platform/evaluation.py`
- Create: `tester/test_evaluation_platform.py`
- Modify: `analysis_section/backtester.py`
- Modify: `tester/test_backtester.py`

**Interfaces:**
- Consumes: factor panels, close returns, universe masks, `horizon`, `purge_periods`, and `embargo_periods`.
- Produces: `generate_purged_folds(...)`, `evaluate_ic(...)`, and `run_quantile_backtest(...)`.

- [ ] **Step 1: Write a failing fold-boundary sentinel test**

```python
def test_purged_folds_leave_horizon_gap():
    dates = pd.bdate_range("2023-01-02", periods=80)
    folds = generate_purged_folds(dates, min_train=40, test_size=10, step=10, horizon=5, embargo=2)
    train, test = folds[0]
    assert dates.get_loc(test[0]) - dates.get_loc(train[-1]) >= 6
    assert set(train).isdisjoint(test)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tester/test_evaluation_platform.py -q`  
Expected: FAIL because the evaluator is missing.

- [ ] **Step 3: Implement purged folds and migrate quantile utilities**

```python
def generate_purged_folds(dates, min_train, test_size, step, horizon, embargo=0, max_train=None):
    dates = pd.Index(dates).sort_values().unique()
    folds = []
    test_start = min_train + horizon
    while test_start + test_size <= len(dates):
        train_end = test_start - horizon
        train_start = max(0, train_end - max_train) if max_train else 0
        train = dates[train_start:train_end]
        test = dates[test_start:test_start + test_size]
        if len(train) >= min_train: folds.append((train, test))
        test_start += step + embargo
    return folds
```

Move quantile labeling, group returns, monotonicity, and summaries into `research_platform.evaluation`; keep imports with the old names in `analysis_section/backtester.py`. Change `WalkForwardBacktester._generate_folds` to call `generate_purged_folds` and add `purge_periods`/`embargo_periods` to its configuration.

- [ ] **Step 4: Run evaluator and compatibility tests**

Run: `python -m pytest tester/test_evaluation_platform.py tester/test_backtester.py -q -k 'not RealDataIntegration'`  
Expected: PASS and every fold satisfies the label-gap sentinel.

- [ ] **Step 5: Commit evaluation migration**

```bash
git add research_platform/evaluation.py tester/test_evaluation_platform.py analysis_section/backtester.py tester/test_backtester.py
git commit -m "feat: add purged factor evaluation"
```

### Task 5: Tradable Long-Short Portfolio and Transaction Costs

**Files:**
- Create: `research_platform/portfolio.py`
- Create: `tester/test_portfolio.py`

**Interfaces:**
- Consumes: signal panel, next-day asset returns, optional industry panel, rebalance frequency, weight cap, and one-way cost in basis points.
- Produces: `build_long_short_weights(...)` and `simulate_portfolio(...) -> PortfolioResult`.

- [ ] **Step 1: Write failing accounting tests**

```python
def test_cost_is_turnover_times_one_way_bps():
    weights = pd.DataFrame([[0.5, -0.5], [-0.5, 0.5]], index=pd.bdate_range("2024-01-02", periods=2), columns=["A", "B"])
    returns = pd.DataFrame(0.0, index=weights.index, columns=weights.columns)
    result = simulate_portfolio(weights, returns, cost_bps=10.0)
    assert result.turnover.iloc[1] == pytest.approx(2.0)
    assert result.net_returns.iloc[1] == pytest.approx(-0.002)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tester/test_portfolio.py -q`  
Expected: FAIL because the portfolio module is missing.

- [ ] **Step 3: Implement weights, lag, costs, and metrics**

```python
@dataclass(frozen=True)
class PortfolioResult:
    weights: pd.DataFrame
    gross_returns: pd.Series
    net_returns: pd.Series
    turnover: pd.Series
    metrics: dict

def simulate_portfolio(weights, asset_returns, cost_bps=10.0):
    aligned_w = weights.reindex_like(asset_returns).fillna(0.0)
    executed = aligned_w.shift(1).fillna(0.0)
    gross = (executed * asset_returns).sum(axis=1)
    turnover = executed.diff().abs().sum(axis=1).fillna(executed.abs().sum(axis=1))
    net = gross - turnover * cost_bps / 10_000.0
    wealth = (1.0 + net).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    metrics = {"annualized_return": float(net.mean() * 252), "annualized_volatility": float(net.std() * np.sqrt(252)), "max_drawdown": float(drawdown.min()), "average_turnover": float(turnover.mean())}
    metrics["sharpe"] = metrics["annualized_return"] / metrics["annualized_volatility"] if metrics["annualized_volatility"] else np.nan
    return PortfolioResult(executed, gross, net, turnover, metrics)
```

Implement equal-weight quantile and score-proportional builders with dollar-neutral, industry-neutral, and per-name cap assertions.

- [ ] **Step 4: Run portfolio tests**

Run: `python -m pytest tester/test_portfolio.py -q`  
Expected: PASS for lag, neutrality, cap, turnover, cost, and drawdown tests.

- [ ] **Step 5: Commit portfolio layer**

```bash
git add research_platform/portfolio.py tester/test_portfolio.py
git commit -m "feat: add cost-aware portfolio backtests"
```

### Task 6: Configuration, Experiment Runner, and Structured Reports

**Files:**
- Create: `research_platform/config.py`
- Create: `research_platform/experiment.py`
- Create: `research_platform/reporting.py`
- Create: `tester/test_experiment.py`
- Create: `configs/research_platform_example.yaml`

**Interfaces:**
- Consumes: `ExperimentConfig`, validated contracts, factor builder callable.
- Produces: `run_experiment(config, inputs) -> ExperimentResult` and `write_result(result, output_dir) -> list[Path]`.

- [ ] **Step 1: Write a failing reproducibility test**

```python
def test_writer_emits_required_artifacts(tmp_path, synthetic_result):
    paths = write_result(synthetic_result, tmp_path)
    assert {p.name for p in paths} == {"experiment_summary.json", "factor_diagnostics.csv", "portfolio_metrics.csv", "quality_report.json", "report.md"}
    summary = json.loads((tmp_path / "experiment_summary.json").read_text())
    assert summary["metadata"]["data_fingerprint"] == "fixture-hash"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tester/test_experiment.py -q`  
Expected: FAIL because config, runner, and writer are missing.

- [ ] **Step 3: Implement validated YAML config and atomic reporting**

```python
@dataclass(frozen=True)
class ExperimentConfig:
    horizon: int = 5
    groups: int = 5
    purge_periods: int = 5
    embargo_periods: int = 0
    cost_bps: float = 10.0
    min_classification_coverage: float = 0.90
    seed: int = 42
    def __post_init__(self):
        if self.horizon < 1: raise ValueError("horizon must be positive")
        if self.purge_periods < self.horizon: raise ValueError("purge_periods must be >= horizon")
        if not 0 <= self.min_classification_coverage <= 1: raise ValueError("classification coverage must be within [0, 1]")
```

Implement `run_experiment` as composition only: build variants, evaluate each, simulate portfolios, collect quality gates, and attach config/data/code metadata. Implement atomic output through temporary sibling files followed by `Path.replace`.

- [ ] **Step 4: Run experiment tests twice**

Run: `python -m pytest tester/test_experiment.py -q && python -m pytest tester/test_experiment.py -q`  
Expected: both runs PASS and emitted file hashes are identical for fixed inputs/config.

- [ ] **Step 5: Commit experiment pipeline**

```bash
git add research_platform/config.py research_platform/experiment.py research_platform/reporting.py tester/test_experiment.py configs/research_platform_example.yaml
git commit -m "feat: add reproducible experiment pipeline"
```

### Task 7: CLI, Documentation, and Compatibility Completion

**Files:**
- Create: `research_platform/cli.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `analysis_section/backtester.py`
- Create: `tester/test_cli.py`

**Interfaces:**
- Produces CLI commands `validate-data`, `run-experiment`, and `generate-report`; retains `run_walkforward_backtest` and `run_alpha_stratified_backtest` imports.

- [ ] **Step 1: Write failing CLI smoke tests**

```python
def test_cli_help_lists_commands():
    proc = subprocess.run([sys.executable, "-m", "research_platform.cli", "--help"], text=True, capture_output=True)
    assert proc.returncode == 0
    assert "validate-data" in proc.stdout
    assert "run-experiment" in proc.stdout
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tester/test_cli.py -q`  
Expected: FAIL because the CLI is missing.

- [ ] **Step 3: Implement CLI and user documentation**

```python
def build_parser():
    parser = argparse.ArgumentParser(prog="factor-research")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-data")
    run = sub.add_parser("run-experiment")
    run.add_argument("--config", required=True)
    report = sub.add_parser("generate-report")
    report.add_argument("--result-dir", required=True)
    return parser
```

Document installation, free-data limitations, cache behavior, PIT caveats, example commands, output schemas, raw-versus-neutralized interpretation, and the distinction between IC evaluation and tradable portfolio returns.

- [ ] **Step 4: Run CLI and full lightweight regression**

Run: `python -m pytest tester/test_cli.py -q`  
Expected: PASS.  
Run: `python -m pytest -q -k 'not RealDataIntegration and not IntegrationRealData'`  
Expected: all lightweight tests PASS.

- [ ] **Step 5: Commit CLI and docs**

```bash
git add research_platform/cli.py pyproject.toml README.md analysis_section/backtester.py tester/test_cli.py
git commit -m "docs: add research platform CLI and guide"
```

### Task 8: Real-Data Baseline, Comparison, and Final Quality Gate

**Files:**
- Create: `tester/test_research_platform_real.py`
- Create: `scripts/run_research_platform_validation.py`
- Create at runtime: `outputs/research_platform_validation/*` (ignored, not committed)
- Modify: `.gitignore`

**Interfaces:**
- Consumes current cleaned price data, cached free classifications, and the example config.
- Produces baseline/raw/neutralized/industry-within-group/cost-aware comparison artifacts and a process exit code based on engineering quality gates.

- [ ] **Step 1: Add real-data marker and engineering assertions**

```python
@pytest.mark.real_data
def test_real_neutralization_quality(real_experiment_result):
    assert real_experiment_result.quality["classification_coverage"] >= 0.90
    assert real_experiment_result.quality["max_abs_industry_exposure"] <= 1e-8
    assert real_experiment_result.quality["label_overlap_count"] == 0
    assert real_experiment_result.metadata["data_fingerprint"]
```

- [ ] **Step 2: Run the test before cached classifications exist**

Run: `python -m pytest tester/test_research_platform_real.py -q -m real_data`  
Expected: FAIL with an explicit missing/insufficient classification cache error, not a silent current-universe fallback.

- [ ] **Step 3: Run the free-data preparation and validation script**

```python
def main() -> int:
    result = run_validation(Path("configs/research_platform_example.yaml"))
    paths = write_result(result, Path("outputs/research_platform_validation"))
    failed = [name for name, passed in result.quality["gates"].items() if not passed]
    print(json.dumps({"outputs": [str(p) for p in paths], "failed_gates": failed}, indent=2))
    return 1 if failed else 0
```

Run one network preparation pass with source snapshots, then rerun validation from cache to prove offline reproducibility.

- [ ] **Step 4: Run all verification gates**

Run: `python -m pytest -q -k 'not RealDataIntegration and not IntegrationRealData'`  
Expected: all lightweight tests PASS.  
Run: `python -m pytest tester/test_research_platform_real.py -q -m real_data`  
Expected: all engineering quality gates PASS; performance metrics may be positive or negative.  
Run: `python scripts/run_research_platform_validation.py`  
Expected: exit 0 only when coverage, neutrality, no-leakage, and reproducibility gates pass, and required JSON/CSV/Markdown files exist.

- [ ] **Step 5: Inspect outputs and commit only code/config changes**

```bash
git add .gitignore tester/test_research_platform_real.py scripts/run_research_platform_validation.py
git commit -m "test: validate research platform on real data"
```

Record raw versus neutralized IC, stratified Top-Bottom, gross/net portfolio metrics, coverage, and every failed research metric in the final handoff. Do not change gates or tune factors after seeing OOS results.

---

## Final Verification Checklist

- [ ] `python -m pytest -q -k 'not RealDataIntegration and not IntegrationRealData'` passes.
- [ ] Marked real-data engineering tests pass with no label overlap.
- [ ] Neutralized factor residual exposure is within configured tolerance.
- [ ] Offline rerun produces the same data fingerprint and result hashes.
- [ ] Required JSON, CSV, and Markdown artifacts exist and are readable.
- [ ] `git status --short` contains no accidental additions of raw/cleaned data, caches, or `.superpowers` files.
- [ ] Final report states honestly whether neutralization improved, degraded, or left alpha unchanged.
