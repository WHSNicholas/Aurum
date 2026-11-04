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
  Open the existing workspace read-only; creation, saves, imports and processing require explicit UI actions.
  Main page titles and the Aurum wordmark use the PDF title's Cormorant font, semibold with small caps.
  Keep larger initial capitals and smaller capitals for lowercase letters. Place a 13px normal-weight muted subtitle
  directly below every title on one line, with no added gap and roughly 35px before the page content.
  Overview and Setup use the personal name, and Setup keeps it editable.
- Overview shows the report owner's name, balance-sheet net worth through the cutoff and monthly budget income,
  expenses and savings. Use the shared report calculation with posted entries and the latest valid saved
  security close on or before the cutoff, matching the security and provider symbol. Both the quote's original
  requested date and price date must be on or before the cutoff; choose the newest price date and skip invalid
  records without altering them. Refresh stays read-only and never fetches prices. Net Worth's caption uses
  one As of cutoff-date line (today for the current month); keep market-price dates only in its tooltip.
  When no valid saved quote exists, or a manual valuation is ambiguous, retain recorded holding values as
  in reports; mark the card
  Provisional and name affected holdings in its tooltip. Whole-account cost questions do not make their
  recorded values provisional. Preserve budget exclusions and signed refunds; incomplete budget targets
  must not block these actuals.
  Label the first card Net Worth. Keep its cutoff date and Savings' Income less expenses caption. Income
  and Expenses show their included transaction counts: each posted journal event counts once per side,
  including manual events, gifts and signed refunds, without multiplying split postings. Use the same
  budget inclusions and reporting cutoff as their totals. Omit explanatory paragraphs beneath the cards.
  Centre the financial cards' contents. Format Overview money and transaction counts
  with narrow no-break spaces (U+202F) for thousands, retaining dollar signs, cents and negative signs.
  Omit the Overview header's Updated timestamp; refresh activity stays in its existing spinner and status.
  Use local replaceable Solar Bold SVG icons and a Sources → Journal → Review → Reports flow titled Workflow.
  Keep the original outline set unchanged in gui/assets/ui, with Solar assets in gui/assets/solar_bold.
  ICON_SET in gui/icons.py selects solar_bold or outline on restart; the same resolver supplies themed
  stylesheet icons. Retain the four thin open navigation/disclosure chevrons instead of filled triangles.
  Preserve theme tinting, transparent interior details, tooltips and accessible names. Keep source URLs,
  creator attribution, licence and adaptation notes with the downloaded assets; PDF icons are independent.
  Retain each stage's original icon on the left; completed stages use a highlighted background and an
  upper-right tick instead of status micro-labels. Completed stages and the All done card use the selected
  theme's primary sidebar colour with contrasting text and icons. Omit the Posted figures · AUD badge and visible
  saved-activity caveat. Counts still reflect saved activity, not proof all expected statements were supplied.
  Distinguish queued answers from questions/retries and include investment classifications affecting reports.
  Keep next-step guidance informational; use sidebar navigation instead of Overview action/link sections.
  Complete the Reports stage and show All done when Sources, Journal and Review are complete and all four
  expected report PDFs exist in the configured output directory's selected reporting-month folder.
  Check saved files on refresh so this works after reopening the app. Missing PDFs or an incomplete earlier
  workflow stage clear completion on refresh. Inspect current managed source PDF headers and hashes on refresh;
  when otherwise complete, show Up to date while a transaction list awaits its final statement. Missing,
  unreadable or not-yet-imported source PDFs also withhold All done. Derive this from files and existing imports,
  without persisted document flags or changes to report accounting/draft logic. Overview has no session or
  report-freshness gate; keep detailed draft handling on Reports. Show Month Year followed by compact previous/next
  arrows, retaining exact reporting dates and unsaved-work guards. List main sidebar destinations without
  Monthly Workflow or Workspace headings. Read-only refresh uses an animated icon; actual jobs retain progress,
  Stop and an accessible information icon for activity details.
- Setup keeps Accounts, Securities, Budget, Reports and ChatGPT as five persistent children in the left sidebar.
  Match Setup and Overview's plain titles without heading icons, with close subtitles and more space before
  the summary cards. Overview says Your finances at a glance; keep the personal-name field beneath Setup's title.
  Centre Setup card contents with aligned icon, heading and detail rows. Use Accounts, Budget and Theme
  as their main headings; show account-type counts, monthly Income/Expenses/Savings allocations and a borderless
  theme picker underneath. Flow budget totals with middle dots across at most two lines; retain full totals in
  the tooltip for unusually long figures. Centre the theme name on the card, allowing equal space on either side
  of its text. The theme popup uses rounded rows, palette previews and a checkmark for the current selection.
  Use the same rounded popup surface and selection treatment for other dropdowns, including native outer corners.
  Buttons retain hover, pressed and keyboard-focus backgrounds without borders or macOS focus-ring outlines.
  Keep the selected month and budget readiness in the card's tooltip. Align main navigation with the brand;
  indent Setup's five children.
  Keep the shared month selector at the right of the current page title on every page and Setup section.
  Show its inclusive scheduled reporting range in 10px text beneath the month name, above compact
  Process/Generate actions on the subtitle row. Omit years from the visible range and align its right edge
  with the final year digit in the month label, before the arrows. Retain full dates in tooltips and
  accessibility text. Use the same reporting-period calculation as reports, without shortening to today's cutoff.
  Align month and title text baselines on the same row; keep the exact reporting range in the month tooltip.
  Align sidebar Aurum/Personal Finance with the page title/subtitle using matching font sizes and row spacing.
  Centre Aurum and Personal Finance horizontally within the sidebar. Place a short, attributed quotation
  between Setup navigation and the sidebar footer, with another centred at the bottom of Overview.
  Centre the sidebar quote and attribution together vertically between ChatGPT and the owner's name. Keep
  navigation at its natural height, allowing it to shrink and scroll in short windows. Use regular Cormorant
  at 16px for the sidebar quote and 18px for Overview, with smaller attribution text and no italics.
  Use the same subtitle font size for Personal Finance; align Process/Generate text with the subtitle baseline.
  Lift the month text and date range four pixels within their allocated cells without moving the arrows
  or Process/Generate buttons; keep a clear gap above the compact primary action.
  Put refresh in the sidebar footer and primary Process/Generate actions beneath the month selector.
  Keep Process and Generate inset fourteen pixels from the content's right edge. Share 120 by 30 pixel
  button bounds and an 18px play icon eight pixels from the button's left edge, independent of label width.
  Empty or fully filtered activity tables show No entries inside their viewport, without a synthetic row or
  a no-matches label above the table. Preserve useful populated counts and actual error messages.
  Setup summary-card headings use full uppercase: ACCOUNTS, BUDGET and THEME.
  Only Budget's targets depend on that month; other Setup settings remain global.
  Reordering controls use thin local chevrons matching the month selector, never native filled arrow glyphs.
  Share the heading, labelled Name field and cards across all five sections. Name is the global personal-name
  setting for Setup, the sidebar and generated report headers/footers; save explicitly and regenerate PDFs
  to update saved snapshots. Use accessible icon buttons for routine actions
  and a fixed Save/Reload bar with explicit unsaved/error/saved states. Navigation never saves silently;
  preserve selection and scroll after saving. Protect separate drafts and stale versions across multi-file saves.
  Reuse clean Setup forms when all configuration bytes are unchanged; explicit reloads still rebuild them.
  Group accounts and securities by type and indent their entries without reordering saved definitions. Keep
  account fields top-aligned at fixed vertical spacing. Infer bank details from BSB/account-number text; both
  blank means no bank details, while a partial pair must pass normal validation before saving. Empty bank fields
  stay editable with muted styling. Closing date is optional text, with no enable checkbox or calendar popup.
  Display the share instrument type as Equities and label market as Exchange / market, retaining stored values.
  Use adjacent accessible plus/bin icon buttons at the top right of each account/security list.
  The three system equity
  accounts have fixed names, types and role mappings in Setup; retain existing legacy values without renaming.
  The theme card exposes Light/Dark only for themes with adjustable appearance. Keep detailed palette definitions
  out of the GUI, preserving existing custom settings.
  GUI appearance follows the saved report theme on a successful workspace refresh, including fixed-theme
  backgrounds and independent colours. Keep it scoped to each workspace window; retain the current appearance
  on invalid or failed refreshes. Unsaved theme choices do not change the GUI. Adapt controls, popups, icons
  and selections for contrast while preserving budget and positive/negative meanings.
  Sunset Hues uses its fourth palette shade (dusty rose) for GUI panels instead of the pale-yellow report
  group fill. Keep this desktop override out of report configuration and PDF rendering.
  Assign each asset account through one
  Account / Report group table, with direct moves and an unassigned Other Assets choice. Keep referenced missing
  keys visible for repair. Manage groups adds, renames, reorders and removes groups in a cancellable draft dialog;
  removing a group leaves its accounts ungrouped. Preserve unchanged group/member order and explicitly configured
  Other Assets groups. Keep Reports concise, with account-based investment and optional-label guidance in tooltips.
  Give account-based investments the budget tables' centred, bounded width and clean headers without cell grids;
  show every row in the outer form scroll. Centre budget category names, monthly amounts and their editors.
  Create missing workspace files explicitly before editing; failed reloads must not leave stale forms active.
- Setup's ChatGPT settings are workspace-wide, with connection before model selection.
  Distinguish saved credentials from a live connection check. Preserve staged model choices across refreshes,
  require an explicit save action and protect unsaved selections together with other Setup edits when leaving.
  Save configuration before starting an asynchronous model save; resume navigation only after success and refresh.
  Display friendly model labels such as GPT 6.1 - Sol while retaining exact stored model identifiers.
  Failed or cancelled checks must not claim success or navigate away from an unsaved choice.
  Keep all connection work behind explicit actions.
- Statements combines PDF preparation and the selected month's transactions on one page, without import/table
  tabs. Choose PDFs opens the workspace's data/bank_statements folder; choosing or dropping local PDFs explicitly
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
  button with a light symbol; retain clear/retry controls and per-file outcomes in import history.
  Left-align the PDF chooser/count/action group and feedback within the import panel. Align chooser and search
  text with balanced icon insets; search icons sit twelve pixels inside the left edge and remain vertically
  centred. Use the same search spacing for Statements, Manual, Review and Journal, retaining native editing
  and clearing without shifting the prompt or icon on focus.
  Keep report-folder and import-history icons centred in 36px square buttons aligned with their adjacent fields.
  Keep search and import history as compact icon controls. Open import history in a separate window on demand,
  showing only the latest successful import for each canonical filename while retaining all hashes internally.
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
  Preserve source identities and answer revisions. Bank answers stay inline and retain drafts across selection,
  filters and refreshes. One save icon commits all edited answers atomically; a stale answer rejects the batch.
  Build displayed review revisions from one shared accounting snapshot; saves still recheck live versions.
  Manual items open the shared popup
  without leaving Review; investment items open the complete journal editor at the relevant posting.
  Use the shared journal card for posted Review events and a transaction card for unposted bank evidence,
  without Answer/Sources tabs. Show plain Status text, Response input and Edit. Preserve bank facts and posting
  metadata in the card and copy/tooltip details. Keep Response and its textbox together at a fixed natural height
  when the window grows. Status displays a saved question, processing explanation or required next action;
  the card's Posted badge and postings show the accounting state. Manual shows one
  saved-history list for instructions and direct journal entries, keyed by manual identity; latest human outcome
  identifies a direct entry without duplicating its event.
  Its plus menu opens a shared Instruction / Journal entry popup. Preserve each draft and its original revision
  while switching or reloading the other method. Save only the selected method, clearly distinguishing queued
  instructions from immediate journal posting. Keep virtual savings allocations out of direct journal mode.
  Use thin local disclosure chevrons for optional bank links and saved outcomes. Keep monthly processing separate
  as Process beneath the month selector, covering saved instructions and bank transactions for the month.
  Use the same placement and label in Review and Journal. Read only editor-specific context when opening Manual;
  do not rebuild unrelated review queues. Keep Instruction and Response editors the same height, with a scrollbar
  gutter, and show all ordinary journal posting rows without an inner table scrollbar.
  Label the optional bank-link disclosure Bank Transactions. Account fields open their choices across the whole
  field while retaining typed search. Use Debit/Credit headings without a totals calculator; validate exact
  balance on save. Size posting options to their current budget/investment controls. Show Starting bank balance
  only for unlinked bank postings, retaining explicit confirmation and bank-evidence validation.
  Show fixed save controls and clear unsaved/saved/error states; preserve dirty input and its original revision
  through filters and refreshes. Only an acknowledged save may clear that submitted form or continue navigation.
  Keep linked-record destinations when navigation waits for a save; explain unavailable monthly destinations.
- Process month also assesses eligible accepted bank reviews missing learned guidance and exports that guidance.
  Desktop Process month retries failed/interrupted accounting and guidance requests while skipping completed
  work and unchanged successful answers. There is no separate retry control or command-line processing mode.
  Cancellation prevents starting another stage; learning/export failures never discard saved accounting.
  Keep Process prominent with its play icon. Show a spinning activity
  indicator with stage text during desktop work. An unavailable investment queue prevents workflow completion.
- Journal separates monthly processing from selected-entry actions and has no CSV exchange controls.
  Selecting an event opens a read-only panel below the table with exact debit/credit totals,
  named postings, full source details and explicit edit/review/manual actions. Manual and Journal use the same
  JournalDetails widget: one unsigned total-debit event amount, description, left-aligned accounting date,
  bank-transaction count and budget treatment together in one horizontal row, with a narrower posting table
  on the right capped at 480px. Stack the table
  below metadata in Review's compact pane with extra separation above the table. Use compact posting rows and
  a shared text inset for amounts, descriptions, metadata, account names, Status and Response. Leave an 18px
  gap between saved-record tables and their detail cards in Statements, Manual and Journal. Hide the gap when
  no detail card is selected. Keep Response text aligned with the card's text rather than its outer border.
  Measure actual styled row heights so up to five postings remain
  fully visible, and let the surrounding detail panel follow its content height. Omit a Sources tab. Keep
  source and posting metadata accessible in tooltips/copy and preserve selection on refresh.
  Reports has one preview view, a rounded report-type picker and Generate with a play icon. Keep draft/error messages
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
  Put optional budget categories and investment treatment in the selected posting's inline details, retaining
  each line's draft when switching. Budget Normal rules explicitly restores all line defaults; Exclude whole entry
  explicitly labels every line exclude, including newly added lines. Show Custom by line for mixed labels.
  Filter category choices by account type and configured restrictions; retain unavailable existing labels visibly
  for repair. Merely opening an editor must preserve absent metadata and existing labels exactly.
- Manual descriptions receive shared, redacted reporting-month context plus complete linked events. Context
  is not a source claim. Select and validate actual evidence separately; independent instructions must not
  block one another merely because they saw the same monthly catalogue. Ask only for essential missing facts
  or genuinely indistinguishable matches. Preserve explicit multi-line instructions, including repeated accounts.
- Run one desktop job at a time with worker-owned SQLite connections and cooperative cancellation between safe
  steps. Preserve unsaved form edits and reject concurrent configuration/answer changes. Configuration forms
  retain YAML comments and identities; prevent changes that orphan accounting history. Reports preview locally
  and never process answers or call AI. Saved but unapplied corrections keep affected reports marked draft.

- Run PDF importing separately. Journal runs process manual instructions and human answers before ordinary
  bank categorisation, then apply savings-pot instructions against the completed income and expense figures.
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
- Sales, holding transfers, staking, corporate actions and FX remain unsupported. Weighted-average disposals
  and replay of dependent activity after backdated corrections belong to that future workflow.
- Automatic inference of manual-entry guidance and cumulative goal balances remain future work. Explicit
  manual `#remember` notes are supported. Monthly savings
  pots divide contributions without changing a bank account's full balance or claiming a cumulative balance.
- Configure virtual pots and the residual category in `config/budget.yaml`. Total savings equals budget income
  minus expenses. Savings receives the residual; Sparkle, Extravagance, Postcards, Peace and Homeward require
  explicit allocations. Display Savings first, then those five pots in that order, then investment contributions.
  Use names without descriptions. Bank transfers and opening positions do not create new savings; interest
  remains income and contributes to the surplus when unspent.
  Keep pots out of the ledger. Store one replaceable allocation per manual identity in `savings_allocations`.
  Use the existing manual AI skill to interpret instructions; Python checks permitted pots and live monthly
  capacity atomically. Unchanged answers do not call AI again. Clearing a response does not undo an allocation.

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
  Store the personal name in report settings and edit it globally at the top of Setup. Headers and footers
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
  Add space between the fixed chart and the amount tables. Centre the tables at a preferred maximum 740px,
  shrinking toward 80% of available width with a practical 600px minimum bounded by the viewport. All rows
  share one outer scroll area, without vertical cell dividers. Use borderless action icons, centred switches
  for independent pots and radio dots for the exclusive residual choice, with no Section action.
  Category/account assignments and virtual-pot choices stay visible.
  Require complete fixed targets and exact planned Income = Expenses + Savings when saving stored plans;
  report generation validates its requested month, without blocking it on another month's incomplete plan.
  Load older incomplete plans so users can correct them;
  an invalid budget must not block journal processing or unrelated configuration saves. No rollover or proration.
  Unset target numbers display a dash and retain an empty progress pill. Zero targets also stay empty when
  actuals are zero or negative; a positive actual fills the pill in its normal section colour.
  Unconfigured income/expense accounts remain visible as unbudgeted actuals.
- Budget actuals follow reviewed income/expense postings, including signed refunds and non-cash expenses.
  Journal posting labels are the source of truth for budget exclusions; do not exclude whole events by account
  configuration. Show saved exclusions in the direct editor's Budget treatment, and label every posting
  `exclude` when excluding a complete event. Default income/expense postings remain included; other account
  types require an explicit budget category. Gifts explicitly labelled on Capital count only as
  budget income. Savings is the residual after expenses, including any money retained in transaction accounts.
  Explicit asset/liability labels and manual pots allocate that total, reducing the catchall by the same amount.
  Later accounting corrections that make allocations exceed capacity flag a draft instead of silently discarding
  them. Preserve negative total savings in deficit months. Without pot settings, show an automatic Savings row.
- A complete confirmed same-account `TRANSFER WITHIN` pair may carry two explicitly selected different savings
  categories: its outgoing bank withdrawal is a ledger credit reducing the source category, and its incoming
  bank receipt is a ledger debit increasing the destination. Retain both signed Actual amounts without changing
  total savings or bank balances. This exception may move savings from earlier months; it neither consumes nor
  releases current-month manual allocation capacity. Ordinary allocations retain the surplus check. Do not infer
  categories or cumulative pot balances, and leave unlabelled pairs neutral.
- Use the direct journal editor or saved human-review/manual instructions to assign or remove budget
  labels. Reports never infer them from descriptions or call AI. Preserve old posted treatment until a changed
  answer is submitted.
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
  Unclassified, Contribution, Return or fee and Opening balance, with a brief cost-basis explanation.
  Unclassified preserves an existing absent or explicit unclassified value until deliberately changed.
  Show historical opening cost only for Opening balance; never infer contributions or change counterpart accounts.
