"""People's names, kept as their parts and as the whole.

A person's name is collected as a first name, an optional middle name and an
optional last name -- optional because many Indian names, on PAN and Aadhaar
too, have no surname. The parts are stored and edited; the whole name is
composed from them on every write, so everything that reads one name --
passbooks, receipts, joins, the audit log -- keeps working unchanged.

A client that sends only the whole name, as older ones do, has it split into
parts: the first word, the last word, and everything between.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..errors import ValidationError
from .common import optional_text, require_text

#: The longest a single part may be, and the longest the whole.
PART_MAX_LENGTH = 100
FULL_MAX_LENGTH = 200


def compose_full_name(first_name: str | None, middle_name: str | None,
                      last_name: str | None) -> str:
    """The parts that are there, in order, joined by single spaces."""
    parts = [part.strip() for part in (first_name, middle_name, last_name)
             if part and part.strip()]
    return " ".join(parts)


def split_full_name(raw_name: str | None) -> tuple[str, str | None, str | None]:
    """A whole name as (first, middle, last): the first word, the last word,
    and the words between. One word is a first name alone."""
    tokens = (raw_name or "").split()
    if not tokens:
        return ("", None, None)
    if len(tokens) == 1:
        return (tokens[0], None, None)
    if len(tokens) == 2:
        return (tokens[0], None, tokens[1])
    return (tokens[0], " ".join(tokens[1:-1]), tokens[-1])


def resolve_name(sent: Mapping[str, Any], existing: Mapping[str, Any] | None, *,
                 parts: tuple[str, str, str], whole: str) -> dict[str, Any]:
    """A name's validated parts and the whole composed from them, from a save.

    ``parts`` names the first, middle and last name fields and ``whole`` the
    full name's. Parts that were sent win, and a part left out of the save keeps
    what ``existing`` has. A whole name sent without parts is split into them.
    With neither, the existing name stands -- split from its whole if it was
    stored before it had parts.
    """
    first_key, middle_key, last_key = parts
    before = existing or {}
    if any(key in sent for key in parts):
        first, middle, last = (sent[key] if key in sent else before.get(key) for key in parts)
        first = require_text(first, first_key, max_length=PART_MAX_LENGTH)
        middle = optional_text(middle, middle_key, max_length=PART_MAX_LENGTH)
        last = optional_text(last, last_key, max_length=PART_MAX_LENGTH)
    elif whole in sent:
        first, middle, last = split_full_name(
            require_text(sent[whole], whole, max_length=FULL_MAX_LENGTH))
    elif before.get(first_key):
        first, middle, last = (before.get(key) for key in parts)
    elif before.get(whole):
        # Stored before names had parts.
        first, middle, last = split_full_name(before[whole])
    else:
        # Nothing names them yet.
        raise ValidationError(f"{first_key} is required.", details={"field": first_key})
    full = compose_full_name(first, middle, last)
    if len(full) > FULL_MAX_LENGTH:
        raise ValidationError(f"The whole name must be at most {FULL_MAX_LENGTH} characters.",
                              details={"field": whole, "max_length": FULL_MAX_LENGTH})
    return {first_key: first, middle_key: middle, last_key: last, whole: full}
