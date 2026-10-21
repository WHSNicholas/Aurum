# Aurum

## Purpose

Aurum helps the user understand financial activity and position for everyday decisions and longer-term goals. Use EigenOne (`../Eigen One/`) only as a reference for intended functionality; leave it unchanged and write Aurum from scratch. Use synthetic examples, never personal financial data or secrets, during development.

## Working Agreement

- Agree on design before implementation. Build one small step at a time, explaining its purpose and affected files first.
- After each step, explain the code in plain language, give a simple way to check it, and pause for review. Answer questions without advancing implementation.
- Record agreed decisions here, separate from open questions. Do not create test files or `__init__.py` files for now.

## Python Style

- Begin each Python file with a 120-character banner: `#` at both ends, hyphen borders, a centred `Aurum` title, and a centred location subtitle separated by ` - `. Put a module docstring immediately below it.
- Group related code under consecutive `# %%` section headings, starting with `# %% 0. Preamble`. Extend each heading with hyphens to column 110.
- Give every class, function, and method a purpose-focused docstring. Put opening and closing `"""` on their own lines; document parameters, returns, and exceptions where relevant.
- Comment on accounting rules and non-obvious steps. Wrap comment and docstring prose naturally at column 110, preserving paragraph breaks and code examples; only the banner uses 120 columns.

## Accounting Decisions

- Use five account types: asset, liability, equity, income, and expense. Accounts are flat, without subtypes or subaccounts for now. Configure report groupings separately.
- Keep account definitions immutable. Validate replacements and rebuild `AccountRegistry`, the chart of accounts. Keep closed accounts for historical reporting.
- Give each account a persistent UUID and stable configuration key. Opening dates are optional; an account without a closing date is active. Do not store a separate active flag.
- Australian bank details and an institution display name are optional.
- Derive AUD balances from journal entries, using balanced opening entries for an existing position.
- Track savings goals separately from the ledger; a bank account retains its full balance.

## Open Questions
- Design journal entries, ingestion, entity links, report grouping, and savings-goal tracking when those features are built.
- Revisit subaccounts only after agreeing on their relationship to accounts, postings, types, and balances.
