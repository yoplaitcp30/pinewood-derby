from collections import Counter
from itertools import combinations

import pytest

from pinewood_derby.models import RaceError
from pinewood_derby.scheduling import generate_schedule


@pytest.mark.parametrize('lanes', range(2, 7))
@pytest.mark.parametrize('count', [1, 2, 3, 5, 7, 10, 17, 23, 31])
@pytest.mark.parametrize('runs', [1, 3, 6, 7])
def test_schedule_invariants(lanes, count, runs):
    ids = [f'racer-{n:03}' for n in range(count)]
    heats = generate_schedule(ids, lanes, runs)
    participation = Counter()
    usage = {r: Counter({lane: 0 for lane in range(1, lanes + 1)}) for r in ids}
    total_lanes = Counter({lane: 0 for lane in range(1, lanes + 1)})
    for heat in heats:
        assert 1 <= len(heat) <= lanes
        assert len({a.racer_id for a in heat}) == len(heat)
        assert len({a.lane for a in heat}) == len(heat)
        for assignment in heat:
            assert 1 <= assignment.lane <= lanes
            participation[assignment.racer_id] += 1
            usage[assignment.racer_id][assignment.lane] += 1
            total_lanes[assignment.lane] += 1
    assert participation == Counter({r: runs for r in ids})
    for counts in usage.values():
        assert max(counts.values()) - min(counts.values()) <= 1
    assert max(total_lanes.values()) - min(total_lanes.values()) <= 1
    assert max(map(len, heats)) - min(map(len, heats)) <= 1
    assert len(heats) == max(runs, (count * runs + lanes - 1) // lanes)


@pytest.mark.parametrize('count,lanes', [(7, 2), (10, 3), (17, 4), (23, 5), (31, 6)])
def test_awkward_inputs_reproduce_and_have_partial_heats(count, lanes):
    ids = [str(i) for i in range(count)]
    first = generate_schedule(ids, lanes, 5)
    assert first == generate_schedule(list(reversed(ids)), lanes, 5)
    assert first == generate_schedule(ids, lanes, 5, seed=0)
    assert generate_schedule(ids, lanes, 5, seed=13) == generate_schedule(ids, lanes, 5, seed=13)
    if count * 5 % lanes:
        assert any(len(h) < lanes for h in first)


def test_opponents_vary_when_possible():
    heats = generate_schedule([str(i) for i in range(16)], 4, 4)
    opponents = {str(i): set() for i in range(16)}
    for heat in heats:
        for a, b in combinations(heat, 2):
            opponents[a.racer_id].add(b.racer_id)
            opponents[b.racer_id].add(a.racer_id)
    assert min(map(len, opponents.values())) >= 5


@pytest.mark.parametrize('ids,lanes,runs', [([], 4, 4), (['a', 'a'], 4, 4),
    (['a'], 1, 3), (['a'], 7, 3), (['a'], 4, 0), (['a'], 4, -1), (['a'], 2.5, 3)])
def test_bad_inputs(ids, lanes, runs):
    with pytest.raises(RaceError):
        generate_schedule(ids, lanes, runs)
