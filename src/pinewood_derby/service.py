"""Application commands. All writes and state transitions pass through here."""
from collections.abc import Sequence
from math import isfinite
from collections import Counter
from dataclasses import replace
from hashlib import sha256
import sqlite3
from uuid import uuid4

from .database import Database
from .finals import Placements, Qualification, placement_groups, qualification, select_qualifiers
from .hardware import LaneIdentifier, ResultSource, ScheduledLaneIdentifier, lane_mismatches
from .models import (ChangeImpact, ConfirmationRequired, Event, Heat, LaneResult, Racer,
                     RacerDraft, RaceError, Standing, LatePolicy, LateEntryPreview)
from .scheduling import generate_schedule
from .scoring import SCORING_METHODS, ScoringMethod
from .standings import calculate_standings
from .late_entry import missed_runs, policy_score
from .remaining import rebalance_remaining, signature
from .tiebreaks import BalancedLaneTiebreak, TiebreakPolicy


def _id() -> str:
    return str(uuid4())


def validate_results(heat: Heat, results: Sequence[LaneResult]) -> None:
    expected = {a.lane for a in heat.assignments}
    if len(results) != len(expected) or {r.lane for r in results} != expected:
        raise RaceError("Enter a result for every occupied lane, and no empty lanes.")
    if any(type(r.position) is not int or type(r.lane) is not int for r in results):
        raise RaceError("Lane and finishing position must be whole numbers.")
    if {r.position for r in results} != set(range(1, len(expected) + 1)):
        raise RaceError(f"Finishing positions must be unique, from 1 through {len(expected)}.")
    if any(r.elapsed_seconds is not None and (
        isinstance(r.elapsed_seconds, bool) or not isinstance(r.elapsed_seconds, (float, int))
        or not isfinite(r.elapsed_seconds) or r.elapsed_seconds <= 0) for r in results):
        raise RaceError("Elapsed times must be positive finite seconds, or blank.")


class RaceService:
    def __init__(self, database: Database, scoring_methods: dict[str, ScoringMethod] | None = None,
                 lane_identifier: LaneIdentifier | None = None,
                 tiebreak_policy: TiebreakPolicy | None = None):
        self.db = database
        self.scoring_methods = dict(SCORING_METHODS if scoring_methods is None else scoring_methods)
        self.lane_identifier = lane_identifier or ScheduledLaneIdentifier()
        self.tiebreak_policy = tiebreak_policy or BalancedLaneTiebreak()
        # Older saved events may have been declared complete with shared places.
        # Reconcile them using the same recorded-round workflow as new results.
        with self.db.connection:
            for event in self.db.events():
                if event.scoring in self.scoring_methods and self.db.heats(event.id, 'finals'):
                    self._sync_placements(event.id)
                    self._update_status(event.id)

    def change_impact(self, event_id: str, scope: str) -> ChangeImpact:
        heats = self.db.heats(event_id)
        if scope == 'qualification':
            heats = [h for h in heats if h.stage != 'preliminary']
        elif scope == 'none':
            heats = []
        admin_count = len(self.db.administrative_results(event_id)) if scope == 'all' else 0
        return ChangeImpact(scope, len(heats), sum(h.completed for h in heats),
                            sum(len(h.results) for h in heats), admin_count)

    def _confirm_change(self, event_id: str, scope: str, confirmed: bool) -> None:
        impact = self.change_impact(event_id, scope)
        if impact.destructive and not confirmed:
            raise ConfirmationRequired(impact)

    def _invalidate(self, event_id: str, scope: str) -> None:
        if scope == 'none':
            return
        if scope == 'all':
            self.db.connection.execute('DELETE FROM heats WHERE event_id=?', (event_id,))
            self.db.connection.execute('DELETE FROM late_batches WHERE event_id=?', (event_id,))
            self.db.connection.execute('UPDATE racers SET late_entry=0 WHERE event_id=?', (event_id,))
        else:
            if not any(h.stage != 'preliminary' for h in self.db.heats(event_id)):
                return
            self.db.connection.execute("DELETE FROM heats WHERE event_id=? AND stage != 'preliminary'", (event_id,))
        self._restore_cursor(event_id)
        self._update_status(event_id)

    def _restore_cursor(self, event_id: str) -> None:
        heats = self.db.heats(event_id)
        current = next((h for h in heats if not h.completed), heats[-1] if heats else None)
        self.db.connection.execute('UPDATE events SET current_heat_id=? WHERE id=?',
                                   (current.id if current else None, event_id))

    def save_event(self, name: str, lanes: int, runs: int, finalists: int,
                   scoring: str = "placement", event_id: str | None = None,
                   *, late_policy: LatePolicy | None = None, confirmed: bool = False) -> Event:
        if not name.strip():
            raise RaceError("Enter an event name.")
        if type(lanes) is not int or not 2 <= lanes <= 6:
            raise RaceError("Choose between 2 and 6 lanes.")
        if type(runs) is not int or runs < 1 or type(finalists) is not int or finalists < 1:
            raise RaceError("Runs and finalist count must be positive whole numbers.")
        if scoring not in self.scoring_methods:
            raise RaceError("Unsupported scoring method.")
        old = self.db.event(event_id) if event_id else None
        policy = late_policy or (LatePolicy(old.late_policy_kind, old.late_policy_value) if old else LatePolicy())
        policy_score(policy, lanes, scoring)
        scope = 'none'
        if old:
            if (lanes, runs) != (old.lanes, old.runs):
                scope = 'all'
            elif (scoring, finalists) != (old.scoring, old.finalists):
                scope = 'qualification'
                if scoring != old.scoring and self.db.administrative_results(event_id):
                    raise RaceError('Reset the schedule before changing scoring with administrative results.')
            if scope != 'all' and self.db.heats(event_id) and finalists > len(self.db.racers(event_id)):
                raise RaceError('Finalist count exceeds registration. Add racers or reduce the count.')
            self._confirm_change(event_id, scope, confirmed)
        key = event_id or _id()
        with self.db.connection:
            if old:
                self._invalidate(event_id, scope)
                self.db.connection.execute(
                    'UPDATE events SET name=?,lanes=?,runs=?,finalists=?,scoring=?,late_policy_kind=?,late_policy_value=? WHERE id=?',
                    (name.strip(), lanes, runs, finalists, scoring, policy.kind, policy.value, key))
            else:
                self.db.connection.execute(
                    'INSERT INTO events(id,name,lanes,runs,finalists,scoring,late_policy_kind,late_policy_value) VALUES(?,?,?,?,?,?,?,?)',
                    (key, name.strip(), lanes, runs, finalists, scoring, policy.kind, policy.value))
        return self.db.event(key)

    def _registration_open(self, event_id: str) -> None:
        self.db.event(event_id)
        if self.db.heats(event_id):
            raise RaceError('A schedule already exists. Reset it before generating again.')

    def _validate_drafts(self, event_id: str, drafts: Sequence[RacerDraft], exclude: str | None = None) -> None:
        racers = [r for r in self.db.racers(event_id) if r.id != exclude]
        numbers = {r.car_number.casefold() for r in racers}
        tags = {r.tag.casefold() for r in racers if r.tag}
        for draft in drafts:
            if not draft.name.strip() or not draft.car_number.strip():
                raise RaceError('Racer name and car number are required.')
            number, tag = draft.car_number.strip().casefold(), draft.tag.strip().casefold()
            if number in numbers or (tag and tag in tags):
                raise RaceError('Car number or RFID/tag is already registered in this event or group.')
            numbers.add(number)
            if tag:
                tags.add(tag)

    def _insert_racer(self, event_id: str, draft: RacerDraft, key: str, batch: str | None = None) -> None:
        self.db.connection.execute(
            'INSERT INTO racers(id,event_id,name,car_name,car_number,number_key,tag,tag_key,late_entry,late_batch_id) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)', (key, event_id, draft.name.strip(), draft.car_name.strip(),
             draft.car_number.strip(), draft.car_number.strip().casefold(), draft.tag.strip() or None,
             draft.tag.strip().casefold() or None, int(batch is not None), batch))

    def save_racer(self, event_id: str, name: str, car_name: str, car_number: str,
                   tag: str = '', racer_id: str | None = None, *, confirmed: bool = False) -> Racer:
        self.db.event(event_id)
        if racer_id and racer_id not in {r.id for r in self.db.racers(event_id)}:
            raise RaceError('Racer does not belong to this event.')
        draft = RacerDraft(name, car_name, car_number, tag)
        self._validate_drafts(event_id, [draft], racer_id)
        if not racer_id:
            self._confirm_change(event_id, 'all', confirmed)
        key = racer_id or _id()
        with self.db.connection:
            if racer_id:
                self.db.connection.execute(
                    'UPDATE racers SET name=?,car_name=?,car_number=?,number_key=?,tag=?,tag_key=? WHERE id=?',
                    (name.strip(), car_name.strip(), car_number.strip(), car_number.strip().casefold(),
                     tag.strip() or None, tag.strip().casefold() or None, key))
            else:
                self._invalidate(event_id, 'all')
                self._insert_racer(event_id, draft, key)
        return next(r for r in self.db.racers(event_id) if r.id == key)

    def remove_racer(self, event_id: str, racer_id: str, *, confirmed: bool = False) -> None:
        self.db.event(event_id)
        if racer_id not in {r.id for r in self.db.racers(event_id)}:
            raise RaceError('Racer not found in this event.')
        self._confirm_change(event_id, 'all', confirmed)
        with self.db.connection:
            self._invalidate(event_id, 'all')
            self.db.connection.execute('DELETE FROM racers WHERE event_id=? AND id=?', (event_id, racer_id))

    def delete_event(self, event_id: str) -> None:
        self.db.event(event_id)
        with self.db.connection:
            # Remove assignments before racers to satisfy their referential constraint.
            self.db.connection.execute("DELETE FROM heats WHERE event_id=?", (event_id,))
            self.db.connection.execute("DELETE FROM events WHERE id=?", (event_id,))

    def _insert_schedule(self, event_id: str, stage: str, schedule, round_number: int = 0,
                         tiebreak_scope: str = 'qualification') -> list[str]:
        start = self.db.connection.execute('SELECT coalesce(max(number),0) FROM heats WHERE event_id=? AND stage=?',
                                           (event_id, stage)).fetchone()[0]
        keys = []
        for number, assignments in enumerate(schedule, start + 1):
            key = _id()
            keys.append(key)
            self.db.connection.execute('INSERT INTO heats(id,event_id,stage,number,round_number,tiebreak_scope) VALUES(?,?,?,?,?,?)',
                                       (key, event_id, stage, number, round_number, tiebreak_scope))
            self.db.connection.executemany('INSERT INTO assignments(heat_id,lane,racer_id) VALUES(?,?,?)',
                                           [(key, a.lane, a.racer_id) for a in assignments])
        return keys

    def _insert_heats(self, event: Event, stage: str, racer_ids: Sequence[str], runs: int) -> list[str]:
        return self._insert_schedule(event.id, stage, generate_schedule(racer_ids, event.lanes, runs))

    def generate_preliminary(self, event_id: str) -> None:
        self._registration_open(event_id)
        event = self.db.event(event_id)
        racers = self.db.racers(event_id)
        if not racers:
            raise RaceError("Register at least one racer before generating heats.")
        if event.finalists > len(racers):
            raise RaceError("Finalist count exceeds registration. Update event setup first.")
        with self.db.connection:
            keys = self._insert_heats(event, "preliminary", [r.id for r in racers], event.runs)
            self.db.connection.execute("UPDATE events SET current_heat_id=?, status='preliminary' WHERE id=?",
                                       (keys[0], event_id))

    def reset_schedule(self, event_id: str) -> None:
        self.db.event(event_id)
        with self.db.connection:
            self._invalidate(event_id, 'all')

    def reset_finals(self, event_id: str) -> None:
        self.db.event(event_id)
        with self.db.connection:
            self.db.connection.execute("DELETE FROM heats WHERE event_id=? AND stage='finals'", (event_id,))
            self.db.connection.execute("DELETE FROM heats WHERE event_id=? AND stage='tiebreak' AND tiebreak_scope='finals'", (event_id,))
            self._restore_cursor(event_id)
            self._update_status(event_id)

    def reset_tiebreaks(self, event_id: str) -> None:
        self.db.event(event_id)
        with self.db.connection:
            self._invalidate(event_id, 'qualification')

    def standings(self, event_id: str, stage: str = 'preliminary', round_number: int | None = None) -> list[Standing]:
        rows = self._scored_standings(event_id, stage, round_number)
        if stage != 'finals':
            return rows
        by_id = {row.racer.id: row for row in rows}
        resolved = []
        for group in self.final_placements(event_id).groups:
            rank = len(resolved) + 1
            resolved.extend(replace(by_id[key], rank=rank) for key in group)
        return resolved

    def _scored_standings(self, event_id: str, stage: str, round_number: int | None = None) -> list[Standing]:
        event = self.db.event(event_id)
        if event.scoring not in self.scoring_methods:
            raise RaceError(f"Scoring method '{event.scoring}' is not installed.")
        heats = self.db.heats(event_id, stage)
        if round_number is not None:
            heats = [h for h in heats if h.round_number == round_number]
        racers = self.db.racers(event_id)
        if stage != 'preliminary':
            participants = {a.racer_id for h in heats for a in h.assignments}
            racers = [r for r in racers if r.id in participants]
        admin = self.db.administrative_results(event_id) if stage == 'preliminary' else ()
        return calculate_standings(racers, heats, self.scoring_methods[event.scoring], admin)

    def tiebreak_heats(self, event_id: str, scope: str = 'qualification') -> list[Heat]:
        return [h for h in self.db.heats(event_id, 'tiebreak') if h.tiebreak_scope == scope]

    def final_placements(self, event_id: str) -> Placements:
        groups = list(placement_groups(self._scored_standings(event_id, 'finals')))
        finals = self.db.heats(event_id, 'finals')
        if not finals or not all(h.completed for h in finals):
            return Placements(tuple(groups))
        heats = self.tiebreak_heats(event_id, 'finals')
        for number in sorted({h.round_number for h in heats}):
            current = [h for h in heats if h.round_number == number]
            participants = {a.racer_id for h in current for a in h.assignments}
            index = next((i for i, group in enumerate(groups) if set(group) == participants), None)
            if index is None:
                raise RaceError('Placement tiebreak participants no longer match the tied group.')
            if not all(h.completed for h in current):
                return Placements(tuple(groups), number)
            groups[index:index + 1] = placement_groups(self._scored_standings(event_id, 'tiebreak', number))
        return Placements(tuple(groups))

    def _schedule_tiebreak(self, event: Event, tied: Sequence[str], scope: str) -> list[str]:
        number = max((h.round_number for h in self.db.heats(event.id, 'tiebreak')), default=0) + 1
        schedule = self.tiebreak_policy.schedule(tied, event.lanes, number)
        return self._insert_schedule(event.id, 'tiebreak', schedule, number, scope)

    def _sync_placements(self, event_id: str) -> str | None:
        finals = self.db.heats(event_id, 'finals')
        if not finals or not all(h.completed for h in finals):
            return None
        plan = self.final_placements(event_id)
        if plan.tied and plan.pending_round is None:
            keys = self._schedule_tiebreak(self.db.event(event_id), plan.tied, 'finals')
            self.db.connection.execute('UPDATE events SET current_heat_id=? WHERE id=?', (keys[0], event_id))
            return keys[0]
        return None

    def qualification(self, event_id: str) -> Qualification:
        event = self.db.event(event_id)
        heats = self.db.heats(event_id, 'preliminary')
        if not heats or not all(h.completed for h in heats):
            raise RaceError('Complete all preliminary heats before selecting finalists.')
        plan = qualification(self.standings(event_id), event.finalists)
        tiebreaks = self.tiebreak_heats(event_id)
        for round_number in sorted({h.round_number for h in tiebreaks}):
            current = [h for h in tiebreaks if h.round_number == round_number]
            if not all(h.completed for h in current):
                return Qualification(plan.automatic, plan.tied, plan.places, round_number)
            resolved = qualification(self.standings(event_id, 'tiebreak', round_number), plan.places)
            plan = Qualification(plan.automatic + resolved.automatic, resolved.tied, resolved.places)
        return plan

    def generate_tiebreak(self, event_id: str) -> None:
        event = self.db.event(event_id)
        if self.db.heats(event_id, 'finals'):
            raise RaceError('Reset finals before generating a tiebreak.')
        plan = self.qualification(event_id)
        if plan.pending_round is not None:
            raise RaceError('Complete the current tiebreak round before generating another.')
        if not plan.tied:
            raise RaceError('There is no unresolved qualification tie.')
        with self.db.connection:
            keys = self._schedule_tiebreak(event, plan.tied, 'qualification')
            self.db.connection.execute("UPDATE events SET current_heat_id=?,status='tiebreak' WHERE id=?", (keys[0], event_id))

    def generate_finals(self, event_id: str, tied_selection: Sequence[str] = ()) -> None:
        event = self.db.event(event_id)
        if self.db.heats(event_id, 'finals'):
            raise RaceError('Finals already exist. Reset finals to change qualification.')
        plan = self.qualification(event_id)
        if tied_selection or plan.tied:
            raise RaceError('Resolve the cutoff tie with recorded tiebreak races before generating finals.')
        finalists = select_qualifiers(plan)
        with self.db.connection:
            keys = self._insert_heats(event, 'finals', finalists, event.lanes)
            self.db.connection.execute("UPDATE events SET current_heat_id=?,status='finals' WHERE id=?", (keys[0], event_id))

    def _update_status(self, event_id: str) -> None:
        heats = self.db.heats(event_id)
        finals = [h for h in heats if h.stage == 'finals']
        tiebreaks = [h for h in heats if h.stage == 'tiebreak']
        status = 'registration'
        if finals:
            status = ('complete' if all(h.completed for h in finals) and not self.final_placements(event_id).tied
                      else 'tiebreak' if any(h.tiebreak_scope == 'finals' for h in tiebreaks) else 'finals')
        elif tiebreaks:
            status = 'ready_finals' if all(h.completed for h in tiebreaks) else 'tiebreak'
        elif heats:
            status = 'ready_finals' if all(h.completed for h in heats) else 'preliminary'
        self.db.connection.execute('UPDATE events SET status=? WHERE id=?', (status, event_id))

    def record_results(self, heat_id: str, source: ResultSource, advance: bool = False,
                       *, confirmed: bool = False) -> None:
        heat = self.db.heat(heat_id)
        placement = heat.stage == 'tiebreak' and heat.tiebreak_scope == 'finals'
        if heat.stage == 'preliminary' and self.db.heats(heat.event_id, 'tiebreak'):
            raise RaceError('Reset tiebreaks (and finals) before correcting preliminary results.')
        if heat.stage != 'finals' and not placement and self.db.heats(heat.event_id, 'finals'):
            raise RaceError('Reset finals before correcting earlier results; qualifiers may change.')
        if heat.stage == 'tiebreak' and not placement and any(h.round_number > heat.round_number for h in self.tiebreak_heats(heat.event_id)):
            raise RaceError('Reset tiebreaks before correcting a round that has a later round.')
        results = tuple(source.obtain_results(heat))
        validate_results(heat, results)
        dependent = []
        if heat.completed and set(results) != set(heat.results) and (heat.stage == 'finals' or placement):
            dependent = [h for h in self.tiebreak_heats(heat.event_id, 'finals')
                         if heat.stage == 'finals' or h.round_number > heat.round_number]
            if dependent and not confirmed:
                raise ConfirmationRequired(ChangeImpact('placement', len(dependent),
                    sum(h.completed for h in dependent), sum(len(h.results) for h in dependent)))
        with self.db.connection:
            for later in dependent:
                self.db.connection.execute('DELETE FROM heats WHERE id=?', (later.id,))
            self.db.connection.execute("DELETE FROM results WHERE heat_id=?", (heat_id,))
            self.db.connection.executemany(
                "INSERT INTO results(heat_id,lane,position,elapsed_seconds) VALUES(?,?,?,?)",
                [(heat_id, r.lane, r.position, r.elapsed_seconds) for r in results])
            next_id = heat_id
            if advance:
                heats = self.db.heats(heat.event_id, heat.stage)
                index = next(i for i, h in enumerate(heats) if h.id == heat_id)
                if index + 1 < len(heats):
                    next_id = heats[index + 1].id
            self.db.connection.execute("UPDATE events SET current_heat_id=? WHERE id=?", (next_id, heat.event_id))
            self._sync_placements(heat.event_id)
            self._update_status(heat.event_id)

    def set_current_heat(self, event_id: str, heat_id: str) -> None:
        if self.db.heat(heat_id).event_id != event_id:
            raise RaceError("Heat belongs to another event.")
        with self.db.connection:
            self.db.connection.execute("UPDATE events SET current_heat_id=? WHERE id=?", (heat_id, event_id))

    def check_lanes(self, heat_id: str) -> list[str]:
        heat = self.db.heat(heat_id)
        return lane_mismatches(heat, self.lane_identifier.identify(heat))

    def _late_snapshot(self, event_id: str) -> str:
        state = (self.db.event(event_id), self.db.racers(event_id), self.db.heats(event_id),
                 self.db.administrative_results(event_id))
        return sha256(repr(state).encode('utf-8')).hexdigest()

    def preview_late_entries(self, event_id: str, drafts: Sequence[RacerDraft],
                             policy: LatePolicy | None = None) -> LateEntryPreview:
        event = self.db.event(event_id)
        all_heats = self.db.heats(event_id)
        if any(h.stage != 'preliminary' for h in all_heats):
            raise RaceError('Late entry is available only before tiebreaks or finals. Reset those stages first.')
        completed = [h for h in all_heats if h.completed]
        if not completed:
            raise RaceError('Use ordinary registration until preliminary racing has started.')
        if not drafts:
            raise RaceError('Add at least one late racer to the group.')
        clean = tuple(RacerDraft(d.name.strip(), d.car_name.strip(), d.car_number.strip(), d.tag.strip()) for d in drafts)
        self._validate_drafts(event_id, clean)
        applied = policy or LatePolicy(event.late_policy_kind, event.late_policy_value)
        score = policy_score(applied, event.lanes, event.scoring)
        racers = self.db.racers(event_id)
        missed = missed_runs(racers, completed, event.runs)
        admin_runs = missed if score is not None else 0
        actual_runs = event.runs - admin_runs
        counts = Counter(a.racer_id for h in completed for a in h.assignments)
        counts.update(r.racer_id for r in self.db.administrative_results(event_id))
        required = {r.id: event.runs - counts[r.id] for r in racers}
        required.update({f'__late_{i}__': actual_runs for i in range(len(clean))})
        previous = [h for h in all_heats if not h.completed]
        schedule = rebalance_remaining(required, completed, event.lanes, previous)
        return LateEntryPreview(event_id, self._late_snapshot(event_id), clean, applied, event.runs,
                                missed, actual_runs, admin_runs, str(score[0]) if score else None,
                                str(score[1]) if score else None, len(previous), schedule)

    def commit_late_entries(self, preview: LateEntryPreview) -> tuple[Racer, ...]:
        """Commit the exact reviewed treatment, or reject a stale/modified preview.

        No UPDATE/DELETE statement in this command targets a completed heat or an
        actual result. The final equality assertion is an additional atomic guard.
        """
        self.db.connection.execute('BEGIN IMMEDIATE')
        with self.db.connection:
            if preview.snapshot != self._late_snapshot(preview.event_id):
                raise RaceError('The event changed after preview. Review late-entry consequences again.')
            verified = self.preview_late_entries(preview.event_id, preview.racers, preview.policy)
            if verified != preview:
                raise RaceError('Late-entry preview is invalid. Generate a fresh preview.')
            event = self.db.event(preview.event_id)
            historical = [h for h in self.db.heats(event.id) if h.completed]
            old_pending = [h for h in self.db.heats(event.id) if not h.completed]
            batch = _id()
            self.db.connection.execute(
                'INSERT INTO late_batches(id,event_id,policy_kind,policy_value,target_runs,missed_runs,actual_runs,reference_lanes) '
                'VALUES(?,?,?,?,?,?,?,?)', (batch, event.id, preview.policy.kind, preview.policy.value,
                                          event.runs, preview.missed_runs, preview.actual_runs, event.lanes))
            keys = {}
            for index, draft in enumerate(preview.racers):
                key = _id()
                keys[f'__late_{index}__'] = key
                self._insert_racer(event.id, draft, key, batch)
                self.db.connection.executemany(
                    'INSERT INTO administrative_results(id,event_id,racer_id,batch_id,kind,ordinal,points,placement) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    [(_id(), event.id, key, batch, 'late_entry_penalty', ordinal,
                      preview.points_per_admin, preview.placement_per_admin)
                     for ordinal in range(1, preview.admin_runs + 1)])
            pending_by_signature = {}
            for heat in old_pending:
                pending_by_signature.setdefault(signature(heat.assignments), []).append(heat)
            retained = set()
            new_assignments = []
            from .models import Assignment
            for assignments in preview.schedule.assignments:
                mapped = tuple(Assignment(a.lane, keys.get(a.racer_id, a.racer_id)) for a in assignments)
                candidates = pending_by_signature.get(signature(mapped), [])
                if candidates:
                    retained.add(candidates.pop(0).id)
                else:
                    new_assignments.append(mapped)
            for heat in old_pending:
                if heat.id not in retained:
                    self.db.connection.execute(
                        'DELETE FROM heats WHERE id=? AND event_id=? AND stage=\'preliminary\' '
                        'AND NOT EXISTS(SELECT 1 FROM results WHERE heat_id=heats.id)', (heat.id, event.id))
            self._insert_schedule(event.id, 'preliminary', new_assignments)
            after = [h for h in self.db.heats(event.id) if h.completed]
            if after != historical:
                raise RaceError('Late-entry scheduling attempted to change completed history; nothing was saved.')
            self._restore_cursor(event.id)
            self._update_status(event.id)
        return tuple(r for r in self.db.racers(event.id) if r.id in keys.values())
