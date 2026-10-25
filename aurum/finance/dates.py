# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Finance - Dates                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum accepts day-first and ISO dates wherever the user enters a structured accounting date.

Manual CSV dates and account closing dates share this parser. Valid input becomes a Python date, so database
values and generated dates keep the same YYYY-MM-DD representation regardless of the input format.

Accounting reports use working months that finish on the last Monday-to-Friday day. Any weekend after that
cutoff belongs to the following reporting month; public holidays do not change these boundaries.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from calendar import monthrange
from datetime import date, timedelta
import re


# %% 1. Date Inputs ------------------------------------------------------------------------------------------
def parse_input_date(value: object) -> date | None:
    """
    Accept a native date, an optional None, or a valid YYYY-MM-DD or DD-MM-YYYY string.

    Raises:
        ValueError: The value has an unsupported format or is not a real calendar date.
    """
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
                return date.fromisoformat(text)
            if re.fullmatch(r"[0-9]{2}-[0-9]{2}-[0-9]{4}", text):
                day, month, year = map(int, text.split("-"))
                return date(year, month, day)
        except ValueError:
            pass
    raise ValueError("Enter a valid calendar date as YYYY-MM-DD or DD-MM-YYYY")


# %% 2. Reporting Months -------------------------------------------------------------------------------------
def working_month_end(year: int, month: int) -> date:
    """
    Return the final Monday-to-Friday date in a calendar month, without holiday adjustments.
    """
    last_day = date(year, month, monthrange(year, month)[1])
    return last_day - timedelta(days=max(0, last_day.weekday() - 4))


def working_month_start(year: int, month: int) -> date:
    """
    Return the day after the preceding working month ends, which can fall in the previous calendar month.
    """
    first_day = date(year, month, 1)
    if first_day == date.min:
        return first_day
    previous = first_day - timedelta(days=1)
    return working_month_end(previous.year, previous.month) + timedelta(days=1)


def reporting_month(value: date) -> tuple[int, int]:
    """
    Find the reporting year and month containing a date, rolling final weekends into the following month.
    """
    if value <= working_month_end(value.year, value.month):
        return value.year, value.month
    return (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
