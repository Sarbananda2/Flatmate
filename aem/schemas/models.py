"""Pydantic models for the HTTP API.

Conventions
-----------

* Money is always ``*_paise: int``. A request that sends a float for money is
  rejected rather than silently rounded.
* Dates are ``YYYY-MM-DD`` strings and periods are ``YYYY-MM`` strings, both
  pattern-validated, because they are compared as strings in SQL.
* Response models are deliberately permissive about extra keys: services return
  rich dictionaries and the API passes them through rather than maintaining two
  parallel definitions of every row.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DATE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"

AllocationMethod = Literal["equal", "weighted", "custom", "measured", "host_guest"]
PlannableMethod = Literal["equal", "weighted", "custom"]
PeriodRule = Literal["previous_month", "current_month", "transaction_date", "custom"]
PaymentKind = Literal["to_pool", "provider_advance", "refund", "adjustment"]
Recurrence = Literal["none", "monthly"]
StatementStatus = Literal["draft", "finalized_correction_window", "closed"]
ExpenseKind = Literal["general", "electricity"]
EngineAllocationMethod = Literal["usage", "equal", "custom"]
EscalationType = Literal["percent", "fixed", "none"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PassthroughModel(BaseModel):
    """A response wrapper that lets service dictionaries through unchanged."""

    model_config = ConfigDict(extra="allow")


class Paise(int):
    """Marker type for documentation only; validation uses `int` with bounds."""


def _money_field(description: str, *, ge: int = 0, default: Any = ...) -> Any:
    return Field(default, ge=ge, description=f"{description} Integer paise; never a float.")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    schema_version: int
    database_backend: str = "postgresql"
    database_path: str
    database_exists: bool
    apartment_count: int
    default_apartment_id: str | None
    settings: dict[str, Any]
    checks: dict[str, Any]


# --------------------------------------------------------------------------- #
# Apartment and residents
# --------------------------------------------------------------------------- #

class ApartmentCreate(ApiModel):
    apartment_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    currency_code: str = Field("INR", min_length=3, max_length=3)
    timezone: str = Field("Asia/Kolkata", min_length=1, max_length=64)


class ApartmentUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    currency_code: str | None = Field(None, min_length=3, max_length=3)
    timezone: str | None = Field(None, min_length=1, max_length=64)


class ResidentCreate(ApiModel):
    resident_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    active_from: str | None = Field(None, pattern=DATE_PATTERN)
    active_until: str | None = Field(None, pattern=DATE_PATTERN)
    is_active: bool = True
    email: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=2000)
    upi_id: str | None = Field(None, max_length=320)
    emergency_contact_name: str | None = Field(None, max_length=200)
    emergency_contact_phone: str | None = Field(None, max_length=50)
    emergency_contact_relation: str | None = Field(None, max_length=100)


class ResidentUpdate(ApiModel):
    display_name: str | None = Field(None, min_length=1, max_length=200)
    active_from: str | None = Field(None, pattern=DATE_PATTERN)
    active_until: str | None = Field(None, pattern=DATE_PATTERN)
    is_active: bool | None = None
    email: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=2000)
    upi_id: str | None = Field(None, max_length=320)
    emergency_contact_name: str | None = Field(None, max_length=200)
    emergency_contact_phone: str | None = Field(None, max_length=50)
    emergency_contact_relation: str | None = Field(None, max_length=100)
    reason: str | None = Field(None, max_length=2000)


class ResidentDeactivate(ApiModel):
    active_until: str | None = Field(None, pattern=DATE_PATTERN)
    reason: str | None = Field(None, max_length=2000)


# --------------------------------------------------------------------------- #
# Expense definitions
# --------------------------------------------------------------------------- #

class DefinitionCreate(ApiModel):
    definition_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=120)
    period_rule: PeriodRule
    default_allocation_method: AllocationMethod = "equal"
    recurrence: Recurrence = "monthly"
    default_amount_paise: int | None = _money_field("Suggested amount when generating.",
                                                    default=None)
    default_provider_name: str | None = Field(None, max_length=200)
    default_participants: list[str] | None = None
    default_weights: dict[str, int] | None = None
    day_of_month: int | None = Field(None, ge=1, le=28)
    notes: str | None = Field(None, max_length=2000)
    sort_order: int = 100


class DefinitionUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, min_length=1, max_length=120)
    period_rule: PeriodRule | None = None
    default_allocation_method: AllocationMethod | None = None
    recurrence: Recurrence | None = None
    is_active: bool | None = None
    default_amount_paise: int | None = _money_field("Suggested amount.", default=None)
    default_provider_name: str | None = Field(None, max_length=200)
    default_participants: list[str] | None = None
    default_weights: dict[str, int] | None = None
    day_of_month: int | None = Field(None, ge=1, le=28)
    notes: str | None = Field(None, max_length=2000)
    sort_order: int | None = None
    reason: str | None = Field(None, max_length=2000)


# --------------------------------------------------------------------------- #
# Allocation preview
# --------------------------------------------------------------------------- #

class AllocationPreviewRequest(ApiModel):
    amount_paise: int = _money_field("The amount to split.")
    method: PlannableMethod
    participants: list[str] | None = None
    weights: dict[str, int] | None = None
    shares_paise: dict[str, int] | None = None
    payer_resident_id: str | None = None


# --------------------------------------------------------------------------- #
# Expenses
# --------------------------------------------------------------------------- #

class ExpenseCreate(ApiModel):
    expense_id: str | None = Field(None, max_length=120,
                                   description="Omit to let the server generate one.")
    description: str = Field(min_length=1, max_length=500)
    amount_paise: int = _money_field("Total household cost of this expense.")
    expense_date: str = Field(pattern=DATE_PATTERN,
                              description="The transaction date.")
    expense_definition_id: str | None = None
    period_rule: PeriodRule | None = Field(
        None, description="Defaults to the definition's rule, else 'custom'.")
    service_period: str | None = Field(None, pattern=MONTH_PATTERN,
                                       description="The month the money was for.")
    settlement_period: str | None = Field(
        None, pattern=MONTH_PATTERN,
        description="The statement month that charges this expense.")
    due_date: str | None = Field(None, pattern=DATE_PATTERN)
    statement_id: str | None = None
    payer_resident_id: str | None = Field(
        None, description="The resident who funded this expense, if any. Not a second expense.")
    provider_name: str | None = Field(None, max_length=200)
    reference: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    allocation_method: AllocationMethod = "custom"
    allocations_paise: dict[str, int] | None = Field(
        None, description="Exact share per resident. Required for 'custom'.")
    participants: list[str] | None = None
    weights: dict[str, int] | None = None

    @field_validator("allocations_paise")
    @classmethod
    def _non_negative(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value and any(amount < 0 for amount in value.values()):
            raise ValueError("Allocation amounts cannot be negative.")
        return value


class ExpenseUpdate(ApiModel):
    description: str | None = Field(None, min_length=1, max_length=500)
    amount_paise: int | None = _money_field("New total.", default=None)
    expense_date: str | None = Field(None, pattern=DATE_PATTERN)
    service_period: str | None = Field(None, pattern=MONTH_PATTERN)
    settlement_period: str | None = Field(None, pattern=MONTH_PATTERN)
    due_date: str | None = Field(None, pattern=DATE_PATTERN)
    payer_resident_id: str | None = None
    provider_name: str | None = Field(None, max_length=200)
    reference: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    statement_id: str | None = None
    expense_definition_id: str | None = None
    allocation_method: PlannableMethod | None = None
    allocations_paise: dict[str, int] | None = None
    participants: list[str] | None = None
    weights: dict[str, int] | None = None
    reason: str | None = Field(
        None, max_length=2000,
        description="Required when the expense's statement is inside its correction window.")


# --------------------------------------------------------------------------- #
# Electricity
# --------------------------------------------------------------------------- #

class SmartPlugReadingInput(ApiModel):
    resident_id: str
    kwh: str | int = Field(
        description="kWh, up to three decimal places. Include any guest usage in the "
                    "host resident's reading.")
    device_label: str | None = Field(None, max_length=120)
    period_start: str | None = Field(None, pattern=DATE_PATTERN)
    period_end: str | None = Field(None, pattern=DATE_PATTERN)
    source_note: str | None = Field(None, max_length=500)


class BillComponentInput(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    amount_paise: int = _money_field("This charge as it appears on the utility bill.")
    allocation_method: EngineAllocationMethod = "usage"
    allocations_paise: dict[str, int] | None = Field(
        None, description="For 'custom': the exact amount per resident.")
    weights: dict[str, Any] | None = Field(
        None, description="For 'custom': relative weights per resident.")


class ElectricityPreviewRequest(ApiModel):
    total_amount_paise: int = _money_field("The actual utility bill total. Authoritative.")
    smart_plug_readings: list[SmartPlugReadingInput] = Field(default_factory=list)
    components: list[BillComponentInput] | None = None
    payer_resident_id: str | None = None
    expense_date: str | None = Field(None, pattern=DATE_PATTERN)


class ElectricityCreate(ApiModel):
    expense_id: str | None = Field(None, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    expense_date: str = Field(pattern=DATE_PATTERN)
    total_amount_paise: int = _money_field("The actual utility bill total. Authoritative.")
    smart_plug_readings: list[SmartPlugReadingInput] = Field(default_factory=list)
    components: list[BillComponentInput] | None = None
    payer_resident_id: str | None = None
    provider_name: str = Field("Electricity provider", max_length=200)
    expense_definition_id: str | None = None
    statement_id: str | None = None
    period_rule: PeriodRule | None = None
    service_period: str | None = Field(None, pattern=MONTH_PATTERN)
    settlement_period: str | None = Field(None, pattern=MONTH_PATTERN)
    bill_period_start: str | None = Field(None, pattern=DATE_PATTERN)
    bill_period_end: str | None = Field(None, pattern=DATE_PATTERN)
    bill_number: str | None = Field(None, max_length=120)
    due_date: str | None = Field(None, pattern=DATE_PATTERN)
    notes: str | None = Field(None, max_length=2000)


class TapoSyncRequest(ApiModel):
    period_start: str = Field(pattern=DATE_PATTERN)
    period_end: str = Field(pattern=DATE_PATTERN)


class ElectricityRecalculate(ElectricityCreate):
    reason: str = Field(min_length=1, max_length=2000,
                        description="A saved calculation is never changed silently.")
    description: str | None = Field(None, min_length=1, max_length=500)
    expense_date: str | None = Field(None, pattern=DATE_PATTERN)
    total_amount_paise: int | None = _money_field("New bill total.", default=None)
    smart_plug_readings: list[SmartPlugReadingInput] | None = None


# --------------------------------------------------------------------------- #
# Payments, credits, adjustments
# --------------------------------------------------------------------------- #

class PaymentCreate(ApiModel):
    payment_id: str | None = Field(None, max_length=120)
    payer_resident_id: str
    amount_paise: int = _money_field("The amount paid.", ge=1)
    payment_date: str = Field(pattern=DATE_PATTERN)
    payment_kind: PaymentKind = "to_pool"
    payee_or_provider: str | None = Field(None, max_length=200)
    reference: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=2000)
    expense_id: str | None = Field(
        None, description="For a provider advance: the expense this payment funds. Linking "
                          "it stops the expense's payer field being credited as well.")


class PeerSettlementCreate(ApiModel):
    settlement_id: str | None = Field(None, max_length=100)
    from_resident_id: str = Field(min_length=1, max_length=64)
    to_resident_id: str = Field(min_length=1, max_length=64)
    amount_paise: int = _money_field("The amount one resident paid the other.", ge=1)
    payment_date: str = Field(pattern=DATE_PATTERN)
    reference: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=2000)


class PaymentReverse(ApiModel):
    reason: str = Field(min_length=1, max_length=2000)
    reversal_payment_id: str | None = Field(None, max_length=120)
    reversal_date: str | None = Field(None, pattern=DATE_PATTERN)


class CreditApplyRequest(ApiModel):
    resident_id: str


class AdjustmentCreate(ApiModel):
    resident_id: str
    amount_paise: int = Field(
        description="Signed. Positive credits the resident; negative debits them.")
    reason: str = Field(min_length=1, max_length=2000)
    statement_id: str | None = None
    effective_date: str | None = Field(None, pattern=DATE_PATTERN)
    adjustment_id: str | None = Field(None, max_length=120)

    @field_validator("amount_paise")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("An adjustment cannot be zero.")
        return value


class AdjustmentReverse(ApiModel):
    reason: str = Field(min_length=1, max_length=2000)


class OpeningBalanceCreate(ApiModel):
    resident_id: str
    amount_paise: int = Field(
        description="Signed. Positive is owed to the resident; negative is owed by them. "
                    "Integer paise; never a float.")
    as_of_date: str = Field(pattern=DATE_PATTERN)
    note: str | None = Field(None, max_length=2000)

    @field_validator("amount_paise")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("An opening balance cannot be zero. Clear it instead.")
        return value


class OpeningBalanceItem(PassthroughModel):
    """One resident's opening balance, with how much of a debt has been paid off."""

    opening_balance_id: str
    resident_id: str
    amount_paise: int
    direction: Literal["owed_to_resident", "owed_by_resident"]
    as_of_date: str
    note: str | None = None
    settled_paise: int
    outstanding_paise: int


# --------------------------------------------------------------------------- #
# Rent
# --------------------------------------------------------------------------- #

class RentLeaseConfigSave(ApiModel):
    landlord_name: str = Field(min_length=1, max_length=200)
    landlord_phone: str | None = Field(None, max_length=40)
    landlord_email: str | None = Field(None, max_length=254)
    landlord_pan: str | None = Field(
        None, max_length=16, description="Printed on HRA receipts. Ten characters.")
    landlord_bank_account: str | None = Field(None, max_length=32)
    landlord_bank_ifsc: str | None = Field(None, max_length=16)
    landlord_upi_id: str | None = Field(None, max_length=320)
    monthly_rent_paise: int = _money_field("The whole monthly rent on the lease.", ge=1)
    due_day_of_month: int = Field(5, ge=1, le=28)
    requires_landlord_receipt: bool = Field(
        False, description="Whether the landlord issues a receipt. When true, a paid month "
                           "shows its receipt as pending until one is attached.")
    expense_definition_id: str | None = Field(
        None, max_length=120,
        description="The Housing definition rent is recorded under. Omit to use the "
                    "household's Rent definition, or to have one created.")
    # The contract term. A field left out keeps what the lease already has.
    lease_start_date: str | None = Field(None, pattern=DATE_PATTERN)
    lease_end_date: str | None = Field(
        None, pattern=DATE_PATTERN,
        description="Omit to have it computed: start date plus the duration, less a day.")
    duration_months: int = Field(11, ge=1, le=60)
    notice_period_days: int = Field(30, ge=0, le=365)
    lock_in_months: int = Field(6, ge=0, le=60)
    security_deposit_paise: int | None = _money_field(
        "The deposit held by the landlord.", default=None)
    escalation_type: EscalationType = "percent"
    escalation_percent: float = Field(5.0, ge=0, le=100)
    renewal_rent_paise: int | None = _money_field(
        "The agreed rent for the next term, when the escalation is a fixed amount.",
        ge=1, default=None)


class RentLeaseRenewRequest(ApiModel):
    new_start_date: str | None = Field(
        None, pattern=DATE_PATTERN, description="Omit to start the day after the current term.")
    duration_months: int | None = Field(None, ge=1, le=60)
    new_monthly_rent_paise: int | None = _money_field(
        "Omit to charge what the escalation clause projects.", ge=1, default=None)
    escalation_type: EscalationType | None = None
    escalation_percent: float | None = Field(None, ge=0, le=100)


class RentLeaseVacateRequest(ApiModel):
    notice_served_date: str = Field(pattern=DATE_PATTERN)
    target_handover_date: str = Field(pattern=DATE_PATTERN)
    notes: str | None = Field(None, max_length=1000)


class RentGenerateRequest(ApiModel):
    excluded_resident_ids: list[str] = Field(
        default_factory=list,
        description="Active residents who do not take part this month. Everyone else is "
                    "charged a full equal share; nothing is prorated.")


class RentPaymentCreate(ApiModel):
    resident_id: str = Field(min_length=1, max_length=64)
    amount_paise: int = _money_field(
        "What the resident paid. Settles their rent share for the month first; any "
        "excess goes to their other charges, oldest first.", ge=1)
    payment_date: str = Field(pattern=DATE_PATTERN)
    payment_mode: Literal["upi", "bank_transfer", "cash", "other"] | None = None
    reference: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=2000)


class LandlordReceiptUpload(ApiModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: Literal["application/pdf", "image/jpeg", "image/png", "image/webp"]
    content_base64: str = Field(min_length=1, description="The file, base64-encoded. 5 MB limit.")


class RentDisburseRequest(ApiModel):
    disbursement_mode: Literal["pool", "resident_advance"]
    funding_resident_id: str | None = Field(
        None, description="For a resident advance: who paid the landlord.")
    reference: str = Field(min_length=1, max_length=200,
                           description="Bank UTR or UPI transaction ID.")
    disbursed_date: str = Field(pattern=DATE_PATTERN)
    landlord_receipt: LandlordReceiptUpload | None = Field(
        None, description="Optional. It can be attached later, whenever the landlord sends it.")


# --------------------------------------------------------------------------- #
# Statements
# --------------------------------------------------------------------------- #

class StatementCreate(ApiModel):
    settlement_month: str = Field(pattern=MONTH_PATTERN)
    statement_id: str | None = Field(None, max_length=120)
    correction_window_hours: int = Field(72, ge=0, le=8760)
    notes: str | None = Field(None, max_length=2000)


class StatementAttachRequest(ApiModel):
    expense_ids: list[str] | None = Field(
        None, description="Omit to attach every eligible unattached expense for this month.")


class StatementFinalizeRequest(ApiModel):
    finalized_by: str = Field(min_length=1, max_length=120)
    confirm: bool = Field(
        description="Must be true. Finalizing takes a snapshot and starts the correction "
                    "window; it is not reversible.")
    correction_window_hours: int | None = Field(None, ge=0, le=8760)
    require_clean: bool = Field(
        True, description="Refuse to finalize while accounting errors remain.")

    @field_validator("confirm")
    @classmethod
    def _must_confirm(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Finalization requires explicit confirmation.")
        return value


class StatementReviseRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=2000)
    change_note: str | None = Field(None, max_length=2000)


class StatementCloseRequest(ApiModel):
    confirm: bool
    force: bool = Field(
        False, description="Close before the correction window has elapsed.")

    @field_validator("confirm")
    @classmethod
    def _must_confirm(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Closing a statement requires explicit confirmation.")
        return value


# --------------------------------------------------------------------------- #
# Guided setup
# --------------------------------------------------------------------------- #

class SetupApartmentRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200,
                      description="The identifier is derived from this; the operator "
                                  "never types one.")
    currency_code: str = Field("INR", min_length=3, max_length=3)
    timezone: str = Field("Asia/Kolkata", min_length=1, max_length=64)
    apartment_id: str | None = Field(None, max_length=64,
                                     description="Override the derived identifier.")


class SetupResidentsRequest(ApiModel):
    names: list[str] = Field(
        min_length=1,
        description="Everyone at once. Blank entries are ignored, so spare input "
                    "rows can be left empty.")


class SetupCategoryItem(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field("Household", min_length=1, max_length=120)
    period_rule: PeriodRule = "current_month"
    default_allocation_method: AllocationMethod = "equal"
    recurrence: Recurrence = "monthly"
    default_amount_paise: int | None = _money_field(
        "Typical amount, used as the suggestion when generating.", default=None)
    definition_id: str | None = Field(None, max_length=64)
    sort_order: int | None = None


class SetupCategoriesRequest(ApiModel):
    items: list[SetupCategoryItem] = Field(min_length=1)


class SetupFinishRequest(ApiModel):
    create_statement_for: str | None = Field(
        None, pattern=MONTH_PATTERN,
        description="Open the first draft statement for this month while finishing.")


# --------------------------------------------------------------------------- #
# Recurring generation
# --------------------------------------------------------------------------- #

class RecurringLine(ApiModel):
    expense_definition_id: str
    amount_paise: int | None = _money_field("The amount for this month.", default=None)
    description: str | None = Field(None, max_length=500)
    expense_date: str | None = Field(None, pattern=DATE_PATTERN)
    payer_resident_id: str | None = None
    provider_name: str | None = Field(None, max_length=200)
    allocation_method: PlannableMethod | None = None
    participants: list[str] | None = None
    weights: dict[str, int] | None = None
    allocations_paise: dict[str, int] | None = None
    reference: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    expense_id: str | None = Field(None, max_length=120)


class RecurringGenerateRequest(ApiModel):
    settlement_period: str = Field(pattern=MONTH_PATTERN)
    lines: list[RecurringLine] = Field(min_length=1)
    statement_id: str | None = None
    attach_to_statement: bool = True
    confirm: bool = Field(description="Must be true. Generation creates records.")

    @field_validator("confirm")
    @classmethod
    def _must_confirm(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Generating recurring expenses requires explicit confirmation.")
        return value


__all__ = [
    "AdjustmentCreate", "AdjustmentReverse", "AllocationMethod", "OpeningBalanceCreate", "OpeningBalanceItem",
    "AllocationPreviewRequest", "ApartmentCreate", "ApartmentUpdate", "ApiModel",
    "BillComponentInput", "CreditApplyRequest", "DATE_PATTERN", "DefinitionCreate",
    "DefinitionUpdate", "ElectricityCreate", "ElectricityPreviewRequest",
    "ElectricityRecalculate", "EngineAllocationMethod", "ErrorBody", "ErrorResponse",
    "ExpenseCreate", "ExpenseKind", "ExpenseUpdate", "HealthResponse", "MONTH_PATTERN",
    "PassthroughModel", "PaymentCreate", "PaymentKind", "PaymentReverse", "PeerSettlementCreate", "PeriodRule",
    "PlannableMethod", "Recurrence", "RecurringGenerateRequest", "RecurringLine",
    "LandlordReceiptUpload", "RentDisburseRequest", "RentGenerateRequest",
    "RentLeaseConfigSave", "RentLeaseRenewRequest", "RentLeaseVacateRequest",
    "RentPaymentCreate",
    "ResidentCreate", "ResidentDeactivate", "ResidentUpdate", "SetupApartmentRequest",
    "SetupCategoriesRequest", "SetupCategoryItem", "SetupFinishRequest",
    "SetupResidentsRequest", "SmartPlugReadingInput", "TapoSyncRequest",
    "StatementAttachRequest", "StatementCloseRequest", "StatementCreate",
    "StatementFinalizeRequest", "StatementReviseRequest", "StatementStatus",
]
