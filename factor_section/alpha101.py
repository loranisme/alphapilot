"""Causal operators and curated metadata for Alpha101 research formulas.

The source formulas are from Kakushadze (2016), ``101 Formulaic Alphas``:
https://arxiv.org/abs/1601.00991.  They are used here for personal research.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


SOURCE_URL = "https://arxiv.org/abs/1601.00991"
APPROVED_ALPHA_IDS = (2, 7, 12, 17, 21, 22, 30, 34, 35, 40, 46, 101)


def _window(value: int | float) -> int:
    window = int(np.floor(value))
    if window < 1:
        raise ValueError("window must be at least one")
    return window


def _finite(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.astype(float).replace([np.inf, -np.inf], np.nan)


def rank(frame: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank independently on each date."""
    return _finite(frame).rank(axis=1, method="average", pct=True)


def delay(frame: pd.DataFrame, periods: int | float) -> pd.DataFrame:
    return _finite(frame).shift(_window(periods))


def delta(frame: pd.DataFrame, periods: int | float) -> pd.DataFrame:
    clean = _finite(frame)
    return clean.subtract(clean.shift(_window(periods)))


def ts_sum(frame: pd.DataFrame, window: int | float) -> pd.DataFrame:
    size = _window(window)
    return _finite(frame).rolling(size, min_periods=size).sum()


def ts_min(frame: pd.DataFrame, window: int | float) -> pd.DataFrame:
    size = _window(window)
    return _finite(frame).rolling(size, min_periods=size).min()


def ts_max(frame: pd.DataFrame, window: int | float) -> pd.DataFrame:
    size = _window(window)
    return _finite(frame).rolling(size, min_periods=size).max()


def stddev(frame: pd.DataFrame, window: int | float) -> pd.DataFrame:
    size = _window(window)
    return _finite(frame).rolling(size, min_periods=size).std(ddof=1)


def ts_rank(frame: pd.DataFrame, window: int | float) -> pd.DataFrame:
    size = _window(window)

    def last_percentile(values: np.ndarray) -> float:
        return float(pd.Series(values).rank(method="average", pct=True).iloc[-1])

    return _finite(frame).rolling(size, min_periods=size).apply(
        last_percentile, raw=True
    )


def correlation(
    left: pd.DataFrame, right: pd.DataFrame, window: int | float
) -> pd.DataFrame:
    size = _window(window)
    return _finite(left).rolling(size, min_periods=size).corr(_finite(right))


def covariance(
    left: pd.DataFrame, right: pd.DataFrame, window: int | float
) -> pd.DataFrame:
    size = _window(window)
    return _finite(left).rolling(size, min_periods=size).cov(_finite(right), ddof=1)


def sign(frame: pd.DataFrame) -> pd.DataFrame:
    return _finite(frame).map(np.sign)


def signed_power(frame: pd.DataFrame, power) -> pd.DataFrame:
    base = _finite(frame)
    powered = np.sign(base) * np.power(np.abs(base), power)
    return _finite(powered)


def safe_divide(numerator: pd.DataFrame, denominator: pd.DataFrame) -> pd.DataFrame:
    result = _finite(numerator).div(_finite(denominator).replace(0.0, np.nan))
    return _finite(result)


def where(
    condition: pd.DataFrame, if_true: pd.DataFrame, if_false: pd.DataFrame
) -> pd.DataFrame:
    boolean_condition = condition.astype("boolean").fillna(False).astype(bool)
    result = _finite(if_true).where(boolean_condition, _finite(if_false))
    return result.mask(condition.isna())


def adv(
    price: pd.DataFrame, volume: pd.DataFrame, window: int | float
) -> pd.DataFrame:
    """Average dollar volume using the supplied causal price proxy."""
    return ts_sum(_finite(price) * _finite(volume), window).div(_window(window))


@dataclass(frozen=True)
class AlphaFormula:
    number: int
    name: str
    inputs: tuple[str, ...]
    lookback: int
    delay: int
    compute: Callable[..., pd.DataFrame] | None
    source: str = SOURCE_URL


def _constant_like(frame: pd.DataFrame, value: float) -> pd.DataFrame:
    return pd.DataFrame(value, index=frame.index, columns=frame.columns, dtype=float)


def _alpha_002(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    log_volume = np.log(data["volume"].where(data["volume"] > 0))
    intraday = safe_divide(data["close"] - data["open"], data["open"])
    return -correlation(rank(delta(log_volume, 2)), rank(intraday), 6)


def _alpha_007(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    change = delta(data["close"], 7)
    active = -ts_rank(change.abs(), 60) * sign(change)
    fallback = _constant_like(data["close"], -1.0)
    result = where(data["adv20"] < data["volume"], active, fallback)
    return result.mask(data["adv20"].isna() | data["volume"].isna())


def _alpha_012(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return sign(delta(data["volume"], 1)) * (-delta(data["close"], 1))


def _alpha_017(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close_rank = -rank(ts_rank(data["close"], 10))
    acceleration_rank = rank(delta(delta(data["close"], 1), 1))
    activity_rank = rank(ts_rank(safe_divide(data["volume"], data["adv20"]), 5))
    return close_rank * acceleration_rank * activity_rank


def _alpha_021(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = data["close"]
    average_8 = ts_sum(close, 8) / 8.0
    average_2 = ts_sum(close, 2) / 2.0
    volatility_8 = stddev(close, 8)
    activity = safe_divide(data["volume"], data["adv20"])
    positive = _constant_like(close, 1.0)
    negative = _constant_like(close, -1.0)
    result = where(
        average_8 + volatility_8 < average_2,
        negative,
        where(
            average_2 < average_8 - volatility_8,
            positive,
            where(activity >= 1.0, positive, negative),
        ),
    )
    missing = (
        average_8.isna()
        | average_2.isna()
        | volatility_8.isna()
        | activity.isna()
    )
    return result.mask(missing)


def _alpha_022(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    changing_correlation = delta(correlation(data["high"], data["volume"], 5), 5)
    return -(changing_correlation * rank(stddev(data["close"], 20)))


def _alpha_030(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = data["close"]
    direction_sum = (
        sign(close - delay(close, 1))
        + sign(delay(close, 1) - delay(close, 2))
        + sign(delay(close, 2) - delay(close, 3))
    )
    numerator = (1.0 - rank(direction_sum)) * ts_sum(data["volume"], 5)
    return safe_divide(numerator, ts_sum(data["volume"], 20))


def _alpha_034(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    volatility_ratio = safe_divide(
        stddev(data["returns"], 2), stddev(data["returns"], 5)
    )
    return rank(
        (1.0 - rank(volatility_ratio))
        + (1.0 - rank(delta(data["close"], 1)))
    )


def _alpha_035(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return (
        ts_rank(data["volume"], 32)
        * (1.0 - ts_rank((data["close"] + data["high"]) - data["low"], 16))
        * (1.0 - ts_rank(data["returns"], 32))
    )


def _alpha_040(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return -rank(stddev(data["high"], 10)) * correlation(
        data["high"], data["volume"], 10
    )


def _alpha_046(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = data["close"]
    slope_change = (
        (delay(close, 20) - delay(close, 10)) / 10.0
        - (delay(close, 10) - close) / 10.0
    )
    positive = _constant_like(close, 1.0)
    negative = _constant_like(close, -1.0)
    fallback = -(close - delay(close, 1))
    result = where(
        slope_change > 0.25,
        negative,
        where(slope_change < 0.0, positive, fallback),
    )
    return result.mask(slope_change.isna() | fallback.isna())


def _alpha_101(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return safe_divide(
        data["close"] - data["open"],
        (data["high"] - data["low"]) + 0.001,
    )


def _registry_spec(
    number: int,
    inputs: tuple[str, ...],
    lookback: int,
    compute: Callable[..., pd.DataFrame],
) -> AlphaFormula:
    return AlphaFormula(
        number=number,
        name=f"alpha101_{number:03d}",
        inputs=inputs,
        lookback=lookback,
        delay=1,
        compute=compute,
    )


ALPHA101_REGISTRY = {
    2: _registry_spec(2, ("open", "close", "volume"), 8, _alpha_002),
    7: _registry_spec(7, ("close", "volume", "adv20"), 67, _alpha_007),
    12: _registry_spec(12, ("close", "volume"), 2, _alpha_012),
    17: _registry_spec(17, ("close", "volume", "adv20"), 24, _alpha_017),
    21: _registry_spec(21, ("close", "volume", "adv20"), 20, _alpha_021),
    22: _registry_spec(22, ("high", "close", "volume"), 20, _alpha_022),
    30: _registry_spec(30, ("close", "volume"), 20, _alpha_030),
    34: _registry_spec(34, ("close", "returns"), 6, _alpha_034),
    35: _registry_spec(35, ("close", "high", "low", "volume", "returns"), 33, _alpha_035),
    40: _registry_spec(40, ("high", "volume"), 10, _alpha_040),
    46: _registry_spec(46, ("close",), 20, _alpha_046),
    101: _registry_spec(101, ("open", "high", "low", "close"), 1, _alpha_101),
}


def _validate_registry() -> None:
    if tuple(ALPHA101_REGISTRY) != APPROVED_ALPHA_IDS:
        raise ValueError("Alpha101 registry IDs do not match the approved set")
    names = [spec.name for spec in ALPHA101_REGISTRY.values()]
    if len(names) != len(set(names)):
        raise ValueError("Alpha101 registry names must be unique")
    if any(spec.delay < 1 for spec in ALPHA101_REGISTRY.values()):
        raise ValueError("delay-0 Alpha101 formulas are not permitted")


_validate_registry()


def _input_panels(bundle) -> dict[str, pd.DataFrame]:
    if not bundle.frames:
        raise ValueError("OHLCV bundle must contain at least one ticker")
    tickers = pd.Index(bundle.frames.keys())
    dates = pd.DatetimeIndex([])
    for frame in bundle.frames.values():
        missing = {"Open", "High", "Low", "Close", "Volume"}.difference(frame.columns)
        if missing:
            raise ValueError(f"missing required OHLCV columns: {sorted(missing)}")
        dates = dates.union(pd.DatetimeIndex(frame.index))
    dates = dates.sort_values()

    panels: dict[str, pd.DataFrame] = {}
    for field in ("Open", "High", "Low", "Close", "Volume"):
        panels[field.lower()] = pd.DataFrame(
            {
                ticker: bundle.frames[ticker][field].reindex(dates)
                for ticker in tickers
            },
            index=dates,
            columns=tickers,
            dtype=float,
        ).replace([np.inf, -np.inf], np.nan)

    price_proxy = (
        panels["open"] + panels["high"] + panels["low"] + panels["close"]
    ) / 4.0
    panels["reported_volume"] = panels["volume"]
    panels["volume"] = price_proxy * panels["reported_volume"]
    panels["adv20"] = adv(_constant_like(price_proxy, 1.0), panels["volume"], 20)
    panels["returns"] = panels["close"].pct_change(fill_method=None)
    return panels


def build_alpha101_factors(bundle) -> dict[str, pd.DataFrame]:
    """Build the fixed 12-formula Alpha101 pool from one causal OHLCV bundle.

    Yahoo-style reported share volume is converted to OHLC4 dollar volume so
    both ``volume`` and ``adv20`` follow the paper's dollar-volume semantics.
    Portfolio execution applies the required one-day delay to all signals.
    """
    data = _input_panels(bundle)
    factors: dict[str, pd.DataFrame] = {}
    for spec in ALPHA101_REGISTRY.values():
        if spec.compute is None:
            raise RuntimeError(f"formula {spec.name} has no compute function")
        factors[spec.name] = _finite(spec.compute(data)).reindex(
            index=data["close"].index, columns=data["close"].columns
        )
    return factors
