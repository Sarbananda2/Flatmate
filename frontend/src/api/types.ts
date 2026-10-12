/**
 * Shapes returned by the backend.
 *
 * Every money field is an integer number of paise and is named `*_paise`.
 * Nothing in the frontend converts money to a float: amounts are formatted for
 * display from the integer, and operator input is parsed back to an integer
 * before it is sent.
 */

export type PeriodRule = 'previous_month' | 'current_month' | 'transaction_date' | 'custom'
export type AllocationMethod = 'equal' | 'weighted' | 'custom' | 'measured' | 'host_guest'
export type PlannableMethod = 'equal' | 'weighted' | 'custom'
export type PaymentKind = 'to_pool' | 'provider_advance' | 'refund' | 'adjustment'
export type PaymentStatus = 'active' | 'reversed' | 'reversal'
export type StatementStatus = 'draft' | 'finalized_correction_window' | 'closed'
export type Recurrence = 'none' | 'monthly'
export type ExpenseKind = 'general' | 'electricity'
export type Severity = 'error' | 'warning' | 'info'

export interface ApiErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
}

export interface Paged<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface Apartment {
  apartment_id: string
  name: string
  currency_code: string
  timezone: string
  created_at: string
}

export interface Resident {
  resident_id: string
  apartment_id: string
  display_name: string
  active_from: string | null
  active_until: string | null
  is_active: number | boolean
  email: string | null
  phone: string | null
  notes: string | null
  upi_id: string | null
  /** Who to call for this resident in an emergency. All optional. */
  emergency_contact_name: string | null
  emergency_contact_phone: string | null
  emergency_contact_relation: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ExpenseDefinition {
  expense_definition_id: string
  apartment_id: string
  name: string
  category: string
  period_rule: PeriodRule
  default_allocation_method: AllocationMethod
  is_recurring: boolean
  is_active: boolean
  recurrence: Recurrence
  default_amount_paise: number | null
  default_provider_name: string | null
  default_participants: string[] | null
  default_weights: Record<string, number> | null
  day_of_month: number | null
  notes: string | null
  sort_order: number
}

export interface AllocationLine {
  resident_id: string
  display_name: string
  amount_paise: number
  is_participant: boolean
}

export interface AllocationPreview {
  amount_paise: number
  method: PlannableMethod
  shares_paise: Record<string, number>
  participants: string[]
  total_paise: number
  basis: Record<string, unknown>
  warnings: string[]
  lines: AllocationLine[]
}

export interface ExpenseAllocation {
  allocation_id: string
  resident_id: string
  display_name: string
  amount_paise: number
  settled_paise: number
  outstanding_paise: number
  allocation_basis: Record<string, unknown> | null
  component_id: string | null
}

export interface ExpenseRow {
  expense_id: string
  description: string
  amount_paise: number
  expense_date: string
  service_period: string | null
  settlement_period: string | null
  period_rule: PeriodRule | null
  due_date: string | null
  expense_kind: ExpenseKind
  allocation_method: AllocationMethod | null
  category: string | null
  definition_name: string | null
  provider_name: string | null
  reference: string | null
  notes: string | null
  payer_resident_id: string | null
  payer_display_name: string | null
  statement_id: string | null
  statement_month: string | null
  statement_status: StatementStatus | null
  expense_definition_id: string | null
  generated_from_recurrence: number | boolean
  allocation_count: number
  allocated_paise: number
  linked_advance_paise: number
}

export interface ExpenseDetail extends ExpenseRow {
  components: Array<{
    component_id: string
    name: string
    amount_paise: number
    allocation_method: string
  }>
  allocations: ExpenseAllocation[]
  linked_payments: PaymentRow[]
  readings: SmartPlugReading[]
  electricity_bill: ElectricityBillRecord | null
  statement: { statement_id: string; settlement_month: string; status: StatementStatus } | null
}

export interface SmartPlugReading {
  reading_id: string
  resident_id: string
  expense_id: string
  device_label: string
  period_start: string
  period_end: string
  energy_wh: number
  kwh?: string
  source_note: string | null
}

export interface ElectricityBillRecord {
  electricity_bill_id: string
  expense_id: string
  bill_period_start: string
  bill_period_end: string
  settlement_period: string
  bill_number: string | null
  provider_name: string | null
  total_amount_paise: number
  notes: string | null
  created_at: string
}

export interface ElectricityComponentResult {
  component_id: string
  name: string
  amount_paise: number
  allocation_method: string
  allocations: Array<{ resident_id: string; amount_paise: number }>
}

export interface ElectricityPreview {
  total_amount_paise: number
  components: ElectricityComponentResult[]
  allocations: Array<{ resident_id: string; amount_paise: number }>
  breakdown: Record<string, unknown>
  warnings: string[]
  residents: Array<{
    resident_id: string
    display_name: string
    energy_wh: number
    kwh: string
    usage_share_percent: number
    amount_paise: number
    is_payer: boolean
  }>
  total_energy_wh: number
  total_kwh: string
  reconciles: boolean
  rounding_rule: string
}

export interface ElectricityBillSummary {
  expense_id: string
  description: string
  amount_paise: number
  expense_date: string
  service_period: string | null
  settlement_period: string | null
  payer_resident_id: string | null
  payer_display_name: string | null
  statement_id: string | null
  statement_status: StatementStatus | null
  settlement_month: string | null
  provider_name: string | null
  bill_period_start: string | null
  bill_period_end: string | null
  bill_number: string | null
  total_energy_wh: number
  total_kwh: string
  component_count: number
}

export interface SavedElectricityBill {
  expense: Record<string, unknown> & { expense_id: string; amount_paise: number }
  components: Array<{ component_id: string; name: string; amount_paise: number; allocation_method: string }>
  readings: SmartPlugReading[]
  allocations: Array<ExpenseAllocation & { component_name: string | null }>
  bill: ElectricityBillRecord | null
  calculation: Record<string, unknown> | null
  resident_totals_paise: Record<string, number>
  reconciles: boolean
}

export interface PaymentRow {
  payment_id: string
  payer_resident_id: string
  payer_display_name: string | null
  amount_paise: number
  payment_date: string
  payment_kind: PaymentKind
  payee_or_provider: string | null
  reference: string | null
  note: string | null
  status: PaymentStatus
  expense_id: string | null
  reverses_payment_id: string | null
  applied_paise: number
  unapplied_paise: number
  application_count: number
  linked_expense_description: string | null
}

export interface PaymentApplicationRow {
  payment_application_id: string
  allocation_id: string
  amount_paise: number
  resident_id: string
  charge_amount_paise: number
  expense_id: string
  expense_description: string
  settlement_period: string
  expense_date: string
}

export interface PaymentDetail extends PaymentRow {
  applications: PaymentApplicationRow[]
  linked_expense: { expense_id: string; description: string; amount_paise: number; settlement_period: string } | null
  reversal: { payment_id: string; payment_date: string; note: string | null } | null
}

export interface CreditRow {
  payment_id: string
  payer_resident_id: string
  display_name: string
  payment_date: string
  amount_paise: number
  applied_paise: number
  unapplied_paise: number
}

export interface ProviderAdvanceRow extends PaymentRow {
  expense_description: string | null
  expense_amount_paise: number | null
  expense_payer_resident_id: string | null
  expense_settlement_period: string | null
  is_linked: boolean
}

export interface AdjustmentRow {
  adjustment_id: string
  statement_id: string | null
  resident_id: string
  display_name: string
  amount_paise: number
  reason: string
  created_at: string
  created_by: string
  effective_date: string | null
  reverses_adjustment_id: string | null
  reversed_by: string | null
  settlement_month: string | null
  statement_status: StatementStatus | null
}

export interface TapoSyncResult {
  period_start: string
  period_end: string
  readings: Array<{
    resident_id: string
    display_name: string
    device_label: string
    energy_wh: number
    kwh: string
    days_reported: number
    days_expected: number
    missing_days: string[]
    complete: boolean
  }>
  errors: Array<{ resident_id: string; ip_address: string; device_label: string; message: string }>
  total_energy_wh: number
  total_kwh: string
  complete: boolean
}

export interface WhatsAppSummary {
  statement_id: string
  text: string
  share_url: string
  /** `settled` is null on a draft, whose transfers are already live. */
  transfers: (TransferSuggestion & { settled: boolean | null })[]
  checksum: { balanced: boolean; sealed: boolean; snapshot_hash: string | null }
}

export interface ValidationIssue {
  code: string
  severity: Severity
  message: string
  entity_type: string
  entity_id: string
  details: Record<string, unknown>
  suggested_action: string | null
}

export interface ReconciliationAccount {
  account_id: string
  account_type: 'resident' | 'pool'
  display_name: string
  is_active: boolean
  net_position_paise: number
  allocated_charges_paise?: number
  settled_paise?: number
  outstanding_charges_paise?: number
  unapplied_credit_paise?: number
  expense_funding_paise?: number
  provider_advances_paise?: number
  funding_credits_paise?: number
  pool_payments_paise?: number
  refunds_paise?: number
  adjustments_paise?: number
  legacy_payment_adjustments_paise?: number
  expenses_incurred_paise?: number
  cash_received_paise?: number
  cash_paid_out_paise?: number
  funded_by_residents_paise?: number
}

export interface TransferSuggestion {
  from_account: string
  to_account: string
  amount_paise: number
  from_display_name: string
  to_display_name: string
}

export interface Reconciliation {
  apartment_id: string
  as_of_date: string | null
  pool_account_id: string
  net_positions_paise_by_account: Record<string, number>
  net_positions_sum_paise: number
  accounts: ReconciliationAccount[]
  transfers: TransferSuggestion[]
  outstanding_charges_paise_by_resident: Record<string, number>
  unapplied_credits_paise_by_resident: Record<string, number>
  household_expenses_paise_by_settlement_period: Record<string, number>
  totals: {
    household_expenses_paise: number
    allocated_charges_paise: number
    outstanding_charges_paise: number
    unapplied_credits_paise: number
    provider_advances_paise: number
    expense_funding_paise: number
    pool_payments_paise: number
    adjustments_paise: number
    pool_net_position_paise: number
  }
  entry_count: number
  validation: {
    issues: ValidationIssue[]
    error_count: number
    warning_count: number
    is_reconcilable: boolean
  }
  notes: string[]
}

export interface LedgerEntry {
  account_id: string
  amount_paise: number
  kind: string
  source_type: string
  source_id: string
  date: string | null
  description: string
  settlement_period: string | null
  counter_account_id: string | null
}

export interface StatementRow {
  statement_id: string
  apartment_id: string
  settlement_month: string
  status: StatementStatus
  created_at: string
  finalized_at: string | null
  correction_deadline: string | null
  closed_at: string | null
  finalized_by: string | null
  correction_window_hours: number
  notes: string | null
  has_snapshot: boolean
  expense_count: number
  statement_total_paise: number
  revision_count: number
  correction_window_open: boolean
  correction_hours_remaining: number
}

export interface StatementExpense {
  expense_id: string
  description: string
  amount_paise: number
  expense_date: string
  service_period: string | null
  settlement_period: string | null
  period_rule: PeriodRule | null
  expense_kind: ExpenseKind
  allocation_method: AllocationMethod | null
  category: string | null
  definition_name: string | null
  provider_name: string | null
  payer_resident_id: string | null
  payer_display_name: string | null
  generated_from_recurrence: boolean
  funding_source: 'linked_provider_advance' | 'payer_resident' | 'household_pool'
  linked_provider_advances: Array<{
    payment_id: string
    payer_resident_id: string
    amount_paise: number
    payment_date: string
  }>
  allocations: Array<{ resident_id: string; amount_paise: number }>
  allocation_details: Array<{
    allocation_id: string
    resident_id: string
    amount_paise: number
    settled_paise: number
    outstanding_paise: number
  }>
}

export interface StatementSnapshot extends Reconciliation {
  snapshot_version: number
  statement_id: string
  settlement_month: string
  status: StatementStatus
  created_at: string
  finalized_at: string | null
  finalized_by?: string
  correction_deadline: string | null
  closed_at: string | null
  correction_window_hours: number
  generated_at: string
  residents: Array<{ resident_id: string; display_name: string; is_active: number }>
  expenses: StatementExpense[]
  expense_count: number
  statement_total_paise: number
  statement_charges_paise_by_resident: Record<string, number>
  payments_in_month: PaymentRow[]
  adjustments: AdjustmentRow[]
  revisions: Array<{
    revision_id: string
    revision_number: number
    reason: string
    created_at: string
    created_by: string
  }>
  snapshot_hash: string
  warning: string
  carry_forward_breakdown?: ResidentCarryForwardRow[]
  /** Column sums of the breakdown. Absent on statements sealed before it existed. */
  carry_forward_totals?: Record<string, number>
}

export interface OpeningBalanceItem {
  opening_balance_id: string
  apartment_id: string
  resident_id: string
  display_name: string
  amount_paise: number
  direction: 'owed_to_resident' | 'owed_by_resident'
  as_of_date: string
  note: string | null
  settled_paise: number
  outstanding_paise: number
  created_at: string
  created_by: string
  updated_at: string
}

export interface OpeningBalanceListResponse {
  items: OpeningBalanceItem[]
  total: number
  net_paise: number
  owed_by_residents_paise: number
  owed_to_residents_paise: number
  is_editable: boolean
  locked_by_month: string | null
}

export interface OpeningBalancePayload {
  resident_id: string
  amount_paise: number
  as_of_date: string
  note?: string | null
}

export interface ResidentCarryForwardRow {
  resident_id: string
  display_name: string
  brought_forward_paise: number
  statement_charges_paise: number
  /** Charges that settle in this month but are not attached to the statement. */
  unattached_charges_paise: number
  payments_applied_paise: number
  provider_advances_paise: number
  adjustments_paise: number
  carried_forward_paise: number
  formula_explanation?: string
}

export interface StatementPreview extends StatementSnapshot {
  candidate_expenses: Array<{
    expense_id: string
    description: string
    amount_paise: number
    expense_date: string
    settlement_period: string
    service_period: string
    expense_kind: ExpenseKind
    payer_resident_id: string | null
  }>
  candidate_expense_total_paise: number
  blocking_issues: ValidationIssue[]
  can_finalize: boolean
  finalize_blocked_reason: string | null
}

export interface RecurringLinePreview {
  expense_definition_id: string
  name: string
  category: string
  period_rule: PeriodRule
  recurrence: Recurrence
  status: 'eligible' | 'already_exists' | 'needs_amount' | 'not_recurring'
  reason: string | null
  suggested_amount_paise: number | null
  suggested_description: string
  provider_name: string | null
  transaction_date: string
  service_period: string
  settlement_period: string
  allocation_method: PlannableMethod
  participants: string[]
  weights: Record<string, number> | null
  suggested_shares_paise: Record<string, number> | null
  warnings: string[]
  existing_expense_id: string | null
}

export interface RecurringPreview {
  apartment_id: string
  settlement_period: string
  statement_id: string | null
  statement_status: StatementStatus | null
  lines: RecurringLinePreview[]
  eligible_count: number
  eligible_total_paise: number
  already_exists_count: number
  needs_amount_count: number
}

export interface DashboardWarning {
  severity: Severity
  code: string
  message: string
  entity_type: string
  entity_id: string
  suggested_action: string | null
}

export interface TransactionRow {
  transaction_type: 'expense' | 'payment' | 'adjustment'
  transaction_id: string
  transaction_date: string
  settlement_period: string | null
  service_period: string | null
  description: string
  amount_paise: number
  subtype: string
  category: string
  resident_id: string | null
  resident_display_name: string
  counterparty: string
  statement_id: string | null
  status: string
  reference: string | null
  created_at: string
  effect: string
}

/** The dashboard carries only the lifecycle fields of the month's statement. */
export interface DashboardStatement {
  statement_id: string
  settlement_month: string
  status: StatementStatus
  finalized_at: string | null
  correction_deadline: string | null
  closed_at: string | null
  finalized_by: string | null
}

export interface Dashboard {
  apartment: Apartment
  settlement_period: string
  previous_settlement_period: string
  as_of_date: string | null
  generated_at: string
  statement: DashboardStatement | null
  previous_statement: { statement_id: string; settlement_month: string; status: StatementStatus } | null
  statement_status: StatementStatus | 'not_created'
  month: {
    household_expenses_paise: number
    expense_count: number
    resident_pool_payments_paise: number
    provider_advances_paise: number
    refunds_paise: number
    direct_provider_payments_paise: number
    charges_by_resident: Array<{ resident_id: string; display_name: string; charged_paise: number }>
    expenses: ExpenseRow[]
    payments: PaymentRow[]
  }
  all_time: {
    household_expenses_paise: number
    outstanding_charges_paise: number
    unapplied_credits_paise: number
    provider_advances_paise: number
    expense_funding_paise: number
    pool_payments_paise: number
    adjustments_paise: number
    pool_net_position_paise: number
  }
  accounts: ReconciliationAccount[]
  suggested_transfers: TransferSuggestion[]
  latest_transactions: TransactionRow[]
  warnings: DashboardWarning[]
  has_blocking_issues: boolean
  resident_count: number
  definition_count: number
  transaction_type_legend: Record<string, string>
  available_months: string[]
}

export interface AuditRow {
  audit_id: string
  apartment_id: string | null
  entity_type: string
  entity_id: string
  action: string
  field_name: string | null
  old_value: string | null
  new_value: string | null
  reason: string | null
  actor: string
  created_at: string
}

export interface ReportDescriptor {
  kind: string
  label: string
  needs_statement: boolean
  supports_period: boolean
}

export interface Report {
  kind: string
  title: string
  apartment_id: string
  settlement_period: string | null
  statement_id: string | null
  generated_at: string
  rows: Array<Record<string, unknown>>
  row_count: number
}

export interface Health {
  status: 'ok' | 'degraded'
  version: string
  schema_version: number
  database_backend?: string
  /** The PostgreSQL target as postgresql://user@host:port/name; no password. */
  database_path: string
  /** Whether the database could be reached. */
  database_exists: boolean
  apartment_count: number
  default_apartment_id: string | null
  settings: Record<string, unknown>
  checks: Record<string, unknown>
}

export interface ChargeRow {
  allocation_id: string
  amount_paise: number
  expense_id: string
  description: string
  expense_date: string
  due_date: string | null
  settlement_period: string
  service_period: string
  statement_id: string | null
  statement_status: StatementStatus | null
  settled_paise: number
  outstanding_paise: number
}

export interface ResidentDetail {
  resident: Resident
  account: ReconciliationAccount | null
  charges: ChargeRow[]
  charges_by_settlement_period: Record<
    string,
    { charged_paise: number; settled_paise: number; outstanding_paise: number }
  >
  payments: PaymentRow[]
  payment_total_paise: number
  unapplied_credits: CreditRow[]
  unapplied_credit_total_paise: number
}

// --------------------------------------------------------------- guided setup --

export type SetupStepKey = 'apartment' | 'residents' | 'categories' | 'finish' | 'done'

export interface SetupStep {
  key: SetupStepKey
  label: string
  done: boolean
  summary: string | null
}

export interface SetupState {
  next_step: SetupStepKey
  is_complete: boolean
  completed_at: string | null
  apartment_count: number
  apartment: Apartment | null
  apartment_id: string | null
  resident_count: number
  active_resident_count: number
  residents: Array<{ resident_id: string; display_name: string; is_active: number }>
  definition_count: number
  definitions: Array<{
    expense_definition_id: string
    name: string
    category: string
    period_rule: PeriodRule
  }>
  statement_count: number
  current_month: string
  steps: SetupStep[]
}

export interface CatalogueEntry {
  definition_id: string
  name: string
  category: string
  period_rule: PeriodRule
  default_allocation_method: AllocationMethod
  recurrence: Recurrence
  sort_order: number
  suggested: boolean
  hint: string
}

/* -------------------------------------------------------------------- rent -- */

export interface RentLeaseConfig {
  config_id: string
  apartment_id: string
  expense_definition_id: string | null
  expense_definition_name: string | null
  landlord_name: string
  landlord_phone: string | null
  landlord_email: string | null
  landlord_pan: string | null
  landlord_bank_account: string | null
  landlord_bank_ifsc: string | null
  landlord_upi_id: string | null
  monthly_rent_paise: number
  due_day_of_month: number
  requires_landlord_receipt: boolean
  created_at: string
  updated_at: string
  /** The contract term. `lease_start_date` is null until the term is entered. */
  lease_start_date: string | null
  /** The last day of the term, inclusive. */
  lease_end_date: string | null
  duration_months: number
  notice_period_days: number
  lock_in_months: number
  security_deposit_paise: number | null
  escalation_type: EscalationType
  escalation_percent: number
  /** The agreed rent for the next term, when the escalation is a fixed amount. */
  renewal_rent_paise: number | null
  lease_status: 'active' | 'notice_served' | 'vacated' | 'renewed'
  notice_served_date: string | null
  target_handover_date: string | null
  /** 1 for the first term; one more for each renewal. */
  term_number: number
  /** Where the lease stands in its term today. Null until it has a start date. */
  lifecycle: LeaseLifecycle | null
}

export type EscalationType = 'percent' | 'fixed' | 'none'

export interface LeaseLifecycle {
  as_of_date: string
  term_number: number
  /** 0 before the term starts, then 1 up to `duration_months`. */
  current_month_index: number
  duration_months: number
  elapsed_percent: number
  /** Negative once the term has ended. */
  days_until_expiry: number
  notice_deadline_date: string
  is_notice_window_active: boolean
  is_expired: boolean
  lock_in_end_date: string | null
  is_within_lock_in: boolean
  projected_renewal_rent_paise: number
}

export type RentDisbursementMode = 'pool' | 'resident_advance'
export type RentCollectionStatus = 'paid' | 'partial' | 'unpaid' | 'funded_payout'

export interface RentParticipant {
  resident_id: string
  display_name: string
  share_paise: number
  settled_paise: number
  outstanding_paise: number
  last_payment_date: string | null
  collection_status: RentCollectionStatus
  receipt_available: boolean
}

interface RentStatementRef {
  statement_id: string
  settlement_month: string
  status: StatementStatus
}

export interface RentCycle {
  cycle_id: string
  apartment_id: string
  settlement_month: string
  service_period: string
  expense_id: string
  description: string
  /** When the landlord is due, from the lease. */
  due_date: string | null
  /** The due date of the residents' charges, which orders oldest-first payment. */
  charge_due_date: string | null
  total_rent_paise: number
  participant_count: number
  base_share_paise: number
  remainder_paise: number
  allocated_paise: number
  /** Paid into the pool against rent, plus the own share of whoever paid the landlord. */
  collected_paise: number
  collected_count: number
  participants: RentParticipant[]
  excluded_residents: Array<{ resident_id: string; display_name: string }>
  disbursement_status: 'pending' | 'disbursed'
  disbursed_at: string | null
  disbursement_reference: string | null
  disbursed_by: RentDisbursementMode | null
  funding_resident_id: string | null
  funding_resident_name: string | null
  funding_payment_id: string | null
  lease_rent_paise: number | null
  matches_lease_rent: boolean
  landlord_receipt: {
    filename: string
    content_type: string
    size_bytes: number
    sha256: string
    uploaded_at: string
  } | null
  /**
   * `pending`: the landlord has been paid, the lease expects a receipt, and none
   * is attached yet. `not_required` covers both a lease with no receipts and a
   * month that has not been paid.
   */
  landlord_receipt_status: 'not_required' | 'pending' | 'attached'
  statement: RentStatementRef | null
  created_at: string
  warnings?: string[]
  replaced_landlord_receipt?: boolean
  /** Present on the response to recording a rent payment. */
  payment?: {
    payment_id: string
    resident_id: string
    amount_paise: number
    applied_to_rent_paise: number
    applied_to_other_charges_paise: number
    unapplied_credit_paise: number
  }
  adopted_existing_expense?: boolean
}

export interface RentPreview {
  settlement_month: string
  /** `adopt`: rent for the month already exists as an expense and will be linked, not duplicated. */
  mode: 'create' | 'adopt'
  existing_expense_id: string | null
  existing_expense_description: string | null
  due_date: string
  total_rent_paise: number
  participant_count: number
  base_share_paise: number
  remainder_paise: number
  participants: Array<{ resident_id: string; display_name: string; share_paise: number }>
  excluded_residents: Array<{ resident_id: string; display_name: string }>
  can_generate: boolean
  blocked_reason: string | null
  matches_lease_rent: boolean
  statement: RentStatementRef | null
}

export interface RentMonthOverview {
  settlement_month: string
  month_label: string
  config: RentLeaseConfig | null
  cycle: RentCycle | null
  preview: RentPreview | null
}

// ------------------------------------------------- household deposit slots --

export type DepositFundingMode = 'primary_signatory' | 'direct_co_tenants'
export type DepositPaymentMethod = 'upi' | 'bank_transfer' | 'cash' | 'direct_to_landlord'
export type DepositSettlementPath = 'signatory_refund' | 'direct_transfer'
export type DepositSlotStatus = 'vacant' | 'occupied_pending' | 'occupied_funded' | 'vacating'

export interface DepositTransaction {
  tx_id: string
  slot_id: string
  resident_id: string
  resident_display_name: string | null
  tx_type:
    | 'funding_payment'
    | 'move_out_deduction'
    | 'move_out_refund'
    | 'direct_replacement_transfer'
  amount_paise: number
  tx_date: string
  counterparty_resident_id: string | null
  counterparty_display_name: string | null
  payment_method: string | null
  reference: string | null
  notes: string | null
}

/** One permanent share of the household's deposit. It outlives its occupants. */
export interface DepositSlot {
  slot_id: string
  slot_number: number
  slot_label: string
  target_deposit_paise: number
  /** Null while the slot is vacant. */
  assigned_resident_id: string | null
  assigned_display_name: string | null
  assigned_since: string | null
  status: DepositSlotStatus
  funded_amount_paise: number
  /** What the occupant still owes on the slot; 0 while it is vacant. */
  outstanding_paise: number
  funded_date: string | null
  payment_method: DepositPaymentMethod | null
  payment_reference: string | null
  paid_to_resident_id: string | null
  paid_to_display_name: string | null
  is_primary_signatory: boolean
  transactions: DepositTransaction[]
}

/** A slot as proposed or as sent to `POST /api/rent/deposits/slots`. */
export interface DepositSlotDraft {
  slot_number: number
  slot_label: string
  target_deposit_paise: number
  assigned_resident_id: string | null
  assigned_display_name?: string | null
}

/** `GET /api/rent/deposits`. Escrow only: none of it is in any statement or balance. */
export interface HouseholdDepositSummary {
  is_lease_configured: boolean
  /** The deposit on the lease; null until it is entered there. */
  master_deposit_paise: number | null
  /** Null until the operator chooses. There is no default. */
  funding_mode: DepositFundingMode | null
  primary_signatory_id: string | null
  primary_signatory_name: string | null
  /** What the signatory is still out of pocket. Null outside `primary_signatory`. */
  signatory_capital_exposure_paise: number | null
  reimbursements_received_paise: number
  retained_deductions_paise: number
  total_slots_count: number
  vacant_slots_count: number
  total_target_paise: number
  total_funded_paise: number
  /** Targets not yet funded, across vacant and part-funded slots. */
  unfunded_gap_paise: number
  /** Whether the slots' targets add up to the lease's deposit. */
  is_balanced: boolean
  /** Current residents who occupy no slot. */
  unassigned_residents: Array<{ resident_id: string; display_name: string }>
  /** Before any slot exists: one per resident, the deposit split equally. */
  proposal: DepositSlotDraft[]
  slots: DepositSlot[]
}

/** `GET /api/services`: the Housing & Utilities hub's one summary call. */
export interface ServicesOverview {
  settlement_month: string
  rent: { config: RentLeaseConfig | null; cycle: RentCycle | null }
  electricity: {
    /** The bill settling in the month, if any. */
    bill: ElectricityBillSummary | null
    bills_for_month: number
    /** The newest bill for any month. */
    latest_bill: ElectricityBillSummary | null
  }
}

export interface RentReceipt {
  receipt_id: string
  receipt_number: string
  cycle_id: string
  settlement_month: string
  month_label: string
  resident_id: string
  resident_name: string
  premises: string
  amount_paise: number
  amount_in_words: string
  total_rent_paise: number
  participant_count: number
  landlord_name: string
  landlord_pan: string | null
  landlord_pan_advised: boolean
  disbursed_at: string
  disbursed_by: RentDisbursementMode
  disbursement_reference: string
  /** Whether the resident has settled this share with the household. */
  share_settled: boolean
  issued_at: string
  verification_hash: string
  hash_matches: boolean
}
