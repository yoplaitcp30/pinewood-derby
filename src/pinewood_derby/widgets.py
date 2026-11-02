"""Small operator controls; all race policy stays outside Qt."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QWidget)

from .late_entry import POLICIES
from .models import LatePolicy


class NumberControl(QWidget):
    """An integer editor with literal, labelled minus and plus buttons."""
    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.decrement = QPushButton('−')
        self.increment = QPushButton('+')
        self.editor = QSpinBox()
        self.editor.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for button in (self.decrement, self.increment):
            button.setFixedWidth(42)
            button.setAutoRepeat(True)
        layout.addWidget(self.decrement)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.increment)
        self.decrement.clicked.connect(self.editor.stepDown)
        self.increment.clicked.connect(self.editor.stepUp)
        self.editor.valueChanged.connect(self.valueChanged.emit)
        self.editor.valueChanged.connect(self._update_buttons)
        self.setAccessibleName('value')
        self.setFocusProxy(self.editor)
        self._update_buttons()

    def _update_buttons(self, *_):
        self.decrement.setEnabled(self.editor.value() > self.editor.minimum())
        self.increment.setEnabled(self.editor.value() < self.editor.maximum())

    def setRange(self, minimum, maximum):
        self.editor.setRange(minimum, maximum)
        self._update_buttons()

    def setValue(self, value):
        self.editor.setValue(value)

    def value(self):
        self.editor.interpretText()
        return self.editor.value()

    def setSpecialValueText(self, text):
        self.editor.setSpecialValueText(text)

    def setAccessibleName(self, name):
        super().setAccessibleName(name)
        self.editor.setAccessibleName(name)
        self.decrement.setAccessibleName(f'Decrease {name}')
        self.increment.setAccessibleName(f'Increase {name}')
        self.decrement.setToolTip(f'Decrease {name} by one')
        self.increment.setToolTip(f'Increase {name} by one')


class PolicyEditor(QWidget):
    changed = Signal()

    def __init__(self, allow_default=False):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        if allow_default:
            self.kind.addItem('Use event default', 'default')
        for key, description in POLICIES.items():
            self.kind.addItem(description, key)
        self.value = QLineEdit()
        self.value.setMaximumWidth(130)
        self.value.setAccessibleName('Late-entry policy value')
        self.value_label = QLabel('Value')
        layout.addWidget(self.kind, 1)
        layout.addWidget(self.value_label)
        layout.addWidget(self.value)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.value.textChanged.connect(self.changed.emit)
        self._kind_changed()

    def _kind_changed(self, *_):
        custom = self.kind.currentData() in ('placement', 'points')
        self.value.setVisible(custom)
        self.value_label.setVisible(custom)
        self.value.setPlaceholderText('e.g. 2' if self.kind.currentData() == 'placement' else 'e.g. 25')
        self.changed.emit()

    def policy(self) -> LatePolicy | None:
        kind = self.kind.currentData()
        if kind == 'default':
            return None
        return LatePolicy(kind, self.value.text().strip() if kind in ('placement', 'points') else None)

    def set_policy(self, policy: LatePolicy):
        self.kind.setCurrentIndex(self.kind.findData(policy.kind))
        self.value.setText(policy.value or '')
