# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Finance - Securities                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Securities identify the shares and crypto held inside Aurum's asset accounts.

A security describes the instrument, independently of the account that holds it. The same security can appear
in several accounts, while different markets may use the same trading symbol. Holdings and their cost come
from journal activity rather than balances stored on these definitions.

This file keeps the security catalogue in a read-only registry and loads it from private YAML configuration.
Missing UUIDs are saved only after the complete catalogue validates, so security identities remain stable
between runs. An optional provider symbol connects the instrument to the separate market-price workflow.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Mapping
from enum import Enum
import io
import os
from pathlib import Path
import stat
import tempfile
from types import MappingProxyType
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

# Default paths
DEFAULT_SECURITIES = Path(__file__).resolve().parents[2] / "config" / "securities.yaml"


# %% 1. Securities -------------------------------------------------------------------------------------------
class SecurityType(Enum):
    """
    Distinguish company shares from crypto instruments.

    SHARE describes an investment instrument, not the equity account type used for net worth.
    """

    SHARE = "share"
    CRYPTO = "crypto"


class Security(BaseModel):
    """
    Identify one investment instrument independently of the accounts that hold it.

    Attributes:
        key: Stable, case-sensitive configuration key.
        name: Readable instrument name.
        id: Persistent UUID, generated when a new definition omits it.
        security_type: Share or crypto classification.
        symbol: Trading symbol, qualified by the market and security type.
        market: Exchange or instrument namespace that distinguishes otherwise identical symbols.
        currency: AUD, the supported bookkeeping and quote currency for this first version.
        price_symbol: Optional Yahoo identifier; otherwise use an AUD crypto pair or an ASX .AX symbol.

    A registry rejects duplicate instruments after ignoring case and surrounding whitespace in market and
    symbol. It preserves the original text for display. To change a security, validate a replacement model
    and rebuild the registry, retaining its UUID.

    Raises:
        ValidationError: Required text is blank, currency is unsupported, or a field is invalid.
    """

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    # Identity
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    id: UUID = Field(default_factory=uuid4)

    # Instrument details
    security_type: SecurityType
    symbol: str = Field(min_length=1)
    market: str = Field(min_length=1)
    currency: Literal["AUD"] = "AUD"
    price_symbol: str | None = None

    # Validation
    @field_validator("key", "name", "symbol", "market", "price_symbol")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        """
        Reject whitespace-only text while preserving valid values and optional None.
        """
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value


class SecurityRegistry:
    """
    Keep security definitions in read-only lookups by configuration key and UUID.

    Args:
        securities: Security objects in configuration order.

    Raises:
        TypeError: The input is not a list of Security objects.
        ValueError: A key, UUID or instrument identity is duplicated.
    """

    def __init__(self, securities: list[Security]) -> None:
        """
        Build the lookups, rejecting duplicate keys, UUIDs and instruments.
        """
        if not isinstance(securities, list):
            raise TypeError("securities must be a list of Security objects")

        # Security lookups
        by_key: dict[str, Security] = {}
        by_identifier: dict[UUID, Security] = {}
        identities: set[tuple[SecurityType, str, str]] = set()
        for security in securities:
            if not isinstance(security, Security):
                raise TypeError("securities must contain only Security objects")
            security = Security.model_validate(security)
            if security.key in by_key:
                raise ValueError(f"Duplicate security key: {security.key!r}")
            if security.id in by_identifier:
                raise ValueError(f"Duplicate security id: {security.id}")
            identity = (
                security.security_type,
                security.market.strip().casefold(),
                security.symbol.strip().casefold(),
            )
            if identity in identities:
                raise ValueError(
                    f"Duplicate security instrument: {security.market!r} / {security.symbol!r}"
                )
            by_key[security.key] = security
            by_identifier[security.id] = security
            identities.add(identity)

        self._securities: Mapping[str, Security] = MappingProxyType(by_key)
        self._by_id: Mapping[UUID, Security] = MappingProxyType(by_identifier)

    @property
    def securities(self) -> Mapping[str, Security]:
        """
        Return the read-only security lookup by configuration key, in input order.
        """
        return self._securities

    def by_id(self, identifier: UUID) -> Security:
        """
        Find a security by its persistent UUID.

        Raises:
            KeyError: The UUID is not present in this registry.
        """
        return self._by_id[identifier]


# %% 2. Security Configuration -------------------------------------------------------------------------------
def load_securities(
    path: Path | str = DEFAULT_SECURITIES, *, initialise_ids: bool = True,
) -> SecurityRegistry:
    """
    Load a YAML security catalogue and save missing UUIDs after every definition validates.

    Omitted or null IDs receive new UUIDs. Existing IDs, comments, quotes and field order are retained.
    An empty list is a valid catalogue. A catalogue with all IDs already present is not rewritten.

    Args:
        path: Private .yaml or .yml file containing a list of security definitions.
        initialise_ids: Save missing IDs when True; reject them without writing when False.

    Returns:
        A registry with unique keys, UUIDs and instrument identities.

    Raises:
        OSError: The file cannot be read or generated identities cannot be saved.
        ValueError: The configuration is invalid or changes while generated IDs are being saved.
    """
    path = Path(path).resolve()
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Security configuration must use a .yaml or .yml extension")
    original = path.read_bytes()

    # Preserve the user's YAML layout when generated identities are added.
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = False
    yaml.width = 110
    try:
        definitions = yaml.load(original.decode("utf-8"))
    except YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line, column = getattr(mark, "line", None), getattr(mark, "column", None)
        location = ""
        if isinstance(line, int) and isinstance(column, int):
            location = f" at line {line + 1}, column {column + 1}"
        raise ValueError(
            f"Invalid YAML security configuration{location}; check syntax and duplicate keys"
        ) from None
    if not isinstance(definitions, list):
        raise ValueError("Security configuration must contain a list of securities")

    # Validate the complete catalogue before changing any persisted identities.
    securities = []
    generated = []
    seen_definitions = set()
    for index, fields in enumerate(definitions, start=1):
        if not isinstance(fields, dict):
            raise ValueError(f"Security {index} must be a mapping of field names to values")
        if id(fields) in seen_definitions:
            raise ValueError("Each security needs its own definition; do not repeat a mapping alias")
        seen_definitions.add(id(fields))
        missing_id = fields.get("id") is None
        if missing_id and not initialise_ids:
            raise ValueError(
                f"Security {index} needs a saved UUID; load securities normally before read-only use"
            )
        values = {key: value for key, value in fields.items() if key != "id"} if missing_id else fields
        security = Security.model_validate(values)
        securities.append(security)
        if missing_id:
            own_fields = dict(fields.non_merged_items()) if isinstance(fields, CommentedMap) else fields
            generated.append((fields, security.id, "id" in own_fields))
    registry = SecurityRegistry(securities)

    if generated:
        for fields, identifier, has_id in generated:
            if isinstance(fields, CommentedMap) and not has_id:
                # Inserting first keeps section comments attached to their existing final fields.
                fields.insert(0, "id", str(identifier))
            else:
                fields["id"] = str(identifier)
        stream = io.StringIO()
        yaml.dump(definitions, stream)
        _save_security_ids(path, stream.getvalue().encode("utf-8"), original)
    return registry


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _save_security_ids(path: Path, content: bytes, original: bytes) -> None:
    """
    Save validated identities atomically while preserving permissions and detecting content changes.

    A failed save returns no registry, so unsaved UUIDs cannot be used for journal postings.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != original:
            raise ValueError("Security configuration changed while UUIDs were being saved; load it again")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
