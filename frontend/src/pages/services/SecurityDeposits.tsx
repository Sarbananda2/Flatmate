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
import { clsx } from 'clsx'
import { BedDouble, Landmark, LogOut, Plus, ShieldCheck, UserPlus } from 'lucide-react'
import { Page } from '../../components/Layout'
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

const TX_LABELS: Record<DepositTransaction['tx_type'], string> = {
  funding_payment: 'Paid in',
  move_out_deduction: 'Deducted on leaving',
  move_out_refund: 'Refunded on leaving',
  direct_replacement_transfer: 'Paid by replacement',
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

/** Security Deposit Management: its own screen under Housing & Utilities. */
export default function SecurityDepositsPage() {
  return (
    <Page
      title="Security Deposit Management"
      subtitle="Capital held in escrow with the landlord: funding mode, permanent resident slots, and move-out clearances."
      breadcrumbs={[
        { label: 'Housing & Utilities', to: '/services' },
        { label: 'Security Deposits' },
      ]}
    >
      <SecurityDepositSection />
    </Page>
  )
}

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
                <Link to="/services/rent/config" className={BUTTON_PRIMARY}>
                  Set up lease
                </Link>
              }
            />
          </Panel>
        ) : data.slots.length === 0 ? (
          <DepositSetupCard
            // Remount once the mode is saved, so the form starts from what is stored.
            key={data.funding_mode ?? 'unset'}
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
      <form
        className="space-y-5"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
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

        {master === null ? (
          <p className="m-0 rounded-xl border border-dashed border-[var(--border-medium)] px-4 py-3 text-xs text-[var(--text-secondary)]">
            The lease has no security deposit amount yet, so there is nothing to divide into
            slots. <Link to="/services/rent/config">Enter it in Lease Settings.</Link>
          </p>
        ) : rows.length === 0 ? (
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
  const fronted = summary.funding_mode === 'primary_signatory'
  const history = summary.slots
    .flatMap((slot) => slot.transactions.map((tx) => ({ tx, slot })))
    .sort((a, b) => a.tx.tx_date.localeCompare(b.tx.tx_date))
    .slice(-6)
    .reverse()

  return (
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
              label="Master Deposit Fronted to Landlord"
              value={<Rupees paise={summary.master_deposit_paise} />}
              note={`By ${summary.primary_signatory_name ?? 'the signatory'}`}
            />
            <MiniStat
              label="Total Slot Reimbursements Collected"
              value={<Rupees paise={summary.reimbursements_received_paise} />}
              note={
                summary.retained_deductions_paise > 0
                  ? `Plus ${formatInr(summary.retained_deductions_paise)} kept back from leavers`
                  : 'From the other occupants'
              }
            />
            <MiniStat
              label="Primary Signatory Net Capital at Risk"
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
          label="Vacant / Unfunded Gap"
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
              <th scope="col" className="w-[13%] pb-3 px-2 font-bold text-center">Slot / Space</th>
              <th scope="col" className="w-[13%] pb-3 px-2 font-bold text-center">Target Deposit</th>
              <th scope="col" className="w-[18%] pb-3 px-2 font-bold text-center">Current Occupant</th>
              <th scope="col" className="w-[14%] pb-3 px-2 font-bold text-center">Funded Status</th>
              <th scope="col" className="w-[16%] pb-3 px-2 font-bold text-center">Paid Date &amp; Ref</th>
              <th scope="col" className="w-[26%] pb-3 px-2 font-bold text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {summary.slots.map((slot) => (
              <tr key={slot.slot_id} className="align-middle">
                <th scope="row" className="py-3 px-2 text-left">
                  <span className="block font-bold text-[var(--text-primary)] truncate">
                    {slot.slot_label}
                  </span>
                  <span className="block text-[11px] font-normal text-[var(--text-tertiary)]">
                    Slot {slot.slot_number}
                  </span>
                  <SlotRoom slot={slot} />
                </th>
                <td className="py-3 px-2 text-center font-bold text-[var(--text-primary)]">
                  <Rupees paise={slot.target_deposit_paise} />
                </td>
                <td className="py-3 px-2">
                  <Occupant slot={slot} />
                </td>
                <td className="py-3 px-2 text-center">
                  <FundedStatus slot={slot} />
                </td>
                <td className="py-3 px-2 text-center text-[var(--text-secondary)]">
                  <PaidDetails slot={slot} />
                </td>
                <td className="py-3 px-2 text-center">
                  <SlotActions slot={slot} onAction={(kind) => setDialog({ kind, slot })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {history.length > 0 && (
        <section aria-label="Recent deposit activity" className="space-y-1.5">
          <h3 className="m-0 text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
            Recent deposit activity
          </h3>
          <ul className="m-0 p-0 list-none space-y-1 text-xs text-[var(--text-secondary)]">
            {history.map(({ tx, slot }) => (
              <li key={tx.tx_id} className="flex flex-wrap items-baseline justify-between gap-x-3">
                <span className="min-w-0">
                  <span className="tabular">{formatDate(tx.tx_date)}</span> · {slot.slot_label} ·{' '}
                  {TX_LABELS[tx.tx_type]}
                  {tx.resident_display_name && <> · {tx.resident_display_name}</>}
                  {tx.notes && <> · {tx.notes}</>}
                </span>
                <Rupees paise={tx.amount_paise} className="font-semibold text-[var(--text-primary)]" />
              </li>
            ))}
          </ul>
        </section>
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
  )
}

function Occupant({ slot }: { slot: DepositSlot }) {
  if (!slot.assigned_resident_id) {
    return <span className="block text-center text-[var(--text-tertiary)]">Vacant</span>
  }
  const name = slot.assigned_display_name ?? slot.assigned_resident_id
  return (
    <span className="flex items-center gap-2.5 min-w-0">
      <Avatar name={name} />
      <span className="min-w-0">
        <Link
          className="block font-semibold text-[var(--text-primary)] truncate"
          to={`/residents/${slot.assigned_resident_id}`}
        >
          {name}
        </Link>
        {slot.is_primary_signatory && (
          <span className="block text-[11px] text-[var(--text-tertiary)]">Primary signatory</span>
        )}
      </span>
    </span>
  )
}

function FundedStatus({ slot }: { slot: DepositSlot }) {
  const status = STATUS[slot.status]
  return (
    <>
      <Pill tone={status.tone}>{status.label}</Pill>
      {slot.assigned_resident_id && (
        <span className="block tabular text-[11px] text-[var(--text-secondary)] mt-1">
          {formatInr(slot.funded_amount_paise)}
          {slot.outstanding_paise > 0 && <> · {formatInr(slot.outstanding_paise)} owed</>}
        </span>
      )}
    </>
  )
}

function PaidDetails({ slot }: { slot: DepositSlot }) {
  if (!slot.funded_date && !slot.payment_method) return <span>—</span>
  return (
    <>
      <span className="tabular block">{slot.funded_date ? formatDate(slot.funded_date) : '—'}</span>
      <span className="block text-[11px] truncate">
        {[slot.payment_method && METHOD_LABELS[slot.payment_method], slot.payment_reference]
          .filter(Boolean)
          .join(' · ')}
      </span>
    </>
  )
}

function SlotActions({
  slot,
  onAction,
}: {
  slot: DepositSlot
  onAction: (kind: DialogKind) => void
}) {
  const occupied = slot.assigned_resident_id !== null
  const who = slot.assigned_display_name ?? 'the occupant'
  const actions: Array<{
    kind: DialogKind
    label: string
    describe: string
    icon: typeof Plus
    className: string
  }> = []
  if (occupied && slot.outstanding_paise > 0) {
    actions.push({
      kind: 'fund',
      label: 'Record Payment',
      describe: `Record payment for ${slot.slot_label} from ${who}`,
      icon: Plus,
      className: BUTTON_SECONDARY,
    })
  }
  // The signatory's own share is settled with the landlord, not here.
  if (occupied && !slot.is_primary_signatory) {
    actions.push({
      kind: 'vacate',
      label: 'Vacate & Settle',
      describe: `Vacate and settle ${slot.slot_label}`,
      icon: LogOut,
      className: BUTTON_QUIET,
    })
  }
  // An occupant can be set while the slot is vacant, or swapped while nothing is paid.
  if (!slot.is_primary_signatory && slot.funded_amount_paise === 0) {
    actions.push({
      kind: 'assign',
      label: occupied ? 'Reassign Occupant' : 'Assign Occupant',
      describe: `${occupied ? 'Reassign' : 'Assign'} occupant of ${slot.slot_label}`,
      icon: UserPlus,
      className: BUTTON_QUIET,
    })
  }
  actions.push({
    kind: 'room',
    label: slot.room_id ? 'Change Room' : 'Link Room',
    describe: `${slot.room_id ? 'Change the room for' : 'Link a room to'} ${slot.slot_label}`,
    icon: BedDouble,
    className: BUTTON_QUIET,
  })

  // Always one line, so every row is the same height. A lone action has room
  // for its name; several do not, so they show as icons, each still named for
  // screen readers and on hover, and the first is spelled out on wide screens.
  const alone = actions.length === 1
  return (
    <span className="inline-flex flex-nowrap items-center justify-center gap-1.5 whitespace-nowrap">
      {actions.map((action, index) => (
        <button
          key={action.kind}
          type="button"
          className={clsx(action.className, 'whitespace-nowrap', !alone && 'px-2.5')}
          onClick={() => onAction(action.kind)}
          aria-label={action.describe}
          title={action.label}
        >
          <action.icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          <span className={alone ? undefined : index === 0 ? 'sr-only xl:not-sr-only' : 'sr-only'}>
            {action.label}
          </span>
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

/** The room a slot backs, with its amenities, under the slot's name. */
function SlotRoom({ slot }: { slot: DepositSlot }) {
  if (!slot.room_number) return null
  const amenities = [
    slot.has_attached_washroom && 'attached washroom',
    slot.has_balcony && 'balcony',
  ].filter(Boolean)
  return (
    <span
      className="mt-1 inline-flex items-center gap-1 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-2 py-0.5 text-[10px] font-semibold text-[var(--text-secondary)]"
      title={amenities.length ? `With ${amenities.join(' and ')}` : 'No private amenities'}
    >
      <BedDouble className="w-3 h-3 shrink-0" aria-hidden="true" />
      {slot.room_number}
      {slot.has_attached_washroom && <span aria-label="attached washroom">🚿</span>}
      {slot.has_balcony && <span aria-label="balcony">🌅</span>}
    </span>
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
                <Link to="/services/deposits">Open Security Deposits.</Link>
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
