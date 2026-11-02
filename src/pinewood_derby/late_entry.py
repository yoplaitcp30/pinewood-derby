"""Pure late-entry policy and progress calculations (never device results)."""
from collections.abc import Sequence
from fractions import Fraction

from .models import Heat, LatePolicy, Racer, RaceError

POLICIES = {
    'none': 'No penalty (race all runs)',
    'last': 'Last-place equivalent',
    'above_last': 'One-above-last-place equivalent',
    'placement': 'Specific placement equivalent',
    'points': 'Custom points (0–100)',
}


def policy_score(policy: LatePolicy, lanes: int, scoring: str = 'placement') -> tuple[Fraction, Fraction] | None:
    if policy.kind not in POLICIES:
        raise RaceError('Unknown late-entry policy.')
    if policy.kind == 'none':
        if policy.value is not None:
            raise RaceError('No-penalty policy does not take a value.')
        return None
    if scoring != 'placement':
        raise RaceError('This scoring method does not support late-entry penalty equivalents. Use no penalty.')
    if policy.kind in ('last', 'above_last'):
        if policy.value is not None:
            raise RaceError('This policy does not take a custom value.')
        position = Fraction(lanes if policy.kind == 'last' else lanes - 1)
        return Fraction(100) * (lanes - position) / (lanes - 1), position
    try:
        value = Fraction(str(policy.value))
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise RaceError('Enter a finite numeric late-policy value.') from exc
    if policy.kind == 'placement':
        if value.denominator != 1 or not 1 <= value <= lanes:
            raise RaceError(f'Specific placement must be a whole number from 1 to {lanes}.')
        return Fraction(100) * (lanes - value) / (lanes - 1), value
    if not 0 <= value <= 100:
        raise RaceError('Custom points must be between 0 and 100.')
    return value, Fraction(lanes) - value * (lanes - 1) / 100


def missed_runs(racers: Sequence[Racer], completed: Sequence[Heat], target: int) -> int:
    """Floor of actual runs per on-time entrant; late groups cannot dilute it."""
    cohort = {r.id for r in racers if not r.late_entry}
    if not cohort:
        raise RaceError('No on-time cohort is available to calculate late-entry progress.')
    appearances = sum(a.racer_id in cohort for h in completed if h.completed and h.stage == 'preliminary'
                      for a in h.assignments)
    return min(target, appearances // len(cohort))
