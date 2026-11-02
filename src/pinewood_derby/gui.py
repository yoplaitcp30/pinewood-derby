"""Qt presentation. Race decisions and persistence live in RaceService."""
from collections.abc import Callable
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .hardware import ManualResultSource
from .models import ConfirmationRequired, LaneResult, LatePolicy, RacerDraft, RaceError
from .widgets import NumberControl as QSpinBox, PolicyEditor
from .late_entry import POLICIES
from fractions import Fraction
from .service import RaceService

STAGE_NAMES = {'preliminary': 'Preliminary', 'tiebreak': 'TIEBREAK', 'finals': 'Finals'}


def stage_name(heat) -> str:
    if heat.stage == 'tiebreak' and heat.tiebreak_scope == 'finals':
        return 'Final placement TIEBREAK'
    return STAGE_NAMES[heat.stage]

STYLE = """
QMainWindow, QWidget { background: #f4f6fa; color: #17263c; }
QLabel#title { font-size: 26px; font-weight: 700; }
QLabel#heading { font-size: 21px; font-weight: 650; }
QLabel#notice { padding: 10px; border-radius: 5px; }
QPushButton { background: #e1e7f0; border: 1px solid #bdc9d8; border-radius: 5px;
              padding: 8px 15px; min-height: 22px; font-weight: 600; }
QPushButton:hover { background: #d4dfed; }
QPushButton:disabled { color: #788391; background: #e9ecf0; }
QPushButton#primary { background: #1559a4; color: white; border: 1px solid #1559a4; }
QPushButton#primary:hover { background: #114981; }
QPushButton#primary:disabled { background: #a4b6cc; border-color: #a4b6cc; }
QLineEdit, QSpinBox, QComboBox { background: white; border: 1px solid #adbacb;
                              border-radius: 4px; padding: 7px; min-height: 22px; }
QTabWidget::pane { border: 1px solid #c6d0df; background: white; }
QTabBar::tab { padding: 12px 17px; background: #e4eaf2; }
QTabBar::tab:selected { background: white; color: #1559a4; font-weight: bold; }
QTableWidget, QListWidget { background: white; alternate-background-color: #eef3f9;
                            border: 1px solid #c6d0df; gridline-color: #e2e8f0; }
QTableWidget::item:selected, QListWidget::item:selected { background: #cfdef2; color: #142a46; }
QHeaderView::section { background: #e7edf5; padding: 9px; border: none; font-weight: bold; }
QProgressBar { border: 1px solid #c6d0df; border-radius: 4px; text-align: center; min-height: 24px; }
QProgressBar::chunk { background: #a5d2b9; }
"""


def label(text: str, heading: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if heading:
        widget.setObjectName('heading')
    return widget


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().setVisible(False)
    widget.verticalHeader().setDefaultSectionSize(42)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    for index, title in enumerate(headers):
        if title not in ('Racer', 'Car', 'Car name', 'RFID / tag'):
            widget.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
    return widget


def fill_table(widget: QTableWidget, rows: list[list[str]]) -> None:
    widget.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            widget.setItem(i, j, QTableWidgetItem(str(value)))


class MainWindow(QMainWindow):
    def __init__(self, service: RaceService, database_path: str | Path):
        super().__init__()
        self.service = service
        self.event_id: str | None = None
        self.racer_id: str | None = None
        self.result_dirty = False
        self.position_inputs: dict[int, QSpinBox] = {}
        self.loaded_heat_id: str | None = None
        self.late_preview = None
        self.setWindowTitle('Pinewood Derby • Race Control')
        self.resize(1240, 880)
        self.setMinimumSize(960, 700)
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 15)
        layout.setSpacing(12)
        title = label('PINEWOOD DERBY')
        title.setObjectName('title')
        layout.addWidget(title)
        top = QHBoxLayout()
        top.addWidget(label('Event'))
        self.events = QComboBox()
        self.events.setMinimumWidth(300)
        self.events.currentIndexChanged.connect(lambda _: self._run(self._choose_event))
        top.addWidget(self.events, 1)
        top.addWidget(self._button('New event', self._new_event))
        self.delete_button = self._button('Delete event…', self._delete_event)
        top.addWidget(self.delete_button)
        layout.addLayout(top)
        self.summary = label('Create an event to begin.')
        layout.addWidget(self.summary)
        self.notice = label('')
        self.notice.setObjectName('notice')
        self.notice.hide()
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_setup()
        self._build_registration()
        self._build_schedule()
        self._build_race()
        self._build_standings()
        self._build_finals()
        self._build_late_entry()
        self.statusBar().showMessage(f'Offline • Changes saved locally • Database: {database_path}')
        self._reload_events()
        if self.events.count():
            self.event_id = self.events.currentData()
        self._refresh()
        if self.event_id and self.service.db.heats(self.event_id):
            self.tabs.setCurrentIndex(3)

    def _button(self, text: str, action: Callable, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName('primary')
        button.clicked.connect(lambda _: self._run(action))
        return button

    def _page(self, title: str) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        self.tabs.addTab(page, title)
        return layout

    def _run(self, action: Callable) -> None:
        self.notice.hide()
        try:
            action()
        except (RaceError, sqlite3.Error, OSError) as exc:
            self._message(str(exc), error=True)

    def _message(self, text: str, error: bool = False) -> None:
        self.notice.setText(text)
        self.notice.setStyleSheet('background: #ffe5e2; color: #8a241c;' if error
                                 else 'background: #dceee4; color: #20573b;')
        self.notice.show()

    def _confirm(self, title: str, text: str) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(text)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def _with_invalidation_confirmation(self, operation: Callable):
        try:
            return operation(False)
        except ConfirmationRequired as exc:
            message = exc.impact.describe()
            if exc.impact.completed_heats and exc.impact.scope == 'all':
                message += '\n\nFor new arrivals, Add Late Racers preserves completed races.'
            if self._confirm('Confirm data invalidation', message):
                return operation(True)
            self._message('Change cancelled. Existing race data was preserved.')
            return None

    def _require_saved_result(self) -> None:
        if self.result_dirty:
            raise RaceError('This heat has unsaved entries. Save the result or choose Discard entry in Race Control before leaving it.')

    def _reload_events(self) -> None:
        self.events.blockSignals(True)
        self.events.clear()
        for event in self.service.db.events():
            self.events.addItem(event.name, event.id)
        if self.event_id:
            self.events.setCurrentIndex(self.events.findData(self.event_id))
        self.events.blockSignals(False)

    def _choose_event(self) -> None:
        requested = self.events.currentData()
        if requested == self.event_id:
            return
        if self.result_dirty:
            self.events.blockSignals(True)
            self.events.setCurrentIndex(self.events.findData(self.event_id))
            self.events.blockSignals(False)
            self._require_saved_result()
        self.event_id = requested
        self._clear_racer()
        self._refresh()
        self.tabs.setCurrentIndex(3 if self.event_id and self.service.db.heats(self.event_id) else 0)

    def _new_event(self) -> None:
        self._require_saved_result()
        self.event_id = None
        self.events.blockSignals(True)
        self.events.setCurrentIndex(-1)
        self.events.blockSignals(False)
        self._clear_racer()
        self._refresh()
        self.tabs.setCurrentIndex(0)
        self.event_name.setFocus()

    def _delete_event(self) -> None:
        self._require_saved_result()
        if self.event_id and self._confirm('Delete event?',
                f'Delete “{self.service.db.event(self.event_id).name}” and all its racers, heats, and results? This cannot be undone.'):
            self.service.delete_event(self.event_id)
            self.event_id = None
            self._reload_events()
            self.event_id = self.events.currentData()
            self._clear_racer()
            self._refresh()
            self._message('Event deleted.')

    def _build_setup(self) -> None:
        layout = self._page('1  Setup')
        layout.addWidget(label('Set up your event', True))
        layout.addWidget(label('Choose the track configuration, register racers, then generate the preliminary schedule.'))
        self.setup_form = QWidget()
        form = QFormLayout(self.setup_form)
        form.setSpacing(16)
        self.event_name = QLineEdit()
        self.event_name.setPlaceholderText('Pack 42 • Pinewood Derby')
        self.lanes = QSpinBox()
        self.lanes.setRange(2, 6)
        self.runs = QSpinBox()
        self.runs.setRange(1, 100)
        self.finalists = QSpinBox()
        self.finalists.setRange(1, 10000)
        self.scoring = QComboBox()
        for key, method in self.service.scoring_methods.items():
            self.scoring.addItem(method.name, key)
        form.addRow('Event name', self.event_name)
        form.addRow('Track lanes', self.lanes)
        form.addRow('Preliminary runs per racer', self.runs)
        form.addRow('Racers advancing to finals', self.finalists)
        form.addRow('Scoring method', self.scoring)
        self.late_default = PolicyEditor()
        form.addRow('Default late-entry policy', self.late_default)
        for control, name in [(self.lanes, 'track lanes'), (self.runs, 'preliminary runs per racer'),
                              (self.finalists, 'finalist count')]:
            control.setAccessibleName(name)
        layout.addWidget(self.setup_form)
        self.save_setup_button = self._button('Create event', self._save_setup, True)
        layout.addWidget(self.save_setup_button)
        layout.addWidget(label('Points: first place earns 100, last earns 0, and intermediate places are evenly spaced. '
                               'Average points rank racers; wins and average finish break ties. Finals start fresh. '
                               'Setup and registration remain editable. Changes that invalidate race data require explicit confirmation.'))
        layout.addStretch()

    def _save_setup(self) -> None:
        self._require_saved_result()
        event = self._with_invalidation_confirmation(lambda confirmed: self.service.save_event(
            self.event_name.text(), self.lanes.value(), self.runs.value(), self.finalists.value(),
            self.scoring.currentData(), self.event_id, late_policy=self.late_default.policy(), confirmed=confirmed))
        if event is None:
            return
        self.event_id = event.id
        self._reload_events()
        self._refresh()
        self.tabs.setCurrentIndex(1)
        self._message('Event saved. Valid race data was preserved.')

    def _build_registration(self) -> None:
        page_layout = self._page('2  Racers')
        self.registration_pages = QStackedWidget()
        page_layout.addWidget(self.registration_pages)
        registration = QWidget()
        self.registration_pages.addWidget(registration)
        layout = QVBoxLayout(registration)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.registration_heading = label('Racer registration', True)
        layout.addWidget(self.registration_heading)
        self.late_entry_button = self._button('Add Late Racers…', self._open_late_entry, True)
        layout.addWidget(self.late_entry_button)
        self.racer_table = table(['Car #', 'Racer', 'Car name', 'RFID / tag', 'Entry'])
        self.racer_table.itemSelectionChanged.connect(self._select_racer)
        layout.addWidget(self.racer_table, 1)
        self.racer_form = QWidget()
        form = QFormLayout(self.racer_form)
        self.racer_name = QLineEdit()
        self.car_name = QLineEdit()
        self.car_number = QLineEdit()
        self.tag = QLineEdit()
        self.tag.setPlaceholderText('Optional • reserved for future hardware')
        form.addRow('Racer name *', self.racer_name)
        form.addRow('Car name', self.car_name)
        form.addRow('Car number *', self.car_number)
        form.addRow('RFID / tag', self.tag)
        layout.addWidget(self.racer_form)
        actions = QHBoxLayout()
        self.save_racer_button = self._button('Add racer', self._save_racer, True)
        self.clear_racer_button = self._button('Clear / new racer', self._clear_racer)
        self.remove_racer_button = self._button('Remove racer…', self._remove_racer)
        actions.addWidget(self.save_racer_button)
        actions.addWidget(self.clear_racer_button)
        actions.addWidget(self.remove_racer_button)
        layout.addLayout(actions)
        self.generate_button = self._button('Generate preliminary heats', self._generate, True)
        layout.addWidget(self.generate_button)

    def _clear_racer(self) -> None:
        self.racer_id = None
        self.racer_table.clearSelection()
        for field in (self.racer_name, self.car_name, self.car_number, self.tag):
            field.clear()
        self.save_racer_button.setText('Add racer')
        self.remove_racer_button.setEnabled(False)

    def _select_racer(self) -> None:
        row = self.racer_table.currentRow()
        if not self.event_id or row < 0 or not self.racer_table.selectedItems():
            return
        racer = self.service.db.racers(self.event_id)[row]
        self.racer_id = racer.id
        for field, value in zip((self.racer_name, self.car_name, self.car_number, self.tag),
                                (racer.name, racer.car_name, racer.car_number, racer.tag or '')):
            field.setText(value)
        self.save_racer_button.setText('Save racer changes')
        self.remove_racer_button.setEnabled(True)

    def _save_racer(self) -> None:
        if not self.event_id:
            return
        self._require_saved_result()
        saved = self._with_invalidation_confirmation(lambda confirmed: self.service.save_racer(
            self.event_id, self.racer_name.text(), self.car_name.text(), self.car_number.text(),
            self.tag.text(), self.racer_id, confirmed=confirmed))
        if saved is None:
            return
        self._clear_racer()
        self._refresh()
        self.racer_name.setFocus()
        self._message('Racer saved.')

    def _remove_racer(self) -> None:
        self._require_saved_result()
        if not self.racer_id:
            return
        impact = self.service.change_impact(self.event_id, 'all')
        message = 'Remove this racer from the event?'
        if impact.destructive:
            message += '\n\n' + impact.describe()
        if self._confirm('Remove racer?', message):
            self.service.remove_racer(self.event_id, self.racer_id, confirmed=True)
            self._clear_racer()
            self._refresh()
            self._message('Racer removed.')

    def _generate(self) -> None:
        self.service.generate_preliminary(self.event_id)
        self._refresh()
        self.tabs.setCurrentIndex(2)
        self._message('Preliminary heats generated and saved. Review the schedule, then open Race Control.')

    def _build_schedule(self) -> None:
        layout = self._page('3  Schedule')
        self.schedule_heading = label('Review the schedule', True)
        layout.addWidget(self.schedule_heading)
        layout.addWidget(label('Races and any late-entry administrative scores fulfill each racer’s run entitlement. Empty lanes are intentional. '
                               'Double-click a heat to open it in Race Control.'))
        self.schedule_table = table(['Stage', 'Heat', 'Status'])
        self.schedule_table.cellDoubleClicked.connect(lambda row, _: self._run(lambda: self._open_schedule_heat(row)))
        layout.addWidget(self.schedule_table, 1)
        actions = QHBoxLayout()
        self.open_heat_button = self._button('Open selected heat', lambda: self._open_schedule_heat(self.schedule_table.currentRow()), True)
        self.reset_button = self._button('Reset entire schedule…', self._reset_schedule)
        actions.addWidget(self.open_heat_button)
        actions.addStretch()
        actions.addWidget(self.reset_button)
        layout.addLayout(actions)

    def _open_schedule_heat(self, row: int) -> None:
        self._require_saved_result()
        heats = self.service.db.heats(self.event_id)
        if not 0 <= row < len(heats):
            raise RaceError('Select a heat in the schedule first.')
        self.service.set_current_heat(self.event_id, heats[row].id)
        self._refresh_race()
        self.tabs.setCurrentIndex(3)

    def _reset_schedule(self) -> None:
        self._require_saved_result()
        impact = self.service.change_impact(self.event_id, 'all')
        if self._confirm('Reset the entire schedule?', impact.describe()
                + '\n\nRacers and event setup will be kept. Late-entry batches and markings will be cleared; everyone starts fresh.'):
            self.service.reset_schedule(self.event_id)
            self._clear_racer()
            self._refresh()
            self.tabs.setCurrentIndex(1)
            self._message('Schedule reset. Racers were kept; all race results, administrative scores, and late-entry batches were cleared.')

    def _build_race(self) -> None:
        layout = self._page('4  Race Control')
        self.race_heading = label('Generate heats to start racing', True)
        layout.addWidget(self.race_heading)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.heat_picker = QComboBox()
        self.heat_picker.currentIndexChanged.connect(lambda _: self._run(self._pick_heat))
        layout.addWidget(self.heat_picker)
        self.race_help = label('Enter finishing positions for occupied lanes. Saving commits immediately to the local database.')
        layout.addWidget(self.race_help)
        self.race_table = table(['Lane', 'Car #', 'Racer', 'Car', 'Finish position'])
        self.race_table.verticalHeader().setMinimumSectionSize(44)
        self.race_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.race_table, 1)
        self.lane_status = label('Manual lane identification: scheduled assignments shown.')
        layout.addWidget(self.lane_status)
        self.entry_status = label('')
        layout.addWidget(self.entry_status)
        actions = QHBoxLayout()
        self.previous_button = self._button('← Previous heat', lambda: self._navigate(-1))
        self.discard_button = self._button('Discard entry', self._discard_result)
        self.save_result_button = self._button('Save result', lambda: self._save_result(False))
        self.next_save_button = self._button('Save result / Next heat →', lambda: self._save_result(True), True)
        self.next_button = self._button('View next heat →', lambda: self._navigate(1))
        for button in (self.previous_button, self.discard_button, self.save_result_button, self.next_save_button, self.next_button):
            actions.addWidget(button)
        layout.addLayout(actions)

    def _pick_heat(self) -> None:
        requested = self.heat_picker.currentData()
        if requested == self.loaded_heat_id or not requested:
            return
        if self.result_dirty:
            self.heat_picker.blockSignals(True)
            self.heat_picker.setCurrentIndex(self.heat_picker.findData(self.loaded_heat_id))
            self.heat_picker.blockSignals(False)
            self._require_saved_result()
        self.service.set_current_heat(self.event_id, requested)
        self._refresh_race()

    def _navigate(self, delta: int) -> None:
        self._require_saved_result()
        heats = self.service.db.heats(self.event_id)
        index = next(i for i, h in enumerate(heats) if h.id == self.loaded_heat_id)
        target = index + delta
        if 0 <= target < len(heats):
            self.service.set_current_heat(self.event_id, heats[target].id)
            self._refresh_race()

    def _mark_dirty(self) -> None:
        self.result_dirty = True
        self.discard_button.setEnabled(True)
        self.entry_status.setText('Unsaved entry • Save the result to keep these finishing positions.')

    def _discard_result(self) -> None:
        self.result_dirty = False
        self._refresh_race()

    def _save_result(self, advance: bool) -> None:
        heat = self.service.db.heat(self.loaded_heat_id)
        existing = {r.lane: r.elapsed_seconds for r in heat.results}
        results = [LaneResult(lane, widget.value(), existing.get(lane)) for lane, widget in self.position_inputs.items()]
        def save(confirmed):
            self.service.record_results(heat.id, ManualResultSource(results), advance, confirmed=confirmed)
            return True
        if not self._with_invalidation_confirmation(save):
            return
        self.result_dirty = False
        self._refresh()
        event = self.service.db.event(self.event_id)
        if event.status == 'ready_finals':
            self._message('Stage results saved. Open Finals / Results to run any required tiebreak or generate finals.')
        elif event.status == 'complete':
            self.tabs.setCurrentIndex(5)
            self._message('Event complete. Final standings and winners are shown below.')
        elif event.status == 'tiebreak' and self.service.db.heats(self.event_id, 'finals'):
            self._message('Final placements are pending. Continue the lane-balanced placement tiebreak in Race Control.')
        else:
            self._message('Result saved. Standings updated.')

    def _build_standings(self) -> None:
        layout = self._page('5  Standings')
        layout.addWidget(label('Live standings', True))
        self.standings_stage = QComboBox()
        self.standings_stage.addItem('Preliminary standings', 'preliminary')
        self.standings_stage.addItem('Tiebreak scores (all rounds; each round resolves its own tied group)', 'tiebreak')
        self.standings_stage.addItem('Finals standings', 'finals')
        self.standings_stage.currentIndexChanged.connect(lambda _: self._run(self._refresh_standings))
        layout.addWidget(self.standings_stage)
        layout.addWidget(label('Higher average points lead. Then wins, then lower average finish. '
                               'Final placement ties are resolved by recorded tiebreak rounds. '
                               'Live standings remain provisional until every required heat is saved.'))
        self.standings_table = table(['Rank', 'Car #', 'Racer', 'Car', 'Races', 'Admin', 'Wins', 'Points', 'Avg points', 'Avg finish'])
        layout.addWidget(self.standings_table, 1)

    def _standing_rows(self, stage: str) -> list[list[str]]:
        if not self.event_id:
            return []
        pending = stage == 'finals' and self.service.db.event(self.event_id).status != 'complete'
        return [['Pending' if pending else str(row.rank) if row.scoring_runs else '—', row.racer.car_number, row.racer.name,
                 row.racer.car_name, str(row.completed), str(row.administrative_count), str(row.wins), f'{row.points:.2f}',
                 f'{row.average_points:.2f}', f'{row.average_finish:.2f}' if row.average_finish is not None else '—']
                for row in self.service.standings(self.event_id, stage)]

    def _refresh_standings(self) -> None:
        fill_table(self.standings_table, self._standing_rows(self.standings_stage.currentData()))

    def _build_finals(self) -> None:
        layout = self._page('6  Finals / Results')
        self.finals_heading = label('Finals qualification', True)
        layout.addWidget(self.finals_heading)
        self.finals_description = label('Complete preliminary racing to select finalists.')
        layout.addWidget(self.finals_description)
        actions = QHBoxLayout()
        self.generate_finals_button = self._button('Generate finals heats', self._generate_finals, True)
        self.generate_tiebreak_button = self._button('Generate tiebreak round', self._generate_tiebreak, True)
        self.reset_tiebreak_button = self._button('Reset tiebreaks and finals…', self._reset_tiebreaks)
        self.reset_finals_button = self._button('Reset finals…', self._reset_finals)
        actions.addWidget(self.generate_finals_button)
        actions.addWidget(self.generate_tiebreak_button)
        actions.addWidget(self.reset_tiebreak_button)
        actions.addStretch()
        actions.addWidget(self.reset_finals_button)
        layout.addLayout(actions)
        self.final_table = table(['Rank', 'Car #', 'Racer', 'Car', 'Races', 'Admin', 'Wins', 'Points', 'Avg points', 'Avg finish'])
        layout.addWidget(self.final_table, 1)

    def _generate_tiebreak(self) -> None:
        self._require_saved_result()
        self.service.generate_tiebreak(self.event_id)
        self._refresh()
        self.tabs.setCurrentIndex(3)
        self._message('Tiebreak round saved. Every tied racer will race once in every lane.')

    def _reset_tiebreaks(self) -> None:
        self._require_saved_result()
        impact = self.service.change_impact(self.event_id, 'qualification')
        if self._confirm('Reset tiebreaks and finals?', impact.describe()):
            self.service.reset_tiebreaks(self.event_id)
            self._refresh()
            self._message('Tiebreaks and finals reset. Preliminary results were preserved.')

    def _generate_finals(self) -> None:
        self._require_saved_result()
        self.service.generate_finals(self.event_id)
        self._refresh()
        self.tabs.setCurrentIndex(3)
        self._message('Finals generated. Each finalist runs once in every lane; points start fresh.')

    def _reset_finals(self) -> None:
        self._require_saved_result()
        if self._confirm('Reset finals?', 'Delete the finals schedule, final-placement tiebreaks, and all their results? '
                         'Preliminary results and qualification tiebreaks will be kept. This cannot be undone.'):
            self.service.reset_finals(self.event_id)
            self._refresh()
            self._message('Finals and placement tiebreaks reset. Qualification results were preserved.')

    def _refresh(self) -> None:
        event = self.service.db.event(self.event_id) if self.event_id else None
        racers = self.service.db.racers(self.event_id) if event else []
        heats = self.service.db.heats(self.event_id) if event else []
        registration_open = event is not None and not heats
        self.delete_button.setEnabled(event is not None)
        self.setup_form.setEnabled(True)
        self.save_setup_button.setEnabled(True)
        self.save_setup_button.setText('Save event setup' if event else 'Create event')
        self.event_name.setText(event.name if event else '')
        self.lanes.setValue(event.lanes if event else 4)
        self.runs.setValue(event.runs if event else 4)
        self.finalists.setValue(event.finalists if event else 4)
        self.scoring.setCurrentIndex(self.scoring.findData(event.scoring if event else 'placement'))
        self.late_default.set_policy(LatePolicy(event.late_policy_kind, event.late_policy_value) if event else LatePolicy())
        states = {'registration': 'Registration open', 'preliminary': 'Preliminary racing',
                  'ready_finals': 'Qualification ready', 'tiebreak': 'Tiebreak racing', 'finals': 'Finals racing', 'complete': 'Event complete'}
        self.summary.setText(f'{event.name}  •  {event.lanes} lanes  •  {len(racers)} racers  •  {states[event.status]}'
                             if event else 'Create an event to begin, or select an existing event above.')
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, event is not None)
        self.registration_heading.setText(f'{len(racers)} racers registered • editable; schedule changes require confirmation')
        self.racer_table.blockSignals(True)
        fill_table(self.racer_table, [[r.car_number, r.name, r.car_name, r.tag or '', 'Late' if r.late_entry else 'On time'] for r in racers])
        self.racer_table.blockSignals(False)
        self.racer_form.setEnabled(event is not None)
        self.save_racer_button.setEnabled(event is not None)
        self.clear_racer_button.setEnabled(event is not None)
        self.remove_racer_button.setEnabled(event is not None and self.racer_id is not None)
        self.generate_button.setEnabled(registration_open and bool(racers))
        self._refresh_schedule(heats, racers, event.lanes if event else 4)
        self._refresh_race()
        self._refresh_standings()
        self._refresh_finals()
        can_late = bool(heats) and any(h.completed for h in heats) and all(h.stage == 'preliminary' for h in heats)
        self.late_entry_button.setEnabled(bool(heats))
        self.preview_late_button.setEnabled(can_late)
        self.late_availability.setText('' if can_late else (
            'Late entry closes once tiebreak or finals heats exist.'
            if any(h.stage != 'preliminary' for h in heats) else
            'Late-entry review is available after the first preliminary heat is saved. '
            'Before then, use Back to Racers to add racers through ordinary registration.'))
        self.late_availability.setVisible(not can_late)
        self.registration_pages.setCurrentIndex(0)
        self.late_default_label.setText('Event default: ' + (POLICIES.get(event.late_policy_kind, event.late_policy_kind)
            + (f' ({event.late_policy_value})' if event.late_policy_value else '') if event else 'Create an event first'))
        self._invalidate_late_preview()

    def _refresh_schedule(self, heats, racers, lanes: int) -> None:
        by_id = {r.id: r for r in racers}
        headers = ['Stage', 'Heat', 'Status'] + [f'Lane {i}' for i in range(1, lanes + 1)]
        self.schedule_table.setColumnCount(len(headers))
        self.schedule_table.setHorizontalHeaderLabels(headers)
        for column in range(len(headers)):
            self.schedule_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents if column < 3 else QHeaderView.ResizeMode.Stretch)
        rows = []
        for heat in heats:
            assigned = {a.lane: by_id[a.racer_id] for a in heat.assignments}
            row = [stage_name(heat) + (f' R{heat.round_number}' if heat.stage == 'tiebreak' else ''), str(heat.number),
                   'Saved' if heat.completed else 'Pending']
            row.extend(f'#{assigned[i].car_number} • {assigned[i].name}\n{assigned[i].car_name}'
                       if i in assigned else '— empty —' for i in range(1, lanes + 1))
            rows.append(row)
        fill_table(self.schedule_table, rows)
        self.schedule_table.resizeRowsToContents()
        byes = sum(len(heat.assignments) == 1 for heat in heats)
        self.schedule_heading.setText(f'Review the schedule • {len(heats)} heats'
                                      + (f' • {byes} solo bye(s): 100 points, no win' if byes else ''))
        self.reset_button.setEnabled(bool(heats))
        self.open_heat_button.setEnabled(bool(heats))

    def _refresh_race(self) -> None:
        self.result_dirty = False
        self.position_inputs = {}
        event = self.service.db.event(self.event_id) if self.event_id else None
        heats = self.service.db.heats(self.event_id) if event else []
        current = next((h for h in heats if h.id == event.current_heat_id), heats[0] if heats else None)
        self.loaded_heat_id = current.id if current else None
        self.heat_picker.blockSignals(True)
        self.heat_picker.clear()
        for heat in heats:
            self.heat_picker.addItem(stage_name(heat) + (f' round {heat.round_number}' if heat.stage == 'tiebreak' else '') + f' • Heat {heat.number}'
                                     + (' • Saved' if heat.completed else ' • Pending'), heat.id)
        self.heat_picker.setCurrentIndex(self.heat_picker.findData(self.loaded_heat_id))
        self.heat_picker.blockSignals(False)
        self.heat_picker.setEnabled(bool(heats))
        self.progress.setRange(0, max(1, len(heats)))
        self.progress.setValue(sum(h.completed for h in heats))
        self.progress.setFormat(f'{sum(h.completed for h in heats)} / {len(heats)} heats saved')
        self.race_table.setRowCount(0)
        self.discard_button.setEnabled(False)
        editable = current is not None and not (
            (current.stage == 'preliminary' and any(h.stage != 'preliminary' for h in heats))
            or (current.stage == 'tiebreak' and current.tiebreak_scope == 'qualification' and any(h.stage == 'finals' or
                (h.stage == 'tiebreak' and h.round_number > current.round_number) for h in heats)))
        self.save_result_button.setEnabled(editable)
        self.next_save_button.setEnabled(editable)
        index = next((i for i, h in enumerate(heats) if current and h.id == current.id), -1)
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(0 <= index < len(heats)-1)
        if not current:
            self.race_heading.setText('Generate heats to start racing')
            self.entry_status.setText('')
            self.lane_status.setText('Manual lane identification: scheduled assignments shown.')
            return
        stage_heats = [h for h in heats if h.stage == current.stage and h.tiebreak_scope == current.tiebreak_scope]
        self.race_heading.setText(stage_name(current) + (f' • round {current.round_number}' if current.stage == 'tiebreak' else '')
                                  + f' • Heat {current.number} • {sum(h.completed for h in stage_heats)}/{len(stage_heats)} saved')
        racers = {r.id: r for r in self.service.db.racers(self.event_id)}
        assigned = {a.lane: racers[a.racer_id] for a in current.assignments}
        saved = {r.lane: r.position for r in current.results}
        self.race_table.setRowCount(event.lanes)
        for lane in range(1, event.lanes + 1):
            racer = assigned.get(lane)
            values = [str(lane), racer.car_number if racer else '—', racer.name if racer else 'Empty lane',
                      racer.car_name if racer else '']
            for column, value in enumerate(values):
                self.race_table.setItem(lane - 1, column, QTableWidgetItem(value))
            if racer:
                position = QSpinBox()
                position.setRange(0, len(assigned))
                position.setSpecialValueText('—')
                position.setValue(saved.get(lane, 0))
                position.setAccessibleName(f'Lane {lane} finishing position')
                position.setMinimumHeight(44)
                position.setEnabled(editable)
                position.valueChanged.connect(self._mark_dirty)
                self.race_table.setCellWidget(lane - 1, 4, position)
                self.position_inputs[lane] = position
            else:
                self.race_table.setItem(lane - 1, 4, QTableWidgetItem('—'))
        self.entry_status.setText('Later qualification races exist. Reset the dependent tiebreak/finals stage to correct this result.'
                                 if not editable else 'Saved result • Change a position and save to correct it.'
                                 if current.completed else 'No result saved • Enter each occupied lane’s finishing position.')
        mismatches = self.service.check_lanes(current.id)
        self.lane_status.setText('Lane check: ' + ' '.join(mismatches) if mismatches
                                else 'Manual lane check • Confirm each car number matches its scheduled lane before racing.')
        self.race_help.setText('Solo run / bye: enter position 1. This earns 100 points but does not count as a competitive win.'
                               if len(assigned) == 1 else
                               f'Enter unique finishing positions 1–{len(assigned)}. Empty lanes do not receive results. Save commits immediately.')

    def _refresh_finals(self) -> None:
        self.generate_finals_button.setEnabled(False)
        self.generate_tiebreak_button.setEnabled(False)
        self.reset_finals_button.setEnabled(False)
        self.reset_tiebreak_button.setEnabled(False)
        fill_table(self.final_table, self._standing_rows('finals'))
        self.finals_heading.setText('Finals qualification')
        if not self.event_id:
            self.finals_description.setText('Complete preliminary racing to select finalists.')
            return
        event = self.service.db.event(self.event_id)
        final_heats = self.service.db.heats(self.event_id, 'finals')
        tiebreaks = self.service.db.heats(self.event_id, 'tiebreak')
        self.reset_tiebreak_button.setEnabled(bool(tiebreaks))
        if final_heats:
            self.reset_finals_button.setEnabled(True)
            if event.status == 'complete':
                winners = [f'#{r.racer.car_number} {r.racer.name} — {r.racer.car_name}'
                           for r in self.service.standings(self.event_id, 'finals') if r.rank == 1]
                self.finals_heading.setText('Final results • Winner')
                self.finals_description.setText('\n'.join(winners) +
                    '\nPlacements include recorded tiebreak decisions. Scores below are from finals heats only.')
            elif event.status == 'tiebreak':
                plan = self.service.final_placements(self.event_id)
                self.finals_heading.setText(f'Final placement TIEBREAK • round {plan.pending_round}')
                self.finals_description.setText('Final placements are pending. Continue in Race Control: '
                    'each tied racer runs once in every lane. Further rounds are generated automatically '
                    'until all finishing positions are resolved. Finals scores below exclude tiebreak scores.')
            else:
                self.finals_heading.setText('Finals in progress')
                self.finals_description.setText(f'{sum(h.completed for h in final_heats)} of {len(final_heats)} finals heats saved. '
                                                'Each finalist runs once per lane. Continue in Race Control.')
            return
        if event.status not in ('ready_finals', 'tiebreak'):
            self.finals_description.setText('Save every preliminary heat result before qualification. Finals points start at zero.')
            return
        plan = self.service.qualification(self.event_id)
        racers = {r.id: r for r in self.service.db.racers(self.event_id)}
        automatic = ', '.join(f'#{racers[key].car_number} {racers[key].name}' for key in plan.automatic) or 'None yet'
        text = f'Qualified: {automatic}. Finals give every finalist {event.lanes} runs, one in each lane.'
        if plan.tied:
            names = ', '.join(f'#{racers[key].car_number} {racers[key].name}' for key in plan.tied)
            text += f'\n{len(plan.tied)} racers tied for {plan.places} place(s): {names}.'
            if plan.pending_round is not None:
                self.finals_heading.setText(f'TIEBREAK • round {plan.pending_round} in progress')
                text += '\nFinish the recorded tiebreak heats in Race Control.'
            else:
                self.finals_heading.setText('TIEBREAK required at the qualification cutoff')
                text += '\nGenerate a balanced round: one race per racer in every lane. Still tied? Race another round.'
                self.generate_tiebreak_button.setEnabled(True)
            if tiebreaks:
                latest = max(h.round_number for h in tiebreaks)
                fill_table(self.final_table, self._standing_rows_for_round(latest))
        else:
            self.generate_finals_button.setEnabled(True)
        self.finals_description.setText(text)

    def _standing_rows_for_round(self, round_number: int):
        return [[str(row.rank) if row.scoring_runs else '—', row.racer.car_number, row.racer.name,
                 row.racer.car_name, str(row.completed), '0', str(row.wins), f'{row.points:.2f}',
                 f'{row.average_points:.2f}', f'{row.average_finish:.2f}' if row.average_finish is not None else '—']
                for row in self.service.standings(self.event_id, 'tiebreak', round_number)]

    def _build_late_entry(self) -> None:
        self.late_entry_page = QWidget()
        self.registration_pages.addWidget(self.late_entry_page)
        layout = QVBoxLayout(self.late_entry_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.back_to_racers_button = self._button('← Back to Racers',
            lambda: self.registration_pages.setCurrentIndex(0))
        layout.addWidget(self.back_to_racers_button)
        layout.addWidget(label('Add late racers • preserve completed racing', True))
        self.late_availability = label('')
        layout.addWidget(self.late_availability)
        layout.addWidget(label('Enter one racer per row. Review the group treatment and schedule impact before committing. '
                               'Completed races are never changed by this workflow.'))
        self.late_default_label = label('Event default: last-place equivalent')
        layout.addWidget(self.late_default_label)
        self.late_policy = PolicyEditor(allow_default=True)
        layout.addWidget(self.late_policy)
        self.late_table = table(['Racer name *', 'Car name', 'Car number *', 'RFID / tag'])
        self.late_table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.late_table.setRowCount(3)
        self.late_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.late_table, 1)
        actions = QHBoxLayout()
        self.add_late_row_button = self._button('+ Add racer row', self._add_late_row)
        self.remove_late_row_button = self._button('− Remove selected row', self._remove_late_row)
        self.add_late_row_button.setToolTip('Add another racer to this late-entry group')
        self.remove_late_row_button.setToolTip('Remove the selected unsaved racer from this group')
        actions.addWidget(self.add_late_row_button)
        actions.addWidget(self.remove_late_row_button)
        self.preview_late_button = self._button('Review late-entry consequences', self._preview_late, True)
        actions.addWidget(self.preview_late_button)
        layout.addLayout(actions)
        self.late_review = label('No preview yet. Fill in the racers and choose Review.')
        self.late_review.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.late_review)
        self.commit_late_button = self._button('Confirm and add late racers…', self._commit_late, True)
        self.commit_late_button.setEnabled(False)
        layout.addWidget(self.commit_late_button)
        self.late_table.cellChanged.connect(self._invalidate_late_preview)
        self.late_policy.changed.connect(self._invalidate_late_preview)

    def _open_late_entry(self) -> None:
        self._require_saved_result()
        self.tabs.setCurrentIndex(1)
        self.registration_pages.setCurrentWidget(self.late_entry_page)

    def _add_late_row(self) -> None:
        self.late_table.insertRow(self.late_table.rowCount())
        self._invalidate_late_preview()

    def _remove_late_row(self) -> None:
        row = self.late_table.currentRow()
        if row >= 0:
            self.late_table.removeRow(row)
            self._invalidate_late_preview()

    def _invalidate_late_preview(self, *_) -> None:
        self.late_preview = None
        self.commit_late_button.setEnabled(False)
        self.late_review.setText('Review required. Completed races will be preserved; only unrun preliminary heats can change.')

    def _preview_late(self) -> None:
        self._require_saved_result()
        drafts = []
        for row in range(self.late_table.rowCount()):
            values = [self.late_table.item(row, col).text() if self.late_table.item(row, col) else '' for col in range(4)]
            if any(value.strip() for value in values):
                drafts.append(RacerDraft(*values))
        self.late_preview = self.service.preview_late_entries(self.event_id, drafts, self.late_policy.policy())
        preview = self.late_preview
        report = preview.schedule.report
        points = float(Fraction(preview.points_per_admin)) if preview.points_per_admin is not None else 0
        lines = [f'Treatment: {POLICIES[preview.policy.kind]}' + (f' ({preview.policy.value})' if preview.policy.value else ''),
                 f'Each racer: {preview.target_runs} target runs; {preview.missed_runs} effectively missed; '
                 f'{preview.actual_runs} actual runs to race; {preview.admin_runs} administrative scores.',
                 f'Administrative equivalent: {preview.placement_per_admin or "none"} place; '
                 f'{points:.2f} points each; {points * preview.admin_runs:.2f} administrative points per racer.',
                 'Racers: ' + ', '.join(f'#{r.car_number} {r.name}' for r in preview.racers),
                 f'Unrun schedule: {preview.old_unrun_heats} → {report.future_heats} heats; '
                 f'{report.retained_heats} unchanged heats retained. Completed heats/results preserved.',
                 f'Projected lane-count spread: at most {report.max_racer_lane_spread} per racer; '
                 f'{report.overall_lane_spread} across the track. Future solo heats: {report.solo_heats}. '
                 f'Repeated opponent meetings: {report.repeated_opponent_meetings}.']
        self.late_review.setText('\n'.join(lines))
        self.commit_late_button.setEnabled(True)

    def _commit_late(self) -> None:
        self._require_saved_result()
        if self.late_preview is None:
            raise RaceError('Review the late-entry consequences first.')
        if not self._confirm('Confirm late racer group', self.late_review.text()):
            return
        self.service.commit_late_entries(self.late_preview)
        self.late_table.clearContents()
        self.late_policy.kind.setCurrentIndex(0)
        self._refresh()
        self.tabs.setCurrentIndex(3)
        self._message('Late racers added. Completed race history is unchanged; the remaining schedule is saved.')

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.result_dirty and not self._confirm('Unsaved finishing positions',
                'Close and discard the current unsaved entry? Previously saved results are safe.'):
            event.ignore()
        else:
            event.accept()
