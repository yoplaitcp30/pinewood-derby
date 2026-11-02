"""Final placements use the recorded, lane-balanced tiebreak pipeline."""
from collections import Counter
import sqlite3

import pytest

from pinewood_derby.database import Database, SCHEMA, MIGRATION_V2
from pinewood_derby.hardware import ManualResultSource
from pinewood_derby.models import ConfirmationRequired, LaneResult
from pinewood_derby.service import RaceService


def save_order(service, heat, order, **kwargs):
    assignments = sorted(heat.assignments, key=lambda a: order.index(a.racer_id))
    service.record_results(heat.id, ManualResultSource([
        LaneResult(a.lane, position) for position, a in enumerate(assignments, 1)]), **kwargs)


def finals_event(service, count=3, lanes=4):
    event = service.save_event('Placement tests', lanes, lanes, count)
    ids = [service.save_racer(event.id, f'Racer {i}', '', str(i)).id for i in range(count)]
    service.generate_preliminary(event.id)
    for heat in service.db.heats(event.id):
        save_order(service, heat, ids)
    service.generate_finals(event.id)
    return event.id, ids


def pending_round(service, event_id):
    number = service.final_placements(event_id).pending_round
    assert number is not None
    return [h for h in service.tiebreak_heats(event_id, 'finals') if h.round_number == number]


def resolve_round(service, event_id, order):
    for heat in pending_round(service, event_id):
        if not heat.completed:
            save_order(service, heat, order, advance=True)


def assert_order(service, event_id, order):
    assert service.db.event(event_id).status == 'complete'
    assert not service.final_placements(event_id).tied
    rows = service.standings(event_id, 'finals')
    assert [row.racer.id for row in rows] == order
    assert [row.rank for row in rows] == list(range(1, len(order) + 1))


@pytest.mark.parametrize('tie_start', [0, 1])
def test_first_second_and_second_third_ties(service, tie_start):
    event_id, ids = finals_event(service)
    swapped = ids.copy()
    swapped[tie_start:tie_start + 2] = reversed(swapped[tie_start:tie_start + 2])
    finals = service.db.heats(event_id, 'finals')
    assert len(finals) == 4
    for index, heat in enumerate(finals):
        save_order(service, heat, ids if index % 2 == 0 else swapped, advance=True)
    original_finals = service.db.heats(event_id, 'finals')
    assert service.db.event(event_id).status == 'tiebreak'
    tied = set(ids[tie_start:tie_start + 2])
    heats = pending_round(service, event_id)
    assert {a.racer_id for h in heats for a in h.assignments} == tied
    for racer in tied:
        assert Counter(a.lane for h in heats for a in h.assignments if a.racer_id == racer) == Counter(range(1, 5))
    assert service.db.event(event_id).current_heat_id == heats[0].id
    assert service.db.heats(event_id)[-len(heats):] == heats
    assert service.qualification(event_id).automatic == tuple(ids)
    # The racer displayed second in the tie wins; alphabetical order is irrelevant.
    resolve_round(service, event_id, swapped)
    assert_order(service, event_id, swapped)
    assert service.db.heats(event_id, 'finals') == original_finals
    assert all(h.completed for h in service.tiebreak_heats(event_id, 'finals'))
    assert all(row.completed == 4 for row in service.standings(event_id, 'finals'))


@pytest.mark.parametrize('lanes', [2, 3, 6])
def test_three_way_tie(service, lanes):
    event_id, ids = finals_event(service, lanes=lanes)
    for index, heat in enumerate(service.db.heats(event_id, 'finals')):
        if lanes == 2:
            pair = {a.racer_id for a in heat.assignments}
            winner = next(ids[i] for i in range(3) if pair == {ids[i], ids[(i + 1) % 3]})
            order = [winner] + [key for key in ids if key != winner]
        else:
            offset = index % 3
            order = ids[offset:] + ids[:offset]
        save_order(service, heat, order)
    assert set(service.final_placements(event_id).tied) == set(ids)
    heats = pending_round(service, event_id)
    for racer in ids:
        assert Counter(a.lane for h in heats for a in h.assignments if a.racer_id == racer) == Counter(range(1, lanes + 1))
    expected = list(reversed(ids))
    resolve_round(service, event_id, expected)
    assert_order(service, event_id, expected)


def test_multiple_placement_groups_and_partial_resolution(service):
    event_id, ids = finals_event(service, count=4)
    # All four racers have the same finals scores.
    for index, heat in enumerate(service.db.heats(event_id, 'finals')):
        save_order(service, heat, ids[index:] + ids[:index])
    first_round = pending_round(service, event_id)
    # Split into tied 1st/2nd and tied 3rd/4th groups.
    for index, heat in enumerate(first_round):
        save_order(service, heat, ids if index % 2 == 0 else [ids[1], ids[0], ids[3], ids[2]])
    assert [set(g) for g in service.final_placements(event_id).groups] == [set(ids[:2]), set(ids[2:])]
    assert {a.racer_id for h in pending_round(service, event_id) for a in h.assignments} == set(ids[:2])
    resolve_round(service, event_id, [ids[1], ids[0]])
    assert service.db.event(event_id).status == 'tiebreak'
    assert {a.racer_id for h in pending_round(service, event_id) for a in h.assignments} == set(ids[2:])
    resolve_round(service, event_id, [ids[3], ids[2]])
    assert_order(service, event_id, [ids[1], ids[0], ids[3], ids[2]])


def two_way_tie(service):
    event_id, ids = finals_event(service, count=2, lanes=2)
    for index, heat in enumerate(service.db.heats(event_id, 'finals')):
        save_order(service, heat, ids if index == 0 else ids[::-1])
    return event_id, ids


def test_repeated_ties_automatically_generate_full_rounds(service):
    event_id, ids = two_way_tie(service)
    for number in range(1, 4):
        heats = pending_round(service, event_id)
        assert {h.round_number for h in heats} == {number}
        for index, heat in enumerate(heats):
            save_order(service, heat, ids if index == 0 else ids[::-1])
        assert service.db.event(event_id).status == 'tiebreak'
    resolve_round(service, event_id, ids[::-1])
    assert_order(service, event_id, ids[::-1])
    assert len(service.tiebreak_heats(event_id, 'finals')) == 8


def test_corrected_result_reopens_and_recalculates_placements(service):
    event_id, ids = two_way_tie(service)
    first_round = pending_round(service, event_id)
    resolve_round(service, event_id, ids)
    assert_order(service, event_id, ids)
    save_order(service, first_round[0], ids[::-1])
    assert service.db.event(event_id).status == 'tiebreak'
    later = pending_round(service, event_id)
    resolve_round(service, event_id, ids)
    assert_order(service, event_id, ids)
    before = service.db.heats(event_id)
    # Saving the same result is harmless, even with a completed later round.
    save_order(service, first_round[0], ids[::-1])
    assert service.db.heats(event_id) == before
    with pytest.raises(ConfirmationRequired) as exc:
        save_order(service, first_round[1], ids[::-1])
    assert exc.value.impact.completed_heats == 2
    assert service.db.heats(event_id) == before
    save_order(service, first_round[1], ids[::-1], confirmed=True)
    assert_order(service, event_id, ids[::-1])
    assert not {h.id for h in later} & {h.id for h in service.db.heats(event_id)}
    assert service.db.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_pending_round_survives_close_reload_without_duplication(tmp_path):
    path = tmp_path / 'resume.sqlite3'
    db = Database(path)
    service = RaceService(db)
    event_id, ids = two_way_tie(service)
    heats = pending_round(service, event_id)
    save_order(service, heats[0], ids, advance=True)
    before = db.heats(event_id)
    cursor = db.event(event_id).current_heat_id
    db.close()
    db = Database(path)
    try:
        restored = RaceService(db)
        assert db.heats(event_id) == before
        assert db.event(event_id).current_heat_id == cursor == heats[1].id
        assert db.event(event_id).status == 'tiebreak'
        resolve_round(restored, event_id, ids)
        assert_order(restored, event_id, ids)
        saved = restored.standings(event_id, 'finals')
    finally:
        db.close()
    db = Database(path)
    try:
        assert RaceService(db).standings(event_id, 'finals') == saved
        assert db.event(event_id).status == 'complete'
    finally:
        db.close()


def test_finals_correction_and_reset_invalidate_placement_rounds(service):
    event_id, ids = two_way_tie(service)
    resolve_round(service, event_id, ids)
    final = service.db.heats(event_id, 'finals')[1]
    with pytest.raises(ConfirmationRequired):
        save_order(service, final, ids)
    save_order(service, final, ids, confirmed=True)
    assert not service.tiebreak_heats(event_id, 'finals')
    assert_order(service, event_id, ids)
    save_order(service, final, ids[::-1])
    assert pending_round(service, event_id)
    prelim = service.db.heats(event_id, 'preliminary')
    service.reset_finals(event_id)
    assert service.db.heats(event_id) == prelim
    assert service.db.event(event_id).status == 'ready_finals'


def test_failed_automatic_round_creation_rolls_back_result(service):
    event_id, ids = finals_event(service, count=2, lanes=2)
    finals = service.db.heats(event_id, 'finals')
    save_order(service, finals[0], ids)
    before = service.db.heats(event_id)
    service.db.connection.execute("CREATE TRIGGER fail_round BEFORE INSERT ON heats "
                                  "BEGIN SELECT RAISE(ABORT, 'failed round'); END")
    with pytest.raises(sqlite3.IntegrityError):
        save_order(service, finals[1], ids[::-1])
    assert service.db.heats(event_id) == before
    assert service.db.event(event_id).status == 'finals'


def test_no_tie_needs_no_extra_round_and_reset_preserves_qualification(service):
    event = service.save_event('Qualification and placement', 2, 2, 2)
    ids = [service.save_racer(event.id, str(i), '', str(i)).id for i in range(3)]
    service.generate_preliminary(event.id)
    for heat in service.db.heats(event.id):
        pair = {a.racer_id for a in heat.assignments}
        winner = next(ids[i] for i in range(3) if pair == {ids[i], ids[(i + 1) % 3]})
        save_order(service, heat, [winner] + [key for key in ids if key != winner])
    service.generate_tiebreak(event.id)
    for heat in service.tiebreak_heats(event.id):
        save_order(service, heat, ids)
    qualification_heats = service.tiebreak_heats(event.id)
    plan = service.qualification(event.id)
    assert set(plan.automatic) == set(ids[:2])
    service.generate_finals(event.id)
    for heat in service.db.heats(event.id, 'finals'):
        save_order(service, heat, ids)
    assert_order(service, event.id, ids[:2])
    assert not service.tiebreak_heats(event.id, 'finals')
    final = service.db.heats(event.id, 'finals')[-1]
    save_order(service, final, ids[::-1])
    assert pending_round(service, event.id)[0].round_number == 2
    resolve_round(service, event.id, ids[::-1])
    assert_order(service, event.id, ids[:2][::-1])
    assert service.qualification(event.id) == plan
    service.reset_finals(event.id)
    assert service.tiebreak_heats(event.id) == qualification_heats
    assert not service.tiebreak_heats(event.id, 'finals')
    assert service.qualification(event.id) == plan


def test_v2_completed_tie_migrates_to_pending_recorded_round(tmp_path):
    path = tmp_path / 'v2.sqlite3'
    with sqlite3.connect(path) as con:
        con.executescript(SCHEMA)
        con.executescript(MIGRATION_V2)
        con.execute('PRAGMA user_version = 2')
        con.execute("INSERT INTO events(id,name,lanes,runs,finalists,scoring,status) "
                    "VALUES('e','Old tied finals',2,2,2,'placement','complete')")
        for key in ['a', 'b']:
            con.execute('INSERT INTO racers(id,event_id,name,car_name,car_number,number_key) VALUES(?,?,?,?,?,?)',
                        (key, 'e', key, '', key, key))
        for number in [1, 2]:
            key = str(number)
            con.execute("INSERT INTO heats(id,event_id,stage,number) VALUES(?,'e','finals',?)", (key, number))
            for lane, racer in enumerate(['a', 'b'], 1):
                con.execute('INSERT INTO assignments VALUES(?,?,?)', (key, lane, racer))
                con.execute('INSERT INTO results VALUES(?,?,?,NULL)', (key, lane, lane if number == 1 else 3 - lane))
    db = Database(path)
    try:
        finals = db.heats('e', 'finals')
        restored = RaceService(db)
        assert db.event('e').status == 'tiebreak'
        assert db.heats('e', 'finals') == finals
        heats = pending_round(restored, 'e')
        RaceService(db)
        assert pending_round(restored, 'e') == heats
        resolve_round(restored, 'e', ['b', 'a'])
        assert_order(restored, 'e', ['b', 'a'])
    finally:
        db.close()
