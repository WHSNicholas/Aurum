# Aurum User Guide

Aurum is a desktop app for bank statements, manual accounting, reviews and financial reports.
Use the app for all inputs and processing. Saved instructions, responses and journal entries live in SQLite;
there are no manual/review CSV inputs or separate processing commands.

## Open Aurum

Double-click the **Aurum.app** shortcut in the Aurum project folder, or open **Aurum.app** from your personal
Applications folder. The project shortcut points to that installed launcher. You can keep it in the Dock.
The launcher uses this project's Python environment, so keep the Aurum project and its `.venv` in place.
The abstract teal-and-gold icon identifies both the launcher and the running app. Restart Aurum after an
icon update to refresh its Dock icon.

For development, run `aurum/scripts/app.py` in PyCharm, or use the one startup command from the project root:

```bash
.venv/bin/python aurum/scripts/app.py
```

Opening Aurum reads the saved workspace, then syncs any bank connection you have enabled for automatic
syncing. Processing, editing and generating reports still require an explicit action.
The sidebar footer shows your name and a refresh control. Refresh reloads saved facts and settings;
the same sidebar icon spins throughout refresh, sync and other background jobs. During processing,
the stop-square icon requests a stop
after the current safe step.
Routine bank-sync results are available through the information icon's activity log. Errors remain visible.
Hover over a transparent button or selector outside tables to highlight its clickable area.
Inside tables, selection and keyboard focus identify the active row or control.
The rising graph opens Analysis. The stacked documents open Reports. The sliders under Setup open report
settings.

On **Transactions**, **Review** and **Journal**, use **1M** beneath the month selector to see
the selected reporting month. Choose **All** to browse every saved date. These pages share this choice
until you close the window. The month above the pill still sets the working month for Process, new entries
and report generation. Review's status filter works within either date range. Switching ranges keeps your
unsaved responses, including responses to rows outside the visible range.

## Setup

Setup has four sections: **Accounts**, **Securities**, **Budget** and **Reports**.
The **Connections** area in Accounts keeps Redbark's API-key controls and ChatGPT's model and connection
controls inline. Redbark opens a dialog only when setting up account mappings and handover dates.
Its summary cards show account counts, monthly budget allocations and the selected theme.
The month selector remains visible in every section; only the budget targets depend on that month.

### Name and appearance

Double-click your name at the bottom of the sidebar, or focus it and press F2, to edit it.
Press Enter to save only the name; other Setup drafts remain unchanged. Escape or clicking elsewhere cancels.
A failed save keeps your edit available to retry; a name changed elsewhere is not silently overwritten.
This one setting supplies the sidebar name and generated report headers and footers.
Enter the personal name only; reports add **Aurum |** automatically.
Regenerate reports to apply a changed name to saved PDFs.

Choose a theme in the **THEME** card and click **Save**. The app and previously generated reports on screen
adopt the saved colours immediately. Adjustable themes offer Light/Dark. The swatches in the card and theme
menu have a white backing in light workspaces and a black backing in dark workspaces.
In Sunset Hues, deep teal highlights the main report figure, with softer teal behind the Net Worth chart.
Soft sage marks report sections such as Assets, Liabilities and Equity. Supporting cards use cool grey-green.
Blue-grey bands mark column headings and totals. Cash Flow and the other secondary charts share the
table background, and a dusty-rose tint groups related rows.
The desktop uses a shared light canvas, and PDFs use the
configured paper colour. Generate a report again to apply the colours to its saved PDFs.
Aurum ships with ten themes including Teal, Navy Blue, Plum Purple, Forest Green, Burgundy,
Tranquil Earth, Warm Neutrals, Velvet Neutrals, Twilight Mauve and Sunset Hues. They are available in
new and cloned workspaces without copying personal configuration. Existing workspace colour overrides
still take precedence.
Palette definitions remain in code rather than appearing as editable colour controls.

The bottom save and reload icons sit beside the activity-log icon while Setup is open.
Save persists staged Setup changes. Reload discards the current section's changes only after
confirmation. When leaving with unsaved edits, choose **Save**, **Discard** or **Cancel**; navigation continues
only after a successful save. Cancel or Escape keeps your draft. The footer stays quiet when nothing has changed.
Unchanged settings retain their existing controls on refresh;
external changes are still detected.

### Accounts and securities

Create missing workspace files with Setup's explicit create action. Existing files remain untouched.
New workspaces start with the fixed Capital, Retained Earnings and Unrealised Capital Gains equity accounts.

Accounts are grouped as assets, liabilities, equity, income and expenses. Select an account to edit it.
In both Accounts and Securities, use the plus and bin icons above the list to add or remove a definition.
The up and down arrows move the selected item within its type. Save to keep the new order.
Account notes use a single line. Existing multiline notes keep their formatting until you edit the note.
For an account with saved history, enter a closing date when it closes. Dates accept `YYYY-MM-DD` or `DD-MM-YYYY`.
Deleting an account referenced by saved accounting is blocked with its name and closing-date guidance;
unused accounts can be removed. Closing an account preserves its postings and report history.
Completed processing snapshots alone do not block removal after the current postings have been reassigned.
Their original evidence is retained. Unfinished work and current accounting still protect referenced accounts.

For a bank starting balance, save the account first, then enter the amount and date in its **Opening balance**
row. Click the checked bill to post it against Capital. This changes the balance sheet without adding
budget income or expenses; the bottom Setup save icon only saves configuration. Negative balances are supported;
zero needs no entry. Editing the row corrects the same opening entry. Complex existing openings use the journal
edit icon instead. Other asset and liability accounts keep their existing transaction workflows.

Enter BSB and account number together for a bank account, preserving leading zeros. Both blank means the
account has no bank details. Accounts start at zero; record starting balances as balanced journal entries.

Set each asset's **Asset account type** to **Cash**, **Investment** or **None**. Every asset offers both roles,
whether or not it has bank details. Cash includes the account in the cash budget; transfers from Cash to
Investment count as Investments, and receipts back count as Investment returns. None clears the budget role.
These choices do not change the accounting type or add an Investment Report holding.
For a loan or credit card, choose its **Expense category** in the same form.
These global choices save with the bottom Setup save icon and affect historical budgets when regenerated.
Save a new account first to enable its budget setting.

Add equities and cryptocurrencies under Securities. Stable account/security identifiers are saved automatically.
Changing a display name does not change its identity. System equity names and roles are fixed.

Saving a new security or changing its type, symbol, market or **Price symbol** checks the ticker with
Yahoo Finance. If Yahoo rejects it, the edits stay open. Apply a suggestion to fill **Price symbol**, or
edit the field yourself, then save again. A rename or reorder alone skips the lookup. If Yahoo is
unavailable or rate-limits the check, Aurum saves the settings with a warning.

### Connect ANZ Plus with Redbark

Set up your bank connection in Redbark first and create an API key with **data:read** access.
Then, in Aurum:

1. Save your ANZ Plus bank accounts in **Setup → Accounts**, including BSB and account number.
2. Under **Connections**, enter the API key in Redbark's masked field and click the Redbark symbol.
3. In the setup dialog, link each returned account to its Aurum account. Check the masked account number carefully.
4. Choose a **handover date** for each account. PDFs cover dates before it; Redbark covers booking dates
   from that date onward. The suggested date follows existing imported activity.
5. Click the save icon to connect and import. Aurum validates the complete import before saving the key in
   macOS Keychain and importing bank activity; there is no separate preview step.

The API key is kept separately for each workspace, outside the repository and database. Aurum never sends
it to ChatGPT. Checking the connection does not save transactions.
If Redbark rejects a request, the error identifies its documented reason and any recognised request field.
Aurum stays just inside Redbark's approximate seven-year history limit and shows the earliest allowed
handover when an older date is selected.

Connected workspaces sync automatically once when opened. Ordinary refresh does not start another sync.
You can also click **Sync** on Transactions. Syncing imports settled bank transactions; it does not run AI
or post journal entries. Click **Process** separately when you want to prepare the journal.
Redbark may return cached bank activity, so a successful check does not guarantee a live bank refresh.

Repeated syncing recognises Redbark's account and transaction IDs, skips previously imported records and
preserves posted entries. A new transaction ID is imported separately, even if its amount, description or
dates match another purchase. Aurum does not ask you to decide whether similar Redbark records are duplicates.
If an earlier version left a possible duplicate awaiting review, syncing imports it when Redbark returns
that record again and clears its warning. Previously resolved links remain as saved.
You can also use **Import transaction** on an older held record in Review.

Changed bank facts for an already imported ID appear in **Review**. They never overwrite
the saved source: **Keep saved record** acknowledges the observation while retaining the original accounting.
Make any required accounting adjustment explicitly in the journal.
Setup shows connection status beneath Redbark and ChatGPT. **Needs attention** indicates an actual connection
or sync error; bank-fact questions remain in Review without adding a count to the connection row.
Redbark sometimes omits an extended description it supplied earlier. Aurum retains the wording already
received and recognises the transaction by its provider ID. A successful account sync clears older warnings
caused by this omission, even if Redbark no longer returns that transaction. For genuine changes, Review
shows each differing field with its last accepted value and the incoming value.

PDF imports remain available for earlier dates and other accounts. PDFs overlapping a Redbark-owned period
are rejected to avoid duplicate accounting, including after a local disconnect. Handovers are retained with
the imported history. When the field shows **Saved API key**, leave it empty and click the Redbark symbol
to check the saved connection. Enter a new key to check it, or use the setup icon to review account mappings.
Connection actions save independently of Setup's save icon. Refresh preserves an entered key; leaving
Accounts with an unused key asks whether to discard it.
If Keychain refuses access, allow access and retry; the inline error explains any recovery needed.

New accounts still start at zero. Use the bank account's **Opening balance** row in Setup for its starting position;
Redbark balances do not become opening entries automatically.

### Budget

Enter the selected month's targets in whole dollars. An older target with cents shows its exact value
so you can choose the whole-dollar amount before saving that month's budget. To reuse last month's
targets, click the clock icon, review the amounts and save.

The Setup Budget card shows whole dollars without thousands separators, such as **$5000**, with Savings
on its second line. Hover for exact amounts including cents; rounding does not change the saved targets or
number formatting elsewhere.

The chart shows income above expenses and savings. Blue represents income, red represents expenses and
green represents savings. The same colours identify the Cash Flow bars in Analysis and the Budget Report's
progress fills. In both light and dark mode, the largest allocation uses the base colour and smaller
allocations use progressively lighter shades. Equal amounts share a shade. Hover for category amounts.
Income, Expenses and Savings stay side by side, each with its own vertical scroll while the chart remains fixed above them.
Use the plus and bin on the left to add or remove categories. The arrows on the right move the selected
category within its section. Each table has three equal-width columns for **Category**, **Amount** and **Links**.
Links shows the full linked account names. Click anywhere in the link field to open the searchable dropdown.
Long names wrap, and amount inputs remain compact within taller rows.
Click an account row to check or uncheck it. Each change updates the unsaved Setup draft immediately, and the
dropdown stays open for further choices. Escape or clicking outside closes it without undoing those changes.
Save Setup to persist the links.
You can also use the arrow keys and Space. Add and save income/expense accounts in **Accounts** first;
the picker shows accounts eligible for that budget section. Automatic destinations show **Auto**.
If an older configuration contains invalid categories, use the repair action; those edits remain staged
until you save Setup.

Monthly targets and income/expense category links stay here. Cash and Investment roles, and each
liability's Expense category, are configured on the account in **Setup → Accounts**.
Category links group the underlying accounts; they do not change journal accounting.

**Savings** and **Investments** are fixed rows. Investments is cash sent to designated investment accounts.
Savings is income less expenses less Investments, including money left in Everyday. It can be negative
when you draw on earlier savings. There are no savings pots or per-entry budget exclusions.

### Reports

Choose the report folder under **Outputs** and assign asset accounts through the **Account / Report Group** table.
**Other Assets** leaves an account ungrouped. Asset Groups and Account-based Investments share one page.
The settings icon beside Asset Groups opens group management. Double-click a group name, or press F2,
to rename it. Plus adds an editable row without opening another dialog. Enter finishes editing; Escape
cancels the edit. Use the save icon to apply group changes to the Setup draft, then save Setup to persist them.
The close icon cancels the dialog. Reordering keeps each group's accounts; removing a group leaves them ungrouped.

Account-based investments track contribution cost for assets such as superannuation without requiring units.
For UniSuper, choose **Capital in / out** for contributions, contribution tax, fund administration fees
and rebates. Fees deducted inside the fund reduce cost basis, and rebates add it back. Choose **Return or
fee** for the posted investment return. The investment report then shows cumulative posted fund returns.
Investment display labels change report labels without renaming accounts.
Use the theme card above the page for report appearance.

### ChatGPT

Find ChatGPT under **Setup → Accounts → Connections**.
Click the ChatGPT logo and authorise Aurum in the browser, then select a model and use the row's save icon.
This saves only the model; unrelated Setup edits remain untouched.
A failed save leaves the selected model and draft available to retry.
The picker shows the saved model before a connection check. **Not checked** means saved access has not been
verified this session. Click the ChatGPT logo again to check the connection and load available models.
Changing the model reveals Save and Discard icons; the picker tooltip retains the saved choice. Refresh keeps
the draft, and leaving Accounts or closing Aurum protects an unsaved model choice. An unavailable model remains
visible until you explicitly choose a replacement.
Sign-in and connection checks require their own actions. Discard does not undo a completed sign-in.

Aurum uses its own ChatGPT sign-in under the workspace's private AI folder. It does not use Codex credentials
or silently switch to a separately billed API key. The selected model stays explicit; there is no fallback.
Friendly labels such as **GPT 6.1 - Sol** retain the original model identifier internally.

## Reporting month and Overview

Use the arrows beside the month. The small date range shows the actual reporting interval.
Reporting months end on the last Monday–Friday day, without public-holiday adjustment. The next day begins
the following month: for example, 31 October 2026 belongs to November's reporting period.

Overview reads posted accounting only:

- **Net Worth** uses the balance sheet's calculation and the latest valid saved market prices on or before
  the cutoff. The card shows As of today's date for the current month, or the cutoff for an earlier month.
  Prices carry forward when a new day's quote is unavailable; their dates stay in the tooltip.
  Refreshing Overview reads prices already saved in the workspace. **Provisional** means some holdings
  retain their recorded value. Hover for details and Generate reports to check prices or required
  investment review.
- **Income** and **Expenses** follow posted movements involving configured Cash accounts and signed refunds.
  Their counts include each contributing journal event once per side, including manual events.
- **Total savings** is budget income less expenses, divided into Savings and Investments in the budget.

Amounts use small spaces for thousands. Pending work is not included, and incomplete budget targets do not
block actual figures.

The financial summary gives Net Worth prominence beside compact Income, Expenses and Savings rows.
These groups stack in narrower views; the figures and reporting cutoff stay the same. Monetary values
throughout the interface use the same number font as the reports.

The four workflow cards show Sources → Journal → Review → Reports. Completed stages retain their icons and gain a
dark background and tick; unfinished stages share a lighter background. Each stage shows its heading and
count; hover for supporting details. Click a stage to open its page—Sources opens Transactions.
There is no separate Workflow heading or next-step message. Actual workspace errors remain visible.
The Reports card completes when earlier stages are complete and all four report PDFs exist for the month.
Card tooltips explain pending final statements or source-file issues. A valid imported final statement takes
precedence over its retained list. Refresh checks the files again. The quote remains below the cards.

These indicators describe saved activity; they do not prove that every expected bank account was supplied
or that saved reports include later accounting changes.

## Analysis

Open **Analysis** beneath **Reports** in the sidebar to explore posted history through the selected
month's cutoff.
The page opens on **6M**. The daily charts cover 28 days for **4W**, 84 days for **12W**, or the
reporting months included in **6M**, **1Y** and **All**. History begins with the first posted entry.
Short ranges mark Sundays on the daily charts. Longer ranges mark the first of each calendar month.
Exact dollar amounts on Analysis use two decimals and ungrouped digits, such as **$12500.00**.

- **Net Worth** plots each day's closing balance, with Assets and Liabilities centred below the chart.
  The right-hand figures show the dollar and percentage change since the first visible day. Hover over
  a day to see its date beside the change.
  A dash means the starting balance was zero and a percentage cannot be calculated.
- **Cash Flow** shows monthly Income, Expenses and Savings side by side, with a legend below the bars.
  Hover over a bar to see the month on a vertical guide and its value on a horizontal guide. The month
  appears above the bars, with all
  three amounts in the centred legend below.
  **4W** shows the selected reporting month, **12W** shows the last three, and the longer ranges show
  six, twelve or all months.
- **Account Balances**, directly below Cash Flow, shows the daily balance of any asset, liability or equity
  account. Select its name to switch accounts. The change beneath the amount is measured from the first
  visible day. Equity includes the same earnings rollover and market adjustments as the balance sheet.
  For investment accounts the line includes both cash in the account and the market value of its holdings.
- **Spending by Category** shows the selected month's breakdown alongside Cash Flow and Account Balances.
  In a narrow window it appears below them. Each category includes its amount and share of total expenses.
  Refunds reduce the category total.

Hover over a day or a set of monthly bars to inspect its reporting month. Click or press Enter to pin
it and use the arrow keys to explore. Click the pinned day again or press Escape to return to the workspace
month. Turning on Reduce Motion in your system settings makes chart and navigation feedback immediate.

Analysis shows saved figures immediately. Opening the page, refreshing it or extending the range checks
Yahoo for missing completed AUD market closes. The chart carries the last available close across
non-trading days. Hover over the balance amount for the underlying market close dates, or read the
accessible chart details. If Yahoo skips a past weekday, opening or refreshing Analysis after 15 minutes
checks that date again. A provisional holding uses its recorded value. Price retrieval errors appear in the
activity log.
Overview and newly generated reports can use the same saved closes.

## Transactions

Use the plug beneath the Transactions title to connect a bank. Once connected, the paired arrows sync its
transactions.
Sync, Process and Generate are icon-only throughout the app. The PDF icon beside search opens
the file chooser for backup imports from other accounts and earlier dates.
The **Source** column shows Redbark, the PDF filename or **Manual Journal**. Selecting a feed transaction shows its booking
date and full description. Descriptions display in uppercase; saved wording and copied source evidence
remain unchanged. The source tooltip retains the additional bank evidence.
Table amounts always include two decimal places. Each transaction occupies one row. Hover over a shortened
cell to read its full value, or select the transaction to see its details. Search includes the full wording.
When Redbark corroborates a transaction already imported from a PDF, its original PDF source and statement
date stay visible. The additional Redbark dates and references appear in the source tooltip.

Click the PDF icon, or drop PDFs on the page. Selected files reveal a compact queue with an import icon.
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
Import feedback retains per-file outcomes in its tooltip; the separate history window has been removed.
Internal import records still prevent duplicate uploads. Empty or filtered-out tables show **No entries**.

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

### Add a manual entry

Use the plus beside **Sync** to open **Manual Entry**. It starts in **Instruction** mode.
Switch to **Journal entry** in the popup for direct posting. Both modes share the date and selected bank
transactions and retain their own drafts.

Standalone entries appear in Transactions with **Manual Journal** as their source. Their amount is the
total of posted debits, or **—** while the amount is unknown. When a manual event posts against imported
bank transactions, each bank movement keeps its own row, date, signed amount and source. Select any of those
rows to see the complete event's debit and credit lines alongside the transaction details. **1M** uses the
bank date for each movement. **All** includes both sides of transfers that cross reporting months.

For an instruction, choose a date from the calendar or type it, then describe the event, amount and counterpart.
Use the paperclip beside **Bank Transactions** to link supporting transactions. Use the save icon,
then open **Review** and click
**Process**. AI questions and responses belong in Review; **Open Review** takes you to the selected instruction.
Editing an instruction in Transactions preserves its existing response.

For a direct journal entry, enter balanced debit and credit lines and select the checked bill to post.
Click anywhere in an account field to choose from its dropdown. The plus in the table's last header cell adds
a posting; the aligned bin icons remove editable rows. Enter a description for each posting; missing details
are highlighted when you try to save. Bank activity needs linked imported evidence; record a bank's initial
balance through **Setup → Accounts → Opening balance**.

Unposted instructions show their saved wording. The posted manual viewer shows Statement date, Effective date,
Account, Reference and Source. For a standalone event, Statement date is the journal date.
The pen icon at the top right of the details opens the appropriate instruction or journal editor.
Closing an edited popup asks **Discard unsaved changes?** Choose Cancel to keep editing or Discard to close.
Switching methods retains both drafts. Changing bank selection asks before replacing edited journal rows.
After a save error, a reload icon refreshes the selected method with confirmation before discarding its draft.

### Budget and investment treatment

Budget treatment follows the complete posted entry and the account roles configured in Setup.
There is no gift category picker, inclusion switch or savings allocation form. Editors and detail cards
omit budget-impact information; the main **Journal** table shows the derived status and category tooltip.
Correct the accounting or Setup account roles when the classification is wrong. Opening balances do not
add budget income or expenses.

| Event | Income Statement | Budget |
| --- | --- | --- |
| Salary or interest paid into a Cash account | Income | Income |
| Cash purchase or expense refund | Expense or reversal | Expense or signed refund |
| Transfer between Cash accounts | None | Savings transfer; totals unchanged |
| Cash payment to a designated investment | None | Investments |
| Investment withdrawal into a Cash account | Gain recognised at disposal only | Full receipt as Investment returns |
| Loan or credit-card repayment | Interest only; principal reduces liability | Whole payment in the liability's selected expense category |
| Gift into a Cash account | Capital, not income | Gift received |
| Gift given: credit Cash, debit Capital | Capital, not expense | Excluded |
| Employer super or reinvested dividend without Cash movement | Income where applicable | Outside the cash budget |
| Unrealised market movement | Equity, not income | None |

A credit-card purchase is an expense when purchased; its cash-budget expense appears when the card is paid.
New borrowing received into a Cash account is not assigned a treatment automatically: it stays in Review.

Investment returns combines cash investment income and full withdrawals into Cash accounts in one budget row.
Its account picker links the income accounts; withdrawals are included automatically. Earlier Investment
withdrawals targets combine with this row when loaded, preserving monthly plans and account links.
Configuration changes are saved through Setup; regenerate reports to update existing PDFs.

Security sales release average recorded cost and recognise the gain or loss at sale, including when the
proceeds remain in brokerage cash. A later receipt into a Cash account does not recognise that gain again. For a
balance-only investment withdrawal, provide the confirmed value immediately before withdrawal; Aurum releases
the same proportion of recorded cost. Missing cost, valuation or gain attribution stays in Review.

For example, withdrawing $600 from a fund worth $1 200 with $1 000 recorded cost releases $500 cost
and recognises $100 gain, provided the recorded unrealised gain supports that reclassification.
Investment instructions and accepted reviews use the same checked calculations.

For a linked incoming bank receipt, the journal editor also offers an **Investment disposal** icon.
Choose the holding and realised-return income account, then enter actual units sold or the confirmed fund
value before withdrawal. The form previews released cost and gain/loss. Its checked bill posts the complete
calculated event; cancelling leaves the journal draft intact. Ordinary journal rows cannot bypass disposal
cost checks. Sales whose proceeds remain in brokerage cash can be entered as a Manual instruction.
The holding chooser includes non-bank investments only; assigning a bank account the Investment budget
role does not make it a security or fund disposal holding.

Historical budgets now use these rules. Old exclusion labels and virtual allocations remain stored as
history but have no effect. Posted amounts and bank identities are preserved. Regenerate reports to replace
saved PDF snapshots; refreshing only recalculates the on-screen figures. An incomplete target remains unset.

Spinners show work in progress. Routine progress and completion messages are available through the information
icon; errors and unsaved changes remain visible where you can act on them.

## Review

Review combines bank questions, unfinished manual instructions, budget issues and investment classifications affecting the
selected reporting cutoff, including earlier unresolved items.

The queue shows **Date, Description, Amount, Status**, with search and a filter on one line:

- **Review:** questions, retries and unresolved classifications.
- **Ready:** queued work and saved responses awaiting Process.
- **Posted:** completed journal events for the selected period, including direct manual entries.
- **All:** these views together with withdrawn or superseded history.

Completed events appear once. A posted event with unresolved work stays in Review or Ready. Journal amounts
show the unsigned entry total; standalone bank transactions retain signed amounts. Links from elsewhere
select the relevant item without hiding other kinds of review work.

For bank and manual items, enter your response and move between rows without saving each one. The save icon beside
Process saves
all edited responses together. A changed instruction, question, accounting record or saved response rejects the
whole stale batch. Refresh retains drafts so you can compare or discard them. Saving queues the responses;
it does not call AI or change posted accounting.

The empty box shows the saved question or **Review the transactions**. Once you type, the question stays in
the tooltip. Only the text you enter is saved as your answer. Issues resolved through Edit retain their
instructions beside the details.
The posting badge/table shows whether accounting is posted.
The pen icon in the detail header opens the journal editor for bank accounting or the original manual instruction.
Investment items open the relevant posting's treatment controls. **All** includes completed manual
conversations, whose responses remain readable without being editable.
The unsaved count beside Save includes responses hidden by a filter. The Undo arrow discards those local
drafts. The bin beside a saved pending response withdraws that response. Failed saves keep
their error visible when you select another item.

Click **Process** after saving answers. It handles saved manual instructions and reviews before ordinary bank
categorisation. It retries failed/interrupted work, skips unchanged
completed work and learns eligible accepted-review guidance. Stop finishes the current safe step before stopping.

## Remembering guidance

Include `#remember` in a human instruction or response when you want that guidance retained.
Saved tagged text contributes to Aurum's private application memory and is supplied to relevant future AI work.
New workspaces also receive Aurum's built-in AI rules. For a UniSuper whole-account investment, ordinary
descriptions of contributions, contribution tax, administration fees and rebates select **Capital in / out**.
A posted investment return selects **Return or fee**. Identify the fund as UniSuper in the account's
institution, report label or instruction. Personal notes and learned guidance stay in the workspace.
This is application guidance, not model training, and it does not rewrite unrelated posted events.

Accepted bank reviews can also produce narrow, evidence-linked guidance during Process.
Conflicting guidance stays inactive. Learning failures do not discard valid accounting.
Readable guidance remains under `data/ai/categorisation_guidelines.md`.

## Journal

Select a saved event in Journal to see its details and postings.
The card shows Accounting date, Source and linked bank Account names, without a bank-transaction count.
Sources distinguish manual entries/instructions, Redbark and PDF filenames; multiple names wrap in the card.
Use **Edit** for a complete balanced correction, retaining imported bank facts and source identities.
Its **Budget** column shows the current classification:

- **Included:** contributes to the cash budget; hover for category names.
- **Savings transfer:** moves money between Cash accounts, including reverse transfers and transfers between
  savings accounts. Savings already equals income less expenses less Investments, so this transfer is not
  counted a second time.
- **Excluded:** an opening balance or an event outside the cash budget; hover for the reason.
- **Needs review:** the accounting or account configuration needs a decision in Review.
- **Unavailable:** the classification could not be loaded or calculated; hover for the explanation.
- **After cutoff:** the accounting date falls after the selected reporting cutoff.

These statuses are calculated from saved accounting, not per-entry switches. Missing budget targets do not
prevent classification. Budget **Income** includes cash receipts such as gifts and investment withdrawals
without changing their income-statement treatment.
The play icon beneath the page title (**Process** in its tooltip) processes saved work for the selected month.
There are no CSV import/export controls; enter and review work in the app.

## Reports

Select **Balance**, **Income**, **Budget** or **Investment** in the switcher. With the switcher focused,
use the left and right arrow keys to change reports. Each report keeps its reading position while you switch.
Scroll through the cards, charts and tables, hover over a chart for exact amounts or select table cells to copy.
In a wide window, Balance, Income and Budget line up the totals at the foot of both columns.
In a narrow window, the sections stack and use only the space their rows need.
The play icon beneath the title (**Generate** in its tooltip) updates all four dashboard reports and saves
the same four PDFs. Hover to see the output folder and any draft reasons for the selected report.
Report settings live in Setup, and accounting issues live in Review.
Total Savings uses a safe, Total Invested uses banknotes, Market or Portfolio Value uses a price tag, and
Investment P&L uses a plus/minus symbol. Generate again to include these icons in previously saved PDFs.

Reports are stored under the configured output folder's monthly subfolder, by default `data/reports/YYYY-MM/`:

- `YYYY-MM Balance Sheet.pdf`
- `YYYY-MM Budget Report.pdf`
- `YYYY-MM Income Statement.pdf`
- `YYYY-MM Investment Report.pdf`

Generating renders all four before replacing saved outputs. Reports use posted accounting and do not process
responses or call AI. Outstanding accounting or unavailable required prices produces a labelled draft.
The dashboard shows the last generated figures using your current saved theme. **Generate** updates the
figures after accounting or report configuration changes and applies the saved theme to the PDF files.
The monthly report folder also contains a private hidden ".aurum-report-view.json" file holding the last
generated dashboard figures and their presentation. Reopening a month or refreshing the workspace loads
that saved result without recalculating it, downloading prices or writing files. Existing months need one
Generate to create the dashboard view. A report icon appears in the empty view until the first generation.
Aurum shows an error for unreadable dashboard data. Changed or missing PDFs leave the last valid dashboard
view available with a warning to regenerate.
Failed generation retains the last valid dashboard view and reports the error.
The shared month selector identifies the dashboard period. PDF headings include the month and year.
Current-month figures stop at the reporting cutoff.

The balance sheet and investment report revalue supported security holdings using completed AUD closes.
Generate checks for a fresh close and falls back to the latest valid saved close on or before the cutoff.
Older saved prices remain usable, with their actual dates shown in the balance-sheet PDF footer.
If no valid price exists, recorded value is retained with a draft warning.
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
are retired; PDF preparation in Transactions performs the same naming and validation through the GUI.
Keep financial data and credentials local and manage your own backups before changing or replacing a database.

## If something needs attention

- **No entries:** the table is empty or its search/filter hides every row. Clear the search or change the filter.
- **Missing workspace files:** use Setup's explicit create action. Do not reset an existing database to refresh it.
- **Changed or stale response:** refresh, compare the current event and resubmit the intended edit.
- **PDF preparation failed:** inspect the file outcome; correct the source or choose conflicting versions separately.
- **Processing failed:** check the activity details, correct the issue, then click Process to retry.
- **Report draft:** resolve accounting questions in Review, check investment classification/prices, then Generate.
- **Sign-in failed:** use Setup → Accounts → Connections → ChatGPT to reconnect and check the selected model.
