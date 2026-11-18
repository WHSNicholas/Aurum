---
target: Manual, Review and Journal workflow
total_score: 26
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:/Users/nicholas/Desktop/3. Projects/Aurum/aurum/gui/activity.py"
target_fingerprint: "sha256:f06cb56f48de451688adaf816e36f56a3f8bf53f63aeb1b45983a2f7ef0a063e"
target_path: /Users/nicholas/Desktop/3. Projects/Aurum/aurum/gui/activity.py
timestamp: 2026-11-16T11-54-06Z
slug: aurum-gui-activity-py
---
Method: dual-agent (A: manual_review_journal_design · B: manual_review_journal_evidence).

**The biggest improvement is to show accounting outcomes more clearly.** Aurum already has a recognisable visual identity: Cormorant headings, restrained colour and monospaced money. These pages still ask users to understand too much internal workflow terminology. More icons alone will not resolve that.

I inspected current native screens using synthetic data and traced the budget calculation. The app and personal records remain unchanged.

| Usability heuristic | Score / 4 | Main finding |
|---|---:|---|
| System status | 3 | Several labels describe the same unfinished item |
| Real-world language | 2 | Budget settings do not explain budget results |
| User control | 3 | Draft preservation and explicit saves are strong |
| Consistency | 2 | Identical pencils can perform different edits |
| Error prevention | 3 | Strong accounting checks; unclear budget reset |
| Recognition over recall | 2 | Important meaning is hidden in tooltips |
| Efficiency | 3 | Batch saves and searchable accounts work well |
| Minimalism | 3 | Repeated totals, statuses and guidance |
| Error recovery | 3 | Drafts and revision checks protect corrections |
| Contextual help | 2 | Explanations are detached from decisions |
| **Total** | **26/40** | **Usable, with significant clarity improvements available** |

The shared detail viewer, inline inspection and batch Review saving are worth keeping. They give the three pages continuity and protect users from losing work.

1. **P1 — Replace “Budget treatment” with the actual budget impact.**

   “Normal rules” currently covers completely different outcomes. An expense, a bank transfer and an opening balance can all display it.

   The current calculation is:

   | Entry | Default budget impact |
   |---|---|
   | $40 grocery purchase | Expenses +$40 |
   | $40 grocery refund | Expenses −$40 |
   | $300 transfer between bank accounts | No impact |
   | Bank opening balance against capital | No impact |

   Income and expense postings normally count, including those without a configured budget category. Asset, liability and equity postings normally do not. Excluding only the bank side of a grocery purchase therefore leaves the expense counted. Budget choices change the budget report, not the journal amounts or balance sheet.

   **Savings needs a separate distinction:** total savings is income less expenses. Savings categories allocate that total. Tagging a $300 transfer as savings does not create another $300 of savings.

   I would show **“Expenses · Groceries · $40.00”**, **“No budget impact”**, or **“Excluded from budget”** in the shared viewer. The editor should show the same calculated preview, with **Adjust budget** revealing category choices and split postings. Savings adjustments should show both affected allocations.

   Replace the current whole-entry selector with explicit actions: **Exclude from budget** and **Reset budget choices**. Choosing “Normal rules” currently resets every line, potentially removing deliberate exceptions; “Custom by line” is a status and should not be a disabled menu option. Keep investment-cost classification separate.

   Use the existing report calculation, including the full month's allocations and warnings, for this preview. Relevant implementation: [budget calculation](/Users/nicholas/Desktop/3.%20Projects/Aurum/aurum/finance/budget.py:608) and [bulk budget changes](/Users/nicholas/Desktop/3.%20Projects/Aurum/aurum/gui/editor.py:521). Suggested Impeccable pass: clarify.

2. **P1 — Give each page one clear responsibility and one primary editing route.**

   | Page | Main job | Primary interaction |
   |---|---|---|
   | Manual | Create an instruction or direct entry | Add → enter → save instruction or post |
   | Review | Resolve unfinished work | Select → read question → respond → save |
   | Journal | Inspect and correct posted accounting | Select → inspect → edit postings |

   Manual currently has a toolbar pencil and a detail-card pencil. For a posted instruction, one can reopen its instruction while the other edits posted accounting directly. They look interchangeable but have different consequences.

   Keep one primary edit action and put the alternative behind clearly named **Edit instruction / Edit postings** choices. Preserve manual history as provenance; it does not need to become a second ledger. Process should remain the same shared operation on all three pages.

   Use icons for add, copy, delete and disclosures. Keep words on consequential actions such as **Post**, **Save** and **Process**. Suggested pass: distill.

3. **P2 — Make the question the visual centre of Review.**

   The current evidence card dominates the right pane. The actual question appears underneath as subdued “Status: …”, while “No unsaved changes” receives a large highlighted footer.

   The hierarchy should be **amount and description → question → Response → supporting evidence**. Keep the question close to its input; make dates, references and source details quieter or expandable. Use a single task label such as **Needs answer**, retaining a separate Posted badge only when it conveys accounting state.

   Remove “Status:” before a question, zero-count category summaries and the idle “No unsaved changes” banner. Show **3 unsaved responses** only when relevant. Preserve batch saving and retained drafts.

   Manual questions currently require opening another composer. A shared inline Response pattern would make Review a more complete inbox. Also fix the incompatible filter combination: selecting Manual with “All bank transactions” can produce a misleading empty result. Suggested passes: layout and clarify.

4. **P2 — Let the Manual editor start simply.**

   A new direct entry immediately shows “Posting 1: choose an existing account” and an expanded Budget panel before the user has entered anything. This makes optional classification feel like the first task.

   Start with date and posting rows. Reveal budget/investment adjustments when relevant or requested. Show required-field errors after interaction or an attempted submission; preserve the validation itself.

   For an unfinished instruction, show its question or processing state instead of a journal card containing a large amount dash and empty budget metadata. Once posted, use the shared journal card.

   Keep a concise distinction between saving an instruction for processing and posting immediately. Remove repeated explanations from placeholders and footers. The information icon currently opens editable line settings; an adjustment icon would communicate that action better. Suggested passes: onboard and distill.

5. **P2 — Make Journal easier to scan and bring details closer to selection.**

   Every balanced event repeats the same aggregate number under Debits and Credits. Use **Date · Description · Amount** in the event list. Keep the actual debit and credit columns in the posting detail, where they provide useful information.

   In Manual and Journal, short lists stretch into large empty areas before the selected detail card. Bound the short-list height so the inspector follows the populated rows, retaining the intentional gap.

   Apply the shared monetary font and narrow-space thousands formatting consistently: the list currently shows values such as `6000.00`, while the card uses grouped currency. Suggested passes: layout and typeset.

Smaller copy reductions: **Instruction / summary → Description**, **Edit the Journal Entry → Edit entry**, **Link statement transactions… → Bank transactions**, and long search prompts → **Search entries**. Keep `#remember` guidance in contextual help rather than every empty Response field. Verify and clear editor-specific footer guidance when the editor closes; it persisted into other pages in the synthetic navigation sequence.

The cognitive load comes from remembering what budget choices mean and which editing route to use. The Review type menu also offers five choices before its second filter; simplify their relationship rather than removing legitimate accounting categories. The emotional friction starts with an immediate error on a blank form and ends with uncertainty about the resulting budget effect.

For a first-time user, the budget terminology and premature errors are the main obstacles. For a regular user, repeated amounts and competing pencils slow work. For keyboard and VoiceOver users, essential tooltip-only explanations deserve attention; native accessibility and keyboard behaviour still need a dedicated check.

I recommend implementing the shared budget-impact model first, then applying the page hierarchy changes around it. That establishes one consistent explanation across all three pages.

Two decisions would shape that implementation:

1. Budget controls: **compact impact summary with Adjust budget (recommended)**, or **always-visible per-line controls**?
2. Manual questions: **answer inline in Review (recommended)**, or **continue opening the Manual composer**?
