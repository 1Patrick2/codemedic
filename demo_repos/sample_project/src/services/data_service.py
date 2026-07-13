"""Data service implementation.

Contains deliberately buggy code for CodeMedic investigator to diagnose.

Bug list:
  1. Config.max_retries: type annotation says int, default is str "three"
  2. compute_weighted: NameError — 'weight' not defined (should be 'weights')
"""

from dataclasses import dataclass

from src.utils import add


@dataclass
class Config:
    """Application configuration."""

    debug: bool = False
    timeout: int = 30

    # BUG: type annotation says int, but the default is a string
    max_retries: int = "three"  # <-- BUG: type mismatch


class DataService:
    """Service that processes data using math helpers."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def compute_score(self, values: list[float]) -> float:
        """Compute aggregate score from a list of values."""
        total = 0.0
        for v in values:
            total = add(total, v)
        return total

    def compute_weighted(self, values: list[float], weights: list[float]) -> float:
        """BUG: uses wrong variable name."""
        if len(values) != len(weights):
            raise ValueError("values and weights must have same length")
        # BUG: 'weight' instead of 'weights'
        return sum(v * w for v, w in zip(values, weight))  # <-- BUG: NameError
