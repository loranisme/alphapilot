"""Validated experiment configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ExperimentConfig:
    horizon: int = 5
    groups: int = 5
    purge_periods: int = 5
    embargo_periods: int = 0
    cost_bps: float = 10.0
    min_names: int = 30
    quantile: float = 0.2
    max_weight: float = 0.05
    classification_coverage: float = 0.90
    seed: int = 42

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise ValueError("horizon must be positive")
        if self.groups < 2:
            raise ValueError("groups must be at least 2")
        if self.purge_periods < self.horizon:
            raise ValueError("purge_periods must be >= horizon")
        if self.embargo_periods < 0:
            raise ValueError("embargo_periods must be non-negative")
        if self.cost_bps < 0:
            raise ValueError("cost_bps must be non-negative")
        if self.min_names < 2:
            raise ValueError("min_names must be at least 2")
        if not 0 < self.quantile <= 0.5:
            raise ValueError("quantile must be within (0, 0.5]")
        if not 0 < self.max_weight <= 1:
            raise ValueError("max_weight must be within (0, 1]")
        if not 0 <= self.classification_coverage <= 1:
            raise ValueError("classification_coverage must be within [0, 1]")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise TypeError("experiment config must be a YAML mapping")
        return cls(**payload)

