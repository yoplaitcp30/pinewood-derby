"""Scoring policies: exchangeable without modifying event control or the GUI."""
from fractions import Fraction
from typing import Protocol

from .models import LaneResult


class ScoringMethod(Protocol):
    id: str
    name: str

    def points(self, result: LaneResult, field_size: int) -> Fraction: ...


class PlacementScoring:
    id = "placement"
    name = "Placement points (0–100, higher is better)"

    def points(self, result: LaneResult, field_size: int) -> Fraction:
        # A solo run is a bye worth full points, but not a competitive win.
        return Fraction(100) if field_size == 1 else Fraction(
            100 * (field_size - result.position), field_size - 1)


SCORING_METHODS: dict[str, ScoringMethod] = {"placement": PlacementScoring()}
