"""Domain types. This module deliberately has no GUI or persistence imports."""
from dataclasses import dataclass
from typing import Literal

Stage = Literal["preliminary", "tiebreak", "finals"]


class RaceError(ValueError):
    """An operator-correctable validation or workflow error."""


@dataclass(frozen=True)
class Event:
    id: str
    name: str
    lanes: int
    runs: int
    finalists: int
    scoring: str = "placement"
    current_heat_id: str | None = None
    status: str = "registration"
    late_policy_kind: str = "last"
    late_policy_value: str | None = None


@dataclass(frozen=True)
class Racer:
    id: str
    event_id: str
    name: str
    car_name: str
    car_number: str
    tag: str | None = None
    late_entry: bool = False


@dataclass(frozen=True)
class Assignment:
    lane: int
    racer_id: str


@dataclass(frozen=True)
class LaneResult:
    lane: int
    position: int
    elapsed_seconds: float | None = None


@dataclass(frozen=True)
class Heat:
    id: str
    event_id: str
    stage: Stage
    number: int
    assignments: tuple[Assignment, ...]
    results: tuple[LaneResult, ...] = ()
    round_number: int = 0
    tiebreak_scope: str = 'qualification'

    @property
    def completed(self) -> bool:
        return bool(self.results)


@dataclass(frozen=True)
class Standing:
    rank: int
    racer: Racer
    completed: int
    wins: int
    points: float
    average_points: float
    average_finish: float | None
    sporting_key: tuple
    administrative_count: int = 0
    scoring_runs: int = 0


@dataclass(frozen=True)
class LatePolicy:
    kind: str = "last"
    value: str | None = None


@dataclass(frozen=True)
class RacerDraft:
    name: str
    car_name: str
    car_number: str
    tag: str = ""


@dataclass(frozen=True)
class AdministrativeResult:
    id: str
    event_id: str
    racer_id: str
    kind: str
    points: str
    placement: str
    batch_id: str


@dataclass(frozen=True)
class ChangeImpact:
    scope: str
    heats: int = 0
    completed_heats: int = 0
    results: int = 0
    administrative_results: int = 0

    @property
    def destructive(self) -> bool:
        return bool(self.heats or self.administrative_results)

    def describe(self) -> str:
        stages = ("all stages" if self.scope == "all" else
                  "later final-placement tiebreak rounds" if self.scope == 'placement' else "tiebreaks and finals")
        return (f"This change deletes {self.heats} heats ({self.completed_heats} completed), "
                f"{self.results} recorded lane results, and {self.administrative_results} "
                f"administrative scores from {stages}. "
                + ("Generate preliminary heats again afterward. " if self.scope == "all"
                   else "Earlier heats and scores will be preserved. " if self.scope == 'placement'
                   else "Preliminary heats and scores will be preserved. ")
                + "Deleted data cannot be recovered by undo.")


class ConfirmationRequired(RaceError):
    def __init__(self, impact: ChangeImpact):
        self.impact = impact
        super().__init__(impact.describe())


@dataclass(frozen=True)
class FairnessReport:
    future_heats: int
    solo_heats: int
    max_racer_lane_spread: int
    overall_lane_spread: int
    repeated_opponent_meetings: int
    retained_heats: int = 0


@dataclass(frozen=True)
class RemainingSchedule:
    assignments: tuple[tuple[Assignment, ...], ...]
    report: FairnessReport


@dataclass(frozen=True)
class LateEntryPreview:
    event_id: str
    snapshot: str
    racers: tuple[RacerDraft, ...]
    policy: LatePolicy
    target_runs: int
    missed_runs: int
    actual_runs: int
    admin_runs: int
    points_per_admin: str | None
    placement_per_admin: str | None
    old_unrun_heats: int
    schedule: RemainingSchedule
