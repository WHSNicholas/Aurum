# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                GUI - Bank Connections                                                #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Link Redbark accounts through an explicit checked draft, then save and import in one worker action.

Widgets never contact Redbark, read credentials or write accounting data themselves. The worker validates
every source before committing the connection; the dialog retains only opaque connection tokens.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date, timedelta

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.gui.controls import DateField, confirm_discard
from aurum.gui.icons import connection_button, icon
from aurum.gui.theme import gui_colour


# %% 1. Saved Connection -------------------------------------------------------------------------------------
class BankConnectionPanel(QFrame):
    """
    Keep the saved Redbark connection visible in one compact row beside account configuration.
    """

    action_requested = Signal(str, dict)
    cancel_requested = Signal()
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """
        Keep key entry and ordinary checks inline, opening account mapping only when explicitly needed.
        """
        super().__init__(parent)
        self._snapshot = {}
        self._busy = False
        self._dialog = None
        self._config_blocked = ""
        self._connection_token = None
        self._setup_dirty = False
        self._check_wants_setup = False
        self._error = ""
        self._error_action = ""
        self._pending_action = ""
        self.setObjectName("setupConnectionRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        identity = QWidget()
        identity.setObjectName("connectionIdentity")
        identity.setFixedWidth(128)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(2)
        title = _label("Redbark", "setupConnectionTitle")
        title.setToolTip("Redbark API Connection")
        identity_layout.addWidget(title)
        self.status = _label("", "connectionStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity_layout.addWidget(self.status)
        row.addWidget(identity)
        self.key = QLineEdit()
        self.key.setObjectName("connectionControl")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setFixedHeight(34)
        self.key.setMinimumWidth(200)
        self.key.setMaximumWidth(300)
        self.key.setAccessibleName("Redbark API key")
        self.key.setToolTip("Leave empty to check the saved key, or enter a new API key.")
        self.key.textEdited.connect(self._key_changed)
        self.key.returnPressed.connect(self._check)
        row.addWidget(self.key, 1)
        row.addStretch()
        self.check = connection_button("Redbark")
        self.check.clicked.connect(self._check)
        row.addWidget(self.check)
        self.configure = _icon_button("Set up Redbark accounts", "settings")
        self.configure.clicked.connect(self.open_dialog)
        row.addWidget(self.configure)
        layout.addLayout(row)
        self.feedback = _label("", "setupError")
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.set_snapshot({})

    def set_snapshot(self, snapshot: dict) -> None:
        """
        Read saved status without changing the original revision of an already open connection dialog.
        """
        self._snapshot = dict(snapshot.get("bank_feed", snapshot))
        configured = bool(self._snapshot.get("configured"))
        status, detail = _connection_status(self._snapshot)
        if self._error:
            status, detail = "Needs attention", self._error
        self.status.setText(status)
        self.status.setToolTip(detail)
        self.status.setAccessibleDescription(detail)
        self.key.setPlaceholderText("Saved API key" if configured else "API key")
        label = "Set up Redbark accounts" if configured else "Connect Redbark"
        self.configure.setAccessibleName(label)
        self.configure.setToolTip(label)
        self.set_busy(self._busy)
        self.changed.emit()

    def set_busy(self, busy: bool) -> None:
        """
        Prevent competing connection actions while preserving the open dialog's submitted draft.
        """
        self._busy = busy
        enabled = not busy and not self._config_blocked
        self.key.setEnabled(enabled)
        self.check.setEnabled(enabled)
        self.configure.setEnabled(enabled)
        for button, label in ((self.check, "Check Redbark connection"),
                              (self.configure, "Set up Redbark accounts")):
            button.setToolTip(self._config_blocked or label)
        if self._dialog is not None:
            self._dialog.set_busy(busy)

    def set_config_blocked(self, reason: str) -> None:
        """
        Keep connection setup behind saved, available account definitions without discarding key drafts.
        """
        changed = reason != self._config_blocked
        self._config_blocked = reason
        if reason and changed:
            self._release_session()
            if self._dialog is not None:
                self._dialog._connection_token = None
                self._dialog._show_error(reason)
        self.set_busy(self._busy)

    def open_dialog(self) -> None:
        """
        Reuse the open mapping draft or explicitly check access before opening account setup.
        """
        if self._busy or self._config_blocked:
            return
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()
            self._dialog.activateWindow()
        else:
            self._check(open_setup=True)

    def has_unsaved_changes(self) -> bool:
        """
        Identify a typed replacement or uncommitted mapping independently of Setup's account drafts.
        """
        return bool(self.key.text().strip() or self._setup_dirty or (
            self._dialog is not None and self._dialog.has_unsaved_changes()
        ))

    def discard_changes(self) -> None:
        """
        Clear an explicitly abandoned key and release only its checked worker session.
        """
        if self._busy:
            return
        if self._dialog is not None:
            self._dialog.discard_changes()
            self._dialog.reject()
        self.key.clear()
        self._setup_dirty = False
        self._release_session()
        self._show_error("")
        self.changed.emit()

    def guard_close(self) -> bool:
        """
        Protect connection drafts before navigation, reload or workspace close.
        """
        if self._busy:
            return not (self.has_unsaved_changes() or self._dialog is not None or self._pending_action)
        if self.has_unsaved_changes() and not confirm_discard(self):
            return False
        self.discard_changes()
        return True

    def deliver_result(self, action: str, result: dict) -> None:
        """
        Apply only acknowledged connection saves and forward discovery to the open dialog.
        """
        data = result.get("data", result)
        if action == self._pending_action:
            self._pending_action = ""
        if action == "redbark_check":
            if self._config_blocked:
                if data.get("connection_token"):
                    self.action_requested.emit("redbark_release_session", {
                        "connection_token": data["connection_token"],
                    })
                self._show_error(self._config_blocked, action)
                return
            if self._check_wants_setup:
                if not data.get("connection_token"):
                    self._show_error("The connection check did not finish. Try again.", action)
                    return
                self._connection_token = data["connection_token"]
                self._show_error("")
                self._dialog = BankConnectionDialog(
                    self._snapshot, data, self.window(), dirty=self._setup_dirty,
                )
                self._dialog.action_requested.connect(self._request_action)
                self._dialog.cancel_requested.connect(self.cancel_requested)
                self._dialog.changed.connect(self.changed)
                self._dialog.finished.connect(self._close_dialog)
                self._dialog.set_busy(self._busy)
                self._dialog.show()
                self._dialog.raise_()
                self._dialog.activateWindow()
            elif data.get("checked"):
                self._snapshot.pop("connection_error", None)
                self._show_error("")
            else:
                self._show_error("The connection check did not finish. Try again.", action)
        elif action in {"redbark_disconnect", "redbark_enable"}:
            expected = action == "redbark_enable"
            if data.get("configured") is not expected:
                self.deliver_error(action, "The connection change was not saved. Try again.")
                return
            self.key.clear()
            self._setup_dirty = False
            self._show_error("")
            self.set_snapshot(data)
            if self._dialog is not None:
                self._dialog.deliver_result(action, result)
            else:
                self._release_session()
        elif action == "redbark_sync" and self._error_action == action:
            self._show_error("")
        self.changed.emit()

    def deliver_error(self, action: str, message: str) -> None:
        """
        Keep failed drafts available and retain an actionable error when no dialog is open.
        """
        if action == self._pending_action:
            self._pending_action = ""
        if self._dialog is not None:
            self._dialog.deliver_error(action, message)
        else:
            self._show_error(message, action)

    def _check(self, _checked: bool = False, *, open_setup: bool = False) -> None:
        """
        Check a saved connection inline; first or replacement credentials continue to account setup.
        """
        if self._busy or self._config_blocked:
            return
        supplied = self.key.text().strip()
        if not supplied and not self._snapshot.get("configured"):
            self._show_error("Enter your Redbark API key first.")
            self.key.setFocus()
            return
        self._release_session()
        self._setup_dirty = bool(supplied) or not self._snapshot.get("configured")
        self._check_wants_setup = bool(open_setup or self._setup_dirty)
        self._show_error("")
        self._request_action("redbark_check", {
            "api_key": supplied or None, "setup": self._check_wants_setup,
        })

    def _key_changed(self, _value: str) -> None:
        """
        Invalidate checked access when the person edits the masked replacement key.
        """
        self._release_session()
        self._setup_dirty = False
        self._show_error("")
        self.changed.emit()

    def _request_action(self, action: str, payload: dict) -> None:
        """
        Track this row's submitted request separately from unrelated workspace activity.
        """
        self._pending_action = action
        self.action_requested.emit(action, payload)

    def _release_session(self) -> None:
        """
        Ask the owning window to release this opaque token directly, without a desktop job or network call.
        """
        token, self._connection_token = self._connection_token, None
        if token:
            self.action_requested.emit("redbark_release_session", {"connection_token": token})

    def _show_error(self, message: str, action: str = "") -> None:
        """
        Retain actionable failures inline while saved-state refreshes leave the person's key draft intact.
        """
        self._error, self._error_action = message.strip(), action if message.strip() else ""
        self.feedback.setText(self._error)
        self.feedback.setVisible(bool(self._error))
        self.set_snapshot(self._snapshot)

    def _close_dialog(self, result: int) -> None:
        """
        Release checked access after a successful save or an explicitly discarded mapping draft.
        """
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog._connection_token = None
            dialog.deleteLater()
        self.key.clear()
        self._setup_dirty = False
        self._release_session()
        self._show_error("")
        self.changed.emit()


# %% 2. Connection Draft -------------------------------------------------------------------------------------
class BankConnectionDialog(QDialog):
    """
    Map a checked connection to saved accounts and handover dates before an explicit save and import.
    """

    action_requested = Signal(str, dict)
    cancel_requested = Signal()

    changed = Signal()

    def __init__(self, snapshot: dict, checked: dict, parent: QWidget | None = None, *, dirty=False) -> None:
        """
        Show only checked account links and handover dates, keeping credential entry in the parent row.
        """
        super().__init__(parent)
        self._snapshot = dict(snapshot)
        self._busy = False
        self._dirty = dirty
        self._connection_token = checked["connection_token"]
        self._rows = []
        self.setWindowTitle("Redbark accounts")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(640)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 20, 24, 20)
        box.setSpacing(16)
        self.feedback = _label("", "setupError")
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        box.addWidget(self.feedback)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.mapping_box = QWidget()
        self.mapping_layout = QGridLayout(self.mapping_box)
        self.mapping_layout.setContentsMargins(0, 0, 12, 0)
        self.mapping_layout.setHorizontalSpacing(14)
        self.mapping_layout.setVerticalSpacing(12)
        self.mapping_layout.setColumnStretch(0, 1)
        self.mapping_layout.setColumnStretch(1, 1)
        self.scroll.setWidget(self.mapping_box)
        self.scroll.hide()
        box.addWidget(self.scroll)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel = _icon_button("Cancel", "close")
        self.cancel.clicked.connect(self._cancel)
        actions.addWidget(self.cancel)
        self.enable = _icon_button("Save connection and import", "save", primary=True)
        self.enable.clicked.connect(self._enable)
        actions.addWidget(self.enable)
        box.addLayout(actions)
        self._populate_mappings(checked)
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        """
        Freeze the submitted inputs and expose cooperative stopping through the existing cancel icon.
        """
        self._busy = busy
        self.mapping_box.setEnabled(not busy and self._connection_token is not None)
        self.cancel.setEnabled(True)
        self.cancel.setIcon(icon("stop" if busy else "close", gui_colour(self, "icon"), size=18))
        self.cancel.setToolTip("Stop after the current safe step" if busy else "Cancel")
        self.cancel.setAccessibleName("Stop" if busy else "Cancel")
        self.enable.setEnabled(not busy and self._connection_token is not None and bool(self._rows))

    def deliver_result(self, action: str, result: dict) -> None:
        """
        Show checked mappings or close only after the worker acknowledges the saved connection state.
        """
        data = result.get("data", result)
        if action == "redbark_enable":
            if not data.get("configured"):
                self._show_error("The connection was not saved. Try again.")
                return
            self._dirty = False
            self.accept()
        elif action == "redbark_disconnect":
            if data.get("configured") is not False:
                self._show_error("The connection was not disconnected. Try again.")
                return
            self._dirty = False
            self.accept()
        self.set_busy(self._busy)

    def deliver_error(self, action: str, message: str) -> None:
        """
        Retain explicit account choices after failure and let the person retry or check a replacement key.
        """
        self._show_error(message)
        self.set_busy(self._busy)

    def reject(self) -> None:
        """
        Protect an in-flight request or edited connection draft from accidental dismissal.
        """
        if self._busy:
            return
        if self._dirty and not confirm_discard(self):
            return
        super().reject()

    def has_unsaved_changes(self) -> bool:
        """
        Report uncommitted credentials or account-mapping changes to the parent navigation guard.
        """
        return self._dirty

    def discard_changes(self) -> None:
        """
        Mark a confirmed discard so the parent can close without asking a second time.
        """
        self._dirty = False

    def _cancel(self) -> None:
        """
        Stop between safe worker steps without closing or losing the current connection draft.
        """
        if self._busy:
            self.cancel.setEnabled(False)
            self.cancel_requested.emit()
        else:
            self.reject()

    def _invalidate(self, *args) -> None:
        """
        Mark the current connection choices as a local draft without starting any background work.
        """
        self._dirty = True
        self.set_busy(self._busy)
        self.changed.emit()

    def _populate_mappings(self, data: dict) -> None:
        """
        Show exact remote identities, local account choices and their permanent import boundaries.
        """
        _clear_layout(self.mapping_layout)
        self._rows.clear()
        locals_by_id = {str(item["id"]): item for item in data.get("aurum_accounts", [])}
        saved = {item["external_account_id"]: item for item in data.get(
            "mappings", self._snapshot.get("mappings", []),
        )}
        for column, text in enumerate(("Redbark account", "Aurum account", "Import from")):
            heading = _label(text, "muted")
            if column == 2:
                heading.setToolTip("PDFs cover earlier dates; Redbark owns this date onward.")
            self.mapping_layout.addWidget(heading, 0, column)
        for row, account in enumerate(data.get("accounts", []), start=1):
            institution = account.get("institution") or {}
            name = str(account.get("name") or "Bank account")
            number = str(account.get("account_number") or "")
            source = _label(f"{name}\n{institution.get('name', '')} {number}".strip())
            source.setWordWrap(True)
            source.setToolTip(f"{name} · {number}")
            combo = QComboBox()
            combo.setMinimumWidth(170)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.setAccessibleName(f"Aurum account for {name} {number}")
            combo.addItem("Do not link", None)
            for local_id, local in locals_by_id.items():
                combo.addItem(local["name"], local_id)
            previous = saved.get(account["id"], {})
            previous_id = str(previous.get("account_id", ""))
            if previous_id and combo.findData(previous_id) < 0:
                combo.addItem(previous.get("name", previous_id) + " (unavailable)", previous_id)
            combo.setCurrentIndex(max(0, combo.findData(previous_id)))
            if previous_id:
                for index in range(1, combo.count()):
                    if combo.itemData(index) != previous_id:
                        combo.model().item(index).setEnabled(False)
                combo.setToolTip("A saved link can be disabled, but cannot be reassigned.")
            handover = DateField(previous.get("handover_date") or data.get("suggested_handover") or "")
            handover.setFixedWidth(150)
            handover.setAccessibleName(f"Import from date for {name} {number}")
            handover.setToolTip("PDFs before this date; Redbark from this date onward.")
            handover.calendar_button.setToolTip("Choose import start date")
            handover.calendar_button.setAccessibleName("Choose import start date")
            handover.setReadOnly(bool(previous.get("handover_date")))
            detail = {"account": account, "combo": combo, "handover": handover,
                      "locals": locals_by_id, "suggested": data.get("suggested_handover", ""),
                      "saved_handover": previous.get("handover_date")}
            self._rows.append(detail)
            combo.currentIndexChanged.connect(lambda _index, item=detail: self._mapping_changed(item))
            handover.textChanged.connect(self._invalidate)
            self.mapping_layout.addWidget(source, row, 0)
            self.mapping_layout.addWidget(combo, row, 1)
            self.mapping_layout.addWidget(handover, row, 2)
        self.scroll.setVisible(bool(self._rows))
        if not self._rows:
            self._show_error("No ANZ Plus accounts are available. Check your Redbark connection.")
        self._fit_contents()

    def _mapping_changed(self, item: dict) -> None:
        """
        Suggest the first feed-owned day while retaining any already established handover.
        """
        local = item["locals"].get(item["combo"].currentData(), {})
        last = local.get("last_pdf_date")
        fixed = item["saved_handover"] or local.get("handover_date")
        try:
            suggested = fixed or (
                (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last else item["suggested"]
            )
        except (TypeError, ValueError):
            suggested = item["suggested"]
        item["handover"].setReadOnly(bool(fixed))
        item["handover"].setText(suggested)
        self._invalidate()

    def _enable(self) -> None:
        """
        Submit explicit choices once; the worker validates all downloaded sources before any save.
        """
        if self._busy or self._connection_token is None:
            return
        mappings = []
        used = set()
        for item in self._rows:
            account_id = item["combo"].currentData()
            if account_id is None:
                continue
            if account_id in used:
                self._show_error("Each Aurum account can be linked to only one Redbark account.")
                return
            used.add(account_id)
            try:
                handover = parse_input_date(item["handover"].text().strip()).isoformat()
            except ValueError:
                self._show_error("Enter each import date as YYYY-MM-DD or DD-MM-YYYY.")
                item["handover"].setFocus()
                return
            account = item["account"]
            mappings.append({
                "external_account_id": account["id"], "account_id": account_id,
                "handover_date": handover, "name": str(account.get("name") or "Bank account"),
                "masked_number": str(account.get("account_number") or ""),
            })
        if not mappings:
            self._show_error("Choose at least one Aurum account to link.")
            return
        self._show_error("")
        self.action_requested.emit("redbark_enable", {
            "connection_token": self._connection_token, "mappings": mappings,
        })

    def _show_error(self, message: str) -> None:
        """
        Reserve visible copy for a failure or required input, then fit the dialog to its actual content.
        """
        self.feedback.setText(message)
        self.feedback.setVisible(bool(message))
        self._fit_contents()

    def _fit_contents(self) -> None:
        """
        Keep short connections at their natural height and scroll only unusually long account lists.
        """
        self.mapping_layout.invalidate()
        self.mapping_layout.activate()
        self.scroll.setFixedHeight(min(340, self.mapping_layout.sizeHint().height() + 8))
        self.layout().invalidate()
        self.layout().activate()
        self.resize(700, self.sizeHint().height())


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _connection_status(snapshot: dict) -> tuple[str, str]:
    """
    Show connection health independently of review-queue counts.

    Import summaries share the legacy error field with failures. Only the exact summary supported by that
    account's structured pending conflicts is treated as review feedback; other errors remain actionable.
    """
    conflicts = snapshot.get("conflicts", ())
    counts = {}
    for item in conflicts:
        identifier = item.get("external_account_id")
        counts[identifier] = counts.get(identifier, 0) + 1
    errors = []
    for mapping in snapshot.get("mappings", ()):
        error = str(mapping.get("error") or "").strip()
        count = counts.get(mapping.get("external_account_id"), 0)
        if error and not (count and error == f"{count} import conflict(s) need Review."):
            errors.append(error)
    connection_error = str(snapshot.get("connection_error") or "").strip()
    if connection_error:
        errors.append(connection_error)
    details = list(dict.fromkeys(errors))
    status = "Needs attention" if errors else (
        "Connected" if snapshot.get("configured") else "Not connected"
    )
    return status, "\n".join(details)


def _icon_button(label: str, symbol: str, *, primary: bool = False) -> QToolButton:
    """
    Create a compact named action using the same local icon system as the rest of Setup.
    """
    button = QToolButton()
    button.setObjectName("setupIconAction")
    button.setFixedSize(32, 32)
    button.setIcon(icon(symbol, "#ffffff" if primary else "#355653", size=18))
    button.setIconSize(QSize(18, 18))
    button.setToolTip(label)
    button.setAccessibleName(label)
    button.setProperty("primary", primary)
    return button


def _label(text: str, name: str = "") -> QLabel:
    """
    Keep external account names and errors as literal text rather than interpreted markup.
    """
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        label.setObjectName(name)
    return label


def _clear_layout(layout) -> None:
    """
    Dispose of old discovered-account widgets before showing checked replacement results.
    """
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
