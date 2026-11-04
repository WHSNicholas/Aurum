# Aurum User Guide

Aurum is a desktop app for bank statements, manual accounting, reviews and financial reports.
Use the app for all inputs and processing. Saved instructions, responses and journal entries live in SQLite;
there are no manual/review CSV inputs or separate processing commands.

## Open Aurum

Open **Aurum.app** from your personal Applications folder. You can keep it in the Dock.
The launcher uses this project's Python environment, so keep the Aurum project and its `.venv` in place.

For development, run `aurum/scripts/app.py` in PyCharm, or use the one startup command from the project root:

```bash
.venv/bin/python aurum/scripts/app.py
```

Opening Aurum reads the saved workspace. Importing, processing, editing and generating reports require an
explicit action. The folder icon in the sidebar footer opens another workspace through the GUI.
The refresh icon reloads saved facts and settings.

## Setup

Setup has five sections: **Accounts**, **Securities**, **Budget**, **Reports** and **ChatGPT**.
Its summary cards show account counts, monthly budget allocations and the selected theme.
The month selector remains visible in every section; only the budget targets depend on that month.

### Name and appearance

Edit **Name** beneath the Setup title, then click the save icon at the bottom.
This one setting supplies the name in the sidebar, Setup and generated report headers and footers.
Enter the personal name only; reports add **Aurum |** automatically.
Regenerate reports to apply a changed name to saved PDFs.

Choose a theme in the **THEME** card. Save, then refresh the workspace to apply its colours to the app.
Fixed themes retain their own appearance; adjustable themes offer Light/Dark.
Palette definitions remain in code rather than appearing as editable colour controls.

The bottom save icon saves staged Setup changes. Reload discards the current section's changes only after
confirmation. Navigation protects unsaved edits. Unchanged settings retain their existing controls on refresh;
external changes are still detected.

### Accounts and securities

Create missing workspace files with Setup's explicit create action. Existing files remain untouched.
New workspaces start with the fixed Capital, Retained Earnings and Unrealised Capital Gains equity accounts.

Accounts are grouped as assets, liabilities, equity, income and expenses. Select an account to edit it.
Use the adjacent plus and bin icons to add or remove definitions. Keep accounts needed by saved history;
use the closing date when an account closes. Dates accept `YYYY-MM-DD` or `DD-MM-YYYY`.

Enter BSB and account number together for a bank account, preserving leading zeros. Both blank means the
account has no bank details. Accounts start at zero; record starting balances as balanced journal entries.

Add equities and cryptocurrencies under Securities. Stable account/security identifiers are saved automatically.
Changing a display name does not change its identity. System equity names and roles are fixed.

### Budget

The budget follows the shared reporting month. Enter monthly amounts directly, or use the copy icon to
stage last month's budget. Copying does not save automatically.

The chart shows income above expenses and savings. Blue represents income, red expenses and green savings.
Segments run from largest allocation to smallest; hover for category amounts. The tables list every category
without separate inner scroll areas. Use plus/bin icons to add or remove categories.

Categories can use account defaults or explicit labels on journal postings. Virtual savings pots divide the
month's surplus without creating bank balances or journal entries. The residual pot receives the unallocated
remainder. A pot allocation is a monthly contribution, not a cumulative goal balance.

### Reports

Choose the output folder and assign asset accounts through the **Account / Report group** table.
**Other Assets** leaves an account ungrouped. **Manage groups** adds, renames, reorders or removes groups.

Account-based investments track contribution cost for assets such as superannuation without requiring units.
Investment display labels change report labels without renaming accounts.
Use the theme card above the tabs for report appearance.

### ChatGPT

Click **Sign in with ChatGPT** and authorise Aurum in the browser. Select a model and save the selection.
A saved sign-in is separate from a successful connection check; use the connection refresh icon when needed.

Aurum uses its own ChatGPT sign-in under the workspace's private AI folder. It does not use Codex credentials
or silently switch to a separately billed API key. The selected model stays explicit; there is no fallback.
Friendly labels such as **GPT 6.1 - Sol** retain the original model identifier internally.

## Reporting month and Overview

Use the arrows beside the month. The small date range shows the actual reporting interval.
Reporting months end on the last Monday–Friday day, without public-holiday adjustment. The next day begins
the following month: for example, 31 October 2026 belongs to November's reporting period.

Overview reads posted accounting only:

- **Net Worth** uses the balance sheet's calculation, including investment valuations from saved prices
  for the same cutoff. Refresh never downloads prices. **Provisional** means some holdings retain their
  recorded value; hover for details and Generate reports to check prices or required investment review.
- **Income** and **Expenses** apply budget inclusions, exclusions and signed refunds. Their counts include
  each contributing journal event once per side, including manual events.
- **Savings** is budget income less expenses, not a bank balance.

Amounts use small spaces for thousands. Pending work is not included, and incomplete budget targets do not
block actual figures.

**Workflow** shows Sources → Journal → Review → Reports. Completed stages retain their icons and gain a
highlighted background and tick. **Next step** explains the remaining work; use the sidebar to navigate.

**All done** requires the earlier steps to be complete and all four report PDFs to exist for the selected month.
While only transaction lists are available, the completed workflow shows **Up to date** instead.
A valid imported final statement takes precedence over its retained list. Missing, unreadable or unimported
current source files withhold completion. Refresh checks the files again.

These indicators describe saved activity; they do not prove that every expected bank account was supplied
or that saved reports include later accounting changes.

## Statements

Click the folder icon beside **Choose PDFs**, or drop PDFs on the page.
The chooser starts in `data/bank_statements`; PDFs from other locations are also accepted.

Choosing or dropping files validates and stages them under standard names before importing:

- Final statements: `YYYY-MM_account.pdf`, for example `2026-11_everyday.pdf`.
- Transaction lists: `YYYY-MM_account_list.pdf`, for example `2026-11_everyday_list.pdf`.

The account comes from the PDF's bank details. Final statements use their calendar end month; cumulative lists
use the reporting month beginning at their commencement date. External originals remain untouched.
A nonstandard filename already inside the managed folder is removed only after its validated replacement succeeds.

Preparation shows a ready count, such as **3 PDFs ready**. Click the import icon to save transactions.
Preparation alone does not import anything. Clear removes files from the queue; failed or unfinished files
remain available for retry. Differing files targeting the same destination require separate preparation.

Importing a known PDF skips its already saved transactions. An explicit managed import also reconciles validated
source filenames in the saved import/transaction metadata. It preserves transaction identities and postings.

Search stays left-aligned with its icon. Selecting a transaction shows details beneath the table.
The clock icon opens import history, including recent outcomes and the latest successful import per filename.
Repeated uploads do not add a visible history row every time. Empty or filtered-out tables show **No entries**.

### Update a month using transaction lists

Download the cumulative ANZ transaction list for each account. Choose/drop it, import it, then click **Process**.
Repeat with newer lists as the month progresses. A newer list replaces that account's managed `_list.pdf`;
the final statement is kept separately when available.

Overlapping imports add only new transaction occurrences. Existing transaction IDs and posted journal entries
remain unchanged. Process handles new saved work and skips already completed entries.

Empty and populated ANZ lists are supported, including optional running balances and opening/closing totals.
Supplied balances are validated. Unsupported layouts, contradictory facts or ambiguous repeated rows produce
an error instead of risking duplicate accounting. An older differing list cannot replace a newer managed list.

At month end, import the final statements and Process any new transactions. The retained lists do not prevent
completion once the corresponding valid final statements are imported.

## Manual

Use **+** to choose **Instruction** or **Journal entry**. Both open the shared editor, and you can switch
methods while keeping their separate drafts. The main page lists saved instructions and direct entries.

For an instruction, enter the accounting date and describe the event, amount and counterpart.
Link supporting **Bank Transactions** where relevant. Save the instruction, then click **Process**.
If more information is needed, answer in **Response**, save, and Process again.

For a direct journal entry, enter balanced debit and credit lines and save to post immediately without AI.
Selecting an account opens its picker across the field. Optional budget and investment controls belong to the
selected posting. A bank-funded event normally needs imported evidence; **Starting bank balance** is an explicit
exception for supported unlinked opening bank postings.

The detail card beneath saved entries shows the unsigned event amount, description, accounting date,
bank transactions, budget treatment and posting table. Journal uses the same card.
Closing or switching away from a dirty editor offers Save, Discard or Cancel.

### Budget and investment treatment

**Normal rules** restores account-based budget treatment. **Exclude whole entry** labels every posting as
excluded, including newly added lines. **Custom by line** indicates mixed or explicit category labels.
Budget labels change budget reporting without changing accounts or money amounts.

Transfers and opening positions do not create income or new savings. Interest remains income.
Same-account `TRANSFER WITHIN` pairs retain both bank observations and are neutral when unlabelled.
Labelling their debit and credit savings sides can show a move between savings categories in the budget.

Investment treatment describes whether a holding movement changes contribution cost or represents another
supported movement. Resolve unclassified movements in Review. Whole-account contributions, including excluded
superannuation contributions, should display their actual budget treatment in the journal editor.

## Review

Review combines bank questions, unfinished manual instructions and investment classifications that affect the
selected reporting cutoff, including earlier unresolved items.

For bank items, type **Response** and move between rows without saving each one. The save icon saves all
edited answers together. Changed accounting or a newer saved answer rejects stale input rather than
overwriting it. Refresh retains drafts so you can compare or discard them.

**Status** explains a saved question, processing outcome or required action; the posting badge/table shows
whether accounting is posted. **Edit** opens the complete journal editor for an immediate balanced correction.
Manual items open their shared editor. Investment items open the relevant posting's treatment controls.

Click **Process** after saving answers. It handles saved manual instructions and reviews before ordinary bank
categorisation, then applies monthly savings allocations. It retries failed/interrupted work, skips unchanged
completed work and learns eligible accepted-review guidance. Stop finishes the current safe step before stopping.

## Remembering guidance

Include `#remember` in a human instruction or response when you want that guidance retained.
Saved tagged text contributes to Aurum's private application memory and is supplied to relevant future AI work.
This is application guidance, not model training, and it does not rewrite unrelated posted events.

Accepted bank reviews can also produce narrow, evidence-linked guidance during Process.
Conflicting guidance stays inactive. Learning failures do not discard valid accounting.
Readable guidance remains under `data/ai/categorisation_guidelines.md`.

## Journal

Journal shows saved monthly events and the same compact detail card used in Manual.
Use **Edit** for a complete balanced correction, retaining imported bank facts and source identities.
The shared **Process** button processes saved work for the selected month.
There are no CSV import/export controls; enter and review work in the app.

## Reports

Choose a report type to preview its saved PDF. **Generate** creates and saves all four reports.
Report settings live in Setup, and accounting issues live in Review.

Reports are stored under the configured output folder's monthly subfolder, by default `data/reports/YYYY-MM/`:

- `YYYY-MM Balance Sheet.pdf`
- `YYYY-MM Budget Report.pdf`
- `YYYY-MM Income Statement.pdf`
- `YYYY-MM Investment Report.pdf`

Generating renders all four before replacing saved outputs. Reports use posted accounting and do not process
responses or call AI. Outstanding accounting or unavailable required prices produces a labelled draft.
Saved PDFs are snapshots: regenerate after changing accounting, names or report settings.

The balance sheet and investment report can revalue supported security holdings using a completed AUD close
on or before the cutoff within seven days. Missing prices retain recorded value with a draft warning.
Overview uses the same calculation and saved prices. Refresh after generation to see updated valuations.
An older saved PDF can still differ after later accounting changes; regenerate it to update its snapshot.

Current reporting-year profit and direct current-year Retained Earnings movements appear in Retained Earnings.
Earlier profit and earlier Retained Earnings movements appear in Capital. Unrealised market movements remain
outside income.

Purchases and opening holdings support AUD amounts, units and stated fees. When units are estimated from a
completed closing price, that is identified. Sales, holding transfers, staking, corporate actions and FX remain
outside the current supported investment workflow.

## Local files

The app manages these private locations:

| Location | Purpose |
| --- | --- |
| `config/` | Saved account, security, budget and report settings |
| `data/aurum.sqlite3` | Imported transactions, saved inputs, postings, reviews and application memory |
| `data/bank_statements/` | Managed PDF statements and cumulative lists |
| `data/reports/` | Generated monthly PDFs, unless another output folder is selected |
| `data/ai/` | Aurum sign-in credentials and readable learned guidance |
| `data/legacy_csv/` | Retired CSV files preserved for reference; never read as app inputs |

No manual/review input folders are created. The former terminal utilities and Rename Statements.command
are retired; PDF preparation in Statements performs the same naming and validation through the GUI.
Keep financial data and credentials local and manage your own backups before changing or replacing a database.

## If something needs attention

- **No entries:** the table is empty or its search/filter hides every row. Clear the search or change the filter.
- **Missing workspace files:** use Setup's explicit create action. Do not reset an existing database to refresh it.
- **Changed or stale response:** refresh, compare the current event and resubmit the intended edit.
- **PDF preparation failed:** inspect the file outcome; correct the source or choose conflicting versions separately.
- **Processing failed:** check the activity details, correct the issue, then click Process to retry.
- **Report draft:** resolve accounting questions in Review, check investment classification/prices, then Generate.
- **Sign-in failed:** use Setup → ChatGPT to reconnect and check the selected model.
