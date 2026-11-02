import sqlite3
import pytest

from pinewood_derby.database import Database, SCHEMA_VERSION
from pinewood_derby.hardware import ManualResultSource, ScheduledLaneIdentifier, lane_mismatches
from pinewood_derby.models import ConfirmationRequired, LaneResult, RaceError
from pinewood_derby.service import RaceService


def setup_event(service, count=7, lanes=2, runs=3, finalists=2):
    event = service.save_event('Pack championship', lanes, runs, finalists)
    for i in range(count):
        service.save_racer(event.id, f'Racer {i}', f'Car {i}', str(i))
    service.generate_preliminary(event.id)
    return event


def results_for(heat, reverse=False):
    positions = list(range(1, len(heat.assignments)+1))
    if reverse:
        positions.reverse()
    return ManualResultSource([LaneResult(a.lane, position) for a, position in zip(heat.assignments, positions)])


def finish_stage(service, event_id, stage='preliminary'):
    for heat in service.db.heats(event_id, stage):
        service.record_results(heat.id, results_for(heat), advance=True)


def test_database_reload_and_edit_result(service):
    event = setup_event(service)
    heat = service.db.heats(event.id)[0]
    service.record_results(heat.id, results_for(heat), advance=True)
    before = service.standings(event.id)
    path = service.db.connection.execute('PRAGMA database_list').fetchone()[2]
    second = Database(path)
    try:
        restored = RaceService(second)
        assert second.racers(event.id) == service.db.racers(event.id)
        assert second.heats(event.id) == service.db.heats(event.id)
        assert restored.standings(event.id) == before
        assert second.event(event.id).current_heat_id != heat.id
        restored.record_results(heat.id, results_for(heat, reverse=True))
        assert restored.standings(event.id) != before
        assert sum(r.completed for r in restored.standings(event.id)) == len(heat.assignments)
    finally:
        second.close()


def test_complete_lifecycle_and_finals_reset(service):
    event = setup_event(service, count=10, lanes=3, runs=3, finalists=3)
    with pytest.raises(RaceError, match='Complete all'):
        service.generate_finals(event.id)
    finish_stage(service, event.id)
    assert service.db.event(event.id).status == 'ready_finals'
    prelim = service.standings(event.id)
    for _ in range(10):
        if not service.qualification(event.id).tied:
            break
        service.generate_tiebreak(event.id)
        for heat in service.db.heats(event.id, 'tiebreak'):
            if not heat.completed:
                ordered = sorted(heat.assignments, key=lambda a: a.racer_id)
                service.record_results(heat.id, ManualResultSource([
                    LaneResult(a.lane, i+1) for i, a in enumerate(ordered)]))
    service.generate_finals(event.id)
    assert service.db.event(event.id).status == 'finals'
    assert all(row.completed == 0 for row in service.standings(event.id, 'finals'))
    final_heats = service.db.heats(event.id, 'finals')
    assert len(final_heats) == 3
    with pytest.raises(RaceError, match='already exist'):
        service.generate_finals(event.id)
    h = service.db.heats(event.id, 'preliminary')[0]
    with pytest.raises(RaceError, match='Reset'):
        service.record_results(h.id, results_for(h))
    finish_stage(service, event.id, 'finals')
    assert service.db.event(event.id).status == 'tiebreak'
    for heat in service.tiebreak_heats(event.id, 'finals'):
        ordered = sorted(heat.assignments, key=lambda a: a.racer_id)
        service.record_results(heat.id, ManualResultSource([
            LaneResult(a.lane, i+1) for i, a in enumerate(ordered)]))
    assert service.db.event(event.id).status == 'complete'
    assert service.standings(event.id) == prelim
    service.reset_finals(event.id)
    assert service.db.event(event.id).status == 'ready_finals'
    assert not service.db.heats(event.id, 'finals')
    service.reset_tiebreaks(event.id)
    service.record_results(h.id, results_for(h, True))


@pytest.mark.parametrize('bad', [
    [LaneResult(1, 1), LaneResult(2, 1)],
    [LaneResult(1, 0), LaneResult(2, 2)],
    [LaneResult(1, 1)],
    [LaneResult(1, 1), LaneResult(3, 2)],
    [LaneResult(1, 1), LaneResult(1, 2)],
    [LaneResult(1, 1.0), LaneResult(2, 2)],
    [LaneResult(1, 1, float('nan')), LaneResult(2, 2)],
    [LaneResult(1, 1, float('inf')), LaneResult(2, 2)],
    [LaneResult(1, 1, -1), LaneResult(2, 2)],
    [LaneResult(1, 1, True), LaneResult(2, 2)],
])
def test_invalid_result_does_not_destroy_saved_result(service, bad):
    event = setup_event(service, count=2)
    heat = service.db.heats(event.id)[0]
    service.record_results(heat.id, results_for(heat))
    original = service.db.heat(heat.id)
    with pytest.raises(RaceError):
        service.record_results(heat.id, ManualResultSource(bad))
    assert service.db.heat(heat.id) == original


def test_optional_times_and_adapter_injection(service):
    event = setup_event(service, count=2)
    heat = service.db.heats(event.id)[0]
    class FutureTimer:
        def obtain_results(self, heat):
            return [LaneResult(a.lane, i+1, 3.218 + i / 10) for i, a in enumerate(heat.assignments)]
    service.record_results(heat.id, FutureTimer())
    assert service.db.heat(heat.id).results[0].elapsed_seconds == 3.218
    assert service.check_lanes(heat.id) == []
    detected = ScheduledLaneIdentifier().identify(heat)
    assert lane_mismatches(heat, detected) == []
    detected[1], detected[2] = detected[2], detected[1]
    assert len(lane_mismatches(heat, detected)) == 2


def test_registration_validation_and_explicit_invalidation(service):
    event = service.save_event('Race', 4, 4, 2)
    a = service.save_racer(event.id, 'Alex', 'Rocket', 'A', 'TAG')
    with pytest.raises(RaceError, match='already registered'):
        service.save_racer(event.id, 'Avery', '', 'a')
    with pytest.raises(RaceError, match='already registered'):
        service.save_racer(event.id, 'Avery', '', 'B', 'tag')
    b = service.save_racer(event.id, 'Avery', '', 'B')
    service.save_racer(event.id, 'Avery changed', 'Comet', 'B', racer_id=b.id)
    service.remove_racer(event.id, a.id)
    with pytest.raises(RaceError, match='exceeds'):
        service.generate_preliminary(event.id)
    service.save_racer(event.id, 'Alex', '', 'A')
    service.generate_preliminary(event.id)
    with pytest.raises(ConfirmationRequired):
        service.remove_racer(event.id, b.id)
    with pytest.raises(ConfirmationRequired):
        service.save_event('changed', 2, 2, 2, event_id=event.id)
    service.reset_schedule(event.id)
    assert service.db.event(event.id).status == 'registration'
    assert service.db.event(event.id).current_heat_id is None
    service.remove_racer(event.id, b.id)


def test_event_isolation_and_deletion(service):
    a = setup_event(service)
    b = setup_event(service)
    h = service.db.heats(a.id)[0]
    with pytest.raises(RaceError, match='another event'):
        service.set_current_heat(b.id, h.id)
    service.record_results(h.id, results_for(h))
    service.delete_event(a.id)
    assert service.db.events() == [service.db.event(b.id)]
    assert len(service.db.racers(b.id)) == 7
    assert service.db.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_schema_version_guard(tmp_path):
    path = tmp_path / 'future.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION + 1}')
    with pytest.raises(RaceError, match='newer'):
        Database(path)


def test_single_racer_event(service):
    event = setup_event(service, count=1, lanes=6, runs=2, finalists=1)
    finish_stage(service, event.id)
    service.generate_finals(event.id)
    finish_stage(service, event.id, 'finals')
    assert service.db.event(event.id).status == 'complete'
    row = service.standings(event.id, 'finals')[0]
    assert row.completed == 6 and row.rank == 1 and row.wins == 0


def test_transaction_rollback_on_failed_result_insert(service):
    event = setup_event(service, count=2)
    heat = service.db.heats(event.id)[0]
    service.record_results(heat.id, results_for(heat))
    before = service.db.heat(heat.id)
    service.db.connection.execute("CREATE TRIGGER fail_result BEFORE INSERT ON results BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.record_results(heat.id, results_for(heat, True))
    assert service.db.heat(heat.id) == before
