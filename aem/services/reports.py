"""Reports and CSV exports.

Every report reads the same records the dashboard and the statements read, so a
figure in a CSV always matches the figure on screen. A statement export is
built from the statement's **stored snapshot** when one exists, so an exported
finalized statement reproduces exactly what was finalized rather than what the
ledger looks like today.

Money is exported twice in every row: once as integer paise (the authoritative
value) and once as a plain rupee string for spreadsheets. No currency symbol or
digit grouping is written into a CSV, so the values parse cleanly.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, Sequence

from .. import db
from ..db import connect, now_timestamp
from ..errors import NotFoundError, ValidationError
from ..money import paise_to_rupee_string
from ..periods import normalize_month
from .common import POOL_ACCOUNT_ID, get_apartment, get_statement
from .electricity import consumption_history
from .reconciliation import ledger_entries, reconcile
from .statements import read_finalized_snapshot, statement_snapshot

REPORT_KINDS = (
    "monthly_household_expenses",
    "resident_expense_breakdown",
    "resident_payments_and_balances",
    "provider_advances",
    "electricity_consumption",
    "credits_and_adjustments",
    "statement_summary",
    "statement_detail",
    "transaction_ledger",
    "audit_trail",
)


def _money_columns(rows: Sequence[dict[str, Any]]) -> list[str]:
    return sorted({key for row in rows for key in row if key.endswith("_paise")})


def to_csv(rows: Sequence[dict[str, Any]], *, columns: Sequence[str] | None = None) -> str:
    """Render rows as CSV, adding a rupee column beside every paise column."""
    if not rows:
        return ""
    money = _money_columns(rows)
    if columns is None:
        ordered: list[str] = []
        for row in rows:
            for key in row:
                if key not in ordered:
                    ordered.append(key)
        columns = ordered
    header: list[str] = []
    for column in columns:
        header.append(column)
        if column in money:
            header.append(column[:-6] + "_rupees")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        out: dict[str, Any] = {}
        for column in columns:
            value = row.get(column)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True, default=str)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            out[column] = value
            if column in money and isinstance(row.get(column), int):
                out[column[:-6] + "_rupees"] = paise_to_rupee_string(row[column])
        writer.writerow(out)
    return buffer.getvalue()


def monthly_household_expenses(db_path, *, apartment_id: str,
                               settlement_period: str | None = None) -> list[dict[str, Any]]:
    """Household expenses by settlement month, or the lines within one month."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        if settlement_period:
            period = normalize_month(settlement_period, "settlement_period")
            return db.fetch_all(
                conn,
                """SELECT e.settlement_period, e.service_period, e.expense_date,
                          e.expense_id, e.description, COALESCE(d.category, '') AS category,
                          e.expense_kind, e.amount_paise, e.provider_name,
                          COALESCE(r.display_name, 'Household pool') AS funded_by,
                          CASE WHEN EXISTS (SELECT 1 FROM payment p
                                             WHERE p.expense_id = e.expense_id
                                               AND p.payment_kind='provider_advance'
                                               AND p.status='active')
                               THEN 'linked_provider_advance'
                               WHEN e.payer_resident_id IS NOT NULL THEN 'payer_resident'
                               ELSE 'household_pool' END AS funding_source,
                          COALESCE(s.status, 'unattached') AS statement_status
                     FROM expense_instance e
                     LEFT JOIN expense_definition d
                            ON d.expense_definition_id = e.expense_definition_id
                     LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
                     LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
                    WHERE e.apartment_id = ? AND e.settlement_period = ?
                    ORDER BY e.expense_date, e.rowid""", (apartment_id, period))
        return db.fetch_all(
            conn,
            """SELECT e.settlement_period, COUNT(*) AS expense_count,
                      SUM(e.amount_paise) AS total_paise,
                      SUM(CASE WHEN e.expense_kind='electricity' THEN e.amount_paise ELSE 0 END)
                          AS electricity_paise,
                      SUM(CASE WHEN e.payer_resident_id IS NOT NULL THEN e.amount_paise ELSE 0 END)
                          AS resident_funded_paise
                 FROM expense_instance e WHERE e.apartment_id = ?
                GROUP BY e.settlement_period ORDER BY e.settlement_period DESC""",
            (apartment_id,))


def resident_expense_breakdown(db_path, *, apartment_id: str,
                               settlement_period: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = [apartment_id]
    clause = ""
    if settlement_period:
        clause = " AND e.settlement_period = ?"
        params.append(normalize_month(settlement_period, "settlement_period"))
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        return db.fetch_all(
            conn,
            f"""SELECT e.settlement_period, a.resident_id, r.display_name,
                       COALESCE(d.category, 'Uncategorised') AS category,
                       COUNT(DISTINCT e.expense_id) AS expense_count,
                       SUM(a.amount_paise) AS charged_paise,
                       COALESCE(SUM((SELECT COALESCE(SUM(pa.amount_paise), 0)
                                       FROM payment_application pa
                                       JOIN payment p ON p.payment_id = pa.payment_id
                                      WHERE pa.allocation_id = a.allocation_id
                                        AND p.status = 'active')), 0) AS settled_paise
                  FROM allocation a
                  JOIN expense_instance e ON e.expense_id = a.expense_id
                  JOIN resident r ON r.resident_id = a.resident_id
                  LEFT JOIN expense_definition d
                         ON d.expense_definition_id = e.expense_definition_id
                 WHERE e.apartment_id = ?{clause}
                 GROUP BY e.settlement_period, a.resident_id, category
                 ORDER BY e.settlement_period DESC, r.display_name, category""", params)


def resident_payments_and_balances(db_path, *, apartment_id: str) -> list[dict[str, Any]]:
    """Per-resident payments, credits, outstanding charges, and net position."""
    reconciliation = reconcile(db_path, apartment_id=apartment_id)
    return [
        {
            "resident_id": account["account_id"],
            "display_name": account["display_name"],
            "is_active": account["is_active"],
            "allocated_charges_paise": account["allocated_charges_paise"],
            "settled_paise": account["settled_paise"],
            "outstanding_charges_paise": account["outstanding_charges_paise"],
            "unapplied_credit_paise": account["unapplied_credit_paise"],
            "pool_payments_paise": account["pool_payments_paise"],
            "provider_advances_paise": account["provider_advances_paise"],
            "expense_funding_paise": account["expense_funding_paise"],
            "refunds_paise": account["refunds_paise"],
            "adjustments_paise": account["adjustments_paise"],
            "net_position_paise": account["net_position_paise"],
            "net_position_meaning": ("household owes resident"
                                     if account["net_position_paise"] > 0
                                     else "resident owes household"
                                     if account["net_position_paise"] < 0 else "settled"),
        }
        for account in reconciliation["accounts"]
        if account["account_type"] == "resident"
    ]


def provider_advance_report(db_path, *, apartment_id: str) -> list[dict[str, Any]]:
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        rows = db.fetch_all(
            conn,
            """SELECT p.payment_id, p.payment_date, p.payer_resident_id,
                      r.display_name, p.amount_paise, p.payee_or_provider, p.reference,
                      p.status, p.expense_id, e.description AS funded_expense_description,
                      e.amount_paise AS funded_expense_paise,
                      e.settlement_period AS funded_settlement_period,
                      e.payer_resident_id AS expense_payer_resident_id
                 FROM payment p
                 JOIN resident r ON r.resident_id = p.payer_resident_id
                 LEFT JOIN expense_instance e ON e.expense_id = p.expense_id
                WHERE p.apartment_id = ? AND p.payment_kind = 'provider_advance'
                ORDER BY p.payment_date DESC, p.rowid DESC""", (apartment_id,))
    for row in rows:
        row["is_linked"] = row["expense_id"] is not None
        row["double_count_risk"] = (
            not row["is_linked"] and row["expense_payer_resident_id"] is None
            and row["status"] == "active")
        row["funding_note"] = (
            "Linked: this advance is the funding record and the expense's payer field is "
            "ignored." if row["is_linked"]
            else "Not linked to an expense: credited as a standalone advance.")
    return rows


def credits_and_adjustments(db_path, *, apartment_id: str) -> list[dict[str, Any]]:
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        credits = db.fetch_all(
            conn,
            """SELECT 'unapplied_credit' AS entry_type, p.payment_id AS entry_id,
                      p.payment_date AS entry_date, p.payer_resident_id AS resident_id,
                      r.display_name,
                      p.amount_paise - COALESCE((SELECT SUM(pa.amount_paise)
                                                   FROM payment_application pa
                                                  WHERE pa.payment_id = p.payment_id), 0)
                          AS amount_paise,
                      'Overpayment held as a credit' AS reason, NULL AS statement_id,
                      NULL AS reversed_by
                 FROM payment p JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = ? AND p.payment_kind='to_pool' AND p.status='active'
                  AND p.amount_paise > COALESCE((SELECT SUM(pa.amount_paise)
                                                   FROM payment_application pa
                                                  WHERE pa.payment_id = p.payment_id), 0)""",
            (apartment_id,))
        adjustments = db.fetch_all(
            conn,
            """SELECT 'ledger_adjustment' AS entry_type, la.adjustment_id AS entry_id,
                      COALESCE(la.effective_date, substr(la.created_at,1,10)) AS entry_date,
                      la.resident_id, r.display_name, la.amount_paise, la.reason,
                      la.statement_id,
                      (SELECT rv.adjustment_id FROM ledger_adjustment rv
                        WHERE rv.reverses_adjustment_id = la.adjustment_id) AS reversed_by
                 FROM ledger_adjustment la JOIN resident r ON r.resident_id = la.resident_id
                WHERE r.apartment_id = ?""", (apartment_id,))
    return sorted(credits + adjustments, key=lambda row: (row["entry_date"] or "",
                                                          row["entry_id"]), reverse=True)


def statement_summary(db_path, *, apartment_id: str) -> list[dict[str, Any]]:
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        return db.fetch_all(
            conn,
            """SELECT s.statement_id, s.settlement_month, s.status, s.created_at,
                      s.finalized_at, s.finalized_by, s.correction_deadline, s.closed_at,
                      (SELECT COUNT(*) FROM expense_instance e
                        WHERE e.statement_id = s.statement_id) AS expense_count,
                      (SELECT COALESCE(SUM(e.amount_paise), 0) FROM expense_instance e
                        WHERE e.statement_id = s.statement_id) AS statement_total_paise,
                      (SELECT COUNT(*) FROM statement_revision sr
                        WHERE sr.statement_id = s.statement_id) AS revision_count
                 FROM monthly_statement s WHERE s.apartment_id = ?
                ORDER BY s.settlement_month DESC""", (apartment_id,))


def statement_detail_rows(db_path, statement_id: str) -> list[dict[str, Any]]:
    """Statement lines, from the stored snapshot when the statement is finalized."""
    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
    if statement["snapshot_json"]:
        snapshot = read_finalized_snapshot(db_path, statement_id)["snapshot"]
        source = "stored_snapshot"
    else:
        snapshot = statement_snapshot(db_path, statement_id)
        source = "live_draft"
    rows: list[dict[str, Any]] = []
    names = {r["resident_id"]: r["display_name"] for r in snapshot.get("residents", [])}
    for expense in snapshot.get("expenses", []):
        for allocation in expense.get("allocations", []):
            rows.append({
                "source": source,
                "settlement_month": snapshot["settlement_month"],
                "statement_status": snapshot["status"],
                "expense_id": expense["expense_id"],
                "description": expense["description"],
                "category": expense.get("category") or "",
                "expense_kind": expense.get("expense_kind") or "general",
                "expense_date": expense["expense_date"],
                "service_period": expense.get("service_period"),
                "settlement_period": expense.get("settlement_period"),
                "period_rule": expense.get("period_rule"),
                "expense_amount_paise": expense["amount_paise"],
                "funding_source": expense.get("funding_source"),
                "payer_resident_id": expense.get("payer_resident_id") or "",
                "resident_id": allocation["resident_id"],
                "resident_display_name": names.get(allocation["resident_id"],
                                                   allocation["resident_id"]),
                "share_paise": allocation["amount_paise"],
            })
    return rows


def statement_export(db_path, statement_id: str) -> dict[str, Any]:
    """The printable/exportable statement: lines, charges, and net positions."""
    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
    if statement["snapshot_json"]:
        read = read_finalized_snapshot(db_path, statement_id)
        snapshot, source, hash_matches = read["snapshot"], "stored_snapshot", read["hash_matches"]
    else:
        snapshot, source, hash_matches = statement_snapshot(db_path, statement_id), \
            "live_draft", None
    return {
        "source": source,
        "snapshot_hash": snapshot.get("snapshot_hash"),
        "snapshot_hash_matches": hash_matches,
        "statement": {k: snapshot.get(k) for k in
                      ("statement_id", "apartment_id", "settlement_month", "status",
                       "finalized_at", "finalized_by", "correction_deadline", "closed_at",
                       "generated_at", "statement_total_paise", "expense_count")},
        "residents": snapshot.get("residents", []),
        "expenses": snapshot.get("expenses", []),
        "statement_charges_paise_by_resident":
            snapshot.get("statement_charges_paise_by_resident", {}),
        "accounts": snapshot.get("accounts", []),
        "suggested_transfers": snapshot.get("transfers", snapshot.get("suggested_transfers", [])),
        "adjustments": snapshot.get("adjustments", []),
        "payments_in_month": snapshot.get("payments_in_month", []),
        "revisions": snapshot.get("revisions", []),
        "detail_rows": statement_detail_rows(db_path, statement_id),
        "validation": snapshot.get("validation", {}),
    }


def audit_trail_rows(db_path, *, apartment_id: str, limit: int = 2000) -> list[dict[str, Any]]:
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        return db.fetch_all(
            conn,
            """SELECT created_at, actor, entity_type, entity_id, action, field_name,
                      old_value, new_value, reason
                 FROM audit_log
                WHERE apartment_id = ? OR apartment_id IS NULL
                ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (apartment_id, int(limit)))


def build_report(db_path, kind: str, *, apartment_id: str,
                 settlement_period: str | None = None,
                 statement_id: str | None = None) -> dict[str, Any]:
    """Dispatch to one report and return its rows plus a title for display."""
    if kind not in REPORT_KINDS:
        raise ValidationError(f"Unknown report: {kind}",
                              details={"field": "kind", "allowed": list(REPORT_KINDS)})
    if kind in ("statement_detail",) and not statement_id:
        raise ValidationError("statement_id is required for the statement detail report.",
                              details={"field": "statement_id"})

    if kind == "monthly_household_expenses":
        rows = monthly_household_expenses(db_path, apartment_id=apartment_id,
                                          settlement_period=settlement_period)
        title = ("Household expenses — " + settlement_period if settlement_period
                 else "Household expenses by month")
    elif kind == "resident_expense_breakdown":
        rows = resident_expense_breakdown(db_path, apartment_id=apartment_id,
                                          settlement_period=settlement_period)
        title = "Resident expense breakdown"
    elif kind == "resident_payments_and_balances":
        rows = resident_payments_and_balances(db_path, apartment_id=apartment_id)
        title = "Resident payments and outstanding balances"
    elif kind == "provider_advances":
        rows = provider_advance_report(db_path, apartment_id=apartment_id)
        title = "Provider advances"
    elif kind == "electricity_consumption":
        rows = consumption_history(db_path, apartment_id)
        title = "Electricity consumption and allocation"
    elif kind == "credits_and_adjustments":
        rows = credits_and_adjustments(db_path, apartment_id=apartment_id)
        title = "Credits and adjustments"
    elif kind == "statement_summary":
        rows = statement_summary(db_path, apartment_id=apartment_id)
        title = "Statement summaries"
    elif kind == "statement_detail":
        rows = statement_detail_rows(db_path, statement_id)
        title = "Statement detail"
    elif kind == "transaction_ledger":
        from .ledger_view import list_transactions
        rows = list_transactions(db_path, apartment_id=apartment_id,
                                 settlement_period=settlement_period, limit=500)["items"]
        title = "Transaction ledger"
    else:  # audit_trail
        rows = audit_trail_rows(db_path, apartment_id=apartment_id)
        title = "Audit trail"

    return {"kind": kind, "title": title, "apartment_id": apartment_id,
            "settlement_period": settlement_period, "statement_id": statement_id,
            "generated_at": now_timestamp(), "rows": rows, "row_count": len(rows)}
