# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                               Workflows - PDF Importer                                               #
# -------------------------------------------------------------------------------------------------------------------- #
"""
This file turns ANZ Plus PDF statements into the Transaction objects Aurum can save and use.

It reads the statement period and bank details from the PDF, then matches those details to a configured
account. Each transaction carries that account's UUID; the bank identifiers are used during parsing and
remain on the account definition.

The parser uses the credit and debit columns to decide each amount's sign. It keeps the descriptions and
printed order of transactions, then checks the results against the statement totals and available running
balances. It currently supports ANZ Plus statements only.

Run imports through: python aurum/scripts/pdf_importer.py
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import re
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pdfplumber
from pdfplumber.page import Page

# Aurum imports
from aurum.finance.accounts import AccountRegistry, BankDetails
from aurum.finance.transaction import Transaction


# %% 1. Parser Setup -----------------------------------------------------------------------------------------
# Statement patterns
MONEY = r"(?:-\$|\$-?)(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
PERIOD = re.compile(r"(\d{1,2} [A-Za-z]+ \d{4})\s*[-–]\s*(\d{1,2} [A-Za-z]+ \d{4})")
SUMMARY = re.compile(rf"(\d{{3}}\s*\d{{3}})\s+(\d[\d ]*)\s+({MONEY})\s+({MONEY})")
OPENING = re.compile(rf"Opening Balance\s+({MONEY})")
EFFECTIVE = re.compile(r"Effective Date (\d{2}/\d{2}/\d{4})")


# %% 2. PDF Importing ----------------------------------------------------------------------------------------
def parse_anz_pdf(
    path: Path | str, accounts: AccountRegistry, *, contents: bytes | None = None
) -> list[Transaction]:
    """
    Read and validate one ANZ Plus PDF, returning its transactions in printed order.

    Args:
        path: Original PDF statement. It is read without changing or moving it.
        accounts: Configured accounts. Exactly one must match the PDF header's bank identifiers.
        contents: Optional PDF bytes already read by the folder importer, so hashing and parsing use the
            same snapshot without reading the file again.

    Returns:
        A list of Transactions, or an empty list for a validated statement with no transactions.
        Every transaction carries its configured source account UUID and the PDF's filename stem.

    Raises:
        OSError: The file cannot be read.
        PdfminerException: The PDF is malformed or cannot be opened without a password.
        ValueError: Statement content fails validation or the source account match is missing or ambiguous.
    """
    # PDF contents
    path = Path(path)
    if contents is None:
        contents = path.read_bytes()
    with pdfplumber.open(BytesIO(contents)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages")
        # Source account
        details, start, end, opening, closing = _read_header(pdf.pages[0])
        matches = [account for account in accounts.accounts.values() if account.bank_details == details]
        if len(matches) != 1:
            raise ValueError("Expected exactly one configured account matching the PDF's bank details")
        # Transaction objects
        rows = _read_rows(pdf, start, end, opening)
        positions = [(row["source_page"], row["source_row"]) for row in rows]
        transactions = [
            Transaction(
                **{key: value for key, value in row.items() if key not in ("source_page", "source_row")},
                account_id=matches[0].id, source_file=path.stem,
            )
            for row in rows
        ]
        _check_balances(transactions, opening, closing, details, positions)
    return transactions


# %% 3. Helpers ----------------------------------------------------------------------------------------------
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
        column = next(
            (name for name in ("Credit", "Debit", "Balance") if abs(word["x1"] - columns[name]) < 2),
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
    amount = amounts["Credit"] if "Credit" in amounts else -amounts["Debit"]
    return {
        "date": _transaction_date(" ".join(word["text"] for word in words[:2]), start, end),
        "description": " ".join(description),
        "amount": amount,
        "statement_balance": amounts.get("Balance"),
    }


def _read_rows(pdf: pdfplumber.PDF, start: date, end: date, opening: Decimal) -> list[dict]:
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
            if text == "Date Description Credit Debit Balance":
                if finished:
                    raise ValueError(f"Page {page.page_number}: table continues after the opening balance")
                columns = {word["text"]: word["x1"] for word in words}
                description_left = next(word["x0"] for word in words if word["text"] == "Description")
                in_table = saw_table = True
                continue
            if not in_table:
                continue
            if text.startswith("Australia and New Zealand Banking Group"):
                break
            if text.startswith("Opening Balance"):
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
                    current["description"] += " " + text
            except ValueError as error:
                raise ValueError(f"Page {page.page_number}, transaction {len(rows) + 1}: {error}") from None

    if current is not None:
        rows.append(current)
    # Statement validation
    if saw_table and not finished:
        raise ValueError("Transaction table has no final opening balance")
    if not saw_table and "no transactions to display" not in pdf.pages[0].extract_text().lower():
        raise ValueError("Neither a transaction table nor an explicit empty-statement message was found")
    return rows


def _check_balances(
    transactions: list[Transaction], opening: Decimal, closing: Decimal, bank_details: BankDetails,
    positions: list[tuple[int, int]],
) -> None:
    """
    Check total movement and available running balances in ANZ's newest-first printed order.

    ANZ prints transfers within one bank account as offsetting rows without running balances. Validate their
    pairs separately and retain both rows; their positions do not affect the bank account's running balance.
    positions supplies temporary (page, row) pairs for error messages, without storing them on Transactions.

    Raises:
        ValueError: The extracted rows do not reconcile with the bank's reported balances.
    """
    # Statement total
    if opening + sum((item.amount for item in transactions), Decimal("0.00")) != closing:
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
        if item.statement_balance is not None and item.statement_balance != expected:
            raise ValueError(
                f"Running balance does not reconcile at page {page}, transaction {number}"
            )
        expected -= item.amount
