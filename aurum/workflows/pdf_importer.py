# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                               Workflows - PDF Importer                                               #
# -------------------------------------------------------------------------------------------------------------------- #
"""
This file turns ANZ Plus statements and month-to-date transaction lists into immutable Transactions.

It reads the statement period and bank details from the PDF, then matches those details to a configured
account. Each transaction carries that account's UUID; the bank identifiers are used during parsing and
remain on the account definition.

The parser uses the credit and debit columns to decide each amount's sign. It keeps the descriptions and
printed order of transactions, then checks the results against the statement totals and available running
balances. Transaction lists retain their generation timestamp without pretending it proves complete coverage.

Run imports through: python aurum/scripts/pdf_importer.py
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import pdfplumber
from pdfplumber.page import Page

# Aurum imports
from aurum.finance.accounts import Account, AccountRegistry, BankDetails
from aurum.finance.dates import reporting_month, working_month_start
from aurum.finance.transaction import Transaction


# %% 1. Parser Setup -----------------------------------------------------------------------------------------
# Statement patterns
MONEY = r"(?:-\$|\$-?)(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
PERIOD = re.compile(r"(\d{1,2} [A-Za-z]+ \d{4})\s*[-–]\s*(\d{1,2} [A-Za-z]+ \d{4})")
SUMMARY = re.compile(rf"(\d{{3}}\s*\d{{3}})\s+(\d[\d ]*)\s+({MONEY})\s+({MONEY})")
OPENING = re.compile(rf"Opening Balance\s+({MONEY})")
EFFECTIVE = re.compile(r"Effective Date (\d{2}/\d{2}/\d{4})")
COMMENCING = re.compile(r"Commencing (\d{1,2} [A-Za-z]+ \d{4})")
LIST_ACCOUNT = re.compile(r"(\d{3}\s*\d{3})\s+(\d[\d ]*)")
GENERATED = re.compile(
    r"Transaction List generated on (\d{1,2} [A-Za-z]+ \d{4}) at (\d{1,2}:\d{2}) Melbourne Time"
)


@dataclass(frozen=True)
class ParsedStatement:
    """
    Retain the validated account and statement period even when a PDF contains no transaction rows.
    """

    account: Account
    start_date: date
    end_date: date
    transactions: tuple[Transaction, ...]
    document_kind: Literal["statement", "transaction_list"] = "statement"
    generated_at: datetime | None = None

    @property
    def reporting_month(self) -> str:
        """
        Name statements by their end month and cumulative lists by their commencing reporting month.
        """
        if self.document_kind == "statement":
            return self.end_date.strftime("%Y-%m")
        year, month = reporting_month(self.start_date)
        return f"{year:04d}-{month:02d}"


# %% 2. PDF Importing ----------------------------------------------------------------------------------------
def parse_anz_statement(
    path: Path | str, accounts: AccountRegistry, *, contents: bytes | None = None,
) -> ParsedStatement:
    """
    Validate an ANZ Plus PDF and return its account, dates and supplied transaction snapshot.

    Check the PDF header, transactions and balances without importing data.
    The matched account and dates support managed filenames without inferring them from a supplied filename.
    """
    # PDF contents
    path = Path(path)
    if contents is None:
        contents = path.read_bytes()
    with pdfplumber.open(BytesIO(contents)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages")
        statement, opening, closing = _document_metadata(pdf, accounts)
        details = statement.account.bank_details
        # Transaction objects
        rows = _read_rows(
            pdf, statement.start_date, statement.end_date, opening,
            transaction_list=statement.document_kind == "transaction_list",
        )
        positions = [(row["source_page"], row["source_row"]) for row in rows]
        transactions = [
            Transaction(
                **{key: value for key, value in row.items() if key not in ("source_page", "source_row")},
                account_id=statement.account.id, source_file=path.stem,
            )
            for row in rows
        ]
        _check_balances(transactions, opening, closing, details, positions)
        if statement.document_kind == "transaction_list" and any(
            newer.date < older.date for newer, older in zip(transactions, transactions[1:])
        ):
            raise ValueError("Expected transaction-list rows in newest-first order")
    return replace(statement, transactions=tuple(transactions))


def read_anz_document_metadata(
    path: Path | str, accounts: AccountRegistry, *, contents: bytes | None = None,
) -> ParsedStatement:
    """
    Read a recognised document's account and dates without interpreting or importing its transactions.

    The empty transactions tuple is a metadata-only result, not a claim that the source PDF is empty.
    A list's end_date is its generation date, used as an upper bound rather than a coverage guarantee.
    """
    contents = Path(path).read_bytes() if contents is None else contents
    with pdfplumber.open(BytesIO(contents)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages")
        statement, _, _ = _document_metadata(pdf, accounts)
    return statement


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _document_metadata(
    pdf: pdfplumber.PDF, accounts: AccountRegistry,
) -> tuple[ParsedStatement, Decimal | None, Decimal | None]:
    """
    Match a supported document header to one configured account, retaining optional statement totals.
    """
    lines = [line["text"] for line in pdf.pages[0].extract_text_lines()]
    is_list = "Transaction List" in lines
    if is_list and "Account Statement" in lines:
        raise ValueError("The PDF has conflicting statement and transaction-list headings")
    generated_at = None
    if is_list:
        details, start, generated_at, opening, closing = _read_list_header(pdf)
        end = generated_at.date()
    else:
        details, start, end, opening, closing = _read_header(pdf.pages[0])
    matches = [account for account in accounts.accounts.values() if account.bank_details == details]
    if len(matches) != 1:
        raise ValueError("Expected exactly one configured account matching the PDF's bank details")
    return ParsedStatement(
        matches[0], start, end, (), "transaction_list" if is_list else "statement", generated_at,
    ), opening, closing


def _read_list_header(
    pdf: pdfplumber.PDF,
) -> tuple[BankDetails, date, datetime, Decimal | None, Decimal | None]:
    """
    Identify a cumulative ANZ list, retaining opening and closing totals when its summary supplies them.
    """
    first = pdf.pages[0]
    text = first.extract_text()
    lines = [line["text"] for line in first.extract_text_lines()]
    if "Australia and New Zealand Banking Group" not in text:
        raise ValueError("Expected an ANZ Plus transaction list")
    starts = [match for line in lines if (match := COMMENCING.fullmatch(line))]
    summaries = [
        match for line in lines if (match := LIST_ACCOUNT.fullmatch(line) or SUMMARY.fullmatch(line))
    ]
    stamps = {
        match.groups() for page in pdf.pages for line in page.extract_text_lines()
        if (match := GENERATED.fullmatch(line["text"]))
    }
    if len(starts) != 1 or len(summaries) != 1 or len(stamps) != 1:
        raise ValueError("Could not identify a unique list commencement, account and generation timestamp")
    start = datetime.strptime(starts[0][1], "%d %B %Y").date()
    stamp = next(iter(stamps))
    generated_at = datetime.strptime(" ".join(stamp), "%d %B %Y %H:%M").replace(
        tzinfo=ZoneInfo("Australia/Melbourne"),
    )
    if start > generated_at.date():
        raise ValueError("Transaction list was generated before its commencement date")
    if start != working_month_start(*reporting_month(start)):
        raise ValueError("Only cumulative lists beginning at the reporting-month start are supported")
    bsb, number, *balances = summaries[0].groups()
    details = BankDetails(bsb="".join(bsb.split()), account_number="".join(number.split()))
    opening, closing = tuple(_money(value) for value in balances) if balances else (None, None)
    return details, start, generated_at, opening, closing


def _money(text: str) -> Decimal:
    """
    Read a printed dollar amount exactly, rejecting an unexpected format.
    """
    if not re.fullmatch(MONEY, text):
        raise ValueError("Expected a dollar amount with two decimal places")
    return Decimal(text.replace("$", "").replace(",", ""))


def _read_header(page: Page) -> tuple[BankDetails, date, date, Decimal, Decimal]:
    """
    Read bank details, the period, and opening and closing balances from the first page.

    Raises:
        ValueError: The ANZ Plus header is missing, ambiguous, or invalid.
    """
    # Statement format
    lines = [line["text"] for line in page.extract_text_lines()]
    if (
        "Account Statement" not in lines
        or "Australia and New Zealand Banking Group" not in page.extract_text()
    ):
        raise ValueError("Expected an ANZ Plus account statement")

    # Statement summary
    periods = [match for line in lines if (match := PERIOD.fullmatch(line))]
    summaries = [match for line in lines if (match := SUMMARY.fullmatch(line))]
    if len(periods) != 1 or len(summaries) != 1:
        raise ValueError("Could not identify a unique statement period and balance summary")

    start, end = (datetime.strptime(value, "%d %B %Y").date() for value in periods[0].groups())
    if end < start:
        raise ValueError("Statement period ends before it starts")
    bsb, number, opening, closing = summaries[0].groups()
    details = BankDetails(bsb="".join(bsb.split()), account_number="".join(number.split()))
    return details, start, end, _money(opening), _money(closing)


def _transaction_date(text: str, start: date, end: date) -> date:
    """
    Resolve a printed day and month to the single matching date within the statement period.

    A period can cross New Year. Neither the filename nor the current year supplies the transaction year.
    """
    candidates = []
    for year in range(start.year, end.year + 1):
        try:
            candidate = datetime.strptime(f"{text} {year}", "%d %b %Y").date()
        except ValueError:
            continue
        if start <= candidate <= end:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError("Transaction date is invalid or outside the statement period")
    return candidates[0]


def _start_row(words: list[dict], columns: dict[str, float], start: date, end: date) -> dict:
    """
    Read a dated row, using the right-aligned column positions to separate money from description text.

    Returns:
        Fields for one Transaction, before source information and continuation lines are added.

    Raises:
        ValueError: A row has an invalid date, amount, or monetary column layout.
    """
    # Row contents
    amounts = {}
    description = []
    for word in words[2:]:
        # ANZ list credits can end two points inside their heading; allow PDF coordinate rounding.
        column = next(
            (name for name in ("Credit", "Debit", "Balance")
             if name in columns and abs(word["x1"] - columns[name]) <= 2.1),
            None,
        )
        if column is not None:
            if column in amounts:
                raise ValueError("Multiple values in one monetary column")
            amounts[column] = _money(word["text"])
        else:
            if word["x0"] >= columns["Credit"] - (columns["Debit"] - columns["Credit"]):
                raise ValueError("Unrecognised content in a monetary column")
            description.append(word["text"])

    # Signed amount
    if ("Credit" in amounts) == ("Debit" in amounts):
        raise ValueError("Expected exactly one Credit or Debit amount")
    if any(amounts[name] < 0 for name in ("Credit", "Debit") if name in amounts):
        raise ValueError("Credit and Debit column amounts must not be negative")
    amount = amounts["Credit"] if "Credit" in amounts else -amounts["Debit"]
    return {
        "date": _transaction_date(" ".join(word["text"] for word in words[:2]), start, end),
        "description": " ".join(description),
        "amount": amount,
        "statement_balance": amounts.get("Balance"),
    }


def _read_rows(
    pdf: pdfplumber.PDF, start: date, end: date, opening: Decimal | None,
    *, transaction_list: bool = False,
) -> list[dict]:
    """
    Assemble dated rows, wrapped descriptions, and effective dates across all pages.

    The pending row survives a page break so its description can continue below the next table heading.
    Unknown content within the transaction table raises an error instead of silently dropping a row.
    """
    # Row state
    rows = []
    current = None
    finished = False
    saw_table = False
    for page in pdf.pages:
        in_table = False
        page_words = page.extract_words()
        for line in page.extract_text_lines(return_chars=False):
            text = line["text"]
            # Keep the original word spacing; text-line character lists omit some space characters.
            words = [
                word for word in page_words
                if word["top"] >= line["top"] - 1 and word["bottom"] <= line["bottom"] + 1
            ]
            # Table boundaries
            if text == "Date Description Credit Debit Balance" or (
                transaction_list and text == "Date Description Credit Debit"
            ):
                if finished:
                    raise ValueError(f"Page {page.page_number}: table continues after the document's end")
                columns = {word["text"]: word["x1"] for word in words}
                description_left = next(word["x0"] for word in words if word["text"] == "Description")
                in_table = saw_table = True
                continue
            if transaction_list and GENERATED.fullmatch(text):
                finished = True
                in_table = False
                continue
            if not in_table:
                continue
            if text.startswith("Australia and New Zealand Banking Group"):
                break
            if text.startswith("Opening Balance"):
                if transaction_list:
                    raise ValueError("Unexpected opening-balance row in a transaction list")
                match = OPENING.fullmatch(text)
                if match is None or _money(match[1]) != opening:
                    raise ValueError(f"Page {page.page_number}: opening balance does not match the header")
                finished = True
                break

            # Transaction rows
            try:
                # The Date column distinguishes a new row from a date mentioned inside a description.
                if line["x0"] < description_left - 2:
                    if current is not None:
                        rows.append(current)
                    current = _start_row(words, columns, start, end)
                    current.update(source_page=page.page_number, source_row=len(rows) + 1)
                elif current is None:
                    raise ValueError("Continuation text has no preceding transaction")
                elif text.startswith("Effective Date"):
                    match = EFFECTIVE.fullmatch(text)
                    if match is None or current.get("effective_date") is not None:
                        raise ValueError("Invalid or repeated effective date")
                    current["effective_date"] = datetime.strptime(match[1], "%d/%m/%Y").date()
                else:
                    if any(word["x0"] >= columns["Credit"] - (
                        columns["Debit"] - columns["Credit"]
                    ) for word in words):
                        raise ValueError("Unrecognised continuation in a monetary column")
                    current["description"] += " " + text
            except ValueError as error:
                raise ValueError(f"Page {page.page_number}, transaction {len(rows) + 1}: {error}") from None

    if current is not None:
        rows.append(current)
    # Statement validation
    if saw_table and not finished:
        ending = "generation timestamp" if transaction_list else "opening balance"
        raise ValueError(f"Transaction table has no final {ending}")
    if not saw_table and "no transactions to display" not in pdf.pages[0].extract_text().lower():
        raise ValueError("Neither a transaction table nor an explicit empty-statement message was found")
    return rows


def _check_balances(
    transactions: list[Transaction], opening: Decimal | None, closing: Decimal | None,
    bank_details: BankDetails, positions: list[tuple[int, int]],
) -> None:
    """
    Check total movement and available running balances in ANZ's newest-first printed order.

    ANZ prints transfers within one bank account as offsetting rows without running balances. Validate their
    pairs separately and retain both rows; their positions do not affect the bank account's running balance.
    positions supplies temporary (page, row) pairs for error messages, without storing them on Transactions.

    Raises:
        ValueError: The extracted rows do not reconcile with the bank's reported balances.
    """
    # Full statements and some cumulative lists supply independent totals for their complete movement.
    if opening is not None and opening + sum(
        (item.amount for item in transactions), Decimal("0.00"),
    ) != closing:
        raise ValueError("Opening balance plus transaction amounts does not equal closing balance")

    # Internal transfers
    within = {}
    for item, (page, number) in zip(transactions, positions, strict=True):
        if item.description.startswith("TRANSFER WITHIN "):
            match = re.fullmatch(r"TRANSFER WITHIN (\d{6}-\d+) #\d+", item.description)
            if (
                match is None
                or match[1] != bank_details.full_details
                or item.statement_balance is not None
            ):
                raise ValueError(f"Unexpected TRANSFER WITHIN format at page {page}, transaction {number}")
            within.setdefault((item.date, item.description), []).append(item)
    for pair in within.values():
        if len(pair) != 2 or pair[0].amount != -pair[1].amount:
            raise ValueError("TRANSFER WITHIN rows must form equal and opposite pairs on the same date")

    # Running balances
    expected = closing
    for item, (page, number) in zip(transactions, positions, strict=True):
        if item.description.startswith("TRANSFER WITHIN "):
            continue
        if expected is None:
            expected = item.statement_balance
        if item.statement_balance is not None and item.statement_balance != expected:
            raise ValueError(
                f"Running balance does not reconcile at page {page}, transaction {number}"
            )
        if expected is not None:
            expected -= item.amount
