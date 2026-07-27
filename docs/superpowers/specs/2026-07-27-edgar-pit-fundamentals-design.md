# EDGAR Point-in-Time Fundamentals (Phase C, step 1)

## Problem

Phase B established that the current OHLCV-only factor pool has no tradeable
cross-sectional alpha surviving honest multiple-testing correction. The natural
next dimension is fundamentals, and the repo already has a complete
`FundamentalFactorDesigner` (value/quality family: earnings_yield, book_to_market,
roe_ttm, gross_profitability, asset_growth_penalty, eps_momentum). But the only
wired data source — yfinance quarterly statements — is too shallow to research:
across all 500 cached tickers the earliest report date is 2024-10, median 6
quarters, zero tickers with >=12 quarters, and the values are restated (not PIT).

A live probe of the SEC EDGAR `companyfacts` XBRL API shows it is dramatically
better: real filing dates (`filed`), ~15-18 years of history (AAPL NetIncomeLoss
back to 2009, 66 calendar-quarter frames CY2008Q2..CY2026Q1), free, no key. This
sub-project builds an EDGAR data path that feeds the existing factor code, then
runs the value/quality family through the Phase B significance gate before any
portfolio work.

## Scope

**In scope:** an EDGAR collector (network) and a pure PIT transform that produces
per-ticker fundamental panels in the exact column shape the existing
`FundamentalFactorDesigner` consumes, plus a diagnostic that runs the resulting
factors through `evaluate_factor_grid`.

**Out of scope (YAGNI):**
- No portfolio backtest, neutralization, or cost model (that is downstream, and
  only if B passes).
- No new fundamental factors — reuse `FundamentalFactorDesigner` unchanged.
- No changes to the yfinance `fundamental_loader.py` (left as-is).
- Not Revenue/analyst/short-interest data — a later step if this clears B.

## What the factor code needs (the output contract)

`FundamentalFactorDesigner` reads these columns from each per-ticker
`DataFrame(index=price_dates, columns=items)`:

| output column | kind | EDGAR concept (first alias wins) |
|---|---|---|
| `Net Income_TTM` | flow, TTM | `NetIncomeLoss` |
| `Gross Profit_TTM` | flow, TTM | `GrossProfit` |
| `Stockholders Equity` | instant | `StockholdersEquity`, `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| `Total Assets` | instant | `Assets` |
| `Share Issued` | instant | `CommonStockSharesOutstanding`, `dei:EntityCommonStockSharesOutstanding` |

Producing exactly these columns lets `build_factor_panels` run unchanged.

## Architecture

```
data_section/edgar_collector.py            NEW — network only
  - ticker -> CIK map from SEC company_tickers.json (cached)
  - GET data.sec.gov/api/xbrl/companyfacts/CIK##########.json
    with required User-Agent, <=10 req/s rate limit, retry/backoff
  - raw JSON cached per ticker under data/edgar_cache/ (gitignored)

research_platform/edgar_fundamentals.py    NEW — pure transform, no network
  - CONCEPT_SPEC: the table above
  - pit_ttm_series(concept_facts, price_dates)     flow -> TTM, PIT
  - pit_instant_series(concept_facts, price_dates) instant, PIT
  - build_pit_fundamental_panel(companyfacts_json, price_dates) -> DataFrame
  - build_pit_fundamentals(raw_by_ticker, price_dates) -> {ticker: DataFrame}

scripts/diag_edgar_fundamental_evidence.py NEW — orchestration
  - collector -> raw JSON (small sample first, then full universe)
  - build_pit_fundamentals -> FundamentalFactorDesigner.build_factor_panels
  - build daily IC panel -> evaluate_factor_grid (the Phase B module)
  - write outputs/edgar_fundamental_evidence/
```

The transform is pure and unit-tested on fixtures; the collector is the only part
that touches the network and is exercised only in an explicitly-marked live check.

## PIT transform (the correctness-critical core)

Each EDGAR fact row carries `start`, `end`, `val`, `filed`, `form`, `fp`, and
sometimes `frame`. `filed` is the actual SEC filing date — the date the market
first knew the value. We use it as the availability date. The transform is
**as-first-reported PIT**: at price-date `t`, only facts with `filed <= t` are
visible.

**Flow items (TTM).** SEC assigns a `frame` like `CY2024Q3` to one canonical
value per calendar quarter, and crucially computes the Q4 calendar quarter even
though a 10-K carries no standalone Q4 income statement (verified on AAPL: Q4
frames present). So:
- Keep facts whose `frame` matches `CY\d{4}Q\d` (one value per calendar quarter).
- For each quarter keep `(quarter_end, filed, val)`.
- `TTM(t)` = sum of the **4 most recent calendar quarters whose `filed <= t`**;
  require all 4 present, else NaN. Forward-filled onto the price calendar (a value
  persists until the next quarter's filing arrives).

**Instant items.** Balance-sheet concepts are point-in-time. At `t`, take the
`val` of the fact with the greatest `end` among those with `filed <= t`
(most-recently-reported balance known by `t`), forward-filled onto price dates.

**Restatements.** Multiple filings can report the same period. Frame rows carry
the first canonical filing's `filed`, which is the correct as-first-reported PIT
choice — we deliberately do not chase later amendments, because a strategy could
only have traded on the originally-reported number. This is stated in the report.

## Sequencing (matches "small-sample validation first")

1. Build collector + transform with **fixture unit tests** (no network) for the
   PIT logic: multiple-filing PIT selection, 4-quarter TTM, a quarter invisible
   before its `filed`, instant latest-balance, concept aliasing.
2. **Live-validate on 10-20 tickers**: confirm alias hits, `filed` alignment,
   history depth (expect ~15y), and non-empty factor panels. Checkpoint for review.
3. Only then crawl the full universe and run the value/quality family through
   `evaluate_factor_grid`. If EDGAR fundamentals do not clear B, that is a cheap,
   defensible negative — same discipline as Phase B.

## Outputs

```
outputs/edgar_fundamental_evidence/
  evidence.csv        6 factors x 7 horizons through the Phase B grid
  null_summary.json   blocks, seeds, per-horizon critical z
  report.md           coverage (tickers, history span), evidence table + verdict,
                      PIT / as-first-reported and survivorship caveats
data/edgar_cache/     raw companyfacts JSON per ticker (gitignored)
```

## Testing

`tester/test_edgar_fundamentals.py`, pure fixtures mimicking companyfacts rows:

1. TTM sums the 4 most recent calendar-quarter frames; steps up on new filings.
2. A quarter is invisible before its `filed` date (no look-ahead).
3. Restatement: two filings of one period -> the value in force at `t` is from the
   latest filing with `filed <= t`.
4. Instant item takes the latest `end` with `filed <= t`.
5. Concept aliasing: falls back to the second alias when the first is absent.
6. Output panel has exactly the five contract columns and the price-date index.

Network collector is covered only by an explicitly-marked live check
(`RealDataIntegration`), skipped by default `pytest -q`, per repo convention.
