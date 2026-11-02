"""Integration contracts. No device I/O or Qt dependencies belong here."""
from collections.abc import Mapping, Sequence
from typing import Protocol

from .models import Heat, LaneResult


class ResultSource(Protocol):
    def obtain_results(self, heat: Heat) -> Sequence[LaneResult]:
        """Return a complete heat result; adapters translate device data here."""
        ...


class LaneIdentifier(Protocol):
    def identify(self, heat: Heat) -> Mapping[int, str | None]:
        """Map occupied lanes to internal racer IDs, or None for unknown cars."""
        ...


class ManualResultSource:
    def __init__(self, results: Sequence[LaneResult]):
        self.results = tuple(results)

    def obtain_results(self, heat: Heat) -> Sequence[LaneResult]:
        return self.results


class ScheduledLaneIdentifier:
    def identify(self, heat: Heat) -> Mapping[int, str | None]:
        return {a.lane: a.racer_id for a in heat.assignments}


def lane_mismatches(heat: Heat, detected: Mapping[int, str | None]) -> list[str]:
    expected = {a.lane: a.racer_id for a in heat.assignments}
    return [f"Lane {lane}: expected {expected.get(lane) or 'empty'}, detected {detected.get(lane) or 'unknown/empty'}."
            for lane in sorted(expected.keys() | detected.keys())
            if expected.get(lane) != detected.get(lane)]
