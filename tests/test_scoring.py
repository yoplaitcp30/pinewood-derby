from fractions import Fraction
import pytest

from pinewood_derby.finals import qualification, select_qualifiers
from pinewood_derby.models import Assignment, Heat, LaneResult, Racer, RaceError
from pinewood_derby.scoring import PlacementScoring
from pinewood_derby.standings import calculate_standings


def racers():
    return [Racer(str(i), 'event', f'Racer {i}', f'Car {i}', str(i)) for i in range(3)]


def heat(results):
    return Heat('heat', 'event', 'preliminary', 1,
                tuple(Assignment(i+1, str(i)) for i in range(3)), tuple(results))


def test_normalized_scoring():
    scoring = PlacementScoring()
    assert scoring.points(LaneResult(1, 1), 4) == 100
    assert scoring.points(LaneResult(1, 4), 4) == 0
    assert scoring.points(LaneResult(1, 2), 4) == Fraction(200, 3)
    assert scoring.points(LaneResult(1, 1), 2) == 100
    assert scoring.points(LaneResult(1, 1), 1) == 100


def test_standings_wins_totals_and_unrun_racers():
    h = heat([LaneResult(1, 2), LaneResult(2, 1), LaneResult(3, 3)])
    rows = calculate_standings(racers(), [h], PlacementScoring())
    assert [r.racer.id for r in rows] == ['1', '0', '2']
    assert [r.rank for r in rows] == [1, 2, 3]
    assert [r.points for r in rows] == [100, 50, 0]
    assert rows[0].wins == 1 and rows[0].completed == 1
    assert rows[1].average_finish == 2
    empty = calculate_standings(racers(), [], PlacementScoring())
    assert [r.rank for r in empty] == [1, 1, 1]
    assert all(r.completed == 0 and r.average_finish is None for r in empty)


def test_true_ties_require_cutoff_selection():
    heats = [heat([LaneResult(1, 1), LaneResult(2, 2), LaneResult(3, 3)]),
             heat([LaneResult(1, 2), LaneResult(2, 1), LaneResult(3, 3)])]
    rows = calculate_standings(racers(), heats, PlacementScoring())
    assert [r.rank for r in rows] == [1, 1, 3]
    plan = qualification(rows, 1)
    assert plan.automatic == () and set(plan.tied) == {'0', '1'} and plan.places == 1
    with pytest.raises(RaceError):
        select_qualifiers(plan)
    with pytest.raises(RaceError):
        select_qualifiers(plan, ['2'])
    assert select_qualifiers(plan, ['1']) == ('1',)
    assert set(select_qualifiers(qualification(rows, 2))) == {'0', '1'}


def test_rational_points_do_not_break_ties():
    rs = [Racer(str(i), 'e', str(i), '', str(i)) for i in range(4)]
    heats = [Heat(str(k), 'e', 'preliminary', k + 1,
                  tuple(Assignment(i + 1, str(i)) for i in range(4)),
                  tuple(LaneResult(i + 1, (i + k) % 4 + 1) for i in range(4))) for k in range(4)]
    rows = calculate_standings(rs, heats, PlacementScoring())
    assert all(row.rank == 1 and row.points == 200 for row in rows)


def test_solo_bye_is_not_a_win():
    h = Heat('h', 'event', 'preliminary', 1, (Assignment(1, '0'),), (LaneResult(1, 1),))
    row = calculate_standings(racers()[:1], [h], PlacementScoring())[0]
    assert row.points == 100 and row.wins == 0
