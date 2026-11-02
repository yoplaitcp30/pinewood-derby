"""Deterministic, balanced bipartite edge-coloring scheduler.

Each requested run is an edge between a racer and a lane. A matching is a
heat. Removing matchings that cover every maximum-degree vertex guarantees that
no racer is lost and no racer/lane appears twice in a heat. Alternating-path
swaps balance heat sizes without altering participation or lane usage.
"""
from collections import Counter
from itertools import combinations
import random
from collections.abc import Sequence

from .models import Assignment, RaceError


def _balance(heats: list[list[Assignment]]) -> None:
    while True:
        large = max(range(len(heats)), key=lambda i: len(heats[i]))
        small = min(range(len(heats)), key=lambda i: len(heats[i]))
        if len(heats[large]) - len(heats[small]) < 2:
            return
        edges = [(0, a) for a in heats[large]] + [(1, a) for a in heats[small]]
        adjacency: dict[tuple, list[int]] = {}
        for i, (_, a) in enumerate(edges):
            for node in (("r", a.racer_id), ("l", a.lane)):
                adjacency.setdefault(node, []).append(i)
        unseen = set(range(len(edges)))
        while unseen:
            pending = [min(unseen)]
            component = set()
            while pending:
                idx = pending.pop()
                if idx in component:
                    continue
                component.add(idx)
                _, a = edges[idx]
                for node in (("r", a.racer_id), ("l", a.lane)):
                    pending.extend(i for i in adjacency[node] if i not in component)
            unseen -= component
            if sum(1 if edges[i][0] == 0 else -1 for i in component) == 1:
                heats[large] = [a for i, (side, a) in enumerate(edges)
                                if (side == 0) != (i in component)]
                heats[small] = [a for i, (side, a) in enumerate(edges)
                                if (side == 1) != (i in component)]
                break


def _candidate(ids: list[str], lanes: int, runs: int, seed: int) -> list[list[Assignment]]:
    rng = random.Random(seed)
    order = ids[:]
    rng.shuffle(order)
    graph = [Counter((racer * runs + run) % lanes for run in range(runs))
             for racer in range(len(ids))]
    return _color_graph(graph, order, lanes, rng)


def _color_graph(graph: list[Counter], order: list[str], lanes: int,
                 rng: random.Random, pairs: Counter | None = None) -> list[list[Assignment]]:
    """Color arbitrary remaining lane demands; caller retains historical edges."""
    degree = max([sum(row.values()) for row in graph]
                 + [sum(row[lane] for row in graph) for lane in range(lanes)], default=0)
    if not degree:
        return []
    pairs = Counter() if pairs is None else pairs.copy()
    heats = []
    for remaining in range(degree, 0, -1):
        mandatory_lanes = sum(1 << lane for lane in range(lanes)
                              if sum(row[lane] for row in graph) == remaining)
        racers = [i for i, row in enumerate(graph) if sum(row.values())]
        rng.shuffle(racers)
        mandatory = {i for i in racers if sum(graph[i].values()) == remaining}
        racers.sort(key=lambda i: i not in mandatory)
        # At most 64 lane masks: process each racer once, choosing at most one
        # lane. Mandatory racers cannot be skipped. Covering all vertices whose
        # degree equals the remaining heat count guarantees future feasibility.
        # Keeping one candidate per mask is exact for feasibility, heuristic for
        # the pair-history objective (which depends on the selected racers).
        states: dict[int, tuple[int, tuple]] = {0: (0, ())}
        for racer in racers:
            next_states = {} if racer in mandatory else dict(states)
            choices = [lane for lane, count in graph[racer].items() if count]
            rng.shuffle(choices)
            for mask, (cost, selected) in states.items():
                extra = sum(pairs[tuple(sorted((racer, other)))] for other, _ in selected)
                for lane in choices:
                    bit = 1 << lane
                    if mask & bit:
                        continue
                    new_mask = mask | bit
                    new_cost = cost + extra
                    if new_mask not in next_states or new_cost < next_states[new_mask][0]:
                        next_states[new_mask] = (new_cost, selected + ((racer, lane),))
            states = next_states
        eligible = [mask for mask in states if mask & mandatory_lanes == mandatory_lanes]
        if not eligible:
            raise RuntimeError("Internal scheduling error: no feasible heat")
        mask = min(eligible, key=lambda mask: (-mask.bit_count(), states[mask][0], mask))
        selected = states[mask][1]
        heat = []
        for racer, lane in selected:
            graph[racer][lane] -= 1
            heat.append(Assignment(lane + 1, order[racer]))
        pairs.update(tuple(sorted(pair)) for pair in combinations([i for i, _ in selected], 2))
        heats.append(heat)
    _balance(heats)
    return [sorted(heat, key=lambda a: a.lane) for heat in heats]


def generate_schedule(racer_ids: Sequence[str], lanes: int, runs: int,
                      seed: int | None = None) -> tuple[tuple[Assignment, ...], ...]:
    """Exact runs, optimal per-racer lane balance, and balanced heat sizes.

    Stable IDs produce stable output regardless of input order. A supplied seed
    selects another reproducible schedule. Opponent variety is a heuristic.
    """
    if type(lanes) is not int or not 2 <= lanes <= 6:
        raise RaceError("Track lanes must be between 2 and 6.")
    if type(runs) is not int or runs < 1:
        raise RaceError("Runs per racer must be a positive integer.")
    ids = sorted(racer_ids)
    if not ids or len(ids) != len(set(ids)):
        raise RaceError("Register at least one racer; racer IDs must be unique.")
    best = None
    best_cost = None
    for attempt in range(6):
        heats = _candidate(ids, lanes, runs, (0 if seed is None else seed) + attempt * 104729)
        opponents: Counter = Counter()
        for heat in heats:
            opponents.update(tuple(sorted(pair)) for pair in combinations([a.racer_id for a in heat], 2))
        cost = sum(count * count for count in opponents.values())
        if best_cost is None or cost < best_cost:
            best, best_cost = heats, cost
    return tuple(tuple(heat) for heat in best)
