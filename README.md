# Pinewood Derby

An offline desktop race-management application built with Python 3, PySide6/Qt,
and SQLite. Create events, register cars, generate balanced heats, enter manual
finishing positions, view live standings, run recorded qualification tiebreaks,
admit late racers, and run a separate finals stage. Saved results and the
selected heat survive application restarts.

## Install and run

### Windows users

Download `PinewoodDerby-Setup.exe` from the latest GitHub release, then
double-click it and follow the installer. Administrator access is not required.
The installer creates a Start Menu shortcut and offers an optional desktop
shortcut. Leave **Launch Pinewood Derby Race Manager** checked to start it when
installation finishes. Python and an internet connection are not needed to run
the installed app.

The Windows app stores its database at
`%LOCALAPPDATA%\pinewood-derby\events.sqlite3`. Updates and uninstalling the app
do not remove this database. For a backup, close the app and copy the database
file to a safe location. To restore it, close the app and copy the saved file
back to that location.

### Running from source

Python **3.11 or later** is required. Install dependencies once with an internet
connection; normal operation is entirely offline.

Linux, from this repository:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pinewood_derby
```

Windows PowerShell, from this repository:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pinewood_derby
```

The installed `pinewood-derby` entry point also starts the program. A real desktop
session and Qt's platform libraries are needed for interactive use. On Linux,
the Qt `xcb` backend may require your distribution's `libxcb-cursor` package;
Wayland can be selected with `QT_QPA_PLATFORM=wayland` where supported.

Choose a different database, for example an experimental event file:

```bash
.venv/bin/python -m pinewood_derby --database ./practice.sqlite3
```

Dependencies are limited to PySide6 and its bundled Qt dependencies. Pytest is
an optional development dependency. No server, account, browser, or network
service is used.

## Operator workflow

1. **Setup:** name the event, choose 2–6 lanes, preliminary runs per racer,
   finalist count, and scoring method. Save the event.
2. **Racers:** add racers with a name and unique car number. Car name and RFID/tag
   are optional. Select a row to edit or remove it. Finalist count must not exceed
   registration before generating heats.
3. **Schedule:** generate and review preliminary heats. Double-click a heat to
   open it. Setup and registration remain editable; invalidating changes require confirmation.
4. **Race Control:** put the displayed cars in their assigned lanes. Enter a
   unique finishing position for each occupied lane. **Save result / Next heat**
   commits results and advances the selection in one transaction. Empty lanes
   need no result. **View next heat** and **Previous heat** navigate without saving.
5. **Corrections:** return to a saved heat, change positions, and save again.
   Standings recalculate immediately. Unsaved result entries block heat/event
   changes until saved or discarded. Closing with unsaved entries asks for
   confirmation. Only explicitly saved entries survive a crash.
6. **Standings:** inspect preliminary and final standings separately. Live
   standings are provisional while racers have different numbers of scored runs.
   Actual races and administrative late-entry scores have separate counts.
7. **Finals / Results:** after every preliminary heat is saved, advance the top
   configured number of racers. A tie at the cutoff requires recorded tiebreak
   racing (see the V2 rules below); there is no manual qualifier selection.
   Generate finals and run them in Race Control. Each finalist runs once in each lane.
   Any tied final placements automatically create lane-balanced tiebreak rounds.
   Final places appear only after every placement tie is resolved.

The event selector reopens saved events. The most recently created event opens
at startup at its saved heat. Select another saved event to resume that event.

**Reset schedule** deletes both stages and all results, keeps registered racers,
and clears late-entry administrative scores and late-entry audit batches. All
remaining registered racers then start fresh as on-time entrants. **Reset finals**
removes finals and their placement tiebreaks, preserving qualification tiebreaks.
**Reset tiebreaks and finals** preserves preliminaries and
removes both downstream stages. Corrections to preliminary results are locked
while either downstream stage exists. Earlier qualification tiebreak rounds cannot
be corrected while later qualification rounds or finals exist; reset their dependent
stages explicitly first. Finals and final-placement tiebreak results remain editable.
A changed result requires confirmation before discarding later placement rounds;
required placement rounds are then regenerated from the corrected results.
Resets, destructive edits, racer
removal, and event deletion require confirmation. These operations cannot be undone.

## Scheduling and scoring rules

The scheduler is pure Python and deterministic for the same internal racer IDs,
lane count, and requested run count, independent of input ordering. Its API also
accepts an explicit seed for reproducible alternatives; the GUI uses the default.

A bipartite multigraph connects each racer to the lanes they must run in. Matchings that
cover every maximum-degree vertex become heats, and alternating-path exchanges
balance heat sizes. The original full-event scheduler guarantees:

- Exactly the requested number of runs for every racer; nobody disappears.
- Each racer's usage of any two lanes differs by at most one.
- Total usage of any two lanes differs by at most one.
- No racer or lane occurs twice within one heat.
- Heat sizes differ by at most one, with no empty heats.
- The minimum possible heat count: `max(runs, ceil(racers * runs / lanes))`.

Six reproducible candidates are compared to reduce repeated opponents. Opponent
variety is a heuristic, not an optimal round-robin guarantee. Heats may have empty
lanes. A solo run can occur in very small events or when the total run count
cannot otherwise fit (for example, 7 racers × 1 run on a 2-lane track).

**Placement points** award `100 × (field size − position) / (field size − 1)`.
First place earns 100, last earns 0, with equal intervals between them. This avoids
an automatic scoring advantage for winning a larger heat. A solo run is a bye
worth 100 points, but **not** a competitive win. Byes can still affect standings;
choose a run count that avoids solo heats for a competitive event (for example,
an even run count for an odd-sized field on a 2-lane track).

Standings sort by average points (descending), competitive wins (descending),
then average finish (ascending). Exact ties share a competition rank (1, 1, 3).
Calculations use rational arithmetic so floating-point rounding does not invent
a tiebreak. Displayed points are rounded to two decimal places. Racer names order
rows within a tie for readability and never determine qualification. Qualification
ties are resolved by recorded races, not manual selection. Finals start from zero,
using the same scoring policy. Final podium ties remain shared places.

Administrative late-entry scores contribute points, placement equivalents, and a
scored-run denominator, but never an actual race, win, lane exposure, or time.
The **Races** and **Admin** columns make that distinction visible. Custom points
have an interpolated placement equivalent for the average-finish tiebreak.
A racer can therefore have zero actual races and nonzero scored runs.

Results must contain the positions 1 through the occupied lane count exactly
once. Tied heat finishes, DNF, disqualifications, and result-entry drafts are not
supported in V2. Resolve a tied heat by your event's manual rerun procedure before
saving. Optional elapsed times are stored for future timing integrations but
are not used by the placement scoring method.

## Database and durability

Default locations:

- Linux: `$XDG_DATA_HOME/pinewood-derby/events.sqlite3`, or
  `~/.local/share/pinewood-derby/events.sqlite3` if unset.
- Windows: `%LOCALAPPDATA%\pinewood-derby\events.sqlite3`.

`--database PATH` overrides the location. A single database holds multiple events.
SQLite transactions, foreign keys, WAL journaling, and `synchronous=FULL` protect
committed operations. Replacing a result and advancing the current heat are
atomic. Event lifecycle status, stage, lane assignments, results, nullable times,
and selected heat are stored. All race data remains on this computer.

Close the application before copying the database for a simple manual backup;
while running, SQLite may also use `-wal` and `-shm` sidecars. Automated backup and
restore controls are not part of V2.

Schema version **3** is recorded with `PRAGMA user_version`. Existing V1 and V2
databases are migrated automatically and transactionally without losing events,
racers, heat IDs/numbers, assignments, results, times, or existing finals.
The V1-to-V2 migration adds tiebreak stages/round numbers, late-entry defaults, racer late
flags and batch references, `late_batches`, and `administrative_results`. The
event and heat tables are rebuilt to extend their CHECK constraints; foreign keys
are checked before commit and enabled again afterward. Failed migrations roll
back to V1. The V2-to-V3 migration adds the tiebreak purpose; existing rounds are
qualification rounds. Opening older tied finals creates placement tiebreaks and
selects their first heat. Otherwise, the selected heat is preserved. Newer,
unsupported schema versions are rejected; older application versions cannot open
a migrated V3 database. Back up an important event file before upgrading.

## Windows builds and distribution

The reproducible Windows build runs in
[`.github/workflows/windows-build.yml`](.github/workflows/windows-build.yml) on
pushes to branches, pull requests, and manual workflow dispatch. It runs the
tests on a Windows runner, builds and smoke-tests the standalone
PyInstaller application, and compiles the Inno Setup installer. The generated
installer is uploaded as the `PinewoodDerby-Setup` Actions artifact. For a
versioned release, download the installer directly from the GitHub release.

To publish a release, update the version in `pyproject.toml`, commit the change,
and push a matching version tag such as `v0.2.0`. The workflow checks that the
tag matches the project version before attaching `PinewoodDerby-Setup.exe` to
the release. To build locally on Windows, install Python 3.11 and Inno Setup,
then run these commands from the repository in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,build]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/windows/PinewoodDerby.spec
$version = .\.venv\Scripts\python.exe -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DAppVersion=$version" packaging/windows/installer.iss
```

The installer is written to `dist/installer/PinewoodDerby-Setup.exe`. It installs
per-user under `%LOCALAPPDATA%\Programs` and stores race data separately under
`%LOCALAPPDATA%\pinewood-derby`, so replacing or uninstalling the program does
not remove the user's database. The packaged executable is tested by CI on
Windows; Linux does not produce a Windows installer.

## Architecture

The original plan and important boundaries are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Module | Responsibility |
| --- | --- |
| `models.py` | Immutable event, racer, assignment, heat, result, and standing records |
| `scheduling.py` | Pure balanced heat generation |
| `scoring.py` | Scoring protocol and placement-points policy |
| `standings.py` | Ranking and aggregate statistics, independent of persistence |
| `finals.py`, `tiebreaks.py` | Qualification boundaries and injectable lane-balanced tiebreak policy |
| `late_entry.py` | Late-entry policy equivalents and actual-progress calculation |
| `remaining.py` | Future scheduling around immutable race history; fairness report |
| `database.py` | SQLite schema migration and record loading |
| `service.py` | Validated event commands, transactions, state transitions |
| `hardware.py` | Result input / lane identification protocols and manual adapters |
| `gui.py`, `widgets.py` | Qt workflows, literal −/+ controls, and policy editors |
| `__main__.py` | Application bootstrap, database selection, smoke-test option |

`RaceService` accepts an injected database, scoring-method registry, and lane
identifier. Every result source passes through the same validation and save path.
Core modules do not import Qt. The GUI does not calculate scoring or schedules.
Setup/registration commands raise a typed `ConfirmationRequired` with a
`ChangeImpact` before destructive changes. The GUI describes that impact and
resubmits only after confirmation. Rejected edits do not mutate the database.

A late-entry preview is an immutable plan containing a snapshot hash of the event,
explicit applied policy, per-racer consequences, future assignments, and fairness
metrics. Commit obtains a SQLite write lock, rejects stale/tampered previews,
revalidates the plan, inserts entrants and administrative scores, retains matching
future heats, and replaces only unmatched unrun heats in one transaction. It
verifies that every completed heat is identical before committing. Device results
continue through the existing `ResultSource` interface; administrative records
never impersonate timer measurements or lane assignments.
`packaging/windows` contains the PyInstaller launcher and Inno Setup installer
definition used by the Windows build workflow.

### Future electronic timing

Implement `hardware.ResultSource.obtain_results(heat)` to return `LaneResult`
records with lane, finishing position, and optional positive finite elapsed
seconds. A timing adapter can rank reported times to create placements; define a
timing-tie policy before implementing that conversion. Pass the adapter to
`RaceService.record_results(heat_id, source, advance=True)`. The existing validator,
transaction, resume behavior, and standings flow are reused unchanged.

For time-based rankings, implement the `ScoringMethod` protocol, register a stable
method ID when constructing the service, and define how missing times and ties
are handled. A different ranking strategy may also require extending the standings
policy; placement-specific tiebreaks are not assumed valid for all future
formats. Time columns are already present in SQLite.

Hardware I/O should run outside the Qt UI thread. Deliver completed observations
through queued Qt signals or an application command queue, then call the service
on its owning thread. Do not share the current SQLite connection across threads,
write device results directly to the database, or let drivers manipulate widgets.

### Future RFID and lane detection

Implement `LaneIdentifier.identify(heat)` to return a mapping from lane number to
internal racer ID (or `None` for an unknown tag/car). Resolve RFID strings against
the event's registered racers in the adapter, then inject it into `RaceService`.
`lane_mismatches` compares observations with the planned assignments and identifies
unexpected, missing, and swapped cars. V2 continues to inject `ScheduledLaneIdentifier`, so the
operator verifies physical car placement manually.

A future asynchronous adapter should cache observations and notify race control
when they change, without doing blocking device I/O during a screen refresh.
No serial, USB, network timing, or RFID driver is implemented in this version.

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pinewood_derby --smoke-test --database /tmp/pinewood-smoke.sqlite3
```

Windows: use `.\.venv\Scripts\python.exe` and a Windows path for the smoke database.
The smoke option selects Qt's offscreen backend and exits automatically. GUI tests
also use offscreen Qt, exercising actual widgets without an external display.

Tests cover all 2–6 lane counts, small and odd fields, partial heats, exact
participation and lane balance, reproducibility, opponent variety, normalized
scoring, true ties, finals qualification, duplicate identifiers, validation,
SQLite reload, optional timing values, correction of saved results, transaction
rollback, event isolation, and the GUI workflow through final results.

## V2 editable setup and registration

You can always return to Setup and Racers. Harmless changes take effect immediately
after **Save** without replacing schedules or results.

| Change | What is invalidated |
| --- | --- |
| Event name; racer/car name, car number, RFID/tag | Nothing; uniqueness validation still applies |
| Default late-entry policy | Nothing; already-applied group treatments remain unchanged |
| Finalist count | Tiebreaks and finals only; preliminary results remain |
| Scoring method | Tiebreaks/finals only; preliminary race results remain and standings recalculate |
| Track lane count or preliminary runs | All schedules, race results, late batches, and administrative scores |
| Ordinary Add Racer or Remove Racer | All schedules, race results, late batches, and administrative scores |
| Add Late Racers | Only unrun preliminary heats that need replacement; completed history is preserved |

Confirmation states the number of affected heats, completed heats, individual
lane results, and administrative scores. Cancelling leaves the event unchanged.
After full invalidation, click **Generate preliminary heats** again. Scoring
changes with existing administrative scores require an explicit full reset first;
penalties cannot silently change scoring meaning. Only placement scoring ships.

## Exact V2 tiebreak procedure

1. Finish all preliminary heats, including any late-entry remaining heats.
2. Rank preliminary standings with the existing sporting rules. Racers strictly
   above the cutoff qualify automatically. If the entire cutoff group fits in
   the available places, all of them qualify and there is no tiebreak.
3. Otherwise, **Generate tiebreak round** schedules only the tied cutoff group.
   Every racer runs **once in each configured track lane**. No administrative
   penalties carry into tiebreak standings. For two racers on a two-lane track,
   this is two head-to-head races with swapped lanes. On larger tracks they each
   use every lane. Groups larger than the track are scheduled over multiple heats.
4. Save actual race results in Race Control. The stage is prominently labelled
   **TIEBREAK**, with its round number. Results, selection, and round progress are
   durable and can be resumed after restart.
5. Rank that completed round independently using placement points, wins, and
   average finish. Racers above its cutoff qualify; racers below it are eliminated.
   If a smaller cutoff group is still tied, generate another full balanced round
   for just that unresolved group and its remaining places. Earlier results are
   retained, but new-round scores start fresh.
6. Generate finals only after all required places are settled by recorded results.

This deliberately uses complete lane cycles instead of an arbitrary-lane sudden
single decider. It can require repeated rounds when cars remain indistinguishable;
there is no coin toss, name/number tiebreak, manual qualifier override, or maximum
round limit. `TiebreakPolicy` is shared by qualification and final placement rounds
and is injectable for later formats.

### Final placement ties

After the last finals heat is saved, every group sharing a sporting score must be
resolved, including ties for lower places. Race Control automatically opens a
recorded **Final placement TIEBREAK** round for the highest unresolved group.
Each racer runs once in every lane using the same schedule policy as qualification.
Only a fully completed round determines placement; a single heat cannot decide it.

The round's independent scores split that group into resolved racers and any
smaller tied groups. Further balanced rounds are generated automatically until
every finishing position is unique, then Final Results and Finals Standings show
the resolved order. Until then, displayed final ranks say **Pending**. Tiebreak
points do not alter the original finals scores or move racers outside their tied
placement group. All rounds, results, and the selected heat survive restart.

Correct a finals or placement-tiebreak heat through Race Control. If later placement
rounds exist, confirmation shows the heats and results that will be discarded;
cancelling preserves them and the original result. Confirming saves the correction
and recalculates placements, creating another round if needed. Re-saving an
unchanged result preserves later rounds. Qualification heats remain unchanged.

Schema version 3 adds the tiebreak purpose to existing heat records. Existing
qualification rounds retain their results. Reopening an older event that ended
with tied finals automatically creates the required placement round and returns
the event to pending tiebreak status without altering its finals results.

## Exact V2 late-entry procedure

Late entry is available after at least one preliminary heat is saved and before
any tiebreak/finals heats exist. It is also available just after preliminaries
finish, before qualification racing begins. Before racing starts, use ordinary
registration and confirm schedule regeneration if needed.

1. Return to **Racers** and choose **Add Late Racers…**. The button is available
   once a schedule exists; review becomes available after the first preliminary
   heat is saved. Use **Back to Racers** to leave the entry page without committing.
2. Enter one or multiple new racers (name, car name, unique number, optional tag).
   Use **+ Add racer row** / **− Remove selected row** for a larger group.
3. Use the event default or select a treatment for this group. A group override
   does **not** change the event-wide default.
4. Click **Review late-entry consequences**. The application validates the whole
   group and shows target runs, missed runs, actual runs remaining, administrative
   scores, placement/points equivalents, total administrative points per racer,
   future heat counts, retained heats, lane spreads, repeats, and solo heats.
5. Confirm the reviewed consequences. The complete group, applied treatment, and
   replacement future schedule are saved atomically. Cancelling changes nothing.
   Changing group inputs invalidates the preview. If racing or event data changed
   since the preview, review it again; a stale preview cannot be committed.

### Missed-run calculation

Let `R` be target preliminary runs, `N` the number of **on-time entrants**, and
`A` their total actual appearances in completed preliminary heats. Then:

```text
missed = min(R, floor(A / N))
```

The selected heat number and heat numbering are irrelevant. This works when
results are recorded out of order and racers have unequal completed counts. The
on-time cohort remains the reference for later groups, so adding a large group
cannot dilute progress and penalties never pretend to be actual racing.
This is a documented event-progress convention, not a claim that every original
racer has completed exactly that many runs.

### Policies and exact scoring

Penalty equivalents use a reference field of **the configured lane count `L`**,
even if an actual heat has empty lanes. That reference is saved in the batch.

| Policy | Treatment |
| --- | --- |
| No penalty | Zero administrative scores; racer receives all `R` actual runs, making up missed runs |
| Last-place equivalent | `missed` scores of 0 points, placement equivalent `L` |
| One-above-last | `missed` scores of `100/(L−1)` points, placement equivalent `L−1` |
| Specific placement `p` | Whole-number `1 ≤ p ≤ L`; each missed run scores `100×(L−p)/(L−1)` |
| Custom points `q` | `0 ≤ q ≤ 100`; each missed run scores `q`, with placement equivalent `L−q×(L−1)/100` |

For every penalized policy, actual remaining runs are `R − missed`. On a two-lane
track, one-above-last means first-place-equivalent points, but never a win.
Changing the default later does not rewrite past treatments. Points are stored as
exact rational strings, not rounded display values.

A late batch records its applied policy/value, target/missed/actual runs, reference
lane count, and creation time. Racer records reference that batch and have a late
flag. Each missed penalized run is a separate `administrative_results` row of kind
`late_entry_penalty`, linked to its batch and racer. These records have **no heat,
physical lane, elapsed time, or competitive win**. The kind field allows future
administrative result types without overloading physical `LaneResult` records.
A penalized entrant admitted after all original runs can have zero actual runs;
this is stated in the preview. Choose no penalty if they should still race.

### Remaining-schedule algorithm and limits

The pure `rebalance_remaining` function takes required future runs per racer,
completed preliminary heats, lane count, and the old pending schedule. Existing
racers need `R − actual completed runs − administrative scored runs`; new entrants
need the reviewed actual run count.

Four deterministic candidates allocate each remaining run to a least-used lane
for that racer, with overall lane usage as the next preference. Historical lane
usage is included. The shared edge-coloring routine creates valid heats from those
lane demands and uses historical opponent encounters when choosing matchings.
Alternating-path exchanges balance future heat sizes. Candidates are compared by
personal lane imbalance, overall lane imbalance, solo heats, repeat encounters,
heat count, and retention of unchanged future heats.

Matching old pending heats retain their IDs, numbers, and assignments. Unmatched
unrun heats are deleted/replaced; completed heat IDs, numbers, lanes, racers,
positions, and times stay exactly the same. New heats are numbered after retained
history, so numbers can contain gaps. Review the updated schedule; pending heats
may move substantially when exact participation and lane fairness require it.

Future participation is exact. Lane balance and opponent variety are best-effort
with fixed history; V1's full-schedule optimality guarantees do **not** automatically
apply after late entry. The preview reports projected maximum per-racer lane-count
spread, overall lane-count spread, repeat meetings, and unavoidable/remaining solo
heats honestly. A no-penalty arrival after everyone else has finished can require
solo catch-up heats. Solo races keep the documented 100-point/no-win bye rule.
Scheduling runs synchronously; very large fields may take several seconds.

## Manual V2 acceptance check

Use a separate practice database:

```bash
.venv/bin/python -m pinewood_derby --database ./v2-practice.sqlite3
```

- Create 4 racers, 2 lanes, 2 preliminary runs, 2 finalists. Generate heats.
  Rename the event and edit a car name: the schedule should stay unchanged.
  Change the lane count: cancel the warning and verify the schedule remains;
  then accept it and regenerate.
- Save two preliminary heats. Try changing runs or ordinary registration and
  check the warning's saved-result counts; cancel. Use **Add Late Racers** for
  two children, review the default penalty, override to **No penalty**, and
  confirm. Check that saved heats/results remain identical and late racers get
  their complete run entitlement. Repeat in another event with custom points.
- Create 2 racers, 2 lanes, 2 preliminary runs, 1 finalist. Make lane 1 win both
  preliminaries: each car wins once after swapping lanes. Generate a tiebreak.
  Make the same **car** win both swapped-lane tiebreak races. Verify only that car
  qualifies. Alternatively split the wins again to test another balanced round.
- Close/reopen after a tiebreak heat or late group; check resume position, stage,
  racers, administrative counts, and completed results.

Current limits: one local operator, synchronous scheduling, no saved registration
or result drafts, no DNF/tied heat finishes, and no manual qualification override.
Timing/RFID drivers, installers, and unrelated visual redesigns remain out of scope.
