# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                GUI - Bank Connections                                                #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Guide a bank connection through explicit account mapping and an import preview before it is enabled.

Widgets emit requests to the workspace worker. They never contact Redbark, read credentials or write
accounting data themselves. Saved connection settings remain independent of account-definition drafts.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date, timedelta

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.icons import icon


# %% 1. Saved Connection -------------------------------------------------------------------------------------
class BankConnectionPanel(QFrame):
    """
    Show connection status and protect changes to automatic syncing within the Accounts setup section.
    """

    action_requested = Signal(str, dict)
    cancel_requested = Signal()
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """
        Keep the connection summary compact while the existing account editor retains the page.
        """
        super().__init__(parent)
        self._snapshot = {}
        self._latest_snapshot = {}
        self._busy = False
        self._dialog = None
        self.setObjectName("setupNotice")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(8)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        title = _label("Bank connection", "setupSectionTitle")
        heading.addWidget(title)
        heading.addStretch()
        self.configure = QPushButton("Connect Redbark")
        self.configure.setIcon(icon("login", size=18))
        self.configure.setIconSize(QSize(18, 18))
        self.configure.clicked.connect(self.open_dialog)
        heading.addWidget(self.configure)
        self.disconnect = QToolButton()
        self.disconnect.setObjectName("setupIconAction")
        self.disconnect.setIcon(icon("close", size=18))
        self.disconnect.setFixedSize(32, 32)
        self.disconnect.setToolTip("Disconnect Redbark from this workspace; keep imported history")
        self.disconnect.setAccessibleName("Disconnect Redbark")
        self.disconnect.clicked.connect(self._disconnect)
        heading.addWidget(self.disconnect)
        box.addLayout(heading)
        self.status = _label("Connect ANZ Plus to import transactions automatically.", "muted")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.auto_sync = QCheckBox("Sync when this workspace opens")
        self.auto_sync.setStyleSheet("QCheckBox { background: transparent; }")
        self.auto_sync.toggled.connect(self.changed)
        box.addWidget(self.auto_sync)
        self.set_snapshot({})

    def set_snapshot(self, snapshot: dict) -> None:
        """
        Refresh saved status while preserving a staged automatic-sync preference.
        """
        dirty = self.has_unsaved_changes()
        self._latest_snapshot = dict(snapshot.get("bank_feed", snapshot))
        if dirty:
            # Keep the draft's original revision so concurrent connection edits reject its later save.
            return
        self._snapshot = dict(self._latest_snapshot)
        configured = bool(self._snapshot.get("configured"))
        self.auto_sync.blockSignals(True)
        self.auto_sync.setChecked(bool(self._snapshot.get("auto_sync", True)))
        self.auto_sync.blockSignals(False)
        mappings = self._snapshot.get("mappings", [])
        count = len([item for item in mappings if item.get("enabled", True)])
        errors = [item.get("error") for item in mappings if item.get("error")]
        if configured:
            text = f"Redbark · {count} linked account{'s' if count != 1 else ''}"
            if errors:
                text += " · Check the connection on Statements"
            self.status.setText(text)
        else:
            self.status.setText("Connect ANZ Plus to import transactions automatically.")
        self.configure.setText("Manage connection" if configured else "Connect Redbark")
        self.auto_sync.setVisible(configured)
        self.disconnect.setVisible(configured)
        self.set_busy(self._busy)
        self.changed.emit()

    def set_busy(self, busy: bool) -> None:
        """
        Prevent competing connection actions while preserving the dialog's displayed progress.
        """
        self._busy = busy
        self.configure.setEnabled(not busy)
        self.disconnect.setEnabled(not busy)
        self.auto_sync.setEnabled(not busy)
        if self._dialog is not None:
            self._dialog.set_busy(busy)

    def has_unsaved_changes(self) -> bool:
        """
        Identify an automatic-sync preference waiting for the ordinary Setup save action.
        """
        return bool(self._snapshot.get("configured")) and (
            self.auto_sync.isChecked() != bool(self._snapshot.get("auto_sync"))
        )

    def discard_changes(self) -> None:
        """
        Restore the last acknowledged preference without writing anything.
        """
        self._snapshot = dict(self._latest_snapshot)
        self.auto_sync.blockSignals(True)
        self.auto_sync.setChecked(bool(self._snapshot.get("auto_sync", True)))
        self.auto_sync.blockSignals(False)
        self.set_snapshot(self._snapshot)

    def save_changes(self) -> bool:
        """
        Submit a changed preference and wait for the worker's explicit acknowledgement.
        """
        if not self.has_unsaved_changes():
            return True
        if not self._busy:
            self.action_requested.emit("redbark_settings", {
                "auto_sync": self.auto_sync.isChecked(), "revision": self._snapshot.get("revision"),
            })
        return False

    def open_dialog(self) -> None:
        """
        Open a protected connection draft without starting a network request.
        """
        if self._busy:
            return
        if self._dialog is None:
            draft = {**self._snapshot, "auto_sync": self.auto_sync.isChecked()}
            self._dialog = BankConnectionDialog(draft, self.window())
            self._dialog.action_requested.connect(self.action_requested)
            self._dialog.cancel_requested.connect(self.cancel_requested)
            self._dialog.finished.connect(self._close_dialog)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def deliver_result(self, action: str, result: dict) -> None:
        """
        Apply only acknowledged settings and forward discovery or preview results to the open dialog.
        """
        data = result.get("data", result)
        if action in {"redbark_settings", "redbark_disconnect", "redbark_enable"}:
            state = data.get("bank_feed", data)
            if "configured" in state:
                self._snapshot = dict(state)
            elif action == "redbark_disconnect":
                self._snapshot = {**self._snapshot, "configured": False, "auto_sync": False}
            elif action == "redbark_settings":
                self._snapshot["auto_sync"] = self.auto_sync.isChecked()
            self._latest_snapshot = dict(self._snapshot)
            self.discard_changes()
        if self._dialog is not None:
            self._dialog.deliver_result(action, result)
        self.changed.emit()

    def deliver_error(self, action: str, message: str) -> None:
        """
        Keep failed drafts in place and show a concise worker-supplied error.
        """
        if self._dialog is not None:
            self._dialog.deliver_error(action, message)
        else:
            self.status.setText(message)

    def _disconnect(self) -> None:
        """
        Confirm that disabling the connection retains every saved transaction and posting.
        """
        if self._busy:
            return
        answer = QMessageBox.question(
            self, "Disconnect Redbark?",
            "Stop syncing this workspace and remove its saved API key? Imported history stays in Aurum.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.action_requested.emit("redbark_disconnect", {"revision": self._snapshot.get("revision")})

    def _close_dialog(self, result: int) -> None:
        """
        Release the protected draft and its session tokens when the dialog finishes.
        """
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.key.clear()
            dialog.deleteLater()


# %% 2. Connection Draft -------------------------------------------------------------------------------------
class BankConnectionDialog(QDialog):
    """
    Stage credential checking, account mapping and a preview as three explicit worker actions.
    """

    action_requested = Signal(str, dict)
    cancel_requested = Signal()

    def __init__(self, snapshot: dict, parent: QWidget | None = None) -> None:
        """
        Build a modal draft using the workspace's normal controls and rounded selectors.
        """
        super().__init__(parent)
        self._snapshot = dict(snapshot)
        self._busy = False
        self._dirty = False
        self._connection_token = None
        self._preview_token = None
        self._rows = []
        self.setWindowTitle("Bank Connection")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(760, 620)
        self.setMinimumSize(640, 460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 20, 24, 20)
        box.setSpacing(16)
        header = QVBoxLayout()
        header.setSpacing(4)
        header.addWidget(_label("Connect Redbark", "setupSectionTitle"))
        subtitle = _label("Link ANZ Plus accounts, then preview the transactions to import.", "muted")
        subtitle.setWordWrap(True)
        header.addWidget(subtitle)
        box.addLayout(header)
        credentials = QHBoxLayout()
        credentials.setSpacing(10)
        label = _label("API key")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setAccessibleName("Redbark API key")
        self.key.setPlaceholderText("Use saved key, or enter a replacement" if snapshot.get("configured")
                                    else "Enter your Redbark API key")
        self.key.textEdited.connect(self._key_changed)
        label.setBuddy(self.key)
        credentials.addWidget(label)
        credentials.addWidget(self.key, 1)
        self.check = QPushButton("Check connection")
        self.check.setIcon(icon("login", size=18))
        self.check.clicked.connect(self._check)
        credentials.addWidget(self.check)
        box.addLayout(credentials)
        self.feedback = _label(
            "Your key is saved in macOS Keychain only when you enable the connection.", "muted",
        )
        self.feedback.setWordWrap(True)
        box.addWidget(self.feedback)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 12, 0)
        self.content_layout.setSpacing(18)
        self.mapping_box = QWidget()
        self.mapping_layout = QGridLayout(self.mapping_box)
        self.mapping_layout.setContentsMargins(0, 0, 0, 0)
        self.mapping_layout.setHorizontalSpacing(16)
        self.mapping_layout.setVerticalSpacing(12)
        self.mapping_layout.setColumnStretch(0, 1)
        self.mapping_layout.setColumnStretch(1, 1)
        self.mapping_box.hide()
        self.content_layout.addWidget(self.mapping_box)
        self.handover_note = _label(
            "PDFs cover dates before the handover. Redbark covers the handover date onward.", "muted",
        )
        self.handover_note.setWordWrap(True)
        self.handover_note.hide()
        self.content_layout.addWidget(self.handover_note)
        self.preview_box = QWidget()
        self.preview_layout = QVBoxLayout(self.preview_box)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_layout.setSpacing(8)
        self.preview_box.hide()
        self.content_layout.addWidget(self.preview_box)
        self.content_layout.addStretch()
        scroll.setWidget(content)
        box.addWidget(scroll, 1)
        self.auto_sync = QCheckBox("Sync automatically when this workspace opens")
        self.auto_sync.setChecked(bool(snapshot.get("auto_sync")) if snapshot.get("configured") else True)
        self.auto_sync.toggled.connect(self._invalidate)
        box.addWidget(self.auto_sync)
        actions = QHBoxLayout()
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self._cancel)
        actions.addWidget(self.cancel)
        actions.addStretch()
        self.preview = QPushButton("Preview import")
        self.preview.setIcon(icon("search", size=18))
        self.preview.clicked.connect(self._preview)
        actions.addWidget(self.preview)
        self.enable = QPushButton("Enable && import")
        self.enable.setProperty("primary", True)
        self.enable.setIcon(icon("import", "#ffffff", 18))
        self.enable.clicked.connect(self._enable)
        actions.addWidget(self.enable)
        box.addLayout(actions)
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        """
        Disable edits during a worker action without changing the submitted draft.
        """
        self._busy = busy
        self.key.setEnabled(not busy)
        self.check.setEnabled(not busy and (
            self._connection_token is None or bool(self.key.text()) or bool(self._snapshot.get("configured"))
        ))
        self.mapping_box.setEnabled(not busy)
        self.auto_sync.setEnabled(not busy)
        self.cancel.setEnabled(True)
        self.cancel.setText("Stop" if busy else "Cancel")
        self.preview.setEnabled(not busy and self._connection_token is not None and bool(self._rows))
        self.enable.setEnabled(not busy and self._preview_token is not None)

    def deliver_result(self, action: str, result: dict) -> None:
        """
        Advance only after a successful credential check, complete preview or saved import.
        """
        data = result.get("data", result)
        if action == "redbark_check":
            if not data.get("connection_token"):
                self.feedback.setText(result.get("message") or "The connection check did not finish.")
                return
            self.key.clear()
            self.key.setPlaceholderText("Connection checked · enter a replacement key to change it")
            self._connection_token = data.get("connection_token")
            self._preview_token = None
            self.feedback.setText(
                result.get("message") or "Connected. Choose the accounts and handover dates."
            )
            self._populate_mappings(data)
        elif action == "redbark_preview":
            if not data.get("preview_token"):
                self.feedback.setText(result.get("message") or "The import preview did not finish.")
                return
            self._preview_token = data.get("preview_token")
            self._show_preview(data)
            self.feedback.setText("Review these counts, then enable the connection to import.")
        elif action == "redbark_enable":
            if not data.get("configured"):
                self.feedback.setText(result.get("message") or "The connection was not saved.")
                return
            self._dirty = False
            self.key.clear()
            self.accept()
        self.set_busy(self._busy)

    def deliver_error(self, action: str, message: str) -> None:
        """
        Retain entered mappings after an unsuccessful action and leave retry controls available.
        """
        self.feedback.setText(message)
        if action == "redbark_preview":
            self._preview_token = None
        self.set_busy(self._busy)

    def reject(self) -> None:
        """
        Protect a submitted job or an unfinished connection draft from accidental dismissal.
        """
        if self._busy:
            return
        if self._dirty and QMessageBox.question(
            self, "Discard connection changes?", "Discard this connection draft? Nothing has been imported.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return
        self.key.clear()
        super().reject()

    def _cancel(self) -> None:
        """
        Offer cooperative stopping inside the modal while preserving the submitted draft.
        """
        if self._busy:
            self.cancel.setEnabled(False)
            self.feedback.setText("Stopping after the current safe step…")
            self.cancel_requested.emit()
        else:
            self.reject()

    def _key_changed(self, value: str) -> None:
        """
        Require another connection check when the credential draft changes.
        """
        self._connection_token = None
        self._invalidate()

    def _invalidate(self, *args) -> None:
        """
        Invalidate the previous preview whenever its inputs change.
        """
        self._dirty = True
        self._preview_token = None
        self.preview_box.hide()
        self.set_busy(self._busy)

    def _check(self) -> None:
        """
        Send the credential directly to the worker without retaining a second widget copy.
        """
        if not self.key.text().strip() and not self._snapshot.get("configured"):
            self.feedback.setText("Enter your Redbark API key first.")
            self.key.setFocus()
            return
        self._connection_token = None
        self._invalidate()
        self.feedback.setText("Checking Redbark…")
        self.action_requested.emit("redbark_check", {"api_key": self.key.text().strip() or None})

    def _populate_mappings(self, data: dict) -> None:
        """
        Display discovered accounts beside explicit Aurum account and handover choices.
        """
        _clear_layout(self.mapping_layout)
        self._rows.clear()
        locals_by_id = {str(item["id"]): item for item in data.get("aurum_accounts", [])}
        saved = {item["external_account_id"]: item for item in data.get(
            "mappings", self._snapshot.get("mappings", []),
        )}
        for column, text in enumerate(("Redbark account", "Aurum account", "Handover date")):
            self.mapping_layout.addWidget(_label(text, "muted"), 0, column)
        for row, account in enumerate(data.get("accounts", []), start=1):
            institution = account.get("institution") or {}
            name = str(account.get("name") or "Bank account")
            number = str(account.get("account_number") or "")
            source = _label(f"{name}\n{institution.get('name', '')} {number}".strip())
            source.setWordWrap(True)
            source.setToolTip(f"{name} · {number}")
            combo = QComboBox()
            combo.setMinimumWidth(200)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.setAccessibleName(f"Aurum account for {name} {number}")
            combo.addItem("Do not link", None)
            for local_id, local in locals_by_id.items():
                combo.addItem(local["name"], local_id)
            previous = saved.get(account["id"], {})
            combo.setCurrentIndex(max(0, combo.findData(str(previous.get("account_id", "")))))
            handover = QLineEdit(previous.get("handover_date") or data.get("suggested_handover") or "")
            handover.setPlaceholderText("YYYY-MM-DD")
            handover.setFixedWidth(126)
            handover.setAccessibleName(f"Handover date for {name} {number}")
            handover.setToolTip("PDFs before this date; Redbark from this date onward")
            detail = {"account": account, "combo": combo, "handover": handover,
                      "locals": locals_by_id, "suggested": data.get("suggested_handover", "")}
            self._rows.append(detail)
            combo.currentIndexChanged.connect(lambda index, item=detail: self._mapping_changed(item))
            handover.textEdited.connect(self._invalidate)
            self.mapping_layout.addWidget(source, row, 0)
            self.mapping_layout.addWidget(combo, row, 1)
            self.mapping_layout.addWidget(handover, row, 2)
        self.mapping_box.setVisible(bool(self._rows))
        self.handover_note.setVisible(bool(self._rows))
        if not self._rows:
            self.feedback.setText("No available ANZ Plus accounts were found. Check your Redbark connection.")

    def _mapping_changed(self, item: dict) -> None:
        """
        Suggest the next date after the selected account's imported PDF coverage.
        """
        local = item["locals"].get(item["combo"].currentData(), {})
        last = local.get("last_pdf_date")
        try:
            suggested = local.get("handover_date") or (
                (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last else item["suggested"]
            )
        except (TypeError, ValueError):
            suggested = item["suggested"]
        item["handover"].setText(suggested)
        self._invalidate()

    def _preview(self) -> None:
        """
        Validate explicit choices locally before asking the worker for a complete import preview.
        """
        mappings = []
        used = set()
        for item in self._rows:
            account_id = item["combo"].currentData()
            if account_id is None:
                continue
            if account_id in used:
                self.feedback.setText("Each Aurum account can be linked to only one Redbark account.")
                return
            used.add(account_id)
            try:
                handover = date.fromisoformat(item["handover"].text().strip()).isoformat()
            except ValueError:
                self.feedback.setText("Enter each handover date as YYYY-MM-DD.")
                item["handover"].setFocus()
                return
            account = item["account"]
            mappings.append({
                "external_account_id": account["id"], "account_id": account_id,
                "handover_date": handover, "name": str(account.get("name") or "Bank account"),
                "masked_number": str(account.get("account_number") or ""),
            })
        if not mappings:
            self.feedback.setText("Choose at least one Aurum account to link.")
            return
        self.feedback.setText("Preparing the import preview…")
        self.action_requested.emit("redbark_preview", {
            "connection_token": self._connection_token, "mappings": mappings,
            "auto_sync": self.auto_sync.isChecked(),
        })

    def _show_preview(self, data: dict) -> None:
        """
        Summarise complete account results without displaying private raw banking payloads.
        """
        _clear_layout(self.preview_layout)
        self.preview_layout.addWidget(_label("Import preview", "setupSectionTitle"))
        for item in data.get("accounts", []):
            new = item.get("new_count", item.get("new", 0))
            existing = item.get("existing_count", item.get("existing", 0))
            conflicts = item.get("conflict_count", item.get("conflicts", 0))
            name = item.get("name") or next((
                row["account"].get("name", "Bank account") for row in self._rows
                if row["account"]["id"] == item.get("external_account_id")
            ), "Bank account")
            label = _label(f"{name} · {new} new · {existing} already imported · {conflicts} need review")
            label.setWordWrap(True)
            self.preview_layout.addWidget(label)
        note = _label("Importing adds bank activity. Use Process separately to prepare the journal.", "muted")
        note.setWordWrap(True)
        self.preview_layout.addWidget(note)
        self.preview_box.show()

    def _enable(self) -> None:
        """
        Authorise exactly the inspected preview; the worker rechecks its saved revision.
        """
        if self._preview_token is not None:
            self.feedback.setText("Saving the connection and importing…")
            self.action_requested.emit("redbark_enable", {"preview_token": self._preview_token})


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _label(text: str, name: str = "") -> QLabel:
    """
    Keep external account names and messages as literal text rather than interpreted markup.
    """
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        label.setObjectName(name)
    return label


def _clear_layout(layout) -> None:
    """
    Dispose of old discovered-account and preview widgets before showing replacement results.
    """
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
