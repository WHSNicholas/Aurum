#----------------------------------------------------------------------------------------------------------------------#
#                                                        Aurum                                                         #
#                                             aurum - gui - forms.py                                                   #
#----------------------------------------------------------------------------------------------------------------------#
"""
Build ordinary configuration forms from Aurum's validated field definitions.

Nested settings use labelled fields and ordered collections rather than a YAML editor. Optional controls
stay off until selected, so inherited report colours and other defaults keep their existing behaviour.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from datetime import date
from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListView, QListWidget, QMessageBox, QPushButton, QScrollArea,
    QSplitter, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.icons import icon

MISSING = object()
LABELS = {
    "account_type": "Account type", "bank_details": "Bank details", "bsb": "BSB",
    "account_number": "Account number", "colour_scheme": "Colour theme",
    "category_keys": "Savings pots (display order)", "residual_category": "Catchall savings category",
    "monthly_overrides": "Monthly budget overrides", "account_investments": "Whole-account investments",
    "investment_symbols": "Investment display symbols", "price_symbol": "Market data symbol",
    "on_primary": "Text on main headings", "on_table_header": "Text on table headings and totals",
    "on_highlight": "Text on highlighted cards", "highlight_text": "Highlighted card label",
    "remaining": "Remaining amount (nonnegative)", "budget_income": "Negative remaining: income",
    "budget_expenses": "Negative remaining: expenses", "budget_savings": "Negative remaining: savings",
    "income_track": "Income progress background", "income_fill": "Income progress fill",
    "expenses_track": "Expense progress background", "expenses_fill": "Expense progress fill",
    "savings_track": "Savings progress background", "savings_fill": "Savings progress fill",
    "group": "Account group highlight", "table_header": "Table headings and total rows",
    "light_group": "Light mode group highlight", "dark_group": "Dark mode group highlight",
    "primary": "Main heading bands", "highlight": "Highlighted summary card",
    "bar_text_dark": "Dark chart value text", "bar_text_light": "Light chart value text",
    "output_directory": "Save reports in",
}


# %% 1. Structured Fields ------------------------------------------------------------------------------------
class ConfigurationForm(QWidget):
    """
    Present a configuration model as editable controls, keeping identities out of the form.
    """

    changed = Signal()

    def __init__(self, schema: dict, value: Any, choices: dict, parent: QWidget | None = None) -> None:
        """
        Build a single root editor using the model's local JSON-schema definitions.
        """
        super().__init__(parent)
        self.node = FieldEditor(schema, schema.get("$defs", {}), value, (), choices)
        self.node.changed.connect(self.changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.node)

    def value(self) -> Any:
        """
        Return form values for full model validation by the configuration service.
        """
        return self.node.value()

    def view_state(self) -> dict:
        """
        Retain the current selection and expanded settings independently of configuration values.
        """
        return self.node.view_state()

    def restore_view_state(self, state: dict) -> None:
        """
        Restore navigation after rebuilding saved settings without marking the form as edited.
        """
        self.node.restore_view_state(state)


class FieldEditor(QWidget):
    """
    Edit one scalar, fixed group or ordered collection, recursively using labelled native controls.
    """

    changed = Signal()

    def __init__(
        self, schema: dict, definitions: dict, value: Any, path: tuple[str, ...], choices: dict,
        parent: QWidget | None = None,
    ) -> None:
        """
        Resolve field references and build an editor that retains absent values and exact numeric text.
        """
        super().__init__(parent)
        self.schema = _resolve(schema, definitions)
        self.definitions, self.path, self.choices = definitions, path, choices
        self.original = deepcopy(value) if value is not MISSING else MISSING
        self.children = {}
        self.enabled_fields = {}
        self.disclosures = {}
        self.records = []
        self.active_index = -1
        self.control = None
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(0, 0, 0, 0)
        self.layout_box.setSpacing(10)
        kind = self.schema.get("type", "string")
        self.kind = kind
        if kind == "object" and "properties" in self.schema:
            self._build_group(value)
        elif kind in {"object", "array"}:
            self._build_collection(value)
        else:
            self._build_scalar(value)

    def value(self) -> Any:
        """
        Read current controls without changing saved configuration or coercing money through floats.
        """
        if self.kind == "object" and "properties" in self.schema:
            # Keep identities and unknown external fields so saving cannot silently discard existing data.
            result = {
                key: deepcopy(value) for key, value in self.original.items() if key not in self.children
            } if isinstance(self.original, dict) else {}
            for key, child in self.children.items():
                toggle = self.enabled_fields.get(key)
                if toggle is not None and not toggle.isChecked():
                    continue
                value = child.value()
                if key == "key" and not value:
                    continue
                result[key] = value
            return result
        if self.kind in {"object", "array"}:
            self._store_active()
            if self.kind == "object":
                return {key: deepcopy(value) for key, value in self.records}
            return [deepcopy(value) for _, value in self.records]
        if isinstance(self.control, QCheckBox):
            return self.control.isChecked()
        if isinstance(self.control, QComboBox):
            return self.control.currentData()
        return self.control.text()

    def _build_group(self, value: Any) -> None:
        """
        Build labelled fields, with explicit toggles for optional values and expandable nested groups.
        """
        value = value if isinstance(value, dict) else {}
        properties = self.schema["properties"]
        required = set(self.schema.get("required", []))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.layout_box.addLayout(form)
        keys = list(properties)
        if self.schema.get("title") in {"Account", "Security"}:
            keys = ["name", *(key for key in keys if key not in {"name", "key"}), "key"]
        for key in keys:
            if key == "id":
                continue
            raw_schema = properties[key]
            schema = _resolve(raw_schema, self.definitions)
            current = value.get(key, MISSING)
            child_value = current
            if current is MISSING or current is None:
                child_value = schema.get("default", MISSING)
            child = FieldEditor(schema, self.definitions, child_value, (*self.path, key), self.choices)
            self.children[key] = child
            child.changed.connect(self.changed)
            title = _label(key)
            optional = (key not in required or _nullable(raw_schema)) and key != "output_directory"
            if optional:
                toggle = QCheckBox(title)
                toggle.setToolTip("Enable to set a value. Leave off to use the application's default.")
                toggle.setChecked(current is not MISSING and current is not None)
                child.setEnabled(toggle.isChecked())
                toggle.toggled.connect(child.setEnabled)
                toggle.toggled.connect(self.changed)
                self.enabled_fields[key] = toggle
                label = toggle
            else:
                label = QLabel(title)
                label.setWordWrap(True)
                label.setMinimumWidth(min(180, max(90, label.fontMetrics().horizontalAdvance(title) + 8)))
                label.setMaximumWidth(210)
            if child.kind in {"object", "array"}:
                box = QFrame()
                box.setObjectName("configGroup")
                box_layout = QVBoxLayout(box)
                box_layout.setContentsMargins(14, 10, 14, 12)
                box_layout.setSpacing(12)
                heading = QHBoxLayout()
                heading.addWidget(label)
                heading.addStretch()
                expand = QToolButton()
                self.disclosures[key] = expand
                expand.setObjectName("configDisclosure")
                expand.setCheckable(True)
                expand.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                expand.setAccessibleName(f"Show {title.lower()}")
                expand.toggled.connect(
                    lambda checked, button=expand, editor=child:
                    self._expand_group(button, editor, checked)
                )
                heading.addWidget(expand)
                box_layout.addLayout(heading)
                box_layout.addWidget(child)
                expanded = key == "bank_details" and current is not MISSING and current is not None
                expand.setChecked(expanded)
                self._expand_group(expand, child, expanded)
                if optional:
                    toggle.toggled.connect(
                        lambda checked, button=expand: button.setChecked(True) if checked else None
                    )
                form.addRow(box)
            else:
                form.addRow(label, child)

    def _expand_group(self, button: QToolButton, child: QWidget, expanded: bool) -> None:
        """
        Keep a nested setting's disclosure arrow and visible fields in agreement.
        """
        child.setVisible(expanded)
        button.setText("Collapse" if expanded else "Expand")
        button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _build_scalar(self, value: Any) -> None:
        """
        Choose a checkbox, labelled choice, colour field or plain exact-text input for a scalar.
        """
        self.layout_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        key = self.path[-1] if self.path else ""
        options = self.schema.get("enum")
        references = _references(self.path, self.choices)
        if self.kind == "boolean":
            self.control = QCheckBox("Enabled")
            self.control.setChecked(value is True)
            self.control.toggled.connect(self.changed)
        elif options or references is not None:
            self.control = QComboBox()
            self.control.setObjectName("configSelect")
            self.control.setView(QListView(self.control))
            arrow = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()
            self.control.setStyleSheet(
                'QComboBox { padding-right: 28px; } '
                f'QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }} '
                'QComboBox:disabled { background: #edf2ef; color: #9baaa6; border-color: #dde5e2; }'
            )
            self.control.addItem("Choose…", "")
            items = references or {option: str(option).replace("_", " ").title() for option in options or []}
            for identifier, label in items.items():
                self.control.addItem(label, identifier)
            if value is not MISSING and value is not None:
                index = self.control.findData(value)
                if index < 0:
                    self.control.addItem(str(value) + " (not currently configured)", value)
                    index = self.control.count() - 1
                self.control.setCurrentIndex(index)
            self.control.currentIndexChanged.connect(self.changed)
        else:
            self.control = QLineEdit()
            if value is not MISSING and value is not None:
                self.control.setText(value.isoformat() if isinstance(value, date) else str(value))
            if key == "key":
                self.control.setReadOnly(True)
                self.control.setPlaceholderText("Generated from the name when saved")
                self.control.setToolTip("Stable internal key. Change the display name above instead.")
            if "const" in self.schema:
                self.control.setText(str(self.schema["const"]))
                self.control.setReadOnly(True)
            if self.schema.get("format") == "date":
                self.control.setPlaceholderText("YYYY-MM-DD or DD-MM-YYYY")
            self.control.textChanged.connect(self.changed)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        controls.addWidget(self.control, 1)
        self.layout_box.addLayout(controls)
        if key == "output_directory":
            self.control.setPlaceholderText("data/reports")
            button = QPushButton("Choose folder…")
            button.clicked.connect(self._pick_directory)
            controls.addWidget(button)
            note = QLabel("All four PDFs are saved in a YYYY-MM subfolder here.")
            note.setObjectName("muted")
            note.setWordWrap(True)
            self.layout_box.addWidget(note)
        pattern = self.schema.get("pattern", "")
        if "#[" in pattern or key in {"page", "surface", "divider"}:
            button = QPushButton("Choose colour…")
            button.clicked.connect(self._pick_colour)
            controls.addWidget(button)
        description = self.schema.get("description")
        if description:
            self.control.setToolTip(description)

    def _pick_directory(self) -> None:
        """
        Choose a PDF root, keeping workspace-local folders portable in the saved configuration.
        """
        workspace = Path(self.choices.get("workspace", Path.cwd())).resolve()
        value = Path(self.control.text() or "data/reports").expanduser()
        current = value if value.is_absolute() else workspace / value
        selected = QFileDialog.getExistingDirectory(self, "Save reports in", str(current))
        if selected:
            path = Path(selected).resolve()
            try:
                path = path.relative_to(workspace)
            except ValueError:
                pass
            self.control.setText(str(path))

    def _pick_colour(self) -> None:
        """
        Store selected RGB or RGBA channels in the format accepted by Aurum's report models.
        """
        if not isinstance(self.control, QLineEdit):
            return
        text = self.control.text()
        colour = QColor(text[:7])
        if len(text) == 9:
            try:
                colour.setAlpha(int(text[7:9], 16))
            except ValueError:
                colour = QColor("#ffffff")
        options = QColorDialog.ColorDialogOption.ShowAlphaChannel if "{2}" in self.schema.get(
            "pattern", ""
        ) else QColorDialog.ColorDialogOption(0)
        selected = QColorDialog.getColor(colour, self, "Choose report colour", options)
        if selected.isValid():
            hex_value = selected.name()
            if options and selected.alpha() != 255:
                hex_value += f"{selected.alpha():02x}"
            self.control.setText(hex_value)

    def _build_collection(self, value: Any) -> None:
        """
        Show ordered items beside one detail editor, avoiding huge repeated forms for catalogues and themes.
        """
        if self.kind == "object":
            self.records = list(deepcopy(value).items()) if isinstance(value, dict) else []
            patterns = self.schema.get("patternProperties", {})
            self.item_schema = next(iter(patterns.values()), self.schema.get(
                "additionalProperties", {"type": "string"},
            ))
        else:
            self.records = [
                ("", deepcopy(item)) for item in value
            ] if isinstance(value, (list, tuple)) else []
            self.item_schema = self.schema.get("items", {"type": "string"})
        if not isinstance(self.item_schema, dict):
            self.item_schema = {"type": "string"}
        self.collection_title, self.collection_noun = self._collection_labels()
        stacked = len(self.path) >= 3
        listing = QWidget()
        if not stacked:
            listing.setMinimumWidth(220)
            listing.setMaximumWidth(310)
        list_layout = QVBoxLayout(listing)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(10)
        heading = QHBoxLayout()
        self.list_heading = QLabel(self.collection_title)
        self.list_heading.setObjectName("configCollectionTitle")
        self.list_heading.setWordWrap(True)
        heading.addWidget(self.list_heading, 1)
        self.add_button = QPushButton("Add " + self.collection_noun)
        self.add_button.clicked.connect(self._add)
        heading.addWidget(self.add_button)
        list_layout.addLayout(heading)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("configList")
        self.list_widget.setAccessibleName(self.collection_title)
        self.list_widget.setMinimumHeight(90 if stacked else 180)
        if stacked:
            self.list_widget.setMaximumHeight(150)
        self.list_widget.currentRowChanged.connect(self._select)
        list_layout.addWidget(self.list_widget, 1)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self._remove)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        self.move_buttons = {}
        for offset, label, symbol in (
            (-1, "Move selected item up", "up"),
            (1, "Move selected item down", "down"),
        ):
            button = QToolButton()
            button.setObjectName("setupIconAction")
            button.setFixedSize(30, 30)
            button.setIcon(icon(symbol, size=14))
            button.setIconSize(QSize(14, 14))
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(lambda checked=False, direction=offset: self._move(direction))
            actions.addWidget(button)
            self.move_buttons[offset] = button
        list_layout.addLayout(actions)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0 if stacked else 16, 0, 0, 0)
        detail_layout.setSpacing(14)
        self.detail_heading = QLabel()
        self.detail_heading.setObjectName("configDetailTitle")
        self.detail_heading.setWordWrap(True)
        self.detail_heading.setTextFormat(Qt.TextFormat.PlainText)
        detail_layout.addWidget(self.detail_heading)
        self.detail = QScrollArea()
        self.detail.setWidgetResizable(True)
        self.detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail.setMinimumHeight(100 if stacked else 230)
        detail_layout.addWidget(self.detail, 1)
        splitter = QSplitter(Qt.Orientation.Vertical if stacked else Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(listing)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 160] if stacked else [270, 560])
        self.layout_box.addWidget(splitter)
        self._refresh_labels()
        if self.records:
            self.list_widget.setCurrentRow(0)
        else:
            self._select(-1)

    def _collection_labels(self) -> tuple[str, str]:
        """
        Name collection actions after the definitions being edited instead of their container type.
        """
        name = self.path[-1] if self.path else self.choices.get("configuration_kind", "")
        labels = {
            "accounts": ("Accounts", "account"), "securities": ("Securities", "security"),
            "themes": ("Themes", "theme"), "palette": ("Palette", "colour"),
            "asset_groups": ("Asset groups", "group"), "categories": ("Categories", "category"),
            "category_keys": ("Savings pots", "pot"),
            "monthly_overrides": ("Monthly overrides", "month"),
            "account_investments": ("Investment accounts", "account"),
            "investment_symbols": ("Investment symbols", "symbol"),
        }
        if "asset_groups" in self.path and name != "asset_groups":
            return "Accounts", "account"
        return labels.get(name, ("Items", "item"))

    def _record_label(self, key: str, value: Any, index: int) -> str:
        """
        Give lists and detail headings the same readable label while retaining stored keys unchanged.
        """
        label = value.get("name") or value.get("key") if isinstance(value, dict) else str(value)
        if key and key in self.choices.get("accounts", {}):
            label = self.choices["accounts"][key]
        elif key:
            label = str(label or key) if isinstance(value, dict) else key
        elif isinstance(value, str):
            references = _references((*self.path, "#"), self.choices)
            label = references.get(value, value) if references is not None else value
        return str(label or f"New {self.collection_noun} {index + 1}")

    def _update_collection_controls(self) -> None:
        """
        Enable removal and ordering only when the current selection supports the operation.
        """
        index = self.list_widget.currentRow()
        selected = 0 <= index < len(self.records)
        self.remove_button.setEnabled(selected)
        for offset, button in self.move_buttons.items():
            button.setEnabled(selected and 0 <= index + offset < len(self.records))

    def view_state(self) -> dict:
        """
        Capture disclosure states and the active item's stable identity, including nested selections.
        """
        if self.kind == "object" and "properties" in self.schema:
            return {
                "expanded": {key: button.isChecked() for key, button in self.disclosures.items()},
                "children": {key: child.view_state() for key, child in self.children.items()},
            }
        if self.kind not in {"object", "array"}:
            return {}
        state = {
            "index": self.active_index,
            "scroll": self.detail.verticalScrollBar().value(),
            "list_scroll": self.list_widget.verticalScrollBar().value(),
        }
        if 0 <= self.active_index < len(self.records):
            key, value = self.records[self.active_index]
            state["selection"] = {"mapping_key": key}
            if isinstance(value, dict):
                state["selection"].update({
                    field: str(value[field]) for field in ("id", "key", "name") if value.get(field)
                })
            else:
                state["selection"]["value"] = value
            editor = self.detail.widget()
            if isinstance(editor, FieldEditor):
                state["editor"] = editor.view_state()
        return state

    def restore_view_state(self, state: dict) -> None:
        """
        Restore selection by identity before falling back to position, without changing saved values.
        """
        if self.kind == "object" and "properties" in self.schema:
            for key, expanded in state.get("expanded", {}).items():
                if key in self.disclosures:
                    self.disclosures[key].setChecked(expanded)
            for key, child in self.children.items():
                child.restore_view_state(state.get("children", {}).get(key, {}))
            return
        if self.kind not in {"object", "array"} or not self.records:
            return
        selected = state.get("selection", {})
        index = None
        for field in ("mapping_key", "id", "key", "name", "value"):
            if field not in selected or selected[field] == "":
                continue
            for candidate, (key, value) in enumerate(self.records):
                actual = key if field == "mapping_key" else (
                    value if field == "value" else str(value.get(field, ""))
                    if isinstance(value, dict) else None
                )
                if actual == selected[field]:
                    index = candidate
                    break
            if index is not None:
                break
        if index is None:
            index = min(max(state.get("index", 0), 0), len(self.records) - 1)
        self.list_widget.setCurrentRow(index)
        editor = self.detail.widget()
        if isinstance(editor, FieldEditor):
            editor.restore_view_state(state.get("editor", {}))
        self.detail.verticalScrollBar().setValue(state.get("scroll", 0))
        self.list_widget.verticalScrollBar().setValue(state.get("list_scroll", 0))

    def _store_active(self) -> None:
        """
        Keep unsaved detail edits when switching between collection items.
        """
        if 0 <= self.active_index < len(self.records) and isinstance(self.detail.widget(), FieldEditor):
            key, _ = self.records[self.active_index]
            self.records[self.active_index] = (key, self.detail.widget().value())

    def _refresh_labels(self) -> None:
        """
        Refresh list labels without triggering selection callbacks halfway through the update.
        """
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for index, (key, value) in enumerate(self.records):
            self.list_widget.addItem(self._record_label(key, value, index))
        self.list_widget.blockSignals(False)
        self.list_heading.setText(f"{self.collection_title} · {len(self.records)}")
        self._update_collection_controls()

    def _select(self, index: int) -> None:
        """
        Save the previous item's local edits and display the selected item's controls.
        """
        self._store_active()
        self.active_index = index
        old = self.detail.takeWidget()
        if old is not None:
            old.deleteLater()
        if index < 0:
            self.detail_heading.setText("No selection")
            message = QLabel(
                f"Choose a {self.collection_noun} to edit its settings."
                if self.records else f"Add a {self.collection_noun} to get started."
            )
            message.setWordWrap(True)
            message.setObjectName("configEmpty")
            message.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self.detail.setWidget(message)
            self._update_collection_controls()
            return
        key, value = self.records[index]
        self.detail_heading.setText(self._record_label(key, value, index))
        editor = FieldEditor(
            self.item_schema, self.definitions, value, (*self.path, key or "#"), self.choices,
        )
        editor.changed.connect(self._detail_changed)
        self.detail.setWidget(editor)
        self._update_collection_controls()

    def _detail_changed(self) -> None:
        """
        Mark the complete configuration dirty and keep the current item's display name up to date.
        """
        self._store_active()
        if self.active_index >= 0:
            key, value = self.records[self.active_index]
            label = self._record_label(key, value, self.active_index)
            self.list_widget.item(self.active_index).setText(label)
            self.detail_heading.setText(label)
        self.changed.emit()

    def _add(self) -> None:
        """
        Add a named mapping item or an empty list item without exposing UUID entry.
        """
        self._store_active()
        key = ""
        schema = _resolve(self.item_schema, self.definitions)
        value = _empty_value(schema)
        if self.kind == "object":
            name = self.path[-1] if self.path else ""
            category_override = len(self.path) > 1 and self.path[-2] == "monthly_overrides"
            if name in {"account_investments", "investment_symbols"} or category_override:
                references = self.choices.get("categories" if category_override else "asset_accounts", {})
                available = [
                    (key, label) for key, label in references.items() if key not in dict(self.records)
                ]
                if not available:
                    QMessageBox.information(
                        self, "Nothing available", "Save a suitable account or budget category first.",
                    )
                    return
                labels = [f"{label} ({key})" for key, label in available]
                noun = "Category" if category_override else "Account"
                label, accepted = QInputDialog.getItem(self, "Choose " + noun.lower(), noun, labels, 0, False)
                if not accepted:
                    return
                key = available[labels.index(label)][0]
            else:
                label, accepted = QInputDialog.getText(
                    self, "Add setting", "Month (YYYY-MM)" if name == "monthly_overrides" else "Name",
                )
                if not accepted or not label.strip():
                    return
                label = label.strip()
                key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") if name in {
                    "categories", "themes",
                } else label
                if name == "categories":
                    value["name"] = label
            if not key or key in dict(self.records):
                QMessageBox.warning(self, "Choose another name", "This name is empty or already exists.")
                return
        self.active_index = -1
        self.records.append((key, value))
        self._refresh_labels()
        self.list_widget.setCurrentRow(len(self.records) - 1)
        self.changed.emit()

    def _remove(self) -> None:
        """
        Remove only the unsaved form item; storage validation protects referenced history on save.
        """
        index = self.list_widget.currentRow()
        if index < 0:
            return
        self.active_index = -1
        self.records.pop(index)
        self._refresh_labels()
        self.list_widget.setCurrentRow(min(index, len(self.records) - 1))
        if not self.records:
            self._select(-1)
        self.changed.emit()

    def _move(self, offset: int) -> None:
        """
        Reorder a definition or report row without changing its identity.
        """
        self._store_active()
        index = self.list_widget.currentRow()
        destination = index + offset
        if index < 0 or not 0 <= destination < len(self.records):
            return
        self.records[index], self.records[destination] = self.records[destination], self.records[index]
        self.active_index = -1
        self._refresh_labels()
        self.list_widget.setCurrentRow(destination)
        self.changed.emit()


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _label(key: str) -> str:
    """
    Turn model field names into short, consistent form labels.
    """
    return LABELS.get(key, key.replace("_", " ").capitalize())


def _resolve(schema: dict, definitions: dict) -> dict:
    """
    Resolve local model references and editable members of optional or numeric unions.
    """
    result = dict(schema)
    if "$ref" in result:
        referred = definitions[result.pop("$ref").rsplit("/", 1)[-1]]
        result = {**referred, **result}
    if "anyOf" in result:
        members = [member for member in result.pop("anyOf") if member.get("type") != "null"]
        chosen = next((member for member in members if member.get("type") == "string"), members[0])
        result = {**_resolve(chosen, definitions), **result}
    return result


def _nullable(schema: dict) -> bool:
    """
    Identify an explicit nullable field without changing its ordinary default semantics.
    """
    return any(member.get("type") == "null" for member in schema.get("anyOf", []))


def _references(path: tuple[str, ...], choices: dict) -> dict | None:
    """
    Show account and category names while saving their stable internal keys.
    """
    if not path:
        return None
    name = path[-1]
    if name in {"capital_account", "retained_earnings_account", "unrealised_gains_account"}:
        return choices.get("equity_accounts", choices.get("accounts", {}))
    if name == "residual_category" or (len(path) > 1 and path[-2] == "category_keys"):
        return choices.get("savings_categories", {})
    if len(path) > 1 and path[-2] == "accounts":
        return choices.get("accounts", {})
    if "asset_groups" in path and name == "#":
        return choices.get("asset_accounts", choices.get("accounts", {}))
    if name == "colour_scheme":
        return choices.get("themes", {})
    return None


def _empty_value(schema: dict) -> Any:
    """
    Start newly added fields with a sensible blank structure for the model to validate on save.
    """
    if "default" in schema and schema["default"] is not None:
        return deepcopy(schema["default"])
    kind = schema.get("type")
    if kind == "object":
        return {}
    if kind == "array":
        return []
    if kind == "boolean":
        return False
    return ""
