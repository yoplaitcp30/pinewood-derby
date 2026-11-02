"""Schedule future runs around immutable completed race history."""
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import combinations
import random

from .models import Assignment, FairnessReport, Heat, RaceError, RemainingSchedule
from .scheduling import _color_graph


def signature(assignments: Sequence[Assignment]) -> tuple:
    return tuple(sorted((a.lane, a.racer_id) for a in assignments))


def rebalance_remaining(remaining: Mapping[str, int], completed: Sequence[Heat], lanes: int,
                        previous: Sequence[Heat] = ()) -> RemainingSchedule:
    """Exact future participation, historical lane deficits, honest metrics.

    Four deterministic greedy lane-demand candidates are edge-colored. Completed
    heats are read-only inputs. Optimal fairness is not promised with fixed history.
    """
    if type(lanes) is not int or not 2 <= lanes <= 6:
        raise RaceError('Track lanes must be between 2 and 6.')
    if any(type(n) is not int or n < 0 for n in remaining.values()):
        raise RaceError('Remaining participation must contain nonnegative whole numbers.')
    if any(not h.completed or h.stage != 'preliminary' for h in completed):
        raise RaceError('Historical heats must be completed preliminaries.')
    usage = {r: Counter({lane: 0 for lane in range(lanes)}) for r in remaining}
    global_usage = Counter({lane: 0 for lane in range(lanes)})
    pairs = Counter()
    for heat in completed:
        for a in heat.assignments:
            if a.racer_id not in usage or not 1 <= a.lane <= lanes:
                raise RaceError('Historical assignment does not match the registered field/track.')
            usage[a.racer_id][a.lane - 1] += 1
            global_usage[a.lane - 1] += 1
        pairs.update(tuple(sorted(pair)) for pair in combinations([a.racer_id for a in heat.assignments], 2))
    previous_counts = Counter(signature(h.assignments) for h in previous)
    best = None
    best_cost = None
    for seed in range(4):
        rng = random.Random(seed)
        ids = sorted(remaining)
        rng.shuffle(ids)
        graph = []
        planned_global = global_usage.copy()
        for racer in ids:
            personal = usage[racer].copy()
            row = Counter()
            for _ in range(remaining[racer]):
                choices = list(range(lanes))
                rng.shuffle(choices)
                lane = min(choices, key=lambda lane: (personal[lane], planned_global[lane]))
                row[lane] += 1
                personal[lane] += 1
                planned_global[lane] += 1
            graph.append(row)
        history_pairs = Counter({tuple(sorted((ids.index(a), ids.index(b)))): count
                                 for (a, b), count in pairs.items()})
        schedule = _color_graph(graph, ids, lanes, rng, history_pairs)
        # Reuse matching old heats first in their prior order; the service can
        # retain their IDs and numbers. Only the remaining unmatched heats move.
        by_signature = {}
        for index, assignments in enumerate(schedule):
            by_signature.setdefault(signature(assignments), []).append(index)
        chosen = []
        for old in previous:
            matches = by_signature.get(signature(old.assignments), [])
            if matches:
                chosen.append(matches.pop(0))
        selected = set(chosen)
        chosen.extend(i for i in range(len(schedule)) if i not in selected)
        schedule = [schedule[i] for i in chosen]
        final_usage = {r: counts.copy() for r, counts in usage.items()}
        final_global = global_usage.copy()
        final_pairs = pairs.copy()
        for assignments in schedule:
            for a in assignments:
                final_usage[a.racer_id][a.lane - 1] += 1
                final_global[a.lane - 1] += 1
            final_pairs.update(tuple(sorted(pair)) for pair in combinations([a.racer_id for a in assignments], 2))
        retained = sum((Counter(signature(h) for h in schedule) & previous_counts).values())
        report = FairnessReport(len(schedule), sum(len(h) == 1 for h in schedule),
                                max((max(c.values()) - min(c.values()) for c in final_usage.values()), default=0),
                                max(final_global.values()) - min(final_global.values()),
                                sum(max(0, count - 1) for count in final_pairs.values()), retained)
        cost = (sum(sum(v*v for v in c.values()) for c in final_usage.values()),
                sum(v*v for v in final_global.values()), report.solo_heats,
                report.repeated_opponent_meetings, len(schedule), -retained)
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best = RemainingSchedule(tuple(tuple(h) for h in schedule), report)
    return best
