# Aurum User Guide

Aurum turns ANZ Plus bank statements into a local accounting journal. You review uncertain transactions
in CSV files and use a second set of CSV files for manual entries. SQLite stores the accounting and saved
review history. Balance sheet, income, budget and investment PDFs report the posted accounting;
dashboards are planned.

Run the commands below from the **Aurum project root**, the folder containing `aurum/` and `requirements.txt`.
Examples use synthetic accounts and amounts; replace them with your own configuration.

## Monthly checklist

After completing the setup below:

1. Put the month's ANZ Plus PDFs directly in `data/bank_statements/`.
2. Import the statements:

   ```bash
   python aurum/scripts/pdf_importer.py
   ```

3. Prepare the journal:

   ```bash
   python aurum/scripts/journal.py
   ```

4. Open `data/reviews/YYYY-MM.csv`. Read the questions and edit only `human_review`.
5. Add any manual entries in `data/manual/YYYY-MM.csv`, if needed.
6. Save the CSV files and run `python aurum/scripts/journal.py` again.
7. Check the printed counts and resulting postings. Resolve any remaining questions or errors.
8. Run `python aurum/scripts/reports.py` and open the PDFs under `data/reports/YYYY-MM/`.

Importing PDFs and preparing the journal are separate steps. Rerunning either command reuses saved progress.

## 1. Set up Python and dependencies

Open the project in PyCharm and select its `.venv/bin/python` as the project interpreter. If `.venv`
does not exist yet, let PyCharm create a project virtual environment.

A virtual environment keeps Aurum's installed packages separate from other projects. `pip` installs packages;
`requirements.txt` records which packages Aurum needs.

In PyCharm's terminal, activate the environment if it is not already active:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Check which interpreter the terminal is using:

```bash
python -c "import sys; print(sys.executable)"
```

The printed path should end in `Aurum/.venv/bin/python`. Repeat the install command when
`requirements.txt` changes.

## 2. Know where files live

| Location | Purpose | What you edit |
| --- | --- | --- |
| `config/accounts.yaml` | Bank, liability, equity, income and expense account definitions | Your account definitions |
| `config/securities.yaml` | Shares and crypto instruments used by holdings and valuation | Your instrument definitions |
| `config/reports.yaml` | Display name, colour scheme, equity account keys and asset groups | Report presentation |
| `config/budget.yaml` | Budget categories, targets, monthly overrides and exclusions | Your budget plan |
| `data/reports/YYYY-MM/` | Balance sheet, income, budget and investment PDFs | Regenerate using the reports command |
| `data/bank_statements/` | Original ANZ Plus PDF statements | Add PDFs |
| `data/aurum.sqlite3` | Transactions, journal and private processing history | Inspect through SQL; use the workflows for changes |
| `data/reviews/YYYY-MM.csv` | Monthly bank-transaction review | Only `human_review` |
| `data/manual/YYYY-MM.csv` | Monthly manual instructions and results | `date`, `instruction`, `source_transactions`, `human_response` |
| `data/ai/categorisation_guidelines.md` | Generated, readable guidance learned from accepted reviews | Read the generated results |
| `data/ai/` | Aurum's private ChatGPT credentials and application state | Managed by Aurum |

`config/`, `data/`, `.venv/` and `.idea/` are ignored by Git. A fresh checkout therefore needs its own
private configuration and statements. Keep backups of the database and private configuration: statements
alone cannot restore your review answers, manual entries or learned guidance.

## 3. Configure accounts

Create `config/accounts.yaml` as a YAML **list**. This minimal example defines accounts used later in this guide:

```yaml
- key: everyday
  name: Everyday Bank
  account_type: asset
  institution: Example Bank
  bank_details:
    bsb: "123456"
    account_number: "00012345"

- key: personal_loan
  name: Personal Loan
  account_type: liability

- key: capital
  name: Capital
  account_type: equity

- key: salary
  name: Salary
  account_type: income

- key: dining
  name: Dining
  account_type: expense

- key: interest_expense
  name: Interest Expense
  account_type: expense
```

Replace the example bank details with the identifiers shown on your statement. Each imported statement
must match exactly one configured asset bank account. Add every account that should receive postings,
including the income and expense accounts you want AI to choose.

| Field | How to use it |
| --- | --- |
| `key` | Required, unique and case-sensitive. Choose a stable identifier such as `everyday`. |
| `name` | Required display name. Use this name when explaining treatment in a review or manual instruction. |
| `account_type` | Required: `asset`, `liability`, `equity`, `income` or `expense`. |
| `id` | Omit for a new account. Aurum generates and saves its UUID on normal loading. Preserve it afterwards. |
| `bank_details` | Needed for imported bank accounts and transfer matching. Quote BSB and account number. |
| `institution` | Optional institution display name. |
| `notes` | Optional account-purpose context for categorisation. |
| `closing_date` | Optional closure date as `YYYY-MM-DD` or `DD-MM-YYYY`. Omit while the account is active. |

Use spaces for YAML indentation and quote bank identifiers so leading zeros are retained. Normal imports
and journal runs initialise missing account IDs; `--review` and `--learn-guidance` require IDs to exist already.

Keep existing IDs and stable keys when editing accounts. Retain accounts referenced by older journal entries;
removing one can prevent those entries from loading. A closing date records closure but does not currently
exclude an account from AI selection.

Accounts have no opening-balance field. Record an existing position with a balanced manual opening entry
as described below.

### Securities configuration

`config/securities.yaml` is a separate YAML list describing instruments, rather than accounts. For example:

```yaml
- key: example_share
  name: Example Share
  security_type: share
  symbol: EXM
  market: ASX
  currency: AUD
- key: example_eth
  name: Ethereum
  security_type: crypto
  symbol: ETH
  market: CRYPTO
  currency: AUD
```

Supported definition types are `share` and `crypto`; current currency support is AUD. Normal journal runs
load this catalogue and save missing IDs automatically. Keep existing IDs and keys when editing it.
Use your configured security and account names or keys in manual instructions. Bank-only use does not
require this file; an investment instruction needs its security to be configured.

Investment calculations and market-price retrieval live together in `aurum/finance/investments.py`.
SQLite quote storage remains in `aurum/database/prices.py`.

Automatic pricing uses `yfinance`, included in `requirements.txt`. ASX symbols resolve to `.AX` tickers
and crypto symbols to `-AUD` pairs. Set optional `price_symbol` for a different Yahoo identifier; its quote
must still be in AUD. For example, `price_symbol: ETH-AUD` selects the Ethereum AUD pair explicitly.

## 4. Connect ChatGPT

Run:

```bash
python aurum/scripts/chatgpt_setup.py
```

Follow the browser sign-in instructions and choose an available model if prompted. This command verifies
access and model availability without sending a categorisation request. Aurum stores its own credentials
privately under `data/ai/`; no API key configuration is needed for this workflow.

To repeat browser authorisation:

```bash
python aurum/scripts/chatgpt_setup.py --sign-in
```

The **journal command defaults to `gpt-6-luna`**, independently of the model saved by the setup command.
To use a different available model, supply its exact slug to the journal command:

```bash
python aurum/scripts/journal.py --model YOUR_AVAILABLE_MODEL
```

Replace `YOUR_AVAILABLE_MODEL` with a model shown by setup. Aurum reports an unavailable model rather
than silently switching to another one.

## 5. Import statements

Put PDFs directly inside `data/bank_statements/`, then run:

```bash
python aurum/scripts/pdf_importer.py
```

The importer checks statement totals and available running balances, matches the statement header to its
configured account, and saves additional transaction lines to SQLite. It keeps original descriptions and
dates; transaction amounts are signed AUD dollars. It does not prepare journal entries.

Unchanged or renamed copies of already imported PDFs are skipped. Failed files remain retryable: correct
the reported problem and rerun the same command. Successfully imported files remain saved.

Subfolders are ignored. Keeping PDFs in `data/bank_statements/temp/` excludes them from the default import.
To import a different folder, pass it as the positional argument:

```bash
python aurum/scripts/pdf_importer.py data/bank_statements/another_batch
```

## 6. Prepare and review the journal

Run:

```bash
python aurum/scripts/journal.py
```

A normal run processes saved manual instructions and changed review answers, matches eligible internal
transfers, and asks AI to categorise remaining eligible bank transactions. Python validates balanced
postings and source links before saving. Posted transactions and unchanged answers are reused.

The command refreshes monthly review CSVs. Review months follow statement dates, and each CSV contains
one row per original bank transaction, including already-posted transactions and both sides of transfers.
An already-posted row appearing in the CSV does not itself mean it needs attention.

### Answer a bank-transaction question

1. Open `data/reviews/YYYY-MM.csv` in a spreadsheet editor.
2. Read `description`, `question`, `status`, `review_status` and `review_result`.
3. Edit only `human_review`. Be specific about the purpose, account or split.
4. Save as a UTF-8 CSV, keeping the existing columns and generated IDs/revisions.
5. Run `python aurum/scripts/journal.py` again and inspect the updated result.

Example answers using the account names above:

- `This was dinner. Use Dining.`
- `This is a capital contribution. Credit Capital instead of Salary.`
- `Of this $105 payment, $100 reduces Personal Loan and $5 is Interest Expense.`

You can correct a posted event through the same process. Explicit human treatment takes precedence over
AI defaults, while account, amount and balance checks still apply. If essential information is missing,
Aurum writes a follow-up question; update the same answer with the missing facts.

Do not change source amounts, account IDs or revision cells in the CSV. If accounting changed after an
answer was written, inspect the refreshed row and edit the answer again to submit a current correction.
Clearing an answer does not undo a posted entry.

### Inspect saved issues

```bash
python aurum/scripts/journal.py --review
```

This displays saved questions and errors without changing the database or contacting AI. It does not apply
CSV answers or refresh the CSV files; use a normal journal run for that.

### Read command feedback

The commands show brief progress followed by results and relevant next steps. PDF importing reports
new, unchanged and failed files, then gives the journal command. ChatGPT setup gives a journal command
with your selected model.

The journal summary separates accounting changed **this run** from saved bank transactions, manual
instructions and human-review questions. “No requests sent” means that run made no AI inference calls;
saved questions or failures may still need attention. Follow the printed CSV/column instructions or use
the complete retry command shown. Suggested commands retain your supplied paths and model.

A journal run returns exit code `0` when accounting preparation is complete, `1` when errors or unfinished
accounting need attention, and `130` when interrupted. `--review` is a read-only inspection command.

### Understand statuses

| Journal status | Meaning |
| --- | --- |
| `pending` | A source row exists but has not been processed yet. |
| `requesting` | A request was reserved; an interruption requires explicit retry. |
| `needs_review` | Essential information or treatment needs your input. |
| `failed` | A request or response failed and requires explicit retry. |
| `posted` | The complete balanced event is saved and contributes to accounting balances. |

Review and manual-entry statuses describe their own workflows. In particular, an `applied` manual entry
has saved its accounting in the journal; it is not another journal posting status.

## 7. Add manual entries

Normal journal runs create monthly templates under `data/manual/` for imported months and the current month.
Use the file whose name matches your entry's accounting month. For another month, create `YYYY-MM.csv`
using the same header and column order, starting with no existing entry rows.

Add a new row and fill these fields:

| Editable field | What to enter |
| --- | --- |
| `date` | Accounting date as `YYYY-MM-DD` or `DD-MM-YYYY`, within the file's month. |
| `instruction` | What happened, the amount and the intended accounts/treatment. |
| `source_transactions` | Optional transaction UUIDs copied from the bank-review CSV; separate several with semicolons. |
| `human_response` | Additional facts answering a saved manual-entry question. |

Dates such as `2026-01-31` and `31-01-2026` mean the same day. Aurum normalises valid manual CSV dates
to `YYYY-MM-DD` when refreshing the file. Changing only the written format does not resubmit an entry.
Invalid calendar dates and dates outside the file's month remain questions on that row.

Leave generated fields blank for a new row. Aurum fills `manual_entry_id`, `status`, `question`,
`proposed_postings`, `journal_entry_id` and `context_revision`. Preserve them when correcting an existing row.

Examples:

| Purpose | Example instruction | Source linking |
| --- | --- | --- |
| Starting position | `Record an opening position of $1,000 in Everyday Bank, offset to Capital.` | Leave blank for a position not already represented by imported activity. |
| Debt repayment | `Of this $105 payment, $100 reduces Personal Loan and $5 is Interest Expense.` | Paste the imported payment's `transaction_id`. |
| Liability increase | `Add HECS debt of $1,000. Debit Capital.` | Leave blank when there is no bank movement. |

Run `python aurum/scripts/journal.py` after saving. Complete supported instructions post automatically;
missing essential facts produce a question in the same row. Answer in `human_response` and rerun.

A bank payment already imported must be linked, rather than entered as a second cash movement. General
manual bank activity requires statement evidence; an explicitly instructed opening position can have no
source transaction. Choose an opening date and amount that do not double-count imported activity.

For a non-bank liability increase, stating the amount and counterpart is enough; no opening keyword is
required. Aurum infers the matching credit from your instruction. An unchanged instruction that previously
failed needs `python aurum/scripts/journal.py --retry-errors`; completed entries are skipped.

Correct a manual event in its original manual CSV row, keeping its generated identity and revision.
Deleting or clearing a row does not reverse posted accounting. If a bank review identifies an event as
owned by a manual instruction, make its correction in the original manual row.

### Investment holdings and purchases

Add the instrument to the securities configuration first, then use the same monthly manual CSV. The
holding account is an asset account, such as Cryptocurrencies or Equities; the instrument identifies what it holds.

| Purpose | Example instruction | Supporting evidence |
| --- | --- | --- |
| Existing holding | `Received 1 ETH in Cryptocurrencies at the closing price on this row's date. Credit Capital.` | No bank source for an existing position. |
| Amount-based purchase | `Bought $200 of ETH in Cryptocurrencies from Everyday Bank, including a $2 fee.` | Link the imported $200 payment. |
| Units and execution price | `Bought 2 EXM shares in Equities at AUD 100 per unit plus a $2 fee from Everyday Bank.` | Link the imported $202 payment. |

Use the actual names or keys from your configuration. Aurum never creates missing accounts or securities.
A bank-funded purchase requires an imported payment; an identified brokerage cash account can supply a
non-bank funding leg. A cash transfer to a brokerage alone does not establish which investment was bought.

Aurum uses supplied units, actual execution prices and bank settlements first. Otherwise it retrieves the
completed AUD daily close on the row's date, or the latest earlier close within seven calendar days.
Opening holdings use that value as their initial book cost; this does not reconstruct earlier purchases.
Current-day prices use the latest completed close, rather than an unfinished daily bar.

For an amount-based purchase, the amount means total spent unless you explicitly say “plus fees”.
With $200 total, an included $2 fee and a reference price of $100, Aurum estimates 1.98 units and records
$200 of investment cost. Unknown fees remain unspecified; inferred quantities are labelled where appropriate.
Supplied units retain their precision. Cash postings round to cents, and recorded cost includes fees once.

The generated `proposed_postings` shows units, book cost, any reference close and its actual date.
Reference quotes and interpreted proposals are saved privately beside the manual result; the journal
keeps the units and cost movement. The investment debit records its contribution to book cost; holdings
are calculated from dated postings rather than kept in a separate mutable table. Accepted instructions
and cached quotes are reused on later runs.

If a quote is unavailable or the trade figures disagree, answer the question in `human_response`. Supply
the actual AUD unit price when known. A saved pricing question can also be retried with `--retry-errors`;
an unchanged valid interpretation is reused without another AI request.

If an investment question was caused by missing account/security configuration, update the configuration
and run the journal command normally. Aurum reconsiders unresolved investment questions once per changed
catalogue; you do not need to repeat information already established by the configuration. Completed entries
stay skipped. Other unchanged questions still need an answer in `human_response`.

To reconsider investment rows rejected by an older version of the skill, run:

```bash
python aurum/scripts/journal.py --retry-errors
```

This explicitly retries older unsupported results once per new skill version. Completed entries remain
skipped. Correct a posted investment in its original manual CSV row, retaining its generated identity.

Holdings use only journal movements dated on or before the requested date. Their book cost stays fixed
until further activity or a correction. The balance sheet separately calculates market values from dated
holdings and closing prices; neither journal preparation nor reporting creates valuation-adjustment entries.


## 8. Read the database

Open `data/aurum.sqlite3` in PyCharm's database viewer and refresh it after running commands.

- `transactions` contains the original imported statement lines, identified by `transaction_id`.
- `journal` contains account postings and source-processing status. Only `posted` rows contribute to balances.
- `review_actions` contains private categorisation/review processing history.
- `manual_entries` contains manual submissions and their results.
  Investment proposals are saved before price retrieval so pricing can resume without new inference.
- `price_quotes` contains cached AUD closing prices and their provider dates.
- `categorisation_guidance` stores reusable guidance inferred from accepted reviews.

Each posted event has one `journal_entry_id` shared by its ordered posting lines. Inspect posted activity with:

```sql
SELECT date, journal_entry_id, line_number, account_name, debit, credit, description
FROM journal
WHERE status = 'posted'
ORDER BY date, journal_entry_id, line_number;
```

Count source-processing states with:

```sql
SELECT status, COUNT(*) AS source_rows
FROM journal
WHERE transaction_id IS NOT NULL
GROUP BY status;
```

Use CSV review or manual instructions for accounting corrections. Direct SQL edits bypass validation and
the saved revision/history checks.

## 9. Learned guidance and explicit retries

Accepted bank reviews can produce narrowly scoped categorisation guidance. Read the generated results in
`data/ai/categorisation_guidelines.md`. Guidance informs later AI decisions; it does not automatically
recategorise earlier postings or train the underlying model.

To assess previously accepted reviews for guidance without changing postings or CSV answers:

```bash
python aurum/scripts/journal.py --learn-guidance
```

Failed or interrupted requests are not retried by an ordinary rerun. Once you have inspected the issue,
explicitly retry journal/review/manual processing with:

```bash
python aurum/scripts/journal.py --retry-errors
```

Retry failed guidance assessments separately:

```bash
python aurum/scripts/journal.py --learn-guidance --retry-errors
```

Learning failures are separate from accounting results. Check their saved explanations in the generated
guidance file; valid posted accounting is retained.

## 10. Generate financial statement PDFs

After processing your manual entries and review answers, run:

```bash
python aurum/scripts/reports.py
```

The default period is the reporting month containing the latest posted journal event. To select January:

```bash
python aurum/scripts/reports.py --month 2026-01
```

Open `balance_sheet.pdf`, `income_statement.pdf`, `budget.pdf` and `investments.pdf` under
`data/reports/2026-01/`. Rerunning regenerates all four. The command prints progress and paths;
the reports themselves appear only in PDFs.

### Report configuration

Create `config/reports.yaml` as a mapping, using the actual keys from your account configuration:

```yaml
name: Aurum | Nicholas Wong
colour_scheme: teal
dark_mode: false

capital_account: capital
retained_earnings_account: retained_earnings
unrealised_gains_account: unrealised_capital_gains

asset_groups:
  Liquid Cash Assets:
    - everyday
  Investments:
    - cryptocurrencies
```

All three equity keys must identify distinct existing equity accounts. Add Retained Earnings and Unrealised
Capital Gains to your account YAML if needed, then run the journal command to initialise their UUIDs:

```yaml
- key: retained_earnings
  name: Retained Earnings
  account_type: equity

- key: unrealised_capital_gains
  name: Unrealised Capital Gains
  account_type: equity
```

Use only existing asset keys in groups; omit the example cryptocurrencies account if you have no such account.
Group order and key order control the asset display. Unassigned assets appear under Other Assets.
Liabilities, equity, income and expenses follow account-configuration order. Both financial statements show
every configured account of their relevant types, including unused accounts at $0.00. Superannuation and
Real Estate are grouped under Investments on the balance sheet. Closed accounts remain visible for history.

The reporting command reads existing IDs without generating them or changing account configuration.
The `name` setting is displayed beneath the statement title and in the footer.
The PDFs use Eigen One's fonts and icons, with aligned account panels and no report-notes section.
Table dividers separate ordinary account rows, inset at each end, with no line immediately above or below
group highlights. Chart categories use
natural letter widths, wrapping or shortening long names; bars have square left ends and rounded right ends.
The footer adds the local date the PDFs were generated, independently of the reporting period.
Negative balance-sheet values keep their minus sign and use the normal text colour.

Choose a theme by changing `colour_scheme` in `config/reports.yaml`, then run
`python aurum/scripts/reports.py` (or add `--month 2026-01` for January). The original built-in choices remain
`teal`, `navy_blue`, `plum_purple`, `forest_green`, `burgundy` and `copper`; omitted selection defaults to teal.
The supplied configuration also includes five themes based on your reference images:

| Name | `colour_scheme` |
| --- | --- |
| Tranquil Earth | `tranquil_earth` |
| Warm Neutrals | `warm_neutrals` |
| Velvet Neutrals | `velvet_neutrals` |
| Twilight Mauve | `twilight_mauve` |
| Sunset Hues | `sunset_hues` |

These five themes each define a complete appearance under `themes.<name>.fixed`. Their backgrounds, text,
icons, budget indicators and other colours ignore both `dark_mode` and the shared `colours.light`/`colours.dark`
settings. Edit the labelled fields within the chosen theme to change its appearance. The palettes preserve
the reference swatches after conversion to sRGB, ordered darkest to lightest, with companion shades for readable
text. Velvet Neutrals samples the textured Vanilla Silk, Alpine Oat, Warm Greige, Cherry Velvet and Bordeaux Noir
swatches approximately. Velvet Neutrals and Twilight Mauve are names chosen for those reference palettes.
Twilight Mauve uses a fixed dark appearance with light text, regardless of the global `dark_mode` value.

Every configured theme needs a `palette` with at least three quoted six-digit hex colours. Charts use the
whole palette. By default, its first shade supplies section backgrounds, the second the right-hand summary
card, and the third column headings and total rows. `primary`, `highlight` and `table_header` can override
those fills. `highlight_text` controls highlighted-card labels and subtext, defaulting to the last palette shade.

A fixed theme requires `page`, `surface`, `divider` and `group` inside `fixed`. Pages include paper margins;
surfaces fill cards and tables; dividers cover header, footer and row lines; groups shade asset-class rows.
Its `fixed.colours` holds all text, icon and status colours. The supplied themes populate every field so each
can be edited directly. Any omitted colour uses the model's default, never the shared light/dark settings.

The original themes still use `dark_mode` and their separate `light_*`/`dark_*` background, surface, divider and
group fields. Use unquoted `true` for dark mode or `false` for light mode; omission preserves light mode.
Their text, icons and indicators come from the shared `colours.light` or `colours.dark` block. Their default
budget indicators retain blue, red and green hues with shades adapted for contrast. Palette order stays the
same in either mode. A missing built-in theme definition, including `copper`, uses its built-in defaults.

You can add custom theme keys such as `my_theme`, using lowercase snake_case names beginning with a letter.
The selected key must exist under `themes` or be one of the six built-ins. Select it with `colour_scheme`,
save, then rerun the reports command.

The same colour field names apply inside a fixed theme's `fixed.colours` and the original shared mode blocks:

| What you want to change | Colour field |
| --- | --- |
| Body text, subtitles and footer | `text`, `muted` |
| Group labels, page titles and icons | `group_text`, `heading_text`, `icon` |
| Text on section, column-heading/total and highlighted-card bands | `on_primary`, `on_table_header`, `on_highlight` |
| Budget pills and Remaining text | `income_*`, `expenses_*`, `savings_*`, `remaining`, `budget_*` |
| Chart labels and values inside bars | `chart_text`, `bar_text_dark`, `bar_text_light` |
| Gain/loss text and draft notices | `positive`, `negative`, `draft_*` |

Most colours use quoted `#RRGGBB`. Group fills and budget pills also accept `#RRGGBBAA`, where the last two
digits set transparency (`00` is invisible; `ff` is opaque). Use six digits for a solid fill. Optional `null`
colours use their documented palette or body-text fallback. Bar values choose whichever of `bar_text_dark`
and `bar_text_light` has better contrast against each bar. Cards and panels have no visible outlines.

The expense chart shows up to eight positive categories, ordered from largest to smallest amount. Bar colours
span the full palette from darkest to lightest in both modes. Shades blend
to fit the number of bars without restarting the palette on the eighth bar. Values sit inside the bars:
amounts and percentages where they fit, or just percentages on smaller bars. Very small bars have a minimum
width to fit their percentage, so their lengths may look larger than their actual share; the percentage and
table values remain accurate. Positive shares below 1% are labelled `<1%`.

### Periods and earnings

A reporting month ends on its last Monday-Friday day. The next month begins on the following day;
public holidays do not change this rule. January 2026 runs from 1 to 30 January; February runs from
31 January to 27 February. The balance-sheet heading shows the cutoff date; the income-statement heading
shows the reporting month. Review/manual CSV filenames still follow calendar months, so their boundaries
can differ from report periods.

The reporting year ends at December's last weekday too. For example, 30-31 December 2028 belong to
January 2029. Current reporting-year profit appears in Retained Earnings. Earlier profit appears in
Capital, together with earlier direct Retained Earnings movements. This rollover is calculated for the
report; it does not write closing entries or erase income/expense history.

The income statement includes only income and expense accounts during the period. Gifts credited to
Capital and transfers between assets are excluded. The balance sheet includes all earlier posted activity,
not only the current month's transactions. Both use the journal accounting date rather than bank effective dates.

An ongoing month stops at today and is labelled month-to-date. A future reporting period is rejected.
An empty database requires an explicit month to generate zero statements.

### Investment values and drafts

For each security held at the cutoff, Aurum uses the latest completed AUD daily close on or before that
date, looking back at most seven calendar days. A weekend or exchange holiday normally uses the preceding
trading close. Actual quote dates remain in the SQLite price cache. Accepted quotes are cached and reused;
rerunning for the same date does not refresh a cached quote after a later market close.

If an investment cost $100 and is now worth $130, its balance-sheet asset value becomes $130 and
Unrealised Capital Gains equity increases by $30. Income is unchanged. Fees already included in cost are
not counted again. Cash or other recorded amounts in the same asset account are preserved.

Assets without security units, including manually valued property or superannuation, retain their posted
journal balances. Aurum does not infer holdings from an account name. Existing ambiguous manual valuations
are flagged rather than counted twice.

A missing suitable price produces a **Draft** balance sheet retaining that holding's recorded value.
Check the security's symbol/currency and rerun reports. Other valid holdings still use their available prices.
Missing account/security definitions and invalid accounting stop generation.

Unposted imported transactions, pending manual instructions or unresolved saved corrections through the
cutoff also produce draft notices. Later activity and guidance-only failures do not mark earlier reports
unfinished. Reports cannot detect CSV edits that have not yet been submitted: run the journal command first.
A draft uses valid posted accounting and shows a compact Draft notice. The terminal lists the unresolved
items when you run the report command; these details are not added as notes to the PDF.

Successful PDF generation returns exit code `0`, including labelled drafts. Failure returns `1`.
Reporting changes no journal or review records; its only database writes are new cached market quotes.
Outputs are regenerated rather than locked as immutable month-end snapshots.

### Fill in your budget

Edit `config/budget.yaml` directly. The supplied template leaves targets unset; replace `null` with your
monthly amounts. An unset target displays as a dash, while `0` means you deliberately budgeted zero.
Unset targets retain an empty progress pill. A zero target stays empty until the actual amount is positive.
Progress fills always keep the normal blue, red or green section colour, including after the target is exceeded.
The bar never grows beyond its base pill; a positive actual against a zero target shows a full pill.
In light mode, Remaining is black when positive or zero. Negative values use dark blue for Income, dark red for
Expenses and dark green for Savings. Dark mode uses light neutral text for nonnegative Remaining and brighter
blue, red and green shades for negatives. Unset targets have no remaining amount.
No interactive setup command is required. Report numbers do not use thousands separators.

Savings is the catchall row, followed by Sparkle, Extravagance, Postcards, Peace and Homeward, then Investments.
Peace keeps the stable key `savings_emergency`, and Homeward keeps `savings_home`. Display names can change
without renaming their stable keys or the underlying accounting accounts. Existing monthly targets are preserved.

Each category has a stable key, display name and kind: `income`, `expense` or `savings`. Ordinary income and
expense categories list their account keys. Gifts and investment contributions use explicit journal labels. Their
`accounts` can be empty or restrict eligible accounts, such as `[capital]` for gifts; listing an asset,
liability or equity account never automatically includes all its movements in the budget.

For example, using your actual account keys:

```yaml
excluded_accounts: []  # Add your superannuation account key to exclude events involving it.
categories:
  salary:
    name: Salary
    kind: income
    accounts: [salary]
    amount: 5000
  dining:
    name: Dining
    kind: expense
    accounts: [dining]
    amount: 250
  gifts:
    name: Gifts
    kind: income
    amount: null
  investment:
    name: Investment contributions
    kind: savings
    percentage: 10
monthly_overrides:
  '2026-12':
    dining:
      amount: 400
```

Choose either `amount` or `percentage` for a target. Percentages are percentages of actual budget income,
including gifts, rather than expected salary. A percentage target is zero when actual income is zero or
negative. Income targets use fixed amounts. Monthly overrides replace a category's target for that month;
unused budgets do not roll forward, and month-to-date reports retain the full monthly targets.

Actuals include reviewed income and expense postings, with refunds credited to an expense reducing that
expense. This is not strictly cash-flow budgeting: credit-card purchases and non-cash expenses can appear.
The configured exclusions remove complete events involving those accounts. Unconfigured income or expense
accounts still appear as unbudgeted rows and remain in totals. A section target is shown as unset if one of its
displayed categories has no target; Aurum does not present a partial target as the whole section's budget.
The summary card says `Budget: Not set` in that case. Set every category to an amount (including `0`) or a
percentage, then regenerate the report to show the section's combined budget.

### Allocate monthly savings

Total savings equals budget income minus expenses. The Savings row receives everything not explicitly
allocated to a pot or investment contribution, including money left in Everyday or Flex Saver. Interest
remains income and contributes to savings when unspent. Moving money between banks does not create additional
savings, and opening balances do not count as this month's income.

The `savings` section of `config/budget.yaml` names the available pot keys and the residual category:

```yaml
savings:
  residual_category: savings
  category_keys: [savings, sparkle, extravagance, postcards, savings_emergency, savings_home]
```

These keys refer to savings categories in the same file. Peace is an explicitly allocated pot, like the others.

Add an instruction to the existing `data/manual/YYYY-MM.csv`, using a date in the reporting month:

`This month I saved $200 to Sparkle and $300 to Homeward.`

Run `python aurum/scripts/journal.py`, then `python aurum/scripts/reports.py --month 2026-01` for January.
The journal command prepares the accounting before interpreting these allocations. If income minus expenses
is $1500, the budget shows Savings $1000, Sparkle $200 and Homeward $300. Total savings stays $1500
and the bank balance is unchanged. The pots have no separate asset accounts or debit/credit entries.

AI translates the instruction into pot names and amounts. Python checks that they are configured, are
whole cents and do not exceed this month's available savings. The saved allocation belongs to that manual
CSV row. Edit the same row to replace its amounts; use a separate row only for an additional allocation.
To undo a row's allocation, edit it to say `Clear this instruction's savings allocations; return them to Savings`.
Deleting text alone does not undo saved work. Unchanged applied rows do not cause another AI request.
If a split was rejected because income or expense entries were not ready, finish preparing the journal, then
change that row's `human_response` to `Retry this allocation` and rerun. An unchanged question is skipped.

These are monthly contributions, not cumulative pot balances. Savings receives everything not allocated to
another pot, so it does not need a separate manual amount. If a later accounting correction reduces available
savings below saved allocations, the report is marked Draft until you correct the split. A month with expenses
above income shows negative total savings. Reporting months end on the last weekday; an instruction dated
31 January 2026 belongs to February, while 30 January belongs to January.

### Label gifts and other contributions

Use the existing review CSV's `human_review`, or a manual row's instruction/`human_response`. For example:

- `This was a gift. Credit Capital and use the gifts budget category.`
- `This purchase is an investment contribution. Use the investment budget category on the holding line.`

Use the category keys from your budget YAML. These labels change reporting, not the debit/credit accounts.
A gift remains outside the income statement but appears in budget income. Savings allocations are counted
once on the selected receiving asset or debt-reduction line. They move an amount from the Savings catchall
into that category without changing total savings. Unlabelled transfers and principal repayments leave the
budget split unchanged. Every report satisfies Income = Expenses + Savings, using its configured budget scope.

When a review clearly identifies a received gift and credits Capital, the AI can select the configured gift
category without you spelling out its key. Your manual pot instructions supply the purpose of retained income.
Opening balances and existing investment holdings are starting positions, not new savings contributions.

The reserved label `exclude` omits an individual posting from the budget. To restore the normal treatment,
ask the review workflow to use `default`, which follows the posting's ordinary account mapping. An omitted
label in an AI correction preserves an existing label. Keep category keys that historical postings still use.

After editing a CSV, run the journal command and then the reports command. Existing posted gifts and
exceptions are not labelled retrospectively: submit a changed review answer when you want to add a label.
The savings total and catchall are recalculated directly from posted income, expenses and saved allocations.
Reports do not read unsubmitted CSV edits or ask AI to guess their budget treatment.

### Read the investment report

The investment report groups holdings by Shares and Cryptocurrencies, keeping the same security in different
accounts separate. It shows units, average carrying cost, the dated market price, cost basis, market value,
unrealised gain/loss and gain percentage. Allocation uses security cost basis, following Eigen One.

Whole-account investments can be included in `config/reports.yaml`:

```yaml
account_investments:
  superannuation: Superannuation
investment_symbols:
  superannuation: UNISUPER
```

UNISUPER appears under Superannuation at its recorded journal balance, including a zero balance. The optional
`investment_symbols` mapping supplies the investment-table label; other reports use the account name,
Superannuation. Without an override, Symbol uses the account name. Units, average cost and quoted price show
`-` for whole-account investments. Record balanced manual entries for their starting positions and changes.
Symbols are indented beneath their group, matching the balance sheet's account indentation.

An account without any posted activity through the report date contributes zero cost and gain. This lets
an unused Superannuation account remain visible without hiding the other investments' totals. An account
with activity still has unknown contribution cost until the journal distinguishes contributions from
investment returns, tax refunds and rollovers. Its balance contributes to portfolio value, but aggregate
cost and gain remain unavailable while that cost is unknown. A zero balance after activity does not imply
zero cost. The Portfolio Allocation chart combines security cost with whole-account recorded balances.
An account cannot appear both here and as individual security holdings.

It reuses the balance sheet's closing quotes and calculations, so generating the extra report does not
fetch another set of prices or write valuation journal entries. If a price is missing, the holding and its
cost remain visible while price, value and gain show `-`. Full portfolio value/gain totals are withheld
and the report is marked Draft until every holding can be valued.

The percentage is unrealised gain divided by current carrying cost. It is not annualised and excludes
dividends, interest and realised gains. Holdings with zero cost have no meaningful gain percentage.
Individual securities require journal lines with units; other whole accounts must be configured explicitly.

### PDF dependencies

Install `requirements.txt` in the project environment after updating. WeasyPrint converts local HTML/CSS
to PDF; fonts and icons are bundled, so rendering needs no font downloads. On macOS it also needs Pango:

```bash
brew install pango
```

Aurum discovers native libraries in the usual Apple Silicon and Intel Homebrew locations. Install Pango
only if the command reports missing native libraries; it is already available on the development machine.

## 11. Command reference

| Command | Purpose |
| --- | --- |
| `python aurum/scripts/chatgpt_setup.py` | Verify sign-in and choose an available model. |
| `python aurum/scripts/chatgpt_setup.py --sign-in` | Repeat browser authorisation. |
| `python aurum/scripts/chatgpt_setup.py --model YOUR_AVAILABLE_MODEL` | Select an exact available model during setup. |
| `python aurum/scripts/pdf_importer.py` | Import PDFs from the default statements folder. |
| `python aurum/scripts/journal.py` | Process eligible activity and refresh review/manual CSVs. |
| `python aurum/scripts/journal.py --review` | Display saved issues without changes or AI calls. |
| `python aurum/scripts/journal.py --retry-errors` | Retry errors, pricing questions and older unsupported manuals. |
| `python aurum/scripts/journal.py --learn-guidance` | Learn from accepted bank reviews without reposting them. |
| `python aurum/scripts/reports.py` | Generate all four PDFs for the latest journal reporting month. |
| `python aurum/scripts/reports.py --month 2026-01` | Regenerate the PDFs for a selected reporting month. |

All scripts accept `--help`. The importer accepts a folder plus `--accounts` and `--database`.
The journal command accepts `--accounts`, `--securities`, `--budget`, `--database`, `--model`, `--reviews`
and `--manuals`.
`--review` and `--learn-guidance` are mutually exclusive.
Reports accept `--month`, `--accounts`, `--securities`, `--config`, `--budget`, `--database` and `--output`.
The report output root defaults to the project's `data/reports/`; pass `--output` for alternate databases.

For example, these commands use the same alternate database:

```bash
python aurum/scripts/pdf_importer.py data/bank_statements --database data/example/aurum.sqlite3
python aurum/scripts/journal.py --database data/example/aurum.sqlite3
```

The separate folder also keeps its default review/manual files separate. These are real operations on that
alternate database; they are not dry runs. Supply the same account
configuration when using `--accounts` with both commands. Explicit relative paths resolve from your current
working directory. Default paths resolve from the project root; default review/manual folders sit beside
the selected database. ChatGPT state remains under the project's `data/ai/` directory.

## 12. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Missing Python package | Activate `.venv`, check `sys.executable`, then install `requirements.txt` with `python -m pip`. |
| Account configuration cannot load | Check YAML indentation, required fields, duplicate keys/IDs and the file extension. |
| Statement account cannot be matched | Configure the exact BSB/account number as quoted strings on one asset bank account. |
| No PDFs found | Put PDFs directly in the selected folder; subfolders are ignored. |
| ChatGPT access fails | Run setup again; use `--sign-in` when fresh authorisation is needed. |
| Model unavailable | Choose an available slug in setup and pass it explicitly with the journal's `--model`. |
| CSV rejected | Keep UTF-8 CSV format, headers, generated IDs and revisions. Manual templates also require the existing column order. |
| File changed during processing | Your edits were preserved. Save the file and rerun the command. |
| Request remains failed/requesting | Inspect saved issues, then use the appropriate explicit retry command. |
| Answer is stale | Read the refreshed accounting and edit the same answer to resubmit it against the current revision. |
| Used account was removed | Restore its original definition and UUID so historical entries can load. |
| Database columns appear outdated | Check that PyCharm is viewing the selected SQLite file and refresh its schema. |

Aurum does not migrate databases or create automatic schema backups during development. If the code expects
a different table layout, recreate the database manually or choose a fresh path with `--database`, then
rerun the PDF importer and journal command. Keep any backups you want yourself.

When starting with a fresh database, also move the existing `data/reviews/` and `data/manual/` CSV folders
aside so old transaction IDs and revision fields are not submitted to the new database. New templates are
created by the journal command; copy only your editable instructions into them and use newly imported
transaction IDs where source evidence is needed.

## 13. Current limits

Current workflows support ANZ Plus PDFs, AUD bookkeeping, bank categorisation/review and general manual
entries such as opening positions and debt repayments. Internal bank transfers can be matched from both
statement observations; uncertain matches require review.

Opening investment holdings and purchases support AUD prices, fractional units and recorded carrying cost.
Sales, holding transfers, staking, corporate actions and foreign-currency conversion remain unavailable.
A later share split can make a historical quote's unit basis ambiguous; supply the actual AUD unit price
when asked. Missing or stale quotes never silently become zero or a price in another currency.

Dashboards and locked month-end snapshots remain future work. Balance sheet, income, budget and investment
PDFs are available. Budget labels keep gifts credited to Capital separate from ordinary accounting income;
a separate Gift balance-sheet account is not required.

This guide describes implemented behaviour. Development decisions and the rule to keep this guide current
live in [AGENTS.md](AGENTS.md).
