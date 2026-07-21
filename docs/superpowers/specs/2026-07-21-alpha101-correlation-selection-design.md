# Alpha101 Correlation-Aware OOS Factor Expansion Design

## 1. Objective

Improve the current factor research process by adding a curated, causally computable subset of Alpha101 factors and a fold-local correlation-aware selection layer. The experiment must distinguish the effect of correlation pruning from the effect of expanding the candidate pool.

The primary outcome is an auditable A/B/C OOS comparison, not a guaranteed Sharpe improvement. OOS results must not be used to change thresholds, formula membership, or weights within this experiment.

## 2. Scope

This project includes:

- a research-safe OHLCV view that does not backfill from the future or apply full-sample time-series clipping;
- reusable Alpha101 daily operators;
- 12 curated delay-1 Alpha101 formulas that can be computed faithfully from available OHLCV;
- factor-value correlation diagnostics;
- IC-series correlation diagnostics;
- fold-local hard correlation pruning and soft redundancy penalties;
- turnover-aware IS quality scores;
- A/B/C OOS ablation reports under identical execution assumptions.

This project excludes:

- implementing all 101 formulas;
- formulas requiring true intraday VWAP, historical point-in-time market cap, or unavailable point-in-time industry classifications;
- Alpha101 formulas identified as delay-0;
- SEC Company Facts ingestion and fundamental-factor expansion;
- changing the approved 5-day horizon, rebalance interval, transaction cost, soft-neutral strength, or name-weight cap;
- selecting parameters after viewing the new OOS result.

SEC point-in-time fundamental expansion is a separate follow-on project so its effect is not confounded with correlation pruning and Alpha101 expansion.

## 3. Source and Research Boundary

The formula source is Zura Kakushadze, *101 Formulaic Alphas*, arXiv:1601.00991:

- abstract and publication record: <https://arxiv.org/abs/1601.00991>;
- formula appendix, operator definitions, input definitions, delay discussion, and disclaimer: <https://arxiv.org/pdf/1601.00991>.

The paper reports average holding periods of approximately 0.6 to 6.4 days and average pairwise alpha correlation of 15.9% in its proprietary sample. Those results motivate testing at the project's 5-day horizon but are not assumed to transfer to the current universe or period.

This is a personal research implementation. The repository will preserve source attribution and will not claim that the formulas or historical performance are original to this project.

## 4. Architecture and Responsibilities

### 4.1 `factor_section/factor_design.py`

Continue to calculate the existing 13 technical factors. It does not delete, select, cluster, or weight factors.

### 4.2 `factor_section/alpha101.py`

Own the reusable Alpha101 operator library, formula registry, formula metadata, and the 12 approved formula implementations.

Each formula registry entry records:

- alpha identifier and local descriptive name;
- required input fields;
- maximum lookback;
- delay class;
- economic interpretation;
- VWAP, industry, and market-cap requirements;
- whether it is approved for the primary experiment.

### 4.3 `factor_section/factor_analysis.py`

Produce research diagnostics:

- date-local cross-sectional factor-value correlation matrices aggregated over IS dates;
- direction-aligned IC-series correlation matrices;
- factor coverage and invalid-value diagnostics;
- correlation-cluster membership tables.

These diagnostics do not make full-sample production selections.

### 4.4 `research_platform/selection.py`

Extend fold-local selection with:

- existing IC stability and direction rules;
- hard factor-value correlation clustering;
- representative selection within each cluster;
- IS rank-turnover penalty;
- soft IC-correlation redundancy penalty;
- deterministic top-6 selection and capped nonnegative weights.

### 4.5 `research_platform/oos.py`

For every fold:

1. use only IS dates to compute IC stability, factor-value correlation, IC correlation, and rank turnover;
2. freeze selected representatives, directions, and weights;
3. apply the frozen state to the fold's OOS dates;
4. generate Raw, Soft Neutral, and Strict Neutral scores;
5. execute the existing 5-day buffered portfolio workflow;
6. concatenate non-overlapping OOS outputs.

Changing any OOS return or OOS factor value must not change the corresponding fold state.

## 5. Research-Safe OHLCV View

The existing cleaning path can perform full-sample IQR clipping, future backfill, and artificial business-day filling. Those transformations are not permitted for the new Alpha101 workflow because rolling deltas, correlations, covariances, and volume changes are sensitive to them.

The research-safe view must:

- parse and sort real observed dates;
- preserve missing trading dates as missing;
- never use backward fill;
- avoid full-sample time-series winsorization;
- mark invalid OHLC rows non-tradable instead of synthesizing historical movements;
- convert nonpositive volume to missing;
- record whether price adjustment semantics are known;
- perform any clipping or standardization only cross-sectionally on the current date;
- use only data available on or before the signal date.

Existing cleaned files and user changes are not overwritten.

## 6. Alpha101 Operator Semantics

The operator layer provides:

- cross-sectional `rank`;
- time-series `ts_rank`;
- `delay` and `delta`;
- `ts_sum`, `ts_min`, and `ts_max`;
- rolling `stddev`, `correlation`, and `covariance`;
- `sign` and `signed_power`;
- average daily dollar volume `adv`;
- deterministic conditional expressions.

Rules:

- rolling values require the complete declared window;
- operators never shorten a window based on available observations;
- invalid division and infinite values become missing;
- missing values are not replaced by zero;
- cross-sectional normalization happens after formula calculation;
- a signal calculated after date `t` close can first be executed on `t+1`.

## 7. Curated Alpha101 Candidate Set

The primary experiment adds exactly these 12 candidates:

| Alpha | Intended incremental structure |
|---|---|
| #2 | Volume change versus intraday return correlation |
| #7 | Relative-volume-conditioned price movement |
| #12 | Volume direction and price-reversal interaction |
| #17 | Price acceleration and relative-volume interaction |
| #21 | Moving-average, volatility, and volume regime switch |
| #22 | Change in price-volume correlation scaled by volatility |
| #30 | Directional price streak and volume ratio |
| #34 | Short/medium volatility ratio plus price change |
| #35 | Volume, price-range, and return-rank interaction |
| #40 | High-price volatility and high-volume correlation |
| #46 | Trend-acceleration regime signal |
| #101 | Intraday return normalized by daily range |

The set is frozen before the new OOS run. It excludes true-VWAP formulas, historical-cap formulas, PIT-industry-dependent formulas, and the paper's delay-0 formulas #42, #48, #53, and #54.

The expanded candidate pool is the existing 13 technical factors plus these 12 Alpha101 factors. Passing the candidate registry does not guarantee selection in any fold.

## 8. Eligibility and Stability Gates

A candidate is eligible in a fold only when all conditions hold on IS dates:

- at least 80% of otherwise eligible IS dates contain a valid cross-section;
- each valid cross-section has at least 30 names;
- no infinite values remain;
- the cross-section is not constant;
- each of the four contiguous IS blocks contains at least 20 valid daily IC observations;
- at least three of four block IC means agree in direction;
- the recent IS half agrees with the full-IS direction;
- absolute robust IC is at least 0.005.

Negative-IC factors are direction-aligned before correlation and weighting.

An ineligible candidate remains visible in diagnostics with an explicit reason. No factor-family backfill or threshold relaxation is allowed.

## 9. Hard Factor-Value Correlation Pruning

For every eligible factor pair and each IS date:

1. standardize and direction-align the factor cross-sections;
2. compute cross-sectional Spearman correlation using at least 30 valid common names;
3. aggregate the daily correlations with their median;
4. use absolute median correlation as the redundancy measure.

Create an undirected edge when:

```text
abs(median_cross_sectional_correlation) >= 0.75
```

Connected components form hard redundancy clusters. This makes clustering deterministic and prevents order-dependent pair deletion. It is intentionally conservative: transitive high-correlation chains share a single cluster.

A pairwise estimate is verified only with at least 60 valid IS dates. An unverified pair is conservatively connected by an edge, so two factors with insufficient joint evidence cannot both survive the same fold.

Within each cluster, retain the factor with the highest base IS quality:

```text
base_quality =
    abs(robust_ic)
    * agreeing_blocks / 4
    * recent_stability_indicator
```

Ties are resolved deterministically by lower missingness, then stable factor name ordering.

## 10. IS Turnover Penalty

For every cluster representative, estimate rank turnover at the approved 5-day rebalance dates:

```text
rank_turnover = mean absolute change in cross-sectional percentile rank
cost_adjusted_quality = base_quality / (1 + rank_turnover)
```

This is a fixed, moderate penalty. It cannot make a factor eligible if the factor failed the predictive stability gates.

## 11. Soft IC-Correlation Penalty and Final Weights

Use direction-aligned IS daily IC series for the hard-pruned representatives. Apply fixed 50% diagonal shrinkage:

```text
C_shrunk = 0.5 * C + 0.5 * I
```

For each representative:

```text
redundancy_penalty_i =
    1 + sum_{j != i}(max(abs(C_shrunk[i, j]) - 0.25, 0))

adjusted_quality_i =
    cost_adjusted_quality_i / redundancy_penalty_i
```

Select at most the six highest adjusted-quality representatives. At least five are required because the existing factor-weight cap is 20% and weights must sum to one.

Final weights are nonnegative, normalized, and individually capped at 20% using the existing capped-simplex allocation. If fewer than five independent eligible representatives remain, the fold is marked invalid and the portfolio holds its prior target rather than relaxing the rules.

## 12. A/B/C Experiment

All experiment arms share identical dates, folds, labels, purge, universe, execution timing, buffers, costs, and report definitions.

| Arm | Candidate pool | Selector |
|---|---|---|
| A | Existing 13 technical factors | Existing stability selector |
| B | Existing 13 technical factors | Correlation-aware selector |
| C | Existing 13 plus 12 curated Alpha101 factors | Correlation-aware selector |

Each arm produces Raw, Soft Neutral, and Strict Neutral paths. The primary research comparison is C Soft versus A Soft, while Raw IC is used to diagnose signal quality before neutralization.

## 13. Acceptance Gates

### 13.1 Engineering gates

- zero train-label overlap with OOS dates;
- OOS perturbations do not change fold correlation matrices, clusters, directions, or weights;
- every valid B or C fold selects five or six factors; Arm A retains its existing selector for a comparable baseline;
- no selected factor pair breaches the fixed hard-correlation rule in its IS diagnostics;
- Alpha101 signals are causally unchanged when future OHLCV is perturbed;
- generated outputs are deterministic for identical data, config, and code;
- existing user-modified legacy files and datasets are not overwritten or staged unintentionally.

### 13.2 Research gates

- C Raw OOS IC is not below A Raw OOS IC;
- C Soft net Sharpe is above A Soft net Sharpe;
- C Soft average turnover is not above A Soft average turnover;
- C has at least as many positive-IC folds as A;
- C Soft average maximum industry exposure is at most 8%;
- all paths include 0, 5, 10, and 20 bps cost stress.

Research-gate failure is a valid result and must not trigger parameter search on the same OOS data.

## 14. Error Handling

- Unsupported or unavailable formula inputs produce an explicit registry error before experiment execution.
- Insufficient formula coverage marks that factor ineligible with a reason.
- Fewer than 60 valid pairwise IS dates do not default to zero correlation; the pair is marked unverified and receives a hard-cluster edge.
- Numerical infinities become missing and count against coverage.
- An invalid fold carries forward existing portfolio targets.
- Reporting failures must not silently discard completed metrics; structured intermediate tables are written atomically.

## 15. Test Strategy

### 15.1 Operator unit tests

Use small deterministic arrays to verify rank, delay, delta, rolling windows, correlation, covariance, signed power, conditional expressions, and ADV.

### 15.2 Formula golden tests

Each of the 12 formulas receives a hand-checkable OHLCV fixture covering normal output, warm-up periods, missing data, zero denominators, and direction.

### 15.3 Causality tests

Mutating future OHLCV must not alter earlier research-safe data, operator output, formula output, fold correlations, cluster membership, directions, or weights.

### 15.4 Correlation-selection tests

Synthetic factors validate:

- high-correlation connected components;
- representative selection;
- deterministic tie-breaking;
- IS turnover penalties;
- IC-correlation soft penalties;
- five-to-six factor limits;
- invalid-fold hold behavior.

### 15.5 Real-data integration tests

Run A/B/C on the same local free-data snapshot and verify data fingerprints, zero overlap, output reproducibility, and honest research-gate results.

## 16. Outputs

Write to `outputs/alpha101_correlation_oos/`:

- `factor_value_correlation.csv`;
- `ic_correlation.csv`;
- `correlation_clusters.csv`;
- `factor_selection_by_fold.csv`;
- `candidate_coverage.csv`;
- `ablation_metrics.csv`;
- `fold_metrics.csv`;
- `year_metrics.csv`;
- `cost_stress.csv`;
- `industry_exposure.csv`;
- `quality_report.json`;
- `metadata.json`;
- `report.md`.

The report must show which factors were redundant, which representative survived each cluster, which Alpha101 candidates ever entered OOS, and whether improvements came from signal quality, turnover, or neutralization.

## 17. Fixed Decisions

- Personal research use with source attribution.
- Correlation handling uses hard factor-value clustering plus soft IC-correlation penalties.
- Hard correlation threshold is 0.75.
- Soft IC redundancy starts above 0.25.
- IC matrix diagonal shrinkage is 50%.
- Rank-turnover penalty is `1 / (1 + rank_turnover)`.
- Candidate set contains the existing 13 factors and exactly 12 curated Alpha101 formulas.
- Final valid fold size is five or six factors.
- Factor cap remains 20%.
- Horizon and rebalance interval remain five trading days.
- Signal is generated at `t` and executed at `t+1`.
- One-way transaction cost remains 10 bps, with 0/5/10/20 bps stress reporting.
- Soft-neutral strength remains 0.5.
- OOS outcomes do not modify any decision above.
