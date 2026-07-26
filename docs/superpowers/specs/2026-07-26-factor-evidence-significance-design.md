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

- `n_boot = 2000`, fixed `seed = 42` for reproducibility.
- Each resample studentizes each cell and records the family-wide `max |z|`.
- Empirical p-value: `p_emp = P(noise max|z| >= observed |z|)`.

Two corrections below (studentization; horizon-matched blocks + per-horizon
families) were added during implementation after the naive `max |IC_bar|` version
produced indefensible rankings. Both are recorded here.

**Studentized statistic (correction 1).** Raw `max |IC_bar|` is the wrong scale
when effective sample size varies 44× across horizons (n_eff = 975 at h=1 vs 22 at
h=42). Long-horizon cells swing far more in absolute IC, dominating the max and
inverting the ranking — the first real run had a t=1.6 cell PASS while a t=3.5 cell
was REJECTED. We studentize each cell by its own block-bootstrap SE,
`z = IC_bar / SE_boot`, and take the grid-wide `max |z|`. Taking the max IS the
family-wise correction: it absorbs horizon/factor correlation, so it does not
over-penalize like Bonferroni/BH over 175 nominally-independent hypotheses. (This
is why we chose a bootstrap max-statistic over analytic t + BH.)

**Horizon-matched blocks + per-horizon families (correction 2).** A block shorter
than the horizon cannot capture the autocorrelation overlapping h-day returns
induce (dependence to ~lag h): it underestimates SE and inflates z. At block=10
this manufactured a spurious `min_ret_reversal@h42` PASS that vanished once the
block reached the horizon. But no single global block serves the whole grid — long
horizons need block ≥ horizon, while a large block over-smooths and spuriously
inflates short-horizon cells (h=1 PASS count climbed 4→13 as block grew 10→84). We
therefore run **each horizon as its own family** with `block(h) = max(10, 2h)`,
apply the studentized family-wise max-T across the 25 factors within that horizon,
then **Bonferroni across the 7 horizons** for the headline verdict. Both
`p_horizon` and `p_global` are reported.

**Resampling level.** We resample the *time labels of the already-computed daily IC
series*, not the raw factor↔return alignment. This lets us avoid 2000×175
cross-sectional Spearman recomputations.

**Imposing the null (null-centering).** A plain block bootstrap of an IC series
*preserves* its observed mean, so it does not describe a no-signal world — a
genuinely strong factor's resampled means would cluster around its real edge, not
zero, and it would never reject. We therefore **demean each column before
resampling** (subtract its own mean IC). The bootstrap of the demeaned,
block-resampled series is the sampling distribution of the mean under H0 (mean IC =
0) with the column's autocorrelation and variance structure retained. Taking the
grid-wide max of these H0 deviations is the Westfall–Young family-wise correction.
(Discovered during TDD: without centering, planted-signal tests correctly failed to
PASS.)

Within a horizon family, all factor columns are resampled with the **same** block
index draw per bootstrap iteration, preserving cross-factor correlation under the
null so that the family-wide max is meaningful.

### Verdict
`verdict = PASS if p_global < 0.05 else REJECT`, where `p_global` is the
per-horizon family-wise p Bonferroni-adjusted across the 7 horizons.
`verdict_horizon` (against `p_horizon`) and a `p_global < 0.10` flag are reported
for reference.

### Actual outcome
With both corrections, exactly **1 of 175 cells** clears the global 5% bar:
`alpha101_034@h1` at `p_global = 0.045` (marginal). Every other cell — the entire
existing 13-factor pool at every horizon, and 11 of 12 Alpha101 factors — is
rejected. The single survivor is a horizon-1 daily-reversal signal (highest
turnover, least tradeable). At h=5, where the portfolio pipeline actually operated,
nothing is significant. This defensibly establishes that the current OHLCV pool has
no tradeable cross-sectional alpha, motivating Phase C (new data) over further
Phase A tuning on this pool.

## Module interface

```python
# research_platform/significance.py

def effective_sample_size(n_dates: int, horizon: int) -> float: ...

@dataclass(frozen=True)
class BootstrapNull:              # columns, per-cell se, null_max_z, block, n_boot, seed
    ...

def block_bootstrap_null(
    ic_panel: pd.DataFrame,       # index=date, columns=MultiIndex[(factor, horizon)]
    block: int = 10, n_boot: int = 2000, seed: int = 42,
) -> BootstrapNull:               # studentized family: per-cell SE + null max|z|

def factor_evidence(
    ic_panel: pd.DataFrame, boot: BootstrapNull, p_threshold: float = 0.05,
) -> pd.DataFrame:                # factor,horizon,n_obs,ic_bar,se_boot,z_stat,n_eff,
                                  # t_naive,p_emp,verdict,flag_10pct

def default_block_for_horizon(horizon: int) -> int: ...   # max(10, 2*horizon)

def evaluate_factor_grid(         # the top-level entry Phase A will reuse
    ic_panel: pd.DataFrame,
    block_for_horizon=default_block_for_horizon,
    n_boot: int = 2000, seed: int = 42, p_threshold: float = 0.05,
) -> pd.DataFrame:                # + block, crit_z_5pct, p_horizon, p_global,
                                  # verdict_horizon; verdict/flag_10pct vs p_global
```

`block_bootstrap_null` and `factor_evidence` stay generic (one family, any block);
`evaluate_factor_grid` composes them per horizon with matched blocks and the
across-horizon Bonferroni. All three are pure and unit-tested on synthetic panels.

## Data flow

```
_load_real_inputs(PROJECT_ROOT)                      # 25 factors + close[976×493]
  -> for each (factor, h): ic_series via evaluate_ic(factor, forward_h)
  -> assemble ic_panel (index=date, columns=(factor,horizon))
  -> grid = evaluate_factor_grid(ic_panel, n_boot=2000, seed=42)
       # internally: per horizon h -> slice -> block_bootstrap_null(block(h))
       #             -> factor_evidence -> Bonferroni across horizons
  -> write outputs/factor_evidence/
```

The bootstrap operates on the daily-IC panel, not raw cross-sections.

## Outputs

```
outputs/factor_evidence/
  evidence.csv        175 rows: factor,horizon,n_obs,ic_bar,se_boot,z_stat,n_eff,
                      t_naive,p_emp,verdict,flag_10pct,block,crit_z_5pct,
                      p_horizon,p_global,verdict_horizon
  null_summary.json   n_boot,seed,p_threshold,statistic,correction,
                      block_by_horizon, family_wise_critical_z_5pct_by_horizon
  report.md           method + params; global/per-horizon PASS counts + conclusion;
                      per-factor best-horizon table sorted by p_global; factor×horizon
                      IC_bar heat table; survivorship-bias / current-universe caveats
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
