# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     AI - Skills                                                      #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum loads its accounting instructions from project skills instead of Python prompts.

The journal command loads each skill once and shares it across a run. Content-derived versions record the
instructions used without resending previously processed events. Loading reads only the named local files,
without discovering unrelated skills, following links or reading private guidance.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from hashlib import sha256
from pathlib import Path
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

# Aurum imports
from aurum.ai.privacy import redact_bank_details
from aurum.finance.accounts import AccountRegistry

if TYPE_CHECKING:
    from aurum.finance.review import Guidance

DEFAULT_SKILL = Path(__file__).resolve().parents[2] / ".agents/skills/aurum-categorisation/SKILL.md"
DEFAULT_MANUAL_SKILL = Path(__file__).resolve().parents[2] / ".agents/skills/aurum-manual-entries/SKILL.md"


# %% 1. Skill Loading ----------------------------------------------------------------------------------------
class Skill(BaseModel):
    """
    Keep the loaded skill body and its reproducible instruction version together.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instructions: str = Field(min_length=1)
    version: str = Field(min_length=1)


def load_categorisation_skill(path: Path | str = DEFAULT_SKILL) -> Skill:
    """
    Read accounting instructions and their content version for categorisation and human review.

    Args:
        path: Skill file, normally resolved from Aurum's project directory.

    Returns:
        The skill body without discovery frontmatter, plus its content-derived version.

    Raises:
        OSError: The skill file cannot be read.
        ValueError: Its expected name, description or instruction body is absent.
    """
    return _load_skill(path, "aurum-categorisation")


def load_manual_skill(path: Path | str = DEFAULT_MANUAL_SKILL) -> Skill:
    """
    Read manual-entry interpretation instructions and their content version.
    """
    return _load_skill(path, "aurum-manual-entries")


def guidance_context(
    guidance: list[Guidance] | None, accounts: AccountRegistry, context: str,
) -> tuple[list[dict], dict[str, Guidance]]:
    """
    Include a small selection of private guidance without sending its database or evidence identifiers.

    Shared words rank context relevance only; ChatGPT still decides whether guidance applies. At most twelve
    items are sent. Unknown account references are omitted until their guidance can be corrected locally.

    Returns:
        Compact guidance records and the local mapping needed to validate explicit supersession references.
    """
    keys = {account.id: key for key, account in accounts.accounts.items()}
    words = set(re.findall(r"[a-z0-9]{3,}", context.casefold()))
    candidates = [
        item for item in (guidance or []) if all(identifier in keys for identifier in item.account_ids)
    ]
    ranked = sorted(
        candidates,
        key=lambda item: len(words & set(re.findall(r"[a-z0-9]{3,}", item.scope.casefold()))),
        reverse=True,
    )[:12]
    references = {f"g{index}": item for index, item in enumerate(ranked, start=1)}
    payload = [
        {
            "guidance_ref": reference,
            "scope": redact_bank_details(item.scope, accounts),
            "instruction": redact_bank_details(item.instruction, accounts),
            "account_keys": [keys[identifier] for identifier in item.account_ids],
        }
        for reference, item in references.items()
    ]
    return payload, references


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _load_skill(path: Path | str, name: str) -> Skill:
    """
    Validate a named skill's discovery fields and version its exact contents, returning only its body.
    """
    text = Path(path).read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"The {name} skill needs YAML frontmatter")
    # Project skills use two simple, single-line discovery fields.
    fields = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    if fields.get("name") != name or not fields.get("description"):
        raise ValueError(f"The {name} skill needs its expected name and a description")
    instructions = parts[2].strip()
    if not instructions:
        raise ValueError(f"The {name} skill needs an instruction body")
    version = name + "-v1:" + sha256(text.encode("utf-8")).hexdigest()[:12]
    return Skill(instructions=instructions, version=version)
