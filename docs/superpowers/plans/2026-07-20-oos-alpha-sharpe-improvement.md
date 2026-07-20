# OOS Alpha and Net Sharpe Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a genuinely fold-specific OOS factor composite with stable direction alignment, soft industry neutralization, five-day buffered rebalancing, and cost-aware raw/soft/strict performance reports.

**Architecture:** Add focused selection and OOS orchestration modules inside `research_platform`, extend preprocessing and portfolio modules through stable functions, and leave the dirty legacy backtester untouched except for a final optional compatibility import. Every fold freezes IS-selected factors, directions, and weights before generating OOS scores and portfolios.

**Tech Stack:** Python 3.11+, pandas 2.0+, NumPy 1.26+, SciPy 1.11+, pytest 8+, existing `research_platform` contracts/evaluation/portfolio/reporting modules.

## Global Constraints

- Prediction horizon and rebalance interval are both exactly 5 trading days.
- Purge is at least 5 trading days.
- Soft-neutral strength is fixed at 0.5 before OOS evaluation.
- Factors must agree in direction in at least 3 of 4 IS time blocks and in the recent IS half.
- Absolute robust IC must be at least 0.005.
- Direction-aligned factor weights are non-negative and capped at 20%.
- New positions enter at the top/bottom 20%; existing positions exit only beyond the 30% buffer.
- Single-name absolute portfolio weight is capped at 2%.
- Default one-way transaction cost is 10 bps.
- OOS outcomes never change selection, direction, neutral strength, rebalance timing, buffers, or costs.
- Existing dirty files belong to the user; do not stage unrelated changes.

---

## File Map

- `research_platform/selection.py`: IS time-block stability, direction alignment, and non-negative factor weights.
- `research_platform/preprocessing.py`: industry fitted component plus raw/soft/strict score variants.
- `research_platform/portfolio.py`: five-day buffered targets and hold-on-invalid behavior.
- `research_platform/oos.py`: fold state, OOS score construction, fold concatenation, and three-path execution.
- `research_platform/reporting.py`: fold/year/cost-stress tables and acceptance gates.
- `scripts/run_oos_alpha_validation.py`: real-data validation entry point.
- `tester/test_selection.py`, `tester/test_oos.py`, `tester/test_portfolio_buffered.py`, `tester/test_oos_real.py`: focused tests.
- `configs/oos_alpha_improvement.yaml`: immutable approved parameters.
- `README.md`: new OOS command and output interpretation.

---

### Task 1: Stable IS Factor Selection and Direction Alignment

**Files:**
- Create: `research_platform/selection.py`
- Create: `tester/test_selection.py`

**Interfaces:**
- Consumes: `ic_series: DataFrame[date × factor]` from IS dates only.
- Produces: `SelectionResult(selected, directions, weights, diagnostics)` and `select_stable_factors(ic_series, min_abs_ic=0.005, n_blocks=4, min_agreeing_blocks=3, max_weight=0.20)`.

- [ ] **Step 1: Write failing direction and stability tests**

```python
def test_negative_ic_factor_is_flipped_and_receives_non_negative_weight():
    dates = pd.bdate_range("2020-01-01", periods=80)
    ic = pd.DataFrame({"negative": -0.03, "positive": 0.02}, index=dates)
    result = select_stable_factors(ic, max_weight=0.60)
    assert result.directions == {"negative": -1, "positive": 1}
    assert all(weight >= 0 for weight in result.weights.values())

def test_factor_failing_three_of_four_blocks_is_excluded():
    dates = pd.bdate_range("2020-01-01", periods=80)
    values = np.r_[np.full(20, 0.02), np.full(20, -0.02), np.full(20, 0.02), np.full(20, -0.02)]
    result = select_stable_factors(pd.DataFrame({"unstable": values}, index=dates))
    assert "unstable" not in result.selected
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tester/test_selection.py -q`
Expected: collection fails because `research_platform.selection` does not exist.

- [ ] **Step 3: Implement selection and capped non-negative weighting**

```python
@dataclass(frozen=True)
class SelectionResult:
    selected: tuple[str, ...]
    directions: dict[str, int]
    weights: dict[str, float]
    diagnostics: pd.DataFrame

def select_stable_factors(ic_series, min_abs_ic=0.005, n_blocks=4, min_agreeing_blocks=3, max_weight=0.20):
    blocks = np.array_split(ic_series.sort_index(), n_blocks)
    rows = []
    for factor in ic_series.columns:
        full = float(ic_series[factor].mean())
        direction = 1 if full >= 0 else -1
        block_means = [float(block[factor].mean()) for block in blocks if block[factor].notna().any()]
        agreeing = sum(np.sign(value) == direction for value in block_means)
        recent = float(ic_series[factor].iloc[len(ic_series) // 2:].mean())
        robust_ic = float(np.median(block_means)) if block_means else np.nan
        eligible = len(block_means) >= 3 and agreeing >= min_agreeing_blocks and np.sign(recent) == direction and abs(robust_ic) >= min_abs_ic
        rows.append({"factor": factor, "direction": direction, "robust_ic": robust_ic, "agreeing_blocks": agreeing, "eligible": eligible})
    diagnostics = pd.DataFrame(rows).set_index("factor")
    selected = diagnostics.index[diagnostics["eligible"]].tolist()
    quality = diagnostics.loc[selected, "robust_ic"].abs() * diagnostics.loc[selected, "agreeing_blocks"] / n_blocks
    weights = capped_simplex_weights(quality, cap=max_weight)
    return SelectionResult(tuple(selected), diagnostics.loc[selected, "direction"].astype(int).to_dict(), weights, diagnostics)
```

Implement `capped_simplex_weights` as iterative cap-and-redistribute and raise a clear error when `len(selected) * max_weight < 1`.

- [ ] **Step 4: Verify GREEN and edge cases**

Run: `python -m pytest tester/test_selection.py -q`
Expected: PASS for direction, recent-half mismatch, block instability, minimum IC, cap feasibility, and no mandatory family backfill.

- [ ] **Step 5: Commit**

```bash
git add research_platform/selection.py tester/test_selection.py
git commit -m "feat: add stable OOS factor selection"
```

### Task 2: Raw, Soft, and Strict Industry Score Variants

**Files:**
- Modify: `research_platform/preprocessing.py`
- Create: `tester/test_score_variants.py`

**Interfaces:**
- Consumes: one cross-sectional raw score and industry labels.
- Produces: `IndustryScoreVariants(raw, soft, strict, fitted, diagnostics)` through `build_industry_score_variants(score, industry, soft_strength=0.5, min_names=30)`.

- [ ] **Step 1: Write failing formula tests**

```python
def test_soft_score_removes_exactly_half_fitted_industry_component():
    tickers = [f"T{i}" for i in range(20)]
    industry = pd.Series(["A"] * 10 + ["B"] * 10, index=tickers)
    score = pd.Series(np.r_[np.arange(10), np.arange(10) + 10.0], index=tickers)
    variants = build_industry_score_variants(score, industry, soft_strength=0.5, min_names=10)
    expected = standardize_series(score - 0.5 * variants.fitted)
    pd.testing.assert_series_equal(variants.soft, expected)
    assert variants.strict.groupby(industry).mean().abs().max() < 1e-10
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tester/test_score_variants.py -q`
Expected: FAIL because score variants are undefined.

- [ ] **Step 3: Implement one-date decomposition and panel wrapper**

```python
@dataclass(frozen=True)
class IndustryScoreVariants:
    raw: pd.Series
    soft: pd.Series
    strict: pd.Series
    fitted: pd.Series
    diagnostics: dict

def standardize_series(values):
    result = pd.Series(np.nan, index=values.index, dtype=float)
    valid = values.dropna()
    scale = float(valid.std(ddof=0))
    if len(valid) >= 2 and np.isfinite(scale) and scale > 0:
        result.loc[valid.index] = (valid - valid.mean()) / scale
    return result

def build_industry_score_variants(score, industry, soft_strength=0.5, min_names=30):
    aligned = pd.concat({"score": score, "industry": industry}, axis=1).dropna()
    if len(aligned) < min_names:
        return invalid_score_variants(score.index, "insufficient_names")
    design = pd.get_dummies(aligned["industry"], drop_first=True, dtype=float)
    design.insert(0, "intercept", 1.0)
    beta, *_ = np.linalg.lstsq(design.to_numpy(), aligned["score"].to_numpy(), rcond=None)
    fitted_valid = pd.Series(design.to_numpy() @ beta, index=aligned.index)
    fitted = fitted_valid.reindex(score.index)
    raw = standardize_series(score)
    soft = standardize_series(score - soft_strength * fitted)
    strict = standardize_series(score - fitted)
    return IndustryScoreVariants(raw, soft, strict, fitted, {"valid": True, "coverage": len(aligned) / score.notna().sum()})
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tester/test_score_variants.py tester/test_preprocessing.py -q`
Expected: PASS without changing existing neutralizer behavior.

- [ ] **Step 5: Commit**

```bash
git add research_platform/preprocessing.py tester/test_score_variants.py
git commit -m "feat: add soft industry score variants"
```

### Task 3: Five-Day Buffered Portfolio Execution

**Files:**
- Modify: `research_platform/portfolio.py`
- Create: `tester/test_portfolio_buffered.py`

**Interfaces:**
- Consumes: daily score panel, optional industry panel, rebalance interval 5, entry quantile 0.20, exit quantile 0.30, name cap 0.02.
- Produces: `BufferedTargetResult(targets, diagnostics)` through `build_buffered_targets(...)`.

- [ ] **Step 1: Write failing hold and buffer tests**

```python
def test_invalid_signal_day_holds_previous_target():
    scores = make_ranked_scores(periods=6, names=100)
    scores.iloc[5] = np.nan
    result = build_buffered_targets(scores, rebalance_interval=5, max_weight=0.02)
    pd.testing.assert_series_equal(result.targets.iloc[5], result.targets.iloc[4], check_names=False)
    assert result.diagnostics.loc[scores.index[5], "action"] == "hold_invalid"

def test_existing_name_stays_until_it_leaves_thirty_percent_buffer():
    previous = pd.Series(0.0, index=[f"T{i}" for i in range(100)])
    previous["T90"] = 0.02
    score = pd.Series(np.arange(100), index=previous.index)
    score["T90"] = 75
    target = buffered_cross_section(score, previous, entry_quantile=0.20, exit_quantile=0.30, max_weight=0.02)
    assert target["T90"] > 0
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tester/test_portfolio_buffered.py -q`
Expected: FAIL because buffered target functions are missing.

- [ ] **Step 3: Implement stateful five-day targets**

```python
@dataclass(frozen=True)
class BufferedTargetResult:
    targets: pd.DataFrame
    diagnostics: pd.DataFrame

def build_buffered_targets(scores, rebalance_interval=5, entry_quantile=0.20, exit_quantile=0.30, max_weight=0.02):
    targets = pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    previous = pd.Series(0.0, index=scores.columns)
    rows = []
    for position, date in enumerate(scores.index):
        if position % rebalance_interval != 0:
            targets.loc[date] = previous
            rows.append({"date": date, "action": "hold_schedule"})
            continue
        if scores.loc[date].dropna().nunique() <= 1:
            targets.loc[date] = previous
            rows.append({"date": date, "action": "hold_invalid"})
            continue
        previous = buffered_cross_section(scores.loc[date], previous, entry_quantile, exit_quantile, max_weight)
        targets.loc[date] = previous
        rows.append({"date": date, "action": "rebalance"})
    return BufferedTargetResult(targets, pd.DataFrame(rows).set_index("date"))
```

Implement `buffered_cross_section` with long gross +1, short gross -1, and an explicit infeasibility error if the 2% cap cannot be satisfied.

```python
def buffered_cross_section(score, previous, entry_quantile=0.20, exit_quantile=0.30, max_weight=0.02):
    valid = score.dropna().sort_values()
    count = len(valid)
    entry_n = max(1, int(np.floor(count * entry_quantile)))
    exit_n = max(entry_n, int(np.floor(count * exit_quantile)))
    prior_long = set(previous.index[previous > 0])
    prior_short = set(previous.index[previous < 0])
    long_names = set(valid.nlargest(entry_n).index) | (prior_long & set(valid.nlargest(exit_n).index))
    short_names = set(valid.nsmallest(entry_n).index) | (prior_short & set(valid.nsmallest(exit_n).index))
    if len(long_names) * max_weight < 1 or len(short_names) * max_weight < 1:
        raise ValueError("name cap is infeasible for selected cross-section")
    target = pd.Series(0.0, index=score.index)
    target.loc[list(long_names)] = allocate_capped_equal(long_names, gross=1.0, cap=max_weight)
    target.loc[list(short_names)] = -allocate_capped_equal(short_names, gross=1.0, cap=max_weight)
    return target
```

- [ ] **Step 4: Verify portfolio behavior**

Run: `python -m pytest tester/test_portfolio_buffered.py tester/test_portfolio.py -q`
Expected: PASS for schedule holds, invalid holds, entry/exit buffers, cap, neutrality, and `t+1` accounting.

- [ ] **Step 5: Commit**

```bash
git add research_platform/portfolio.py tester/test_portfolio_buffered.py
git commit -m "feat: add buffered five-day portfolio execution"
```

### Task 4: Fold-Specific OOS Composite Orchestration

**Files:**
- Create: `research_platform/oos.py`
- Create: `tester/test_oos.py`

**Interfaces:**
- Consumes: multi-factor panel, close matrix, industry panel, and immutable `OOSConfig`.
- Produces: `OOSExperimentResult(folds, scores, portfolios, diagnostics, quality)` through `run_oos_experiment(...)`.

- [ ] **Step 1: Write failing OOS freeze test**

```python
def test_changing_test_returns_does_not_change_fold_state():
    inputs = make_oos_fixture()
    original = run_oos_experiment(**inputs)
    changed = inputs["close"].copy()
    changed.loc[original.folds[0].test_dates, :] *= np.linspace(1.0, 2.0, len(original.folds[0].test_dates))[:, None]
    rerun = run_oos_experiment(**{**inputs, "close": changed})
    assert original.folds[0].selection.directions == rerun.folds[0].selection.directions
    assert original.folds[0].selection.weights == rerun.folds[0].selection.weights
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tester/test_oos.py -q`
Expected: FAIL because `research_platform.oos` does not exist.

- [ ] **Step 3: Implement immutable fold state and OOS score construction**

```python
@dataclass(frozen=True)
class OOSConfig:
    horizon: int = 5
    min_train: int = 300
    test_size: int = 50
    step: int = 50
    purge: int = 5
    soft_strength: float = 0.5
    rebalance_interval: int = 5
    cost_bps: float = 10.0

def compose_oos_score(factor_table, dates, selection):
    score = pd.DataFrame(0.0, index=dates, columns=factor_table.columns.get_level_values(0).unique())
    valid_weight = pd.DataFrame(0.0, index=dates, columns=score.columns)
    for factor, weight in selection.weights.items():
        panel = factor_table.xs(factor, level=1, axis=1).reindex(index=dates, columns=score.columns)
        aligned = standardize_panel(panel) * selection.directions[factor]
        score = score.add(aligned.fillna(0.0) * weight, fill_value=0.0)
        valid_weight = valid_weight.add(aligned.notna().astype(float) * weight, fill_value=0.0)
    return score.div(valid_weight.replace(0.0, np.nan))
```

For each fold: calculate IS IC only on train dates, select/freeze state, compose OOS raw scores, derive soft/strict scores, concatenate non-overlapping OOS dates, build buffered targets, and simulate one-day returns.

```python
def make_walkforward_slices(dates, config):
    slices = []
    stop = config.min_train
    while stop + config.purge < len(dates):
        test_start = stop + config.purge
        test_stop = min(test_start + config.test_size, len(dates))
        slices.append((dates[:stop], dates[test_start:test_stop]))
        stop += config.step
    return slices

def assert_no_label_overlap(train_dates, test_dates, horizon):
    if len(train_dates) and len(test_dates):
        gap = test_dates[0] - train_dates[-1]
        if gap < pd.offsets.BDay(horizon + 1):
            raise ValueError("training labels overlap OOS dates")
```

- [ ] **Step 4: Verify fold isolation and concatenation**

Run: `python -m pytest tester/test_oos.py -q`
Expected: PASS for purge gap, OOS freeze, selected direction, no duplicate OOS dates, no forced liquidation, and three score paths.

- [ ] **Step 5: Commit**

```bash
git add research_platform/oos.py tester/test_oos.py
git commit -m "feat: add fold-specific OOS factor composites"
```

### Task 5: OOS Reports, Cost Stress, and Acceptance Gates

**Files:**
- Modify: `research_platform/reporting.py`
- Create: `tester/test_oos_reporting.py`
- Create: `configs/oos_alpha_improvement.yaml`

**Interfaces:**
- Consumes: `OOSExperimentResult` and comparable raw baseline.
- Produces: fold/year metrics, cost stress table, exposure table, and explicit acceptance gate results.

- [ ] **Step 1: Write failing acceptance tests**

```python
def test_acceptance_gates_use_comparable_oos_baseline():
    gates = evaluate_oos_gates(
        raw_sharpe=0.50,
        soft_sharpe=0.60,
        baseline_annual_cost=0.10,
        soft_annual_cost=0.05,
        soft_max_industry_exposure=0.07,
        raw_ic=0.04,
        soft_ic=0.034,
        label_overlap_count=0,
    )
    assert all(gates.values())
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tester/test_oos_reporting.py -q`
Expected: FAIL because OOS report functions are missing.

- [ ] **Step 3: Implement reports and immutable config**

```yaml
horizon: 5
min_train: 300
test_size: 50
step: 50
purge: 5
soft_strength: 0.5
rebalance_interval: 5
entry_quantile: 0.20
exit_quantile: 0.30
factor_weight_cap: 0.20
name_weight_cap: 0.02
cost_bps: 10.0
cost_stress_bps: [0.0, 5.0, 10.0, 20.0]
max_industry_exposure: 0.08
min_soft_ic_retention: 0.80
min_cost_reduction: 0.40
```

Write fold/year/cost/exposure CSV files and add gate details to Markdown. Gate formulas must be direct comparisons, with no tolerance changed after results are observed.

```python
def evaluate_oos_gates(raw_sharpe, soft_sharpe, baseline_annual_cost, soft_annual_cost,
                       soft_max_industry_exposure, raw_ic, soft_ic, label_overlap_count):
    cost_reduction = 1.0 - soft_annual_cost / baseline_annual_cost if baseline_annual_cost > 0 else np.nan
    ic_retention = soft_ic / raw_ic if raw_ic > 0 else np.nan
    return {
        "soft_sharpe_beats_raw": bool(soft_sharpe > raw_sharpe),
        "annual_cost_reduction_at_least_40pct": bool(cost_reduction >= 0.40),
        "industry_exposure_at_most_8pct": bool(soft_max_industry_exposure <= 0.08),
        "soft_ic_retention_at_least_80pct": bool(ic_retention >= 0.80),
        "zero_label_overlap": bool(label_overlap_count == 0),
    }
```

- [ ] **Step 4: Run report tests**

Run: `python -m pytest tester/test_oos_reporting.py tester/test_experiment.py -q`
Expected: PASS and preserve the existing five standard output artifacts.

- [ ] **Step 5: Commit**

```bash
git add research_platform/reporting.py tester/test_oos_reporting.py configs/oos_alpha_improvement.yaml
git commit -m "feat: report OOS alpha acceptance gates"
```

### Task 6: Real-Data OOS Validation and Documentation

**Files:**
- Create: `scripts/run_oos_alpha_validation.py`
- Create: `tester/test_oos_real.py`
- Modify: `README.md`

**Interfaces:**
- Consumes cleaned OHLCV, current factor table, cached industry snapshot, and approved OOS config.
- Produces `outputs/oos_alpha_improvement/` with raw/soft/strict OOS reports and a non-zero process status when engineering gates fail.

- [ ] **Step 1: Write failing real-data engineering test**

```python
@pytest.mark.real_data
def test_real_oos_pipeline_has_no_leakage_and_reproduces(tmp_path):
    first = run_real_oos_validation(output_dir=tmp_path / "first", allow_network=False)
    second = run_real_oos_validation(output_dir=tmp_path / "second", allow_network=False)
    assert first.quality["label_overlap_count"] == 0
    assert first.metadata["data_fingerprint"] == second.metadata["data_fingerprint"]
    assert hash_outputs(tmp_path / "first") == hash_outputs(tmp_path / "second")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tester/test_oos_real.py -q -m real_data`
Expected: FAIL because the validation script is missing.

- [ ] **Step 3: Implement the real runner and README command**

```python
def main() -> int:
    result = run_real_oos_validation(
        config_path=PROJECT_ROOT / "configs" / "oos_alpha_improvement.yaml",
        output_dir=PROJECT_ROOT / "outputs" / "oos_alpha_improvement",
        allow_network=False,
    )
    failed = [name for name, passed in result.quality["gates"].items() if not passed]
    print(json.dumps({"failed_gates": failed, "quality": result.quality}, indent=2))
    return 1 if failed else 0
```

Document the command, raw/soft/strict interpretation, fixed parameters, and rule that a research gate failure is a valid result rather than an implementation failure.

- [ ] **Step 4: Run complete verification**

Run: `python -m pytest -q --disable-warnings -m 'not real_data' -k 'not RealDataIntegration and not IntegrationRealData'`
Expected: all lightweight tests PASS.
Run: `python -m pytest tester/test_oos_real.py -q -m real_data`
Expected: engineering checks PASS; performance gates may report PASS or FAIL honestly.
Run: `python scripts/run_oos_alpha_validation.py`
Expected: produces all OOS artifacts; exit code reflects fixed acceptance gates.

- [ ] **Step 5: Commit code and docs only**

```bash
git add scripts/run_oos_alpha_validation.py tester/test_oos_real.py README.md
git commit -m "test: validate OOS alpha improvements on real data"
```

---

## Final Verification Checklist

- [ ] Every new behavior was observed failing before implementation.
- [ ] All lightweight tests pass without warnings.
- [ ] Real-data engineering checks pass with zero label overlap.
- [ ] Fold state is unchanged when OOS returns are perturbed.
- [ ] Raw, soft, and strict OOS paths share identical folds and execution assumptions.
- [ ] Five-day buffered execution never liquidates solely because a signal date is invalid.
- [ ] Cost stress and industry exposure tables are present.
- [ ] Performance gates report actual PASS/FAIL without parameter search.
- [ ] Generated outputs are deterministic for identical data/config/code.
- [ ] Git staging excludes existing dirty legacy files and generated data.
