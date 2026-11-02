"""Finals policy and explicit resolution of ties at the qualification cutoff."""
from dataclasses import dataclass
from collections.abc import Sequence
from itertools import groupby

from .models import RaceError, Standing


@dataclass(frozen=True)
class Qualification:
    automatic: tuple[str, ...]
    tied: tuple[str, ...]
    places: int
    pending_round: int | None = None


@dataclass(frozen=True)
class Placements:
    groups: tuple[tuple[str, ...], ...]
    pending_round: int | None = None

    @property
    def tied(self) -> tuple[str, ...]:
        return next((group for group in self.groups if len(group) > 1), ())


def placement_groups(standings: Sequence[Standing]) -> tuple[tuple[str, ...], ...]:
    """Keep equal sporting scores together; display order never decides a place."""
    return tuple(tuple(row.racer.id for row in group)
                 for _, group in groupby(standings, key=lambda row: row.sporting_key))


def qualification(standings: Sequence[Standing], count: int) -> Qualification:
    if not 1 <= count <= len(standings):
        raise RaceError("Finalist count must be between 1 and the number of racers.")
    cutoff = standings[count - 1].sporting_key
    automatic = tuple(row.racer.id for row in standings if row.sporting_key > cutoff)
    tied = tuple(row.racer.id for row in standings if row.sporting_key == cutoff)
    needed = count - len(automatic)
    if len(tied) == needed:
        return Qualification(automatic + tied, (), 0)
    return Qualification(automatic, tied, needed)


def select_qualifiers(plan: Qualification, selected: Sequence[str] = ()) -> tuple[str, ...]:
    if len(selected) != plan.places or len(set(selected)) != len(selected) or not set(selected) <= set(plan.tied):
        raise RaceError(f"Select exactly {plan.places} racers from the tied qualification group.")
    return plan.automatic + tuple(selected)
