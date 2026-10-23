# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     AI - Privacy                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum replaces configured bank identifiers with readable labels before sending text to AI services.

The same redaction is used for transaction descriptions, account notes and review instructions. Original
wording stays in the local records; this helper changes only the outbound copy.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import re

# Aurum imports
from aurum.finance.accounts import AccountRegistry


# %% 1. Bank Identifiers -------------------------------------------------------------------------------------
def redact_bank_details(text: str, accounts: AccountRegistry) -> str:
    """
    Replace known bank identifiers with labels, including common space and hyphen formatting.

    Digit boundaries prevent replacing a shorter identifier within a longer number. A single substitution
    pass keeps replacement labels from being processed again and favours the longest matching identifier.
    """
    labels: dict[str, str] = {}
    for index, account in enumerate(accounts.accounts.values(), start=1):
        details = account.bank_details
        if details is not None:
            labels.setdefault(details.account_number, f"[bank account {index}]")
            labels.setdefault(details.bsb, "[bank routing code]")
    if not labels:
        return text

    identifiers = sorted(labels, key=len, reverse=True)
    alternatives = [
        rf"(?P<bank_{index}>(?<![0-9])"
        + r"[ \t-]*".join(re.escape(digit) for digit in identifier)
        + r"(?![0-9]))"
        for index, identifier in enumerate(identifiers)
    ]
    pattern = re.compile("|".join(alternatives))
    return pattern.sub(lambda match: labels[identifiers[int(match.lastgroup.removeprefix("bank_"))]], text)


