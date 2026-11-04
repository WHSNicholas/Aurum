# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Database - Memory                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum remembers explicitly tagged human instructions in their existing private SQLite records.

The latest saved response or manual submission is authoritative. Editing or removing its tag updates its
memory; clearing a bank answer or withdrawing a manual instruction removes its active notes. This read-only
projection never edits skill files, changes accounting or creates a second store to keep synchronised.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from hashlib import sha256
import json
import re
import sqlite3

# Aurum imports
from aurum.finance.manual import ManualInput
from aurum.finance.review import ReviewAnswer

# A marker must be a complete hashtag, not part of another word or hashtag.
_REMEMBER = re.compile(r"(?<![\w#])#remember(?![\w-])", re.IGNORECASE)


# %% 1. Explicit Human Memory --------------------------------------------------------------------------------
def remember_text(text: str) -> str:
    """
    Read the note after the first standalone tag, or the whole preceding message for a trailing tag.

    Case does not matter. Repeated markers are removed while the note's other wording and line breaks stay
    intact. An absent tag or a tag with no accompanying words supplies no memory.
    """
    marker = _REMEMBER.search(text)
    if marker is None:
        return ""
    following = _REMEMBER.sub("", text[marker.end():]).strip()
    return following or _REMEMBER.sub("", text[:marker.start()]).strip()


def load_explicit_memory(connection: sqlite3.Connection) -> list[dict]:
    """
    Read tagged notes from each current bank response and manual input without changing the database.

    Each note retains its local source identity and saved revision. Provenance stays separate from its text,
    allowing callers to redact outbound wording without exposing private record identifiers.
    """
    latest_answers = {}
    for identifier, payload in connection.execute(
        "SELECT id, payload FROM review_actions WHERE kind = 'answer' ORDER BY created_at, rowid"
    ):
        answer = ReviewAnswer.model_validate_json(payload)
        latest_answers[str(answer.transaction_id)] = (str(identifier), answer)
    cleared = {
        str(identifier) for identifier, payload in connection.execute(
            "SELECT transaction_id, payload FROM review_actions WHERE kind = 'export'"
        ) if json.loads(payload).get("human_review_cleared", False)
    }
    notes = []
    for source_id, (revision, answer) in latest_answers.items():
        if source_id not in cleared:
            _append_note(notes, answer.human_review, "review", source_id, revision)
    latest_manuals = {}
    for revision, source_id, payload, status in connection.execute(
        "SELECT id, manual_id, submission, status FROM manual_entries ORDER BY rowid"
    ):
        latest_manuals[str(source_id)] = (str(revision), payload, status)
    for source_id, (revision, payload, status) in latest_manuals.items():
        if status == "withdrawn":
            continue
        submission = ManualInput.model_validate_json(payload)
        _append_note(notes, submission.instruction, "manual_instruction", source_id, revision)
        _append_note(notes, submission.human_response, "manual_response", source_id, revision)
    return sorted(notes, key=lambda item: (item["source_kind"], item["source_id"]))


def memory_revision(connection: sqlite3.Connection) -> str:
    """
    Fingerprint current explicit notes so an interpretation cannot reuse outdated human memory.
    """
    content = json.dumps(load_explicit_memory(connection), sort_keys=True, ensure_ascii=False)
    return sha256(content.encode("utf-8")).hexdigest()


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _append_note(notes: list[dict], text: str, source_kind: str, source_id: str, revision: str) -> None:
    """
    Keep a tagged note's complete wording alongside stable local provenance.
    """
    note = remember_text(text)
    if note:
        notes.append({
            "id": f"{source_kind}:{source_id}", "text": note,
            "source_kind": source_kind, "source_id": source_id, "revision": revision,
        })
