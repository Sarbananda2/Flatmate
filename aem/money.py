"""Integer-paise money helpers.

Money is always an `int` number of paise. Decimal is used only to parse
operator input (rupees) and to carry kWh readings; it never produces a
monetary result without an exact integer conversion.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Sequence

from .errors import ValidationError

PAISE_PER_RUPEE = 100


def ensure_paise(value: Any, label: str, *, allow_negative: bool = False,
                 allow_zero: bool = True) -> int:
    """Validate that `value` is an integer number of paise.

    `bool` is rejected explicitly: `True` is an `int` in Python and silently
    accepting it would store 1 paisa.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{label} must be an integer number of paise.",
                              details={"field": label, "value": repr(value)})
    if not allow_negative and value < 0:
        raise ValidationError(f"{label} cannot be negative.",
                              details={"field": label, "value": value})
    if not allow_zero and value == 0:
        raise ValidationError(f"{label} must not be zero.", details={"field": label})
    return value


def gst_paise(base_paise: int, rate_percent: float) -> int:
    """GST at ``rate_percent`` on an amount before GST, in whole paise, rounded
    half up."""
    return int((Decimal(base_paise) * Decimal(str(rate_percent)) / Decimal(100))
               .quantize(Decimal(1), ROUND_HALF_UP))


def gst_rate(value: Any, label: str = "gst_rate_percent") -> float:
    """A GST rate: from 0 to 100 percent, to two decimal places at most."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a number.", details={"field": label})
    rate = Decimal(str(value))
    if not rate.is_finite() or rate < 0 or rate > 100 or -rate.as_tuple().exponent > 2:
        raise ValidationError(
            "The GST rate must be from 0 to 100 percent, to two decimal places at most.",
            code="invalid_gst_rate", details={"field": label})
    return float(rate)


def apportion_paise(total: int, weights: Sequence[int]) -> list[int]:
    """`total` split in proportion to `weights`, in whole paise that add up to
    it: each its whole part, and the paise left over one each to those with the
    largest remainders, the first of equals first."""
    whole = sum(weights)
    if not whole:
        return [0] * len(weights)
    parts = [total * weight // whole for weight in weights]
    order = sorted(range(len(weights)), key=lambda index: (-(total * weights[index] % whole), index))
    for index in order[:total - sum(parts)]:
        parts[index] += 1
    return parts


def rupees_to_paise(value: Any, label: str = "amount") -> int:
    """Convert a rupee string/Decimal with at most two decimals to paise."""
    try:
        amount = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        raise ValidationError(f"{label} must be a valid rupee amount.",
                              details={"field": label, "value": repr(value)}) from None
    if not amount.is_finite():
        raise ValidationError(f"{label} must be a finite rupee amount.",
                              details={"field": label})
    paise = amount * PAISE_PER_RUPEE
    if paise != paise.to_integral_value():
        raise ValidationError(f"{label} supports at most two decimal places.",
                              details={"field": label, "value": str(value)})
    return int(paise)


def paise_to_rupee_string(paise: int) -> str:
    """Render paise as a plain rupee string, e.g. -1234 -> '-12.34'."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(int(paise)), PAISE_PER_RUPEE)
    return f"{sign}{whole}.{frac:02d}"


def format_inr(paise: int) -> str:
    """Render paise as an INR string with Indian digit grouping."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(int(paise)), PAISE_PER_RUPEE)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{sign}₹{digits}.{frac:02d}"


_ONES = ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _words_below_thousand(value: int) -> str:
    parts: list[str] = []
    if value >= 100:
        parts.append(f"{_ONES[value // 100]} Hundred")
        value %= 100
    if value >= 20:
        parts.append(_TENS[value // 10] + (f"-{_ONES[value % 10]}" if value % 10 else ""))
    elif value or not parts:
        parts.append(_ONES[value])
    return " ".join(parts)


def _integer_in_words(value: int) -> str:
    """Whole number in words with Indian grouping: thousand, lakh, crore."""
    if value == 0:
        return _ONES[0]
    crore, value = divmod(value, 10_000_000)
    lakh, value = divmod(value, 100_000)
    thousand, value = divmod(value, 1_000)
    parts: list[str] = []
    if crore:
        parts.append(f"{_integer_in_words(crore)} Crore")
    if lakh:
        parts.append(f"{_words_below_thousand(lakh)} Lakh")
    if thousand:
        parts.append(f"{_words_below_thousand(thousand)} Thousand")
    if value:
        parts.append(_words_below_thousand(value))
    return " ".join(parts)


def paise_in_words(paise: int) -> str:
    """Render a non-negative amount the way a receipt states it.

    ``1500000`` -> ``'Fifteen Thousand Rupees Only'``;
    ``1500050`` -> ``'Fifteen Thousand Rupees and Fifty Paise Only'``.
    """
    whole, frac = divmod(ensure_paise(paise, "amount_paise"), PAISE_PER_RUPEE)
    words = f"{_integer_in_words(whole)} {'Rupee' if whole == 1 else 'Rupees'}"
    if frac:
        words += f" and {_integer_in_words(frac)} {'Paisa' if frac == 1 else 'Paise'}"
    return f"{words} Only"


def kwh_to_wh(value: Any, label: str = "kwh") -> int:
    """Convert a kWh value with at most three decimals to whole watt-hours."""
    try:
        kwh = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        raise ValidationError(f"{label} must be a valid kWh value.",
                              details={"field": label, "value": repr(value)}) from None
    if not kwh.is_finite() or kwh < 0:
        raise ValidationError(f"{label} must be a non-negative kWh value.",
                              details={"field": label, "value": str(value)})
    wh = kwh * 1000
    if wh != wh.to_integral_value():
        raise ValidationError(
            f"{label} supports at most three decimal places in kWh (whole Wh).",
            details={"field": label, "value": str(value)})
    return int(wh)


def wh_to_kwh_string(energy_wh: int) -> str:
    """Render whole watt-hours as a three-decimal kWh string."""
    return f"{Decimal(int(energy_wh)) / Decimal(1000):.3f}"
