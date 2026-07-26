# Factor Evidence Significance Diagnostic (Phase B)

## Problem

The Alpha101 correlation-aware OOS experiment fails every research gate: arm A raw
OOS IC is +0.0022 with a net Sharpe of -0.50. Diagnostics traced this to a
first-order construction problem, but the selector's evidence bar is itself broken:
`min_abs_ic = 0.005` is roughly one quarter of the sampling standard error of a
mean IC on this data, and the "4-block direction agreement" test has ~15 effective
observations per block. The selector therefore picks the highest in-sample IC out
of a pool of factors whose true OOS IC is statistically indistinguishable from zero
— noise mining.

Before fixing construction (Phase A), we must first fix measurement (Phase B): a
statistically defensible answer to "does any factor in the current 25-factor pool
have OOS predictive power that survives multiple-testing correction?"

## Scope

**In scope:** a self-contained statistical significance module and a diagnostic
report over the existing 25 factors × 7 horizons.

**Explicitly out of scope (YAGNI):**
- No changes to `selection.py`, `oos.py`, `portfolio.py`, `ablation.py`.
- No portfolio backtest, no Sharpe, no neutralization paths, no cost model.
- No Phase A construction fixes (separate spec).

The module is designed so Phase A can later import it to replace the selector's
`min_abs_ic` gate, but Phase B ships no such wiring.

## Architecture

Two new files with strict separation of concerns:

```
research_platform/significance.py     pure statistics, no IO, no project coupling
scripts/diag_factor_evidence.py       orchestration + IO + report rendering
tester/test_significance.py           synthetic-data unit tests
```

`significance.py` never loads data and does not know what a "factor" is beyond a
labeled per-date IC series. It is testable entirely on synthetic panels. The `diag_`
script reuses `scripts/run_alpha101_correlation_oos._load_real_inputs` for the
identical 25-factor / close inputs, and `research_platform.evaluation.evaluate_ic`
for the per-date IC computation. This mirrors the existing `run_*` / `diag_*`
script pattern.

## Statistical method

### Point estimate
For factor `f` and horizon `h ∈ {1,2,3,5,10,21,42}`:
`forward_h = close.shift(-h)/close - 1`, then per-date Spearman IC via `evaluate_ic`
(min_names=30), then `IC_bar(f,h) = mean over dates`.

### Effective sample size
Overlapping h-day forward returns are strongly autocorrelated, so the nominal
n≈976 dates overstates independent information. Report
`n_eff(h) = n_dates / h` as the standard IC-overlap haircut, and a naive
`t_naive = IC_bar / se(IC) * sqrt(n_eff)` purely as a human-readable cross-check.
The verdict does NOT come from `t_naive`; it comes from the bootstrap p-value.

### Block bootstrap empirical null (the correction mechanism)
Null hypothesis: the factor has no predictive power for future returns. Realized in
the bootstrap by resampling the time axis of the already-computed per-date IC
series with a circular block bootstrap:

- Block length = 10 days (> half the max horizon; preserves overlap autocorrelation
  and volatility clustering).
- `n_boot = 2000`, fixed `seed = 42` for reproducibility.
- Each resample recomputes `IC_bar` for the entire 25×7 grid and records the
  grid-wide `max |IC_bar|`.
- Empirical p-value: `p_emp(f,h) = P(noise max|IC_bar| >= observed |IC_bar(f,h)|)`.

Taking the grid-wide max IS the multiple-testing correction: it absorbs the
correlation between horizons and between factors, so it does not over-penalize the
way Bonferroni/BH over 175 nominally-independent hypotheses would. This is why we
chose a bootstrap max-statistic over analytic t + BH.

**Resampling level.** We resample the *time labels of the already-computed daily IC
series*, not the raw factor↔return alignment. Shuffling date blocks of the daily IC
series is distributionally equivalent to shuffling the forward-return alignment and
recomputing `IC_bar`, because `IC_bar` is a mean over per-date ICs and the null
breaks the date→return correspondence either way. This equivalence is what lets us
avoid 2000×175 cross-sectional Spearman recomputations; it is documented in the code.

The daily IC series for all 25×7 grid cells are resampled with the **same** block
index draw per bootstrap iteration, preserving cross-factor and cross-horizon
correlation under the null so that the grid-wide max is meaningful.

### Verdict
`verdict = PASS if p_emp < 0.05 else REJECT`. A secondary `p_emp < 0.10` flag is
reported for reference; the default gate is 0.05.

### Honest expected outcome
All 25 factors have naive |t| < 1.5 (strongest: min_ret_reversal at 1.11). After
max-statistic correction, near-certain result is REJECT across the board. Phase B's
value is not finding a winner but *proving*, defensibly, that the current pool has
no usable factor — the key evidence for whether to pursue Phase C (new data).

## Module interface

```python
# research_platform/significance.py

def effective_sample_size(n_dates: int, horizon: int) -> float: ...

def block_bootstrap_null(
    ic_panel: pd.DataFrame,      # index=date, columns=MultiIndex[(factor, horizon)]
    block: int = 10,
    n_boot: int = 2000,
    seed: int = 42,
) -> np.ndarray:                 # shape (n_boot,), grid-wide max|IC_bar| per draw

def factor_evidence(
    ic_panel: pd.DataFrame,
    null_max: np.ndarray,
    n_dates_by_horizon: dict[int, int],
    p_threshold: float = 0.05,
) -> pd.DataFrame:               # columns: factor,horizon,ic_bar,n_eff,t_naive,p_emp,verdict
```

## Data flow

```
_load_real_inputs(PROJECT_ROOT)                      # 25 factors + close[976×493]
  -> for each (factor, h): ic_series via evaluate_ic(factor, forward_h)
  -> assemble ic_panel (index=date, columns=(factor,horizon))
  -> null_max = block_bootstrap_null(ic_panel, 10, 2000, 42)
  -> evidence = factor_evidence(ic_panel, null_max, n_dates_by_horizon)
  -> write outputs/factor_evidence/
```

Bootstrap operates on the daily-IC panel, not raw cross-sections, per the
equivalence above.

## Outputs

```
outputs/factor_evidence/
  evidence.csv        175 rows: factor,horizon,ic_bar,n_eff,t_naive,p_emp,verdict
  null_summary.json   block,n_boot,seed, null quantiles (50/90/95/99%), global critical |IC_bar|
  report.md           method + params; per-factor best-horizon table sorted by p_emp;
                      factor×horizon IC_bar heat table; PASS count (expected 0) + one-line
                      conclusion; explicit survivorship-bias / current-universe disclaimer
```

`report.md` header carries the survivorship-bias warning (universe = current 493
S&P constituents, 2022–2026, which inflates momentum), consistent with the earlier
diagnostic.

## Testing

`tester/test_significance.py`, pure synthetic data, no real loader:

1. Zero signal -> REJECT: random factor vs random forward; p_emp roughly uniform,
   verdict REJECT.
2. Strong planted signal -> PASS: `factor = forward + small noise`; p_emp ~ 0,
   verdict PASS.
3. `n_eff` monotonicity: doubling horizon halves n_eff.
4. Reproducibility: same seed yields identical null array and p-values.
5. Family-wise property: a grid of 175 pure-noise factors yields family-wise false
   positive rate near 5%, not per-hypothesis 5% × 175.

Real-data integration is behind the existing `RealDataIntegration` marker, skipped
by default `pytest -q`, per repo convention.

## Non-goals / follow-up

Phase A will import `significance.py` to replace the selector's `min_abs_ic` gate,
decouple horizon from rebalance interval, and add a turnover budget. That is a
separate spec gated on Phase B's conclusion.
