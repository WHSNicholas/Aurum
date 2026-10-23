# Aurum User Guide

Aurum turns ANZ Plus bank statements into a local accounting journal. You review uncertain transactions
in CSV files and use a second set of CSV files for manual entries. SQLite stores the accounting and saved
review history. Dashboards and PDF reports are planned.

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
| `config/securities.yaml` | Preparatory catalogue of shares and crypto instruments | Optional instrument definitions |
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

The three commands show brief progress followed by results and relevant next steps. PDF importing reports
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
holding account is an asset account, such as Crypto or Equities; the instrument identifies what it holds.

| Purpose | Example instruction | Supporting evidence |
| --- | --- | --- |
| Existing holding | `Received 1 ETH in Crypto at the closing price on this row's date. Credit Capital.` | No bank source for an existing position. |
| Amount-based purchase | `Bought $200 of ETH in Crypto from Everyday Bank, including a $2 fee.` | Link the imported $200 payment. |
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
until further activity or a correction. Market-value reporting and month-end snapshots will be added
with reports; running the journal command does not create valuation-adjustment entries.


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

## 10. Command reference

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

All three scripts accept `--help`. The importer accepts a folder plus `--accounts` and `--database`.
The journal command accepts `--accounts`, `--securities`, `--database`, `--model`, `--reviews` and `--manuals`.
`--review` and `--learn-guidance` are mutually exclusive.

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

## 11. Troubleshooting

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

## 12. Current limits

Current workflows support ANZ Plus PDFs, AUD bookkeeping, bank categorisation/review and general manual
entries such as opening positions and debt repayments. Internal bank transfers can be matched from both
statement observations; uncertain matches require review.

Opening investment holdings and purchases support AUD prices, fractional units and recorded carrying cost.
Sales, holding transfers, staking, corporate actions and foreign-currency conversion remain unavailable.
A later share split can make a historical quote's unit basis ambiguous; supply the actual AUD unit price
when asked. Missing or stale quotes never silently become zero or a price in another currency.

Dashboards, budget reports, market-value reports, month-end snapshots and PDF exports are not available yet.
Budget-only labels, such as a gift credited to Capital, are not stored separately from the accounting
treatment yet. That distinction will be designed with budget reporting; a separate Gift balance-sheet
account is not required.

This guide describes implemented behaviour. Development decisions and the rule to keep this guide current
live in [AGENTS.md](AGENTS.md).
