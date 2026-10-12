"""Read smart-plug energy from TP-Link Tapo plugs on the local network.

This fetches; it does not record. A reading only enters the accounts as part
of an electricity bill, where it is a weight for sharing that bill's usage
charge and is saved together with the bill or not at all. So a sync returns
each resident's measured energy for a period, for the operator to review and
carry into the bill form. Nothing is written here, and a plug that cannot be
reached is reported beside the ones that could, never filled in with a guess.

Energy stays in whole watt-hours end to end, as the plug reports it.

The connector needs the optional ``tapo`` package (``pip install tapo``) and
three settings: ``AEM_TAPO_USERNAME``, ``AEM_TAPO_PASSWORD``, and
``AEM_TAPO_PLUGS`` mapping each resident to a plug's address.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from ..config import Settings, TapoPlug
from ..db import connect
from ..errors import AemError, ValidationError
from ..money import wh_to_kwh_string
from ..periods import parse_iso_date
from .common import get_apartment, list_residents

MAX_PERIOD_DAYS = 100

# (ip_address, first_day, last_day) -> watt-hours for each day the plug reported.
DailyEnergyFetcher = Callable[[str, date, date], Awaitable[dict[date, int]]]


class TapoUnavailableError(AemError):
    code = "tapo_unavailable"
    http_status = 503


def _quarter_starts(first: date, last: date) -> list[date]:
    """The first day of every calendar quarter that overlaps the period."""
    starts: list[date] = []
    cursor = date(first.year, 3 * ((first.month - 1) // 3) + 1, 1)
    while cursor <= last:
        starts.append(cursor)
        month = cursor.month + 3
        cursor = date(cursor.year + (month > 12), month - 12 if month > 12 else month, 1)
    return starts


def _tapo_fetcher(username: str, password: str) -> DailyEnergyFetcher:
    """The real connector. Imported lazily so the app runs without the package."""
    try:
        from tapo import ApiClient
        from tapo.requests import EnergyDataInterval
    except ImportError:
        raise TapoUnavailableError(
            "The Tapo connector is not installed. Install it with 'pip install tapo'.",
            code="tapo_not_installed") from None

    client = ApiClient(username, password)

    async def fetch(ip_address: str, first: date, last: date) -> dict[date, int]:
        device = await client.p110(ip_address)
        daily: dict[date, int] = {}
        # The plug serves daily figures one calendar quarter at a time.
        for quarter_start in _quarter_starts(first, last):
            result = await device.get_energy_data(EnergyDataInterval.Daily, quarter_start)
            local_zone = result.local_time.tzinfo
            for entry in result.entries:
                if entry.energy is None:
                    continue
                moment = entry.start_date_time
                if local_zone is not None and moment.tzinfo is not None:
                    moment = moment.astimezone(local_zone)
                day = moment.date()
                if first <= day <= last:
                    daily[day] = int(entry.energy)
        return daily

    return fetch


async def _gather(plugs: tuple[TapoPlug, ...], fetch: DailyEnergyFetcher, first: date,
                  last: date) -> list[dict[date, int] | BaseException]:
    return await asyncio.gather(
        *(fetch(plug.ip_address, first, last) for plug in plugs), return_exceptions=True)


def sync_readings(db_path, *, apartment_id: str, settings: Settings, period_start: str,
                  period_end: str, fetch: DailyEnergyFetcher | None = None) -> dict[str, Any]:
    """Fetch each configured plug's energy for the period. Writes nothing."""
    first = parse_iso_date(period_start, "period_start")
    last = parse_iso_date(period_end, "period_end")
    if last < first:
        raise ValidationError("period_end cannot be before period_start.",
                              details={"field": "period_end"})
    days_expected = (last - first).days + 1
    if days_expected > MAX_PERIOD_DAYS:
        raise ValidationError(
            f"A billing period can be at most {MAX_PERIOD_DAYS} days.",
            details={"field": "period_end", "days": days_expected})
    if last >= date.today():
        raise ValidationError(
            "The period must have ended: today's energy is still being measured.",
            details={"field": "period_end"})

    plugs = settings.tapo_plugs
    if fetch is None:
        if not (settings.tapo_username and settings.tapo_password and plugs):
            raise TapoUnavailableError(
                "The Tapo connector is not configured. Set AEM_TAPO_USERNAME, "
                "AEM_TAPO_PASSWORD, and AEM_TAPO_PLUGS.", code="tapo_not_configured")
        fetch = _tapo_fetcher(settings.tapo_username, settings.tapo_password)

    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        names = {row["resident_id"]: row["display_name"]
                 for row in list_residents(conn, apartment_id)}
    unknown = sorted({plug.resident_id for plug in plugs} - set(names))
    if unknown:
        raise ValidationError(
            "AEM_TAPO_PLUGS names residents that are not in this apartment.",
            code="tapo_unknown_resident", details={"resident_ids": unknown})

    results = asyncio.run(_gather(plugs, fetch, first, last))

    readings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for plug, result in zip(plugs, results):
        if isinstance(result, BaseException):
            errors.append({"resident_id": plug.resident_id, "ip_address": plug.ip_address,
                           "device_label": plug.label, "message": str(result) or
                           type(result).__name__})
            continue
        missing = [(first + timedelta(days=offset)).isoformat()
                   for offset in range(days_expected)
                   if first + timedelta(days=offset) not in result]
        energy_wh = sum(result.values())
        readings.append({
            "resident_id": plug.resident_id,
            "display_name": names[plug.resident_id],
            "device_label": plug.label,
            "ip_address": plug.ip_address,
            "energy_wh": energy_wh,
            "kwh": wh_to_kwh_string(energy_wh),
            "days_reported": days_expected - len(missing),
            "days_expected": days_expected,
            # A plug that was unplugged or offline reports no figure for those
            # days. The total is then a floor, and the operator must know.
            "missing_days": missing,
            "complete": not missing,
        })

    total_wh = sum(reading["energy_wh"] for reading in readings)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "readings": readings,
        "errors": errors,
        "total_energy_wh": total_wh,
        "total_kwh": wh_to_kwh_string(total_wh),
        "complete": not errors and all(reading["complete"] for reading in readings),
        "saved": False,
    }
