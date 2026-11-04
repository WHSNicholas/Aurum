# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Finance - Budget                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum compares monthly budget targets with the journal's recorded income, expenses and savings allocations.

Ordinary income and expense postings follow configured account groups. Optional journal labels handle
exceptions without changing the accounting: a gift can increase Capital while appearing as budget income.
Total savings is income less expenses, regardless of which bank holds the money. Explicit savings labels
and manual pot allocations divide that total; the Savings row holds the remainder. Explicitly tagged internal
savings transfers move existing savings between categories without changing total savings or monthly capacity.
Transfers and opening positions do not create new savings. Interest contributes through income, and refunds
retain their sign.

Targets come from private YAML, with monthly overrides and no rollover. An omitted target stays blank,
rather than implying a zero budget. These calculations never change journal entries or call AI.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Iterable, Iterator, Mapping
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
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.dates import reporting_month, working_month_start, working_month_end
from aurum.finance.journal import JournalEntry
from aurum.finance.savings import SavingsAllocation

if TYPE_CHECKING:
    from aurum.finance.reports import ReportPeriod

# Default paths and amounts
DEFAULT_BUDGET_SETTINGS = Path(__file__).resolve().parents[2] / "config" / "budget.yaml"
Money = Annotated[Decimal, Field(decimal_places=2, allow_inf_nan=False)]
CATEGORY_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
UNALLOCATED_SAVINGS = "unallocated:savings"


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

    Income and expense account mappings supply defaults. Savings labels allocate part of income left after
    expenses. Accounts restrict eligible labelled postings; an empty tuple leaves eligible types unrestricted.
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

class SavingsSettings(BaseModel):
    """
    Name monthly savings pots and the category holding income not spent or allocated elsewhere.

    Pot keys refer to savings categories, not additional ledger accounts. The residual pot is calculated
    automatically after explicit allocations to the other pots.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    residual_category: str = Field(min_length=1)
    category_keys: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_pots(self) -> Self:
        """
        Require unique pot keys and include the calculated residual among them.
        """
        if len(set(self.category_keys)) != len(self.category_keys):
            raise ValueError("Savings pot keys must not repeat")
        if self.residual_category not in self.category_keys:
            raise ValueError("The residual savings category must be one of the configured pots")
        return self


class BudgetSettings(BaseModel):
    """
    Keep ordered budget categories, savings pots and month-specific target overrides immutable.

    Legacy monthly overrides inherit category defaults. From monthly_plans_from onward, every month has
    independent targets and absent amounts stay blank. Neither policy changes names or account mappings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    categories: Mapping[str, BudgetCategory] = Field(default_factory=dict)
    monthly_overrides: Mapping[str, Mapping[str, BudgetTarget]] = Field(default_factory=dict)
    monthly_plans_from: str | None = None
    savings: SavingsSettings | None = None

    @field_validator("monthly_plans_from")
    @classmethod
    def _check_planning_start(cls, value: str | None) -> str | None:
        """
        Keep the optional transition to independent monthly targets at a valid reporting-month label.
        """
        if value is not None:
            _validate_month(value)
        return value

    @model_validator(mode="after")
    def _check_settings(self) -> Self:
        """
        Validate category identities and override months, then freeze both mapping levels.
        """
        for key in self.categories:
            if not CATEGORY_KEY.fullmatch(key) or key in {"exclude", "default"}:
                raise ValueError(
                    "Category keys use lowercase letters/digits/underscores; exclude and default are reserved"
                )
        if self.savings is not None:
            for key in self.savings.category_keys:
                if key not in self.categories or self.categories[key].kind != "savings":
                    raise ValueError(f"Savings pot {key!r} must name a savings budget category")
        overrides = {}
        for month, targets in self.monthly_overrides.items():
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
                raise ValueError("Budget override months must use YYYY-MM")
            date.fromisoformat(month + "-01")
            for key, target in targets.items():
                if key not in self.categories:
                    raise ValueError(f"Unknown budget category in monthly override: {key!r}")
            overrides[month] = MappingProxyType(dict(targets))
        object.__setattr__(self, "categories", MappingProxyType(dict(self.categories)))
        object.__setattr__(self, "monthly_overrides", MappingProxyType(overrides))
        return self

    @field_serializer("categories")
    def _serialise_categories(self, value: Mapping[str, BudgetCategory]) -> dict:
        """
        Expose category settings as ordinary mappings for request context fingerprints.
        """
        return {key: category.model_dump(mode="json") for key, category in value.items()}

    @field_serializer("monthly_overrides")
    def _serialise_overrides(self, value: Mapping[str, Mapping[str, BudgetTarget]]) -> dict:
        """
        Expose monthly targets without leaking mutable mappings into the model.
        """
        return {
            month: {key: target.model_dump(mode="json") for key, target in targets.items()}
            for month, targets in value.items()
        }


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

    Total savings and surplus both equal income less expenses, including a negative result. Named pots divide
    that total; the residual Savings row holds the remainder. A section's target is blank if any displayed
    row lacks a target, so a partial plan cannot masquerade as a complete budget.
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


# %% 2. Configuration And Labels -----------------------------------------------------------------------------
def load_budget_settings(path: Path | str, accounts: AccountRegistry) -> BudgetSettings:
    """
    Load private budget YAML and validate its account references without changing any files.

    Raises:
        OSError: The configuration cannot be read.
        ValueError: YAML, targets, category definitions or account mappings are invalid.
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
    settings = BudgetSettings.model_validate(fields)
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
    Check explicit budget labels without altering the event's accounting.

    Labels belong to the posting that supplies the budget amount, not both bank observations. Savings
    labels can record withdrawals. Two distinct savings labels can move an allocation between categories;
    opposing postings with the same label are rejected. Several same-direction postings can be a true split.

    Raises:
        ValueError: An account or label is unknown, a label is incompatible with its account, or both
            transfer directions have the same savings label.
    """
    by_id = {account.id: account for account in accounts.accounts.values()}
    savings_sides: dict[str, set[bool]] = {}
    for line in entry.lines:
        account = by_id.get(line.account_id)
        if account is None:
            raise ValueError(f"Journal entry {entry.id} references an unknown account")
        key = line.budget_category
        if key is None or key in {"exclude", "default"}:
            continue
        category = settings.categories.get(key)
        if category is None:
            raise ValueError(f"Unknown budget category: {key!r}")
        if account.account_type not in _allowed_types(category.kind):
            raise ValueError(f"Budget category {key!r} cannot label account {account.key!r}")
        if category.accounts and account.key not in category.accounts:
            raise ValueError(f"Account {account.key!r} is not permitted for budget category {key!r}")
        if category.kind == "savings":
            savings_sides.setdefault(key, set()).add(line.debit > 0)
    if any(len(sides) > 1 for sides in savings_sides.values()):
        raise ValueError(
            "Use different savings categories for the two sides of a transfer, or leave both untagged"
        )


def budget_catalogue(settings: BudgetSettings) -> tuple[dict, ...]:
    """
    Provide compact permitted labels for manual-entry and human-review AI requests.

    Account keys limit labelled postings when supplied. Exclude suppresses one line; default explicitly
    restores ordinary account mapping. Only explicit posting labels exclude amounts from the budget.
    """
    categories = tuple(
        {"key": key, "name": category.name, "kind": category.kind, "accounts": category.accounts}
        for key, category in settings.categories.items()
    )
    return (
        *categories,
        {"key": "exclude", "name": "Exclude from budget", "kind": "exclude", "accounts": ()},
        {"key": "default", "name": "Use ordinary account mapping", "kind": "default", "accounts": ()},
    )


def validate_budget_labels(
    entries: Iterable[JournalEntry],
    accounts: AccountRegistry,
    settings: BudgetSettings | None = None,
    previous: Iterable[JournalEntry] = (),
) -> None:
    """
    Validate proposed labels, permitting preserved labels when no budget configuration is loaded.

    Without settings, an existing label can remain only on the same account and posting direction.
    New labels require the configuration so its permitted categories and account restrictions are checked.
    """
    if settings is not None:
        for entry in entries:
            validate_budget_entry(entry, accounts, settings)
        return
    existing = {
        (line.account_id, line.debit > 0, line.budget_category)
        for entry in previous
        for line in entry.lines
        if line.budget_category is not None
    }
    for entry in entries:
        for line in entry.lines:
            if line.budget_category is not None and (
                line.account_id, line.debit > 0, line.budget_category
            ) not in existing:
                raise ValueError("Load budget settings before assigning a new budget category")


def budget_revision(settings: BudgetSettings | None) -> str | None:
    """
    Identify AI categorisation instructions without invalidating proposals when monthly targets change.
    """
    if settings is None:
        return None
    context = {
        "categories": budget_catalogue(settings),
        "savings": settings.savings.model_dump(mode="json") if settings.savings else None,
    }
    payload = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


# %% 3. Budget Calculations ----------------------------------------------------------------------------------
def savings_baseline(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, budget: BudgetSettings, day: date,
) -> Decimal:
    """
    Return a reporting month's income left after expenses and explicit journal savings allocations.

    Manual splits consume this remaining capacity. Income includes interest and labelled gifts; transfers
    and opening positions do not create savings. Fully tagged internal savings transfers remain in report
    actuals, but neither consume nor release capacity for new allocations from this month's surplus.
    """
    _validate_settings(budget, accounts)
    year, month = reporting_month(day)
    start, end = working_month_start(year, month), working_month_end(year, month)
    entries = tuple(entries)
    amounts, _, _, _ = _budget_amounts(entries, accounts, budget, start, end)
    residual = budget.savings.residual_category if budget.savings else UNALLOCATED_SAVINGS
    transfers = _savings_transfer_adjustment(entries, accounts, budget, start, end)
    return _money(amounts[residual] + transfers)


def build_budget_report(
    entries: Iterable[JournalEntry],
    accounts: AccountRegistry,
    settings: BudgetSettings,
    period: "ReportPeriod",
    *,
    draft_reasons: Iterable[str] = (),
    allocations: Iterable[SavingsAllocation] = (),
) -> BudgetReport:
    """
    Calculate posted-journal actuals and monthly targets from a shared reporting snapshot.

    The caller supplies posted entries only. Ordinary income/expense accounts without a configured row
    appear as unbudgeted categories. Savings always equals income less expenses, even when no savings
    configuration exists. Journal labels and manual allocations divide that total without changing the
    balance-sheet accounting; the residual row keeps any unallocated amount or shortfall visible.
    """
    _validate_settings(settings, accounts)
    entries = tuple(entries)
    amounts, unbudgeted, actual_income, total_expenses = _budget_amounts(
        entries, accounts, settings, period.start_date, period.cutoff,
    )
    reasons = list(draft_reasons)
    _allocate_savings(amounts, allocations, settings, period)
    residual = settings.savings.residual_category if settings.savings else UNALLOCATED_SAVINGS
    surplus = actual_income - total_expenses
    transfers = _savings_transfer_adjustment(
        entries, accounts, settings, period.start_date, period.cutoff,
    )
    if surplus - amounts[residual] - transfers > max(surplus, 0):
        reasons.append(
            "Savings allocations exceed income left after expenses; review the month's allocations"
        )
    rows: dict[str, list[BudgetRow]] = {"income": [], "expense": [], "savings": []}
    targets = budget_targets(settings, period.month)
    for key, category in settings.categories.items():
        target = targets[key]
        amount = target.amount
        rows[category.kind].append(BudgetRow(
            key=key, name=category.name, actual=_money(amounts[key]), target=amount,
        ))
    for key, (name, kind) in unbudgeted.items():
        rows[kind].append(BudgetRow(key=key, name=name, actual=_money(amounts[key])))
    income, expenses, savings = (tuple(rows[kind]) for kind in ("income", "expense", "savings"))
    label = period.end_date.strftime("%B %Y") + (" (month-to-date)" if period.is_partial else "")
    return BudgetReport(
        start_date=period.start_date, end_date=period.cutoff, period_label=label,
        income=income, expenses=expenses, savings=savings,
        total_income=_money(actual_income), total_expenses=_money(total_expenses),
        total_savings=_money(sum(_cents(row.actual) for row in savings)),
        income_target=_target_total(income), expense_target=_target_total(expenses),
        savings_target=_target_total(savings), surplus=_money(surplus),
        draft_reasons=tuple(dict.fromkeys(reasons)),
    )


def budget_transaction_counts(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings,
    period: "ReportPeriod",
) -> dict[str, int]:
    """
    Count distinct posted events contributing to the period's budget income and expenses.

    The caller supplies posted entries only. Split postings count once per event and side; refunds still
    count as activity, and an event containing both included income and expenses contributes to both counts.
    Journal exclusions, explicit labels and unbudgeted accounts follow the report's actual-amount rules.
    """
    _validate_settings(settings, accounts)
    identifiers = {"income": set(), "expense": set()}
    for identifier, _, _, kind, _ in _budget_postings(
        entries, accounts, settings, period.start_date, period.cutoff,
    ):
        if kind in identifiers:
            identifiers[kind].add(identifier)
    return {"income": len(identifiers["income"]), "expenses": len(identifiers["expense"])}


# %% 4. Helpers ----------------------------------------------------------------------------------------------
def _validate_month(value: str) -> None:
    """
    Reject malformed planning labels while allowing future months to be prepared in advance.
    """
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValueError("Budget months must use YYYY-MM")
    date.fromisoformat(value + "-01")


def _budget_amounts(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings,
    start: date, end: date,
) -> tuple[dict[str, int], dict[str, tuple[str, str]], int, int]:
    """
    Collect signed journal amounts in whole cents and derive the residual from income less expenses.

    Reports and manual allocation capacity share these rules, including gifts, refunds and exclusions.
    Explicit savings labels divide the total; they never increase it by counting a transfer as new income.
    """
    # Add integer cents so large valid amounts cannot lose pennies to Decimal's current precision.
    amounts = {key: 0 for key in settings.categories}
    unbudgeted: dict[str, tuple[str, str]] = {}
    for _, key, name, kind, signed in _budget_postings(entries, accounts, settings, start, end):
        if key not in settings.categories:
            unbudgeted[key] = (name, kind)
        amounts[key] = amounts.get(key, 0) + signed

    actual_income = sum(
        amounts[key] for key, category in settings.categories.items() if category.kind == "income"
    ) + sum(amounts[key] for key, (_, kind) in unbudgeted.items() if kind == "income")
    actual_expenses = sum(
        amounts[key] for key, category in settings.categories.items() if category.kind == "expense"
    ) + sum(amounts[key] for key, (_, kind) in unbudgeted.items() if kind == "expense")
    residual = settings.savings.residual_category if settings.savings else UNALLOCATED_SAVINGS
    assigned = sum(
        amounts[key] for key, category in settings.categories.items()
        if category.kind == "savings" and key != residual
    )
    amounts[residual] = actual_income - actual_expenses - assigned
    if settings.savings is None:
        unbudgeted[residual] = ("Savings", "savings")
    return amounts, unbudgeted, actual_income, actual_expenses


def _budget_postings(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings,
    start: date, end: date,
) -> Iterator[tuple[UUID, str, str, str, int]]:
    """
    Share included posting identities, categories and signed cents between budget totals and event counts.
    """
    by_id = {account.id: account for account in accounts.accounts.values()}
    mapped = {
        account_key: key
        for key, category in settings.categories.items()
        if category.kind in {"income", "expense"}
        for account_key in category.accounts
        if accounts.accounts[account_key].account_type in {AccountType.INCOME, AccountType.EXPENSE}
    }
    for entry in entries:
        if not start <= entry.date <= end:
            continue
        validate_budget_entry(entry, accounts, settings)
        for line in entry.lines:
            account = by_id[line.account_id]
            key = line.budget_category
            if key == "exclude":
                continue
            if key == "default":
                key = None
            if key is None:
                if account.account_type not in {AccountType.INCOME, AccountType.EXPENSE}:
                    continue
                key = mapped.get(account.key, "unbudgeted:" + account.key)
            kind = settings.categories[key].kind if key in settings.categories else account.account_type.value
            debit, credit = _cents(line.debit), _cents(line.credit)
            signed = credit - debit if kind == "income" else debit - credit
            yield entry.id, key, account.name, kind, signed


def _savings_transfer_adjustment(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, settings: BudgetSettings,
    start: date, end: date,
) -> int:
    """
    Separate explicit moves of existing savings from allocations of this month's income surplus.

    Only a complete, source-linked TRANSFER WITHIN pair on its own bank account qualifies. Both sides
    require different savings labels. Keep these signed movements in report actuals, but neither consume
    nor release current-month capacity for new manual allocations. Other entries retain normal checks.
    """
    by_id = {account.id: account for account in accounts.accounts.values()}
    residual = settings.savings.residual_category if settings.savings else UNALLOCATED_SAVINGS
    adjustment = 0
    for entry in entries:
        if not start <= entry.date <= end or len(entry.lines) != 2:
            continue
        first, second = entry.lines
        if (
            first.account_id != second.account_id
            or first.transaction_id is None or second.transaction_id is None
            or first.transaction_id == second.transaction_id
            or first.debit != second.credit or first.credit != second.debit
            or first.budget_category is None or second.budget_category is None
            or first.budget_category == second.budget_category
        ):
            continue
        account = by_id.get(first.account_id)
        if account is None or account.account_type != AccountType.ASSET or account.bank_details is None:
            continue
        categories = [
            settings.categories.get(first.budget_category), settings.categories.get(second.budget_category),
        ]
        if any(category is None or category.kind != "savings" for category in categories):
            continue
        first_route, second_route = [re.fullmatch(
            r"TRANSFER\s+WITHIN\s+([0-9]{6})\s*-\s*([0-9]+)\s+#([0-9]+)\s*",
            line.description, re.IGNORECASE,
        ) for line in entry.lines]
        if first_route is None or second_route is None or first_route.groups() != second_route.groups():
            continue
        if first_route.groups()[:2] != (account.bank_details.bsb, account.bank_details.account_number):
            continue
        adjustment += sum(
            _cents(line.debit) - _cents(line.credit)
            for line in entry.lines if line.budget_category != residual
        )
    return adjustment


def _allocate_savings(
    amounts: dict[str, int], allocations: Iterable[SavingsAllocation], settings: BudgetSettings,
    period: "ReportPeriod",
) -> None:
    """
    Move saved monthly contributions from the residual to named pots, leaving their combined total intact.

    If later accounting corrections reduce capacity, retain the allocation and flag the report for review.
    These amounts describe this month's saving, not cumulative balances carried across months.
    """
    selected = [item for item in allocations if period.start_date <= item.date <= period.cutoff]
    if not selected:
        return
    if settings.savings is None:
        raise ValueError("Saved pot allocations require savings settings in the budget configuration")
    residual = settings.savings.residual_category
    permitted = set(settings.savings.category_keys) - {residual}
    total = 0
    for allocation in selected:
        for item in allocation.amounts:
            if item.category_key not in permitted:
                raise ValueError(f"Saved allocation refers to an unconfigured pot: {item.category_key!r}")
            cents = _cents(item.amount)
            amounts[item.category_key] += cents
            total += cents
    amounts[residual] -= total


def _allowed_types(kind: str) -> set[AccountType]:
    """
    Identify accounting types that can supply a category's signed amount.
    """
    if kind == "income":
        return {AccountType.INCOME, AccountType.EQUITY}
    if kind == "expense":
        return {AccountType.EXPENSE}
    return {AccountType.ASSET, AccountType.LIABILITY}


def _validate_settings(settings: BudgetSettings, accounts: AccountRegistry) -> None:
    """
    Reject unknown accounts, incompatible category mappings and ambiguous automatic groups.
    """
    defaults = set()
    for key, category in settings.categories.items():
        for account_key in category.accounts:
            account = accounts.accounts.get(account_key)
            if account is None:
                raise ValueError(f"Unknown account {account_key!r} in budget category {key!r}")
            if account.account_type not in _allowed_types(category.kind):
                raise ValueError(f"Account {account_key!r} is incompatible with budget category {key!r}")
            if account.account_type in {AccountType.INCOME, AccountType.EXPENSE}:
                if account_key in defaults:
                    raise ValueError(f"Budget account {account_key!r} is mapped to more than one category")
                defaults.add(account_key)


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
