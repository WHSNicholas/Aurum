# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 GUI - Manual Editor                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Compose a written instruction or explicit journal entry in one revision-bound dialog.

Both methods share their date and bank evidence while retaining their own content drafts. Saving submits
only the selected method: instructions wait for processing, while validated journal lines post immediately.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget, QTabBar,
    QToolButton, QVBoxLayout,
)

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.gui.controls import DateField, confirm_discard
from aurum.gui.editor import JournalEditor
from aurum.gui.icons import icon
from aurum.gui.manual import InstructionForm, SourcePicker
from aurum.gui.theme import gui_colour


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
        self._pending_sources = None
        self.setObjectName("manualEntryEditor")
        self.setWindowTitle("Manual Entry")
        self.setModal(True)
        self.setMinimumSize(820, 580)
        self.resize(940, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        self.heading = QLabel("Manual Entry")
        self.heading.setObjectName("workspacePageTitle")
        layout.addWidget(self.heading)
        self.modes = QTabBar()
        self.modes.setObjectName("manualEntryModes")
        self.modes.setExpanding(False)
        self.modes.setDrawBase(False)
        self.modes.addTab("Instruction")
        self.modes.addTab("Journal entry")
        self.modes.setAccessibleName("Manual entry method")
        layout.addWidget(self.modes, 0, Qt.AlignmentFlag.AlignLeft)
        metadata = QHBoxLayout()
        metadata.setSpacing(10)
        initial_date = context.get("entry", {}).get("date") or ""
        if context.get("mode") != "direct" and not context.get("protected_trade"):
            initial_date = (context.get("manual_record") or {}).get("date") or initial_date
        self.date = DateField(str(initial_date))
        self.date.setAccessibleName("Accounting date")
        self.date.setPlaceholderText("YYYY-MM-DD or DD-MM-YYYY")
        self.date.setMaximumWidth(190)
        metadata.addWidget(self.date)
        self.source_button = QPushButton("Bank transactions")
        self.source_button.setIcon(icon("attach_evidence", size=16))
        self.source_button.setAccessibleName("Choose linked bank transactions")
        self.source_button.clicked.connect(self._choose_sources)
        metadata.addWidget(self.source_button)
        metadata.addStretch()
        layout.addLayout(metadata)
        self._sources = list(context.get("source_ids", []))
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.instruction = InstructionForm(
            context.get("manual_record"), context.get("snapshot", {}), self, show_metadata=False,
        )
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
        actions.addStretch()
        self.reload_button = QToolButton()
        self.reload_button.setObjectName("viewerIconAction")
        self.reload_button.setIcon(icon("refresh", size=20))
        self.reload_button.setIconSize(QSize(20, 20))
        self.reload_button.setFixedSize(36, 36)
        self.reload_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.reload_button.clicked.connect(self._reload)
        self.reload_button.hide()
        actions.addWidget(self.reload_button)
        self.cancel_button = QToolButton()
        self.cancel_button.setObjectName("viewerIconAction")
        self.cancel_button.setIcon(icon("close", size=20))
        self.cancel_button.setIconSize(QSize(20, 20))
        self.cancel_button.setFixedSize(36, 36)
        self.cancel_button.setToolTip("Cancel")
        self.cancel_button.setAccessibleName("Cancel manual entry")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QToolButton()
        self.save_button.setObjectName("viewerIconAction")
        self.save_button.setIconSize(QSize(20, 20))
        self.save_button.setFixedSize(36, 36)
        self.save_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.save_button.clicked.connect(self.save_changes)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.instruction.changed.connect(self._update_actions)
        self.journal.changed.connect(self._update_actions)
        self.journal.action_requested.connect(self._journal_action)
        self._sync_metadata()
        self.instruction._baseline = self.instruction.values()
        self.journal._baseline = self.journal._values()
        self.date.textChanged.connect(self._metadata_changed)
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
        if not busy:
            self._pending_sources = None
        self.instruction.set_busy(busy)
        self.journal.set_busy(busy)
        self.date.setEnabled(not busy and not self.journal.protected_trade)
        self.source_button.setEnabled(
            not busy and not self.context.get("journal_entry_id") and not self.journal.protected_trade
        )
        self.modes.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self._update_actions()

    def show_error(self, message: str) -> None:
        """
        Keep the submitted draft visible after failed validation, stale input or cancellation.
        """
        if message:
            self._pending_sources = None
        if self.modes.currentIndex() == 0:
            self.instruction.show_error(message)
        else:
            self.journal.show_error(message)
        self._update_actions()

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
        pending_sources = self._pending_sources
        self._pending_sources = None
        self.context = deepcopy(context)
        if context.get("reload_instruction"):
            previous = self.instruction_scroll.takeWidget()
            self.instruction = InstructionForm(
                context.get("manual_record"), context.get("snapshot", {}), self, show_metadata=False,
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
        if pending_sources is not None:
            effective_sources = list(context.get("source_ids", []))
            self._sources = [value for value in pending_sources if value in effective_sources]
            self._sources += [value for value in effective_sources if value not in self._sources]
        if self.journal.protected_trade:
            self.date.setText(str(self.journal.entry.get("date") or ""))
        self._sync_metadata()
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
        try:
            if parse_input_date(self.date.text()) is None:
                raise ValueError("Enter a date.")
        except ValueError as error:
            self.show_error(str(error))
            self.date.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._sync_metadata()
        if self.modes.currentIndex() == 1:
            self.journal.save_changes()
            return
        try:
            payload = self.instruction.payload()
        except ValueError as error:
            self.show_error(str(error))
            return
        self.show_error("")
        self.action_requested.emit("save_manual", payload)

    def reject(self) -> None:
        """
        Retain unfinished input unless the person explicitly discards both drafts.
        """
        if self._busy:
            return
        if not self._saved and self.has_unsaved_changes() and not confirm_discard(self):
            return
        super().reject()

    def _change_mode(self, index: int) -> None:
        """
        Show the chosen method while retaining all values in the inactive form.
        """
        self.stack.setCurrentIndex(index)
        self._update_actions()

    def _update_actions(self) -> None:
        """
        Explain the selected save's effect and keep validation with its own draft.
        """
        if not hasattr(self, "save_button"):
            return
        instruction = self.modes.currentIndex() == 0
        selected = self.instruction if instruction else self.journal
        reload_action = "Reload instruction" if instruction else "Reload journal entry"
        self.reload_button.setToolTip(reload_action)
        self.reload_button.setAccessibleName(reload_action)
        self.reload_button.setAccessibleDescription("Reload this method after confirming any unsaved edits.")
        self.reload_button.setVisible(bool(selected.error.text()))
        self.reload_button.setEnabled(not self._busy)
        action = "Save instruction" if instruction else "Post journal entry"
        if self.save_button.accessibleName() != action:
            symbol = "save" if instruction else "post"
            self.save_button.setIcon(icon(symbol, gui_colour(self, "icon", "#355653"), size=20))
        self.save_button.setToolTip(action)
        self.save_button.setAccessibleName(action)
        self.save_button.setAccessibleDescription(
            "Save this instruction for processing in Review." if instruction else
            "Post this balanced journal entry immediately."
        )
        written = bool(self.instruction.instruction.toPlainText().strip())
        enabled = (
            self.instruction.has_unsaved_changes() and written
            if instruction else
            self.journal.has_unsaved_changes() or self.journal.save_button.isEnabled()
        )
        self.save_button.setEnabled(not self._busy and enabled)
        message = self.context.get("direct_unavailable", "")
        self.notice.setText(message)
        self.notice.setVisible(bool(message))
        self.date.setEnabled(not self._busy and not self.journal.protected_trade)
        self.source_button.setEnabled(
            not self._busy and not self.context.get("journal_entry_id") and not self.journal.protected_trade
        )

    def _metadata_changed(self) -> None:
        """
        Apply a date edit to both drafts without changing either saved revision.
        """
        self._sync_metadata()
        self._update_actions()

    def _sync_metadata(self) -> None:
        """
        Keep both payload builders on the shared date and the successfully loaded bank selection.
        """
        self.instruction.date.setText(self.date.text())
        self.journal.date.setText(self.date.text())
        self.instruction._sources = list(self._sources)
        self.instruction._update_sources_tooltip()
        self.source_button.setToolTip(self.instruction.source_button.toolTip())
        self.source_button.setAccessibleDescription(self.instruction.source_button.accessibleDescription())
        if self.context.get("journal_entry_id"):
            self.source_button.setToolTip(
                self.source_button.toolTip()
                + "\nExisting journal entries retain their complete bank transaction links."
            )

    def _choose_sources(self) -> None:
        """
        Rebuild bank-linked postings only after selection and any draft discard are acknowledged.
        """
        if self._busy or self.context.get("journal_entry_id"):
            return
        dialog = SourcePicker(self.context.get("transactions", []), self._sources, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.selected_sources()
        selected = [value for value in self._sources if value in chosen]
        selected += [value for value in chosen if value not in selected]
        if selected == self._sources:
            return
        if self.journal.has_posting_changes() and not confirm_discard(self):
            return
        self._pending_sources = selected
        self.action_requested.emit("reload_manual_editor", {
            "manual_entry_id": self.context.get("manual_entry_id"),
            "source_ids": selected, "date": self.date.text(),
            "replace_unposted_sources": True,
            "manual_context_revision": self.instruction.record.get("context_revision", ""),
            "mode": "direct" if self.modes.currentIndex() else "instruction",
        })

    def _journal_action(self, action: str, payload: dict) -> None:
        """
        Keep evidence reloads inside this popup instead of opening another direct-entry dialog.
        """
        self._update_actions()
        if action == "reload_entry_editor":
            self.action_requested.emit("reload_manual_editor", {
                **payload, "mode": "direct", "date": self.date.text(), "source_ids": list(self._sources),
                "replace_unposted_sources": bool(self.journal.context.get("replace_unposted_sources")),
            })
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
        if self.instruction.has_unsaved_changes() and not confirm_discard(self):
            return
        self.action_requested.emit("reload_manual_editor", {
            "manual_entry_id": self.context["manual_entry_id"],
            "mode": "instruction", "reload_instruction": True,
            "date": self.date.text(), "source_ids": list(self._sources),
            "replace_unposted_sources": bool(self.journal.context.get("replace_unposted_sources")),
            "reload_saved": True,
        })
