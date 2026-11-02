from collections import Counter
from dataclasses import replace
from fractions import Fraction
import sqlite3

import pytest

from pinewood_derby.database import Database, SCHEMA, SCHEMA_VERSION
from pinewood_derby.hardware import ManualResultSource
from pinewood_derby.late_entry import policy_score
from pinewood_derby.models import (Assignment, ConfirmationRequired, Heat, LaneResult, LatePolicy,
                                  RacerDraft, RaceError)
from pinewood_derby.remaining import rebalance_remaining
from pinewood_derby.service import RaceService
from pinewood_derby.tiebreaks import BalancedLaneTiebreak


def event_with_racers(service, n=7, lanes=2, runs=4, finalists=2):
    event = service.save_event('Event', lanes, runs, finalists)
    racers = [service.save_racer(event.id, f'Racer {i}', f'Car {i}', str(i)) for i in range(n)]
    service.generate_preliminary(event.id)
    return service.db.event(event.id), racers


def save_heat(service, heat, winner=None):
    assignments = sorted(heat.assignments, key=lambda a: (a.racer_id != winner, a.racer_id))
    service.record_results(heat.id, ManualResultSource([LaneResult(a.lane, i + 1, 3.0 + i / 10)
                                                      for i, a in enumerate(assignments)]), advance=True)


def finish(service, event_id, stage='preliminary', winner=None):
    for heat in service.db.heats(event_id, stage):
        if not heat.completed:
            save_heat(service, heat, winner)


def draft(number='new'):
    return RacerDraft(f'Late {number}', 'Comet', number)


def historical(service, event_id):
    return [h for h in service.db.heats(event_id) if h.completed]


def test_setup_before_and_after_scheduling(service):
    event = service.save_event('Initial', 2, 2, 1)
    service.save_event('Updated', 3, 3, 2, event_id=event.id)
    assert service.db.event(event.id).lanes == 3
    for i in range(3):
        service.save_racer(event.id, str(i), '', str(i))
    service.generate_preliminary(event.id)
    saved = service.db.heats(event.id)
    service.save_event('Renamed', 3, 3, 1, event_id=event.id, late_policy=LatePolicy('none'))
    assert service.db.heats(event.id) == saved
    with pytest.raises(ConfirmationRequired) as exc:
        service.save_event('Renamed', 4, 3, 1, event_id=event.id)
    assert exc.value.impact.completed_heats == 0
    assert service.db.heats(event.id) == saved
    service.save_event('Renamed', 4, 3, 1, event_id=event.id, confirmed=True)
    assert not service.db.heats(event.id)
    assert service.db.event(event.id).status == 'registration'
    service.generate_preliminary(event.id)


def test_metadata_survives_racing_and_registration_changes_confirm(service):
    event, racers = event_with_racers(service)
    save_heat(service, service.db.heats(event.id)[0])
    before = service.db.heats(event.id)
    service.save_racer(event.id, 'New name', 'New car name', 'NEW-ID', 'RFID', racers[0].id)
    assert service.db.heats(event.id) == before
    for operation in [lambda: service.save_racer(event.id, 'Added', '', '999'),
                      lambda: service.remove_racer(event.id, racers[0].id),
                      lambda: service.save_event('Event', 3, 4, 2, event_id=event.id)]:
        with pytest.raises(ConfirmationRequired) as exc:
            operation()
        assert exc.value.impact.completed_heats == 1 and exc.value.impact.results == 2
        assert service.db.heats(event.id) == before
    service.save_racer(event.id, 'Added', '', '999', confirmed=True)
    assert not service.db.heats(event.id)
    assert len(service.db.racers(event.id)) == 8
    service.generate_preliminary(event.id)
    service.remove_racer(event.id, racers[0].id, confirmed=True)
    assert not service.db.heats(event.id)


def test_invalid_metadata_does_not_invalidate_even_if_confirmed(service):
    event, racers = event_with_racers(service)
    saved = service.db.heats(event.id)
    with pytest.raises(RaceError, match='already registered'):
        service.save_racer(event.id, 'Duplicate', '', racers[0].car_number, confirmed=True)
    assert service.db.heats(event.id) == saved


def test_finalist_count_resets_only_downstream(service):
    event, racers = event_with_racers(service, n=3, lanes=3, runs=3, finalists=3)
    finish(service, event.id)
    prelim = service.db.heats(event.id)
    service.generate_finals(event.id)
    finish(service, event.id, 'finals')
    with pytest.raises(ConfirmationRequired) as exc:
        service.save_event('Updated', 3, 3, 2, event_id=event.id)
    assert exc.value.impact.scope == 'qualification'
    service.save_event('Updated', 3, 3, 2, event_id=event.id, confirmed=True)
    assert service.db.heats(event.id) == prelim


@pytest.mark.parametrize('count,lanes', [(2, 2), (2, 6), (3, 2), (7, 3), (10, 4), (13, 6)])
def test_tiebreak_policy_balances_every_lane(count, lanes):
    ids = [str(i) for i in range(count)]
    schedule = BalancedLaneTiebreak().schedule(ids, lanes, 1)
    assert schedule == BalancedLaneTiebreak().schedule(ids, lanes, 1)
    for racer in ids:
        assert Counter(a.lane for h in schedule for a in h if a.racer_id == racer) == Counter(range(1, lanes + 1))
    assert all(len(h) >= 2 for h in schedule)


def create_two_way_tie(service):
    event, racers = event_with_racers(service, n=2, lanes=2, runs=2, finalists=1)
    for heat, racer in zip(service.db.heats(event.id), racers):
        save_heat(service, heat, racer.id)
    return event, racers


def test_tiebreak_rounds_resume_and_no_arbitrary_selection(service):
    event, racers = create_two_way_tie(service)
    assert set(service.qualification(event.id).tied) == {r.id for r in racers}
    with pytest.raises(RaceError, match='recorded tiebreak'):
        service.generate_finals(event.id, [racers[0].id])
    service.generate_tiebreak(event.id)
    heats = service.db.heats(event.id, 'tiebreak')
    assert len(heats) == 2
    assert heats[0].assignments[0].racer_id != heats[1].assignments[0].racer_id
    save_heat(service, heats[0], racers[0].id)
    assert service.qualification(event.id).pending_round == 1
    with pytest.raises(RaceError, match='Complete the current'):
        service.generate_tiebreak(event.id)
    path = service.db.connection.execute('PRAGMA database_list').fetchone()[2]
    second = Database(path)
    try:
        restored = RaceService(second)
        assert restored.qualification(event.id) == service.qualification(event.id)
        assert second.heats(event.id) == service.db.heats(event.id)
        save_heat(restored, heats[1], racers[1].id)
        assert restored.qualification(event.id).tied
        restored.generate_tiebreak(event.id)
        for heat in second.heats(event.id, 'tiebreak'):
            if heat.round_number == 2:
                save_heat(restored, heat, racers[0].id)
        assert restored.qualification(event.id).automatic == (racers[0].id,)
        with pytest.raises(RaceError, match='later round'):
            save_heat(restored, heats[0], racers[1].id)
        restored.generate_finals(event.id)
        assert {a.racer_id for h in second.heats(event.id, 'finals') for a in h.assignments} == {racers[0].id}
        with pytest.raises(RaceError, match='Reset tiebreaks'):
            save_heat(restored, second.heats(event.id, 'preliminary')[0])
    finally:
        second.close()


def test_multi_racer_tie_larger_than_track(service):
    event, racers = event_with_racers(service, n=6, lanes=2, runs=1, finalists=1)
    # Every heat has a winner, producing a cutoff tie of 3 winners.
    finish(service, event.id)
    assert len(service.qualification(event.id).tied) == 3
    service.generate_tiebreak(event.id)
    heats = service.db.heats(event.id, 'tiebreak')
    assert len(heats) == 3 and all(len(h.assignments) == 2 for h in heats)
    strongest = min(service.qualification(event.id).tied)
    finish(service, event.id, 'tiebreak', strongest)
    assert service.qualification(event.id).automatic == (strongest,)
    service.generate_finals(event.id)


@pytest.mark.parametrize('policy,points,placement', [
    (LatePolicy('none'), None, None), (LatePolicy('last'), 0, 4),
    (LatePolicy('above_last'), Fraction(100, 3), 3),
    (LatePolicy('placement', '2'), Fraction(200, 3), 2),
    (LatePolicy('points', '37.5'), Fraction(75, 2), Fraction(23, 8)),
])
def test_policy_equivalents(policy, points, placement):
    assert policy_score(policy, 4) == (None if points is None else (points, placement))


@pytest.mark.parametrize('policy', [LatePolicy('bad'), LatePolicy('points', 'nan'), LatePolicy('points', 'inf'),
    LatePolicy('points', '-1'), LatePolicy('points', '101'), LatePolicy('placement', '0'),
    LatePolicy('placement', '5'), LatePolicy('placement', '1.5'), LatePolicy('last', '3')])
def test_bad_policy(policy):
    with pytest.raises(RaceError):
        policy_score(policy, 4)


@pytest.mark.parametrize('count,lanes', [(7, 2), (10, 3), (17, 4), (23, 5), (31, 6)])
@pytest.mark.parametrize('policy', [LatePolicy('none'), LatePolicy('last'), LatePolicy('above_last'), LatePolicy('points', '25')])
def test_late_entry_preserves_completed_and_exact_entitlements(service, count, lanes, policy):
    event, racers = event_with_racers(service, n=count, lanes=lanes, runs=4)
    original = service.db.heats(event.id)
    # Save out of order to prove current heat number does not determine missed runs.
    for heat in original[::2][:max(1, len(original)//2)]:
        save_heat(service, heat)
    before = historical(service, event.id)
    saved_ids = {h.id for h in before}
    preview = service.preview_late_entries(event.id, [draft('a'), draft('b')], policy)
    assert preview.missed_runs == sum(len(h.assignments) for h in before) // count
    assert historical(service, event.id) == before
    assert preview.actual_runs == (4 if policy.kind == 'none' else 4 - preview.missed_runs)
    added = service.commit_late_entries(preview)
    assert len(added) == 2 and all(r.late_entry for r in added)
    assert historical(service, event.id) == before
    assert saved_ids <= {h.id for h in service.db.heats(event.id)}
    participation = Counter(a.racer_id for h in service.db.heats(event.id, 'preliminary') for a in h.assignments)
    admin = Counter(r.racer_id for r in service.db.administrative_results(event.id))
    for racer in service.db.racers(event.id):
        assert participation[racer.id] + admin[racer.id] == 4
    for racer in added:
        assert participation[racer.id] == preview.actual_runs
        row = next(r for r in service.standings(event.id) if r.racer.id == racer.id)
        assert row.completed == 0 and row.administrative_count == preview.admin_runs and row.wins == 0
    all_heats = service.db.heats(event.id)
    for racer in service.db.racers(event.id):
        counts = Counter({lane: 0 for lane in range(1, lanes+1)})
        counts.update(a.lane for h in all_heats for a in h.assignments if a.racer_id == racer.id)
        assert max(counts.values()) - min(counts.values()) <= 1
    assert service.db.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_early_late_entry_has_zero_missed_runs_and_preview_is_read_only(service):
    event, _ = event_with_racers(service, n=10, lanes=3)
    save_heat(service, service.db.heats(event.id)[0])
    before = service.db.heats(event.id)
    preview = service.preview_late_entries(event.id, [draft()])
    assert preview.missed_runs == 0 and preview.actual_runs == 4 and preview.admin_runs == 0
    assert service.db.heats(event.id) == before and len(service.db.racers(event.id)) == 10
    service.commit_late_entries(preview)
    assert not service.db.administrative_results(event.id)


def test_near_end_late_group_override_and_restart(service):
    event, _ = event_with_racers(service, n=7, lanes=2)
    finish(service, event.id)
    before = historical(service, event.id)
    # Default penalizes all four missed runs, override grants all four actual runs.
    default = service.preview_late_entries(event.id, [draft()])
    assert default.missed_runs == 4 and default.actual_runs == 0
    preview = service.preview_late_entries(event.id, [draft('A'), draft('B'), draft('C')], LatePolicy('none'))
    added = service.commit_late_entries(preview)
    assert all(r.completed == 0 for r in service.standings(event.id) if r.racer.id in {r.id for r in added})
    assert service.db.event(event.id).late_policy_kind == 'last'
    path = service.db.connection.execute('PRAGMA database_list').fetchone()[2]
    second = Database(path)
    try:
        restored = RaceService(second)
        assert second.heats(event.id) == service.db.heats(event.id)
        assert restored.standings(event.id) == service.standings(event.id)
        assert historical(restored, event.id) == before
        batch = second.connection.execute('SELECT * FROM late_batches').fetchone()
        assert batch['policy_kind'] == 'none' and batch['actual_runs'] == 4
    finally:
        second.close()


def test_penalty_records_are_not_races_and_default_edits_are_not_retroactive(service):
    event, _ = event_with_racers(service, n=2, lanes=2, runs=4)
    save_heat(service, service.db.heats(event.id)[0])
    preview = service.preview_late_entries(event.id, [draft()], LatePolicy('placement', '1'))
    racer = service.commit_late_entries(preview)[0]
    admin = service.db.administrative_results(event.id)
    assert len(admin) == 1 and admin[0].kind == 'late_entry_penalty' and admin[0].points == '100'
    row = next(r for r in service.standings(event.id) if r.racer.id == racer.id)
    assert row.completed == 0 and row.scoring_runs == 1 and row.wins == 0 and row.average_points == 100
    service.save_event(event.name, 2, 4, 2, event_id=event.id, late_policy=LatePolicy('none'))
    assert service.db.administrative_results(event.id) == admin
    assert service.preview_late_entries(event.id, [draft('another')]).missed_runs == 1
    service.reset_schedule(event.id)
    assert not service.db.administrative_results(event.id)
    assert not any(r.late_entry for r in service.db.racers(event.id))


def test_stale_or_forged_late_preview_is_rejected_atomically(service):
    event, _ = event_with_racers(service)
    heats = service.db.heats(event.id)
    save_heat(service, heats[0])
    preview = service.preview_late_entries(event.id, [draft()])
    with pytest.raises(RaceError, match='invalid'):
        service.commit_late_entries(replace(preview, actual_runs=999))
    save_heat(service, heats[1])
    before = service.db.heats(event.id)
    with pytest.raises(RaceError, match='changed after preview'):
        service.commit_late_entries(preview)
    assert service.db.heats(event.id) == before and len(service.db.racers(event.id)) == 7
    assert not service.db.connection.in_transaction


def test_bulk_validation_and_late_commit_rollback(service):
    event, _ = event_with_racers(service)
    save_heat(service, service.db.heats(event.id)[0])
    with pytest.raises(RaceError, match='already registered'):
        service.preview_late_entries(event.id, [draft('A'), draft('a')])
    preview = service.preview_late_entries(event.id, [draft()])
    before = service.db.heats(event.id)
    service.db.connection.execute("CREATE TRIGGER fail_late BEFORE INSERT ON late_batches BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.commit_late_entries(preview)
    assert service.db.heats(event.id) == before and len(service.db.racers(event.id)) == 7


def test_no_late_entry_during_tiebreak_or_finals(service):
    event, _ = create_two_way_tie(service)
    service.generate_tiebreak(event.id)
    with pytest.raises(RaceError, match='only before'):
        service.preview_late_entries(event.id, [draft()])


def test_remaining_schedule_reports_unfixable_history():
    completed = [Heat(str(i), 'e', 'preliminary', i+1, (Assignment(1, 'a'), Assignment(2, 'b')),
                      (LaneResult(1, 1), LaneResult(2, 2))) for i in range(3)]
    plan = rebalance_remaining({'a': 0, 'b': 0, 'late': 1}, completed, 4)
    assert len(plan.assignments) == 1
    assert plan.report.max_racer_lane_spread == 3
    assert plan.report.solo_heats == 1
    assert plan == rebalance_remaining({'late': 1, 'b': 0, 'a': 0}, completed, 4)


def test_migration_v1_preserves_history_and_ids(tmp_path):
    path = tmp_path / 'v1.sqlite3'
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    with con:
        con.execute("INSERT INTO events(id,name,lanes,runs,finalists,scoring,current_heat_id,status) VALUES('e','V1 race',2,2,1,'placement','h','preliminary')")
        for r in ['a','b']:
            con.execute('INSERT INTO racers(id,event_id,name,car_name,car_number,number_key) VALUES(?,?,?,?,?,?)', (r,'e',r,'Car',r,r))
        con.execute("INSERT INTO heats VALUES('h','e','preliminary',1)")
        con.executemany('INSERT INTO assignments VALUES(?,?,?)', [('h',1,'a'),('h',2,'b')])
        con.executemany('INSERT INTO results VALUES(?,?,?,?)', [('h',1,1,3.218),('h',2,2,None)])
    original_results = con.execute('SELECT * FROM results').fetchall()
    original_assignments = con.execute('SELECT * FROM assignments').fetchall()
    con.close()
    db = Database(path)
    try:
        assert db.connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert db.event('e').current_heat_id == 'h' and db.event('e').late_policy_kind == 'last'
        assert [tuple(row) for row in db.connection.execute('SELECT * FROM results')] == original_results
        assert [tuple(row) for row in db.connection.execute('SELECT * FROM assignments')] == original_assignments
        assert db.heat('h').completed and not any(r.late_entry for r in db.racers('e'))
        assert db.connection.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        db.close()
    reopened = Database(path)
    reopened.close()


def test_failed_migration_rolls_back_to_v1(tmp_path):
    path = tmp_path / 'invalid_v1.sqlite3'
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    with con:
        con.execute("INSERT INTO heats VALUES('broken','missing','preliminary',1)")
    con.close()
    with pytest.raises(RaceError, match='original database was preserved'):
        Database(path)
    con = sqlite3.connect(path)
    assert con.execute('PRAGMA user_version').fetchone()[0] == 1
    assert 'round_number' not in [row[1] for row in con.execute('PRAGMA table_info(heats)')]
    assert con.execute('SELECT id FROM heats').fetchone()[0] == 'broken'
    con.close()


def test_late_commit_never_writes_or_deletes_completed_rows(service):
    event, _ = event_with_racers(service, n=7, lanes=2)
    for heat in service.db.heats(event.id)[:4]:
        save_heat(service, heat)
    before = historical(service, event.id)
    # Fail immediately if any historical SQL write is even attempted, including
    # a delete/reinsert that could otherwise leave equal-looking values.
    service.db.connection.executescript('''
    CREATE TRIGGER protect_results_delete BEFORE DELETE ON results
      BEGIN SELECT RAISE(ABORT, 'historical result delete'); END;
    CREATE TRIGGER protect_results_update BEFORE UPDATE ON results
      BEGIN SELECT RAISE(ABORT, 'historical result update'); END;
    CREATE TRIGGER protect_heat_delete BEFORE DELETE ON heats
      WHEN EXISTS(SELECT 1 FROM results WHERE heat_id=OLD.id)
      BEGIN SELECT RAISE(ABORT, 'historical heat delete'); END;
    CREATE TRIGGER protect_heat_update BEFORE UPDATE ON heats
      WHEN EXISTS(SELECT 1 FROM results WHERE heat_id=OLD.id)
      BEGIN SELECT RAISE(ABORT, 'historical heat update'); END;
    CREATE TRIGGER protect_assignment_delete BEFORE DELETE ON assignments
      WHEN EXISTS(SELECT 1 FROM results WHERE heat_id=OLD.heat_id)
      BEGIN SELECT RAISE(ABORT, 'historical assignment delete'); END;
    CREATE TRIGGER protect_assignment_update BEFORE UPDATE ON assignments
      WHEN EXISTS(SELECT 1 FROM results WHERE heat_id=OLD.heat_id)
      BEGIN SELECT RAISE(ABORT, 'historical assignment update'); END;
    ''')
    service.commit_late_entries(service.preview_late_entries(event.id, [draft('one'), draft('two')]))
    assert historical(service, event.id) == before


def test_finalist_count_validation_preserves_scheduled_event(service):
    event, _ = event_with_racers(service, n=3, lanes=3)
    before = service.db.heats(event.id)
    with pytest.raises(RaceError, match='exceeds registration'):
        service.save_event('Updated', 3, 4, 99, event_id=event.id)
    assert service.db.heats(event.id) == before


def test_changing_finalist_count_does_not_move_preliminary_cursor(service):
    event, _ = event_with_racers(service)
    service.set_current_heat(event.id, service.db.heats(event.id)[-1].id)
    cursor = service.db.event(event.id).current_heat_id
    service.save_event(event.name, 2, 4, 3, event_id=event.id)
    assert service.db.event(event.id).current_heat_id == cursor


def test_all_missed_penalty_can_have_zero_actual_runs(service):
    event, _ = event_with_racers(service, n=2, lanes=2, runs=2)
    finish(service, event.id)
    before = service.db.heats(event.id)
    preview = service.preview_late_entries(event.id, [draft()], LatePolicy('last'))
    assert preview.actual_runs == 0 and preview.admin_runs == 2
    added = service.commit_late_entries(preview)
    assert service.db.heats(event.id) == before
    row = next(r for r in service.standings(event.id) if r.racer.id == added[0].id)
    assert row.completed == 0 and row.scoring_runs == 2 and row.points == 0
    assert service.db.event(event.id).status == 'ready_finals'


def test_six_children_and_later_group_do_not_dilute_actual_progress(service):
    event, racers = event_with_racers(service, n=7, lanes=2, runs=4)
    for h in service.db.heats(event.id)[:8]:
        save_heat(service, h)
    before = historical(service, event.id)
    preview = service.preview_late_entries(event.id, [draft(str(100+i)) for i in range(6)], LatePolicy('none'))
    assert preview.missed_runs == 2
    service.commit_late_entries(preview)
    second = service.preview_late_entries(event.id, [draft('200')])
    assert second.missed_runs == 2
    service.commit_late_entries(second)
    assert historical(service, event.id) == before
    counts = Counter(a.racer_id for h in service.db.heats(event.id) for a in h.assignments)
    counts.update(r.racer_id for r in service.db.administrative_results(event.id))
    assert set(counts.values()) == {4} and len(counts) == 14


def test_delete_event_with_late_batches_and_scores(service):
    event, _ = event_with_racers(service, n=2, lanes=2)
    save_heat(service, service.db.heats(event.id)[0])
    service.commit_late_entries(service.preview_late_entries(event.id, [draft()]))
    service.delete_event(event.id)
    for table in ['events', 'racers', 'heats', 'assignments', 'results', 'late_batches', 'administrative_results']:
        assert service.db.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert service.db.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_ordinary_invalidation_rolls_back_if_racer_insert_fails(service):
    event, _ = event_with_racers(service)
    save_heat(service, service.db.heats(event.id)[0])
    before = service.db.heats(event.id)
    service.db.connection.execute("CREATE TRIGGER fail_racer BEFORE INSERT ON racers BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.save_racer(event.id, 'New child', 'Car', 'new', confirmed=True)
    assert service.db.heats(event.id) == before


def test_tiebreak_partial_qualification_repeats_only_cutoff_group(service):
    event, racers = event_with_racers(service, n=4, lanes=4, runs=4, finalists=2)
    # Cyclic placements: every racer has every position once, all prelims tied.
    for round_index, heat in enumerate(service.db.heats(event.id)):
        assignments = sorted(heat.assignments, key=lambda a: a.racer_id)
        service.record_results(heat.id, ManualResultSource([
            LaneResult(a.lane, (i+round_index) % 4 + 1) for i, a in enumerate(assignments)]))
    assert len(service.qualification(event.id).tied) == 4
    service.generate_tiebreak(event.id)
    ids = sorted(r.id for r in racers)
    for index, heat in enumerate(service.db.heats(event.id, 'tiebreak')):
        places = {ids[0]: 1, ids[1]: 2 + index % 2, ids[2]: 3 - index % 2, ids[3]: 4}
        service.record_results(heat.id, ManualResultSource([LaneResult(a.lane, places[a.racer_id]) for a in heat.assignments]))
    plan = service.qualification(event.id)
    assert plan.automatic == (ids[0],) and set(plan.tied) == {ids[1], ids[2]} and plan.places == 1
    service.generate_tiebreak(event.id)
    round_two = [h for h in service.db.heats(event.id, 'tiebreak') if h.round_number == 2]
    assert {a.racer_id for h in round_two for a in h.assignments} == set(plan.tied)
    finish(service, event.id, 'tiebreak', ids[1])
    assert service.qualification(event.id).automatic == (ids[0], ids[1])
    service.generate_finals(event.id)


def test_migration_preserves_completed_v1_finals(tmp_path):
    path = tmp_path / 'v1-finals.sqlite3'
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    with con:
        con.execute("INSERT INTO events(id,name,lanes,runs,finalists,scoring,current_heat_id,status) VALUES('e','Finished V1 event',2,1,1,'placement','f','complete')")
        con.execute("INSERT INTO racers(id,event_id,name,car_name,car_number,number_key,tag,tag_key) VALUES('r','e','Original winner','Car','7','7','ABC','abc')")
        for key, stage in [('p','preliminary'), ('f','finals')]:
            con.execute('INSERT INTO heats VALUES(?,?,?,?)',(key,'e',stage,1))
            con.execute('INSERT INTO assignments VALUES(?,?,?)',(key,1,'r'))
            con.execute('INSERT INTO results VALUES(?,?,?,?)',(key,1,1,3.25))
    con.close()
    db = Database(path)
    try:
        service = RaceService(db)
        assert db.event('e').status == 'complete' and db.event('e').current_heat_id == 'f'
        assert service.standings('e','finals')[0].racer.id == 'r'
        assert db.heat('f').results[0].elapsed_seconds == 3.25
        assert not db.heats('e','tiebreak')
        service.save_racer('e','Renamed winner','Car','7','ABC','r')
        assert db.heat('f').completed
    finally:
        db.close()
