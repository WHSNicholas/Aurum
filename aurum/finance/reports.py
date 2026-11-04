# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Finance - Reports                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum builds its financial statements and investment report from validated, posted journal entries.

Reporting months finish on the last Monday-to-Friday day. The balance sheet includes all earlier postings,
while the income statement includes only the selected month. Current reporting-year earnings appear in
Retained Earnings; earlier earnings appear in Capital. These are presentation calculations, with no closing
entries or changes to the journal.

Security holdings can be valued with supplied AUD market quotes. Their recorded cost is replaced only within
the report, with the same change added to Unrealised Capital Gains. Accounts without security movements keep
their journal balances. This file calculates reports and validates settings; it never fetches prices, writes
configuration or renders PDFs.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# Aurum imports
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.budget import BudgetReport, BudgetSettings, build_budget_report
from aurum.finance.dates import reporting_month, working_month_end, working_month_start
from aurum.finance.investments import PriceQuote, holdings_as_of, price_symbol
from aurum.finance.journal import JournalEntry
from aurum.finance.savings import SavingsAllocation
from aurum.finance.securities import SecurityRegistry

# Default paths and money
DEFAULT_REPORT_SETTINGS = Path(__file__).resolve().parents[2] / "config" / "reports.yaml"
Money = Annotated[Decimal, Field(decimal_places=2, allow_inf_nan=False)]
HexColour = Annotated[str, Field(min_length=7, max_length=7, pattern=r"^#[0-9a-fA-F]{6}$", strict=True)]
TransparentColour = Annotated[
    str, Field(min_length=7, max_length=9, pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", strict=True),
]
ColourScheme = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", strict=True)]


# %% 1. Report Models ----------------------------------------------------------------------------------------
class ReportColours(BaseModel):
    """
    Configure text, icons and status colours for a shared page mode or one fixed theme.

    Missing heading, group and normal-icon colours follow the selected theme's primary shade. Missing chart
    text follows the body text. Progress tracks and fills accept an optional final alpha byte in their hex
    colour; all other fields are opaque six-digit colours. Defaults preserve the existing light reports.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    # Text and icons
    heading_text: HexColour | None = None
    group_text: HexColour | None = None
    icon: HexColour | None = None
    chart_text: HexColour | None = None
    text: HexColour = "#2c3e50"
    muted: HexColour = "#6c757d"

    # Text on coloured bands and cards
    on_primary: HexColour = "#ffffff"
    on_table_header: HexColour = "#ffffff"
    on_highlight: HexColour = "#ffffff"

    # Gains, losses and budget remaining amounts
    positive: HexColour = "#35a062"
    negative: HexColour = "#b1453a"
    remaining: HexColour = "#000000"
    budget_income: HexColour = "#2f6289"
    budget_expenses: HexColour = "#8c3f3b"
    budget_savings: HexColour = "#23744f"

    # Budget progress pills
    income_track: TransparentColour = "#60a5d226"
    income_fill: TransparentColour = "#468cc3b3"
    expenses_track: TransparentColour = "#dc695f26"
    expenses_fill: TransparentColour = "#c85a55b3"
    savings_track: TransparentColour = "#41b98226"
    savings_fill: TransparentColour = "#32a573b3"

    # Draft notices and contrast-selected chart values
    draft_bg: HexColour = "#fdf7ea"
    draft_text: HexColour = "#694d1d"
    draft_border: HexColour = "#dbc38a"
    bar_text_dark: HexColour = "#000000"
    bar_text_light: HexColour = "#ffffff"


DEFAULT_DARK_COLOURS = ReportColours(
    text="#e6e9ed", heading_text="#e6e9ed", group_text="#e6e9ed", icon="#e6e9ed",
    muted="#b3bec8", positive="#84d0a5", negative="#e7948d", remaining="#e6e9ed",
    budget_income="#83b9e2", budget_expenses="#efa09b", budget_savings="#83d3aa",
    draft_bg="#373025", draft_text="#ead4a4", draft_border="#796641",
)


class ReportColourModes(BaseModel):
    """
    Keep shared light and dark colours together without changing any theme's palette or backgrounds.

    Partial dark-mode configuration inherits the dark defaults, so changing one colour preserves readable
    text and indicators elsewhere. Both modes are immutable and reject unknown colour names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    light: ReportColours = Field(default_factory=ReportColours)
    dark: ReportColours = DEFAULT_DARK_COLOURS

    @field_validator("dark", mode="before")
    @classmethod
    def _dark_defaults(cls, value: object) -> object:
        """
        Fill omitted dark-mode fields from the dark defaults before validating supplied colours.
        """
        if isinstance(value, Mapping):
            return {**DEFAULT_DARK_COLOURS.model_dump(), **value}
        return value


class ReportAppearance(BaseModel):
    """
    Keep a theme's backgrounds, dividers and text colours independent of the global page mode.

    All four surface colours are explicit. Omitted text, icon and status colours use ReportColours defaults,
    never the user's shared light/dark colour settings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    page: HexColour
    surface: HexColour
    divider: HexColour
    group: TransparentColour
    colours: ReportColours = Field(default_factory=ReportColours)


class ReportTheme(BaseModel):
    """
    Set a report's accent palette with separate light/dark backgrounds or one fixed appearance.

    Palette colours run from darkest to lightest, with at least three quoted six-digit hex values. The first
    three shades colour major headings, highlighted cards and column headings unless explicitly overridden.
    Total bands share the column-heading colours. Highlight_text overrides highlighted-card subtext;
    otherwise it uses the last palette shade. Group colours shade asset-class rows with optional alpha bytes.
    Page colours cover the whole sheet, including margins; surfaces fill cards and tables.
    Divider colours apply to header, footer and table-row lines. Cards and panels have no visible outlines.
    Omitted background and divider fields use the default light and dark colours. An optional fixed appearance
    supplies backgrounds and text colours independently of dark_mode and the shared light/dark settings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    palette: tuple[HexColour, ...] = Field(min_length=3)
    primary: HexColour | None = None
    table_header: HexColour | None = None
    highlight: HexColour | None = None
    highlight_text: HexColour | None = None
    fixed: ReportAppearance | None = None
    light_page: HexColour = "#ffffff"
    light_surface: HexColour = "#f8f9fa"
    light_divider: HexColour = "#9aa6ad"
    light_group: TransparentColour | None = None
    dark_page: HexColour = "#191A1C"
    dark_surface: HexColour = "#27282B"
    dark_divider: HexColour = "#46515c"
    dark_group: TransparentColour = "#344047"


# Defaults keep existing reports working when a theme is omitted from configuration.
DEFAULT_THEMES: Mapping[ColourScheme, ReportTheme] = MappingProxyType({
    "teal": ReportTheme(
        palette=("#144848", "#1d6767", "#257f7f", "#2d9898", "#35b0b0", "#4fcaca", "#72d6d6", "#96e3e3"),
        highlight_text="#d7e3e3",
    ),
    "navy_blue": ReportTheme(
        palette=("#182f47", "#244763", "#346081", "#497b9c", "#6a96b3", "#92b4c9", "#bed2de"),
    ),
    "plum_purple": ReportTheme(
        palette=("#302440", "#49365a", "#644b78", "#816795", "#a088b1", "#bcacc9", "#dbd0e2"),
    ),
    "forest_green": ReportTheme(
        palette=("#1a372a", "#2a513c", "#406d53", "#5d886d", "#7ea28d", "#a5bfad", "#cfddd3"),
    ),
    "burgundy": ReportTheme(
        palette=("#462331", "#623545", "#82465a", "#9f6374", "#bb8793", "#d2adb5", "#e8d4d8"),
    ),
    "copper": ReportTheme(
        palette=("#462e22", "#654431", "#875e43", "#a37a5e", "#be997d", "#d7bca4", "#eadacf"),
    ),
})


class ReportSettings(BaseModel):
    """
    Configure report presentation, colour scheme, asset groups and whole-account investments.

    Account references are stable configuration keys, not UUIDs. Omitted asset groups leave all assets under
    Other Assets. Account investments map an asset key to an investment-report group, using its posted
    balance and explicitly classified contribution cost, without assumed units. investment_symbols override
    their investment-table labels without renaming accounts in other reports. Mappings are immutable.
    The colour scheme controls report accents and charts; budget section colours retain their meaning.
    Themes map each colour scheme to an editable palette and separate light/dark page and surface colours.
    An omitted built-in theme uses its default colours; custom theme names require a configured definition.
    Shared colours configure text, icons and indicators separately for light and dark mode. Themes with a
    fixed appearance use their own backgrounds and colours regardless of the global mode or shared settings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    name: str = "Aurum"
    output_directory: str = Field(default="data/reports", min_length=1)
    colour_scheme: ColourScheme = "teal"
    dark_mode: bool = Field(default=False, strict=True)
    themes: Mapping[ColourScheme, ReportTheme] = Field(default_factory=dict)
    colours: ReportColourModes = Field(default_factory=ReportColourModes)
    capital_account: str = "capital"
    retained_earnings_account: str = "retained_earnings"
    unrealised_gains_account: str = "unrealised_capital_gains"
    asset_groups: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    account_investments: Mapping[str, str] = Field(default_factory=dict)
    investment_symbols: Mapping[str, str] = Field(default_factory=dict)

    @property
    def owner_name(self) -> str:
        """
        Return the personal name, accepting earlier configuration that included the Aurum prefix.
        """
        name = self.name.strip()
        if name == "Aurum":
            return ""
        if name.startswith("Aurum |"):
            return name.partition("|")[2].strip()
        return name

    @property
    def display_name(self) -> str:
        """
        Brand the configured personal name consistently in report headers and footers.
        """
        return f"Aurum | {self.owner_name}" if self.owner_name else "Aurum"

    @property
    def theme(self) -> ReportTheme:
        """
        Return the selected configured theme, falling back to its built-in colours when omitted.
        """
        if self.colour_scheme in self.themes:
            return self.themes[self.colour_scheme]
        return DEFAULT_THEMES[self.colour_scheme]

    @property
    def mode_colours(self) -> ReportColours:
        """
        Use a fixed theme's own colours, or the shared colours for the selected page mode.
        """
        if self.theme.fixed is not None:
            return self.theme.fixed.colours
        return self.colours.dark if self.dark_mode else self.colours.light

    @field_serializer("themes")
    def _serialise_themes(self, value: Mapping[str, ReportTheme]) -> dict[str, dict[str, object]]:
        """
        Export immutable themes as ordinary YAML/JSON-compatible mappings.
        """
        return {scheme: colours.model_dump() for scheme, colours in value.items()}

    @field_validator(
        "name", "output_directory", "capital_account", "retained_earnings_account",
        "unrealised_gains_account",
    )
    @classmethod
    def _check_text(cls, value: str) -> str:
        """
        Reject blank display names and account keys.
        """
        if not value.strip():
            raise ValueError("Report names and account keys must not be blank")
        return value

    @field_validator("asset_groups")
    @classmethod
    def _freeze_groups(cls, value: Mapping[str, tuple[str, ...]]) -> Mapping[str, tuple[str, ...]]:
        """
        Preserve group order while rejecting blank labels and repeated account assignments.
        """
        seen = set()
        for label, keys in value.items():
            if not label.strip():
                raise ValueError("Asset group labels must not be blank")
            for key in keys:
                if not key.strip() or key in seen:
                    raise ValueError("Every grouped asset key must be nonblank and assigned only once")
                seen.add(key)
        return MappingProxyType(dict(value))

    @field_serializer("asset_groups")
    def _serialise_groups(self, value: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        """
        Expose immutable groups as ordinary YAML/JSON-compatible mappings when requested.
        """
        return dict(value)

    @field_validator("account_investments", "investment_symbols")
    @classmethod
    def _freeze_investment_labels(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        """
        Keep whole-account investment groups and symbol overrides nonblank and immutable.
        """
        if any(not key.strip() or not group.strip() for key, group in value.items()):
            raise ValueError("Account investment keys and display labels must not be blank")
        return MappingProxyType(dict(value))

    @field_serializer("account_investments", "investment_symbols")
    def _serialise_investment_labels(self, value: Mapping[str, str]) -> dict[str, str]:
        """
        Expose investment display mappings as ordinary dictionaries when serialising settings.
        """
        return dict(value)

    @model_validator(mode="after")
    def _check_theme(self) -> Self:
        """
        Require the selected colour scheme to have a configured or built-in theme.
        """
        if self.colour_scheme not in self.themes and self.colour_scheme not in DEFAULT_THEMES:
            choices = ", ".join(sorted(set(DEFAULT_THEMES) | set(self.themes)))
            raise ValueError(
                f"Unknown report colour_scheme {self.colour_scheme!r}; "
                f"choose {choices} or add it under themes"
            )
        return self

    @model_validator(mode="after")
    def _check_equity_keys(self) -> Self:
        """
        Keep the three equity presentation roles separate.
        """
        keys = (self.capital_account, self.retained_earnings_account, self.unrealised_gains_account)
        if len(set(keys)) != 3:
            raise ValueError("Capital, Retained Earnings and Unrealised Capital Gains need distinct accounts")
        if set(self.investment_symbols) - set(self.account_investments):
            raise ValueError("Investment symbol overrides must name configured whole-account investments")
        # Pydantic does not run field validators for an omitted default mapping.
        object.__setattr__(self, "themes", MappingProxyType(dict(self.themes)))
        object.__setattr__(self, "asset_groups", MappingProxyType(dict(self.asset_groups)))
        object.__setattr__(self, "account_investments", MappingProxyType(dict(self.account_investments)))
        object.__setattr__(self, "investment_symbols", MappingProxyType(dict(self.investment_symbols)))
        return self


class ReportPeriod(BaseModel):
    """
    Identify a complete working month and the date through which this run can report.

    end_date is the scheduled weekday month end; cutoff is earlier for a month-to-date report.
    year_start follows the same working-month rule as January, including any preceding December weekend.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    month: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")
    start_date: date
    end_date: date
    cutoff: date
    year_start: date
    is_partial: bool

    @model_validator(mode="after")
    def _check_boundaries(self) -> Self:
        """
        Require dates that describe the selected reporting month consistently.
        """
        year, month = map(int, self.month.split("-"))
        if (
            self.start_date != working_month_start(year, month)
            or self.end_date != working_month_end(year, month)
            or self.year_start != working_month_start(year, 1)
            or not self.start_date <= self.cutoff <= self.end_date
            or self.is_partial != (self.cutoff < self.end_date)
        ):
            raise ValueError("Report period dates do not match its working month")
        return self


class ReportRow(BaseModel):
    """
    Keep one account's current display name, signed report amount and optional asset group together.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    amount: Money
    group: str | None = None


class BalanceSheet(BaseModel):
    """
    Describe financial position at one cutoff, including report-only earnings and market-value adjustments.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: date
    assets: tuple[ReportRow, ...]
    liabilities: tuple[ReportRow, ...]
    equity: tuple[ReportRow, ...]
    total_assets: Money
    total_liabilities: Money
    total_equity: Money
    net_worth: Money
    entry_count: int = Field(ge=0)
    notes: tuple[str, ...] = ()
    draft_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_totals(self) -> Self:
        """
        Require exact section totals and Assets = Liabilities + Equity.
        """
        asset_cents = sum(_cents(row.amount) for row in self.assets)
        liability_cents = sum(_cents(row.amount) for row in self.liabilities)
        equity_cents = sum(_cents(row.amount) for row in self.equity)
        if (
            _cents(self.total_assets) != asset_cents
            or _cents(self.total_liabilities) != liability_cents
            or _cents(self.total_equity) != equity_cents
            or asset_cents != liability_cents + equity_cents
            or _cents(self.net_worth) != asset_cents - liability_cents
        ):
            raise ValueError("The balance sheet does not reconcile exactly")
        return self


class IncomeStatement(BaseModel):
    """
    Describe period income, expenses and net income, preserving refunds, reversals and losses.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: date
    end_date: date
    period_label: str
    income: tuple[ReportRow, ...]
    expenses: tuple[ReportRow, ...]
    total_income: Money
    total_expenses: Money
    net_income: Money
    entry_count: int = Field(ge=0)
    notes: tuple[str, ...] = ()
    draft_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_totals(self) -> Self:
        """
        Require exact account totals and Net Income = Income - Expenses.
        """
        income_cents = sum(_cents(row.amount) for row in self.income)
        expense_cents = sum(_cents(row.amount) for row in self.expenses)
        if (
            self.start_date > self.end_date
            or _cents(self.total_income) != income_cents
            or _cents(self.total_expenses) != expense_cents
            or _cents(self.net_income) != income_cents - expense_cents
        ):
            raise ValueError("Income statement dates or totals are inconsistent")
        return self


class InvestmentRow(BaseModel):
    """
    Show a security holding or a whole-account investment such as Superannuation.

    Security gains compare current value with carrying cost, excluding distributions and realised gains.
    Whole-account gains compare recorded value with net contributions and explicit opening cost, including
    separately recorded fund earnings. Neither percentage is annualised or time weighted.
    Missing security prices leave valuation fields blank. Whole-account investments instead show their
    recorded journal balance without traded units or quotes. An account with no postings has zero cost and
    gain. Unclassified activity leaves contribution cost and gains unknown until explicitly supported.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_key: str
    account_name: str
    security_key: str | None = None
    name: str
    symbol: str = ""
    asset_class: str
    units: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    average_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_basis: Money | None = Field(default=None, ge=0)
    recorded_value: Money | None = None
    price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    price_date: date | None = None
    market_value: Money | None = None
    unrealised_gain: Money | None = None
    return_percentage: Decimal | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_valuation(self) -> Self:
        """
        Keep account balances separate from security quantities and require consistent valuation figures.
        """
        values = (self.price, self.price_date, self.market_value, self.unrealised_gain)
        if self.security_key is None:
            security_fields = (
                self.units, self.average_cost, self.price, self.price_date, self.market_value,
            )
            if self.recorded_value is None or any(value is not None for value in security_fields):
                raise ValueError("An account investment needs a recorded balance without security quantities")
            if self.cost_basis is None:
                if self.unrealised_gain is not None or self.return_percentage is not None:
                    raise ValueError("Unknown contribution cost must leave the account's gain unknown")
            elif (
                self.unrealised_gain is None
                or _cents(self.unrealised_gain) != _cents(self.recorded_value) - _cents(self.cost_basis)
                or self.return_percentage != _gain_percentage(self.unrealised_gain, self.cost_basis)
            ):
                raise ValueError("Account investment value, contribution cost and gain must reconcile")
            return self
        if self.recorded_value is not None or any(
            value is None for value in (self.units, self.average_cost, self.cost_basis)
        ):
            raise ValueError("A security holding needs units and cost, not a whole-account recorded value")
        if any(value is None for value in values):
            if any(value is not None for value in values) or self.return_percentage is not None:
                raise ValueError("An unpriced holding must leave all valuation fields blank")
        elif (
            self.market_value < 0
            or _cents(self.unrealised_gain) != _cents(self.market_value) - _cents(self.cost_basis)
            or self.return_percentage != _gain_percentage(self.unrealised_gain, self.cost_basis)
        ):
            raise ValueError("Investment value, gain or gain percentage is inconsistent")
        return self

    @property
    def value(self) -> Decimal | None:
        """
        Return market value for a security or the explicitly identified recorded account balance.
        """
        return self.recorded_value if self.security_key is None else self.market_value

    @property
    def allocation_value(self) -> Decimal:
        """
        Allocate known security cost and whole-account recorded balances without depending on quotes.
        """
        return self.recorded_value if self.security_key is None else self.cost_basis


class InvestmentReport(BaseModel):
    """
    Summarise securities and configured account balances without inventing unknown cost or gain figures.

    Portfolio value includes quoted securities and explicitly recorded account balances. Missing security
    prices withhold that total; unknown contribution cost on an active account investment withholds cost
    and gain. Accounts with no journal activity contribute zero rather than hiding known portfolio totals.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: date
    positions: tuple[InvestmentRow, ...]
    total_cost: Money | None
    total_value: Money | None
    total_gain: Money | None
    return_percentage: Decimal | None = Field(default=None, allow_inf_nan=False)
    draft_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_totals(self) -> Self:
        """
        Keep distinct holdings and complete totals, without presenting a partial valuation as a full total.
        """
        identities = {(row.account_key, row.security_key) for row in self.positions}
        if len(identities) != len(self.positions):
            raise ValueError("An investment holding appears more than once")
        whole_accounts = {row.account_key for row in self.positions if row.security_key is None}
        if any(row.account_key in whole_accounts and row.security_key is not None for row in self.positions):
            raise ValueError("A whole-account investment cannot also be counted as separate securities")
        cost = (
            _money(sum(_cents(row.cost_basis) for row in self.positions))
            if all(row.cost_basis is not None for row in self.positions) else None
        )
        if self.total_cost != cost:
            raise ValueError("Investment cost does not match its holdings")
        value = (
            _money(sum(_cents(row.value) for row in self.positions))
            if all(row.value is not None for row in self.positions) else None
        )
        gain = _money(_cents(value) - _cents(cost)) if value is not None and cost is not None else None
        percent = _gain_percentage(gain, cost) if gain is not None else None
        if self.total_value != value or self.total_gain != gain or self.return_percentage != percent:
            raise ValueError("Investment totals do not reconcile with their holdings")
        return self


class Reports(BaseModel):
    """
    Keep all four reports produced from one journal snapshot and one set of closing prices together.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    balance_sheet: BalanceSheet
    income_statement: IncomeStatement
    budget_report: BudgetReport
    investment_report: InvestmentReport


# %% 2. Report Configuration ---------------------------------------------------------------------------------
def load_report_settings(path: Path | str, accounts: AccountRegistry) -> ReportSettings:
    """
    Read report presentation settings without generating IDs or changing configuration.

    Raises:
        OSError: The YAML file cannot be read.
        ValueError: YAML, account references, equity roles or group assignments are invalid.
    """
    path = Path(path)
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Report configuration must use a .yaml or .yml extension")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        fields = yaml.load(path.read_text(encoding="utf-8"))
    except YAMLError:
        raise ValueError("Invalid report YAML; check syntax and duplicate keys") from None
    if not isinstance(fields, dict):
        raise ValueError("Report configuration must contain a mapping of settings")
    settings = ReportSettings.model_validate(fields)
    _validate_settings(settings, accounts)
    return settings


# %% 3. Report Calculations ----------------------------------------------------------------------------------
def resolve_period(
    entries: Iterable[JournalEntry], month: str | None = None, today: date | None = None,
) -> ReportPeriod:
    """
    Select an explicit working month or the month containing the latest posted accounting date.

    Future periods are rejected. A period that has started but has not ended stops at today. With no posted
    entries an explicit month is required, allowing deliberately empty statements to be generated.
    """
    today = today or date.today()
    if month is None:
        latest = max((entry.date for entry in entries), default=None)
        if latest is None:
            raise ValueError("There are no posted journal entries; choose an explicit --month YYYY-MM")
        year, month_number = reporting_month(latest)
        month = f"{year:04d}-{month_number:02d}"
    try:
        year_text, month_text = month.split("-")
        if len(year_text) != 4 or len(month_text) != 2 or not year_text.isdigit() or not month_text.isdigit():
            raise ValueError
        year, month_number = int(year_text), int(month_text)
        start = working_month_start(year, month_number)
        end = working_month_end(year, month_number)
    except (TypeError, ValueError):
        raise ValueError("Choose a valid reporting month as YYYY-MM") from None
    if start > today:
        raise ValueError("Future reporting months are not available")
    cutoff = min(end, today)
    return ReportPeriod(
        month=month, start_date=start, end_date=end, cutoff=cutoff,
        year_start=working_month_start(year, 1), is_partial=cutoff < end,
    )


def build_reports(
    entries: Iterable[JournalEntry],
    accounts: AccountRegistry,
    settings: ReportSettings,
    period: ReportPeriod,
    *,
    quotes: Mapping[UUID, PriceQuote],
    budget_settings: BudgetSettings,
    savings_allocations: Iterable[SavingsAllocation] = (),
    securities: SecurityRegistry | None = None,
    valuation_errors: Mapping[UUID, str] | None = None,
    draft_reasons: Iterable[str] = (),
) -> Reports:
    """
    Calculate all four reports from one validated posted-journal snapshot.

    Only entries through the accounting cutoff contribute. Quotes must identify the security and configured
    provider symbol, with their original request and close dates on or before the cutoff. The latest cached
    valuation may be older than the cutoff; its actual close date is retained. Unavailable quotes retain
    carrying cost and mark the balance sheet as draft; supplied draft reasons mark both statements.
    The journal, configuration and cache are read-only.

    Raises:
        ValueError: Settings, journal identities, investment history or report reconciliation are invalid.
    """
    _validate_settings(settings, accounts)
    period = ReportPeriod.model_validate(period)
    entries = _validate_entries(entries, accounts)
    included = tuple(entry for entry in entries if entry.date <= period.cutoff)
    current = tuple(entry for entry in included if entry.date >= period.start_date)
    account_lookup = {account.id: account for account in accounts.accounts.values()}
    balances = _balances(included)
    month_balances = _balances(current)
    equity_keys = (settings.capital_account, settings.retained_earnings_account)
    capital, retained = (accounts.accounts[key] for key in equity_keys)

    # Income and expenses retain their journal balances; earnings are presented only in equity.
    earlier_profit = 0
    current_profit = 0
    earlier_retained = 0
    for entry in included:
        for line in entry.lines:
            net_credit = _cents(line.credit) - _cents(line.debit)
            kind = account_lookup[line.account_id].account_type
            if kind in {AccountType.INCOME, AccountType.EXPENSE}:
                if entry.date < period.year_start:
                    earlier_profit += net_credit
                else:
                    current_profit += net_credit
            if line.account_id == retained.id and entry.date < period.year_start:
                earlier_retained += net_credit
    balances[capital.id] = balances.get(capital.id, 0) - earlier_profit - earlier_retained
    balances[retained.id] = balances.get(retained.id, 0) - current_profit + earlier_retained

    common_drafts = tuple(dict.fromkeys(draft_reasons))
    notes = [
        "Current reporting-year earnings are included in Retained Earnings; earlier earnings are in Capital.",
        "Earnings rollover and market valuations are report calculations; no closing entries are posted.",
    ]
    if period.is_partial:
        notes.insert(0, f"Month-to-date: {period.month}; the reporting month ends on {period.end_date}.")
    investment_report = _value_holdings(
        included, accounts, settings, period.cutoff, balances, quotes, securities,
        valuation_errors or {}, notes, common_drafts,
    )
    assets = _asset_rows(accounts, settings, balances)
    liabilities = _rows(accounts, AccountType.LIABILITY, balances)
    equity = _rows(accounts, AccountType.EQUITY, balances)
    income = _rows(accounts, AccountType.INCOME, month_balances)
    expenses = _rows(accounts, AccountType.EXPENSE, month_balances)
    asset_total = sum(_cents(row.amount) for row in assets)
    liability_total = sum(_cents(row.amount) for row in liabilities)
    equity_total = sum(_cents(row.amount) for row in equity)
    income_total = sum(_cents(row.amount) for row in income)
    expense_total = sum(_cents(row.amount) for row in expenses)
    month_name = date(int(period.month[:4]), int(period.month[5:]), 1).strftime("%B %Y")
    label = month_name
    return Reports(
        balance_sheet=BalanceSheet(
            as_of=period.cutoff, assets=assets, liabilities=liabilities, equity=equity,
            total_assets=_money(asset_total), total_liabilities=_money(liability_total),
            total_equity=_money(equity_total), net_worth=_money(asset_total - liability_total),
            entry_count=len(included), notes=tuple(notes),
            draft_reasons=investment_report.draft_reasons,
        ),
        income_statement=IncomeStatement(
            start_date=period.start_date, end_date=period.cutoff, period_label=label,
            income=income, expenses=expenses, total_income=_money(income_total),
            total_expenses=_money(expense_total), net_income=_money(income_total - expense_total),
            entry_count=len(current),
            notes=(
                "Amounts follow the journal accounting date and the displayed working-month boundaries.",
                "Gifts posted to Capital and unrealised market gains are excluded from income.",
            ),
            draft_reasons=common_drafts,
        ),
        budget_report=build_budget_report(
            included, accounts, budget_settings, period, allocations=savings_allocations,
            draft_reasons=common_drafts,
        ),
        investment_report=investment_report,
    )


def recorded_net_worth(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, cutoff: date,
) -> Decimal:
    """
    Return posted assets less liabilities through the cutoff using their recorded journal amounts.

    The caller supplies posted entries only. Budget exclusions do not remove assets from financial position,
    and security holdings retain their recorded value without looking up prices or applying market quotes.
    """
    validated = _validate_entries(entries, accounts)
    balances = _balances(entry for entry in validated if entry.date <= cutoff)
    # Liability debit balances already have the opposite sign to the amount owed.
    total = sum(
        balances.get(account.id, 0) for account in accounts.accounts.values()
        if account.account_type in {AccountType.ASSET, AccountType.LIABILITY}
    )
    return _money(total)


# %% 4. Helpers ----------------------------------------------------------------------------------------------
def _validate_settings(settings: ReportSettings, accounts: AccountRegistry) -> None:
    """
    Require existing equity presentation accounts and valid, uniquely grouped assets.
    """
    settings = ReportSettings.model_validate(settings)
    equity_keys = (
        settings.capital_account, settings.retained_earnings_account, settings.unrealised_gains_account,
    )
    for key in equity_keys:
        account = accounts.accounts.get(key)
        if account is None or account.account_type != AccountType.EQUITY:
            raise ValueError(f"Report equity setting must name an existing equity account: {key}")
    for keys in settings.asset_groups.values():
        for key in keys:
            account = accounts.accounts.get(key)
            if account is None or account.account_type != AccountType.ASSET:
                raise ValueError(f"Report asset group must name an existing asset account: {key}")
    for key in settings.account_investments:
        account = accounts.accounts.get(key)
        if account is None or account.account_type != AccountType.ASSET:
            raise ValueError(f"Account investments must name an existing asset account: {key}")


def _validate_entries(entries: Iterable[JournalEntry], accounts: AccountRegistry) -> tuple[JournalEntry, ...]:
    """
    Revalidate balances and identities before aggregating, rejecting duplicate events or source links.
    """
    by_id = {account.id: account for account in accounts.accounts.values()}
    result = []
    event_ids = set()
    source_ids = set()
    for supplied in entries:
        entry = JournalEntry.model_validate(supplied)
        if entry.id in event_ids:
            raise ValueError("The report snapshot contains a duplicate journal event")
        event_ids.add(entry.id)
        for line in entry.lines:
            account = by_id.get(line.account_id)
            if account is None:
                raise ValueError("A journal posting references an account missing from configuration")
            if line.transaction_id is not None:
                if line.transaction_id in source_ids:
                    raise ValueError("A source transaction is used more than once in the journal")
                source_ids.add(line.transaction_id)
            if line.investment is not None and (
                account.account_type != AccountType.ASSET or account.bank_details is not None
            ):
                raise ValueError("Investment units require a non-bank asset account")
            if line.account_investment is not None and (
                account.account_type != AccountType.ASSET or account.bank_details is not None
            ):
                raise ValueError("Whole-account investment treatment requires a non-bank asset account")
        result.append(entry)
    return tuple(result)


def _balances(entries: Iterable[JournalEntry]) -> dict[UUID, int]:
    """
    Accumulate signed debit balances as integer cents.
    """
    totals: dict[UUID, int] = {}
    for entry in entries:
        for line in entry.lines:
            movement = _cents(line.debit) - _cents(line.credit)
            totals[line.account_id] = totals.get(line.account_id, 0) + movement
    return totals


def _rows(
    accounts: AccountRegistry, kind: AccountType, balances: Mapping[UUID, int],
) -> tuple[ReportRow, ...]:
    """
    Create rows in account order, using the normal debit or credit sign for each section.

    Every configured account of this type remains visible, including accounts without any postings.
    """
    sign = 1 if kind in {AccountType.ASSET, AccountType.EXPENSE} else -1
    return tuple(
        ReportRow(key=account.key, name=account.name, amount=_money(sign * balances.get(account.id, 0)))
        for account in accounts.accounts.values()
        if account.account_type == kind
    )


def _asset_rows(
    accounts: AccountRegistry, settings: ReportSettings, balances: Mapping[UUID, int],
) -> tuple[ReportRow, ...]:
    """
    Show every configured asset, including zero balances, in group order.

    Assets without an assigned group appear under Other Assets.
    """
    groups = {name: list(keys) for name, keys in settings.asset_groups.items()}
    assigned = {key for keys in groups.values() for key in keys}
    unassigned = [
        account.key for account in accounts.accounts.values()
        if account.account_type == AccountType.ASSET and account.key not in assigned
    ]
    if unassigned:
        groups.setdefault("Other Assets", []).extend(unassigned)
    rows = []
    for group, keys in groups.items():
        for key in keys:
            account = accounts.accounts[key]
            amount = balances.get(account.id, 0)
            rows.append(ReportRow(key=key, name=account.name, amount=_money(amount), group=group))
    return tuple(rows)


def _value_holdings(
    entries: tuple[JournalEntry, ...],
    accounts: AccountRegistry,
    settings: ReportSettings,
    cutoff: date,
    balances: dict[UUID, int],
    quotes: Mapping[UUID, PriceQuote],
    securities: SecurityRegistry | None,
    errors: Mapping[UUID, str],
    notes: list[str],
    common_drafts: tuple[str, ...],
) -> InvestmentReport:
    """
    Value each holding once for both reports, preserving unrelated balances in its asset account.
    """
    holdings = holdings_as_of(entries, cutoff)
    by_id = {account.id: account for account in accounts.accounts.values()}
    unit_accounts = {holding.account_id for holding in holdings}
    for key in settings.account_investments:
        if accounts.accounts[key].id in unit_accounts:
            raise ValueError(
                f"Account {key!r} has security holdings; remove its whole-account investment setting"
            )
    gains_account = accounts.accounts[settings.unrealised_gains_account]
    ambiguous = _ambiguous_valuations(entries, {holding.account_id for holding in holdings}, gains_account.id)
    drafts = list(common_drafts)
    positions = []
    for account_id in sorted(ambiguous, key=str):
        drafts.append(
            f"{by_id[account_id].name}: existing manual valuation postings need review; "
            "the recorded account balance is retained."
        )
    for holding in holdings:
        account = by_id[holding.account_id]
        if securities is None:
            raise ValueError("The security catalogue is required for posted investment holdings")
        try:
            security = securities.by_id(holding.security_id)
        except KeyError:
            raise ValueError("Restore securities referenced by posted holdings before reporting") from None
        fields = {
            "account_key": account.key, "account_name": account.name,
            "security_key": security.key, "name": security.name, "symbol": security.symbol,
            "asset_class": "Shares" if security.security_type.value == "share" else "Cryptocurrencies",
            "units": holding.units, "average_cost": holding.average_cost, "cost_basis": holding.cost_basis,
        }
        if holding.account_id in ambiguous:
            positions.append(InvestmentRow(**fields))
            continue
        try:
            quote = quotes.get(holding.security_id)
            if quote is None:
                raise ValueError(errors.get(holding.security_id, "No suitable AUD market quote is available"))
            quote = PriceQuote.model_validate(quote)
            if (
                quote.security_id != security.id or quote.requested_date > cutoff or quote.price_date > cutoff
                or quote.symbol != price_symbol(security)
            ):
                raise ValueError("The supplied quote must match the security and be on or before the cutoff")
            with localcontext() as context:
                context.prec = max(
                    50, len(holding.units.as_tuple().digits) + len(quote.price.as_tuple().digits)
                    + max(0, holding.units.adjusted() + quote.price.adjusted()) + 5,
                )
                market_value = (holding.units * quote.price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            adjustment = _cents(market_value) - _cents(holding.cost_basis)
            gain = _money(adjustment)
            positions.append(InvestmentRow(
                **fields, price=quote.price, price_date=quote.price_date, market_value=market_value,
                unrealised_gain=gain, return_percentage=_gain_percentage(gain, holding.cost_basis),
            ))
            balances[account.id] = balances.get(account.id, 0) + adjustment
            balances[gains_account.id] = balances.get(gains_account.id, 0) - adjustment
            notes.append(
                f"{account.name} / {security.name}: AUD close dated {quote.price_date.isoformat()}; "
                f"market value {market_value:,.2f}, recorded cost {holding.cost_basis:,.2f}."
            )
        except ValueError as error:
            positions.append(InvestmentRow(**fields))
            drafts.append(f"{account.name} / {security.name}: {error}; market valuation is unavailable.")
    for key, group in settings.account_investments.items():
        account = accounts.accounts[key]
        cost = _account_investment_cost(entries, account.id)
        value = _money(balances.get(account.id, 0))
        gain = _money(_cents(value) - _cents(cost)) if cost is not None else None
        if cost is None:
            drafts.append(
                f"{account.name}: classify its investment movements as contributions, earnings or an opening "
                "position with historical cost. Its value is available, but cost and return are unknown."
            )
        positions.append(InvestmentRow(
            account_key=key, account_name=account.name, name=account.name,
            symbol=settings.investment_symbols.get(key, account.name),
            asset_class=group,
            recorded_value=value, cost_basis=cost, unrealised_gain=gain,
            return_percentage=_gain_percentage(gain, cost) if gain is not None else None,
        ))
    positions.sort(key=lambda row: (
        row.asset_class, -_cents(row.allocation_value), row.name, row.account_name,
    ))
    total_cost = (
        _money(sum(_cents(row.cost_basis) for row in positions))
        if all(row.cost_basis is not None for row in positions) else None
    )
    total_value = (
        _money(sum(_cents(row.value) for row in positions))
        if all(row.value is not None for row in positions) else None
    )
    total_gain = (
        _money(_cents(total_value) - _cents(total_cost))
        if total_value is not None and total_cost is not None else None
    )
    return InvestmentReport(
        as_of=cutoff, positions=tuple(positions), total_cost=total_cost,
        total_value=total_value, total_gain=total_gain,
        return_percentage=_gain_percentage(total_gain, total_cost) if total_gain is not None else None,
        draft_reasons=tuple(dict.fromkeys(drafts)),
    )


def _account_investment_cost(entries: tuple[JournalEntry, ...], account_id: UUID) -> Decimal | None:
    """
    Sum net contributions and supplied opening costs without counting fund earnings as new money.

    Every movement must be classified. An unexplained debit, withdrawal or charge leaves cost unknown;
    it is never inferred from a description, posting sign or the account's final balance.
    """
    cents = 0
    for entry in entries:
        for line in entry.lines:
            if line.account_id != account_id:
                continue
            details = line.account_investment
            if details is None or details.kind == "unclassified":
                return None
            if details.kind == "contribution":
                cents += _cents(line.debit)
            elif details.kind == "opening":
                cents += _cents(details.cost_basis)
    return _money(cents)


def _gain_percentage(gain: Decimal, cost: Decimal) -> Decimal | None:
    """
    Express unrealised gain as a percentage of positive carrying cost, without annualising it.
    """
    if cost == 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return gain / cost * 100


def _ambiguous_valuations(
    entries: tuple[JournalEntry, ...], holding_accounts: set[UUID], gains_account_id: UUID,
) -> set[UUID]:
    """
    Identify existing value-only holding movements offset against unrealised-gains equity.

    Such postings may already include a manual market adjustment. Their security allocation cannot be
    inferred safely, so automatic report valuation for the entire affected account is withheld.
    """
    ambiguous = set()
    for entry in entries:
        if any(line.account_id == gains_account_id for line in entry.lines):
            ambiguous.update(
                line.account_id for line in entry.lines
                if line.account_id in holding_accounts and line.investment is None
            )
    return ambiguous


def _cents(value: Decimal) -> int:
    """
    Convert a validated whole-cent Decimal to an exact integer without context-dependent rounding.
    """
    numerator, denominator = value.as_integer_ratio()
    cents, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise ValueError("Report money must contain whole cents")
    return cents


def _money(cents: int) -> Decimal:
    """
    Construct an exact two-decimal amount from integer cents, including very large balances.
    """
    digits = tuple(map(int, str(abs(cents))))
    return Decimal((int(cents < 0), digits, -2))
