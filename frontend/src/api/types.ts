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
  /** The postal address, part by part; each is null until recorded. */
  flat_number?: string | null
  tower_block?: string | null
  society_name?: string | null
  locality_street?: string | null
  city?: string | null
  state?: string | null
  pincode?: string | null
  /** The parts on one line, as HRA receipts print it; the name when none are set. */
  premises_address?: string
  /** The household pool, "House Kitty" in every account and statement: where
   * residents send their shares and settlements. */
  pool_name?: string
  /** The legal name on the bank account or UPI ID that holds the kitty. */
  pool_account_holder?: string | null
  pool_bank_account?: string | null
  pool_bank_ifsc?: string | null
  pool_upi_id?: string | null
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
  // The dossier: none of it is read by the ledger.
  profession: string | null
  employer: string | null
  work_mode: WorkMode | null
  blood_group: BloodGroup | null
  secondary_emergency_name: string | null
  secondary_emergency_phone: string | null
  secondary_emergency_relation: string | null
  notice_period_days: number | null
  lock_in_until: string | null
  is_lease_signatory: number | boolean
  /** Keys, fobs and remotes handed over at move-in. */
  keys_issued_notes: string | null
  vehicle_type: VehicleType | null
  /** Stored upper-case. */
  vehicle_reg_no: string | null
  parking_slot: string | null
  society_rfid_tag: string | null
  id_document_type: IdDocumentType | null
  /** Shown only by its last four characters. */
  id_document_number: string | null
  home_city: string | null
  home_state: string | null
  police_verification_status: PoliceVerificationStatus
  society_noc_status: SocietyNocStatus
  created_at: string | null
  updated_at: string | null
}

export type WorkMode = 'wfh' | 'hybrid' | 'onsite'
export type BloodGroup = 'A+' | 'A-' | 'B+' | 'B-' | 'AB+' | 'AB-' | 'O+' | 'O-'
export type VehicleType = 'none' | 'two_wheeler' | 'four_wheeler' | 'both'
export type IdDocumentType = 'aadhaar' | 'passport' | 'voter_id' | 'driving_license' | 'other'
export type PoliceVerificationStatus = 'pending' | 'submitted' | 'verified' | 'exempt'
export type SocietyNocStatus = 'pending' | 'approved' | 'not_applicable'

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
  /** The pool's own row: where to pay it, once recorded on the apartment. */
  bank_account?: string | null
  bank_ifsc?: string | null
  upi_id?: string | null
}

// ------------------------------------------------------------ payment pools --

/** One line of a Payment Pools passbook: money into or out of the account. */
export interface StatementEntry {
  entry_id: string
  account: 'pool' | 'landlord'
  date: string
  /** Its place in date order, from 1. */
  seq: number
  source_type: 'payment' | 'rent_cycle' | 'deposit' | 'expense'
  category: string
  description: string
  counterparty_name: string | null
  counterparty_resident_id: string | null
  direction: 'in' | 'out'
  amount_paise: number
  /** The account's balance after this entry. */
  balance_paise: number
  payment_mode: string | null
  reference: string | null
  /** The payment behind the entry, when there is one. */
  payment_id: string | null
  note: string | null
}

export interface PoolStatement {
  /** For the kitty, `account_holder` is whose bank account or UPI ID holds it. */
  account: { instance_id: string; name: string; account_holder?: string | null }
  /** Newest first. */
  items: StatementEntry[]
  total_in_paise: number
  total_out_paise: number
  closing_balance_paise: number
}

export interface LandlordStatement extends PoolStatement {
  /** `name` is whose name the account is in; `owner_name` is always the landlord's. */
  account: { instance_id: string; name: string; owner_name?: string }
  total_rent_paid_paise: number
  /** Deposits paid to the landlord, less what they refunded or kept. */
  deposit_held_paise: number
}

export interface PaymentPoolsStatement {
  apartment_id: string
  pool_statement: PoolStatement
  landlord_statement: LandlordStatement
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
  /** The whole name, composed from the parts below. */
  landlord_name: string
  /** The landlord's name in parts; null on a lease saved before names had parts. */
  landlord_first_name?: string | null
  landlord_middle_name?: string | null
  landlord_last_name?: string | null
  landlord_phone: string | null
  landlord_email: string | null
  landlord_pan: string | null
  landlord_bank_account: string | null
  landlord_bank_ifsc: string | null
  landlord_upi_id: string | null
  /** Whose name the payout account is in, when it is not the landlord's own. The
   * landlord stays the owner on the lease; null means the account is theirs. */
  payout_beneficiary_name?: string | null
  /** Who rent and deposits are paid to: the beneficiary, else the landlord. */
  effective_payout_name?: string
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
  /** The household's agreement model: how each month's rent is divided. */
  rent_split_mode: RentSplitMode
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

/** Whether the landlord has been paid this month's rent: not yet, in part, or in full. */
export type RentDisbursementStatus = 'pending' | 'partially_disbursed' | 'disbursed'

/** One payment to the landlord towards a month's rent: the rent can go in tranches. */
export interface RentDisbursement {
  disbursement_id: string
  cycle_id: string
  amount_paise: number
  disbursed_at: string
  disbursement_reference: string
  disbursed_by: RentDisbursementMode
  funding_resident_id: string | null
  funding_resident_name: string | null
  funding_payment_id: string | null
  note: string | null
  created_at: string
}
export type RentCollectionStatus = 'paid' | 'partial' | 'unpaid' | 'funded_payout'

/** A payment that settled part of a resident's rent share: only what it put towards the share. */
export interface RentParticipantPayment {
  payment_id: string
  payment_date: string
  amount_paise: number
  payment_mode: 'upi' | 'bank_transfer' | 'cash' | 'other' | null
  reference: string | null
  note: string | null
  created_at: string | null
}

export interface RentParticipant {
  resident_id: string
  display_name: string
  share_paise: number
  settled_paise: number
  outstanding_paise: number
  last_payment_date: string | null
  collection_status: RentCollectionStatus
  receipt_available: boolean
  /** What the resident paid the landlord directly this month: an advance, not a pool payment. */
  advanced_paise: number
  /** The payments that settled this share, oldest first. They add up to settled_paise. */
  payments: RentParticipantPayment[]
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
  /** How this month was divided: equally, or by each room's base rent. */
  split_mode: RentSplitMode
  participant_count: number
  base_share_paise: number
  remainder_paise: number
  allocated_paise: number
  /** Paid into the pool against rent, plus the own share of whoever paid the landlord. */
  collected_paise: number
  collected_count: number
  participants: RentParticipant[]
  excluded_residents: Array<{ resident_id: string; display_name: string }>
  disbursement_status: RentDisbursementStatus
  /** Paid to the landlord so far, across every tranche. */
  disbursed_paise: number
  /** Still owed to the landlord for the month. */
  remaining_disbursement_paise: number
  /** Each payment to the landlord, oldest first. */
  disbursements: RentDisbursement[]
  /** The latest tranche's date and reference. */
  disbursed_at: string | null
  disbursement_reference: string | null
  /** One mode, or 'mixed' when tranches were paid different ways. */
  disbursed_by: RentDisbursementMode | 'mixed' | null
  funding_resident_id: string | null
  funding_resident_name: string | null
  funding_payment_id: string | null
  lease_rent_paise: number | null
  matches_lease_rent: boolean
  /** 'live' while the month follows the lease; 'frozen' once it is over, finalized or paid to the landlord in full. */
  cycle_state?: 'live' | 'frozen'
  /** What the kitty owes back to each resident who paid the landlord more than their share. */
  reimbursements?: KittyReimbursement[]
  reimbursement?: KittyReimbursement | null
  /** Whether a payment or payout recorded by mistake can still be deleted: until the statement is finalized. */
  transactions_open?: boolean
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
  /** The split this preview was worked out for. */
  split_mode: RentSplitMode
  /** Whether the rent can be split by room, and each participant's room rent. */
  room_tiered: RoomTieredPlan
}

export type RentSplitMode = 'equal' | 'room_tiered'

export interface RoomTieredPlan {
  available: boolean
  blocked_reason: string | null
  room_total_paise: number
  lease_rent_paise: number
  shares: Array<{
    resident_id: string
    display_name: string
    room_id: string | null
    room_number: string | null
    base_rent_paise: number | null
  }>
}

export interface RentMonthOverview {
  settlement_month: string
  month_label: string
  config: RentLeaseConfig | null
  cycle: RentCycle | null
  preview: RentPreview | null
  /**
   * 'live': charged, and following the lease while the month is open; 'frozen': its snapshot;
   * 'upcoming': the month has not begun; 'not_generated': not charged.
   */
  month_state?: 'live' | 'frozen' | 'upcoming' | 'not_generated'
  /** Why an open month cannot follow the lease as it stands, if it cannot: it stays as it was. */
  live_blocked_reason?: string | null
  /** Whether the month could be split by room, charged or not. */
  room_tiered?: RoomTieredPlan | null
}

// ------------------------------------------------- household deposit slots --

export type DepositFundingMode = 'primary_signatory' | 'direct_co_tenants'
export type DepositPaymentMethod = 'upi' | 'bank_transfer' | 'cash' | 'direct_to_landlord'
/** Who received a deposit payment: a resident, the landlord, or the household pool. */
export type DepositPaidToParty = 'resident' | 'landlord' | 'pool'

/** The landlord or the household pool, with where to send them money. */
export interface PaymentInstance {
  instance_id: '__landlord__' | '__pool__'
  party: 'landlord' | 'pool'
  name: string
  bank_account: string | null
  bank_ifsc: string | null
  upi_id: string | null
}

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
  /** The kind of party on the other side; older rows are read from what they record. */
  counterparty_party: DepositPaidToParty | null
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
  /** "Landlord (name)", the pool's name, or the resident paid. */
  paid_to_display_name: string | null
  paid_to_party: DepositPaidToParty | null
  is_primary_signatory: boolean
  transactions: DepositTransaction[]
  /** The room the slot backs; all null while it is linked to none. */
  room_id: string | null
  room_number: string | null
  has_attached_washroom: boolean | null
  has_balcony: boolean | null
  base_rent_paise: number | null
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
  primary_signatory_upi_id: string | null
  /** The two standing payees, with their payment details. */
  landlord_instance: PaymentInstance
  pool_instance: PaymentInstance
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

// ------------------------------------------------------------ exit clearance --

export type ClearancePath =
  | 'signatory_refund'
  | 'direct_replacement_transfer'
  | 'standalone_clearance'

/** `GET /api/residents/{id}/clearance/preview`: what a clearance would settle. */
export interface ExitClearancePreview {
  resident_id: string
  display_name: string
  is_active: boolean
  /** The deposit slot they occupy; null if they hold none. */
  slot: {
    slot_id: string
    slot_number: number
    slot_label: string
    target_deposit_paise: number
    funded_amount_paise: number
  } | null
  funding_mode: DepositFundingMode | null
  primary_signatory_id: string | null
  primary_signatory_name: string | null
  ledger_outstanding_charges_paise: number
  ledger_unapplied_credits_paise: number
  /** Negative while they owe the household. This is what an offset clears. */
  operating_net_balance_paise: number
  default_move_out_date: string
  /** Current residents with no slot, who could take theirs over. */
  incoming_candidates: Array<{ resident_id: string; display_name: string }>
  allowed_settlement_paths: ClearancePath[]
  /** Why they cannot be cleared here, when they cannot. */
  blocking_reason: string | null
}

/** A recorded exit clearance, as returned by the clearance endpoints. */
export interface ExitClearance {
  clearance_id: string
  resident_id: string
  resident_display_name: string
  apartment_name: string
  move_out_date: string
  slot_id: string | null
  slot_number: number | null
  slot_label: string | null
  deposit_funded_paise: number
  deposit_deductions_paise: number
  deduction_reason: string | null
  /** Put in, less deductions. */
  deposit_refund_paise: number
  ledger_outstanding_paise: number
  ledger_credit_paise: number
  net_ledger_offset_applied: boolean
  /** Signed: what the operating ledger added to, or took from, the cash. */
  ledger_offset_paise: number
  /** Positive: payable to the resident. Negative: payable by them. */
  net_settlement_paise: number
  settlement_direction: 'refund_to_resident' | 'payment_from_resident' | 'zero_balanced'
  settlement_path: ClearancePath
  settlement_date: string
  settlement_reference: string | null
  counterparty_resident_id: string | null
  counterparty_display_name: string | null
  clearing_payment_id: string | null
  notes: string | null
  /** Who the net amount is paid to, and a UPI link for it when they have a UPI ID. */
  payee_resident_id: string | null
  payee_display_name: string | null
  payee_upi_id: string | null
  upi_uri: string | null
  /** The clearance as a short message, ready to paste into a chat. */
  summary_text: string
  created_at: string
}

/** `GET /api/services`: the Services & Utilities hub's one summary call. */
/* ---------------------------------------------- household services: furniture */

export type FurnitureCategory = 'living_room' | 'bedroom' | 'appliance' | 'dining' | 'workstation' | 'other'
export type FurnitureItemStatus = 'active' | 'pending_delivery' | 'returned'
export type FurnitureDepositStatus = 'held' | 'partially_refunded' | 'refunded'

/** One recurring arrangement the flat pays for; each type has its own module. */
export interface HouseholdService {
  service_id: string
  service_type: 'furniture_rental' | 'broadband' | 'domestic_help' | 'water_tanker' | 'cooking_gas'
  display_name: string
  provider_name: string | null
  status: 'active' | 'paused' | 'retired'
  billing_day_of_month: number | null
  period_rule: string
  created_at: string
  updated_at: string
}

export interface FurnitureConfig {
  provider_name: string
  customer_id: string | null
  subscription_reference: string | null
  deposit_paid_paise: number
  deposit_refunded_paise: number
  deposit_status: FurnitureDepositStatus
  /** The expense definition each month's bill is recorded under. */
  expense_definition_id: string | null
  notes: string | null
  updated_at: string
}

export interface FurnitureItem {
  item_id: string
  /** The provider's subscription it is rented on. */
  service_id: string
  provider_name?: string
  service_display_name?: string
  name: string
  category: FurnitureCategory
  monthly_rent_paise: number
  /** common: split equally; resident: charged to assigned_resident_id alone. */
  allocation_scope: 'common' | 'resident'
  assigned_resident_id: string | null
  assigned_resident_name: string | null
  status: FurnitureItemStatus
  delivery_date: string | null
  return_date: string | null
  created_at: string
  updated_at: string
}

/**
 * Where a resident's charge on a recorded bill stands. `funded_payout`: they
 * paid the provider the whole bill, which covers their own share.
 */
export type FurnitureShareStatus = 'paid' | 'partial' | 'unpaid' | 'funded_payout'

export interface FurnitureShare {
  resident_id: string
  display_name: string
  taking_part: boolean
  common_share_paise: number
  private_paise: number
  private_items: string[]
  total_paise: number
  /** Paid towards their charge, into the kitty or by paying the provider. 0 until the bill is recorded. */
  paid_paise: number
  /** Paid into the kitty against their charge. */
  settled_paise: number
  /** What is still owed on their charge. 0 until the bill is recorded. */
  outstanding_paise: number
  /** Null until the bill is recorded, and for a charge of nothing. */
  status: FurnitureShareStatus | null
  paid_date: string | null
  /** Payments into the kitty that settled their charge, oldest first, each with what it put towards it. */
  payments: Array<{
    payment_id: string
    payment_date: string
    amount_paise: number
    payment_mode: 'upi' | 'bank_transfer' | 'cash' | 'other' | null
    reference: string | null
    note: string | null
  }>
}

/** One payment to a household service's provider towards a month's bill: from the kitty, or by a resident. */
export interface FurnitureDisbursement {
  disbursement_id: string
  /** The provider it paid. */
  service_id?: string
  provider_name?: string
  amount_paise: number
  disbursed_at: string
  disbursement_reference: string
  /** pool: paid from the House Kitty; resident_advance: a resident paid it from their own account. */
  disbursed_by: 'pool' | 'resident_advance'
  funding_resident_id: string | null
  funding_resident_name: string | null
  funding_payment_id: string | null
  note: string | null
  created_at: string
}

export type FurniturePayoutStatus = 'pending' | 'partially_disbursed' | 'disbursed'

/** One provider's part of the month's bill, and what has been paid to them. */
export interface FurnitureProviderPayout {
  service_id: string
  display_name: string
  provider_name: string
  customer_id: string | null
  subscription_reference: string | null
  billing_day_of_month: number | null
  item_count: number
  total_paise: number
  disbursed_paise: number
  remaining_disbursement_paise: number
  payout_status: FurniturePayoutStatus
  disbursements: FurnitureDisbursement[]
}

/** One provider's subscription, with its own totals and deposit. */
export interface FurnitureServiceEntry {
  service: HouseholdService
  config: FurnitureConfig
  monthly_total_paise: number
  active_item_count: number
  next_billing_date: string | null
  deposit_held_paise: number
}

/** What the kitty owes the resident who paid the provider, and has paid back. */
/**
 * What the House Kitty owes back to a resident who paid the landlord or a
 * provider more than their own share of a month's bill, and what it has paid.
 */
export interface KittyReimbursement {
  recipient_resident_id: string
  recipient_name: string
  total_fronted_paise: number
  /** Their own charge on the bill. */
  payer_share_paise: number
  /** What they paid, less the part of their own share not already paid into the kitty. */
  net_reimbursement_owed_paise: number
  reimbursed_so_far_paise: number
  pending_reimbursement_paise: number
  /** What flatmates paid into the kitty for the bill, less what has been paid back. */
  kitty_cash_available_paise: number
  /** What can be paid back now: the lesser of the two above. */
  reimbursable_paise: number
  can_reimburse: boolean
  reimbursements: Array<{ payment_id: string; amount_paise: number; payment_date: string; reference: string | null }>
}

/** The furniture bill's name for it. */
export type FurnitureReimbursement = KittyReimbursement

/** What a month bills. Nothing prorates: an item in the flat at any point counts in full. */
export interface FurnitureMonthBill {
  settlement_month: string
  billable_items: FurnitureItem[]
  common_total_paise: number
  private_total_paise: number
  total_paise: number
  participant_count: number
  base_share_paise: number
  remainder_paise: number
  shares: FurnitureShare[]
  excluded_residents: Array<{ resident_id: string; display_name: string }>
  /** Why the month cannot be billed as it stands, if it cannot. */
  blocked_reason: string | null
  /** Whether a payment or payout recorded by mistake can still be deleted: until the statement is finalized. */
  transactions_open?: boolean
  /** The month's bill once recorded. */
  expense: { expense_id: string; description: string; amount_paise: number; statement_id: string | null } | null
  statement_status: StatementStatus | null
  can_generate: boolean
  /**
   * 'live': recorded, and following its items while the month is open;
   * 'frozen': the month is over or its statement finalized, so the bill is its snapshot;
   * 'upcoming': the month has not begun; 'not_billed': nothing to bill.
   */
  bill_state?: 'live' | 'frozen' | 'upcoming' | 'not_billed'
  /** Paid into the kitty against the bill, plus what paying the provider covered of a resident's own share. */
  collected_paise: number
  /** Residents whose share is covered in full. */
  collected_count: number
  /** 'pending' until the provider is paid anything; the bill can be paid in tranches. */
  payout_status: FurniturePayoutStatus
  disbursed_paise: number
  remaining_disbursement_paise: number
  disbursements: FurnitureDisbursement[]
  /** Null unless a resident paid a provider more than their share; the first of `reimbursements`. */
  reimbursement: FurnitureReimbursement | null
  /** What the kitty owes each resident who paid a provider more than their share. */
  reimbursements?: FurnitureReimbursement[]
  /** Each provider's part of the bill, and what has been paid to them. */
  provider_payouts?: FurnitureProviderPayout[]
}

export interface FurnitureRentalOverview {
  /** Null until furniture rental is set up, and after it is retired. The first provider's. */
  service: HouseholdService | null
  config: FurnitureConfig | null
  /** Every live provider's subscription; the totals below are across all of them. */
  services?: FurnitureServiceEntry[]
  items: FurnitureItem[]
  month: FurnitureMonthBill | null
  /** What the items in the flat now cost a month. */
  monthly_total_paise: number
  active_item_count: number
  next_billing_date: string | null
  deposit_held_paise: number
}

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

/** One expense's share charged to a resident in a settlement month. */
export interface ResidentMonthlyStatementItem {
  expense_id: string
  category: string
  description: string
  expense_date: string
  service_period: string
  allocation_method: string
  total_expense_paise: number
  resident_share_paise: number
  notes: string | null
  components: Array<{
    name: string
    allocation_method: string
    total_paise: number
    resident_share_paise: number
  }>
}

/** A payment or credit in the month. Positive reduces what the resident owes. */
export interface ResidentMonthlyPaymentItem {
  payment_id: string
  payment_date: string
  payment_kind: string
  payment_mode: string
  amount_paise: number
  reference: string | null
  description: string
}

/**
 * A resident's own statement for one settlement month. Amounts read like a
 * bill: a positive net due is owed to the pool, a negative one to the resident.
 * net due = brought forward + total charges - total payments.
 */
export interface ResidentMonthlyStatement {
  resident_id: string
  display_name: string
  is_active: boolean
  room_label: string | null
  apartment_name: string
  settlement_month: string
  statement_id: string | null
  statement_status: 'draft' | 'finalized_correction_window' | 'closed' | 'not_created'
  items: ResidentMonthlyStatementItem[]
  total_charges_paise: number
  payments: ResidentMonthlyPaymentItem[]
  total_payments_paise: number
  brought_forward_paise: number
  net_due_paise: number
  direction: 'owed_to_pool' | 'owed_to_resident' | 'settled'
  /** What is still to pay today: never more than the net due. */
  payable_now_paise: number
  recipient_upi_id: string | null
  recipient_name: string | null
  upi_uri: string | null
  whatsapp_text: string
}

export interface FlatmateStatementList {
  settlement_month: string
  statement_status: ResidentMonthlyStatement['statement_status']
  items: ResidentMonthlyStatement[]
  totals: {
    total_charges_paise: number
    total_payments_paise: number
    brought_forward_paise: number
    net_due_paise: number
  }
}

/** A room in the flat, and who occupies it. */
export interface Room {
  room_id: string
  apartment_id: string
  room_number: string
  has_attached_washroom: boolean
  has_balcony: boolean
  /** This room's share of the rent for a room-tiered split; null until set. */
  base_rent_paise: number | null
  assigned_resident_id: string | null
  assigned_display_name: string | null
  assigned_since: string | null
  status: 'occupied' | 'vacant'
  /** The deposit slot that backs the room, if any. */
  linked_deposit_slot: {
    slot_id: string
    slot_number: number
    slot_label: string
    target_deposit_paise: number
    funded_amount_paise: number
    status: DepositSlotStatus
  } | null
  created_at: string
  updated_at: string
}

export interface RoomList {
  items: Room[]
  total: number
  summary: {
    total_rooms: number
    occupied_rooms: number
    vacant_rooms: number
    with_attached_washroom: number
    with_balcony: number
    rooms_with_base_rent: number
    total_base_rent_paise: number
    linked_slots: number
  }
}

export type ContactRole = 'owner' | 'broker' | 'caretaker' | 'society_manager' | 'emergency_utility'

/** Someone the household deals with. Role-specific fields are null for other roles. */
export interface HouseholdContact {
  contact_id: string
  apartment_id: string
  role: ContactRole
  /** The whole name, composed from the parts below. Shown wherever a name is read. */
  name: string
  /** The name in parts, as it is edited. */
  first_name?: string | null
  middle_name?: string | null
  last_name?: string | null
  phone: string | null
  email: string | null
  pan: string | null
  bank_account_no: string | null
  bank_ifsc: string | null
  upi_id: string | null
  /** Owner only: whose name their payout account is in, when it is not their own. */
  beneficiary_name?: string | null
  agency_name: string | null
  fee_amount_paise: number | null
  fee_status: 'paid' | 'pending' | 'na' | null
  fee_display: string | null
  responsibilities: string | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface ContactList {
  items: HouseholdContact[]
  total: number
  roles_on_file: ContactRole[]
}

export type DocumentCategory =
  | 'lease_agreement'
  | 'society_noc'
  | 'police_verification'
  | 'inventory_handover'
  | 'other'

/** A stored document's details. The file itself is only ever downloaded. */
export interface HouseholdDocument {
  doc_id: string
  apartment_id: string
  title: string
  category: DocumentCategory
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  uploaded_by_resident_id: string | null
  notes: string | null
  uploaded_at: string
}

export interface DocumentList {
  items: HouseholdDocument[]
  total: number
  total_bytes: number
  categories_on_file: DocumentCategory[]
}

/** A platform user: the household admin, for now the only kind that signs up. */
export interface AuthUser {
  user_id: string
  email: string
  full_name: string
  role: 'household_admin' | 'resident' | 'auditor'
}

export interface AuthStatus {
  /** Nobody has signed up yet: the first to do so becomes the household admin. */
  setup_required: boolean
  authenticated: boolean
  user: AuthUser | null
}
