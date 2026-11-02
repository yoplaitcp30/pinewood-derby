"""Shared qualification and placement tiebreak policy: balanced lane cycles."""
from typing import Protocol
from collections.abc import Sequence

from .models import Assignment, RaceError
from .scheduling import generate_schedule


class TiebreakPolicy(Protocol):
    def schedule(self, racer_ids: Sequence[str], lanes: int, round_number: int) -> tuple[tuple[Assignment, ...], ...]: ...


class BalancedLaneTiebreak:
    """One actual run in every lane per round; no arbitrary deciding lane."""
    def schedule(self, racer_ids: Sequence[str], lanes: int, round_number: int) -> tuple[tuple[Assignment, ...], ...]:
        if len(racer_ids) < 2:
            raise RaceError('A tiebreak requires at least two tied racers.')
        return generate_schedule(racer_ids, lanes, lanes, seed=round_number - 1)
