"""Settlement summaries for sharing outside the application.

A summary is a rendering of figures the statement already holds. It is built
from the stored snapshot when the statement has been finalized, so what is
shared is exactly what was sealed, and from a live preview while the statement
is still a draft. Building one never writes anything.

A sealed summary is shared for days after it is sealed, and people pay in the
meantime. Its figures and its hash stay those of the snapshot, but each sealed
transfer is marked settled or pending from the payments made since, so
nobody pays a transfer twice.

UPI links are a convenience for the reader. The amount in a link is the
suggested transfer and nothing more: following it does not record a payment.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

from ..db import connect
from ..money import format_inr, paise_to_rupee_string
from ..periods import parse_month
from .common import POOL_ACCOUNT_ID, get_apartment, get_statement
from .reconciliation import reconcile
from .statements import snapshot_hash, statement_snapshot

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_RULE = "━━━━━━━━━━━━━━━━━━━━━"


def _month_label(month: str) -> str:
    year, number = parse_month(month)
    return f"{_MONTHS[number - 1]} {year}"


def upi_uri(*, upi_id: str, payee_name: str, amount_paise: int, note: str) -> str:
    """A ``upi://pay`` link. The amount is rendered from integer paise."""
    query = urlencode(
        {"pa": upi_id, "pn": payee_name, "am": paise_to_rupee_string(amount_paise),
         "cu": "INR", "tn": note},
        quote_via=quote, safe="@")
    return f"upi://pay?{query}"


def _paid_since(db_path, apartment_id: str,
                snapshot: dict[str, Any]) -> dict[tuple[str, str], int]:
    """Money moved since the snapshot was taken, by (resident, payment kind).

    The snapshot holds each resident's pool payments and refunds as they stood
    when it was sealed, so the difference from today's totals is exactly what
    the sealed transfers do not already reflect -- whatever date a payment
    carries. Reversed payments are in neither total, so reversing a payment
    reopens the transfer it had settled. A snapshot too old to hold a total
    gives no evidence of payment, and its transfers stay pending.
    """
    today = reconcile(db_path, apartment_id=apartment_id)
    paid: dict[tuple[str, str], int] = {}
    for kind, field in (("to_pool", "pool_payments_paise_by_resident"),
                        ("refund", "refunds_paise_by_resident")):
        sealed_totals = snapshot.get(field)
        if sealed_totals is None:
            continue
        for resident_id, total in today[field].items():
            paid[(resident_id, kind)] = total - sealed_totals.get(resident_id, 0)
    return paid


def _settled_flags(transfers: list[dict[str, Any]],
                   paid: dict[tuple[str, str], int]) -> list[bool]:
    """Whether each sealed transfer has been paid in full since the seal.

    A resident's transfer is covered by what they have paid into the pool, which
    is also the payer's leg of a direct settlement; a transfer out of the pool is
    covered by refunds to its receiver. Payments are spent on a payer's transfers
    in the order the snapshot lists them, and a transfer that is only partly
    covered stays pending.
    """
    remaining = dict(paid)
    flags = []
    for transfer in transfers:
        key = ((transfer["to_account"], "refund")
               if transfer["from_account"] == POOL_ACCOUNT_ID
               else (transfer["from_account"], "to_pool"))
        settled = remaining.get(key, 0) >= transfer["amount_paise"]
        if settled:
            remaining[key] -= transfer["amount_paise"]
        flags.append(settled)
    return flags


def whatsapp_summary(db_path, statement_id: str) -> dict[str, Any]:
    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
        apartment = get_apartment(conn, statement["apartment_id"])
        upi_ids = {
            row["resident_id"]: row["upi_id"]
            for row in conn.execute(
                "SELECT resident_id, upi_id FROM resident WHERE apartment_id=?",
                (statement["apartment_id"],)).fetchall()
            if row["upi_id"]
        }
        stored = statement["snapshot_json"]

    if stored:
        snapshot = json.loads(stored)
        sealed = True
        paid = _paid_since(db_path, statement["apartment_id"], snapshot)
        hash_matches = snapshot.get("snapshot_hash") == snapshot_hash(snapshot)
    else:
        snapshot = statement_snapshot(db_path, statement_id)
        sealed = False
        hash_matches = None

    month = _month_label(snapshot["settlement_month"])
    names = {row["resident_id"]: row["display_name"] for row in snapshot.get("residents", [])}
    charges = snapshot["statement_charges_paise_by_resident"]
    # Snapshots sealed by an older version may not carry the precomputed sum.
    checksum_paise = snapshot.get("net_positions_sum_paise")
    if checksum_paise is None:
        checksum_paise = sum(snapshot.get("net_positions_paise_by_account", {}).values())
    note = f"{month} {apartment['name']}"

    # A draft's transfers are already live, so only a sealed one carries a status.
    sealed_transfers = snapshot.get("transfers", [])
    flags = _settled_flags(sealed_transfers, paid) if sealed else [None] * len(sealed_transfers)
    transfers: list[dict[str, Any]] = []
    for transfer, settled in zip(sealed_transfers, flags):
        receiver_upi = upi_ids.get(transfer["to_account"])
        transfers.append({
            **transfer,
            "settled": settled,
            "upi_id": receiver_upi,
            "upi_uri": upi_uri(upi_id=receiver_upi,
                               payee_name=transfer["to_display_name"],
                               amount_paise=transfer["amount_paise"],
                               note=note) if receiver_upi else None,
        })

    lines = [
        f"🏠 *{apartment['name']} — {month} settlement*",
        f"💰 *Household spend:* {format_inr(snapshot['statement_total_paise'])}",
        _RULE,
        "🧾 *Each resident's share:*",
    ]
    for resident_id in sorted(charges, key=lambda rid: names.get(rid, rid)):
        lines.append(f"• {names.get(resident_id, resident_id)}: "
                     f"{format_inr(charges[resident_id])}")
    lines += [_RULE, "👥 *Transfers to settle:*"]
    if not transfers:
        lines.append("Nothing to settle — every account is square.")
    for transfer in transfers:
        marker = {None: "🔴", True: "✅ [Settled]", False: "⏳ [Pending]"}[transfer["settled"]]
        lines.append(f"{marker} *{transfer['from_display_name']} pays "
                     f"{transfer['to_display_name']}:* {format_inr(transfer['amount_paise'])}")
        # No link beside a settled transfer: it would only invite a second payment.
        if transfer["upi_uri"] and not transfer["settled"]:
            lines.append(f"   UPI {transfer['upi_id']} — {transfer['upi_uri']}")
    lines.append(_RULE)
    lines.append("✅ Balances sum to ₹0.00" if checksum_paise == 0
                 else f"⚠️ Balances are off by {format_inr(checksum_paise)}")
    if sealed:
        lines.append(f"🔒 Sealed · SHA-256 {snapshot['snapshot_hash'][:12]}")
    else:
        lines.append("📝 Draft — figures can still change")
    lines.append("_Generated by Apartment Expense Manager_")

    return {
        "statement_id": statement_id,
        "settlement_month": snapshot["settlement_month"],
        "status": statement["status"],
        "text": "\n".join(lines),
        "share_url": f"https://wa.me/?text={quote(chr(10).join(lines))}",
        "resident_charges": [
            {"resident_id": rid, "display_name": names.get(rid, rid),
             "charged_paise": charges[rid]}
            for rid in sorted(charges, key=lambda rid: names.get(rid, rid))
        ],
        "transfers": transfers,
        "checksum": {
            "net_positions_sum_paise": checksum_paise,
            "balanced": checksum_paise == 0,
            "sealed": sealed,
            "snapshot_hash": snapshot["snapshot_hash"] if sealed else None,
            "hash_matches": hash_matches,
        },
    }
