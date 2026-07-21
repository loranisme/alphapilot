# Alpha101 Correlation-Aware OOS Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a causal 12-factor Alpha101 candidate pool and a deterministic hard-cluster/soft-penalty selector, then compare it honestly with the current 13-factor baseline in one fixed A/B/C out-of-sample experiment.

**Architecture:** Keep the existing baseline OOS API intact. Add a research-safe raw-OHLCV loader, an isolated Alpha101 operator/formula module, correlation diagnostics and a new selector, then compose those pieces in a separate ablation runner and reporter. Every fold fits coverage, direction, correlations, clusters, turnover penalties, and weights on IS dates only; all three arms share dates, labels, purge, execution, neutralization, and costs.

**Tech Stack:** Python 3.11+, pandas 2.0+, NumPy 1.26+, SciPy 1.11+, PyYAML 6+, pytest 8+, existing `research_platform` OOS/preprocessing/portfolio/reporting modules.

## Global Constraints

- Use only local/free OHLCV and the existing free industry snapshot; do not add paid data or network runtime dependencies.
- Preserve the current Arm A implementation and results as the baseline.
- Use exactly Alpha101 #2, #7, #12, #17, #21, #22, #30, #34, #35, #40, #46, and #101 from Kakushadze (2016), with attribution in metadata and the report.
- Do not use true VWAP, market cap, fundamental inputs, PIT industry inputs, or delay-0 formulas in the new candidate set.
- Alpha inputs may use information available through date `t`; scores are first executed at `t+1`.
- Do not calendar-fill missing trading dates, future-backfill values, or perform full-sample time-series clipping.
- Prediction horizon, purge, and rebalance interval remain 5 trading days; Soft Neutral strength remains 0.5.
- Use 4 contiguous IS IC blocks, at least 20 valid IC observations per block, 3/4 direction agreement, recent-half agreement, and absolute robust IC at least 0.005.
- Require factor coverage at least 80%, at least 30 names per valid cross-section, and at least 60 valid IS dates for a verified factor-pair correlation.
- Hard factor-value correlation threshold is 0.75; unverified pairs receive a conservative hard edge.
- IC-correlation shrinkage is `0.5 * C + 0.5 * I`; soft redundancy starts above absolute correlation 0.25.
- Estimate rank turnover only at 5-day rebalance dates and use the fixed penalty `1 / (1 + rank_turnover)`.
- Correlation-aware valid folds select 5 or 6 factors, with non-negative weights capped at 20%; invalid folds carry prior targets instead of relaxing rules.
- Use identical 0/5/10/20 bps cost stress for every arm/path.
- OOS outcomes never change formulas, thresholds, fold rules, neutralization, buffers, or costs.
- Existing dirty files and datasets belong to the user; stage only files explicitly listed in this plan.

## File Map

- `research_platform/market_data.py`: causal raw-OHLCV validation/loading and source metadata.
- `factor_section/alpha101.py`: operator library, formula registry, and the 12 curated formulas.
- `research_platform/correlation.py`: factor-value correlation, IC correlation, clusters, and turnover diagnostics.
- `research_platform/selection.py`: correlation-aware selection and deterministic capped weights.
- `research_platform/ablation.py`: Arm A/B/C orchestration with shared folds and invalid-fold state.
- `research_platform/ablation_reporting.py`: fixed tables, research/engineering gates, atomic output, and Markdown report.
- `scripts/run_alpha101_correlation_oos.py`: local real-data input assembly and executable entry point.
- `configs/alpha101_correlation_oos.yaml`: immutable approved experiment configuration.
- `tester/test_research_market_data.py`: raw-data causality and validation tests.
- `tester/test_alpha101_operators.py`: deterministic operator unit tests.
- `tester/test_alpha101_formulas.py`: formula registry, golden, and causality tests.
- `tester/test_factor_correlation.py`: correlation, clustering, and turnover tests.
- `tester/test_correlation_selection.py`: eligibility, weighting, and perturbation tests.
- `tester/test_alpha101_ablation.py`: shared-fold A/B/C and invalid-fold tests.
- `tester/test_alpha101_reporting.py`: gates and exact-output-contract tests.
- `tester/test_alpha101_correlation_real.py`: marked real-data deterministic integration test.
- `README.md`: run command, output interpretation, source attribution, and research boundary.

---

### Task 1: Research-Safe Raw OHLCV View

**Files:**
- Create: `research_platform/market_data.py`
- Create: `tester/test_research_market_data.py`

**Interfaces:**
- `sanitize_research_ohlcv(frame: pd.DataFrame) -> pd.DataFrame`
- `load_research_ohlcv(raw_dir: str | Path, tickers: Iterable[str], start=None, end=None) -> ResearchOHLCVBundle`
- `ResearchOHLCVBundle.frames` maps ticker to sorted, unique, numeric `Open/High/Low/Close/Volume` frames; `.metadata` records files, row counts, invalid rows, date range, and adjustment semantics.

- [ ] **Step 1: Write failing validation and causality tests**

  Cover timezone normalization, duplicate-date resolution, preservation of missing dates, no backward fill, invalid OHLC rows becoming missing/nontradable, non-positive volume becoming missing, and unchanged history after appending future rows.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_research_market_data.py -q`

  Expected: collection fails because `research_platform.market_data` does not exist.

- [ ] **Step 3: Implement the minimal loader**

  Parse dates without synthesizing a calendar. Coerce required fields to numeric, replace infinities, invalidate prices when `low > min(open, close)`, `high < max(open, close)`, `low > high`, or prices are non-positive, and set `volume <= 0` to missing. Slice only after sorting/validation. Raise a clear error when no requested files are usable.

- [ ] **Step 4: Verify GREEN and baseline regression**

  Run: `python -m pytest tester/test_research_market_data.py tester/test_contracts.py tester/test_providers.py -q`

  Expected: all pass; no existing cleaned/raw CSV is modified.

- [ ] **Step 5: Commit**

  ```bash
  git add research_platform/market_data.py tester/test_research_market_data.py
  git commit -m "feat: add research-safe OHLCV loading"
  ```

### Task 2: Alpha101 Operators and Registry Contract

**Files:**
- Create: `factor_section/alpha101.py`
- Create: `tester/test_alpha101_operators.py`

**Interfaces:**
- Cross-sectional: `rank(frame)`.
- Time-series: `delay`, `delta`, `ts_rank`, `ts_sum`, `ts_min`, `ts_max`, `stddev`, `correlation`, `covariance`, `adv`.
- Element-wise: `sign`, `signed_power`, `safe_divide`, `where`.
- Registry types: `AlphaFormula(number, name, inputs, lookback, delay, compute, source)` and `ALPHA101_REGISTRY`.

- [ ] **Step 1: Write failing hand-checkable operator tests**

  Test percentile ranks by date, full-window warm-up, pairwise-valid rolling correlation/covariance, sample-standard-deviation convention, negative-base signed power, zero denominators, ternary preservation of missing conditions, and ADV as rolling dollar volume using the project OHLC proxy documented in metadata.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_alpha101_operators.py -q`

  Expected: collection fails because `factor_section.alpha101` does not exist.

- [ ] **Step 3: Implement operators with strict missing-value semantics**

  Require full windows (`min_periods=window`), floor numeric window arguments, preserve index/columns, replace infinities with missing, never convert missing factor values to zero, and return deterministic floats.

- [ ] **Step 4: Add registry validation**

  Reject duplicate IDs/names, unsupported inputs, delay below 1, and any registry whose IDs differ from `{2, 7, 12, 17, 21, 22, 30, 34, 35, 40, 46, 101}`.

- [ ] **Step 5: Verify GREEN**

  Run: `python -m pytest tester/test_alpha101_operators.py -q`

  Expected: all operator and registry-contract tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git add factor_section/alpha101.py tester/test_alpha101_operators.py
  git commit -m "feat: add causal Alpha101 operators"
  ```

### Task 3: Twelve Curated Alpha101 Formulas

**Files:**
- Modify: `factor_section/alpha101.py`
- Create: `tester/test_alpha101_formulas.py`

**Interfaces:**
- `build_alpha101_factors(bundle: ResearchOHLCVBundle) -> dict[str, pd.DataFrame]`
- Output names are `alpha101_002`, `alpha101_007`, `alpha101_012`, `alpha101_017`, `alpha101_021`, `alpha101_022`, `alpha101_030`, `alpha101_034`, `alpha101_035`, `alpha101_040`, `alpha101_046`, and `alpha101_101`.

- [ ] **Step 1: Write failing registry and golden tests**

  Use a deterministic multi-ticker OHLCV fixture. For each formula assert exact warm-up length, shape/alignment, no infinities, non-constant post-warm-up output, and selected hand-computed points. Assert Alpha #101 is delayed one row before exposure to the experiment because the paper describes it as delay-1.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_alpha101_formulas.py -q`

  Expected: the registry exists but formula compute functions/build function are missing.

- [ ] **Step 3: Implement paper-faithful formulas**

  Translate the twelve formulas from Appendix A using the Task 2 operators. Treat `adv20` as average daily dollar volume using the documented OHLC proxy because true daily VWAP is unavailable. Do not introduce alternate formula choices or tuned windows.

- [ ] **Step 4: Add formula causality tests**

  Perturb all OHLCV after a cutoff and assert every factor through the cutoff is bitwise/equivalently unchanged. Verify the builder refuses missing required columns and produces the exact 12-name registry order.

- [ ] **Step 5: Verify GREEN**

  Run: `python -m pytest tester/test_alpha101_operators.py tester/test_alpha101_formulas.py -q`

  Expected: all tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git add factor_section/alpha101.py tester/test_alpha101_formulas.py
  git commit -m "feat: add curated Alpha101 factor pool"
  ```

### Task 4: IS Correlation and Turnover Diagnostics

**Files:**
- Create: `research_platform/correlation.py`
- Create: `tester/test_factor_correlation.py`

**Interfaces:**
- `factor_value_correlation(factors, dates, directions, min_names=30, min_dates=60) -> CorrelationEstimate`
- `ic_correlation(ic_series, directions, shrinkage=0.5) -> CorrelationEstimate`
- `connected_correlation_clusters(correlation, verified, threshold=0.75) -> pd.DataFrame`
- `factor_rank_turnover(factor, dates, rebalance_interval=5, min_names=30) -> float`
- `CorrelationEstimate.values` and `.verified` are aligned square DataFrames; diagnostics expose common-date counts.

- [ ] **Step 1: Write failing correlation/cluster tests**

  Validate direction-aligned per-date Spearman aggregation by median, absolute-threshold edges, transitive connected components, conservative edges for unverified pairs, deterministic cluster IDs, 50% IC shrinkage, and 5-day percentile-rank turnover.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_factor_correlation.py -q`

  Expected: collection fails because `research_platform.correlation` does not exist.

- [ ] **Step 3: Implement deterministic diagnostics**

  Use only passed IS dates. Require 30 common names for each daily pair; mark pairs verified only with 60 valid dates. Set diagonal values to 1 and diagonal verification true. Sort factor names before graph traversal.

- [ ] **Step 4: Verify GREEN**

  Run: `python -m pytest tester/test_factor_correlation.py -q`

  Expected: all correlation, verification, clustering, and turnover tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add research_platform/correlation.py tester/test_factor_correlation.py
  git commit -m "feat: add factor correlation diagnostics"
  ```

### Task 5: Correlation-Aware IS Selector

**Files:**
- Modify: `research_platform/selection.py`
- Create: `tester/test_correlation_selection.py`

**Interfaces:**
- Add `CorrelationSelectionResult`, preserving the existing `SelectionResult` fields and adding `factor_value_correlation`, `ic_correlation`, `clusters`, and `valid`.
- Add `select_correlation_aware_factors(factors, ic_series, train_dates, min_abs_ic=0.005, min_coverage=0.80, min_names=30, min_block_observations=20, hard_threshold=0.75, min_pair_dates=60, ic_shrinkage=0.5, ic_soft_threshold=0.25, rebalance_interval=5, min_factors=5, max_factors=6, max_weight=0.20)`.

- [ ] **Step 1: Write failing eligibility and representative tests**

  Cover four valid IC blocks, 80% coverage, missing/constant reasons, negative direction alignment, base-quality representative choice, missingness/name tie-breaks, turnover penalty, IC redundancy penalty, 5–6 count, 20% cap, and invalid result when fewer than five independent candidates survive.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_correlation_selection.py -q`

  Expected: import/attribute failure for the new selector.

- [ ] **Step 3: Implement eligibility without changing baseline selection**

  Keep `select_stable_factors` byte-for-behavior compatible. Record explicit `ineligible_reason`. Calculate `base_quality = abs(robust_ic) * agreeing_blocks / 4 * recent_stability_indicator`, choose one representative per hard cluster, then calculate cost-adjusted and soft-correlation-adjusted quality.

- [ ] **Step 4: Implement final selection and weights**

  Sort by adjusted quality descending, missingness ascending, then name. Retain at most six; require at least five. Use existing `capped_simplex_weights` for valid folds and return empty weights with `valid=False` for invalid folds.

- [ ] **Step 5: Add OOS perturbation test**

  Mutate factor values and returns outside `train_dates`; assert factor-value correlations, IC correlations, clusters, directions, selected names, and weights are unchanged.

- [ ] **Step 6: Verify GREEN and baseline regression**

  Run: `python -m pytest tester/test_selection.py tester/test_correlation_selection.py -q`

  Expected: new tests pass and all existing selection tests remain unchanged.

- [ ] **Step 7: Commit**

  ```bash
  git add research_platform/selection.py tester/test_correlation_selection.py
  git commit -m "feat: add correlation-aware factor selection"
  ```

### Task 6: Shared-Fold A/B/C OOS Orchestration

**Files:**
- Create: `research_platform/ablation.py`
- Create: `tester/test_alpha101_ablation.py`

**Interfaces:**
- `AblationConfig` embeds/constructs the existing `OOSConfig` plus fixed correlation settings.
- `AblationArmResult(name, experiment, fold_diagnostics)`.
- `AblationResult(arms, shared_dates, quality)`.
- `run_alpha101_correlation_ablation(existing_factors, alpha101_factors, close, industry, config) -> AblationResult`.

- [ ] **Step 1: Write failing shared-comparison tests**

  Assert Arm A uses current 13 candidates/current selector, Arm B uses current 13/correlation selector, Arm C uses 25/correlation selector; all arms share exact train/test dates, forward labels, purge, universe, paths, cost, and execution settings.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_alpha101_ablation.py -q`

  Expected: collection fails because `research_platform.ablation` does not exist.

- [ ] **Step 3: Implement a selector-injected OOS engine**

  Reuse `compose_oos_score`, industry variants, buffered targets, and portfolio simulation. Generate fold slices once and pass them to all arms. Freeze each correlation-aware fold state before OOS scoring.

- [ ] **Step 4: Implement invalid-fold hold behavior**

  When B/C has fewer than five factors, generate an invalid score segment and ensure buffered portfolio construction carries the last valid target; record `invalid_fold_hold` in fold diagnostics. Never backfill factors or loosen the cap.

- [ ] **Step 5: Add perturbation and baseline-compatibility tests**

  Change only OOS factors/returns and assert B/C fold state does not change. On a deterministic fixture assert Arm A metrics/fold selections equal a direct `run_oos_experiment` call.

- [ ] **Step 6: Verify GREEN**

  Run: `python -m pytest tester/test_oos.py tester/test_alpha101_ablation.py -q`

  Expected: all new tests and existing OOS tests pass.

- [ ] **Step 7: Commit**

  ```bash
  git add research_platform/ablation.py tester/test_alpha101_ablation.py
  git commit -m "feat: add shared-fold factor ablation"
  ```

### Task 7: Ablation Reporting and Immutable Gates

**Files:**
- Create: `research_platform/ablation_reporting.py`
- Create: `tester/test_alpha101_reporting.py`

**Interfaces:**
- `build_ablation_report(result, forward_returns, asset_returns, industry, config) -> tuple[dict[str, pd.DataFrame], dict]`.
- `write_ablation_report(tables, quality, metadata, output_dir) -> list[Path]`.

- [ ] **Step 1: Write failing gate tests**

  Assert exact research gates: C Raw IC >= A Raw; C Soft net Sharpe > A Soft; C Soft turnover <= A Soft; C positive-IC folds >= A; C Soft average maximum industry exposure <= 8%. Assert engineering gates for overlap, valid-fold size, pair threshold, causality/determinism flags, and all four cost levels.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_alpha101_reporting.py -q`

  Expected: collection fails because `research_platform.ablation_reporting` does not exist.

- [ ] **Step 3: Build the full table contract**

  Produce `factor_value_correlation`, `ic_correlation`, `correlation_clusters`, `factor_selection_by_fold`, `candidate_coverage`, `ablation_metrics`, `fold_metrics`, `year_metrics`, `cost_stress`, and `industry_exposure`. Include arm/path/fold keys so every row is auditable.

- [ ] **Step 4: Implement atomic output and explanatory report**

  Write the ten CSVs plus `quality_report.json`, `metadata.json`, and `report.md`. The report must identify redundant clusters, survivors, Alpha101 factors ever selected, and whether any change came from raw signal, turnover, or neutralization. Research-gate FAIL is a successful report outcome, not a reason to mutate parameters.

- [ ] **Step 5: Verify GREEN and deterministic bytes**

  Run: `python -m pytest tester/test_alpha101_reporting.py -q`

  Expected: exact file set, stable ordering, stable JSON serialization, and byte-identical rerun output.

- [ ] **Step 6: Commit**

  ```bash
  git add research_platform/ablation_reporting.py tester/test_alpha101_reporting.py
  git commit -m "feat: report Alpha101 correlation ablation"
  ```

### Task 8: Real-Data Runner, Configuration, and Documentation

**Files:**
- Create: `scripts/run_alpha101_correlation_oos.py`
- Create: `configs/alpha101_correlation_oos.yaml`
- Create: `tester/test_alpha101_correlation_real.py`
- Modify: `README.md`

**Interfaces:**
- `run_alpha101_correlation_validation(output_dir, config_path, project_root, allow_network=False) -> RealAblationValidationResult`.
- Default output: `outputs/alpha101_correlation_oos/`.

- [ ] **Step 1: Write failing small integration and real-data tests**

  The small test verifies raw loader -> current factors -> Alpha101 -> A/B/C -> exact outputs. The `@pytest.mark.real_data` test verifies local input availability, zero overlap, deterministic fingerprints, exact 12-factor registry, exact A/B/C pools, cost levels, and honest gate booleans.

- [ ] **Step 2: Verify RED**

  Run: `python -m pytest tester/test_alpha101_correlation_real.py -q -m real_data`

  Expected: import failure because the runner does not exist.

- [ ] **Step 3: Implement local input assembly and fingerprints**

  Load dates/universe from the same existing experiment anchor, load OHLCV from `data/raw`, build the existing 13 factors with full-sample winsorization disabled, build the 12 Alpha101 factors, align all panels once, construct the existing industry snapshot, and fingerprint raw inputs, factors, config, and classifications.

- [ ] **Step 4: Add fixed YAML and executable main**

  Encode every approved threshold explicitly. Print failed research gates but return success when engineering completes and outputs are valid; reserve nonzero exit for data/engineering failures.

- [ ] **Step 5: Document command and interpretation**

  Add the command, all 13 output files, A/B/C definitions, the free-data/non-PIT limitations, and attribution/link to `101 Formulaic Alphas` (arXiv:1601.00991). State that the implementation is for personal research and that Appendix A rights remain with their owner.

- [ ] **Step 6: Run focused and lightweight suites**

  Run:

  ```bash
  python -m pytest tester/test_research_market_data.py tester/test_alpha101_operators.py tester/test_alpha101_formulas.py tester/test_factor_correlation.py tester/test_correlation_selection.py tester/test_alpha101_ablation.py tester/test_alpha101_reporting.py -q
  python -m pytest -q -k 'not real_data'
  ```

  Expected: all pass with no regressions.

- [ ] **Step 7: Run the full local A/B/C experiment**

  Run: `python scripts/run_alpha101_correlation_oos.py`

  Expected: the exact output contract is written under `outputs/alpha101_correlation_oos/`; engineering gates pass; research gates may honestly PASS or FAIL.

- [ ] **Step 8: Verify real-data integration and reproducibility**

  Run:

  ```bash
  python -m pytest tester/test_alpha101_correlation_real.py -q -m real_data
  python scripts/run_alpha101_correlation_oos.py
  ```

  Expected: real-data test passes and a second run produces the same fingerprints, selections, gates, and output hash.

- [ ] **Step 9: Commit only implementation files**

  ```bash
  git add scripts/run_alpha101_correlation_oos.py configs/alpha101_correlation_oos.yaml tester/test_alpha101_correlation_real.py README.md
  git commit -m "feat: validate Alpha101 correlation OOS experiment"
  ```

### Task 9: Final Evidence and Handoff

**Files:**
- Verify: all files in this plan and `outputs/alpha101_correlation_oos/`

- [ ] **Step 1: Inspect the scoped diff**

  Run: `git diff --stat HEAD~8..HEAD` and `git status --short`.

  Expected: implementation commits contain only plan-listed files; unrelated dirty user files remain unstaged.

- [ ] **Step 2: Run final verification from a fresh process**

  Run:

  ```bash
  python -m pytest -q -k 'not real_data'
  python -m pytest tester/test_alpha101_correlation_real.py -q -m real_data
  python scripts/run_alpha101_correlation_oos.py
  ```

  Expected: tests pass and the runner completes with the exact output set.

- [ ] **Step 3: Review results without OOS retuning**

  Compare A/B/C Raw and Soft IC, net Sharpe, turnover, positive folds, cost stress, and industry exposure. Explain which factors were removed as redundant, which Alpha101 factors survived, and every failed research gate. Do not edit fixed parameters based on these results.

- [ ] **Step 4: Final handoff**

  Report commits, verification commands/results, exact output path, A/B/C metrics, gate outcomes, free-data limitations, and any next experiment that requires a new pre-registered IS hypothesis.
