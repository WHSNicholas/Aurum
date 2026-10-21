# Aurum

## Purpose

Aurum is a personal finance app for understanding financial activity and position, with dashboards and PDF reports planned. Rebuild from scratch; use EigenOne (`../Eigen One/`) only as a requirements reference and leave it unchanged. Develop with synthetic examples, never personal financial data or secrets.

## Working Agreement

- Agree on design, purpose and affected files before one small, reviewable step. Explain the code in plain language and give a simple check afterwards, then pause. Answer questions without advancing implementation.
- Prefer straightforward Python. Explain unfamiliar concepts and dependencies; add packages only when needed.
- Use PyCharm, one project `.venv` and `python -m pip`. Record dependencies in `requirements.txt`. Keep `.venv`, `.idea`, `data/` and private account configuration out of Git.
- Preserve local edits: read current files, save through PyCharm and verify them on disk. Keep agreed decisions here, with open questions below; current decisions override older notes.
- Do not create test files or `__init__.py` files for now. Use small synthetic checks where appropriate.

## Python Style

- Begin Python files with a 120-character banner: `#` at both ends, hyphen borders, centred `Aurum` title and location subtitle using ` - `. Follow it with the module docstring.
- Number Title Case `# %%` sections consecutively from `# %% 0. Preamble`, padding with hyphens to column 110. Group by purpose; main functions precede standalone private helpers in a `Helpers` section. Keep methods inside classes and omit unused sections.
- Module docstrings explain the file's contribution in natural paragraphs. Give every class, function and method a purpose-focused docstring, with arguments, returns and exceptions where relevant. Put opening and closing `"""` on separate lines.
- Use short, unnumbered, unpadded sentence-case grouping comments, such as `# Core fields` or `# Validation`, selectively. Label standard-library/third-party imports `# Imports` and project imports `# Aurum imports`; preserve order and omit empty groups.
- Explain accounting rules and non-obvious steps. Wrap comments and docstrings at column 110, preserving paragraphs and examples; only banners use 120 columns.

## Accounting Decisions

- Use immutable Pydantic models and five account types: asset, liability, equity, income and expense. Accounts are flat; configure report groups separately. Subtypes and subaccounts are deferred.
- Accounts have persistent UUIDs and stable keys. Opening dates, Australian bank details and institution display names are optional. No closing date means active; retain closed accounts for history and omit separate active flags.
- Validate replacements and rebuild `AccountRegistry`. Journal entries will determine AUD balances; represent existing positions with balanced opening entries.
- Keep savings goals separate from the ledger so allocations never reduce a bank account's full balance.

## Importing Decisions

- Layout: `finance/` models, with account definitions, registry and configuration loading together in `accounts.py`; `importers/` PDF parsing; `database/` SQLite; `scripts/` runnable commands.
- ANZ Plus PDFs live in `data/bank_statements/`; SQLite at `data/aurum.sqlite3`; private account definitions, requiring explicit UUIDs, at `config/accounts.json`. Run `python aurum/scripts/pdf_importer.py`. Support folder/config/database overrides: defaults resolve from the script's project root, supplied relative paths from the working directory.
- Use one immutable `Transaction` per statement line, retaining original descriptions, dates, signed AUD dollars, a required source account UUID and filename stem (no directory or extension). Keep both transfer observations. Bank identifiers stay on accounts; match each PDF header to exactly one configured account.
- Validate statement totals and available running balances. SQLite stores numeric dollars (`REAL`, approximate); validate whole cents and use `Decimal` for Python calculations and reconstructed transactions. Inspect SQLite in PyCharm; no CSV exports.
- Store successful PDF content hashes only in `imports`, alongside filename stems; skip unchanged or renamed PDFs before parsing. Save each PDF's transactions/history atomically; failures remain retryable. Page/row positions are temporary diagnostics.
- For changed or overlapping PDFs, compare account UUID, transaction/effective dates, amount, whitespace-normalised description and running balance. Compute keys/counts during import, saving only additional identical-row occurrences. Preserve existing UUIDs, descriptions and source details; never persist fingerprints or occurrence counters.

## Next Steps

- Next agree a unified double-entry journal: counterpart matching, categorisation and transfer reconciliation, plus manual investment, debt and other entries. Dashboards and PDF reports will use this journal.
- Keep journal work out of importing. Resolve corrections/deletions, entity links, report grouping and savings goals when those features are built. Revisit subaccounts only after agreeing their relationship to accounts, postings, types and balances.
