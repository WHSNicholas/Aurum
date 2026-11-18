# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Finance - Budget                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum projects its cash budget from complete, posted journal events involving assets configured as Cash.

Budget inflows and expenses are separate from income-statement accounting. Investments records cash-funded
investment contributions; Savings is the remaining cash surplus and may be negative. Internal cash transfers,
opening positions and movements outside the configured cash accounts do not create budget activity.

Historical labels and virtual-pot allocations are inert. Unknown cash counterparts remain explicit review
issues instead of being silently excluded. Targets retain exact monthly amounts and never roll over.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Annotated, Literal, Self, TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# Aurum imports
from aurum.finance.accounts import Account, AccountRegistry, AccountType
from aurum.finance.dates import reporting_month, working_month_start, working_month_end
from aurum.finance.journal import JournalEntry
from aurum.finance.savings import SavingsAllocation
from aurum.finance.transaction import Transaction

if TYPE_CHECKING:
    from aurum.finance.reports import ReportPeriod

# Default paths and amounts
DEFAULT_BUDGET_SETTINGS = Path(__file__).resolve().parents[2] / "config" / "budget.yaml"
Money = Annotated[Decimal, Field(decimal_places=2, allow_inf_nan=False)]
CATEGORY_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
UNALLOCATED_SAVINGS = "savings"


# %% 1. Budget Models ----------------------------------------------------------------------------------------
class BudgetTarget(BaseModel):
    """
    Set a fixed monthly dollar amount, or leave the target unfinished while configuring a plan.

    A complete saved plan requires an amount for every category. Reading unfinished historical settings
    remains possible so they can be corrected without interrupting ordinary journal work.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    amount: Money | None = Field(default=None, ge=0)


class BudgetCategory(BudgetTarget):
    """
    Name a budget row and identify the accounts that normally contribute to it.

    Income and expense accounts map cash-backed activity to these rows. Savings and Investments are fixed
    calculated categories; posting labels never change the projection.
    """

    name: str = Field(min_length=1)
    kind: Literal["income", "expense", "savings"]
    accounts: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        """
        Reject empty display names.
        """
        if not value.strip():
            raise ValueError("Budget category names must not be blank")
        return value

    @field_validator("accounts")
    @classmethod
    def _check_accounts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """
        Require nonblank, unique account keys.
        """
        if any(not key.strip() for key in value) or len(set(value)) != len(value):
            raise ValueError("Budget account keys must be nonblank and unique within a category")
        return value

class BudgetSettings(BaseModel):
    """
    Keep cash-budget account roles, category targets and monthly plans separate from journal accounting.

    Legacy savings pots are normalised on read. Cash and investment keys resolve to persistent account
    UUIDs; display names never determine a posted event's treatment.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    categories: Mapping[str, BudgetCategory] = Field(default_factory=dict)
    monthly_overrides: Mapping[str, Mapping[str, BudgetTarget]] = Field(default_factory=dict)
    monthly_plans_from: str | None = None
    cash_accounts: tuple[str, ...] = ()
    investment_accounts: tuple[str, ...] = ()
    liability_categories: Mapping[str, str] = Field(default_factory=dict)
    capital_account: str = "capital"
    unrealised_gains_account: str = "unrealised_capital_gains"

    @model_validator(mode="before")
    @classmethod
    def _read_legacy(cls, value):
        """
        Convert old category and pot settings without modifying the original document.
        """
        return normalise_budget_data(value) if isinstance(value, Mapping) else value

    @field_validator("monthly_plans_from")
    @classmethod
    def _check_planning_start(cls, value: str | None) -> str | None:
        """
        Retain the saved boundary between inherited and independent monthly targets.
        """
        if value is not None:
            _validate_month(value)
        return value

    @field_validator("cash_accounts", "investment_accounts")
    @classmethod
    def _check_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """
        Require unique, nonblank stable account keys for each role.
        """
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("Budget account roles require unique, nonblank account keys")
        return value

    @model_validator(mode="after")
    def _check_settings(self) -> Self:
        """
        Validate fixed rows, monthly references and role mappings before freezing the configuration.
        """
        for key in self.categories:
            if not CATEGORY_KEY.fullmatch(key) or key in {"exclude", "default"}:
                raise ValueError("Budget category keys use lowercase letters, digits and underscores")
        expected = {
            "savings": ("Savings", "savings"), "investments": ("Investments", "savings"),
            "gift_received": ("Gift received", "income"),
            "investment_return": ("Investment returns", "income"),
        }
        for key, (name, kind) in expected.items():
            category = self.categories.get(key)
            if category is None or (category.name, category.kind) != (name, kind):
                raise ValueError(f"Budget category {key!r} is a fixed system category")
        if {key for key, item in self.categories.items() if item.kind == "savings"} != {
            "savings", "investments",
        }:
            raise ValueError("Savings and Investments are the only savings categories")
        if set(self.cash_accounts) & set(self.investment_accounts):
            raise ValueError("An account cannot be both budget cash and an investment")
        for account, category in self.liability_categories.items():
            if not account.strip() or category not in self.categories:
                raise ValueError("Liability repayments require an existing expense category")
            if self.categories[category].kind != "expense":
                raise ValueError("Liability repayments require an expense category")
        overrides = {}
        for month, targets in self.monthly_overrides.items():
            _validate_month(month)
            if set(targets) - set(self.categories):
                raise ValueError("A monthly target refers to an unknown budget category")
            overrides[month] = MappingProxyType(dict(targets))
        object.__setattr__(self, "categories", MappingProxyType(dict(self.categories)))
        object.__setattr__(self, "monthly_overrides", MappingProxyType(overrides))
        object.__setattr__(self, "liability_categories", MappingProxyType(dict(self.liability_categories)))
        return self

    @property
    def savings(self) -> None:
        """
        Leave retired pot readers inactive while historical records remain readable.
        """
        return None

    @field_serializer("categories")
    def _serialise_categories(self, value: Mapping[str, BudgetCategory]) -> dict:
        """
        Export ordered report categories as ordinary configuration data.
        """
        return {key: category.model_dump(mode="json") for key, category in value.items()}

    @field_serializer("monthly_overrides")
    def _serialise_overrides(self, value: Mapping[str, Mapping[str, BudgetTarget]]) -> dict:
        """
        Export each month's explicitly configured targets.
        """
        return {
            month: {key: target.model_dump(mode="json") for key, target in targets.items()}
            for month, targets in value.items()
        }

    @field_serializer("liability_categories")
    def _serialise_liabilities(self, value: Mapping[str, str]) -> dict:
        """
        Export stable liability-to-expense category assignments.
        """
        return dict(value)


class BudgetIssue(BaseModel):
    """
    Identify a posted event whose cash movement needs a human accounting or configuration decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: UUID
    code: str
    message: str


class BudgetProjection(BaseModel):
    """
    Describe one complete event's deterministic budget result without changing its journal postings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: UUID
    rows: tuple["BudgetImpactRow", ...] = ()
    issues: tuple[BudgetIssue, ...] = ()
    reason: str
    income: Money = Decimal("0.00")
    expenses: Money = Decimal("0.00")
    investments: Money = Decimal("0.00")
    savings: Money = Decimal("0.00")

    @property
    def total_savings(self) -> Decimal:
        """
        Return budget inflows less cash expenses, including a negative result.
        """
        return _money(_cents(self.income) - _cents(self.expenses))


class BudgetRow(BaseModel):
    """
    Compare one category's signed actual amount with its optional monthly target.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    actual: Money
    target: Money | None = Field(default=None, ge=0)

    @property
    def remaining(self) -> Decimal | None:
        """
        Return target minus actual, or None when the user has not supplied a target.
        """
        return None if self.target is None else _money(_cents(self.target) - _cents(self.actual))

    @property
    def progress(self) -> Decimal | None:
        """
        Return uncapped percentage progress; a missing or zero target has no meaningful ratio.
        """
        if self.target is None or self.target == 0:
            return None
        return self.actual / self.target * 100


class BudgetReport(BaseModel):
    """
    Describe monthly income, expenses and savings, with every amount included exactly once in their totals.

    Total savings and surplus both equal inflows less expenses, including a negative result. Investments
    records cash-funded contributions; Savings holds the remainder. A section's target is blank if any
    displayed row lacks a target, so a partial plan cannot masquerade as a complete budget.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: date
    end_date: date
    period_label: str
    income: tuple[BudgetRow, ...]
    expenses: tuple[BudgetRow, ...]
    savings: tuple[BudgetRow, ...]
    total_income: Money
    total_expenses: Money
    total_savings: Money
    income_target: Money | None
    expense_target: Money | None
    savings_target: Money | None
    surplus: Money
    draft_reasons: tuple[str, ...] = ()
    issues: tuple[BudgetIssue, ...] = ()

    @model_validator(mode="after")
    def _check_totals(self) -> Self:
        """
        Require displayed rows, summary amounts and dates to agree.
        """
        for rows, actual, target in (
            (self.income, self.total_income, self.income_target),
            (self.expenses, self.total_expenses, self.expense_target),
            (self.savings, self.total_savings, self.savings_target),
        ):
            if _cents(actual) != sum(_cents(row.actual) for row in rows) or target != _target_total(rows):
                raise ValueError("Budget section totals do not match their rows")
        if (
            self.start_date > self.end_date
            or _cents(self.surplus) != _cents(self.total_income) - _cents(self.total_expenses)
            or _cents(self.total_savings) != _cents(self.surplus)
        ):
            raise ValueError("Budget dates or surplus are inconsistent")
        return self


class BudgetImpactRow(BaseModel):
    """
    Identify one signed change to a report category, including the calculated savings remainder.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    kind: Literal["income", "expense", "savings"]
    amount: Money
    residual: bool = False


class BudgetImpactMonth(BaseModel):
    """
    Compare a complete month's saved budget with its projected replacement accounting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    month: str
    period_label: str
    income_change: Money
    expense_change: Money
    savings_change: Money
    rows: tuple[BudgetImpactRow, ...]
    warnings: tuple[str, ...] = ()
    issues: tuple[BudgetIssue, ...] = ()


class BudgetImpact(BudgetImpactMonth):
    """
    Explain an event's budget contribution and any change from the accounting it replaces.

    Main amounts compare the complete month with and without the proposed event. Changes by month compare
    the saved snapshot with the proposed replacement, including an old month when the date changes.
    Warnings always describe the complete projected month, and identify any unresolved cash events.
    """

    summary: str
    tooltip: str
    excluded: bool
    cutoff: date
    changes_by_month: tuple[BudgetImpactMonth, ...] = ()


class BudgetImpactCalculator:
    """
    Compile one saved cash-budget snapshot for read-only draft previews and correction comparisons.
    """

    def __init__(self, context: Mapping) -> None:
        """
        Restore exact account roles and explicit opening provenance once per editor or snapshot.
        """
        self.accounts = AccountRegistry([Account.model_validate(item) for item in context["accounts"]])
        self.settings = BudgetSettings.model_validate(context["settings"])
        self.entries = tuple(JournalEntry.model_validate(item) for item in context.get("entries", ()))
        self.openings = {UUID(str(value)) for value in context.get("opening_entry_ids", ())}
        self.replacing = {UUID(str(value)) for value in context.get("replacing_entry_ids", ())}
        self.today = date.fromisoformat(str(context.get("today") or date.today()))
        self._by_id = {entry.id: entry for entry in self.entries}
        if len(self._by_id) != len(self.entries):
            raise ValueError("Budget preview event identities must be unique")
        self._months = {}
        for entry in self.entries:
            year, month = reporting_month(entry.date)
            self._months.setdefault(f"{year:04d}-{month:02d}", []).append(entry)
        self._reports = {}
        self._empty_reports = {}
        _validate_settings(self.settings, self.accounts)

    def calculate(self, entry: JournalEntry | Mapping) -> BudgetImpact:
        """
        Show this event's contribution and separate deltas from the saved months it replaces.
        """
        opening = False
        if isinstance(entry, Mapping) and "entry" in entry:
            opening = bool(entry.get("opening", False))
            entry = entry["entry"]
        candidate = JournalEntry.model_validate(entry)
        replaced = self.replacing | {candidate.id}
        previous = tuple(self._by_id[key] for key in replaced if key in self._by_id)
        openings = self.openings | ({candidate.id} if opening else set())
        period = _impact_period(candidate.date, self.today)
        unchanged = (
            len(previous) == 1 and previous[0] == candidate
            and (candidate.id in openings) == (candidate.id in self.openings)
        )
        after = self._saved_report(period) if unchanged else build_budget_report(
            (*self._remaining_month(period.month, replaced), candidate), self.accounts, self.settings, period,
            opening_entry_ids=openings,
        )
        if period.month not in self._empty_reports:
            self._empty_reports[period.month] = build_budget_report((), self.accounts, self.settings, period)
        candidate_report = build_budget_report(
            (candidate,), self.accounts, self.settings, period, opening_entry_ids=openings,
        )
        contribution = _impact_difference(
            self._empty_reports[period.month], candidate_report, self.settings, period.month,
            warnings=after.draft_reasons,
        )
        changes = []
        affected = {} if unchanged else {period.month: period}
        if not unchanged:
            for old in previous:
                old_period = _impact_period(old.date, self.today)
                affected[old_period.month] = old_period
        for month, changed_period in sorted(affected.items()):
            saved = self._saved_report(changed_period)
            changed = after if month == period.month else build_budget_report(
                self._remaining_month(month, replaced), self.accounts, self.settings, changed_period,
                opening_entry_ids=openings,
            )
            difference = _impact_difference(saved, changed, self.settings, month)
            if (
                difference.rows or saved.issues != changed.issues
                or saved.draft_reasons != changed.draft_reasons
            ):
                changes.append(difference)
        projection = _project_entry(candidate, self.accounts, self.settings, opening=candidate.id in openings)
        summary = _impact_summary(contribution, False)
        if candidate_report.issues:
            summary = "Needs review"
        if not contribution.rows and not candidate_report.issues:
            summary = projection.reason
        if candidate.date > period.cutoff:
            summary = "After reporting cutoff"
        tooltip = _impact_tooltip(contribution, period.cutoff, False, changes=changes)
        if not contribution.rows and not candidate_report.issues:
            tooltip += "\n" + projection.reason + "."
        if candidate.date > period.cutoff:
            tooltip += "\nThis accounting date is after the current reporting cutoff."
        return BudgetImpact(
            **contribution.model_dump(), summary=summary, tooltip=tooltip, excluded=False,
            cutoff=period.cutoff, changes_by_month=tuple(changes),
        )

    def transfer_pair(self, entry: JournalEntry | Mapping) -> None:
        """
        Retire the old From and To pot controls; cash transfers are neutral.
        """
        return None

    def _saved_report(self, period: "ReportPeriod") -> BudgetReport:
        """
        Cache each unchanged month while successive previews share the same saved snapshot.
        """
        if period.month not in self._reports:
            self._reports[period.month] = build_budget_report(
                self._months.get(period.month, ()), self.accounts, self.settings, period,
                opening_entry_ids=self.openings,
            )
        return self._reports[period.month]

    def _remaining_month(self, month: str, replaced: set[UUID]) -> tuple[JournalEntry, ...]:
        """
        Retain unaffected events before inserting or removing a correction.
        """
        return tuple(item for item in self._months.get(month, ()) if item.id not in replaced)


# %% 2. Budget Configuration ---------------------------------------------------------------------------------
def normalise_budget_data(
    data: Mapping, accounts: AccountRegistry | None = None, *,
    investment_account_keys: Iterable[str] = (), capital_account: str | None = None,
    unrealised_gains_account: str | None = None,
) -> dict:
    """
    Return canonical cash-budget settings while preserving prior targets and leaving source YAML untouched.

    Legacy investment targets become Investments. Other savings targets combine into Savings; an unknown
    component keeps the combined target unknown. Existing Capital-linked gift targets become Gift received.
    Investment withdrawal targets join Investment returns, retaining income-account links and row position.
    Missing roles are inferred only for legacy configuration: ANZ bank identities and supplied investment
    account keys. Explicit role lists are never expanded automatically.
    """
    result = dict(data)
    source = {
        key: item.model_dump(mode="json") if isinstance(item, BaseModel) else dict(item)
        for key, item in result.get("categories", {}).items()
    }
    capital = result.get("capital_account", capital_account or "capital")
    groups = {"savings": [], "investments": [], "gift_received": [], "investment_return": []}
    categories = {}
    for key, category in source.items():
        kind = category.get("kind")
        if key in {"investment_return", "investment_withdrawals"}:
            groups["investment_return"].append(key)
            categories.setdefault("investment_return", {})
        elif kind == "savings":
            name = str(category.get("name", "")).strip().casefold()
            target = "investments" if key in {"investment", "investments"} or name in {
                "investment", "investments",
            } else "savings"
            groups[target].append(key)
        elif kind == "income" and (
            key in {"gift_received", "gifts"} or capital in category.get("accounts", ())
        ):
            groups["gift_received"].append(key)
        else:
            categories[key] = category
    fixed = (
        ("gift_received", "Gift received", "income"),
        ("investment_return", "Investment returns", "income"),
        ("savings", "Savings", "savings"), ("investments", "Investments", "savings"),
    )
    for key, name, kind in fixed:
        categories[key] = {
            "name": name, "kind": kind,
            "accounts": list(dict.fromkeys(
                account for item in groups[key] for account in source[item].get("accounts", ())
            )) if key == "investment_return" else [],
            "amount": _combined_target(source[item].get("amount") for item in groups[key]),
        }
    overrides = {}
    boundary = result.get("monthly_plans_from")
    for month, supplied in result.get("monthly_overrides", {}).items():
        old = {
            key: item.model_dump(mode="json") if isinstance(item, BaseModel) else dict(item)
            for key, item in supplied.items()
        }
        updated = {key: value for key, value in old.items() if key in categories and key not in groups}
        independent = boundary is not None and month >= boundary
        for key, members in groups.items():
            if not any(member in old for member in members):
                continue
            updated[key] = {"amount": _combined_target(
                old[member].get("amount") if member in old else (
                    None if independent else source[member].get("amount")
                )
                for member in members
            )}
        overrides[month] = updated
    result["categories"] = categories
    result["monthly_overrides"] = overrides
    result.pop("savings", None)
    result.setdefault("capital_account", capital)
    result.setdefault("unrealised_gains_account", unrealised_gains_account or "unrealised_capital_gains")
    if "cash_accounts" not in result:
        result["cash_accounts"] = [
            key for key, account in accounts.accounts.items()
            if account.account_type == AccountType.ASSET and account.bank_details is not None and (
                key == "anz" or key.startswith("anz_")
                or (account.institution or "").strip().casefold() in {"anz", "anz plus", "anz bank"}
            )
        ] if accounts is not None else []
    if "investment_accounts" not in result:
        result["investment_accounts"] = list(dict.fromkeys(investment_account_keys))
    result.setdefault("liability_categories", {})
    return result


def budget_impact_context(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings,
    *, replacing_entry_ids: Iterable[UUID | str] = (), opening_entry_ids: Iterable[UUID | str] = (),
    today: date | None = None, allocations: Iterable[SavingsAllocation] = (),
    transactions: Iterable[Transaction] = (),
    transfer_evidence: Mapping[str, Mapping[str, object]] | None = None,
) -> dict:
    """
    Supply the editor with a serialisable, read-only cash-budget snapshot and explicit opening identities.

    Retired allocation and transfer arguments remain accepted for old callers but never affect projection.
    """
    return {
        "accounts": [account.model_dump(mode="json") for account in accounts.accounts.values()],
        "settings": settings.model_dump(mode="json"),
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "opening_entry_ids": [str(identifier) for identifier in opening_entry_ids],
        "replacing_entry_ids": [str(identifier) for identifier in replacing_entry_ids],
        "today": (today or date.today()).isoformat(),
    }


def load_budget_settings(
    path: Path | str, accounts: AccountRegistry, *,
    investment_account_keys: Iterable[str] | None = None, capital_account: str | None = None,
    unrealised_gains_account: str | None = None,
) -> BudgetSettings:
    """
    Read and normalise private budget settings without rewriting configuration or historical accounting.
    """
    path = Path(path)
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Budget configuration must use a .yaml or .yml extension")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        fields = yaml.load(path.read_text(encoding="utf-8"))
    except YAMLError:
        raise ValueError("Invalid budget YAML; check syntax and duplicate keys") from None
    if not isinstance(fields, dict):
        raise ValueError("Budget configuration must contain a mapping of settings")
    if investment_account_keys is None or capital_account is None or unrealised_gains_account is None:
        reports_path = path.with_name("reports.yaml")
        if reports_path.exists():
            try:
                reports = yaml.load(reports_path.read_text(encoding="utf-8"))
            except YAMLError:
                raise ValueError("Invalid reports YAML while resolving budget account roles") from None
            if not isinstance(reports, dict):
                raise ValueError("Report configuration must contain a mapping of settings")
        else:
            reports = {}
        if investment_account_keys is None:
            investment_account_keys = tuple(reports.get("account_investments", {}))
        if capital_account is None:
            capital_account = reports.get("capital_account", "capital")
        if unrealised_gains_account is None:
            unrealised_gains_account = reports.get("unrealised_gains_account", "unrealised_capital_gains")
    settings = BudgetSettings.model_validate(normalise_budget_data(
        fields, accounts, investment_account_keys=investment_account_keys or (),
        capital_account=capital_account,
        unrealised_gains_account=unrealised_gains_account,
    ))
    _validate_settings(settings, accounts)
    return settings


def budget_targets(settings: BudgetSettings, month: str | None = None) -> dict[str, BudgetTarget]:
    """
    Resolve monthly targets without changing legacy plans or filling an independent month automatically.

    A missing policy preserves default inheritance. Earlier months retain that same behavior after the
    policy starts; later missing targets remain unset. None selects the stored legacy defaults themselves.
    """
    if month is not None:
        _validate_month(month)
    overrides = settings.monthly_overrides.get(month, {})
    independent = month is not None and settings.monthly_plans_from is not None and (
        month >= settings.monthly_plans_from
    )
    return {
        key: overrides.get(key, BudgetTarget() if independent else category)
        for key, category in settings.categories.items()
    }


def budget_plan_totals(settings: BudgetSettings, month: str | None = None) -> dict[str, Decimal | None]:
    """
    Sum fixed targets for one plan, retaining an unknown total when any target is unfinished.

    Targets follow the saved monthly policy. Zero is intentional and differs from an empty amount.
    """
    targets = budget_targets(settings, month)
    totals = {kind: 0 for kind in ("income", "expense", "savings")}
    incomplete = set()
    for key, category in settings.categories.items():
        amount = targets[key].amount
        if amount is None:
            incomplete.add(category.kind)
        else:
            totals[category.kind] += _cents(amount)
    return {kind: None if kind in incomplete else _money(amount) for kind, amount in totals.items()}


def validate_budget_plan(settings: BudgetSettings, month: str | None = None) -> None:
    """
    Require complete, balanced dollar targets before saving a budget or generating reports.

    This deliberately does not run when loading settings for journal work. Existing incomplete plans
    remain editable, and account or report settings can still be saved while the budget is being repaired.
    A supplied month validates that effective plan only. Without a month, saving checks all stored plans,
    plus the default plan while legacy inheritance is the active policy. Unvisited future months need not
    be entered, but reporting an independent month requires its complete targets.
    """
    months = (month,) if month is not None else (
        (() if settings.monthly_plans_from is not None else (None,)) + tuple(settings.monthly_overrides)
    )
    for selected in months:
        label = selected or "Default budget"
        targets = budget_targets(settings, selected)
        missing = [
            category.name for key, category in settings.categories.items()
            if targets[key].amount is None
        ]
        if missing:
            raise ValueError(
                f"{label}: enter a dollar target for " + ", ".join(missing) + ". Use 0 if needed."
            )
        totals = budget_plan_totals(settings, selected)
        difference = _cents(totals["income"]) - _cents(totals["expense"]) - _cents(totals["savings"])
        if difference:
            raise ValueError(
                f"{label}: planned income must equal expenses plus savings. "
                f"Difference: ${_money(difference):.2f}. Adjust the targets before saving or reporting."
            )


def validate_budget_entry(
    entry: JournalEntry, accounts: AccountRegistry, settings: BudgetSettings,
) -> None:
    """
    Validate account references while leaving retired posting labels inert.
    """
    by_id = {account.id for account in accounts.accounts.values()}
    if any(line.account_id not in by_id for line in entry.lines):
        raise ValueError("A budget event refers to an unknown account")


def validate_budget_labels(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings | None = None,
    previous: Iterable[JournalEntry] = (), *, transactions: Iterable[Transaction] = (),
    transfer_evidence: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    """
    Retain compatibility for commit callers without applying historical budget labels or exclusions.
    """
    by_id = {account.id for account in accounts.accounts.values()}
    for entry in entries:
        if any(line.account_id not in by_id for line in entry.lines):
            raise ValueError("A budget event refers to an unknown account")


def budget_catalogue(settings: BudgetSettings) -> tuple[dict, ...]:
    """
    Describe report categories, not labels that AI or editors can attach to journal postings.
    """
    return tuple(
        {"key": key, "name": category.name, "kind": category.kind, "accounts": category.accounts}
        for key, category in settings.categories.items()
    )


def budget_policy(settings: BudgetSettings | None) -> dict:
    """
    Supply shared cash-budget roles and deterministic conventions to accounting interpretation.
    """
    return {
        "model": "cashflow_v1",
        "cash_accounts": list(settings.cash_accounts) if settings else [],
        "investment_accounts": list(settings.investment_accounts) if settings else [],
        "liability_categories": dict(settings.liability_categories) if settings else {},
        "capital_account": settings.capital_account if settings else None,
        "unrealised_gains_account": settings.unrealised_gains_account if settings else None,
        "fixed_categories": ["gift_received", "investment_return", "savings", "investments"],
        "rule": (
            "Budget results are derived from complete journal events involving configured cash accounts. "
            "Do not output budget labels, exclusions or virtual-pot allocations. Cash transfers are neutral. "
            "Cash paid to configured investment assets is Investments; a withdrawal contributes its entire "
            "cash receipt once to investment_return alongside its mapped cash investment income, without "
            "negative Investments or duplicate gains. "
            "Cash income and expenses use their accounting categories; cash refunds reduce expenses. "
            "A cash-funded investment and separately posted fees split into Investments and expenses. "
            "A liability payment, including interest in the same event, uses one configured repayment "
            "expense category. New loan proceeds and ambiguous mixed events need Review. Capital receipts "
            "with bank evidence are gift_received. Crediting Cash and debiting only configured Capital "
            "is a gift given, excluded from budget; explicit opening positions are neutral. Events without "
            "configured cash movement, including employer contributions and unrealised gains, are outside "
            "the budget. Savings is inflows less expenses less Investments and may be negative."
        ),
    }


def savings_transfer_policy(settings: BudgetSettings | None, **kwargs) -> dict:
    """
    Keep older request builders on the new shared policy until their obsolete argument is removed.
    """
    return budget_policy(settings)


def savings_transfer_pair(*args, **kwargs) -> None:
    """
    Retire contextual virtual-pot choices; cash-account transfers have no budget allocation.
    """
    return None


def validate_savings_transfer_assignments(*args, **kwargs) -> None:
    """
    Leave historical savings labels inert instead of treating them as active assignments.
    """
    return None


def budget_revision(settings: BudgetSettings | None) -> str | None:
    """
    Fingerprint the projection roles and category mappings independently of editable monthly targets.
    """
    if settings is None:
        return None
    context = budget_policy(settings)
    context["categories"] = budget_catalogue(settings)
    return sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# %% 3. Cash Budget Projection -------------------------------------------------------------------------------
def project_budget_entry(
    entry: JournalEntry, accounts: AccountRegistry, settings: BudgetSettings, *, opening: bool = False,
) -> BudgetProjection:
    """
    Classify one balanced event using its cash boundary and complete counterpart postings.

    Accounting accounts and source facts remain unchanged. Unknown or incompatible flows return an issue
    with no invented amounts; callers must display the resulting report as provisional.
    """
    entry = JournalEntry.model_validate(entry)
    _validate_settings(settings, accounts)
    return _project_entry(entry, accounts, settings, opening=opening)


def build_budget_report(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings,
    period: "ReportPeriod", *, draft_reasons: Iterable[str] = (),
    opening_entry_ids: Iterable[UUID | str] = (), allocations: Iterable[SavingsAllocation] = (),
) -> BudgetReport:
    """
    Project posted cash events into monthly targets, two fixed savings rows and explicit review issues.

    Legacy posting labels and saved virtual allocations have no effect. Total savings is budget inflows
    less expenses; Savings is that total less Investments. The remainder is allowed to be negative.
    """
    _validate_settings(settings, accounts)
    openings = {UUID(str(identifier)) for identifier in opening_entry_ids}
    amounts = {key: 0 for key in settings.categories}
    additional = {}
    issues = []
    for entry in entries:
        if not period.start_date <= entry.date <= period.cutoff:
            continue
        projection = _project_entry(entry, accounts, settings, opening=entry.id in openings)
        issues.extend(projection.issues)
        for row in projection.rows:
            amounts[row.key] = amounts.get(row.key, 0) + _cents(row.amount)
            if row.key not in settings.categories:
                additional[row.key] = row
    targets = budget_targets(settings, period.month)
    rows = {"income": [], "expense": [], "savings": []}
    for key, category in settings.categories.items():
        rows[category.kind].append(BudgetRow(
            key=key, name=category.name, actual=_money(amounts[key]), target=targets[key].amount,
        ))
    for key, row in additional.items():
        rows[row.kind].append(BudgetRow(key=key, name=row.name, actual=_money(amounts[key])))
    income, expenses, savings = (tuple(rows[kind]) for kind in ("income", "expense", "savings"))
    income_total = sum(_cents(row.actual) for row in income)
    expense_total = sum(_cents(row.actual) for row in expenses)
    reasons = list(draft_reasons) + ["Budget needs review: " + issue.message for issue in issues]
    plan = budget_plan_totals(settings, period.month)
    if all(value is not None for value in plan.values()) and (
        _cents(plan["income"]) != _cents(plan["expense"]) + _cents(plan["savings"])
    ):
        reasons.append("Budget targets do not balance: planned income must equal expenses plus savings")
    return BudgetReport(
        start_date=period.start_date, end_date=period.cutoff, period_label=period.end_date.strftime("%B %Y"),
        income=income, expenses=expenses, savings=savings,
        total_income=_money(income_total), total_expenses=_money(expense_total),
        total_savings=_money(income_total - expense_total), surplus=_money(income_total - expense_total),
        income_target=_target_total(income), expense_target=_target_total(expenses),
        savings_target=_target_total(savings), draft_reasons=tuple(dict.fromkeys(reasons)),
        issues=tuple(issues),
    )


def budget_transaction_counts(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings,
    period: "ReportPeriod", *, opening_entry_ids: Iterable[UUID | str] = (),
) -> dict[str, int]:
    """
    Count each projected cash event once per income or expense side, including signed refunds.
    """
    _validate_settings(settings, accounts)
    openings = {UUID(str(identifier)) for identifier in opening_entry_ids}
    identifiers = {"income": set(), "expenses": set()}
    for entry in entries:
        if period.start_date <= entry.date <= period.cutoff:
            projection = _project_entry(entry, accounts, settings, opening=entry.id in openings)
            if projection.income:
                identifiers["income"].add(entry.id)
            if projection.expenses:
                identifiers["expenses"].add(entry.id)
    return {key: len(values) for key, values in identifiers.items()}


def savings_baseline(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, budget: BudgetSettings, day: date,
) -> Decimal:
    """
    Supply the cash Savings remainder to retired readers without reinstating virtual allocation capacity.
    """
    period = _impact_period(day, max(day, date.today()))
    report = build_budget_report(entries, accounts, budget, period)
    return next(row.actual for row in report.savings if row.key == "savings")


# %% 4. Helpers ----------------------------------------------------------------------------------------------
def _project_entry(
    entry: JournalEntry, accounts: AccountRegistry, settings: BudgetSettings, *, opening: bool = False,
) -> BudgetProjection:
    """
    Apply cash-boundary rules once to the complete event, never to independent bank observations.
    """
    validate_budget_entry(entry, accounts, settings)
    if opening:
        return BudgetProjection(entry_id=entry.id, reason="Opening position")
    by_id = {account.id: account for account in accounts.accounts.values()}
    cash_ids = {accounts.accounts[key].id for key in settings.cash_accounts}
    investment_ids = {accounts.accounts[key].id for key in settings.investment_accounts}
    cash = [line for line in entry.lines if line.account_id in cash_ids]
    if not cash:
        return BudgetProjection(entry_id=entry.id, reason="Outside cash budget")
    other = [line for line in entry.lines if line.account_id not in cash_ids]
    if not other:
        return BudgetProjection(entry_id=entry.id, reason="Cash transfer")
    movement = sum(_cents(line.debit) - _cents(line.credit) for line in cash)
    if not movement:
        return _budget_issue(
            entry, "mixed_cash_event",
            "Separate the unrelated movements in this event so its cash inflows and outflows are clear.",
        )
    kinds = {by_id[line.account_id].account_type for line in other}
    if kinds == {AccountType.INCOME}:
        return _projection(
            entry, settings, _account_rows(other, accounts, settings, "income"), "Cash income",
        )
    if kinds == {AccountType.EXPENSE}:
        reason = "Expense refund" if movement > 0 else "Cash expense"
        return _projection(entry, settings, _account_rows(other, accounts, settings, "expense"), reason)
    liabilities = [line for line in other if by_id[line.account_id].account_type == AccountType.LIABILITY]
    if liabilities and kinds <= {AccountType.LIABILITY, AccountType.EXPENSE}:
        if movement > 0 and all(line.credit > 0 for line in liabilities):
            return _budget_issue(
                entry, "loan_proceeds",
                "New borrowing received in a cash account has no budget treatment; review this event.",
            )
        if movement >= 0 or any(line.credit > 0 for line in other):
            return _budget_issue(
                entry, "mixed_liability_payment",
                "Separate this liability movement from other cash activity before budgeting it.",
            )
        keys = {settings.liability_categories.get(by_id[line.account_id].key) for line in liabilities}
        if None in keys:
            return _budget_issue(
                entry, "liability_category",
                "Choose an expense category for this liability in Setup → Accounts.",
            )
        if len(keys) != 1:
            return _budget_issue(
                entry, "mixed_liability_categories",
                "Separate repayments that use different expense categories.",
            )
        key = next(iter(keys))
        return _projection(entry, settings, [(key, -movement, "expense", "")], "Liability payment")
    investments = [line for line in other if line.account_id in investment_ids]
    remaining = [line for line in other if line.account_id not in investment_ids]
    unrealised = accounts.accounts.get(settings.unrealised_gains_account)
    if investments and all(
        by_id[line.account_id].account_type in {AccountType.INCOME, AccountType.EXPENSE}
        or unrealised is not None and line.account_id == unrealised.id
        for line in remaining
    ):
        reclassification = any(
            unrealised is not None and line.account_id == unrealised.id for line in remaining
        )
        withdrawal = (
            _reclassified_withdrawal(investments, remaining, by_id, unrealised.id)
            if reclassification else all(line.credit > 0 for line in investments)
        )
        if movement > 0 and withdrawal:
            return _projection(
                entry, settings, [("investment_return", movement, "income", "")],
                "Investment withdrawal",
            )
        if movement < 0 and all(line.debit > 0 for line in investments) and all(
            by_id[line.account_id].account_type == AccountType.EXPENSE for line in remaining
        ):
            invested = sum(_cents(line.debit) for line in investments)
            rows = [("investments", invested, "savings", "")]
            rows.extend(_account_rows(remaining, accounts, settings, "expense"))
            return _projection(entry, settings, rows, "Investment contribution")
        return _budget_issue(
            entry, "mixed_investment_event",
            "Separate cash-funded investment movements from other investment or income movements.",
        )
    capital = accounts.accounts.get(settings.capital_account)
    if (
        capital is not None and all(line.account_id == capital.id and line.debit > 0 for line in other)
        and all(line.credit > 0 for line in cash)
    ):
        return BudgetProjection(entry_id=entry.id, reason="Gift given")
    if capital is not None and all(line.account_id == capital.id for line in other) and movement > 0:
        if any(line.transaction_id is None for line in cash):
            return _budget_issue(
                entry, "opening_or_gift",
                "Confirm whether this unlinked Capital receipt is an opening position "
                "or link its gift evidence.",
            )
        return _projection(entry, settings, [("gift_received", movement, "income", "")], "Gift received")
    return _budget_issue(
        entry, "unknown_cash_counterpart",
        "Review this cash event's counterpart accounts or configured investment roles.",
    )


def _account_rows(lines, accounts: AccountRegistry, settings: BudgetSettings, kind: str) -> list[tuple]:
    """
    Map exact ordinary counterpart amounts without assuming a missing budget target means exclusion.
    """
    by_id = {account.id: account for account in accounts.accounts.values()}
    mapped = {
        account_key: key for key, category in settings.categories.items() if category.kind == kind
        for account_key in category.accounts
    }
    rows = []
    for line in lines:
        account = by_id[line.account_id]
        key = mapped.get(account.key, "unbudgeted:" + account.key)
        signed = _cents(line.credit) - _cents(line.debit)
        rows.append((key, signed if kind == "income" else -signed, kind, account.name))
    return rows


def _reclassified_withdrawal(investments, remaining, accounts: Mapping, unrealised_id: UUID) -> bool:
    """
    Recognise the shared fund-disposal builder's exact contribution and gain-release structure.

    A loss can debit the fund's earnings line. The configured unrealised-equity reclassification must
    exactly offset realised income, and match the signed earnings released from that same fund.
    """
    if len({line.account_id for line in investments}) != 1:
        return False
    if any(line.account_investment is None for line in investments):
        return False
    contribution = [line for line in investments if line.account_investment.kind == "contribution"]
    earnings = [line for line in investments if line.account_investment.kind == "earnings"]
    if not contribution or not earnings or len(contribution) + len(earnings) != len(investments):
        return False
    if any(line.credit <= 0 for line in contribution):
        return False
    gain = sum(_cents(line.credit) - _cents(line.debit) for line in earnings)
    equity = sum(
        _cents(line.debit) - _cents(line.credit) for line in remaining if line.account_id == unrealised_id
    )
    income = sum(
        _cents(line.credit) - _cents(line.debit) for line in remaining
        if accounts[line.account_id].account_type == AccountType.INCOME
    )
    return gain != 0 and gain == equity == income


def _projection(
    entry: JournalEntry, settings: BudgetSettings, supplied: Iterable[tuple], reason: str,
) -> BudgetProjection:
    """
    Aggregate exact category cents and calculate the one residual Savings amount.
    """
    amounts, names, kinds = {}, {}, {}
    for key, amount, kind, name in supplied:
        amounts[key] = amounts.get(key, 0) + amount
        kinds[key] = kind
        names[key] = settings.categories[key].name if key in settings.categories else name
    income = sum(amount for key, amount in amounts.items() if kinds[key] == "income")
    expenses = sum(amount for key, amount in amounts.items() if kinds[key] == "expense")
    invested = amounts.get("investments", 0)
    savings = income - expenses - invested
    amounts["savings"], names["savings"], kinds["savings"] = savings, "Savings", "savings"
    rows = tuple(
        BudgetImpactRow(
            key=key, name=names[key], kind=kinds[key], amount=_money(amount), residual=key == "savings",
        )
        for key, amount in amounts.items() if amount
    )
    return BudgetProjection(
        entry_id=entry.id, rows=rows, reason=reason,
        income=_money(income), expenses=_money(expenses),
        investments=_money(invested), savings=_money(savings),
    )


def _budget_issue(entry: JournalEntry, code: str, message: str) -> BudgetProjection:
    """
    Keep unresolved cash movements visible rather than certifying invented or silently omitted amounts.
    """
    return BudgetProjection(
        entry_id=entry.id, reason="Needs review",
        issues=(BudgetIssue(entry_id=entry.id, code=code, message=message),),
    )


def _combined_target(values: Iterable) -> str | None:
    """
    Sum complete target groups in cents, preserving an unknown result when any target is missing.
    """
    values = tuple(values)
    if not values or any(value is None for value in values):
        return None
    amounts = [BudgetTarget(amount=value).amount for value in values]
    return format(_money(sum(_cents(amount) for amount in amounts)), ".2f")


def _validate_settings(settings: BudgetSettings, accounts: AccountRegistry) -> None:
    """
    Resolve stable roles to existing account identities and reject incompatible category mappings.
    """
    for key in settings.cash_accounts:
        account = accounts.accounts.get(key)
        if account is None or account.account_type != AccountType.ASSET:
            raise ValueError(f"Budget Cash type must name an asset account: {key!r}")
    for key in settings.investment_accounts:
        account = accounts.accounts.get(key)
        if account is None or account.account_type != AccountType.ASSET:
            raise ValueError(f"Budget Investment type must name an asset account: {key!r}")
    for key in settings.liability_categories:
        account = accounts.accounts.get(key)
        if account is None or account.account_type != AccountType.LIABILITY:
            raise ValueError(f"Budget repayment mapping must name a liability account: {key!r}")
    for key in (settings.capital_account, settings.unrealised_gains_account):
        account = accounts.accounts.get(key)
        if account is not None and account.account_type != AccountType.EQUITY:
            raise ValueError("Budget Capital and unrealised-gain roles must name equity accounts")
    mapped = set()
    for key, category in settings.categories.items():
        for account_key in category.accounts:
            account = accounts.accounts.get(account_key)
            if account is None:
                raise ValueError(f"Unknown account {account_key!r} in budget category {key!r}")
            expected = AccountType.INCOME if category.kind == "income" else AccountType.EXPENSE
            if category.kind == "savings" or account.account_type != expected:
                raise ValueError(f"Account {account_key!r} is incompatible with budget category {key!r}")
            if account_key in mapped:
                raise ValueError(f"Budget account {account_key!r} is mapped to more than one category")
            mapped.add(account_key)


def _impact_period(day: date, today: date) -> "ReportPeriod":
    """
    Follow report working-month boundaries and the snapshot's current accounting cutoff.
    """
    from aurum.finance.reports import resolve_period
    year, month = reporting_month(day)
    return resolve_period((), month=f"{year:04d}-{month:02d}", today=today)


def _impact_difference(
    before: BudgetReport, after: BudgetReport, settings: BudgetSettings, month: str,
    *, warnings: tuple[str, ...] | None = None,
) -> BudgetImpactMonth:
    """
    Derive every displayed amount from two real budget reports, including their savings remainders.
    """
    residual = "savings"
    rows = []
    for kind, old_rows, new_rows in (
        ("income", before.income, after.income),
        ("expense", before.expenses, after.expenses),
        ("savings", before.savings, after.savings),
    ):
        old = {row.key: row for row in old_rows}
        new = {row.key: row for row in new_rows}
        for key in dict.fromkeys((*new, *old)):
            amount = _cents(new[key].actual) if key in new else 0
            amount -= _cents(old[key].actual) if key in old else 0
            if amount:
                rows.append(BudgetImpactRow(
                    key=key, name=(new.get(key) or old[key]).name, kind=kind,
                    amount=_money(amount), residual=key == residual,
                ))
    return BudgetImpactMonth(
        month=month, period_label=after.period_label, rows=tuple(rows),
        warnings=after.draft_reasons if warnings is None else warnings, issues=after.issues,
        income_change=_money(_cents(after.total_income) - _cents(before.total_income)),
        expense_change=_money(_cents(after.total_expenses) - _cents(before.total_expenses)),
        savings_change=_money(_cents(after.total_savings) - _cents(before.total_savings)),
    )


def _impact_summary(impact: BudgetImpactMonth, excluded: bool) -> str:
    """
    Lead with an event's actual contribution rather than its saved label or account-default setting.
    """
    ordinary = [row for row in impact.rows if row.kind in {"income", "expense"}]
    if len(ordinary) == 1:
        row = ordinary[0]
        caption = "Income" if row.kind == "income" else "Expenses"
        return f"{caption} {_impact_money(row.amount)} · {row.name}"
    if ordinary:
        totals = [
            f"{caption} {_impact_money(amount)}"
            for caption, amount in (("Income", impact.income_change), ("Expenses", impact.expense_change))
            if amount
        ]
        return " · ".join(totals) if totals else f"Split across {len(ordinary)} categories"
    if impact.rows:
        invested = sum((_cents(row.amount) for row in impact.rows if row.key == "investments"), 0)
        return "Investments " + _impact_money(_money(invested)) if invested else "Savings movement"
    return "No budget impact"


def _impact_tooltip(
    impact: BudgetImpactMonth, cutoff: date, excluded: bool, *, changes: Iterable[BudgetImpactMonth] = (),
) -> str:
    """
    Explain signed category contributions, calculated total savings and complete-month warnings.
    """
    lines = [f"{impact.period_label} · Through {cutoff.isoformat()}"]
    for row in impact.rows:
        lines.append(_impact_row_text(row))
    if not impact.rows:
        lines.append("Unresolved cash event." if impact.issues else "No change to budget totals.")
    lines.append(f"Total savings: {_impact_money(impact.savings_change)} (income less expenses).")
    if any(row.kind == "savings" and not row.residual for row in impact.rows):
        lines.append("Savings is income less expenses less Investments.")
    lines.extend(impact.warnings)
    for change in changes:
        lines.extend(("", f"Change to saved {change.period_label}"))
        lines.extend(_impact_row_text(row) for row in change.rows)
        if not change.rows:
            lines.append("Budget totals unchanged.")
        lines.append(f"Total savings change: {_impact_money(change.savings_change)}.")
        lines.extend(change.warnings)
    return "\n".join(lines)


def _impact_row_text(row: BudgetImpactRow) -> str:
    """
    Name a signed category amount consistently in contribution and saved-month comparison details.
    """
    caption = {"income": "Income", "expense": "Expenses", "savings": "Savings"}[row.kind]
    return f"{caption} · {row.name}: {_impact_money(row.amount)}"


def _impact_money(value: Decimal) -> str:
    """
    Format a signed AUD contribution with the same narrow thousands spacing as desktop money.
    """
    sign = "-" if value < 0 else "+" if value > 0 else ""
    amount = format(abs(value), ",.2f").replace(",", "\u202f")
    return f"{sign}${amount}"


def _validate_month(value: str) -> None:
    """
    Reject malformed planning labels while allowing future months to be prepared in advance.
    """
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValueError("Budget months must use YYYY-MM")
    date.fromisoformat(value + "-01")


def _target_total(rows: Iterable[BudgetRow]) -> Decimal | None:
    """
    Sum a complete set of targets, leaving the total blank if any displayed row is unbudgeted.
    """
    targets = [row.target for row in rows]
    if any(target is None for target in targets):
        return None
    return _money(sum(_cents(value) for value in targets))


def _cents(value: Decimal) -> int:
    """
    Convert validated whole-cent money to exact integer cents independently of Decimal precision.
    """
    numerator, denominator = value.as_integer_ratio()
    return numerator * 100 // denominator


def _money(cents: int) -> Decimal:
    """
    Restore integer cents as a two-place Decimal without rounding through the active numeric context.
    """
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return Decimal(f"{sign}{whole}.{fraction:02d}")
