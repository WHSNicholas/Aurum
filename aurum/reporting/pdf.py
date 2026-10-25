# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Reporting - PDF                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Turn Aurum's accounting, budget and investment results into four matching PDF reports.

The presentation follows Eigen One's cards, serif headings and paired account tables, using the configured
colour scheme. Fonts, icons and charts are embedded locally, so rendering needs no network connection. This
module only formats validated report results; the finance and workflow modules decide balances, dates,
prices and draft status.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------

# Imports
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from html import escape
from io import BytesIO
from math import cos, pi, sin
import os
from pathlib import Path
import sys
from textwrap import wrap
from urllib.parse import unquote, urlparse

from fontTools.ttLib import TTFont
from PIL import Image

# Aurum imports
from aurum.finance.budget import BudgetReport, BudgetRow
from aurum.finance.reports import (
    BalanceSheet, IncomeStatement, InvestmentReport, InvestmentRow, ReportRow, Reports, ReportSettings,
)

ASSETS = Path(__file__).resolve().parent / "assets"
STYLES = Path(__file__).with_name("styles.css")
CHART_FONT_SIZE = 16

# Keep the supplied silhouettes; normal icons are tinted in memory to match each palette or page mode.
ICON_FILES = {
    "assets": "asset.png",
    "liabilities": "liability.png",
    "net_worth": "net_worth.png",
    "income": "income.png",
    "expenses": "expense.png",
    "net_income": "net_income.png",
    "market_value": "market_value.png",
    "unrealised_gain": "unrealised_gain.png",
}
BUNDLED_ASSETS = ("Geist.ttf", "GeistMono.ttf", "Cormorant.ttf", *ICON_FILES.values())
HIGHLIGHT_ICONS = {"net_worth.png", "net_income.png", "unrealised_gain.png"}


# %% 1. PDF Reports ------------------------------------------------------------------------------------------

def render_reports(
    reports: Reports, settings: ReportSettings, *, generated_on: date | None = None,
) -> tuple[bytes, bytes, bytes, bytes]:
    """
    Render the balance sheet, income statement, budget and investment report before returning their PDF bytes.

    All resources must come from the bundled assets. Missing fonts, icons or styles raise an error rather than
    silently producing a different report. The caller saves the returned bytes after all four renders succeed.
    Every footer uses the same local generation date; supply generated_on for a reproducible rendering.
    """
    generated_on = generated_on if generated_on is not None else date.today()
    if not STYLES.is_file():
        raise FileNotFoundError("Missing bundled report stylesheet: styles.css")
    for filename in BUNDLED_ASSETS:
        if not (ASSETS / filename).is_file():
            raise FileNotFoundError(f"Missing bundled report asset: {filename}")
    html_type, css_type, font_type, asset_fetcher = _pdf_engine(settings)
    fonts = font_type()
    stylesheet = css_type(
        string=STYLES.read_text(encoding="utf-8") + _theme_styles(settings),
        base_url=STYLES.parent.as_uri() + "/",
        url_fetcher=asset_fetcher,
        font_config=fonts,
    )
    documents = (
        _balance_sheet_html(reports.balance_sheet, settings, generated_on),
        _income_statement_html(reports.income_statement, settings, generated_on),
        _budget_report_html(reports.budget_report, settings, generated_on),
        _investment_report_html(reports.investment_report, settings, generated_on),
    )
    rendered = tuple(
        html_type(string=document, url_fetcher=asset_fetcher).write_pdf(
            stylesheets=[stylesheet], font_config=fonts,
        )
        for document in documents
    )
    return rendered[0], rendered[1], rendered[2], rendered[3]


# %% 2. Helpers ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _DisplayRow:
    """
    Keep a rendered table label, value and grouping style together during pagination.
    """

    name: str
    amount: Decimal
    group: bool = False
    indent: bool = False


def _asset_path(url: str) -> Path:
    """
    Allow only the named local fonts and icons, preventing rendering from reading arbitrary files or URLs.
    """
    parsed = urlparse(url)
    path = Path(unquote(parsed.path)).resolve()
    if (
        parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment
        or path.parent != ASSETS or path.name not in BUNDLED_ASSETS
    ):
        raise ValueError("PDF rendering only permits the bundled report fonts and icons")
    return path


def _theme_styles(settings: ReportSettings) -> str:
    """
    Combine the selected theme with the configured light/dark text, indicators and other shared colours.

    Palette shades supply unset accent overrides. Group backgrounds are explicit in dark mode so their
    headings remain visible against panel surfaces. No report colours are chosen by the stylesheet.
    A fixed appearance supplies its own backgrounds and shared colours, ignoring the light/dark switch.
    """
    theme = settings.theme
    mode = settings.mode_colours
    palette = theme.palette
    primary = theme.primary or palette[0]
    colours = {
        name.replace("_", "-"): value for name, value in mode.model_dump().items() if value is not None
    }
    colours.update({
        "primary": primary,
        "table-header": theme.table_header or palette[2],
        "highlight": theme.highlight or palette[1],
        "highlight-text": theme.highlight_text or palette[-1],
        "group": theme.dark_group if settings.dark_mode else (theme.light_group or primary + "14"),
        "heading-text": mode.heading_text or primary,
        "group-text": mode.group_text or primary,
        "page": theme.dark_page if settings.dark_mode else theme.light_page,
        "surface": theme.dark_surface if settings.dark_mode else theme.light_surface,
        "divider": theme.dark_divider if settings.dark_mode else theme.light_divider,
    })
    if theme.fixed is not None:
        colours.update({
            "page": theme.fixed.page, "surface": theme.fixed.surface,
            "divider": theme.fixed.divider, "group": theme.fixed.group,
        })
    return "\n:root { " + " ".join(f"--report-{name}: {value};" for name, value in colours.items()) + " }\n"


def _bar_text_colour(colour: str, dark_text: str, light_text: str) -> str:
    """
    Choose whichever of the two configured chart-value colours contrasts best with the bar's fill.
    """
    background = _colour_luminance(colour)
    dark = _colour_luminance(dark_text)
    light = _colour_luminance(light_text)
    dark_contrast = (max(background, dark) + .05) / (min(background, dark) + .05)
    light_contrast = (max(background, light) + .05) / (min(background, light) + .05)
    return dark_text if dark_contrast >= light_contrast else light_text


def _colour_luminance(colour: str) -> float:
    """
    Measure an opaque hex colour's relative brightness for chart-label contrast.
    """
    channels = [int(colour[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def _expense_colour(palette: Sequence[str], index: int, count: int) -> str:
    """
    Space expense colours across the selected scale, blending adjacent shades instead of cycling back.

    A single bar uses the first shade. Multiple bars span the full scale, even when there are more bars than
    colours. Both page modes run from the darkest shade to the lightest.
    """
    position = index * (len(palette) - 1) / max(1, count - 1)
    lower = int(position)
    upper = min(lower + 1, len(palette) - 1)
    fraction = position - lower
    channels = []
    for offset in (1, 3, 5):
        start = int(palette[lower][offset:offset + 2], 16)
        end = int(palette[upper][offset:offset + 2], 16)
        channels.append(round(start + (end - start) * fraction))
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _tinted_icon(path: Path, colour: str) -> bytes:
    """
    Recolour a bundled icon while preserving its original transparent silhouette and file on disk.
    """
    with Image.open(path) as original:
        tinted = Image.new("RGBA", original.size, colour)
        tinted.putalpha(original.convert("RGBA").getchannel("A"))
    output = BytesIO()
    tinted.save(output, format="PNG")
    return output.getvalue()


def _pdf_engine(settings: ReportSettings) -> tuple:
    """
    Load WeasyPrint and supply local fonts, tinted icons and solid table dividers for the selected theme.
    """
    if sys.platform == "darwin":
        variable = "DYLD_FALLBACK_LIBRARY_PATH"
        directories = [directory for directory in os.environ.get(variable, "").split(":") if directory]
        for directory in ("/opt/homebrew/lib", "/usr/local/lib"):
            if Path(directory).is_dir() and directory not in directories:
                directories.append(directory)
        os.environ[variable] = ":".join(directories)
    try:
        from weasyprint import CSS, HTML
        from weasyprint.text.fonts import FontConfiguration
        from weasyprint.urls import URLFetcher, URLFetcherResponse
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "PDF rendering requires WeasyPrint and Pango. Install requirements.txt in Aurum's .venv; "
            "on macOS, install the native libraries with brew install pango."
        ) from error
    # Thin gradient backgrounds can fill entire rows in Preview. Bounded SVG rectangles stay one pixel high.
    theme = settings.theme
    divider = (
        theme.fixed.divider if theme.fixed is not None
        else theme.dark_divider if settings.dark_mode else theme.light_divider
    )
    dividers = {}
    for side, offset in (("middle", "0"), ("left", "4pt"), ("right", "-4pt")):
        dividers[f"aurum-divider:{side}"] = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="1">'
            f'<rect x="{offset}" width="100%" height="1" fill="{divider}"/>'
            '</svg>'
        ).encode("utf-8")

    class LocalAssetFetcher(URLFetcher):
        """
        Restrict WeasyPrint 70's resource loader to Aurum's named report assets and generated divider lines.
        """

        def fetch(self, url, headers=None):
            """
            Supply known divider lines and bundled resources, tinting icons for their summary cards.
            """
            if url in dividers:
                return URLFetcherResponse(
                    url, body=dividers[url], headers={"Content-Type": "image/svg+xml"},
                )
            path = _asset_path(url)
            if path.suffix == ".png":
                mode = settings.mode_colours
                colour = (
                    mode.on_highlight if path.name in HIGHLIGHT_ICONS
                    else mode.icon or settings.theme.primary or settings.theme.palette[0]
                )
                # A memory URL prevents WeasyPrint from reopening the untinted image from its file path.
                return URLFetcherResponse(
                    f"aurum-icon:{path.name}", body=_tinted_icon(path, colour),
                    headers={"Content-Type": "image/png"},
                )
            return super().fetch(url, headers)

    return HTML, CSS, FontConfiguration, LocalAssetFetcher(allowed_protocols={"file"}, fail_on_errors=True)


def _balance_sheet_html(statement: BalanceSheet, settings: ReportSettings, generated_on: date) -> str:
    """
    Arrange assets beside liabilities and equity, with matched continuation pages when tables grow.
    """
    debt_ratio = (
        f"Debt Ratio: {statement.total_liabilities / statement.total_assets * 100:.1f}%"
        if statement.total_assets > 0 else "Assets less liabilities"
    )
    cards = _cards((
        ("assets", "Total Assets", statement.total_assets, _count(len(statement.assets), "account")),
        (
            "liabilities", "Total Liabilities", statement.total_liabilities,
            _count(len(statement.liabilities), "account"),
        ),
        ("net_worth", "Net Worth", statement.net_worth, debt_ratio),
    ))
    first_capacity = 18 if statement.draft_reasons else 19
    left = _column_pages(
        (("Assets", _display_rows(statement.assets), statement.total_assets),), first_capacity, 25,
    )
    right = _column_pages((
        ("Liabilities", _display_rows(statement.liabilities), statement.total_liabilities),
        ("Equity", _display_rows(statement.equity), statement.total_equity),
    ), first_capacity, 25)
    return _document(
        "Balance Sheet", f"Statement of Financial Position as of {_date(statement.as_of, superscript=True)}",
        settings.name, cards, left, right, statement.draft_reasons, generated_on, "balance-sheet",
    )


def _income_statement_html(statement: IncomeStatement, settings: ReportSettings, generated_on: date) -> str:
    """
    Present income and expenses with the original layout's expense chart and summary statistics.
    """
    savings_rate = (
        f"Savings Rate: {statement.net_income / statement.total_income * 100:.1f}%"
        if statement.total_income > 0 else "Income less expenses"
    )
    cards = _cards((
        ("income", "Total Income", statement.total_income, _count(len(statement.income), "category")),
        ("expenses", "Total Expenses", statement.total_expenses, _count(len(statement.expenses), "category")),
        ("net_income", "Net Income", statement.net_income, savings_rate),
    ))
    chart = _expense_chart(
        statement.expenses, settings.theme.palette,
        text_colour=settings.mode_colours.chart_text or settings.mode_colours.text,
        bar_text_colours=(settings.mode_colours.bar_text_dark, settings.mode_colours.bar_text_light),
    )
    first_capacity = 15 if chart else 19
    if statement.draft_reasons:
        first_capacity -= 1
    left = _column_pages(
        (("Income", _display_rows(statement.income), statement.total_income),), first_capacity, 25,
    )
    right = _column_pages(
        (("Expenses", _display_rows(statement.expenses), statement.total_expenses),), first_capacity, 25,
    )
    return _document(
        "Income Statement", f"Statement of Financial Performance for {escape(statement.period_label)}",
        settings.name, cards + chart, left, right, statement.draft_reasons, generated_on, "income-statement",
    )


def _document(
    title: str,
    subtitle_html: str,
    name: str,
    summary: str,
    left: list[str],
    right: list[str],
    draft_reasons: Sequence[str],
    generated_on: date,
    report_class: str,
    *,
    full_width: bool = False,
) -> str:
    """
    Add headers, draft notices and a running footer, using subtitle markup prepared by the report helpers.
    """
    page_count = max(len(left), len(right))
    pages = []
    for index in range(page_count):
        continuation = " (continued)" if index else ""
        header = (
            f'<header class="header"><h1>{escape(title)}</h1>'
            f'<p class="subtitle">{subtitle_html}{continuation}</p>'
            f'<p class="author">{escape(name)}</p>'
            '<hr class="header-divider"></header>'
        )
        draft = ""
        if draft_reasons:
            draft = (
                '<div class="draft"><strong>Draft - incomplete information.</strong> '
                'See the report command output for details.</div>'
            )
        first_content = summary if index == 0 else ""
        left_content = left[index] if index < len(left) else ""
        right_content = right[index] if index < len(right) else ""
        body = (
            f'<div class="full-width">{left_content}</div>' if full_width else
            f'<div class="two-column"><div class="column">{left_content}</div>'
            f'<div class="column">{right_content}</div></div>'
        )
        pages.append(
            f'<section class="report-page">{header}{draft}{first_content}{body}</section>'
        )
    footer = f'<footer>{escape(name)} | Generated {_date(generated_on)}</footer>'
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<title>{escape(title)} - {escape(name)}</title></head><body class="{escape(report_class)}">'
        + footer + "".join(pages) + "</body></html>"
    )


def _cards(
    items: Sequence[tuple[str, str, Decimal | None, str]], *, signed_last: bool = False, missing: str = "N/A",
) -> str:
    """
    Build the three familiar summary cards, highlighting the final net value.
    """
    cards = []
    for index, (icon, label, amount, detail) in enumerate(items):
        highlight = " highlight" if index == 2 else ""
        compact = " compact" if len(_money(amount, missing=missing)) > 15 else ""
        value = _signed_money(amount) if signed_last and index == 2 else _money(amount, missing=missing)
        drawing = (
            f'<img class="summary-icon" src="{(ASSETS / ICON_FILES[icon]).as_uri()}" '
            f'alt="{escape(label)}">'
        )
        cards.append(
            f'<div class="summary-card{highlight}">{drawing}'
            f'<div class="summary-label">{escape(label)}</div>'
            f'<div class="summary-value{compact}">{value}</div>'
            f'<div class="summary-subtext">{escape(detail)}</div></div>'
        )
    return '<div class="summary-grid">' + "".join(cards) + "</div>"


def _display_rows(rows: Sequence[ReportRow]) -> list[_DisplayRow]:
    """
    Add an asset-group subtotal before its account rows while preserving configured ordering.
    """
    result = []
    groups = dict.fromkeys(row.group for row in rows if row.group)
    if groups:
        for group in groups:
            members = [row for row in rows if row.group == group]
            with localcontext() as context:
                context.prec = max(
                    28,
                    max(len(row.amount.as_tuple().digits) for row in members) + len(str(len(members))) + 1,
                )
                subtotal = sum((row.amount for row in members), Decimal(0))
            result.append(_DisplayRow(group, subtotal, group=True))
            result.extend(_DisplayRow(row.name, row.amount, indent=True) for row in members)
        result.extend(_DisplayRow(row.name, row.amount) for row in rows if not row.group)
    else:
        result.extend(_DisplayRow(row.name, row.amount) for row in rows)
    return result


def _column_pages(
    sections: Sequence[tuple[str, list[_DisplayRow], Decimal]], first_capacity: int, later_capacity: int,
) -> list[str]:
    """
    Keep tables within a page-sized row budget, repeating headings and saving totals for their final part.

    Long account names consume additional row space. Explicitly limiting each column avoids a large flex
    container being clipped at an A4 boundary when the paired panels are stretched to the same height.
    """
    pages = [""]
    used = 0
    for title, rows, total in sections:
        position = 0
        while True:
            capacity = first_capacity if len(pages) == 1 else later_capacity
            if used + 5 > capacity:
                pages.append("")
                used = 0
                capacity = later_capacity
            budget = capacity - used - 3
            selected = []
            weight = 0
            while position + len(selected) < len(rows):
                row = rows[position + len(selected)]
                cost = max(1, len(wrap(row.name, width=40)))
                if row.group and position + len(selected) + 1 < len(rows):
                    following = rows[position + len(selected) + 1]
                    required = cost + max(1, len(wrap(following.name, width=40)))
                else:
                    required = cost
                if selected and weight + required > budget:
                    break
                selected.append(row)
                weight += cost
                if weight >= budget:
                    break
            finished = position + len(selected) == len(rows)
            heading = title + (" (continued)" if position else "")
            pages[-1] += _table(heading, selected, total if finished else None, title)
            used += weight + 3
            position += len(selected)
            if finished:
                break
            pages.append("")
            used = 0
    return pages


def _table(title: str, rows: Sequence[_DisplayRow], total: Decimal | None, total_label: str) -> str:
    """
    Render one bounded account table with aligned money and an optional final total.
    """
    body = []
    for row in rows:
        style = "group" if row.group else "indent" if row.indent else ""
        negative = ' class="negative"' if row.amount < 0 and not row.group else ""
        body.append(
            f'<tr class="{style}"><td>{escape(row.name)}</td><td{negative}>{_money(row.amount)}</td></tr>'
        )
    if not rows:
        body.append('<tr><td class="empty">No balances</td><td>$0.00</td></tr>')
    total_bar = ""
    if total is not None:
        total_bar = (
            f'<div class="section-total"><span>Total {escape(total_label)}</span>'
            f'<span>{_money(total)}</span></div>'
        )
    return (
        f'<section class="section"><h2 class="section-title">{escape(title)}</h2>'
        '<div class="section-content"><table><thead><tr><th>Account</th><th>Amount</th></tr></thead>'
        '<tbody>' + "".join(body) + f"</tbody></table>{total_bar}</div></section>"
    )


def _expense_chart(
    expenses: Sequence[ReportRow], palette: Sequence[str], *, text_colour: str,
    bar_text_colours: tuple[str, str],
) -> str:
    """
    Draw up to eight positive expense categories as local SVG bars, keeping refunds in the statement.
    """
    positive = sorted((row for row in expenses if row.amount > 0), key=lambda row: row.amount, reverse=True)
    if not positive:
        return ""
    denominator = sum((row.amount for row in positive), Decimal(0))
    shown = positive[:8]
    largest = shown[0].amount
    height = len(shown) * 46 + 16
    drawing = []
    # Read the bundled font's natural character widths; labels must never stretch to fill the gutter.
    with TTFont(ASSETS / "Geist.ttf") as font:
        scale = CHART_FONT_SIZE / font["head"].unitsPerEm
        advances = {
            chr(code): font["hmtx"].metrics[glyph][0] * scale
            for code, glyph in font.getBestCmap().items()
        }
    for index, row in enumerate(shown):
        colour = _expense_colour(palette, index, len(shown))
        y = 8 + index * 46
        width = float(row.amount / largest * 894)
        labels = _chart_labels(row.name, advances)
        percent = row.amount / denominator * 100
        percentage = "<1%" if percent < 1 else f"{percent:.0f}%"
        value = f"${row.amount:.0f} ({percentage})"
        # The 1050-unit chart scales the reference's 16-unit lettering to about 6 CSS pixels on the page.
        # A shared minimum width fits any percentage and keeps the bars ordered by size.
        if width < len(value) * 10 + 24:
            value = percentage
        width = max(width, 64)
        for line, label in enumerate(labels):
            label_y = y + 23 if len(labels) == 1 else y + 14 + line * 18
            drawing.append(
                f'<text x="110" y="{label_y}" text-anchor="end" fill="{text_colour}" '
                f'font-family="Geist" font-size="{CHART_FONT_SIZE}">{escape(label)}</text>'
            )
        right = 126 + width
        # Only the right-hand corners curve; the left edge stays at the common bar baseline.
        outline = (
            f"M126 {y} H{right - 16:.2f} A16 16 0 0 1 {right:.2f} {y + 16} "
            f"V{y + 18} A16 16 0 0 1 {right - 16:.2f} {y + 34} H126 Z"
        )
        drawing.append(
            f'<path d="{outline}" fill="{colour}"/>'
            f'<text x="138" y="{y + 23}" text-anchor="start" '
            f'fill="{_bar_text_colour(colour, *bar_text_colours)}" '
            f'font-family="Geist Mono" font-size="{CHART_FONT_SIZE}">{escape(value)}</text>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1050 {height}" '
        f'role="img" aria-label="Largest expense categories">{"".join(drawing)}</svg>'
    )
    return '<section class="chart"><h2 class="chart-title">Expense Breakdown</h2>' + svg + '</section>'


def _chart_labels(name: str, advances: Mapping[str, float]) -> list[str]:
    """
    Fit a category into two natural-width lines, shortening overflow while keeping its full name in the table.
    """
    def width(text: str) -> float:
        """
        Measure text at the chart's font size, reserving one em for any unsupported character.
        """
        return sum(advances.get(character, CHART_FONT_SIZE) for character in text)

    words = name.split()
    first = []
    # Prefer whole words on the first line. A single oversized word is shortened rather than split awkwardly.
    while words and width(" ".join([*first, words[0]])) <= 104:
        first.append(words.pop(0))
    if words and not first:
        first.append(words.pop(0))
    lines = []
    for label in (" ".join(first), " ".join(words)):
        if not label:
            continue
        if width(label) > 104:
            while label and width(label.rstrip() + "...") > 104:
                label = label[:-1]
            label = label.rstrip() + "..."
        lines.append(label)
    return lines


def _money(amount: Decimal | None, *, missing: str = "N/A") -> str:
    """
    Format signed AUD amounts consistently, using the report's chosen marker for unavailable values.
    """
    if amount is None:
        return missing
    return f"-${amount.copy_abs():.2f}" if amount < 0 else f"${amount:.2f}"


def _signed_money(amount: Decimal | None) -> str:
    """
    Add an explicit plus to positive investment gains while keeping missing figures unavailable.
    """
    return "+" + _money(amount) if amount is not None and amount > 0 else _money(amount, missing="-")


def _date(value: date, *, superscript: bool = False) -> str:
    """
    Format an ordinal date, optionally raising its suffix for the balance sheet subtitle.
    """
    suffix = "th" if 11 <= value.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(value.day % 10, "th")
    ending = f"<sup>{suffix}</sup>" if superscript else suffix
    return f"{value.day}{ending} {value:%B %Y}"


def _count(number: int, noun: str) -> str:
    """
    Format account and category counts with the correct singular or plural label.
    """
    plural = "categories" if noun == "category" else noun + "s"
    return f"{number} {noun if number == 1 else plural}"


# %% 3. Budget Reports ---------------------------------------------------------------------------------------
def _budget_report_html(report: BudgetReport, settings: ReportSettings, generated_on: date) -> str:
    """
    Compare monthly actuals with configured targets, placing income and savings beside expenses.
    """
    cards = _cards((
        ("income", "Income", report.total_income, _budget_target(report.income_target)),
        ("expenses", "Expenses", report.total_expenses, _budget_target(report.expense_target)),
        ("net_income", "Savings", report.total_savings, _budget_target(report.savings_target)),
    ))
    first_capacity = 19 if report.draft_reasons else 20
    left = _budget_pages((
        ("Income", report.income, report.total_income),
        ("Savings", report.savings, report.total_savings),
    ), first_capacity)
    right = _budget_pages((("Expenses", report.expenses, report.total_expenses),), first_capacity)
    return _document(
        "Budget Report", f"Summary of Budget Allocation for {escape(report.period_label)}", settings.name,
        cards, left, right, report.draft_reasons, generated_on, "budget-report",
    )


def _budget_target(value: Decimal | None) -> str:
    """
    Distinguish an unset target from a deliberately configured zero budget.
    """
    return "Budget: Not set" if value is None else f"Budget: {_money(value)}"


def _budget_pages(
    sections: Sequence[tuple[str, Sequence[BudgetRow], Decimal]], first_capacity: int,
) -> list[str]:
    """
    Split narrow budget tables conservatively, repeating headings and keeping totals on their final part.
    """
    pages = [""]
    used = 0
    for title, rows, total in sections:
        position = 0
        while True:
            capacity = first_capacity if len(pages) == 1 else 24
            if used + 5 > capacity:
                pages.append("")
                used = 0
                capacity = 24
            selected = []
            weight = 0
            for row in rows[position:]:
                # Allow for wrapped 9px labels and vertical padding within the 32px minimum row height.
                lines = max(1, len(wrap(row.name, width=12)))
                cost = (lines * 13 + 8 + 31) // 32
                if selected and weight + cost > capacity - used - 3:
                    break
                selected.append(row)
                weight += cost
            finished = position + len(selected) == len(rows)
            heading = title + (" (continued)" if position else "")
            pages[-1] += _budget_table(heading, selected, total if finished else None, title)
            used += max(1, weight) + 3
            position += len(selected)
            if finished:
                break
            pages.append("")
            used = 0
    return pages


def _budget_table(
    title: str, rows: Sequence[BudgetRow], total: Decimal | None, kind: str,
) -> str:
    """
    Show actuals, targets and remaining amounts without treating an undefined percentage as zero.
    """
    body = []
    for row in rows:
        target = _money(row.target) if row.target is not None else "—"
        remaining = _money(row.remaining) if row.remaining is not None else "—"
        exceeded = row.remaining is not None and row.remaining < 0
        progress_fill = ""
        progress_title = "Budget not set" if row.target is None else "Zero budget"
        if row.progress is not None or exceeded:
            # A positive actual against a zero budget has no ratio, but has still exceeded the target.
            width = Decimal(100) if exceeded else min(Decimal(100), max(Decimal(0), row.progress))
            progress_title = f"{row.progress:.0f}%" if row.progress is not None else "Zero budget exceeded"
            progress_fill = f'<div class="progress-fill" style="width:{width:.2f}%"></div>'
        progress = f'<div class="progress-track" title="{progress_title}">{progress_fill}</div>'
        actual_style = " negative" if row.actual < 0 else ""
        remaining_style = " budget-remaining"
        if exceeded:
            remaining_style += " budget-remaining-negative"
        body.append(
            f'<tr><td>{escape(row.name)}</td><td class="numeric">{target}</td>'
            f'<td class="numeric{actual_style}">{_money(row.actual)}</td><td>{progress}</td>'
            f'<td class="numeric{remaining_style}">{remaining}</td></tr>'
        )
    if not body:
        body.append('<tr><td class="empty" colspan="5">No activity</td></tr>')
    total_bar = (
        f'<div class="section-total"><span>{escape(kind)}</span><span>{_money(total)}</span></div>'
        if total is not None else ""
    )
    return (
        f'<section class="section budget-{kind.lower()}"><h2 class="section-title">{escape(title)}</h2>'
        '<div class="section-content"><table class="budget-table"><colgroup>'
        '<col style="width:22%"><col style="width:18%"><col style="width:18%">'
        '<col style="width:20%"><col style="width:22%"></colgroup><thead><tr>'
        '<th>Category</th><th>Budget</th><th>Actual</th><th>Progress</th><th>Remaining</th></tr></thead>'
        '<tbody>' + "".join(body) + f'</tbody></table>{total_bar}</div></section>'
    )


# %% 4. Investment Reports -----------------------------------------------------------------------------------
def _investment_report_html(report: InvestmentReport, settings: ReportSettings, generated_on: date) -> str:
    """
    Present account-specific holdings, their cost allocation and available market valuations.
    """
    recorded = any(row.security_key is None for row in report.positions)
    cards = _cards((
        ("assets", "Total Invested", report.total_cost, _count(len(report.positions), "position")),
        (
            "market_value", "Portfolio Value" if recorded else "Market Value", report.total_value,
            "Includes recorded balances" if recorded else
            _count(len({row.account_key for row in report.positions}), "account"),
        ),
        (
            "unrealised_gain", "Unrealised P&L", report.total_gain,
            f"Return: {_percentage(report.return_percentage)}",
        ),
    ), signed_last=True, missing="-")
    groups = [
        (name, tuple(sorted(
            (row for row in report.positions if row.asset_class == name),
            key=lambda row: row.allocation_value, reverse=True,
        )))
        for name in dict.fromkeys(row.asset_class for row in report.positions)
    ]
    groups.sort(key=lambda group: _sum_values(row.allocation_value for row in group[1]), reverse=True)
    chart = _allocation_chart(
        groups, settings.theme.palette,
        text_colour=settings.mode_colours.chart_text or settings.mode_colours.text,
    )
    capacity = (12 if chart else 20) - 2 * bool(report.draft_reasons)
    pages = _investment_pages(report, groups, capacity)
    return _document(
        "Investment Report", f"Summary of Investment Portfolio as of {_date(report.as_of, superscript=True)}",
        settings.name, cards + chart, pages, [], report.draft_reasons, generated_on,
        "investment-report", full_width=True,
    )


def _investment_pages(
    report: InvestmentReport, groups: Sequence[tuple[str, Sequence[InvestmentRow]]], first_capacity: int,
) -> list[str]:
    """
    Keep holdings and group headings together, with repeated column labels on every continuation page.
    """
    bodies = [[]]
    used = 0
    counts = Counter(row.security_key for row in report.positions if row.security_key is not None)
    for name, positions in groups:
        for index, row in enumerate(positions):
            show_account = counts[row.security_key] > 1
            lines = len(wrap(row.account_name, width=18)) if show_account else 0
            if row.security_key is None:
                lines += max(0, len(wrap(row.symbol or row.account_name, width=18)) - 1)
            cost = max(1, (26 + lines * 10 + 31) // 32)
            capacity = first_capacity if len(bodies) == 1 else 25
            heading_needed = index == 0 or not bodies[-1]
            # Reserve one row for a final total, avoiding a separate totals-only page.
            if bodies[-1] and used + cost + int(heading_needed) + 1 > capacity:
                bodies.append([])
                used = 0
                heading_needed = True
            if heading_needed:
                label = name + (" (continued)" if index else "")
                bodies[-1].append(_investment_group(label, positions))
                used += 1
            bodies[-1].append(_investment_row(row, show_account=show_account))
            used += cost
    if not report.positions:
        bodies[0].append('<tr><td class="empty" colspan="8">No investment holdings</td></tr>')
    bodies[-1].append(_investment_totals(
        "Grand Total", report.total_cost, report.total_value, report.total_gain,
        report.return_percentage, "portfolio-total",
    ))
    return [
        '<section class="section"><h2 class="section-title">Portfolio Holdings'
        + (' (continued)' if index else '')
        + '</h2><div class="section-content"><table class="investment-table"><thead><tr>'
        '<th>Symbol</th><th>Units</th><th>Avg Cost</th><th>Price</th>'
        '<th>Cost Basis</th><th>Value</th><th>P&amp;L</th><th>Return</th></tr></thead>'
        '<tbody>' + "".join(body) + '</tbody></table></div></section>'
        for index, body in enumerate(bodies)
    ]


def _investment_group(name: str, positions: Sequence[InvestmentRow]) -> str:
    """
    Sum a class of holdings, withholding its valuation if any member lacks a price.
    """
    cost = _sum_values(row.cost_basis for row in positions)
    value = _sum_values(row.value for row in positions)
    gain = _sum_values(row.unrealised_gain for row in positions)
    with localcontext() as context:
        context.prec = 50
        percent = gain / cost * 100 if gain is not None and cost else None
    return _investment_totals(name, cost, value, gain, percent, "group")


def _investment_totals(
    name: str, cost: Decimal | None, value: Decimal | None, gain: Decimal | None,
    percent: Decimal | None, style: str,
) -> str:
    """
    Align class subtotals and the final portfolio total with the holding columns.
    """
    return (
        f'<tr class="{style}"><td>{escape(name)}</td><td></td><td></td><td></td>'
        f'<td class="numeric">{_money(cost, missing="-")}</td>'
        f'<td class="numeric">{_money(value, missing="-")}</td>'
        f'<td class="numeric">{_signed_money(gain)}</td><td class="numeric">{_percentage(percent)}</td></tr>'
    )


def _investment_row(row: InvestmentRow, *, show_account: bool = False) -> str:
    """
    Show security holdings and whole-account investments using only their supplied values.
    """
    detail = f'<span class="holding-detail">{escape(row.account_name)}</span>' if show_account else ""
    gain_style = ""
    if row.unrealised_gain is not None and row.unrealised_gain != 0:
        gain_style = " positive" if row.unrealised_gain > 0 else " negative"
    return (
        f'<tr class="indent"><td>{escape(row.symbol or row.account_name)}{detail}</td>'
        f'<td class="numeric">{_quantity(row.units)}</td>'
        f'<td class="numeric">{_quantity(row.average_cost, money=True)}</td>'
        f'<td class="numeric">{_quantity(row.price, money=True)}</td>'
        f'<td class="numeric">{_money(row.cost_basis, missing="-")}</td>'
        f'<td class="numeric">{_money(row.value, missing="-")}</td>'
        f'<td class="numeric{gain_style}">{_signed_money(row.unrealised_gain)}</td>'
        f'<td class="numeric{gain_style}">{_percentage(row.return_percentage)}</td></tr>'
    )


def _allocation_chart(
    groups: Sequence[tuple[str, Sequence[InvestmentRow]]], palette: Sequence[str],
    *, text_colour: str,
) -> str:
    """
    Show the reference's centred portfolio allocation donut using each holding's recorded allocation value.
    """
    allocations = [(name, _sum_values(row.allocation_value for row in rows)) for name, rows in groups]
    allocations = [(name, cost) for name, cost in allocations if cost > 0]
    if not allocations:
        return ""
    total = _sum_values(cost for _, cost in allocations)
    # Allow for each SVG's scale, then make allocation text 20% larger than the expense chart's text.
    font_size = CHART_FONT_SIZE * 420 / 1050 * .55 / .5 * 1.2
    circumference = 2 * pi * 72.5
    offset = 0.0
    drawing = []
    labels = []
    for index, (name, cost) in enumerate(allocations):
        fraction = float(cost / total)
        length = fraction * circumference
        colour = palette[index % len(palette)]
        angle = (offset + length / 2) / circumference * 2 * pi - pi / 2
        centre_x, centre_y = 210 + 2 * cos(angle), 140 + 2 * sin(angle)
        dash = (
            f'stroke-dasharray="{length:.5f} {circumference - length:.5f}" '
            f'stroke-dashoffset="{-offset:.5f}" ' if len(allocations) > 1 else ""
        )
        drawing.append(
            f'<circle cx="{centre_x:.3f}" cy="{centre_y:.3f}" r="72.5" fill="none" '
            f'stroke="{colour}" stroke-width="55" {dash}'
            f'transform="rotate(-90 {centre_x:.3f} {centre_y:.3f})"/>'
        )
        offset += length
        x, y = 210 + 122 * cos(angle), 140 + 122 * sin(angle)
        anchor = "middle" if abs(cos(angle)) < .3 else "start" if cos(angle) > 0 else "end"
        x = min(x, 412 - len(name) * 6) if anchor == "start" else x
        x = max(x, 8 + len(name) * 6) if anchor == "end" else x
        # Leave room for the top labels after moving the drawing upward inside its fixed frame.
        labels.append([name, fraction, x, min(254, max(26, y)), anchor])
    # Two small adjacent slices near the top share the outside margin instead of overlapping labels.
    for index, label in enumerate(labels):
        for earlier in labels[:index]:
            if abs(label[2] - earlier[2]) < 100 and abs(label[3] - earlier[3]) < 28:
                earlier[2], earlier[4] = 165, "end"
                label[2], label[4] = 255, "start"
    for name, fraction, x, y, anchor in labels:
        drawing.append(
            f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" fill="{text_colour}" '
            f'font-family="Geist" font-size="{font_size:g}">{escape(name)}'
            f'<tspan x="{x:.2f}" dy="{font_size * 1.125:g}" '
            f'font-family="Geist Mono">{fraction * 100:.3g}%</tspan></text>'
        )
    title = "Portfolio Allocation"
    # Raise the donut and its labels inside the SVG, preserving the title and surrounding layout.
    return (
        '<section class="chart allocation-chart">'
        f'<h2 class="chart-title">{title}</h2>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 280" '
        f'role="img" aria-label="{title}"><g transform="translate(0 -16)">'
        + "".join(drawing) + '</g></svg></section>'
    )


def _sum_values(values: Iterable[Decimal | None]) -> Decimal | None:
    """
    Add display subtotals precisely, keeping any incomplete valuation unavailable.
    """
    values = tuple(values)
    if any(value is None for value in values):
        return None
    with localcontext() as context:
        context.prec = max(
            50, max((len(value.as_tuple().digits) for value in values), default=1)
            + len(str(len(values))) + 2,
        )
        return sum(values, Decimal(0))


def _quantity(value: Decimal | None, *, money: bool = False) -> str:
    """
    Match regular units to four decimals and prices to cents, preserving unusually small values.
    """
    if value is None:
        return "-"
    if money and value.copy_abs() >= Decimal("0.01"):
        return _money(value)
    if value and (value.copy_abs() < Decimal("0.0001") or value.adjusted() >= 10):
        number = f"{value:.4E}"
    else:
        number = f"{value:.4f}" if not money else f"{value:.8f}".rstrip("0").rstrip(".")
        if money:
            whole, separator, fraction = number.partition(".")
            number = whole + "." + fraction.ljust(2, "0")
    return ("$" if money else "") + number


def _percentage(value: Decimal | None) -> str:
    """
    Show signed unrealised gain percentages without implying an annualised return.
    """
    return "-" if value is None else f"{value:+.1f}%"
