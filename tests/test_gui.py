"""Real Qt widgets, exercised offscreen; no pytest-qt dependency required."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QPA_PLATFORMTHEME'] = ''

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from pinewood_derby.database import Database
from pinewood_derby.gui import MainWindow
from pinewood_derby.service import RaceService
from pinewood_derby.models import LatePolicy, LaneResult
from pinewood_derby.hardware import ManualResultSource


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyle('Fusion')
    yield instance


@pytest.fixture
def window(app, tmp_path):
    path = tmp_path / 'ui.sqlite3'
    database = Database(path)
    window = MainWindow(RaceService(database), path)
    window.show()
    app.processEvents()
    yield window
    window.result_dirty = False
    window.close()
    database.close()


def click(button, app):
    assert button.isEnabled()
    button.click()
    app.processEvents()


def register(window, app, count=4, lanes=2, runs=2, finalists=2):
    window.event_name.setText('GUI derby')
    window.lanes.setValue(lanes)
    window.runs.setValue(runs)
    window.finalists.setValue(finalists)
    click(window.save_setup_button, app)
    for i in range(count):
        window.racer_name.setText(f'Racer {i}')
        window.car_name.setText(f'Car {i}')
        window.car_number.setText(str(i))
        click(window.save_racer_button, app)
    click(window.generate_button, app)
    window.tabs.setCurrentIndex(3)


def enter_result(window, reverse=False):
    values = list(range(1, len(window.position_inputs)+1))
    if reverse:
        values.reverse()
    for widget, value in zip(window.position_inputs.values(), values):
        widget.setValue(value)


def finish_current_stage(window, app):
    stage = window.service.db.heat(window.loaded_heat_id).stage
    for _ in [h for h in window.service.db.heats(window.event_id, stage) if not h.completed]:
        if stage == 'tiebreak':
            heat = window.service.db.heat(window.loaded_heat_id)
            for position, assignment in enumerate(sorted(heat.assignments, key=lambda a: a.racer_id), 1):
                window.position_inputs[assignment.lane].setValue(position)
        else:
            enter_result(window)
        click(window.next_save_button, app)


def test_gui_workflow_registration_results_finals_and_restart(window, app, tmp_path):
    assert window.event_id is None
    assert not window.tabs.isTabEnabled(1)
    register(window, app)
    event_id = window.event_id
    assert window.racer_table.rowCount() == 4
    assert window.schedule_table.rowCount() == 4
    assert len(window.position_inputs) == 2
    assert all(widget.value() == 0 for widget in window.position_inputs.values())
    assert window.racer_form.isEnabled()
    finish_current_stage(window, app)
    assert window.service.db.event(event_id).status == 'ready_finals'
    for _ in range(10):
        if not window.service.qualification(event_id).tied:
            break
        click(window.generate_tiebreak_button, app)
        finish_current_stage(window, app)
    click(window.generate_finals_button, app)
    assert 'Finals' in window.race_heading.text()
    window.tabs.setCurrentIndex(1)
    click(window.late_entry_button, app)
    assert window.late_entry_page.isVisible()
    assert not window.preview_late_button.isEnabled()
    assert 'closes' in window.late_availability.text()
    click(window.back_to_racers_button, app)
    window.tabs.setCurrentIndex(3)
    finish_current_stage(window, app)
    assert window.service.db.event(event_id).status == 'tiebreak'
    assert 'Final placement TIEBREAK' in window.race_heading.text()
    assert 'Final placement TIEBREAK' in window.finals_heading.text()
    assert window.final_table.item(0, 0).text() == 'Pending'
    finish_current_stage(window, app)
    assert window.service.db.event(event_id).status == 'complete'
    assert 'Final results' in window.finals_heading.text()
    assert window.final_table.rowCount() == 2
    assert [window.final_table.item(i, 0).text() for i in range(2)] == ['1', '2']
    path = window.service.db.connection.execute('PRAGMA database_list').fetchone()[2]
    second_db = Database(path)
    try:
        reopened = MainWindow(RaceService(second_db), path)
        assert reopened.event_id == event_id
        assert 'Final results' in reopened.finals_heading.text()
        assert reopened.final_table.rowCount() == 2
        assert reopened.loaded_heat_id == window.loaded_heat_id
        reopened.close()
    finally:
        second_db.close()


def test_invalid_entry_and_unsaved_navigation_are_safe(window, app):
    register(window, app)
    first_heat = window.loaded_heat_id
    for widget in window.position_inputs.values():
        widget.setValue(1)
    click(window.next_save_button, app)
    assert window.loaded_heat_id == first_heat
    assert not window.service.db.heat(first_heat).completed
    assert 'unique' in window.notice.text()
    click(window.next_button, app)
    assert window.loaded_heat_id == first_heat
    assert 'unsaved' in window.notice.text()
    window.heat_picker.setCurrentIndex(1)
    assert window.loaded_heat_id == first_heat
    assert window.heat_picker.currentData() == first_heat
    click(window.discard_button, app)
    assert not window.result_dirty
    click(window.next_button, app)
    assert window.loaded_heat_id != first_heat
    click(window.previous_button, app)
    assert window.loaded_heat_id == first_heat
    enter_result(window)
    click(window.save_result_button, app)
    previous = window.service.db.heat(first_heat).results
    enter_result(window, reverse=True)
    click(window.save_result_button, app)
    assert window.service.db.heat(first_heat).results != previous


def test_racer_edit_remove_and_destructive_confirmation(window, app, monkeypatch):
    window.event_name.setText('Registration testing')
    window.finalists.setValue(1)
    click(window.save_setup_button, app)
    window.racer_name.setText('Alex')
    window.car_number.setText('7')
    click(window.save_racer_button, app)
    window.racer_table.selectRow(0)
    window.racer_name.setText('Alex updated')
    click(window.save_racer_button, app)
    assert window.service.db.racers(window.event_id)[0].name == 'Alex updated'
    window.racer_table.selectRow(0)
    monkeypatch.setattr(window, '_confirm', lambda *_: False)
    click(window.remove_racer_button, app)
    assert len(window.service.db.racers(window.event_id)) == 1
    click(window.generate_button, app)
    click(window.reset_button, app)
    assert window.service.db.heats(window.event_id)
    monkeypatch.setattr(window, '_confirm', lambda *_: True)
    click(window.reset_button, app)
    assert not window.service.db.heats(window.event_id)
    window.racer_table.selectRow(0)
    click(window.remove_racer_button, app)
    assert not window.service.db.racers(window.event_id)


def test_gui_setup_remains_editable_and_invalidation_can_be_cancelled(window, app, monkeypatch):
    register(window, app)
    event_id = window.event_id
    saved = window.service.db.heats(event_id)
    assert window.setup_form.isEnabled() and window.racer_form.isEnabled()
    confirmations = []
    monkeypatch.setattr(window, '_confirm', lambda title, message: confirmations.append(message) or False)
    window.event_name.setText('Renamed without resetting')
    click(window.save_setup_button, app)
    assert not confirmations and window.service.db.heats(event_id) == saved
    window.lanes.setValue(3)
    click(window.save_setup_button, app)
    assert '0 completed' in confirmations[-1]
    assert window.service.db.heats(event_id) == saved
    monkeypatch.setattr(window, '_confirm', lambda *_: True)
    click(window.save_setup_button, app)
    assert not window.service.db.heats(event_id)
    assert window.generate_button.isEnabled()
    click(window.generate_button, app)
    assert window.service.db.event(event_id).lanes == 3


def test_gui_destructive_warning_counts_saved_results(window, app, monkeypatch):
    register(window, app)
    enter_result(window)
    click(window.next_save_button, app)
    saved = window.service.db.heats(window.event_id)
    messages = []
    monkeypatch.setattr(window, '_confirm', lambda _, text: messages.append(text) or False)
    window.runs.setValue(3)
    click(window.save_setup_button, app)
    assert '1 completed' in messages[-1] and '2 recorded lane results' in messages[-1]
    assert 'Add Late Racers' in messages[-1]
    assert window.service.db.heats(window.event_id) == saved


def test_gui_plus_minus_controls_are_explicit(window, app):
    assert window.lanes.decrement.text() == '−'
    assert window.lanes.increment.text() == '+'
    assert 'Decrease' in window.lanes.decrement.accessibleName()
    assert window.lanes.increment.toolTip()
    start = window.lanes.value()
    click(window.lanes.increment, app)
    assert window.lanes.value() == start + 1
    click(window.lanes.decrement, app)
    assert window.lanes.value() == start
    register(window, app)
    entry = next(iter(window.position_inputs.values()))
    click(entry.increment, app)
    assert entry.value() == 1 and window.result_dirty
    click(entry.decrement, app)
    assert entry.value() == 0


def add_late_rows(window, rows):
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            window.late_table.setItem(row, column, QTableWidgetItem(value))


def test_late_entry_navigation_and_availability(window, app, monkeypatch):
    assert window.tabs.count() == 6
    assert all('Late Racers' not in window.tabs.tabText(i) for i in range(window.tabs.count()))
    assert not window.late_entry_button.isEnabled()
    register(window, app)
    window.tabs.setCurrentIndex(1)
    assert window.tabs.isTabEnabled(1)
    assert window.late_entry_button.isVisible()
    click(window.late_entry_button, app)
    assert window.late_entry_page.isVisible()
    assert not window.preview_late_button.isEnabled()
    assert 'first preliminary heat' in window.late_availability.text()
    before = window.service.db.heats(window.event_id)
    add_late_rows(window, [['Late Alex', '', '101', '']])
    click(window.back_to_racers_button, app)
    assert window.racer_table.isVisible()
    assert window.service.db.heats(window.event_id) == before
    assert len(window.service.db.racers(window.event_id)) == 4
    enter_result(window)
    click(window.late_entry_button, app)
    assert window.racer_table.isVisible()
    assert 'unsaved' in window.notice.text()
    click(window.next_save_button, app)
    click(window.late_entry_button, app)
    assert window.preview_late_button.isEnabled()
    assert window.late_table.item(0, 0).text() == 'Late Alex'
    click(window.back_to_racers_button, app)
    monkeypatch.setattr(window, '_confirm', lambda *_: True)
    click(window.reset_button, app)
    assert window.racer_table.isVisible()
    assert not window.late_entry_button.isEnabled()


def test_gui_bulk_late_preview_override_cancel_and_commit(window, app, monkeypatch):
    register(window, app)
    for _ in range(2):
        enter_result(window)
        click(window.next_save_button, app)
    before = [h for h in window.service.db.heats(window.event_id) if h.completed]
    click(window.late_entry_button, app)
    assert window.tabs.currentIndex() == 1
    assert window.registration_pages.currentWidget() == window.late_entry_page
    add_late_rows(window, [['Late Alex', 'Comet', '101', ''], ['Late Sam', 'Rocket', '102', '']])
    window.late_policy.kind.setCurrentIndex(window.late_policy.kind.findData('none'))
    click(window.preview_late_button, app)
    assert '1 effectively missed' in window.late_review.text()
    assert '2 actual runs' in window.late_review.text()
    assert '0 administrative scores' in window.late_review.text()
    monkeypatch.setattr(window, '_confirm', lambda *_: False)
    click(window.commit_late_button, app)
    assert len(window.service.db.racers(window.event_id)) == 4
    window.late_table.item(0, 0).setText('Late Alex updated')
    assert not window.commit_late_button.isEnabled()
    click(window.preview_late_button, app)
    monkeypatch.setattr(window, '_confirm', lambda *_: True)
    click(window.commit_late_button, app)
    assert len(window.service.db.racers(window.event_id)) == 6
    assert [h for h in window.service.db.heats(window.event_id) if h.completed] == before
    assert window.service.db.event(window.event_id).late_policy_kind == 'last'
    assert not window.service.db.administrative_results(window.event_id)
    assert window.tabs.currentIndex() == 3


def test_gui_late_specific_policy_preview_and_admin_standings(window, app, monkeypatch):
    register(window, app)
    for _ in range(2):
        enter_result(window)
        click(window.next_save_button, app)
    add_late_rows(window, [['Late Avery', 'Rocket', '103', '']])
    window.late_policy.set_policy(LatePolicy('points', '25'))
    click(window.preview_late_button, app)
    assert '25.00 points each' in window.late_review.text()
    monkeypatch.setattr(window, '_confirm', lambda *_: True)
    click(window.commit_late_button, app)
    late = next(r for r in window.service.standings(window.event_id) if r.racer.late_entry)
    assert late.administrative_count == 1 and late.completed == 0 and late.points == 25
    assert window.standings_table.horizontalHeaderItem(5).text() == 'Admin'


def test_gui_tiebreak_is_required_recorded_and_resolves(window, app):
    register(window, app, count=2, lanes=2, runs=2, finalists=1)
    # Winning lane 1 in each heat gives each racer one win after swapping lanes.
    finish_current_stage(window, app)
    assert window.generate_tiebreak_button.isEnabled()
    assert not window.generate_finals_button.isEnabled()
    click(window.generate_tiebreak_button, app)
    assert 'TIEBREAK' in window.race_heading.text()
    assert 'round 1' in window.race_heading.text()
    assert 'TIEBREAK' in window.schedule_table.item(2, 0).text()
    finish_current_stage(window, app)
    assert window.generate_finals_button.isEnabled()
    click(window.generate_finals_button, app)
    assert window.service.db.heats(window.event_id, 'tiebreak')
    finish_current_stage(window, app)
    assert window.service.db.event(window.event_id).status == 'complete'


def test_gui_placement_tiebreak_resume_and_correction(window, app, monkeypatch):
    register(window, app, count=2, lanes=2, runs=2, finalists=2)
    finish_current_stage(window, app)
    click(window.generate_finals_button, app)
    finish_current_stage(window, app)
    assert 'Final placement TIEBREAK' in window.race_heading.text()
    path = window.service.db.connection.execute('PRAGMA database_list').fetchone()[2]
    second_db = Database(path)
    try:
        reopened = MainWindow(RaceService(second_db), path)
        assert reopened.loaded_heat_id == window.loaded_heat_id
        assert 'Final placement TIEBREAK' in reopened.race_heading.text()
        assert reopened.next_save_button.isEnabled()
        assert reopened.final_table.item(0, 0).text() == 'Pending'
        reopened.close()
    finally:
        second_db.close()
    heats = window.service.tiebreak_heats(window.event_id, 'finals')
    finish_current_stage(window, app)
    assert window.service.db.event(window.event_id).status == 'complete'
    window.tabs.setCurrentIndex(3)
    window.heat_picker.setCurrentIndex(window.heat_picker.findData(heats[0].id))
    for widget in window.position_inputs.values():
        widget.setValue(3 - widget.value())
    click(window.save_result_button, app)
    assert 'Final placement TIEBREAK' in window.race_heading.text()
    assert window.service.db.event(window.event_id).status == 'tiebreak'
    assert window.final_table.item(0, 0).text() == 'Pending'
    # Correcting the other old heat invalidates the newly generated round.
    window.heat_picker.setCurrentIndex(window.heat_picker.findData(heats[1].id))
    for widget in window.position_inputs.values():
        widget.setValue(3 - widget.value())
    before = window.service.db.heats(window.event_id)
    messages = []
    monkeypatch.setattr(window, '_confirm', lambda _, text: messages.append(text) or False)
    click(window.save_result_button, app)
    assert 'later final-placement tiebreak rounds' in messages[-1]
    assert window.service.db.heats(window.event_id) == before
    assert window.result_dirty
    monkeypatch.setattr(window, '_confirm', lambda *_: True)
    click(window.save_result_button, app)
    assert window.service.db.event(window.event_id).status == 'complete'
    assert [window.final_table.item(i, 0).text() for i in range(2)] == ['1', '2']
    window.standings_stage.setCurrentIndex(window.standings_stage.findData('finals'))
    assert [window.standings_table.item(i, 2).text() for i in range(2)] == [
        row.racer.name for row in window.service.standings(window.event_id, 'finals')]
