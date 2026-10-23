# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Finance - Dates                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum accepts day-first and ISO dates wherever the user enters a structured accounting date.

Manual CSV dates and account closing dates share this parser. Valid input becomes a Python date, so database
values and generated dates keep the same YYYY-MM-DD representation regardless of the input format.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date
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
