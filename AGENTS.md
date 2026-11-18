# Aurum

## Purpose

Aurum is a personal finance app for understanding financial activity and position. Rebuild from scratch;
use EigenOne (`../Eigen One/`) as a requirements reference and leave it unchanged. The reporting milestone
explicitly permits adapting its PDF layouts and reporting code. Other implementation stays original.
Balance sheet, income, budget and investment PDFs and the Overview snapshot use posted accounting.

## Working Agreement

- Agree on purpose, design and affected files, then complete the approved scope without intermediate stops.
  Explain changes plainly and give a simple check. Answer questions without advancing implementation.
- Prefer straightforward Python and introduce dependencies only when needed. Use PyCharm, one project
  `.venv`, `python -m pip` and `requirements.txt`.
- Read current files, preserve local edits, save through PyCharm and verify on disk.
- Develop and verify with synthetic examples. Keep personal data, credentials and private configuration local;
  `.venv/`, `.idea/`, `data/` and `config/` stay out of Git.
- Do not create test files or `__init__.py` files. Use temporary synthetic checks where appropriate.
- Keep this file for enduring decisions and [USER_GUIDE.md](USER_GUIDE.md) for desktop user workflows.
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
- Balances start at zero and come only from posted journal entries. Setup's inline bank opening row creates
  balanced journal entries against Capital; accounts have no YAML opening-balance field or separate active flag.
- Account removal protects current accounting and unfinished processing references. Completed applied,
  superseded or withdrawn processing snapshots remain unchanged as evidence and alone do not block removal.
  Offer the row only for saved asset bank accounts, with explicit amount, date and a separate post icon.
  Configuration Save never posts it. Preserve row drafts across account selection and refresh; guard navigation
  with an icon-free Cancel/Discard prompt. Keep existing simple openings editable without changing their identity;
  route complex or multiple openings to the complete journal editor. Recheck duplicates and stale revisions
  within the posting transaction. Other assets and liabilities retain their existing transaction workflows.
- One `JournalEntry` represents an event with two or more ordered `JournalLine` postings. Each line has one
  positive debit or credit; whole-cent AUD totals balance exactly. Income/expense accounts supply categories.
- Store postings and unfinished source statuses in one `journal` table. Only `posted` rows affect balances.
  Keep questions, reasoning and AI metadata in processing history. No event summaries or description AI calls.
- Historical journal budget labels remain stored in journal_budget and are carried through corrections
  unchanged, but never influence current cash-budget projection. Do not create new per-posting budget labels.
  Omit absent metadata from snapshots to retain existing review identities.
- Preserve each linked transaction's UUID, bank account, full signed amount, original description and effective
  date. Use it once across the journal. The event's accounting date may differ from statement dates.
- Save complete events and corrections atomically. Preserve the event UUID for one-to-one corrections;
  merged/split events need new identities. Corrections preserve complete source groups and reject stale input.

## Files and Inputs

- Keep models in `finance/`, coordination and PDF parsing in `workflows/`, SQLite storage in `database/`
  and the desktop entry point in `scripts/app.py`. Account definitions, registry and YAML loading live together
  in `accounts.py`. All user inputs and processing go through the GUI; do not restore CLI/CSV adapters.
- Defaults: `config/accounts.yaml`, `config/securities.yaml`, `data/bank_statements/` and `data/aurum.sqlite3`.
  Resolve defaults from the project root and supplied relative paths from the working directory.
- Accept `YYYY-MM-DD` and `DD-MM-YYYY` for manual accounting and account-closing inputs. Store ISO dates.
  Preserve YAML comments and quotes with `ruamel.yaml`. New workspaces create bank_statements and reports
  folders, with private AI files created when needed. Retired CSV files may be archived for reference but are
  never processed as inputs; do not create manual/reviews input folders.
- Import one immutable `Transaction` per ANZ Plus statement or cumulative transaction-list line, keeping
  both observations of transfers. Match PDF headers to one configured bank account; validate full-statement
  totals and every available running balance. Lists may omit balances and effective dates. Recognise known
  table layouts or an explicit empty list; reject unknown formats rather than inventing missing information.
- SQLite is authoritative. Money uses `Decimal` in Python and validated cent-valued `REAL` in SQLite;
  security quantities and prices use exact decimal text.
- Skip successfully imported PDF content hashes before parsing. For changed/overlapping statements, compare
  required source facts and compatible optional details, matching exact rows first and adding only surplus
  repeated-row occurrences. Reject conflicting known details or ambiguous surplus rows atomically. Earlier
  accurate lists are cumulative subsets; importing a shorter version never removes rows. Preserve existing
  UUIDs, economic facts and journal postings, including absent optional fields. Keep internal import hashes;
  do not persist row fingerprints, occurrence counters, document kinds or partial/final flags in new storage.

## Journal Workflow and AI

- The PySide6 desktop launches with `python aurum/scripts/app.py`. Keep widgets in `gui/`, reusable operations
  in `workflows/`. GUI actions call shared functions directly. The macOS Applications launcher opens this same
  entry point using the project's .venv; choose workspaces through the GUI rather than command-line options.
  Use the abstract app artwork in `gui/assets/app/`: the PNG supplies Qt's application icon and the ICNS
  supplies the macOS launcher's bundle icon. Keep the original master and generation prompt alongside them.
  Open the existing workspace read-only, then sync previously enabled bank feeds in a background job.
  Ordinary refresh stays read-only; first connection, saves, PDF imports and processing require UI actions.
  Main page titles and the Aurum wordmark use the PDF title's Cormorant font, semibold with small caps.
  Keep larger initial capitals and smaller capitals for lowercase letters. Omit descriptive page subtitles;
  place compact primary actions directly beneath the left-aligned titles, with 16px of header spacing
  before the normal page layout gap.
  Edit the global personal name in the sidebar footer; omit it beneath the Setup title.
  Monetary values throughout the GUI use the reports' Geist Mono font, including amount inputs,
  table cells, summary figures and budget charts. Keep surrounding labels in the normal UI font.
- Overview shows balance-sheet net worth through the cutoff and monthly budget income,
  expenses and savings. Use the shared report calculation with posted entries and the latest valid saved
  security close on or before the cutoff, matching the security and provider symbol. Both the quote's original
  requested date and price date must be on or before the cutoff; choose the newest price date and skip invalid
  records without altering them. Refresh stays read-only and never fetches prices. Net Worth's caption uses
  one As of cutoff-date line (today for the current month); keep market-price dates only in its tooltip.
  When no valid saved quote exists, or a manual valuation is ambiguous, retain recorded holding values as
  in reports; mark the card
  Provisional and name affected holdings in its tooltip. Whole-account cost questions do not make their
  recorded values provisional. Apply deterministic cash-budget rules and signed refunds; incomplete budget targets
  must not block these actuals.
  Lead the shared financial summary with Net Worth. Keep its cutoff date and Savings' Income less expenses
  caption. Income and Expenses show their included transaction counts: each posted journal event counts once per side,
  including manual events, gifts and signed refunds, without multiplying split postings. Use the same
  budget inclusions and reporting cutoff as their totals. Omit explanatory paragraphs beneath the cards.
  Centre the prominent Net Worth amount beside three compact monthly rows, with labels and transaction
  counts on the left and amounts on the right. Use one shared financial surface, with quiet separators
  instead of four equally weighted cards. Use 20px for the three secondary monetary values and retain
  Net Worth's larger size. Stack the two groups when the content area is narrow.
  Format Overview money and transaction counts
  with narrow no-break spaces (U+202F) for thousands, retaining dollar signs, cents and negative signs.
  Omit the Overview header's Updated timestamp; refresh activity stays in its existing spinner and status.
  Use local replaceable Solar Bold SVG icons and four Sources → Journal → Review → Reports cards without
  a Workflow heading. Keep the Overview quote below the cards.
  Keep the original outline set unchanged in gui/assets/ui, with Solar assets in gui/assets/solar_bold.
  ICON_SET in gui/icons.py selects solar_bold or outline on restart; the same resolver supplies themed
  stylesheet icons. Retain the four thin open navigation/disclosure chevrons instead of filled triangles.
  Preserve theme tinting, transparent interior details, tooltips and accessible names. Keep source URLs,
  creator attribution, licence and adaptation notes with the downloaded assets; PDF icons are independent.
  Retain each stage's original icon on the left; completed stages use a highlighted background and an
  upper-right tick instead of status micro-labels. Align stage icons, titles and counts consistently;
  show only the title and count as text rows, with supporting details in tooltips and accessibility text.
  Use 12px normal-weight stage counts. Every unfinished stage shares one lighter panel colour;
  completion is the only state that changes its fill. Completed stages use the selected
  theme's primary sidebar colour with contrasting text and icons. Omit the Posted figures · AUD badge and visible
  saved-activity caveat. Counts still reflect saved activity, not proof all expected statements were supplied.
  Distinguish queued answers from questions/retries and include investment classifications affecting reports.
  Each whole workflow card opens its corresponding page:
  Sources opens Statements; Journal, Review and Reports open their namesakes. Retain keyboard activation,
  accessible names and a quiet title underline on hover or focus without introducing another stage fill.
  Omit the next-step panel entirely, including routine prompts, All done text and reserved spacing.
  Keep source scope, prerequisites and completion qualifications in relevant card tooltips and accessibility
  text. Actual workspace failures remain visible; the sidebar spinner supplies loading feedback.
  Complete the Reports stage when Sources, Journal and Review are complete and all four
  expected report PDFs exist in the configured output directory's selected reporting-month folder.
  Check saved files on refresh so this works after reopening the app. Missing PDFs or an incomplete earlier
  workflow stage clear completion on refresh. Inspect current managed source PDF headers and hashes on refresh;
  describe pending final statements, missing, unreadable or not-yet-imported source PDFs in card tooltips.
  Qualify all-done guidance there until source completion is confirmed. Derive this from files and existing imports,
  without persisted document flags or changes to report accounting/draft logic. Overview has no session or
  report-freshness gate; keep detailed draft handling on Reports. Show Month Year followed by compact previous/next
  arrows, retaining exact reporting dates and unsaved-work guards. List main sidebar destinations without
  Monthly Workflow or Workspace headings. All desktop jobs use only the sidebar's animated refresh control,
  continuously across chained refresh/sync jobs, without a second spinner or bottom status caption. Actual jobs
  retain accessible stop-square and information icons. Stop waits for the current safe step.
  Routine progress and completion messages appear only in the information log, never in persistent status
  labels or changing action captions. Keep errors, actionable issues and unsaved-state indicators visible.
- Setup keeps Accounts, Securities, Budget and Reports as four persistent children in the left sidebar.
  Accounts begins with compact inline Redbark and ChatGPT connection rows. Keep routine connection controls,
  masked API-key entry and model selection on this page; only Redbark mappings and handover use a dialog.
  Match Setup and Overview's plain titles without heading icons, with space before the summary cards.
  Overview and Setup have no subtitle or personal-name field beneath their titles.
  Centre Setup card contents with aligned icon, heading and detail rows. Use Accounts, Budget and Theme
  as their main headings; show account-type counts, monthly Income/Expenses/Savings allocations and a borderless
  theme picker underneath. Put Income and Expenses on the first budget-detail line and Savings on the
  second. Only this card shows ungrouped whole dollars such as $5000, rounded half away from zero from
  Decimal totals; retain exact cents and existing grouping in its tooltip and budget configuration.
  Keep other monetary formatting unchanged. Centre the theme name on the card,
  allowing equal space on either side
  of its text. The theme popup uses rounded rows, palette previews and a checkmark for the current selection.
  Put palette swatches on a white rounded pill in both the card and popup so each shade stays visible;
  keep the adjustable Light/Dark control outside that preview pill.
  Use the same rounded popup surface and selection treatment for other dropdowns, including native outer corners.
  Buttons retain hover, pressed and keyboard-focus backgrounds without borders or macOS focus-ring outlines.
  Keep the selected month and budget readiness in the card's tooltip. Align main navigation with the brand;
  indent Setup's four children.
  Keep the shared month selector at the right of the current page title on every page and Setup section.
  Show its inclusive scheduled reporting range in 10px text beneath the month name.
  Omit years from the visible range and align its right edge
  with the final year digit in the month label, before the arrows. Retain full dates in tooltips and
  accessibility text. Use the same reporting-period calculation as reports, without shortening to today's cutoff.
  Align month and title text baselines on the same row; keep the exact reporting range in the month tooltip.
  Align sidebar Aurum with the page title; retain Personal Finance beneath the wordmark at 13px.
  Centre Aurum and Personal Finance horizontally within the sidebar. Place a short, attributed quotation
  between Setup navigation and the sidebar footer, with another centred at the bottom of Overview.
  Centre the sidebar quote and attribution together vertically between Reports and the owner's name. Keep
  navigation at its natural height, allowing it to shrink and scroll in short windows. Use regular Cormorant
  at 16px for the sidebar quote and 18px for Overview, with smaller attribution text and no italics.
  Keep Sync, Process and Generate on the same compact header-action baseline.
  Lift the month text and date range four pixels within their allocated cells without moving the arrows
  or Sync/Process/Generate buttons; keep a clear gap above the compact primary action.
  Put refresh in the sidebar footer and primary Sync/Process/Generate actions beneath the page title.
  The sidebar footer shows only the personal name, vertically aligned with refresh; omit the workspace
  name and the open-another-workspace action. Add a divider above the name and refresh row. Keep the name
  transparent, without a field background. Double-click or F2 begins editing; Enter explicitly saves only
  the name, while Escape or focus loss cancels. Guard its saved revision and retain a failed edit for retry.
  Keep this save separate from unrelated Setup drafts. Retain the launcher's initial workspace selection.
  Left-align Sync, Process and Generate with the title. Sync/Connect, Process, Generate and New entry are
  icon-only, 32 by 28 pixels, with centred 16px symbols, accessible names and tooltips.
  Use Solar Bold Refresh Circle for the shared refresh role and preserve the former
  artwork in the archive. Keep the month selector at the upper right.
  Empty or fully filtered activity tables show No entries inside their viewport, without a synthetic row or
  a no-matches label above the table. Preserve useful populated counts and actual error messages.
  Setup summary-card headings use full uppercase: ACCOUNTS, BUDGET and THEME.
  Only Budget's targets depend on that month; other Setup settings remain global.
  Reordering controls use thin local chevrons matching the month selector, never native filled arrow glyphs.
  Share the heading and cards across all four sections. The sidebar name retains an accessible Name label
  and supplies generated report headers/footers; regenerate PDFs to update saved snapshots after renaming.
  Use accessible icon buttons for routine actions
  and fixed 32px Save/Reload controls in the shared bottom activity row beside the logs icon, visible only
  in Setup. Keep explicit unsaved/error/saved states; omit idle No unsaved changes text.
  Navigation uses a compact centred Save/Discard/Cancel dialog without a question-mark graphic and never
  saves silently. Cancel and Escape retain the draft; navigation waits for an acknowledged successful save.
  Preserve selection and scroll after saving. Protect separate drafts and stale versions across multi-file saves.
  Reuse clean Setup forms when all configuration bytes are unchanged; explicit reloads still rebuild them.
  Group accounts and securities by type and indent their entries without reordering saved definitions. Keep
  account fields top-aligned at fixed vertical spacing. Infer bank details from BSB/account-number text; both
  blank means no bank details, while a partial pair must pass normal validation before saving. Empty bank fields
  stay editable with muted styling. Closing date is optional text, with no enable checkbox or calendar popup.
  Configure global Cash/Investment membership and liability repayment categories on Accounts, using one
  aligned row in each relevant account's details: Asset account type for assets and Expense category for
  liabilities. Offer None, Cash and Investment for every asset independently of bank details; the two roles
  remain mutually exclusive. None clears the role. These budget choices do not change accounting types or
  investment-report membership. Changing a global role recalculates historical budgets when regenerated.
  Keep unavailable saved choices visible for repair and disable the row until a new account has a saved key.
  Stage these choices through Setup Save, retaining their existing budget.yaml storage and accounting rules.
  Budget retains monthly targets and category-account links; omit its separate cash-flow account controls.
  Review's missing repayment-category action opens Accounts and selects the affected liability.
  Display the share instrument type as Equities and label market as Exchange / market, retaining stored values.
  Use adjacent accessible plus/bin icon buttons at the top right of each account/security list.
  The three system equity
  accounts have fixed names, types and role mappings in Setup; retain existing legacy values without renaming.
  The theme card exposes Light/Dark only for themes with adjustable appearance; omit Fixed palette text.
  Use Cormorant regular title case at 26px for main Setup section headings and 22px for nested headings.
  Omit redundant Accounts/Securities list titles and selected-record headings above the Name field.
  Retain grouped list labels, counts, regular Geist fields and adjacent plus/bin controls. Start Name beside
  the grouped list, below the plus/bin toolbar, in both Accounts and Securities. Reports' nested group titles
  use regular Geist, retaining Cormorant only for the main section heading.
  Budget's Income, Expenses and Savings table headings use centred 16px regular Geist, without repeated totals.
  Keep main title small caps and body/status/monetary typography unchanged. Remove generic instructional page
  copy, retaining actionable errors and necessary review guidance. Shared search fields use borderless themed backgrounds
  with hover and keyboard-focus fills, preserving the existing icon/text insets and native clearing.
  Keep detailed palette definitions
  out of the GUI, preserving existing custom settings.
  GUI appearance follows the saved report theme on a successful workspace refresh. Keep canvas, cards and
  controls visibly distinct using the palette, while retaining fixed light/dark appearance and sidebar identity.
  Report colours remain unchanged. Keep it scoped to each workspace window; retain the current appearance
  on invalid or failed refreshes. Unsaved theme choices do not change the GUI. Adapt controls, popups, icons
  and selections for contrast while preserving budget and positive/negative meanings.
  Use the primary theme colour for consequential actions, clear filled selections for tabs and controls,
  and restrained table headers. Activity tables use horizontal row separators without vertical grids.
  Sunset Hues uses its fourth palette shade (dusty rose) for GUI panels instead of the pale-yellow report
  group fill. Keep this desktop override out of report configuration and PDF rendering.
  Assign each asset account through one
  Account / Report Group table, with direct moves and an unassigned Other Assets choice. Keep referenced missing
  keys visible for repair. Stack Asset Groups and Account-based Investments on one Reports page under the
  output folder, without inner tabs. A settings icon beside Asset Groups opens a cancellable group draft.
  Double-click or F2 renames a group inline; plus adds a blank row and starts editing. Enter commits the edit;
  Escape cancels it and removes an empty new row. Use save/close icons and commit any active editor before
  validating nonblank unique names or reordering/removing rows. Dialog Save applies to the Setup draft only;
  Setup Save persists it. Removing a group leaves its accounts ungrouped. Preserve group/member order and
  explicitly configured Other Assets groups. Keep Reports concise, with account-based investment and
  optional-label guidance in tooltips.
  Keep account-based investments centred at their current bounded width, with clean headers without cell grids;
  show every row in the outer form scroll. Centre all entries, editors and group-picker choices in both
  Reports tables, with Title Case headers: Account, Report Group, Investment Group and Report Label.
  Keep other dropdowns' existing alignment and preserve full-field mouse and keyboard operation.
  Centre budget category names, monthly amounts and their editors.
  Budget account linking opens an anchored searchable multi-select dropdown with visible checked/unchecked
  indicators. Clicking an account row or pressing Space immediately updates the unsaved Setup draft without
  rebuilding the form or closing the dropdown. Escape and outside clicks dismiss it without undoing choices;
  Setup Save persists them. Preserve selected links hidden by search, order, category-type filtering and
  unavailable saved references. Empty pickers explain which account type to add in Accounts.
  Create missing workspace files explicitly before editing; failed reloads must not leave stale forms active.
- Setup's ChatGPT settings are workspace-wide and stay inline under Accounts → Connections. Put status below
  each connection name in small muted text, with aligned API-key/model fields and two right-aligned routine
  icon actions per row. Match field heights, backgrounds, padding and typography. Use short Not connected,
  Not checked, Connected and
  Needs attention states, with actual errors inline and explanations in tooltips. Display the saved friendly
  model in the picker before checking
  access; showing it does not create a draft. Retain unavailable saved or staged models visibly without choosing
  a replacement automatically. Omit duplicate saved-model labels. The row has its own model-only Save;
  saving never commits unrelated Setup account or configuration drafts.
  Show Save/Discard icons only for a changed selection and retain the saved model in its tooltip.
  Distinguish saved credentials from a live connection check. Preserve staged model choices across refreshes,
  require an explicit save action and protect unsaved model selections when leaving Accounts, reloading or
  closing the workspace. Use the compact Discard/Cancel guard separately from Setup Save. Clear a model draft
  only after asynchronous success is acknowledged; failures retain the selected model and draft.
  Display friendly model labels such as GPT 6.1 - Sol while retaining exact stored model identifiers.
  Failed or cancelled checks must not claim success or navigate away from an unsaved choice.
  Keep sign-in and connection checks behind explicit actions. Discard does not undo completed authentication.
- Statements prioritises bank Sync in the shared header-action position above the month's transactions.
  Centre Amount, Source and Status cell contents beneath their headers. Size Date, Amount and Status from
  their contents; use Account and Source's natural widths when possible and wrap them fully when narrow.
  Description fills the remainder and may elide. Measure and paint wrapped text consistently, including
  long unbroken filenames, growing rows without horizontal overflow at supported widths.
  Read-only monetary table cells always display two decimals; keep missing values and intentional dashes.
  Omit routine connected-account counts. The PDF icon beside search opens the file chooser directly.
  Choosing or dropping PDFs reveals the pending count and explicit import controls. Keep actionable errors
  visible. There is no import-history GUI; preserve internal imports and hashes for reconciliation.
  Choose PDFs opens the workspace's data/bank_statements folder; choosing or dropping local PDFs explicitly
  stages validated copies there before importing. Use the statement's calendar end month and stable account key
  as YYYY-MM_account.pdf, stripping leading anz_plus_ or anz_ to retain existing ANZ filenames. A cumulative
  transaction list uses its commencement's reporting month and must start at that reporting period's start;
  its generation timestamp bounds transaction dates, without claiming final coverage. Name lists
  YYYY-MM_account_list.pdf so the final YYYY-MM_account.pdf can coexist. Reject an older or equal-time
  differing list replacing a newer list, and never replace a final statement with a list. An imported valid
  final statement takes precedence over its retained list for completion. Preserve external originals;
  rename noncanonical managed sources only after a successful atomic replacement. Replace an existing
  canonical PDF, but reject differing selected-source or same-batch destination collisions for separate preparation.
  Invalid PDFs must not overwrite managed files. Staging never opens the database or imports transactions.
  Import complete PDFs only on the explicit import action, regardless of the selected month. Retain failed and
  unattempted files after partial preparation/import or cancellation, preserve newly queued files, and remove only
  confirmed completed submissions. Stop-and-close must also defer queued preparation. Successful hashes still skip
  parsing during imports; file replacement never rewrites earlier saved transactions or journal entries.
  Explicit managed imports reconcile validated canonical source filenames in imports and matching transaction
  rows, including already imported hashes. Reuse successful staging validation in memory after verifying hash
  and current account configuration; known hashes never trigger another import parse. Only filename metadata
  changes: keep economic facts, UUIDs, journal postings and historical payloads unchanged. Retain ambiguous
  repeated-row labels with an import notice, and keep canonical sources belonging to other overlapping PDFs.
  Show selected PDFs as a ready/preparing count without a filename list. Use an accessible icon-only import
  button with a light symbol; retain clear/retry controls and per-file outcomes in import feedback tooltips.
  Left-align the PDF chooser/count/action group and feedback within the import panel. Align chooser and search
  text with balanced icon insets; search icons sit twelve pixels inside the left edge and remain vertically
  centred. Use the same search spacing for Statements, Manual, Review and Journal, retaining native editing
  and clearing without shifting the prompt or icon on focus.
  Keep report-folder and backup-PDF icons centred in 36px square buttons aligned with their adjacent fields.
  Display transaction and journal descriptions in uppercase in read-only tables and detail cards;
  preserve stored descriptions, editable inputs and copied source evidence exactly. Keep table text in
  Geist and monetary values in Geist Mono.
  Selecting a transaction shows compact details below the table, with status, signed dollar amount, dates,
  account, reference and full source wording; transaction selection never opens a popup.
  Lay statement date, effective date, account, reference and statement filename out in one horizontal metadata
  row in Statements. Keep Review's compact transaction layout readable in its narrower pane.
  Display -$40.00 AUD for negative amounts and show the .pdf filename from the stored canonical stem.
- Rename downloaded PDFs through Statements' choose/drop preparation. Keep validation, collision and stale-file
  checks in the shared workflow; do not maintain a separate terminal renamer.
- GUI reviews and manual instructions save directly to SQLite without inference. Process the selected reporting
  month plus complete linked transfers/corrections; inspect global evidence without writing unrelated months.
  Keep manual instructions and review responses in SQLite, preserving revisions and rejecting stale saves.
- Review is the shared queue for bank questions, unfinished manual instructions and unclassified investment
  movements through the selected report cutoff, including earlier months. Keep ready-to-process work visible,
  distinguish questions and explicit retries, and retain posted bank events for corrections. Suppress duplicate
  bank attention only when its owning/blocking unfinished manual instruction is available in the queue.
  Show a unified Review queue with Date, Description, Amount and Status; keep Date visible. Place the compact
  Posted / Review / Ready / All filter on the search row, defaulting to Review. Review contains questions,
  retries and unresolved classifications; Ready contains queued work and saved responses awaiting Process.
  Posted contains completed journal events in the selected period, including direct manual entries without
  review history, deduplicated by journal UUID. Unresolved work keeps its event out of Posted. All combines
  these views and truthful withdrawn/superseded history. Preserve separate actionable issues and routing.
  Give Description the remaining table width and include the question in its tooltip.
  Related-work navigation selects the matching item without hiding other kinds; retain internal kind and
  review identities for routing and separate issues. Journal amounts show unsigned total debits; standalone
  bank observations keep signed amounts. Unknown manual amounts show an em dash, without parsing instructions.
  Preserve source identities and answer revisions. Bank and manual responses stay inline and retain drafts across
  selection, filters and refreshes. One save icon commits all edited answers atomically; a stale answer rejects the batch.
  Build displayed review revisions from one shared accounting snapshot; saves still recheck live versions.
  Manual responses preserve the original instruction, date and source links; their saved-question token and
  accounting revision are checked inside the same atomic batch as bank answers. All retains completed
  manual conversations read-only. Edit opens the original instruction; investment items open the complete
  journal editor at the relevant posting.
  Use the shared journal card for posted Review events and a transaction card for unposted bank evidence,
  without Answer/Sources tabs. Keep an effective 24px gap from the active viewer to the question/response section.
  Answerable questions appear as the Response placeholder, tooltip and accessible description, never as
  answer content or a separate label. Keep action guidance visible for issues without a response editor.
  The Response label is 13px medium weight above its fixed-height editor.
  Put Save beside Process with matching 32 by 28 pixel bounds and an 8px gap, followed by the unsaved-response
  count and discard-all-drafts control. Keep pending-answer discard beside its response, with no bottom save bar.
  Batch-save errors remain page-wide below the actions until addressed, including across selection changes.
  Keep idle save feedback quiet. Preserve bank facts and posting metadata in the card and tooltips.
  Remove clipboard Copy buttons from all shared transaction and journal viewers. Their trailing header action
  accepts the owning page's icon-only edit route, preserving manual ownership, source IDs and investment focus.
  Keep bank-feed conflict decisions explicitly labelled, and use a settings icon for budget configuration issues.
  Keep Response and its textbox together at a fixed natural height
  when the window grows. The question displays saved AI guidance, processing explanation or required next action;
  the card's Posted badge and postings show the accounting state. Manual shows one
  saved-history list for instructions and direct journal entries, keyed by manual identity; latest human outcome
  identifies a direct entry without duplicating its event.
  Direct-entry list descriptions use the first nonempty posting description, matching the journal card;
  instruction entries retain their instruction text. These are presentation choices, never stored-text rewrites.
  Manual's table shows Date, Description, Amount, Source and Status, with no Account column. Source identifies
  Instruction or Manual journal; retain linked/posting account names in tooltips and details. Unknown facts show
  an em dash. Centre status values, including Posted, and use unsigned event totals for posted journal work.
  Manual is the input surface: New entry directly opens the shared Manual Entry popup in Instruction mode.
  Retain the Instruction / Journal entry switcher only inside the popup, with separate text and posting drafts.
  Keep one shared typed-date field and icon-labelled Bank Transactions picker above both methods. Carry their
  values across switching; replacing edited postings needs a discard confirmation before rebuilding source rows.
  Keep the popup at its initial 940 by 700 size across methods, with an outer scroll for longer content.
  Use the calendar popup and no external date label. Omit mode subtitles, the Instruction label
  and routine Reload. Keep named, icon-only Cancel and Save/Post controls with existing unsaved-work guards.
  Show an error-only Reload icon for the active method, retaining its discard guard and the inactive draft.
  Keep one icon-only edit action in the detail header and Open Review for conversations. Unposted items show their instruction instead
  of empty journal amounts; short lists keep details directly beneath them. Preserve each draft and its original revision
  while switching or reloading the other method. Save only the selected method, clearly distinguishing queued
  instructions from immediate journal posting. Keep virtual savings allocations out of direct journal mode.
  Keep an always-visible Bank Transactions picker button and thin chevrons for saved outcomes. Keep Process beneath the title
  in Review and Journal, covering saved instructions and bank transactions for the month. Manual has no separate
  Process or response editor. Editing an instruction preserves its saved response bytes and revision.
  Read only editor-specific context when opening Manual; do not rebuild unrelated review queues. Keep a scrollbar
  gutter and show all ordinary journal posting rows without an inner table scrollbar.
  Account fields are selection-only dropdowns that open across the whole field, with keyboard navigation and
  no editable text or completer. Centre the add-posting icon in the posting table's final 48px header column;
  centre each 30px bin button beneath it with equal horizontal inset. Use Debit/Credit headings without a totals calculator; validate exact
  balance on save. Size posting options to their current budget/investment controls. Omit the Starting bank
  balance checkbox; direct new bank openings go through Setup while corrections preserve saved opening provenance.
  Manual has search and saved history without a status filter. Omit persistent unsaved and inactive-method
  discard notices. Use the shared compact, centred Discard unsaved changes? dialog for local draft discards,
  with centred Cancel/Discard text buttons and no graphic. Cancel is default and Escape keeps the draft.
  Show fixed save controls and validation errors; preserve dirty input and its original revision
  through filters and refreshes. Only an acknowledged save may clear that submitted form or continue navigation.
  Keep linked-record destinations when navigation waits for a save; explain unavailable monthly destinations.
- Process month also assesses eligible accepted bank reviews missing learned guidance and exports that guidance.
  Desktop Process month retries failed/interrupted accounting and guidance requests while skipping completed
  work and unchanged successful answers. There is no separate retry control or command-line processing mode.
  Cancellation prevents starting another stage; learning/export failures never discard saved accounting.
  Keep Process prominent with its play icon. Show a spinning activity
  indicator during desktop work, with stage text only in the activity log. An unavailable investment queue prevents workflow completion.
- Journal separates monthly processing from selected-entry actions and has no CSV exchange controls.
  Selecting an event opens a read-only panel below the table with exact debit/credit totals,
  named postings, full source details and explicit edit/review/manual actions. Manual and Journal use the same
  JournalDetails widget: one unsigned total-debit event amount, description, left-aligned accounting date,
  source and linked bank account names together in one horizontal row, with a narrower posting table
  on the right capped at 480px. Stack the table
  below metadata in Review's compact pane with extra separation above the table. Use compact posting rows and
  a shared text inset for amounts, descriptions, metadata, account names, Status and Response. Leave an 18px
  gap between saved-record tables and their detail cards in Statements, Manual and Journal. Hide the gap when
  no detail card is selected. Keep Response text aligned with the card's text rather than its outer border.
  Measure actual styled row heights so up to five postings remain
  fully visible, and let the surrounding detail panel follow its content height. Omit a Sources tab. Keep
  source labels readable as Redbark, PDF filenames, Manual entry or Manual instruction; deduplicate and wrap
  multiple names. Omit bank-transaction counts in all viewers and Manual instruction summaries. Keep
  source and posting metadata accessible in tooltips and preserve selection on refresh.
  Only the main Journal event table shows a Budget column: Included, Savings transfer, Excluded, Needs review,
  Unavailable or After cutoff. Derive it read-only from the shared event projection, opening provenance and
  reporting cutoff, never retired exclusion flags or incomplete target amounts. Its tooltip names primary
  categories or the reason. Cash-to-Cash events show Savings transfer without adding to report totals.
  Keep budget-impact information out of all detail viewers and editors; do not build expensive monthly impact
  previews or editor impact contexts for this UI. Preserve finance calculation APIs and accounting validation.
  Reports has one preview view, a rounded report-type picker and an icon-only Generate play button.
  Keep draft/error messages
  inline, with accounting issues in Review and settings under Setup. No attention tab or report-settings button.
  Show the output folder in Generate's tooltip; omit saved timestamps, external-open and save-copy controls.
  Saved files and last-generation messages never certify that reports reflect later changes.
  Preserve preview choices for unchanged files, clear stale previews when files/months change and distinguish
  missing or unreadable output from generation failure. Cancelled work must not claim completion.
  Keep inspection available while preventing competing writes.
- Direct journal editing saves a complete balanced event immediately, without AI. Preserve imported bank facts,
  event identity for one-to-one corrections and protected security trade economics. Record human provenance,
  supersede displaced reviews and reject stale forms. Show debit/credit account names and visible selection ticks.
  Keep the posting table free of vertical dividers, with account, description, debit and credit plus icon actions.
  Omit Budget impact summaries, gears, Adjust budget, breakdown panels and whole-entry reset/exclude menus.
  Remove manual budget inclusion, gift and pot controls.
  Keep investment cost controls separate and context-specific, with guarded disposal calculations.
  Preserve per-line descriptions and validate them on save with field focus and concise hints; never display
  raw model paths such as lines.0.description or incomplete-description banners while typing.
  Preserve Journal's event date, description and debit/credit columns beside its derived Budget status.
  Restrict investment-disposal holding choices to non-bank investment assets even when a bank has the
  Investment budget role. Retain bank source-evidence requirements and protected security trade validation.
  Merely opening an editor preserves absent and historical metadata exactly; retired budget labels stay inert.
- Manual descriptions receive shared, redacted reporting-month context plus complete linked events. Context
  is not a source claim. Select and validate actual evidence separately; independent instructions must not
  block one another merely because they saw the same monthly catalogue. Ask only for essential missing facts
  or genuinely indistinguishable matches. Preserve explicit multi-line instructions, including repeated accounts.
- Run one desktop job at a time with worker-owned SQLite connections and cooperative cancellation between safe
  steps. Preserve unsaved form edits and reject concurrent configuration/answer changes. Configuration forms
  retain YAML comments and identities; prevent changes that orphan accounting history. Reports preview locally
  and never process answers or call AI. Saved but unapplied corrections keep affected reports marked draft.

- Redbark is the primary bank-import option for new users, with PDF imports retained. Configure it within
  Setup's Accounts → Connections section, retaining four Setup children. Store the API key only in macOS
  Keychain under a workspace-specific identifier; never put it in YAML, SQLite, logs, command arguments or AI context.
  Use API v2 with its explicit version header, data:read and only read endpoints. Keep Redbark and ChatGPT
  in compact connection rows with icon actions and 24px before the account form. Redbark exposes a masked
  API-key field, check and setup controls inline; omit a disconnect button and never load the saved key
  into the field.
  Checking an already configured saved key stays inline. First connections, replacement keys and explicit
  setup checks open the mapping/handover dialog with Save/Cancel icons. Saving that dialog remains independent
  of Setup Save. Preserve key drafts across refreshes, guard abandoning them, and release unused checked
  sessions without database writes or network requests. Omit the preview stage and explanatory paragraphs.
  Keep bank-fact review counts in Review, outside the Setup connection row. Reserve Needs attention for
  actual connection or sync errors; pending reviews alone leave a configured connection Connected.
  The final action submits the checked connection token and mappings, running the existing complete import
  preflight internally before credential persistence and first import. Checking never writes transactions.
  Confirm each ANZ Plus AUD asset-bank mapping and handover through these explicit inputs. Existing bank details
  are still required; masked provider account numbers do not supply missing routing information.
  PDFs own earlier dates; Redbark owns booking dates from the handover onward. Reject overlapping PDF
  imports under the database write lock, including after disconnect, to preserve permanent source ownership.
  Import posted provider transactions only; provider posted does not mean posted in Aurum's journal.
  Use post_date or, when absent, date; retain all original provider date fields and wording separately.
  Convert validated integer AUD cents through Decimal. Never derive journal positions from API balances.
  Map external account/transaction IDs permanently to immutable Aurum transaction UUIDs in additive feed
  tables. Repeat unchanged observations are no-ops. Missing observations never delete transactions.
  Compare same-ID descriptions and extended descriptions case-insensitively and amounts numerically, keeping
  original payloads and stored transactions unchanged. Preserve strict integer-cent validation for new provider
  observations. Casing/formatting-only conflicts clear atomically on a successful sync; substantive fact
  changes still require Review.
  Deduplicate Redbark observations only by their external account/transaction IDs. Import unseen IDs
  within the feed-owned period as separate transactions, even when amounts, wording or dates match.
  Do not infer handover duplicates or replacement IDs from similar bank facts. A successful sync clears
  a retained duplicate warning when that provider record is imported; preserve previous resolved links.
  Older held records remain explicitly importable in Review, without a duplicate-link choice.
  Hold changed facts for known IDs in Review. Acknowledging changes keeps the saved source;
  accounting adjustments remain explicit journal work.
  Rescan available posted history inside Redbark's approximate seven-year purchase-date limit, using
  365-day years and a one-day boundary margin in requests and new-handover validation. Preserve saved
  handovers as the available history window moves forward. Apply the handover locally so late posting
  dates are not lost to a short lookback. Explain documented API errors using local wording and allowlisted
  codes/field names; never echo remote messages or credentials. Follow same-origin pagination, split
  truncated windows and reject incomplete responses. Commit complete accounts atomically, with
  stale configuration checks and cooperative cancellation. Do not change earlier successful imports
  when another account fails. Reconnection requires explicit mapping and reconciliation.
  An enabled connection always runs one startup sync after the first workspace display. Retire automatic-sync
  checkboxes and ignore legacy false preferences without rewriting them on refresh. Ordinary refresh remains
  read-only and never schedules another startup sync. Sync is
  also available on Statements and never runs Process or AI. Keep feed errors and last successful checks
  visible without claiming live-bank freshness. Feed-owned periods do not require final PDF statements;
  current periods remain Up to date, while missing coverage or conflicts withhold workflow completion.
  Disconnect removes the local credential and stops syncing while retaining all source and journal history.
  Coordinate credential deletion and database disconnect under the workspace connection lock. A denied
  deletion leaves the connection intact; a failed database save restores the key. Refresh after failed
  disconnects and retain the error. Corroborating feed evidence never relabels a PDF's original source or
  statement date as API booking facts; show additional feed dates in the source tooltip instead.
  Use separate, consistent full transfer routing from description or extended_description as evidence;
  never concatenate incomplete fields or assume a provider reference is the bank's trailing # reference.
- Run PDF importing separately. Journal runs process manual instructions and human answers before ordinary
  bank categorisation. Retired savings-pot instructions are historical only and never allocate current budgets.
  Keep desktop progress brief and distinguish current work from previously saved outcomes.
- Before AI, match transfers by their full trailing `#` reference, preserving leading zeros. Require distinct
  banks, equal/opposite amounts, dates within three days, reciprocal bank details and consistent FROM/TO
  direction. Use the outgoing date. Missing, contradictory or ambiguous evidence requires review.
  Also match `TRANSFER WITHIN` only as a unique equal/opposite pair in the same bank account, on the same
  statement date, with the same full trailing reference and routing matching that account's own bank details.
  Keep both source postings; malformed or ambiguous evidence requires review. Unlabelled pairs are neutral.
- Use Aurum's own Sign in with ChatGPT credentials under ignored `data/ai/` with owner-only permissions.
  Never copy Codex credentials or fall back to separately billed API keys. Default to `gpt-6-luna`;
  model overrides are explicit, with no silent fallback.
- Load the categorisation/manual skills explicitly from `.agents/skills/` and version their contents.
  Send batches of at most 20, compact account catalogues and relevant guidance. Redact bank identifiers,
  treat descriptions as data and validate response coverage, account choices and completed responses.
- Initial AI selects existing income/expense accounts only for clear purposes. Refunds, principal, splits
  and uncertainty need review; Other Income/Expense must not hide uncertainty. Human choices are authoritative.
- Reserve requests before inference. Skip completed work and unchanged answers. Process month includes
  retries for failed/interrupted requests.
  Reconsider unresolved investment questions once when the account/security catalogue
  changes. Save its version in the existing request context, without a database schema migration.
- Bank reviews and manual inputs are saved through the GUI into SQLite. Preserve generated identities,
  revisions and concurrent edits. Clearing an answer does not undo accounting. Retain historical baseline
  storage records used for stale checks without reintroducing file-based exchange or schema migrations.
- Keep manual corrections under their original saved instruction identity. Bank-funded events reuse imported evidence; only
  explicit bank opening positions can omit it. Non-bank changes need a movement, amount and counterpart,
  without an opening keyword. Block competing manual/bank answers before inference.
- Accepted bank reviews may learn narrow, evidence-linked guidance. Store it privately and export readable
  `categorisation_guidelines.md`. Conflicts remain inactive; guidance never rewrites unrelated posted events.
  Learning errors do not discard valid accounting. This is application memory, not model training.
- A standalone case-insensitive `#remember` in a saved bank Review response, Manual instruction or Manual
  response explicitly retains the following text through the end of that field; a trailing tag retains its
  preceding text. Project active notes from the latest saved SQLite inputs without a new store or migration.
  Clearing/removing the tag or withdrawing an instruction removes active memory while preserving history.
  Supply every explicit note to both AI skills, separately from the inferred-guidance shortlist, with bank
  identifiers, record UUIDs and transaction references redacted. Keep source provenance private. Current
  human instructions override stored notes; notes cannot invent facts or bypass accounting validation.
  Conflicts need clarification. Do not create inferred duplicates from tagged bank reviews. Export notes
  alongside learned guidance and confirm capture after a successful GUI save, without an AI call. Fingerprint
  memory in unfinished manual proposal caches and stale-result checks, leaving completed accounting skipped.

## Investments and Deferred Work

- Keep calculations and market pricing in `finance/investments.py`, security definitions in `securities.py`
  and SQLite quote storage in `database/prices.py`. Derive holdings and book cost from dated journal movements.
- Support opening holdings and purchases in AUD. Prefer actual units, execution prices and bank settlements;
  otherwise use a completed daily close on/before the date, within seven days. Cache quotes and interpreted
  proposals; reuse only with matching input, model, skills, accounting context and configuration.
- Amounts mean total spent unless stated otherwise. Capitalise stated fees once and label estimated units.
  Investment details belong on the non-bank holding line. Missing prices need a question, never zero or FX
  substitution. Price retrieval does not create valuation journal entries.
- Support average recorded-cost security sales and proportional confirmed-value account withdrawals through
  shared checked builders. Holding transfers, staking, corporate actions and FX remain unsupported. Reject
  backdated corrections that invalidate a later disposal; do not silently replay or rewrite posted events.
- Automatic inference of manual-entry guidance and cumulative goal balances remain future work. Explicit
  manual `#remember` notes and accepted bank-review learning remain supported. Retired pot/exclusion guidance
  cannot override the cash-budget policy. Preserve old allocation records without processing them again.

## Financial Reports

- Generate PDFs through Reports after journal preparation, using the selected reporting month and concise
  desktop progress. Keep generation separate from processing and use Setup for the output destination.
- Configure `output_directory` in report settings, defaulting to workspace-relative `data/reports`. Generate
  and save all four PDFs in its reporting-month subfolder.
- Reporting months end on the last Monday-Friday day, without public-holiday adjustment; the next day starts
  the next month. Reporting years follow the same December boundary. Use journal dates, not effective dates.
  Report period headings show Month Year without a month-to-date suffix; partial-month cutoffs remain unchanged.
- Read one validated journal snapshot. Only posted events affect figures; outstanding accounting produces a
  labelled draft. Reports do not process responses, call AI, close accounts or change journal postings.
- Present current reporting-year profit and current-year direct Retained Earnings movements in Retained
  Earnings. Present earlier profit and earlier Retained Earnings movements in Capital. Do not double-count
  recorded equity balances. Income statements include only income/expense accounts in the selected period.
- Derive dated holdings from the journal. Replace only their cost component with market value, preserving
  other balances in the account. The same adjustment goes to Unrealised Capital Gains equity, outside income.
  Explicit Generate first checks for a completed AUD close within seven days of the cutoff, then uses the
  latest valid cached close on/before that cutoff if fetching fails or it is newer than the fetched close.
  A valid older cached close remains usable beyond seven days; preserve its original request and price dates
  rather than relabelling it as current. Show price dates in Overview, the balance-sheet footer and investment
  price rows. The seven-day rule for original trade/reference pricing stays unchanged. Missing prices retain
  recorded value in a labelled draft. Ambiguous existing valuations need review.
- Keep ordered asset groups and equity account keys in private `config/reports.yaml`, separate from accounts.
  Require exact balance-sheet reconciliation and preserve losses, refunds and negative balances.
  Show every configured account in its relevant financial statement, including unused accounts at $0.00.
  Group Superannuation and Real Estate with the other Investments on the balance sheet.
- Choose `colour_scheme` in `config/reports.yaml`: a configured theme key or the built-in `teal` (default),
  `navy_blue`, `plum_purple`, `forest_green`, `burgundy`, `copper`, `tranquil_earth`, `warm_neutrals`,
  `velvet_neutrals`, `twilight_mauve` or `sunset_hues`. Ship all eleven in the tracked `DEFAULT_THEMES`
  catalogue so cloned and new workspaces have them without private configuration. Local theme definitions
  override built-ins; keep personal report configuration excluded from Git. Apply themes to report accents,
  icons and charts.
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
  Missing fixed colour fields use model defaults, never the shared configuration. Keep reusable sampled
  palettes and labelled companion colours in built-ins, with optional workspace overrides in config.
  Convert embedded image profiles to sRGB before sampling; textured
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
  Store the personal name in report settings and edit it globally in the sidebar footer. Headers and footers
  always display Aurum, followed by | and the personal name when supplied. Accept older prefixed names without
  duplicating Aurum. The footer adds the local generation date.
  Balance-sheet negatives use normal text colour with their minus signs.
  Chart values sit left-aligned inside bars. Small bars show only percentages, with a minimum visual width
  sufficient for the label; this intentionally enlarges their apparent share without changing the numbers.
  Bars have square left ends and rounded right ends. Category labels use natural glyph widths; wrap or shorten
  long names instead of stretching text.
  Save `YYYY-MM Balance Sheet.pdf`, `YYYY-MM Income Statement.pdf`, `YYYY-MM Budget Report.pdf` and
  `YYYY-MM Investment Report.pdf` under the configured output folder's `YYYY-MM/` subfolder
  (default `data/reports/YYYY-MM/`). Render all four before replacing previous outputs.
- Keep ordered budget categories, monthly fixed targets and overrides in private
  `config/budget.yaml`. The desktop edits fixed dollar targets only, with no transaction tracking in Setup.
  New workspaces use independent monthly plans: absent months have blank amounts and Copy last month explicitly
  stages a copy. Persist the cutoff in optional monthly_plans_from; earlier months retain legacy defaults and
  overrides. Load older plans unchanged; the first explicit budget edit adopts monthly plans from that month
  and materializes existing future overrides before changing targets. Never convert merely on viewing a month.
  Show income above separate expense and savings segmented pills on one shared amount scale, with blue/red/green
  category shades and exact hover amounts. Use a consistent muted blue/red/green GUI palette independent of
  saved report-theme budget overrides. Keep this chart beneath the Budget heading outside the amounts
  scroll area. Sort each pill's segments by descending amount, darkest to lightest; keep thick rounded ends
  and visible positive allocations at a 10px minimum, without changing exact labels. Place expense/savings
  totals above their pills, using opposite edges when needed for readable labels. Omit the positive balance
  notice and routine incomplete-amount instructions; retain actionable validation and imbalance errors.
  Add space between the fixed chart and the amount tables. Keep Income, Expenses and Savings side by side
  at every supported width, including the minimum window. Each table scrolls vertically independently while
  the chart stays fixed. Use compact Category, Amount and Links columns without vertical cell dividers,
  reserving at least 104px for Amount, then sharing remaining width 40/60 between Category and Links while
  retaining legible headers. Links shows complete account names and a thin dropdown chevron; wrap names and
  expand rows as needed, keeping amount inputs compact and vertically centred. Use an anchored multi-select dropdown;
  automatic destinations show Auto. Preserve category selection and each table's scroll through rebuilds.
  Keep malformed legacy categories reachable in a repair dialog that changes only the Setup draft.
  Use borderless action icons and no Section action. Long names and amounts remain available without creating
  horizontal overflow. Completed plans compare Income = Expenses + Savings + Investments;
  missing targets remain unset.
  Load older incomplete plans so users can correct them;
  an invalid budget must not block journal processing or unrelated configuration saves. No rollover or proration.
  Unset target numbers display a dash and retain an empty progress pill. Zero targets also stay empty when
  actuals are zero or negative; a positive actual fills the pill in its normal section colour.
  Unconfigured income/expense accounts remain visible as unbudgeted actuals.
- Budget actuals are a deterministic cash-flow projection of complete posted events. Configure exact
  cash account keys, designated investment account keys and one expense category per liability. Reports and
  Journal statuses share the same projection; AI chooses valid journal accounts and supported investment intent,
  never budget labels, exclusions, amounts or virtual allocations. Old labels and allocations remain readable
  historical data but are inactive for all months. Preserve postings, UUIDs and human histories.
- Income Statement follows income and expense accounts independently of the budget. Employer super and other
  noncash income remain income where appropriate but outside the configured Cash budget. Unrealised market movement
  uses the configured unrealised-gains equity account and never income or budget. Opening provenance excludes
  starting positions; never infer it merely from a description or Capital counterpart.
- The two fixed savings destinations are Savings and Investments. Investments is gross Cash paid to
  designated Investment accounts. Savings is income minus cash expenses minus Investments, including cash
  left in Everyday; a negative result is a valid drawdown. Total savings equals income minus expenses.
  Transfers among configured Cash accounts, including same-account internal transfers and reverse transfers,
  are neutral and appear as Savings transfer in Journal. Do not count their amounts again as Savings.
  Investment receipts into Cash accounts join mapped cash investment income in Investment returns, using the full
  receipt once and never negative Investments or a second realised gain.
  Capital gifts into Cash accounts use fixed Gift received income without income-statement income.
  Gifts given credit configured Cash accounts and debit only configured Capital; exclude these complete
  events from budget without income-statement impact. Mixed counterparts still require existing rules/Review.
  Use Income rather than Inflows in all user-facing budget labels and PDFs; this wording never changes the
  distinction between budget receipts and income-statement recognition.
- Liability repayments use the liability's single configured expense category for the complete cash payment,
  including interest. Principal debits liability; interest debits expense. Credit-card purchases affect the
  income statement at purchase and the cash budget at repayment. Refunds reverse their expense category.
  New borrowing receipts and ambiguous or unsupported movements require Review. Do not invent other handlers.
- Security disposals release average recorded cost and recognise realised gain at sale, even with proceeds
  retained in brokerage cash. Balance-only withdrawals release proportional cost using a confirmed
  pre-withdrawal value and attributable unrealised equity. Missing basis, valuation or gain attribution,
  ambiguous chronology and unsupported zero-cent cost dispositions require Review. Shared immutable disposal
  calculations are recomputed against current history before commit; preserve full bank facts, protect stale
  revisions and reject earlier changes that invalidate later recorded disposal costs. No automatic valuation
  adjustment or second gain on a later cash withdrawal.
- Setup keeps user income/expense targets, maps the existing investment target to Investments and combines
  other old savings targets into Savings. Merge legacy investment_withdrawals targets into investment_return,
  retaining income-account links, inherited/independent monthly rules and explicit unset target components.
  Show fixed Gift received, Investment returns, Savings and Investments rows; Investment returns retains an
  editable income-account picker while the other fixed rows use automatic account treatment. No pot/residual
  controls, gift picker, From/To choices, capacity warnings or budget override switches. Incomplete targets
  may be saved while account roles are configured; they never block the income statement or actual cash totals.
  Old configurations are normalised in memory until an explicit Setup save. Saved PDFs change on Generate.
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
  Assign investment donut colours from medium to lighter shades in the existing asset-class order for all
  themes and page modes. Evenly sample 25%–65% of the palette for the displayed class count, blending shades
  for two to five classes without cycling; one class uses the 25% shade. Expense charts keep the full scale.
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
- Whole-account investment postings carry explicit contribution, earnings or opening-cost treatment in the
  optional `journal_account_investments` table. Net contributions add cost; earnings change value but not cost.
  For UNISUPER, credit Unrealised Capital Gains for fund earnings, keeping them outside personal income/budget
  income. Opening cost must be supplied separately from opening value. Unclassified movements keep cost unknown.
  Preserve metadata in atomic corrections and revisions; omit absent fields to retain older snapshot identities.
  Super units, average cost and quote remain a dash. Investment P&L includes its recorded fund earnings.
  Review lists unclassified posted investment movements through the report cutoff, including earlier months;
  Reports links to that queue. Open complete events at the indicated posting in the direct editor. Classification
  and opening historical cost require explicit human choices. Refresh the queue after saves and regenerate
  reports to clear resolved draft notices.
  Investment treatment uses an explicit Qt list popup with a visible thin chevron in the posting's details.
  Show it only for configured account-based holdings or existing treatment metadata. Label the choices
  Unclassified, Capital in / out, Return or fee and Opening balance, with a brief cost-basis explanation.
  Unclassified preserves an existing absent or explicit unclassified value until deliberately changed.
  Show historical opening cost only for Opening balance; never infer contributions or change counterpart accounts.
