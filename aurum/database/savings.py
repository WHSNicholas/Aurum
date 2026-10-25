# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Database - Savings                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum stores monthly virtual-pot splits independently from the accounting journal.

One row belongs to one stable manual instruction. Replacing that row changes the budget allocation only;
source transactions, journal balances and other instructions remain untouched. The normal manual workflow
owns request history and commits the split with its accepted outcome.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.database.journal import load_journal_entries
from aurum.finance.accounts import AccountRegistry
from aurum.finance.budget import BudgetSettings, budget_revision, savings_baseline
from aurum.finance.dates import reporting_month, working_month_start, working_month_end
from aurum.finance.savings import SavingsAllocation, SavingsContext, SavingsCapacityError


SAVINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS savings_allocations (
    manual_id TEXT PRIMARY KEY NOT NULL,
    date TEXT NOT NULL,
    allocation TEXT NOT NULL
) STRICT;
"""


# %% 1. Savings Storage --------------------------------------------------------------------------------------
def load_savings_allocations(connection: sqlite3.Connection) -> dict[UUID, SavingsAllocation]:
    """
    Read saved manual splits, treating an older database without this optional table as empty.
    """
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'savings_allocations'"
    ).fetchone():
        return {}
    allocations = {}
    for identifier, day, payload in connection.execute(
        "SELECT manual_id, date, allocation FROM savings_allocations ORDER BY manual_id"
    ):
        allocation = SavingsAllocation.model_validate_json(payload)
        if allocation.date.isoformat() != day:
            raise ValueError("A stored savings allocation has conflicting dates")
        allocations[UUID(identifier)] = allocation
    return allocations


def savings_context(
    connection: sqlite3.Connection, manual_id: UUID, day: date,
    accounts: AccountRegistry, budget: BudgetSettings,
) -> SavingsContext:
    """
    Build the current monthly capacity and version without reserving sources or writing accounting.
    """
    if budget.savings is None:
        raise ValueError("Configure savings pots before allocating monthly savings")
    year, month = reporting_month(day)
    start, end = working_month_start(year, month), working_month_end(year, month)
    entries = [entry for entry in load_journal_entries(connection, accounts) if start <= entry.date <= end]
    allocations = load_savings_allocations(connection)
    others = {
        identifier: allocation for identifier, allocation in allocations.items()
        if identifier != manual_id and start <= allocation.date <= end
    }
    other_amounts = tuple(item for allocation in others.values() for item in allocation.amounts)
    # Different instructions may use the same pot; sum their cents without merging their identities.
    cents = sum(item.amount.as_integer_ratio()[0] * 100 // item.amount.as_integer_ratio()[1]
                for item in other_amounts)
    total = Decimal(f"{cents // 100}.{cents % 100:02d}")
    baseline = savings_baseline(entries, accounts, budget, day)
    context = {
        "budget_revision": budget_revision(budget), "month": f"{year:04d}-{month:02d}",
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "allocations": {str(identifier): allocation.model_dump(mode="json")
                        for identifier, allocation in others.items()},
        "current": allocations[manual_id].model_dump(mode="json") if manual_id in allocations else None,
    }
    revision = sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return SavingsContext(
        month=context["month"], baseline=baseline, other_allocated=total,
        current=allocations.get(manual_id), revision=revision,
    )


def save_savings_allocation(
    connection: sqlite3.Connection, manual_id: UUID, allocation: SavingsAllocation,
    accounts: AccountRegistry, budget: BudgetSettings,
) -> None:
    """
    Replace one complete split inside the manual workflow's transaction after checking live capacity.

    Other saved instructions count against the same month's budget income less expenses and existing
    explicit journal allocations. Interest contributes through income. Opening balances and transfers
    of existing money do not create new savings.
    """
    if not connection.in_transaction:
        raise ValueError("Save savings allocations inside the manual outcome transaction")
    allocation = SavingsAllocation.model_validate(allocation)
    if budget.savings is None:
        raise ValueError("Configure savings pots before allocating monthly savings")
    permitted = set(budget.savings.category_keys) - {budget.savings.residual_category}
    if any(item.category_key not in permitted for item in allocation.amounts):
        raise ValueError("Allocate only configured pots; the residual is calculated automatically")
    context = savings_context(connection, manual_id, allocation.date, accounts, budget)
    requested = allocation.total.as_integer_ratio()
    others = context.other_allocated.as_integer_ratio()
    baseline = max(context.baseline, Decimal("0")).as_integer_ratio()
    # A later journal correction can leave old allocations over capacity. Permit resetting or reducing
    # this instruction so the user can repair that excess one row at a time.
    previous = context.current
    reducing = (
        allocation.total == 0 or previous is not None
        and reporting_month(previous.date) == reporting_month(allocation.date)
        and allocation.total < previous.total
    )
    if not reducing and (
        requested[0] * 100 // requested[1] + others[0] * 100 // others[1]
        > baseline[0] * 100 // baseline[1]
    ):
        raise SavingsCapacityError(
            "Reduce this row's amounts, or finish preparing the journal and change human_response to "
            "'Retry this allocation'. The split exceeds monthly budget income less expenses and "
            "existing allocations."
        )
    connection.execute(
        """
        INSERT INTO savings_allocations (manual_id, date, allocation) VALUES (?, ?, ?)
        ON CONFLICT(manual_id) DO UPDATE SET date = excluded.date, allocation = excluded.allocation
        """,
        (str(manual_id), allocation.date.isoformat(), allocation.model_dump_json()),
    )
