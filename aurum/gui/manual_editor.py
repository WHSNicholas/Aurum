# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 GUI - Manual Editor                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Compose a written instruction or explicit journal entry in one revision-bound dialog.

Each method retains its own draft while the person switches views. Saving submits only the selected method;
instructions wait for processing, while validated journal lines post immediately through the shared editor.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabBar,
    QToolButton, QVBoxLayout,
)

# Aurum imports
from aurum.gui.editor import JournalEditor
from aurum.gui.icons import icon
from aurum.gui.manual import InstructionForm


# %% 1. Manual Entry Composer --------------------------------------------------------------------------------
class ManualEditor(QDialog):
    """
    Keep both entry methods together without interpreting text or silently changing saved accounting.
    """

    action_requested = Signal(str, object)

    def __init__(self, context: dict, parent=None) -> None:
        """
        Build the two draft views from one saved manual identity and fresh accounting context.
        """
        super().__init__(parent)
        self.context = deepcopy(context)
        self._busy = False
        self._saved = False
        self.setWindowTitle("Manual")
        self.setModal(True)
        self.setMinimumSize(820, 580)
        self.resize(1320, 740)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        self.heading = QLabel("Manual")
        self.heading.setObjectName("workspacePageTitle")
        layout.addWidget(self.heading)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("workspacePageSubtitle")
        self.subtitle.setWordWrap(False)
        layout.addWidget(self.subtitle)
        self.modes = QTabBar()
        self.modes.setObjectName("manualEntryModes")
        self.modes.setExpanding(False)
        self.modes.setDrawBase(False)
        self.modes.addTab("Instruction")
        self.modes.addTab("Journal entry")
        self.modes.setAccessibleName("Manual entry method")
        layout.addWidget(self.modes)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.instruction = InstructionForm(context.get("manual_record"), context.get("snapshot", {}), self)
        self.instruction_scroll = QScrollArea()
        self.instruction_scroll.setWidgetResizable(True)
        self.instruction_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.instruction_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.instruction_scroll.setWidget(self.instruction)
        self.stack.addWidget(self.instruction_scroll)
        self.journal = JournalEditor(context, self, embedded=True)
        self.journal_scroll = QScrollArea()
        self.journal_scroll.setWidgetResizable(True)
        self.journal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.journal_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.journal_scroll.setWidget(self.journal)
        self.stack.addWidget(self.journal_scroll)
        self.notice = QLabel()
        self.notice.setObjectName("workspaceHint")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        self.reload_button = QToolButton()
        self.reload_button.setObjectName("manualIconAction")
        self.reload_button.setIcon(icon("refresh", size=18))
        self.reload_button.setIconSize(QSize(18, 18))
        self.reload_button.setFixedSize(34, 34)
        self.reload_button.setToolTip("Reload this method from saved data")
        self.reload_button.setAccessibleName("Reload saved manual entry")
        self.reload_button.clicked.connect(self._reload)
        actions.addWidget(self.reload_button)
        self.save_state = QLabel()
        self.save_state.setObjectName("workspaceHint")
        actions.addWidget(self.save_state, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton()
        self.save_button.setProperty("primary", True)
        self.save_button.clicked.connect(self.save_changes)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.instruction.changed.connect(self._update_actions)
        self.journal.changed.connect(self._update_actions)
        self.journal.action_requested.connect(self._journal_action)
        self.modes.currentChanged.connect(self._change_mode)
        unavailable = context.get("direct_unavailable", "")
        self.modes.setTabEnabled(1, not bool(unavailable))
        self.modes.setTabToolTip(1, unavailable)
        self.modes.setCurrentIndex(1 if context.get("mode") == "direct" and not unavailable else 0)
        self._change_mode(self.modes.currentIndex())

    def has_unsaved_changes(self) -> bool:
        """
        Protect both drafts when the person closes the popup after switching methods.
        """
        return self.instruction.has_unsaved_changes() or self.journal.has_unsaved_changes()

    def set_busy(self, busy: bool) -> None:
        """
        Freeze both drafts while the controller owns a save or context reload.
        """
        self._busy = busy
        self.instruction.set_busy(busy)
        self.journal.set_busy(busy)
        self.modes.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.reload_button.setEnabled(not busy)
        self._update_actions()

    def show_error(self, message: str) -> None:
        """
        Keep the submitted draft visible after failed validation, stale input or cancellation.
        """
        if self.modes.currentIndex() == 0:
            self.instruction.show_error(message)
        else:
            self.journal.show_error(message)

    def mark_saved(self) -> None:
        """
        Close only after the controller confirms the selected method was saved successfully.
        """
        self._saved = True
        self.accept()

    def reload_context(self, context: dict) -> None:
        """
        Replace fresh journal evidence while retaining the other method's draft and original revision.
        """
        self.context = deepcopy(context)
        if context.get("reload_instruction"):
            previous = self.instruction_scroll.takeWidget()
            self.instruction = InstructionForm(
                context.get("manual_record"), context.get("snapshot", {}), self,
            )
            self.instruction_scroll.setWidget(self.instruction)
            self.instruction.changed.connect(self._update_actions)
            previous.deleteLater()
        if not context.get("reload_instruction"):
            previous = self.journal_scroll.takeWidget()
            self.journal = JournalEditor(context, self, embedded=True)
            self.journal_scroll.setWidget(self.journal)
            self.journal.changed.connect(self._update_actions)
            self.journal.action_requested.connect(self._journal_action)
            previous.deleteLater()
        unavailable = context.get("direct_unavailable", "")
        self.modes.setTabEnabled(1, not bool(unavailable))
        self.modes.setTabToolTip(1, unavailable)
        if unavailable:
            self.modes.setCurrentIndex(0)
        self._change_mode(self.modes.currentIndex())

    def save_changes(self) -> None:
        """
        Submit the active method without converting or submitting the hidden draft.
        """
        if self._busy:
            return
        if self.modes.currentIndex() == 1:
            self.journal.save_changes()
            return
        try:
            payload = self.instruction.payload()
        except ValueError as error:
            self.instruction.show_error(str(error))
            return
        self.action_requested.emit("save_manual", payload)

    def reject(self) -> None:
        """
        Retain unfinished input unless the person explicitly discards both drafts.
        """
        if self._busy:
            return
        if not self._saved and self.has_unsaved_changes():
            answer = QMessageBox.question(
                self, "Discard manual entry?", "Discard the unsaved instruction and journal drafts?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()

    def _change_mode(self, index: int) -> None:
        """
        Show the chosen method while retaining all values in the inactive form.
        """
        self.stack.setCurrentIndex(index)
        if getattr(self, "_visible_mode", None) != index:
            self.resize(1320, 820) if index else self.resize(940, 700)
            self._visible_mode = index
        self.subtitle.setText(
            "Describe what happened, then save it for processing."
            if index == 0 else "Enter balanced journal lines and post them immediately."
        )
        self._update_actions()

    def _update_actions(self) -> None:
        """
        Explain the selected save's effect and keep validation with its own draft.
        """
        if not hasattr(self, "save_button"):
            return
        instruction = self.modes.currentIndex() == 0
        self.save_button.setText("Save instruction" if instruction else "Post journal entry")
        self.save_button.setMinimumWidth(
            max(150, self.save_button.fontMetrics().horizontalAdvance(self.save_button.text()) + 40)
        )
        written = bool(self.instruction.instruction.toPlainText().strip())
        enabled = (
            self.instruction.has_unsaved_changes() and written
            if instruction else self.journal.save_button.isEnabled()
        )
        self.save_button.setEnabled(not self._busy and enabled)
        self.save_state.setText(
            "Saving…" if self._busy else "Unsaved changes" if self.has_unsaved_changes() else ""
        )
        inactive = self.journal if instruction else self.instruction
        message = ""
        if inactive.has_unsaved_changes():
            message = "Only this method will be saved; the other draft will be discarded."
        if self.context.get("direct_unavailable"):
            message = self.context["direct_unavailable"]
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _journal_action(self, action: str, payload: dict) -> None:
        """
        Keep evidence reloads inside this popup instead of opening another direct-entry dialog.
        """
        if action == "reload_entry_editor":
            self.action_requested.emit("reload_manual_editor", {**payload, "mode": "direct"})
        else:
            self.action_requested.emit(action, payload)

    def _reload(self) -> None:
        """
        Reload only after protecting unsaved input in the selected method.
        """
        if self._busy:
            return
        if self.modes.currentIndex() == 1:
            self.journal._reload()
            return
        if self.instruction.has_unsaved_changes():
            answer = QMessageBox.question(
                self, "Reload instruction", "Discard this instruction draft and reload its saved version?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        self.action_requested.emit("reload_manual_editor", {
            "manual_entry_id": self.context["manual_entry_id"],
            "mode": "instruction", "reload_instruction": True,
        })
