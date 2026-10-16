/**
 * The household's security deposit: how it was funded, the permanent slots it
 * is divided into, and what happens to a slot when its occupant leaves.
 *
 * A deposit is escrow, not operating money. Nothing on these screens creates
 * an expense, a charge or a payment, and no figure here is part of a statement
 * or a balance. Every amount shown comes from the backend; the one sum done
 * here is "put in, less deductions", which the backend checks again.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  BedDouble,
  History,
  Landmark,
  LogOut,
  Plus,
  ShieldCheck,
  UserPlus,
} from 'lucide-react'
import { AsyncSection, ErrorNotice, Modal } from '../../components/Primitives'
import {
  DateField,
  MoneyInput,
  SelectField,
  TextAreaField,
  TextField,
} from '../../components/Form'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  EditButton,
  EmptyBlock,
  MiniStat,
  Panel,
  Pill,
  Rupees,
  type Tone,
} from '../../components/ui'
import { api } from '../../api/client'
import { type Resource, useMutation, useResource } from '../../lib/useApi'
import { formatDate, formatInr, paiseToInput, parseRupees, todayIso } from '../../lib/format'
import type {
  DepositFundingMode,
  DepositPaymentMethod,
  DepositSettlementPath,
  DepositSlot,
  DepositTransaction,
  HouseholdDepositSummary,
  RoomList,
} from '../../api/types'

const DEPOSITS = '/api/rent/deposits'

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold'

const STATUS: Record<DepositSlot['status'], { tone: Tone; label: string }> = {
  vacant: { tone: 'neutral', label: 'Vacant' },
  occupied_pending: { tone: 'warning', label: 'Pending' },
  occupied_funded: { tone: 'success', label: 'Funded' },
  vacating: { tone: 'warning', label: 'Vacating' },
}

const METHOD_LABELS: Record<DepositPaymentMethod, string> = {
  upi: 'UPI',
  bank_transfer: 'Bank transfer',
  cash: 'Cash',
  direct_to_landlord: 'Direct to landlord',
}

/** Money in is green; money handed back or kept is amber. */
const TX_TONES: Record<DepositTransaction['tx_type'], Tone> = {
  funding_payment: 'success',
  direct_replacement_transfer: 'success',
  move_out_refund: 'warning',
  move_out_deduction: 'warning',
}

const TX_LABELS: Record<DepositTransaction['tx_type'], string> = {
  funding_payment: 'Paid in',
  move_out_deduction: 'Deducted on leaving',
  move_out_refund: 'Refunded on leaving',
  direct_replacement_transfer: 'Paid by replacement',
}

/**
 * Who the money moved from and who it moved to. The entry's resident is always
 * the slot's occupant (or leaver); the counterparty is the signatory paid or
 * refunding, or the replacement buying the slot. When nobody else is named the
 * deposit's holder is the signatory who fronted it, or else the landlord.
 */
function txParties(
  tx: DepositTransaction,
  summary: HouseholdDepositSummary,
): { from: string; to: string } {
  const occupant = tx.resident_display_name ?? '—'
  const holder =
    tx.counterparty_display_name ??
    (summary.funding_mode === 'primary_signatory'
      ? (summary.primary_signatory_name ?? 'Primary signatory')
      : 'Landlord')
  switch (tx.tx_type) {
    case 'funding_payment':
      return { from: occupant, to: holder }
    case 'move_out_refund':
      return { from: holder, to: occupant }
    case 'direct_replacement_transfer':
      return { from: tx.counterparty_display_name ?? '—', to: occupant }
    case 'move_out_deduction':
      return { from: occupant, to: holder }
  }
}

/** How the money moved; a deduction never moves, the holder keeps it. */
function txMethod(tx: DepositTransaction): string {
  if (tx.payment_method) {
    return METHOD_LABELS[tx.payment_method as DepositPaymentMethod] ?? tx.payment_method
  }
  return tx.tx_type === 'move_out_deduction' ? 'Retained from deposit' : 'Not recorded'
}

const MODES: Array<{ value: DepositFundingMode; title: string; body: string }> = [
  {
    value: 'primary_signatory',
    title: 'Primary Signatory Fronted (Master Tenant)',
    body: 'One resident fronted the full deposit to the landlord; the other occupants reimburse them.',
  },
  {
    value: 'direct_co_tenants',
    title: 'Direct Co-Tenants',
    body: 'Joint escrow: each flatmate pays the landlord, or a common pool, directly.',
  },
]

const MODE_LABELS: Record<DepositFundingMode, string> = {
  primary_signatory: 'Primary signatory fronted',
  direct_co_tenants: 'Direct co-tenants',
}

export function useDepositSummary(): Resource<HouseholdDepositSummary> {
  return useResource<HouseholdDepositSummary>(DEPOSITS)
}

/** A rupee field's text and what it parses to, kept together. */
function useMoney(initialPaise: number | null) {
  const [raw, setRaw] = useState(initialPaise != null ? paiseToInput(initialPaise) : '')
  const parsed = parseRupees(raw, { allowEmpty: true })
  return { raw, setRaw, paise: parsed.paise, ok: parsed.ok, empty: !raw.trim() }
}

function slotName(slot: Pick<DepositSlot, 'slot_number' | 'slot_label'>): string {
  return `Slot ${slot.slot_number} (${slot.slot_label})`
}

/* --------------------------------------------------------------- the page -- */

/** Setup, or the escrow pool once slots exist. Before there is a lease, a pointer to it. */
export function SecurityDepositSection() {
  const summary = useDepositSummary()
  return (
    <AsyncSection resource={summary} loadingLabel="Loading security deposits…">
      {(data) =>
        !data.is_lease_configured ? (
          <Panel
            title="Set up the lease first"
            icon={<ShieldCheck className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
          >
            <EmptyBlock
              title="No lease on file yet"
              body="The security deposit belongs to the lease. Enter the lease, including the deposit held by the landlord, and its slots can then be set up here."
              action={
                <Link to="/housing?tab=lease" className={BUTTON_PRIMARY}>
                  Set up lease
                </Link>
              }
            />
          </Panel>
        ) : data.slots.length === 0 ? (
          <DepositSetupCard
            // Remount once the mode or the deposit is saved, so the form starts
            // from what is stored and the proposal follows the new amount.
            key={`${data.funding_mode ?? 'unset'}:${data.master_deposit_paise ?? 'none'}`}
            summary={data}
            onSaved={summary.reload}
          />
        ) : (
          <EscrowPoolCard summary={data} onChanged={summary.reload} />
        )
      }
    </AsyncSection>
  )
}

/* ------------------------------------------------------------------ setup -- */

interface SlotRow {
  slot_number: number
  label: string
  resident: string
  amount: string
}

/**
 * The mandatory first step: say how the deposit was funded, then divide it into
 * slots. The mode is never pre-selected. Once it is saved, only the slots are
 * left to set up.
 */
function DepositSetupCard({
  summary,
  onSaved,
}: {
  summary: HouseholdDepositSummary
  onSaved: () => void
}) {
  const modeSaved = summary.funding_mode !== null
  // Deliberately empty to start: the operator must choose.
  const [mode, setMode] = useState<DepositFundingMode | ''>(summary.funding_mode ?? '')
  const [signatory, setSignatory] = useState(summary.primary_signatory_id ?? '')
  const [touched, setTouched] = useState(false)
  const [rows, setRows] = useState<SlotRow[]>(() =>
    summary.proposal.map((slot) => ({
      slot_number: slot.slot_number,
      label: slot.slot_label,
      resident: slot.assigned_resident_id ?? '',
      amount: paiseToInput(slot.target_deposit_paise),
    })),
  )
  const master = summary.master_deposit_paise
  const canSplit = master !== null && rows.length > 0

  const modeError = touched && !mode ? 'Choose how the deposit was funded.' : null
  const signatoryError =
    touched && mode === 'primary_signatory' && !signatory
      ? 'Select the primary lease signatory.'
      : null
  const modeValid = Boolean(mode) && (mode !== 'primary_signatory' || Boolean(signatory))

  const checked = rows.map((row) => {
    const parsed = parseRupees(row.amount, { allowEmpty: true })
    const clash =
      row.resident !== '' && rows.filter((other) => other.resident === row.resident).length > 1
    return {
      ...row,
      paise: parsed.paise,
      labelError: !row.label.trim() ? 'Give the slot a name.' : null,
      residentError: clash ? 'Already assigned to another slot.' : null,
      amountError: !row.amount.trim()
        ? 'Enter the target.'
        : parsed.ok && parsed.paise <= 0
          ? 'Enter an amount above zero.'
          : null,
      amountOk: parsed.ok && parsed.paise > 0,
    }
  })
  const total = checked.reduce((sum, row) => sum + (row.amountOk ? row.paise : 0), 0)
  const difference = master !== null ? total - master : 0
  const slotsValid =
    !canSplit ||
    (checked.every((row) => !row.labelError && !row.residentError && row.amountOk) &&
      difference === 0)
  const valid = modeValid && slotsValid

  const save = useMutation(
    async () => {
      try {
        if (!modeSaved) {
          await api.post<HouseholdDepositSummary>(`${DEPOSITS}/config`, {
            funding_mode: mode,
            primary_signatory_resident_id: mode === 'primary_signatory' ? signatory : null,
          })
        }
        if (canSplit) {
          await api.post<HouseholdDepositSummary>(`${DEPOSITS}/slots`, {
            slots: checked.map((row) => ({
              slot_number: row.slot_number,
              slot_label: row.label.trim(),
              target_deposit_paise: row.paise,
              assigned_resident_id: row.resident || null,
            })),
          })
        }
      } finally {
        // The mode may have saved even if the slots were refused, so the
        // screen is always brought back in line with what is stored.
        onSaved()
      }
    },
  )

  const setRow = (index: number, changes: Partial<SlotRow>) =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...changes } : row)))
  const residents = summary.unassigned_residents

  return (
    <Panel
      title="Household Security Deposit Setup"
      icon={<ShieldCheck className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="The deposit belongs to the household. Say how it was funded and divide it into slots, one per space; slots stay as people come and go. Deposits are held in escrow and never appear in statements or balances."
      actions={<Pill tone="warning">Setup required</Pill>}
    >
      {/* The deposit amount saves through a form of its own, so the setup form
          starts below it; the mode and signatory above are read from state. */}
      <div className="space-y-5">
        {save.error && <ErrorNotice error={save.error} />}

        {modeSaved ? (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            Funding mode:{' '}
            <strong className="text-[var(--text-primary)]">
              {MODE_LABELS[summary.funding_mode!]}
            </strong>
            {summary.primary_signatory_name && <> · signatory {summary.primary_signatory_name}</>}
          </p>
        ) : (
          <>
            <fieldset
              role="radiogroup"
              aria-required="true"
              aria-invalid={modeError ? true : undefined}
              aria-describedby={modeError ? 'deposit-mode-error' : undefined}
              className="m-0 border-0 p-0 space-y-2.5"
            >
              <legend className="p-0 pb-1 text-sm font-bold text-[var(--text-primary)]">
                How was the deposit funded?
              </legend>
              {MODES.map((option) => (
                <label
                  key={option.value}
                  className={
                    mode === option.value
                      ? 'flex items-start gap-3 rounded-xl border border-[var(--accent)] bg-[var(--accent-glow)] p-3.5 cursor-pointer'
                      : modeError
                        ? 'flex items-start gap-3 rounded-xl border border-[var(--danger)] bg-[var(--bg-elevated)] p-3.5 cursor-pointer'
                        : 'flex items-start gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 cursor-pointer'
                  }
                >
                  <input
                    type="radio"
                    name="deposit-funding-mode"
                    className="mt-1 shrink-0"
                    value={option.value}
                    checked={mode === option.value}
                    onChange={() => setMode(option.value)}
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-bold text-[var(--text-primary)]">
                      {option.title}
                    </span>
                    <span className="block text-xs text-[var(--text-secondary)]">
                      {option.body}
                    </span>
                  </span>
                </label>
              ))}
              {modeError && (
                <span className="field-error" id="deposit-mode-error">
                  {modeError}
                </span>
              )}
            </fieldset>

            {mode === 'primary_signatory' && (
              <div className="max-w-sm">
                <SelectField
                  label="Primary Signatory"
                  required
                  value={signatory}
                  placeholder="Choose a resident"
                  options={residents.map((row) => ({
                    value: row.resident_id,
                    label: row.display_name,
                  }))}
                  onChange={setSignatory}
                  error={signatoryError}
                  hint="The resident who paid the whole deposit to the landlord. Their own slot counts as funded."
                />
              </div>
            )}
          </>
        )}

        <DepositAmountForm master={master} onSaved={onSaved} />

        <form
          aria-label="Deposit setup"
          className="space-y-5"
          noValidate
          onSubmit={(event) => {
            event.preventDefault()
            setTouched(true)
            if (valid && !save.pending) void save.run(undefined)
          }}
        >
          {master === null ? null : rows.length === 0 ? (
            <p className="m-0 text-xs text-[var(--text-secondary)]">
              There are no current residents to propose slots for.
            </p>
          ) : (
            <div className="space-y-2">
              <div className="text-sm font-bold text-[var(--text-primary)]">
                Household deposit slots — <Rupees paise={master} /> split equally
              </div>
              <div className="overflow-x-auto min-w-0">
                <table className="w-full text-xs border-collapse min-w-[620px]">
                  <caption className="sr-only">Proposed household deposit slots</caption>
                  <thead>
                    <tr className={TABLE_HEAD}>
                      <th scope="col" className="pb-2.5 pr-3 font-bold text-center">Slot</th>
                      <th scope="col" className="pb-2.5 pr-3 font-bold text-center">Slot / Space</th>
                      <th scope="col" className="pb-2.5 pr-3 font-bold text-center">Occupant</th>
                      <th scope="col" className="pb-2.5 font-bold text-center">Target Deposit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {checked.map((row, index) => (
                      <tr key={row.slot_number} className="align-top">
                        <th scope="row" className="py-2 pr-3 pt-4 text-center font-bold text-[var(--text-primary)]">
                          {row.slot_number}
                        </th>
                        <td className="py-2 pr-3">
                          <TextField
                            label={<span className="sr-only">Name of slot {row.slot_number}</span>}
                            value={row.label}
                            maxLength={100}
                            error={row.labelError}
                            onChange={(value) => setRow(index, { label: value })}
                          />
                        </td>
                        <td className="py-2 pr-3">
                          <SelectField
                            label={<span className="sr-only">Occupant of slot {row.slot_number}</span>}
                            value={row.resident}
                            placeholder="Vacant"
                            options={residents.map((resident) => ({
                              value: resident.resident_id,
                              label: resident.display_name,
                            }))}
                            error={row.residentError}
                            onChange={(value) => setRow(index, { resident: value })}
                          />
                        </td>
                        <td className="py-2">
                          <MoneyInput
                            label={
                              <span className="sr-only">Target deposit for slot {row.slot_number}</span>
                            }
                            value={row.amount}
                            hint=""
                            error={row.amountError}
                            onChange={(raw) => setRow(index, { amount: raw })}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p
                className={
                  difference === 0
                    ? 'tabular m-0 text-xs text-[var(--text-secondary)]'
                    : 'tabular m-0 text-xs font-semibold text-[var(--danger)]'
                }
                role="status"
              >
                {difference === 0
                  ? `${formatInr(total)} of ${formatInr(master)} — the slots add up to the deposit.`
                  : difference > 0
                    ? `${formatInr(difference)} over the ${formatInr(master)} deposit. The slots must add up to it exactly.`
                    : `${formatInr(-difference)} short of the ${formatInr(master)} deposit. The slots must add up to it exactly.`}
              </p>
            </div>
          )}

          <div className="flex justify-end">
            <button type="submit" className={BUTTON_PRIMARY} disabled={save.pending}>
              {save.pending ? 'Saving…' : 'Save Deposit Setup'}
            </button>
          </div>
        </form>
      </div>
    </Panel>
  )
}

/* ------------------------------------------------------------ escrow pool -- */

type DialogKind = 'fund' | 'vacate' | 'assign' | 'room'
type Dialog = { kind: DialogKind; slot: DepositSlot } | null

function EscrowPoolCard({
  summary,
  onChanged,
}: {
  summary: HouseholdDepositSummary
  onChanged: () => void
}) {
  const [dialog, setDialog] = useState<Dialog>(null)
  const [detailsId, setDetailsId] = useState<string | null>(null)
  const details = summary.slots.find((slot) => slot.slot_id === detailsId) ?? null
  const fronted = summary.funding_mode === 'primary_signatory'
  // A ₹0 entry (a refund of nothing, say) moves no money, so it is left out.
  const history = summary.slots
    .flatMap((slot) => slot.transactions.map((tx) => ({ tx, slot })))
    .filter(({ tx }) => tx.amount_paise > 0)
    .sort((a, b) => a.tx.tx_date.localeCompare(b.tx.tx_date))
    .slice(-6)
    .reverse()

  return (
    <div className="stack">
      <Panel
        title="Household Escrow Pool"
        icon={<Landmark className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="The landlord's deposit, divided into the household's permanent slots. Held in escrow; not part of any statement, expense or balance."
        actions={summary.funding_mode && <Pill tone="accent">{MODE_LABELS[summary.funding_mode]}</Pill>}
      >
        <section
          aria-label={fronted ? 'Capital exposure' : 'Deposit totals'}
          className="grid grid-cols-2 lg:grid-cols-4 gap-3"
        >
          {fronted ? (
            <>
              <MiniStat
                label="Master Deposit Fronted"
                value={<Rupees paise={summary.master_deposit_paise} />}
                note={`By ${summary.primary_signatory_name ?? 'the signatory'}`}
              />
              <MiniStat
                label="Reimbursements Collected"
                value={<Rupees paise={summary.reimbursements_received_paise} />}
                note={
                  summary.retained_deductions_paise > 0
                    ? `Plus ${formatInr(summary.retained_deductions_paise)} kept back from leavers`
                    : 'From the other occupants'
                }
              />
              <MiniStat
                label="Signatory Capital at Risk"
                value={<Rupees paise={summary.signatory_capital_exposure_paise} tone="accent" />}
                note="Still out of pocket"
              />
            </>
          ) : (
            <>
              <MiniStat
                label="Master Deposit"
                value={<Rupees paise={summary.master_deposit_paise} />}
                note="Held by the landlord"
              />
              <MiniStat
                label="Total Funded"
                value={<Rupees paise={summary.total_funded_paise} />}
                note={`Of ${formatInr(summary.total_target_paise)} across the slots`}
              />
              <MiniStat
                label="Slots"
                value={`${summary.total_slots_count - summary.vacant_slots_count} of ${summary.total_slots_count} occupied`}
                note={
                  summary.vacant_slots_count > 0 ? `${summary.vacant_slots_count} vacant` : 'None vacant'
                }
              />
            </>
          )}
          <MiniStat
            label="Unfunded Gap"
            value={<Rupees paise={summary.unfunded_gap_paise} />}
            note={
              summary.vacant_slots_count > 0
                ? `${summary.vacant_slots_count} slot${summary.vacant_slots_count === 1 ? '' : 's'} vacant`
                : 'Targets not yet paid in'
            }
          />
        </section>

        {!summary.is_balanced && (
          <p className="m-0 text-xs font-semibold text-[var(--danger)]" role="alert">
            The slots add up to {formatInr(summary.total_target_paise)}, not the{' '}
            {formatInr(summary.master_deposit_paise)} deposit on the lease. Bring them back in line.
          </p>
        )}

        <div className="overflow-x-auto min-w-0">
          <table className="w-full table-fixed text-left text-xs min-w-[720px] border-collapse">
            <caption className="sr-only">Household deposit slots</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="w-[16%] pb-3 px-2 font-bold text-center">Slot / Space</th>
                <th scope="col" className="w-[16%] pb-3 px-2 font-bold text-center">Target Deposit</th>
                <th scope="col" className="w-[22%] pb-3 px-2 font-bold text-center">Current Occupant</th>
                <th scope="col" className="w-[15%] pb-3 px-2 font-bold text-center">Funded Status</th>
                <th scope="col" className="w-[15%] pb-3 px-2 font-bold text-center">Paid Date</th>
                <th scope="col" className="w-[16%] pb-3 px-2 font-bold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {summary.slots.map((slot) => {
                const status = STATUS[slot.status]
                return (
                  // The whole row opens the slot's details. The slot's button holds
                  // the keyboard focus, gets it back on close, and its click reaches
                  // the row; the action buttons stop theirs from doing so.
                  <tr
                    key={slot.slot_id}
                    className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                    onClick={() => setDetailsId(slot.slot_id)}
                  >
                    <th scope="row" className="py-2.5 px-2 text-center">
                      <button
                        type="button"
                        aria-haspopup="dialog"
                        className="inline-block max-w-full truncate align-middle rounded font-bold text-[var(--text-primary)] cursor-pointer"
                      >
                        {slot.slot_label}
                      </button>
                    </th>
                    <td className="py-2.5 px-2 text-center font-bold text-[var(--text-primary)]">
                      <Rupees paise={slot.target_deposit_paise} />
                    </td>
                    <td className="py-2.5 px-2">
                      <Occupant slot={slot} />
                    </td>
                    <td className="py-2.5 px-2 text-center">
                      <Pill tone={status.tone}>{status.label}</Pill>
                    </td>
                    <td className="py-2.5 px-2 text-center tabular text-[var(--text-secondary)]">
                      {slot.funded_date ? formatDate(slot.funded_date) : '—'}
                    </td>
                    <td className="py-2.5 px-2 text-center">
                      <SlotActions slot={slot} onAction={(kind) => setDialog({ kind, slot })} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {details && (
          <DepositSlotDetailsModal
            slot={details}
            onClose={() => setDetailsId(null)}
            onChangeRoom={() => {
              setDetailsId(null)
              setDialog({ kind: 'room', slot: details })
            }}
          />
        )}

        {dialog && (
          <SlotDialog
            dialog={dialog}
            summary={summary}
            onClose={() => setDialog(null)}
            onDone={() => {
              setDialog(null)
              onChanged()
            }}
          />
        )}
      </Panel>

      {/* Its own card, not a box inside the pool; left out until money has moved. */}
      {history.length > 0 && <RecentDepositActivity history={history} summary={summary} />}
    </div>
  )
}

/**
 * The latest deposit entries, one row each with who paid whom. A row opens a
 * read-only dialog with how it was paid, the reference and the full note, so
 * the table itself never changes height.
 */
function RecentDepositActivity({
  history,
  summary,
}: {
  history: Array<{ tx: DepositTransaction; slot: DepositSlot }>
  summary: HouseholdDepositSummary
}) {
  const [selected, setSelected] = useState<{ tx: DepositTransaction; slot: DepositSlot } | null>(
    null,
  )
  return (
    <Panel
      title="Recent Deposit Activity"
      icon={<History className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Payments, refunds and deductions recorded across the household's deposit slots."
    >
      <div className="overflow-x-auto min-w-0">
        <table className="w-full table-fixed text-xs min-w-[720px] border-collapse">
          <caption className="sr-only">Recent deposit activity</caption>
          <thead>
            <tr className={TABLE_HEAD}>
              <th scope="col" className="w-[14%] pb-2.5 px-2 font-bold text-center">Date</th>
              <th scope="col" className="w-[13%] pb-2.5 px-2 font-bold text-center">Slot / Space</th>
              <th scope="col" className="w-[21%] pb-2.5 px-2 font-bold text-center">Activity</th>
              <th scope="col" className="w-[18%] pb-2.5 px-2 font-bold text-center">From</th>
              <th scope="col" className="w-[18%] pb-2.5 px-2 font-bold text-center">To</th>
              <th scope="col" className="w-[16%] pb-2.5 px-2 font-bold text-center">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {history.map(({ tx, slot }) => {
              const { from, to } = txParties(tx, summary)
              return (
                // The whole row opens the dialog. The date's button holds the
                // keyboard focus, gets it back on close, and its click reaches the row.
                <tr
                  key={tx.tx_id}
                  className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                  onClick={() => setSelected({ tx, slot })}
                >
                  <td className="py-2.5 px-2 text-center">
                    <button
                      type="button"
                      aria-haspopup="dialog"
                      aria-label={`Details: ${TX_LABELS[tx.tx_type]}, ${slot.slot_label}, ${formatDate(tx.tx_date)}, ${formatInr(tx.amount_paise)}`}
                      className="whitespace-nowrap rounded tabular text-[var(--text-secondary)] cursor-pointer"
                    >
                      {formatDate(tx.tx_date)}
                    </button>
                  </td>
                  <td className="py-2.5 px-2 text-center">
                    <span className="inline-block max-w-full truncate rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 py-0.5 text-[11px] font-semibold text-[var(--text-secondary)] align-middle">
                      {slot.slot_label}
                    </span>
                  </td>
                  <td className="py-2.5 px-2 text-center">
                    <Pill tone={TX_TONES[tx.tx_type]}>{TX_LABELS[tx.tx_type]}</Pill>
                  </td>
                  <td className="py-2.5 px-2 text-center font-semibold text-[var(--text-primary)] truncate" title={from}>
                    {from}
                  </td>
                  <td className="py-2.5 px-2 text-center font-semibold text-[var(--text-primary)] truncate" title={to}>
                    {to}
                  </td>
                  <td className="py-2.5 px-2 text-center tabular font-bold text-[var(--text-primary)]">
                    <Rupees paise={tx.amount_paise} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {selected && (
        <DepositTransactionModal {...selected} summary={summary} onClose={() => setSelected(null)} />
      )}
    </Panel>
  )
}

/** One deposit entry in full, read-only: nothing here can be changed. */
function DepositTransactionModal({
  tx,
  slot,
  summary,
  onClose,
}: {
  tx: DepositTransaction
  slot: DepositSlot
  summary: HouseholdDepositSummary
  onClose: () => void
}) {
  const { from, to } = txParties(tx, summary)
  return (
    <Modal
      title="Deposit Transaction Details"
      onClose={onClose}
      footer={
        <button type="button" className={BUTTON_SECONDARY} onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="space-y-4">
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 flex items-center justify-between gap-3">
          <Pill tone={TX_TONES[tx.tx_type]}>{TX_LABELS[tx.tx_type]}</Pill>
          <Rupees paise={tx.amount_paise} className="text-base font-bold tabular text-[var(--text-primary)]" />
        </div>
        <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">
          <DetailFact label="Date">{formatDate(tx.tx_date)}</DetailFact>
          <DetailFact label="Slot / Space">
            {slot.slot_label} (Slot {slot.slot_number})
          </DetailFact>
          <DetailFact label="From">{from}</DetailFact>
          <DetailFact label="To">{to}</DetailFact>
          <DetailFact label="Payment Method">{txMethod(tx)}</DetailFact>
          <DetailFact label="Reference / UTR">
            {tx.reference ? <span className="font-mono break-all">{tx.reference}</span> : 'Not recorded'}
          </DetailFact>
          <DetailFact label="Notes / Reason" className="sm:col-span-2">
            <span className="whitespace-pre-wrap break-words">{tx.notes || 'None'}</span>
          </DetailFact>
        </dl>
      </div>
    </Modal>
  )
}

/** One fact in a read-only details dialog: the label above, the value below. */
function DetailFact({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={className ? `min-w-0 space-y-1 ${className}` : 'min-w-0 space-y-1'}>
      <dt className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {label}
      </dt>
      <dd className="m-0 text-[var(--text-primary)]">{children}</dd>
    </div>
  )
}

function Occupant({ slot }: { slot: DepositSlot }) {
  if (!slot.assigned_resident_id) {
    return <span className="block text-center text-[var(--text-tertiary)]">Vacant</span>
  }
  const name = slot.assigned_display_name ?? slot.assigned_resident_id
  return (
    <span className="flex items-center gap-2 min-w-0">
      <Avatar name={name} size="sm" />
      <span className="font-bold text-[var(--text-primary)] truncate">{name}</span>
    </span>
  )
}

/** One look for every slot action, so each row reads the same. */
const ACTION_BUTTON =
  'inline-flex items-center gap-1.5 rounded-lg border border-[var(--border-medium)] bg-[var(--bg-surface)] px-2.5 py-1 text-xs font-semibold whitespace-nowrap text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer'

function SlotActions({
  slot,
  onAction,
}: {
  slot: DepositSlot
  onAction: (kind: DialogKind) => void
}) {
  const occupied = slot.assigned_resident_id !== null
  const who = slot.assigned_display_name ?? 'the occupant'
  // Each action has a short name on the button, its full name as the tooltip,
  // and a description naming the slot for screen readers.
  const actions: Array<{
    kind: DialogKind
    text: string
    label: string
    describe: string
    icon: typeof Plus
  }> = []
  if (occupied && slot.outstanding_paise > 0) {
    actions.push({
      kind: 'fund',
      text: 'Pay',
      label: 'Record Payment',
      describe: `Record payment for ${slot.slot_label} from ${who}`,
      icon: Plus,
    })
  }
  // The signatory's own share is settled with the landlord, not here.
  if (occupied && !slot.is_primary_signatory) {
    actions.push({
      kind: 'vacate',
      text: 'Vacate',
      label: 'Vacate & Settle',
      describe: `Vacate and settle ${slot.slot_label}`,
      icon: LogOut,
    })
  }
  // An occupant can be set while the slot is vacant, or swapped while nothing is paid.
  if (!slot.is_primary_signatory && slot.funded_amount_paise === 0) {
    actions.push({
      kind: 'assign',
      text: occupied ? 'Reassign' : 'Assign',
      label: occupied ? 'Reassign Occupant' : 'Assign Occupant',
      describe: `${occupied ? 'Reassign' : 'Assign'} occupant of ${slot.slot_label}`,
      icon: UserPlus,
    })
  }
  // The signatory's own funded slot has nothing to do here.
  if (actions.length === 0) return <span className="text-[var(--text-tertiary)]">—</span>

  return (
    <span className="inline-flex flex-wrap items-center justify-center gap-1.5">
      {actions.map((action) => (
        <button
          key={action.kind}
          type="button"
          className={ACTION_BUTTON}
          onClick={(event) => {
            // The action opens its own dialog, not the row's details.
            event.stopPropagation()
            onAction(action.kind)
          }}
          aria-label={action.describe}
          title={action.label}
        >
          <action.icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          {action.text}
        </button>
      ))}
    </span>
  )
}

function SlotDialog({
  dialog,
  summary,
  onClose,
  onDone,
}: {
  dialog: NonNullable<Dialog>
  summary: HouseholdDepositSummary
  onClose: () => void
  onDone: () => void
}) {
  const props = { slot: dialog.slot, summary, onClose, onDone }
  if (dialog.kind === 'fund') return <FundingModal {...props} />
  if (dialog.kind === 'vacate') return <VacateModal {...props} />
  if (dialog.kind === 'room') return <RoomLinkModal {...props} />
  return <AssignModal {...props} />
}

/**
 * One slot in full, read-only. The room it backs is changed from here; paying,
 * vacating and assigning stay on the row.
 */
function DepositSlotDetailsModal({
  slot,
  onClose,
  onChangeRoom,
}: {
  slot: DepositSlot
  onClose: () => void
  onChangeRoom: () => void
}) {
  const status = STATUS[slot.status]
  const paid = slot.funded_amount_paise > 0
  const name = slot.assigned_display_name
  const amenities =
    slot.room_number === null
      ? '—'
      : [slot.has_attached_washroom && 'Attached Washroom', slot.has_balcony && 'Balcony']
          .filter(Boolean)
          .join(' · ') || 'None'
  // Money paid with nobody else named went to the landlord: the signatory's
  // own share, or a co-tenant paying the landlord directly.
  const paidTo = !paid
    ? '—'
    : slot.is_primary_signatory
      ? 'Direct to landlord'
      : (slot.paid_to_display_name ?? 'Direct to landlord')
  return (
    <Modal
      title="Deposit Slot Details"
      description={`${slot.slot_label} · Slot ${slot.slot_number}`}
      onClose={onClose}
      footer={
        <div className="flex w-full items-center justify-between gap-3">
          <button type="button" className={BUTTON_QUIET} onClick={onChangeRoom}>
            <BedDouble className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            {slot.room_id ? 'Change Room' : 'Link Room'}
          </button>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose}>
            Close
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 flex flex-wrap items-center justify-between gap-3">
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone={status.tone}>{status.label}</Pill>
            {slot.is_primary_signatory && <Pill tone="accent">Primary signatory</Pill>}
          </span>
          <span className="text-xs text-[var(--text-secondary)]">
            Funded{' '}
            <Rupees
              paise={slot.funded_amount_paise}
              className="text-base font-bold tabular text-[var(--text-primary)]"
            />{' '}
            of{' '}
            <Rupees paise={slot.target_deposit_paise} className="font-semibold tabular text-[var(--text-primary)]" />
          </span>
        </div>
        <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">
          <DetailFact label="Slot / Space">
            {slot.slot_label} (Slot {slot.slot_number})
          </DetailFact>
          <DetailFact label="Linked Room">{slot.room_number ?? 'Not linked'}</DetailFact>
          <DetailFact label="Private Amenities">{amenities}</DetailFact>
          <DetailFact label="Current Occupant">
            {name ? (slot.is_primary_signatory ? `${name} (Primary signatory)` : name) : 'Vacant'}
          </DetailFact>
          <DetailFact label="Occupied Since">
            {slot.assigned_since ? formatDate(slot.assigned_since) : '—'}
          </DetailFact>
          <DetailFact label="Outstanding Balance">
            <Rupees paise={slot.outstanding_paise} />
          </DetailFact>
          <DetailFact label="Paid Date">
            {slot.funded_date
              ? formatDate(slot.funded_date)
              : slot.is_primary_signatory
                ? 'Fronted with master deposit'
                : 'Not paid yet'}
          </DetailFact>
          <DetailFact label="Paid To">{paidTo}</DetailFact>
          <DetailFact label="Payment Method">
            {slot.payment_method
              ? METHOD_LABELS[slot.payment_method]
              : slot.is_primary_signatory
                ? 'Master lease deposit'
                : 'Not recorded'}
          </DetailFact>
          <DetailFact label="Reference / UTR">
            {slot.payment_reference ? (
              <span className="font-mono break-all">{slot.payment_reference}</span>
            ) : (
              'Not recorded'
            )}
          </DetailFact>
        </dl>
      </div>
    </Modal>
  )
}

/* ----------------------------------------------------------------- modals -- */

interface SlotModalProps {
  slot: DepositSlot
  summary: HouseholdDepositSummary
  onClose: () => void
  onDone: () => void
}

/** The earliest date a deposit event could plausibly carry. */
const EARLIEST_DATE = '2000-01-01'

function dateError(value: string): string | null {
  if (!value) return 'Enter a date.'
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Enter a valid date.'
  if (value > todayIso()) return 'The date cannot be in the future.'
  if (value < EARLIEST_DATE) return 'That date is too far in the past.'
  return null
}

function ModalFooter({
  pending,
  valid,
  label,
  pendingLabel,
  onClose,
  form,
}: {
  pending: boolean
  valid: boolean
  label: string
  pendingLabel: string
  onClose: () => void
  form: string
}) {
  return (
    <>
      <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={pending}>
        Cancel
      </button>
      <button type="submit" form={form} className={BUTTON_PRIMARY} disabled={!valid || pending}>
        {pending ? pendingLabel : label}
      </button>
    </>
  )
}

function FundingModal({ slot, summary, onClose, onDone }: SlotModalProps) {
  const fronted = summary.funding_mode === 'primary_signatory'
  const amount = useMoney(slot.outstanding_paise)
  const [date, setDate] = useState(todayIso())
  const [method, setMethod] = useState<DepositPaymentMethod>(
    fronted ? 'upi' : 'direct_to_landlord',
  )
  const [reference, setReference] = useState('')

  const amountError = amount.empty
    ? 'Enter the amount paid.'
    : !amount.ok
      ? null // the money input says what is wrong with the text
      : amount.paise <= 0
        ? 'The amount must be more than zero.'
        : amount.paise > slot.outstanding_paise
          ? `That is more than the ${formatInr(slot.outstanding_paise)} still owed.`
          : null
  const paidOnError = dateError(date)
  const valid = amount.ok && !amount.empty && !amountError && !paidOnError

  const save = useMutation(
    () =>
      api.post<HouseholdDepositSummary>(
        `${DEPOSITS}/slots/${encodeURIComponent(slot.slot_id)}/fund`,
        {
          amount_paise: amount.paise,
          paid_date: date,
          payment_method: method,
          payment_reference: reference.trim() || null,
          paid_to_resident_id: fronted ? summary.primary_signatory_id : null,
        },
      ),
    { onSuccess: onDone },
  )

  const methods = (Object.keys(METHOD_LABELS) as DepositPaymentMethod[])
    // The signatory already paid the landlord; occupants reimburse the signatory.
    .filter((value) => !fronted || value !== 'direct_to_landlord')
    .map((value) => ({ value, label: METHOD_LABELS[value] }))

  return (
    <Modal
      title="Record Deposit Payment"
      description={`${slotName(slot)} · ${slot.assigned_display_name} · ${formatInr(slot.outstanding_paise)} of ${formatInr(slot.target_deposit_paise)} still owed${
        fronted ? ` · paid to ${summary.primary_signatory_name}` : ''
      }`}
      onClose={onClose}
      footer={
        <ModalFooter
          form="deposit-funding-form"
          pending={save.pending}
          valid={valid}
          label="Record Payment"
          pendingLabel="Recording…"
          onClose={onClose}
        />
      }
    >
      <form
        id="deposit-funding-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount paid"
            required
            value={amount.raw}
            error={amountError}
            hint="Pre-filled with what is still owed. A part payment is fine."
            onChange={amount.setRaw}
          />
          <DateField
            label="Paid on"
            required
            value={date}
            min={EARLIEST_DATE}
            max={todayIso()}
            onChange={setDate}
            error={paidOnError}
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField<DepositPaymentMethod>
            label="Payment method"
            value={method}
            options={methods}
            onChange={setMethod}
          />
          <TextField
            label="Reference"
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="UPI or bank reference"
          />
        </div>
      </form>
    </Modal>
  )
}

const PATHS: Array<{ value: DepositSettlementPath; title: string; body: string }> = [
  {
    value: 'signatory_refund',
    title: 'Refund the leaver',
    body: 'Their money is handed back, and the slot stands vacant until someone is assigned to it.',
  },
  {
    value: 'direct_transfer',
    title: 'Direct transfer to a replacement',
    body: 'The incoming resident pays the leaver directly and takes the slot over straight away.',
  },
]

function VacateModal({ slot, summary, onClose, onDone }: SlotModalProps) {
  const leaver = slot.assigned_display_name ?? 'The occupant'
  const funded = slot.funded_amount_paise
  // No path is assumed: the operator says how the slot is being settled.
  const [path, setPath] = useState<DepositSettlementPath | ''>('')
  const deductions = useMoney(null)
  const [reason, setReason] = useState('')
  const [incoming, setIncoming] = useState('')
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [touched, setTouched] = useState(false)

  const deducted = deductions.empty ? 0 : deductions.paise
  const refund = funded - deducted
  const direct = path === 'direct_transfer'
  const candidates = summary.unassigned_residents.filter(
    (row) => row.resident_id !== slot.assigned_resident_id,
  )

  const pathError = touched && !path ? 'Choose how the slot is being settled.' : null
  const deductionsError =
    !deductions.empty && deductions.ok && deducted > funded
      ? `Deductions cannot be more than the ${formatInr(funded)} put in.`
      : null
  const reasonError = deducted > 0 && !reason.trim() ? 'Say what the deductions are for.' : null
  const incomingError =
    direct && touched && !incoming ? 'Choose the resident taking the slot over.' : null
  const nothingToHandOver =
    direct && deductions.ok && !deductionsError && refund <= 0
      ? 'Nothing would be handed over. Settle the slot as a refund instead.'
      : null
  const settledOnError = dateError(date)
  // A missing path is pointed out on submit, so it does not disable the button.
  const valid =
    deductions.ok &&
    !deductionsError &&
    !reasonError &&
    !settledOnError &&
    (!direct || (Boolean(incoming) && !nothingToHandOver))

  const refunder =
    summary.funding_mode === 'primary_signatory' ? summary.primary_signatory_name : 'the landlord'

  const save = useMutation(
    () =>
      api.post<HouseholdDepositSummary>(
        `${DEPOSITS}/slots/${encodeURIComponent(slot.slot_id)}/vacate`,
        {
          settlement_path: path,
          deductions_paise: deducted,
          deduction_reason: deducted > 0 ? reason.trim() : null,
          refund_amount_paise: refund,
          refund_date: date,
          refund_reference: reference.trim() || null,
          incoming_resident_id: direct ? incoming : null,
        },
      ),
    { onSuccess: onDone },
  )

  return (
    <Modal
      title="Vacate & Settle"
      description={`${leaver} is leaving ${slotName(slot)}, having put in ${formatInr(funded)}. The slot itself stays. This cannot be undone.`}
      onClose={onClose}
      footer={
        <ModalFooter
          form="deposit-vacate-form"
          pending={save.pending}
          valid={valid}
          label="Confirm Settlement"
          pendingLabel="Settling…"
          onClose={onClose}
        />
      }
    >
      <form
        id="deposit-vacate-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (path && valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <fieldset
          role="radiogroup"
          aria-required="true"
          aria-invalid={pathError ? true : undefined}
          aria-describedby={pathError ? 'deposit-path-error' : undefined}
          className="m-0 border-0 p-0 space-y-2"
        >
          <legend className="p-0 pb-1 text-sm font-bold text-[var(--text-primary)]">
            How is the slot being settled?
          </legend>
          {PATHS.map((option) => (
            <label
              key={option.value}
              className={
                path === option.value
                  ? 'flex items-start gap-3 rounded-xl border border-[var(--accent)] bg-[var(--accent-glow)] p-3 cursor-pointer'
                  : pathError
                    ? 'flex items-start gap-3 rounded-xl border border-[var(--danger)] bg-[var(--bg-elevated)] p-3 cursor-pointer'
                    : 'flex items-start gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 cursor-pointer'
              }
            >
              <input
                type="radio"
                name="deposit-settlement-path"
                className="mt-1 shrink-0"
                value={option.value}
                checked={path === option.value}
                onChange={() => {
                  setTouched(true)
                  setPath(option.value)
                }}
              />
              <span className="min-w-0">
                <span className="block text-sm font-bold text-[var(--text-primary)]">
                  {option.title}
                </span>
                <span className="block text-xs text-[var(--text-secondary)]">{option.body}</span>
              </span>
            </label>
          ))}
          {pathError && (
            <span className="field-error" id="deposit-path-error">
              {pathError}
            </span>
          )}
        </fieldset>

        {direct &&
          (candidates.length === 0 ? (
            <p className="m-0 text-xs font-semibold text-[var(--warning)]">
              Nobody can take this slot over yet. Add the replacement as a resident first; a
              resident who already occupies a slot cannot take a second one.
            </p>
          ) : (
            <SelectField
              label="Incoming resident"
              required
              value={incoming}
              placeholder="Choose a resident"
              options={candidates.map((row) => ({
                value: row.resident_id,
                label: row.display_name,
              }))}
              onChange={setIncoming}
              error={incomingError}
              hint={`Someone other than ${leaver}, who occupies no slot yet.`}
            />
          ))}

        <MoneyInput
          label="Deductions"
          value={deductions.raw}
          error={deductionsError}
          hint="Repairs, repainting or unpaid dues kept back. Leave empty if none."
          onChange={deductions.setRaw}
        />
        {deducted > 0 && (
          <TextAreaField
            label="Reason for deductions"
            required
            value={reason}
            onChange={setReason}
            rows={2}
            error={reasonError}
          />
        )}

        <dl
          className="m-0 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-1.5 text-xs"
          aria-label="Settlement"
        >
          <div className="flex justify-between gap-3">
            <dt className="text-[var(--text-secondary)]">Put in</dt>
            <dd className="m-0 font-semibold">
              <Rupees paise={funded} />
            </dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-[var(--text-secondary)]">Less deductions</dt>
            <dd className="m-0 font-semibold">
              <Rupees paise={deductions.ok ? deducted : null} />
            </dd>
          </div>
          <div className="flex justify-between gap-3 border-t border-[var(--border-subtle)] pt-1.5 text-sm">
            <dt className="font-bold text-[var(--text-primary)]">
              {direct ? `Replacement pays ${leaver}` : `Refund to ${leaver}`}
            </dt>
            <dd className="m-0 font-bold">
              <Rupees
                paise={deductions.ok && !deductionsError ? refund : null}
                tone="accent"
              />
            </dd>
          </div>
        </dl>
        {nothingToHandOver && (
          <p className="m-0 text-xs font-semibold text-[var(--warning)]">{nothingToHandOver}</p>
        )}
        {path === 'signatory_refund' && (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            Refunded by {refunder}. The slot then shows as vacant, an unfunded gap of{' '}
            {formatInr(slot.target_deposit_paise)}, until someone is assigned.
          </p>
        )}
        {direct && deducted > 0 && refund > 0 && !deductionsError && (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            The replacement takes the {formatInr(slot.target_deposit_paise)} slot having paid{' '}
            {formatInr(refund)}, so {formatInr(deducted)} will still be owed on it.
          </p>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <DateField
            label="Settlement date"
            required
            value={date}
            min={EARLIEST_DATE}
            max={todayIso()}
            onChange={setDate}
            error={settledOnError}
          />
          <TextField
            label="Reference"
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="UPI or bank reference"
          />
        </div>
      </form>
    </Modal>
  )
}

function AssignModal({ slot, summary, onClose, onDone }: SlotModalProps) {
  const [resident, setResident] = useState('')
  const [touched, setTouched] = useState(false)
  const occupied = slot.assigned_resident_id !== null
  const candidates = summary.unassigned_residents
  const error = touched && !resident ? 'Choose who is taking the slot.' : null

  const save = useMutation(
    () =>
      api.post<HouseholdDepositSummary>(`${DEPOSITS}/slots`, {
        slots: [
          {
            slot_number: slot.slot_number,
            slot_label: slot.slot_label,
            target_deposit_paise: slot.target_deposit_paise,
            assigned_resident_id: resident,
          },
        ],
      }),
    { onSuccess: onDone },
  )

  return (
    <Modal
      title={occupied ? 'Reassign Occupant' : 'Assign Occupant'}
      description={
        occupied
          ? `${slotName(slot)} is assigned to ${slot.assigned_display_name}, who has paid nothing towards it. Choose who takes it instead.`
          : `${slotName(slot)} is vacant. Whoever takes it owes its ${formatInr(slot.target_deposit_paise)} target.`
      }
      onClose={onClose}
      footer={
        <ModalFooter
          form="deposit-assign-form"
          pending={save.pending}
          valid={candidates.length > 0}
          label="Assign Occupant"
          pendingLabel="Assigning…"
          onClose={onClose}
        />
      }
    >
      <form
        id="deposit-assign-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (resident && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        {candidates.length === 0 ? (
          <p className="m-0 text-xs font-semibold text-[var(--warning)]">
            Every current resident already occupies a slot. Add the new resident first.
          </p>
        ) : (
          <SelectField
            label="New occupant"
            required
            value={resident}
            placeholder="Choose a resident"
            options={candidates.map((row) => ({ value: row.resident_id, label: row.display_name }))}
            onChange={setResident}
            error={error}
            hint="A current resident who occupies no slot."
          />
        )}
      </form>
    </Modal>
  )
}

/* ---------------------------------------------------- resident detail card -- */

/** The slot one resident occupies, for their own page. */
export function ResidentDepositCard({ residentId }: { residentId: string }) {
  const summary = useDepositSummary()
  const [dialog, setDialog] = useState<Dialog>(null)

  return (
    <Panel
      title="Household Deposit Allocation"
      icon={<ShieldCheck className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="The household deposit slot they occupy. Held in escrow; not part of their balance."
    >
      <AsyncSection resource={summary} loadingLabel="Loading deposit…">
        {(data) => {
          const slot = data.slots.find((row) => row.assigned_resident_id === residentId)
          if (!slot) {
            return (
              <p className="m-0 text-xs text-[var(--text-secondary)]">
                {!data.is_lease_configured
                  ? 'There is no lease yet, so no deposit is tracked.'
                  : data.slots.length === 0
                    ? 'The household deposit has not been set up yet.'
                    : 'This resident does not occupy a deposit slot.'}{' '}
                <Link to="/housing?tab=deposits">Open Security Deposits.</Link>
              </p>
            )
          }
          return (
            <>
              <p className="m-0 text-sm font-bold text-[var(--text-primary)]">
                Assigned to Household {slotName(slot)}
              </p>
              <dl className="m-0 space-y-2.5 text-xs">
                <DepositFact label="Target Deposit">
                  <Rupees paise={slot.target_deposit_paise} />
                </DepositFact>
                <DepositFact label="Funded">
                  <Rupees paise={slot.funded_amount_paise} />
                  {slot.outstanding_paise > 0 && (
                    <span className="font-normal text-[var(--text-secondary)]">
                      {' '}
                      · {formatInr(slot.outstanding_paise)} still owed
                    </span>
                  )}
                </DepositFact>
                <DepositFact label="Paid to">
                  <PaidTo slot={slot} />
                </DepositFact>
                <DepositFact label="Status">
                  <Pill tone={STATUS[slot.status].tone}>{STATUS[slot.status].label}</Pill>
                </DepositFact>
              </dl>
              <div className="flex flex-wrap items-center gap-2">
                {slot.outstanding_paise > 0 && !slot.is_primary_signatory && (
                  <button
                    type="button"
                    className={BUTTON_SECONDARY}
                    onClick={() => setDialog({ kind: 'fund', slot })}
                  >
                    <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                    Record Payment
                  </button>
                )}
                {!slot.is_primary_signatory && (
                  <button
                    type="button"
                    className={BUTTON_QUIET}
                    onClick={() => setDialog({ kind: 'vacate', slot })}
                  >
                    <LogOut className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                    Vacate &amp; Settle Refund
                  </button>
                )}
              </div>
              {dialog && (
                <SlotDialog
                  dialog={dialog}
                  summary={data}
                  onClose={() => setDialog(null)}
                  onDone={() => {
                    setDialog(null)
                    summary.reload()
                  }}
                />
              )}
            </>
          )
        }}
      </AsyncSection>
    </Panel>
  )
}

function DepositFact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 min-w-0">
      <dt className="text-[var(--text-secondary)] shrink-0">{label}</dt>
      <dd className="m-0 font-semibold text-[var(--text-primary)] min-w-0">{children}</dd>
    </div>
  )
}

function PaidTo({ slot }: { slot: DepositSlot }) {
  if (slot.is_primary_signatory) return <>The landlord, as primary signatory</>
  if (slot.funded_amount_paise === 0) {
    return <span className="font-normal text-[var(--text-secondary)]">Nothing paid yet</span>
  }
  const parts = [
    slot.paid_to_display_name ?? 'The landlord',
    slot.payment_method ? `via ${METHOD_LABELS[slot.payment_method]}` : null,
    slot.payment_reference ? `(${slot.payment_reference})` : null,
  ].filter(Boolean)
  return <>{parts.join(' ')}</>
}

/** Choose the room a slot backs, or none. A room is backed by one slot at most. */
function RoomLinkModal({ slot, summary, onClose, onDone }: SlotModalProps) {
  const rooms = useResource<RoomList>('/api/property/rooms')
  const [roomId, setRoomId] = useState(slot.room_id ?? '')
  const taken = new Set(
    summary.slots
      .filter((other) => other.slot_id !== slot.slot_id && other.room_id)
      .map((other) => other.room_id),
  )
  const options = (rooms.data?.items ?? [])
    .filter((room) => !taken.has(room.room_id))
    .map((room) => ({
      value: room.room_id,
      label: [
        room.room_number,
        room.has_attached_washroom ? 'attached washroom' : null,
        room.has_balcony ? 'balcony' : null,
      ]
        .filter(Boolean)
        .join(' · '),
    }))
  const unchanged = roomId === (slot.room_id ?? '')

  const save = useMutation(
    () =>
      api.post<HouseholdDepositSummary>(
        `${DEPOSITS}/slots/${encodeURIComponent(slot.slot_id)}/link-room`,
        { room_id: roomId || null },
      ),
    { onSuccess: onDone },
  )

  return (
    <Modal
      title={slot.room_id ? 'Change Room' : 'Link Room'}
      description={`Which room ${slotName(slot)} is the deposit for. A room is backed by one slot at most.`}
      onClose={onClose}
      footer={
        <ModalFooter
          form="deposit-room-form"
          pending={save.pending}
          valid={!unchanged}
          label={roomId ? 'Save Room' : 'Unlink Room'}
          pendingLabel="Saving…"
          onClose={onClose}
        />
      }
    >
      <form
        id="deposit-room-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          if (!unchanged && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <AsyncSection resource={rooms} loadingLabel="Loading rooms…">
          {() =>
            options.length === 0 && !slot.room_id ? (
              <p className="m-0 text-xs text-[var(--text-secondary)]" role="status">
                Every room is already backed by a slot, or no rooms have been added. Add rooms
                under Household &amp; Property.
              </p>
            ) : (
              <SelectField
                label="Room"
                value={roomId}
                onChange={setRoomId}
                placeholder="No room"
                options={options}
                hint="Rooms already backed by another slot are not listed."
              />
            )
          }
        </AsyncSection>
      </form>
    </Modal>
  )
}

/**
 * The deposit the landlord holds, entered here with the rest of the deposit.
 * It can be changed until slots divide it up; after that the slots' targets
 * must keep adding up to it, so it is shown but not edited here.
 */
function DepositAmountForm({
  master,
  onSaved,
}: {
  master: number | null
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  // The first time, the amount is entered here; after that it is shown, and
  // edited in a dialog like every other saved value.
  if (master === null) return <DepositAmountFields master={null} onSaved={onSaved} />
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-4 py-3">
      <span className="text-xs text-[var(--text-secondary)]">
        {DEPOSIT_AMOUNT_TITLE}:{' '}
        <Rupees paise={master} className="font-bold text-[var(--text-primary)]" />
      </span>
      <EditButton label="Edit deposit amount" onClick={() => setEditing(true)} />
      {editing && (
        <Modal
          title={DEPOSIT_AMOUNT_TITLE}
          description="The refundable deposit on the lease. The slots divide it between the rooms."
          onClose={() => setEditing(false)}
        >
          <DepositAmountFields
            master={master}
            onCancel={() => setEditing(false)}
            onSaved={() => {
              setEditing(false)
              onSaved()
            }}
          />
        </Modal>
      )}
    </div>
  )
}

/** Named once, for the line that shows the amount and the dialog that edits it. */
const DEPOSIT_AMOUNT_TITLE = 'Deposit held by the landlord'

function DepositAmountFields({
  master,
  onSaved,
  onCancel,
}: {
  master: number | null
  onSaved: () => void
  onCancel?: () => void
}) {
  const amount = useMoney(master)
  const error = amount.empty
    ? 'Enter the deposit held by the landlord.'
    : !amount.ok
      ? null // the money input says what is wrong with the text
      : amount.paise <= 0
        ? 'The deposit must be more than zero.'
        : null
  const save = useMutation(
    () => api.post('/api/rent/config', { security_deposit_paise: amount.paise }),
    { onSuccess: onSaved },
  )
  return (
    <form
      aria-label="Security deposit amount"
      className={onCancel ? 'stack' : 'flex flex-wrap items-end gap-3'}
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        if (!error && amount.ok && !save.pending) void save.run(undefined)
      }}
    >
      {save.error && <ErrorNotice error={save.error} />}
      <div className={onCancel ? undefined : 'max-w-sm flex-1 min-w-[14rem]'}>
        <MoneyInput
          label="Security Deposit Held by Landlord"
          required
          value={amount.raw}
          placeholder="e.g. 1,50,000"
          onChange={(raw) => amount.setRaw(raw)}
          error={amount.empty && master === null ? null : error}
          hint="The refundable deposit on the lease. The slots below divide it between the rooms."
        />
      </div>
      <div className={onCancel ? 'flex items-center justify-between gap-3' : 'contents'}>
        {onCancel && (
          <button type="button" className={BUTTON_SECONDARY} onClick={onCancel} disabled={save.pending}>
            Cancel
          </button>
        )}
        <button
          type="submit"
          className={BUTTON_PRIMARY}
          disabled={save.pending || Boolean(error) || !amount.ok}
        >
          {save.pending ? 'Saving…' : onCancel ? 'Save Changes' : 'Save Deposit'}
        </button>
      </div>
    </form>
  )
}
