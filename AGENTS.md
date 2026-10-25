# Aurum

## Purpose

Aurum is a personal finance app for understanding financial activity and position. Rebuild from scratch;
use EigenOne (`../Eigen One/`) as a requirements reference and leave it unchanged. The reporting milestone
explicitly permits adapting its PDF layouts and reporting code. Other implementation stays original.
Balance sheet, income, budget and investment PDFs use posted accounting; dashboards will follow.

## Working Agreement

- Agree on purpose, design and affected files, then complete the approved scope without intermediate stops.
  Explain changes plainly and give a simple check. Answer questions without advancing implementation.
- Prefer straightforward Python and introduce dependencies only when needed. Use PyCharm, one project
  `.venv`, `python -m pip` and `requirements.txt`.
- Read current files, preserve local edits, save through PyCharm and verify on disk.
- Develop and verify with synthetic examples. Keep personal data, credentials and private configuration local;
  `.venv/`, `.idea/`, `data/` and `config/` stay out of Git.
- Do not create test files or `__init__.py` files. Use temporary synthetic checks where appropriate.
- Keep this file for enduring decisions and [USER_GUIDE.md](USER_GUIDE.md) for commands and user workflows.
  Update both when relevant behaviour changes; current decisions override older notes.
- No database migrations or automatic backups during development. The user manages backups and recreates
  incompatible databases manually. Never reset personal data as part of verification.

## Python Style

- Begin each Python file with the 120-column banner: `#` at both ends, hyphen borders, centred `Aurum` and
  a location subtitle using ` - `. Follow it with the module docstring.
- Number Title Case `# %%` sections from `0. Preamble`, padding with hyphens to column 110. Group by purpose;
  main functions precede standalone private helpers in a `Helpers` section. Keep methods in their classes.
- Give every module, class, function and method a natural, purpose-focused docstring. Add arguments, returns
  and exceptions where useful. Put opening and closing `"""` on separate lines.
- Use short sentence-case grouping comments selectively. Label imports `# Imports` and `# Aurum imports`,
  omitting empty groups. Explain accounting rules and non-obvious steps.
- Wrap code, comments and docstrings at column 110; only banners use 120 columns.

## Accounting

- Use immutable Pydantic models and five account types: asset, liability, equity, income and expense.
  Accounts are flat; report grouping, subtypes, subaccounts and savings goals remain separate concerns.
- Accounts and securities have stable keys and persistent UUIDs. Generate and save omitted IDs only after
  validating the whole YAML configuration. Preserve IDs and retain closed accounts needed by history.
- Balances start at zero and come only from posted journal entries. Record starting positions with balanced
  manual entries; accounts have no opening-balance field or separate active flag.
- One `JournalEntry` represents an event with two or more ordered `JournalLine` postings. Each line has one
  positive debit or credit; whole-cent AUD totals balance exactly. Income/expense accounts supply categories.
- Store postings and unfinished source statuses in one `journal` table. Only `posted` rows affect balances.
  Keep questions, reasoning and AI metadata in processing history. No event summaries or description AI calls.
- Journal lines may have one configured budget category without changing their accounts or amounts. Persist
  labels in `journal_budget`, linked to journal row IDs, so adding the feature preserves existing postings.
  Omit absent labels from snapshots to retain existing review identities. Corrections carry labels forward;
  explicit `default` restores account-based treatment, while `exclude` suppresses that posting in the budget.
- Preserve each linked transaction's UUID, bank account, full signed amount, original description and effective
  date. Use it once across the journal. The event's accounting date may differ from statement dates.
- Save complete events and corrections atomically. Preserve the event UUID for one-to-one corrections;
  merged/split events need new identities. Corrections preserve complete source groups and reject stale input.

## Files and Inputs

- Keep models in `finance/`, coordination and PDF parsing in `workflows/`, SQLite storage in `database/`
  and commands in `scripts/`. Account definitions, registry and YAML loading live together in `accounts.py`.
- Defaults: `config/accounts.yaml`, `config/securities.yaml`, `data/bank_statements/` and `data/aurum.sqlite3`.
  Resolve defaults from the project root and supplied relative paths from the working directory.
- Accept `YYYY-MM-DD` and `DD-MM-YYYY` for manual accounting and account-closing inputs. Store/export ISO dates;
  monthly CSV filenames remain `YYYY-MM.csv`. Preserve YAML comments and quotes with `ruamel.yaml`.
- Import one immutable `Transaction` per ANZ Plus statement line, keeping both observations of transfers.
  Match PDF headers to one configured bank account and validate totals and available running balances.
- SQLite is authoritative. Money uses `Decimal` in Python and validated cent-valued `REAL` in SQLite;
  security quantities and prices use exact decimal text.
- Skip successfully imported PDF content hashes before parsing. For changed/overlapping statements, compare
  source facts and running balance, adding only surplus repeated-row occurrences. Save each import atomically;
  preserve existing identities and source details. Do not persist row fingerprints or occurrence counters.

## Journal Workflow and AI

- Run PDF importing separately. Journal runs process manual instructions and human answers before ordinary
  bank categorisation, then apply savings-pot instructions against the completed income and expense figures.
  Keep CLI progress brief, distinguish this run from saved work and print relevant next
  commands with path/model overrides preserved.
- Before AI, match transfers by their full trailing `#` reference, preserving leading zeros. Require distinct
  banks, equal/opposite amounts, dates within three days, reciprocal bank details and consistent FROM/TO
  direction. Use the outgoing date. Missing, contradictory or ambiguous evidence requires review.
- Use Aurum's own Sign in with ChatGPT credentials under ignored `data/ai/` with owner-only permissions.
  Never copy Codex credentials or fall back to separately billed API keys. Default to `gpt-6-luna`;
  model overrides are explicit, with no silent fallback.
- Load the categorisation/manual skills explicitly from `.agents/skills/` and version their contents.
  Send batches of at most 20, compact account catalogues and relevant guidance. Redact bank identifiers,
  treat descriptions as data and validate response coverage, account choices and completed responses.
- Initial AI selects existing income/expense accounts only for clear purposes. Refunds, principal, splits
  and uncertainty need review; Other Income/Expense must not hide uncertainty. Human choices are authoritative.
- Reserve requests before inference. Skip completed work and unchanged answers; failed/interrupted requests
  require `--retry-errors`. Reconsider unresolved investment questions once when the account/security catalogue
  changes. Save its version in the existing request context, without a database schema migration.
- Bank review uses `data/reviews/YYYY-MM.csv`: edit only `human_review`. Manual input uses
  `data/manual/YYYY-MM.csv`: edit date, instruction, optional source IDs and `human_response`. Preserve generated
  identities/revisions and concurrent edits. Clearing an answer does not undo accounting.
- Keep manual corrections in their original CSV rows. Bank-funded events reuse imported evidence; only
  explicit bank opening positions can omit it. Non-bank changes need a movement, amount and counterpart,
  without an opening keyword. Block competing manual/bank answers before inference.
- Accepted bank reviews may learn narrow, evidence-linked guidance. Store it privately and export readable
  `categorisation_guidelines.md`. Conflicts remain inactive; guidance never rewrites unrelated posted events.
  Learning errors do not discard valid accounting. This is application memory, not model training.

## Investments and Deferred Work

- Keep calculations and market pricing in `finance/investments.py`, security definitions in `securities.py`
  and SQLite quote storage in `database/prices.py`. Derive holdings and book cost from dated journal movements.
- Support opening holdings and purchases in AUD. Prefer actual units, execution prices and bank settlements;
  otherwise use a completed daily close on/before the date, within seven days. Cache quotes and interpreted
  proposals; reuse only with matching input, model, skills, accounting context and configuration.
- Amounts mean total spent unless stated otherwise. Capitalise stated fees once and label estimated units.
  Investment details belong on the non-bank holding line. Missing prices need a question, never zero or FX
  substitution. Price retrieval does not create valuation journal entries.
- Sales, holding transfers, staking, corporate actions and FX remain unsupported. Weighted-average disposals
  and replay of dependent activity after backdated corrections belong to that future workflow.
- Manual-entry guidance learning, dashboards and cumulative goal balances remain future work. Monthly savings
  pots divide contributions without changing a bank account's full balance or claiming a cumulative balance.
- Configure virtual pots and the residual category in `config/budget.yaml`. Total savings equals budget income
  minus expenses. Savings receives the residual; Sparkle, Extravagance, Postcards, Peace and Homeward require
  explicit allocations. Display Savings first, then those five pots in that order, then investment contributions.
  Use names without descriptions. Bank transfers and opening positions do not create new savings; interest
  remains income and contributes to the surplus when unspent.
  Keep pots out of the ledger. Store one replaceable allocation per manual CSV identity in `savings_allocations`.
  Use the existing manual AI skill to interpret instructions; Python checks permitted pots and live monthly
  capacity atomically. Unchanged answers do not call AI again. Clearing a CSV cell does not undo an allocation.

## Financial Reports

- Run `python aurum/scripts/reports.py` after journal preparation. Generate PDFs only, with concise terminal
  progress. Default to the latest posted event's reporting month; support an explicit month and path overrides.
- Reporting months end on the last Monday-Friday day, without public-holiday adjustment; the next day starts
  the next month. Reporting years follow the same December boundary. Use journal dates, not effective dates.
- Read one validated journal snapshot. Only posted events affect figures; outstanding accounting produces a
  labelled draft. Reports do not process CSV answers, call AI, close accounts or change journal postings.
- Present current reporting-year profit and current-year direct Retained Earnings movements in Retained
  Earnings. Present earlier profit and earlier Retained Earnings movements in Capital. Do not double-count
  recorded equity balances. Income statements include only income/expense accounts in the selected period.
- Derive dated holdings from the journal. Replace only their cost component with market value, preserving
  other balances in the account. The same adjustment goes to Unrealised Capital Gains equity, outside income.
  Use completed AUD closes on/before the cutoff within seven days; retain actual dates in the quote cache.
  Missing prices retain recorded value in a labelled draft. Ambiguous existing valuations need review.
- Keep ordered asset groups and equity account keys in private `config/reports.yaml`, separate from accounts.
  Require exact balance-sheet reconciliation and preserve losses, refunds and negative balances.
  Show every configured account in its relevant financial statement, including unused accounts at $0.00.
  Group Superannuation and Real Estate with the other Investments on the balance sheet.
- Choose `colour_scheme` in `config/reports.yaml`: a configured theme key or the built-in `teal` (default),
  `navy_blue`, `plum_purple`, `forest_green`, `burgundy` or `copper`. Apply it to report accents, icons and charts.
  For the original themes, keep budget Income/Expenses/Savings
  progress and Remaining indicators in their original blue/red/green shades by default, independently editable
  under shared mode colours rather than tied to the theme palette.
  Default to muted shades across all six palettes and positive/negative report accents, preserving their meanings
  and shade order in both page modes. Omitted settings preserve teal. Table column-heading bars
  and bottom total rows share the palette's third shade and text colour. The right-hand highlighted summary
  card uses the second palette shade, with white icon/value defaults. Order up to eight expense bars by
  descending amount, spanning the full palette from darkest to lightest. Blend shades for the displayed bar
  count instead of cycling back to the darkest shade on the eighth bar.
- For themes without a fixed appearance, `dark_mode` is a strict boolean setting; omission or `false` preserves
  light mode. When `true`, default
  to dark charcoal page backgrounds and slightly lighter surfaces, preserving the selected palette's colours
  and order.
  Default to white text/icons on dark accent bands and highlighted cards; use light neutral text for standalone
  headings and normal icons. Expense bars remain darkest to lightest. Preserve budget section and
  positive/negative colour meanings, adapting shades for contrast rather than changing their hues.
- Configure optional `themes` in `config/reports.yaml`, keyed by lowercase names with underscores. Each
  configured theme requires a `palette` of at least three quoted `#RRGGBB` colours, ordered darkest to lightest.
  The first shade supplies major headings and light-mode icons, the second the highlighted summary card and
  the third table column headings and bottom totals. `table_header` and `on_table_header` control both;
  optional `highlight_text` overrides the highlighted card's label/subtext, otherwise the last shade.
  Omitted themes use their built-in muted definition; keep existing teal overrides when writing its config.
  Each theme also accepts `light_page`, `light_surface`, `dark_page` and `dark_surface`, defaulting to
  `#ffffff`, `#f8f9fa`, `#191A1C` and `#27282B`. Reject unconfigured custom selections, unknown fields and invalid colours.
  Optional `light_divider` and `dark_divider` default to `#9aa6ad` and `#46515c`; they colour header/footer
  and table-row dividers. Cards and panels have no visible outlines; preserve their existing spacing.
  Configure group-row backgrounds with `light_group` and `dark_group`; dark groups use a visible solid fill.
  Group-row fills match the column-heading bars' width, height, padding and rounded corners; retain their text size.
  Optional `primary`, `table_header` and `highlight` override their palette-derived accent roles.
  The selected theme and mode choose the page/surface pair independently of palette colours. Page covers the
  whole paper including margins; surface fills cards and tables. Text and indicators retain their mode's
  treatment, so chosen backgrounds must remain readable.
- Original themes keep remaining colours in shared `colours.light` / `colours.dark`, with descriptive labels:
  text, icons, band lettering, chart labels, budget indicators, gain/loss text and draft notices.
  Partial mode settings inherit the correct mode's defaults. Null palette/text fallbacks are explicit.
  Accept six-digit hex colours, and optional alpha bytes only for group fills and budget pills. Validate before
  rendering; CSS and SVG/icon rendering must all use the configured values. Choose bar-value text by contrast
  between two configurable colours. Preserve original budget indicator hues unless the user edits them.
- A theme's optional `fixed` block supplies `page`, `surface`, `divider`, `group` and its own `colours`.
  It ignores `dark_mode` and all shared mode colours, including icons, budget indicators and draft notices.
  Missing fixed colour fields use model defaults, never the shared configuration. Keep sampled palettes and
  labelled companion colours in config. Convert embedded image profiles to sRGB before sampling; textured
  swatches are approximate. New fixed themes are Tranquil Earth, Warm Neutrals, Velvet Neutrals, Twilight Mauve
  and Sunset Hues. Twilight Mauve has a fixed dark appearance; original themes retain their existing behaviour.
- Keep PDF assets local under `reporting/`; match Eigen One's A4 layout with safe continuation pages.
  Match its rendered font weights and supplied icons; align paired panel bottoms with totals beneath the rows.
  Keep balance-sheet Liabilities and Equity at natural heights, putting spare vertical space between them
  so Equity ends level with Assets, rather than stretching the space above the Equity total.
  Centre header/footer dividers at 90%/80% of the content width. Separate table rows in all reports with subtle
  solid vector lines inset 4pt at each end; avoid gradient row dividers, which can fill cells in Preview.
  Omit lines immediately above/below group highlights and below the last row. Omit report notes.
  Show a compact Draft notice when needed, with detailed reasons in the terminal.
  Use the configured name in headers and footers; the footer adds the local generation date.
  Balance-sheet negatives use normal text colour with their minus signs.
  Chart values sit left-aligned inside bars. Small bars show only percentages, with a minimum visual width
  sufficient for the label; this intentionally enlarges their apparent share without changing the numbers.
  Bars have square left ends and rounded right ends. Category labels use natural glyph widths; wrap or shorten
  long names instead of stretching text.
  Save `balance_sheet.pdf`, `income_statement.pdf`, `budget.pdf` and `investments.pdf` under
  `data/reports/YYYY-MM/`. Render all four before replacing previous outputs.
- Keep ordered budget categories, monthly fixed/percentage targets, overrides and excluded accounts in private
  `config/budget.yaml`. No rollover or target proration; percentages use actual budget income including gifts.
  Unset target numbers display a dash and retain an empty progress pill. Zero targets also stay empty when
  actuals are zero or negative; a positive actual fills the pill in its normal section colour.
  Unconfigured income/expense accounts remain visible as unbudgeted actuals.
- Budget actuals follow reviewed income/expense postings, including signed refunds and non-cash expenses.
  A configured excluded account removes its whole event. Gifts explicitly labelled on Capital count only as
  budget income. Savings is the residual after expenses, including any money retained in transaction accounts.
  Explicit asset/liability labels and manual pots allocate that total, reducing the catchall by the same amount.
  Later accounting corrections that make allocations exceed capacity flag a draft instead of silently discarding
  them. Preserve negative total savings in deficit months. Without pot settings, show an automatic Savings row.
- Use existing human-review/manual CSV instructions to assign or remove budget labels. Reports never infer
  them from descriptions or call AI. Preserve old posted treatment until a changed answer is submitted.
  An explicit human statement that a receipt is a gift supplies its budget purpose; the AI may select the
  unique configured gift-income label on Capital without requiring the person to quote its internal key.
- Preserve stable budget keys and user-entered targets when changing display names. Use Summary of Budget
  Allocation for the selected month as the subtitle. Keep Income and Savings at natural heights, adding space
  above Savings to align its bottom with Expenses. Budget columns use widths 22/18/18/20/22%, 8px headings,
  9px body text and
  50px by 12px progress pills; keep row-divider insets consistent with the income statement.
  In light mode, Remaining is black when nonnegative; negative Remaining uses dark blue for Income,
  dark red for Expenses and dark green for Savings. In dark mode, use light neutral text for
  nonnegative Remaining and contrasting blue/red/green shades for negatives. Use plain numbers without
  thousands separators.
  Progress fills always keep the normal blue/red/green section colour, including when Remaining is negative,
  with the fill capped at 100% of the base pill.
- Investment reports show account/security holdings, cost, dated AUD price, market value and unrealised gain.
  Use the exact same valuation as the balance sheet, with cost-basis allocation like Eigen One. Unpriced
  holdings remain listed with unavailable valuations; withhold full portfolio valuation totals and mark a draft.
  Gain percentage is gain divided by carrying cost, not annualised performance or a dividend-inclusive return.
  Display unavailable investment figures as `-`, including summary cards and totals. Indent holding symbols
  beneath their asset-class headings using the balance sheet's account indentation. Use Cryptocurrencies
  as the report class label; keep the instrument-type value `crypto` separate from account keys and names.
  Use Summary of Investment Portfolio as of the cutoff date as the subtitle. Portfolio Allocation labels
  use the expense chart's fonts at a 20% larger rendered size: Geist labels and Geist Mono values.
- Include configured `account_investments` such as UNISUPER at their recorded journal balance, even zero.
  Use optional `investment_symbols` overrides in report settings, falling back to the account name. Keep
  Superannuation as the account name and UNISUPER as its investment symbol. Units, average cost and quoted
  price show `-` for whole-account investments. Accounts with no posted activity through the cutoff have zero
  cost and gain, so they do not suppress portfolio totals. An account with activity, even a net-zero balance,
  keeps unknown cost and gain until supported. Positive movements alone cannot distinguish contributions,
  tax refunds and investment returns.
  Include their balance in portfolio value; withhold aggregate cost/gain when unknown. Keep the chart title
  Portfolio Allocation, using security cost and whole-account recorded balances for now. Reject double counting
  an account as both individual securities and a whole-account holding. Match Eigen One's investment cards,
  outside-labelled allocation donut and compact holdings table with asset-class subtotal bands.
