"""The searchable transaction ledger.

One list covering expenses, payments, and adjustments, with the filters the
operator actually needs: month, resident, category, transaction type, status.
Every row carries the identifiers needed to open the underlying record, so a
figure in a report can always be traced to the row that produced it.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..db import connect
from ..errors import ValidationError
from ..periods import normalize_month, parse_iso_date
from .common import get_apartment, paginate

TRANSACTION_TYPES = ("expense", "payment", "adjustment")

_SORTS = {
    "date_desc": ("transaction_date DESC, created_at DESC, transaction_id DESC"),
    "date_asc": ("transaction_date ASC, created_at ASC, transaction_id ASC"),
    "amount_desc": ("ABS(amount_paise) DESC, transaction_date DESC"),
    "amount_asc": ("ABS(amount_paise) ASC, transaction_date ASC"),
}

# One shape for all three sources so they can be filtered and sorted together.
_UNION_SQL = """
SELECT 'expense' AS transaction_type,
       e.expense_id AS transaction_id,
       e.expense_date AS transaction_date,
       e.settlement_period AS settlement_period,
       e.service_period AS service_period,
       e.description AS description,
       e.amount_paise AS amount_paise,
       e.expense_kind AS subtype,
       COALESCE(d.category, '') AS category,
       e.payer_resident_id AS resident_id,
       COALESCE(pr.display_name, '') AS resident_display_name,
       COALESCE(e.provider_name, '') AS counterparty,
       e.statement_id AS statement_id,
       COALESCE(s.status, 'unattached') AS status,
       e.reference AS reference,
       e.created_at AS created_at,
       'Household expense incurred' AS effect
  FROM expense_instance e
  LEFT JOIN expense_definition d ON d.expense_definition_id = e.expense_definition_id
  LEFT JOIN resident pr ON pr.resident_id = e.payer_resident_id
  LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
 WHERE e.apartment_id = :apartment_id

UNION ALL

SELECT 'payment' AS transaction_type,
       p.payment_id,
       p.payment_date,
       substr(p.payment_date, 1, 7),
       substr(p.payment_date, 1, 7),
       COALESCE(p.note, p.payee_or_provider, p.payment_kind),
       p.amount_paise,
       p.payment_kind,
       '' AS category,
       p.payer_resident_id,
       r.display_name,
       COALESCE(p.payee_or_provider, 'Household pool'),
       NULL AS statement_id,
       p.status,
       p.reference,
       COALESCE(p.created_at, p.payment_date),
       CASE p.payment_kind
            WHEN 'to_pool' THEN 'Resident transfer into the pool'
            WHEN 'provider_advance' THEN 'Payment to an external provider'
            WHEN 'refund' THEN 'Refund paid out of the pool'
            ELSE 'Legacy payment adjustment' END
  FROM payment p
  JOIN resident r ON r.resident_id = p.payer_resident_id
 WHERE p.apartment_id = :apartment_id

UNION ALL

SELECT 'adjustment' AS transaction_type,
       la.adjustment_id,
       COALESCE(la.effective_date, substr(la.created_at, 1, 10)),
       substr(COALESCE(la.effective_date, la.created_at), 1, 7),
       substr(COALESCE(la.effective_date, la.created_at), 1, 7),
       la.reason,
       la.amount_paise,
       CASE WHEN la.amount_paise >= 0 THEN 'credit' ELSE 'debit' END,
       '' AS category,
       la.resident_id,
       r.display_name,
       'Household pool',
       la.statement_id,
       'posted',
       NULL AS reference,
       la.created_at,
       'Signed ledger adjustment' AS effect
  FROM ledger_adjustment la
  JOIN resident r ON r.resident_id = la.resident_id
 WHERE r.apartment_id = :apartment_id
"""


def list_transactions(
    db_path, *, apartment_id: str, settlement_period: str | None = None,
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
    params: dict[str, Any] = {"apartment_id": apartment_id}
    clauses: list[str] = []
    if settlement_period:
        clauses.append("settlement_period = :settlement_period")
        params["settlement_period"] = normalize_month(settlement_period, "settlement_period")
    if resident_id:
        # An expense's resident_id is its payer; a resident's *share* of an expense
        # is matched through the allocation table so filtering by resident finds
        # every transaction that touches them.
        clauses.append("(resident_id = :resident_id OR (transaction_type = 'expense' AND "
                       "EXISTS (SELECT 1 FROM allocation a WHERE a.expense_id = transaction_id "
                       "AND a.resident_id = :resident_id)))")
        params["resident_id"] = resident_id
    if category:
        clauses.append("category = :category")
        params["category"] = category
    if transaction_type:
        clauses.append("transaction_type = :transaction_type")
        params["transaction_type"] = transaction_type
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if date_from:
        clauses.append("transaction_date >= :date_from")
        params["date_from"] = parse_iso_date(date_from, "date_from").isoformat()
    if date_to:
        clauses.append("transaction_date <= :date_to")
        params["date_to"] = parse_iso_date(date_to, "date_to").isoformat()
    if search:
        clauses.append("(description LIKE :search OR counterparty LIKE :search "
                       "OR transaction_id LIKE :search OR COALESCE(reference,'') LIKE :search "
                       "OR resident_display_name LIKE :search)")
        params["search"] = f"%{search.strip()}%"
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    inner = f"SELECT * FROM ({_UNION_SQL}) {where}"

    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        total = int(conn.execute(f"SELECT COUNT(*) FROM ({inner})", params).fetchone()[0])
        rows = db.fetch_all(
            conn, f"{inner} ORDER BY {_SORTS[sort]} LIMIT :limit OFFSET :offset",
            {**params, "limit": limit, "offset": offset})
        by_type = {row["transaction_type"]: {"count": row["n"], "total_paise": row["total"]}
                   for row in conn.execute(
                       f"SELECT transaction_type, COUNT(*) AS n, "
                       f"COALESCE(SUM(amount_paise),0) AS total FROM ({inner}) "
                       f"GROUP BY transaction_type", params).fetchall()}
    return {"items": rows, "total": total, "limit": limit, "offset": offset,
            "totals_by_type": by_type}


def filter_options(db_path, apartment_id: str) -> dict[str, Any]:
    """The values that actually appear in this apartment's data."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        return {
            "residents": db.fetch_all(
                conn, "SELECT resident_id, display_name, is_active FROM resident "
                      "WHERE apartment_id=? ORDER BY display_name", (apartment_id,)),
            "categories": [row[0] for row in conn.execute(
                "SELECT DISTINCT category FROM expense_definition WHERE apartment_id=? "
                "ORDER BY category", (apartment_id,)).fetchall()],
            "settlement_periods": [row[0] for row in conn.execute(
                "SELECT DISTINCT settlement_period FROM expense_instance "
                "WHERE apartment_id=? AND settlement_period IS NOT NULL "
                "ORDER BY settlement_period DESC", (apartment_id,)).fetchall()],
            "transaction_types": list(TRANSACTION_TYPES),
            "statuses": ["unattached", "draft", "finalized_correction_window", "closed",
                         "active", "reversed", "reversal", "posted"],
        }
