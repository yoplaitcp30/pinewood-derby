# Architecture and implementation plan

## Layers

1. Immutable domain records describe event configuration, racers, heat assignments,
   results (including nullable elapsed seconds), and standings.
2. A pure deterministic scheduler produces lane assignments independently of Qt and
   SQLite. A scoring protocol and standings calculator rank any completed stage.
3. Finals qualification and heat generation are stage policies, separate from UI.
4. SQLite owns durable records and versioned migrations. An application service
   validates state transitions and commits each operation atomically.
5. Result-source and lane-identification protocols accept domain objects. Manual
   adapters remain the default in V2; hardware adapters can implement those contracts later.
6. PySide6 renders the service state and sends commands. It does not calculate
   schedules, scores, or qualifiers.

## Implementation sequence

- Establish package, models, pure scheduler/scoring/finals, persistence, service,
  and manual integration adapters.
- Test lane and participation invariants, scoring, qualification, editing,
  restart recovery, and invalid transitions.
- Add event selection/setup, registration, schedule, race control, standings,
  finals, and explicit confirmation for destructive schedule replacement.
- Run full tests and offscreen Qt workflow smoke tests; document installation,
  data location, policies, extension seams, and limitations.

## Durable boundaries and policies

- Each event is independent within a local database. SQLite foreign keys and
  transactions protect assignments and results. Schema versions reject databases
  created by newer application versions rather than guessing at their format.
- Car numbers and nonblank RFID identifiers are unique per event (case-insensitive).
  IDs are internal UUIDs. Names need not be unique.
- Registration and setup remain accessible. Metadata edits preserve schedules.
  Adding/removing a racer or changing lanes/runs requires typed invalidation
  confirmation; full reset clears race and administrative results. Finalist or
  scoring changes invalidate only downstream qualification/finals as applicable.
  Event late-entry defaults never rewrite already-applied group treatments.
- Preliminary corrections after tiebreak/finals exist are rejected until those
  stages are reset. Earlier qualification tiebreak corrections are blocked if later rounds
  exist. This prevents qualification from silently becoming inconsistent.
- Points use normalized placement: last = 0, first = 100, intermediate positions
  equally spaced. Solo runs are byes worth 100 points, without a competitive win. Standings use average points, then wins, then average finish.
  Exact ties share rank. Cutoff ties require recorded balanced tiebreak rounds;
  the operator cannot select qualifiers. No car number or UUID breaks sporting ties.
- Results require exactly one unique position from 1 through the occupied-lane
  count. Times may be positive finite seconds or null. Tied heat finishes/DNF are not V2 rules.
- Finals start with zero points and give each finalist one run per track lane.
  Preliminary, per-round tiebreak, and final standings remain separate.
  Final placement ties automatically use the same recorded tiebreak stage and
  injectable schedule policy as qualification. Each completed round refines one
  tied placement group; repeated rounds start fresh and cannot move a racer
  outside that group. Final ranks remain pending until every group is resolved.
  Corrections to finals or placement rounds invalidate later placement rounds
  with explicit confirmation, then recalculate and schedule any unresolved group.
  Result writes, invalidation, round creation, cursor, and status commit atomically.
  Resetting finals also removes placement rounds, preserving qualification rounds.
- GUI callbacks run on the Qt thread. Future hardware adapters should marshal
  asynchronous observations onto that thread (or a command queue), then call the
  same service methods. Hardware must never write directly to SQLite or widgets.

Scheduler guarantees, scoring rules, operational limitations, and hardware
integration examples are documented in README.md. The GUI invokes synchronous
service commands; hardware adapters must not perform blocking I/O in refresh calls.

## V2 implementation plan (operator feedback)

1. Establish the existing test baseline (219 passing tests).
2. Add immutable change-impact, late-entry policy/preview, administrative result,
   and tiebreak-round records; migrate V1 data transactionally to schema V2.
3. Extend service commands with selective, explicit invalidation; implement
   lane-balanced tiebreak rounds and late-entry preview/commit operations.
4. Extend the pure scheduler to consider historical runs, lanes, opponents, and
   future run requirements. Completed heats remain untouched.
5. Test core invariants, migration, stale previews, rollback, and restart before
   wiring setup edits, tiebreak operation, and bulk late entry into Qt.
6. Run GUI workflows, full regression tests, launch smoke test, and integrity review.

Chosen policies: a tiebreak round gives every tied racer one run per lane;
subsequent rounds involve only the unresolved cutoff group. Missed runs are the
floor of the mean completed actual preliminary runs among on-time entrants.
No-penalty entries receive all target runs; other policies replace missed runs
with explicit administrative scores normalized to the configured lane count.


## V2 integrity boundaries

- `ConfirmationRequired(ChangeImpact)` is raised before mutation for ordinary
  edits that invalidate data. The GUI confirms the concrete counts and explicitly
  resubmits. Validation precedes invalidation; a duplicate identifier must never
  erase a schedule even if a caller already supplied confirmation.
- `BalancedLaneTiebreak` implements the injectable `TiebreakPolicy`: every tied
  racer runs once per lane, each round is scored independently, automatic
  qualifiers accumulate, and only the unresolved cutoff group repeats. Round
  numbers and all actual results persist through restarts.
- `LateEntryPreview` separates review from commitment. It contains explicit
  applied policy, named entrants, actual/administrative entitlements, a complete
  future schedule and fairness report, plus a snapshot fingerprint. Commit takes
  an immediate SQLite write lock, checks the fingerprint, recomputes/validates the
  plan, and applies it atomically. A stale or altered preview cannot be used.
- Late scheduling deletes only pending preliminary heats with no result rows.
  It never updates historical heat rows, assignment rows, or actual results.
  Completed heat records are compared before commit; inequality rolls back the
  entire group. Matching future heat signatures retain their IDs and numbers.
- Administrative scores live separately from physical results, have an explicit
  kind and a persisted applied-policy batch, and count toward scoring entitlement
  without increasing actual-race counts, wins, timing samples, or lane exposure.
  Normalized scoring uses exact rational values including placement equivalents.
- Schema V2 migrates V1 atomically. Expanded CHECK constraints require copying
  event/heat tables with their existing IDs and fields; referential integrity is
  checked before commit. Failure leaves the original V1 schema/data intact.
- Schema V3 transactionally adds `heats.tiebreak_scope` (`qualification` or
  `finals`). It reuses heat assignments, results, globally unique round numbers,
  and scoring; there is no separate finals-tiebreak storage or scheduling engine.
  Existing rounds default to qualification. On service startup, older completed
  finals with ties receive a pending placement round without changing old results;
  already-pending rounds and the saved cursor are preserved.
- Qt controls and widgets remain presentation-only. Literal minus/plus controls
  replace ambiguous spin arrows. The late-entry page collects drafts and shows
  a service-produced preview; it does not invent penalties or schedules.
- The existing timing and lane-identification protocols continue to operate on
  actual Heat/LaneResult records for all physical racing stages. No hardware
  integration code treats an administrative score as a race or lane reading.

The README is the authoritative operator-facing description of exact V2 policies,
including progress rounding, per-group overrides, repeated tiebreak rounds,
late-admission boundaries, reset semantics, and the limits of partial scheduling.
