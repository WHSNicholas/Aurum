# Aurum

## Purpose

Aurum is a personal finance app for understanding financial activity and position. Rebuild from scratch;
use EigenOne (`../Eigen One/`) only as a requirements reference, leave it unchanged and never copy its code.
Dashboards, budget reporting and PDF reports will follow the accounting workflows.

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
  bank categorisation. Keep CLI progress brief, distinguish this run from saved work and print relevant next
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
- Manual-entry guidance learning, report valuations/monthly closing, dashboards, budgets and PDF reports remain
  future work. Budget-only labels, such as gifts posted to Capital, need a reporting design. Resolve entity
  links, savings allocations and report grouping when their workflows are designed.
