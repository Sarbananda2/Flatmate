"""The searchable transaction ledger.

One list covering expenses, payments, and adjustments, with the filters the
operator actually needs: month, resident, category, transaction type, status.
Every row carries the identifiers needed to open the underlying record, so a
figure in a report can always be traced to the row that produced it.
"""
from __future__ import annotations

from typing import Any

from ..errors import ValidationError
from ..periods import normalize_month, parse_iso_date
from ..persistence import Database
from .common import get_apartment, paginate

TRANSACTION_TYPES = ("expense", "payment", "adjustment")

# Sort keys; the ordering each one stands for lives with the query, in the
# repository.
_SORTS = ("date_desc", "date_asc", "amount_desc", "amount_asc")


def list_transactions(
    db: Database, *, apartment_id: str, settlement_period: str | None = None,
    resident_id: str | None = None, category: str | None = None,
    transaction_type: str | None = None, status: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    search: str | None = None, limit: int | None = 50, offset: int | None = 0,
    sort: str = "date_desc",
) -> dict[str, Any]:
    limit, offset = paginate(limit, offset)
    if sort not in _SORTS:
        raise ValidationError(f"Unsupported sort: {sort}",
                              details={"field": "sort", "allowed": sorted(_SORTS)})
    if transaction_type and transaction_type not in TRANSACTION_TYPES:
        raise ValidationError(f"Unsupported transaction_type: {transaction_type}",
                              details={"field": "transaction_type",
                                       "allowed": list(TRANSACTION_TYPES)})
    filters: dict[str, Any] = {
        "apartment_id": apartment_id, "settlement_period": None, "resident_id": None,
        "category": None, "transaction_type": None, "status": None,
        "date_from": None, "date_to": None, "search": None}
    if settlement_period:
        filters["settlement_period"] = normalize_month(settlement_period, "settlement_period")
    if resident_id:
        # An expense's resident_id is its payer; a resident's *share* of an expense
        # is matched through the allocation table so filtering by resident finds
        # every transaction that touches them.
        filters["resident_id"] = resident_id
    if category:
        filters["category"] = category
    if transaction_type:
        filters["transaction_type"] = transaction_type
    if status:
        filters["status"] = status
    if date_from:
        filters["date_from"] = parse_iso_date(date_from, "date_from").isoformat()
    if date_to:
        filters["date_to"] = parse_iso_date(date_to, "date_to").isoformat()
    if search:
        filters["search"] = search.strip()

    with db.read() as uow:
        get_apartment(uow, apartment_id)
        total = uow.ledger_view.count_transactions(**filters)
        rows = uow.ledger_view.list_transactions(sort=sort, limit=limit, offset=offset,
                                                 **filters)
        by_type = {row["transaction_type"]: {"count": row["n"], "total_paise": row["total"]}
                   for row in uow.ledger_view.totals_by_type(**filters)}
    return {"items": rows, "total": total, "limit": limit, "offset": offset,
            "totals_by_type": by_type}


def filter_options(db: Database, apartment_id: str) -> dict[str, Any]:
    """The values that actually appear in this apartment's data."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return {
            "residents": uow.ledger_view.residents_for_filter(apartment_id),
            "categories": uow.ledger_view.categories_in_use(apartment_id),
            "settlement_periods": uow.ledger_view.settlement_periods_in_use(apartment_id),
            "transaction_types": list(TRANSACTION_TYPES),
            "statuses": ["unattached", "draft", "finalized_correction_window", "closed",
                         "active", "reversed", "reversal", "posted"],
        }
