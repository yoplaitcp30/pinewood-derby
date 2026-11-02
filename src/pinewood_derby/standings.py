"""Pure standings calculations; rational arithmetic preserves genuine ties."""
from collections import defaultdict
from collections.abc import Iterable
from fractions import Fraction

from .models import AdministrativeResult, Heat, Racer, Standing
from .scoring import ScoringMethod


def calculate_standings(racers: Iterable[Racer], heats: Iterable[Heat],
                        scoring: ScoringMethod,
                        administrative: Iterable[AdministrativeResult] = ()) -> list[Standing]:
    entries = defaultdict(list)
    for heat in heats:
        if not heat.completed:
            continue
        results = {r.lane: r for r in heat.results}
        for assignment in heat.assignments:
            result = results[assignment.lane]
            entries[assignment.racer_id].append((result, len(heat.assignments)))
    admin = defaultdict(list)
    for result in administrative:
        admin[result.racer_id].append(result)
    rows = []
    for racer in racers:
        finishes = entries[racer.id]
        completed = len(finishes)
        wins = sum(r.position == 1 and size > 1 for r, size in finishes)
        points = sum((scoring.points(r, size) for r, size in finishes), Fraction())
        adjustments = admin[racer.id]
        scoring_runs = completed + len(adjustments)
        points += sum((Fraction(r.points) for r in adjustments), Fraction())
        average = points / scoring_runs if scoring_runs else Fraction()
        placements = sum(r.position for r, _ in finishes) + sum((Fraction(r.placement) for r in adjustments), Fraction())
        finish = placements / scoring_runs if scoring_runs else None
        key = (scoring_runs > 0, average, wins, -finish if finish is not None else Fraction())
        rows.append((key, racer, completed, wins, points, average, finish, len(adjustments), scoring_runs))
    rows.sort(key=lambda row: (row[1].name.casefold(), row[1].car_number, row[1].id))
    rows.sort(key=lambda row: row[0], reverse=True)
    standings = []
    previous = None
    rank = 0
    for index, (key, racer, completed, wins, points, average, finish, admin_count, scoring_runs) in enumerate(rows, 1):
        if key != previous:
            rank = index
        standings.append(Standing(rank, racer, completed, wins, float(points), float(average),
                                  float(finish) if finish is not None else None, key, admin_count, scoring_runs))
        previous = key
    return standings
