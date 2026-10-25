/**
 * Rent under a single master lease, split equally.
 *
 * Two flows of money are shown side by side and never merged: what residents
 * have paid into the pool against their rent share, and whether the landlord
 * has been paid. Every figure comes from the backend; nothing here divides
 * money.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Building2,
  Check,
  Clock,
  CreditCard,
  Settings,
  Users,
} from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { RentAgreementCard } from './RentAgreement'
import { AsyncSection, Callout, ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, MoneyInput, SelectField, TextField } from '../../components/Form'
import {
  ResidentCollectionTable,
  type CollectionStatus,
  type ResidentCollectionItem,
} from '../../components/ResidentCollectionTable'
import { useToast } from '../../components/Toast'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  EmptyBlock,
  MiniStat,
  Panel,
  Pill,
  Rupees,
  TransferDestination,
  type Tone,
} from '../../components/ui'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { LeaseNoticeBanner } from './LeaseLifecycle'
import { formatDate, formatInr, formatMonth, paiseToInput, todayIso } from '../../lib/format'
import type {
  Apartment,
  RentCollectionStatus,
  RentCycle,
  RentDisbursement,
  RentDisbursementMode,
  RentDisbursementStatus,
  RentLeaseConfig,
  RentMonthOverview,
  RentParticipant,
  RentPreview,
  Resident,
} from '../../api/types'

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold'

export default function RentPage() {
  const { month, apartment } = useApp()
  return <RentMonth key={month} month={month} apartment={apartment} />
}

/** A share of the whole to one decimal place, for display only. */
function percent(part: number, whole: number): string {
  if (whole <= 0) return '0.0%'
  const tenths = Math.round((part * 1000) / whole)
  // Rounding must never make an incomplete collection read as complete.
  return `${((part < whole ? Math.min(tenths, 999) : tenths) / 10).toFixed(1)}%`
}

/**
 * How long a payment has been past its due date, as `13 days overdue`, or
 * null when it is not yet due. Calendar days, so no time zone can shift it.
 */
function overdueNotice(dueDate: string | null | undefined): string | null {
  if (!dueDate) return null
  const day = (iso: string) => {
    const [year, month, date] = iso.split('-').map(Number)
    return Date.UTC(year, month - 1, date) / 86_400_000
  }
  const days = day(todayIso()) - day(dueDate)
  if (!(days > 0)) return null
  return days === 1 ? '1 day overdue' : `${days} days overdue`
}

export function RentMonth({ month, apartment = null }: { month: string; apartment?: Apartment | null }) {
  const toast = useToast()
  // An open month is charged by itself and follows the lease, so there is
  // nothing to generate here: the month shows its live shares, or a preview.
  const overview = useResource<RentMonthOverview>(`/api/rent/cycle/${month}`)
  const [dialog, setDialog] = useState<'payout' | null>(null)
  const [payingFor, setPayingFor] = useState<RentParticipant | null>(null)
  const [viewingStatement, setViewingStatement] = useState<RentParticipant | null>(null)
  const [viewingLandlordStatement, setViewingLandlordStatement] = useState(false)

  return (
    <AsyncSection resource={overview} loadingLabel="Loading rent…">
      {(data) => {
        const { config, cycle, preview } = data
        return (
          <Page
            title="Rent"
            subtitle="What residents have paid towards this month's rent, and whether the landlord has been paid, under the master lease."
            breadcrumbs={[{ label: 'Services & Utilities', to: '/services' }, { label: 'Rent' }]}
          >
            {!config ? (
              <Panel
                title="Set up the lease"
                icon={<Building2 className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
              >
                <EmptyBlock
                  title="No lease on file yet"
                  body="Enter the landlord and the monthly rent once. Each month's rent is then split equally between the residents taking part."
                  action={
                    <Link to="/housing?tab=lease" className={BUTTON_PRIMARY}>
                      <Settings className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                      Set up lease
                    </Link>
                  }
                />
              </Panel>
            ) : (
              <>
                <LeaseNoticeBanner
                  config={config}
                  participantCount={(cycle ?? preview)?.participant_count ?? 0}
                  onChanged={(message, detail) => {
                    toast.success(message, detail)
                    overview.reload()
                  }}
                />
                <Metrics config={config} cycle={cycle} preview={preview} month={month} />
                {/* The month's two steps, in order: collect from residents, then
                    pay the landlord. */}
                {cycle ? (
                  <CyclePanel
                    cycle={cycle}
                    config={config}
                    blockedReason={data.live_blocked_reason ?? null}
                    onPay={setPayingFor}
                    onViewStatement={setViewingStatement}
                  />
                ) : (
                  preview && <PreviewPanel month={month} preview={preview} />
                )}
                <LandlordPanel
                  config={config}
                  cycle={cycle}
                  onPayout={() => setDialog('payout')}
                  onViewStatement={() => setViewingLandlordStatement(true)}
                />
                {/* The contract the rent is charged under, for reference. */}
                <RentAgreementCard
                  config={config}
                  cycle={cycle}
                  preview={preview}
                  roomTiered={data.room_tiered ?? preview?.room_tiered ?? null}
                  month={month}
                  onSaved={overview.reload}
                />
              </>
            )}

            {dialog === 'payout' && cycle && config && (
              <PayoutModal
                month={month}
                cycle={cycle}
                config={config}
                onClose={() => setDialog(null)}
                onRecorded={(updated) => {
                  setDialog(null)
                  toast.success(
                    'Landlord payout recorded',
                    updated.warnings?.[0] ??
                      (updated.disbursement_status === 'partially_disbursed'
                        ? `${formatInr(updated.remaining_disbursement_paise)} is still owed to the landlord.`
                        : undefined),
                  )
                  overview.reload()
                }}
              />
            )}
            {viewingLandlordStatement && cycle && config && (
              <LandlordStatementModal
                month={month}
                config={config}
                cycle={cycle}
                onClose={() => setViewingLandlordStatement(false)}
              />
            )}
            {viewingStatement && cycle && (
              <RentStatementModal
                month={month}
                dueDate={cycle.due_date}
                // Read from the month as it now stands, not as it was when opened.
                participant={
                  cycle.participants.find(
                    (row) => row.resident_id === viewingStatement.resident_id,
                  ) ?? viewingStatement
                }
                disbursements={cycle.disbursements}
                onClose={() => setViewingStatement(null)}
              />
            )}
            {payingFor && cycle && (
              <RentPaymentModal
                month={month}
                participant={payingFor}
                apartment={apartment}
                onClose={() => setPayingFor(null)}
                onRecorded={(updated) => {
                  const name = payingFor.display_name
                  const row = updated.participants.find(
                    (item) => item.resident_id === payingFor.resident_id,
                  )
                  setPayingFor(null)
                  toast.success(
                    row?.collection_status === 'paid'
                      ? `${name}'s rent is paid`
                      : `Payment from ${name} recorded`,
                    row && row.outstanding_paise > 0
                      ? `${formatInr(row.outstanding_paise)} of this month's share is still owed.`
                      : updated.payment && updated.payment.unapplied_credit_paise > 0
                        ? `${formatInr(updated.payment.unapplied_credit_paise)} is left as unapplied credit.`
                        : undefined,
                  )
                  overview.reload()
                }}
              />
            )}
          </Page>
        )
      }}
    </AsyncSection>
  )
}

function Metrics({
  config,
  cycle,
  preview,
  month,
}: {
  config: RentLeaseConfig
  cycle: RentCycle | null
  preview: RentPreview | null
  month: string
}) {
  const source = cycle ?? preview
  const total = source?.total_rent_paise ?? config.monthly_rent_paise
  const count = source?.participant_count ?? 0
  const dueDate =
    source?.due_date ?? `${month}-${String(config.due_day_of_month).padStart(2, '0')}`
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
      <MiniStat
        label="Contract Rent"
        value={<Rupees paise={total} />}
        note={
          cycle && !cycle.matches_lease_rent
            ? `Lease now says ${formatInr(cycle.lease_rent_paise)}`
            : 'Single master lease'
        }
      />
      <MiniStat
        label={count > 0 ? `Equal Share (1/${count})` : 'Equal Share'}
        value={<Rupees paise={source ? source.base_share_paise : null} />}
        note={
          count === 0
            ? 'Nobody taking part'
            : source && source.remainder_paise > 0
              ? `${count} taking part · ${source.remainder_paise} paise over`
              : `${count} taking part`
        }
      />
      <MiniStat
        label="Internal Collection"
        value={<Rupees paise={cycle ? cycle.collected_paise : null} />}
        note={
          cycle
            ? `${cycle.collected_count} of ${cycle.participant_count} covered · ${percent(cycle.collected_paise, cycle.total_rent_paise)}`
            : 'Starts once the month begins'
        }
      />
      <MiniStat
        label="Landlord Due Date"
        value={<span className="tabular">{formatDate(dueDate)}</span>}
        note={
          cycle?.disbursement_status === 'disbursed'
            ? `Paid ${formatDate(cycle.disbursed_at)}`
            : cycle?.disbursement_status === 'partially_disbursed'
              ? `${formatInr(cycle.remaining_disbursement_paise)} still to pay`
              : config.landlord_name
        }
      />
    </div>
  )
}

const COLLECTION: Record<RentCollectionStatus, { tone: Tone; label: string }> = {
  paid: { tone: 'success', label: 'Paid' },
  partial: { tone: 'warning', label: 'Part paid' },
  unpaid: { tone: 'warning', label: 'Unpaid' },
  funded_payout: { tone: 'success', label: 'Paid' },
}

function CyclePanel({
  cycle,
  config,
  blockedReason,
  onPay,
  onViewStatement,
}: {
  cycle: RentCycle
  config: RentLeaseConfig
  /** Why the open month cannot follow the lease as it stands: it stays as it was. */
  blockedReason: string | null
  onPay: (participant: RentParticipant) => void
  onViewStatement: (participant: RentParticipant) => void
}) {
  // Rent is due by the landlord's due date; a share still owed after it is overdue.
  const overdue = overdueNotice(cycle.due_date)
  const items: ResidentCollectionItem[] = cycle.participants.map((participant) => {
    const funded = participant.collection_status === 'funded_payout'
    const settled = funded || participant.collection_status === 'paid'
    // Paying the landlord directly counts towards their share, as paying into the kitty does.
    const coveredPaise = Math.min(participant.share_paise, participant.settled_paise + participant.advanced_paise)
    const stillOwedPaise = Math.max(0, participant.share_paise - coveredPaise)
    const status: CollectionStatus = settled
      ? 'paid'
      : participant.collection_status === 'partial'
        ? 'partial'
        : 'unpaid'
    return {
      id: participant.resident_id,
      name: participant.display_name,
      href: `/residents/${participant.resident_id}`,
      sharePaise: participant.share_paise,
      paidIntoKittyPaise: coveredPaise,
      stillOwedPaise,
      overdueNotice: stillOwedPaise > 0 && overdue ? overdue : undefined,
      status,
      canPay: !settled && participant.outstanding_paise > 0,
      onPay: () => onPay(participant),
      onViewStatement: () => onViewStatement(participant),
    }
  })
  return (
    <Panel
      title="Resident Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each resident owes for this month's rent, and how much of it has been collected into the House Kitty."
    >
      {blockedReason && (
        <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
          {blockedReason} This month stays as charged until it does.
        </p>
      )}
      {!blockedReason && !cycle.matches_lease_rent && (
        <p className="m-0 text-xs text-[var(--warning)]">
          This month was charged at {formatInr(cycle.total_rent_paise)}; the lease now says{' '}
          {formatInr(cycle.lease_rent_paise ?? config.monthly_rent_paise)}. A closed month keeps its own amount.
        </p>
      )}

      <ResidentCollectionTable caption="Rent shares and collection" items={items} />

      {cycle.excluded_residents.length > 0 && (
        <p className="m-0 text-[11px] text-[var(--text-secondary)]">
          Not taking part this month:{' '}
          {cycle.excluded_residents.map((resident) => resident.display_name).join(', ')}.
        </p>
      )}
    </Panel>
  )
}

function PreviewPanel({ month, preview }: { month: string; preview: RentPreview }) {
  // The split follows the agreement model; it is changed on the agreement card.
  const tiered = preview.split_mode === 'room_tiered'
  const rooms = new Map(preview.room_tiered.shares.map((row) => [row.resident_id, row.room_number]))
  const items: ResidentCollectionItem[] = preview.participants.map((row) => ({
    id: row.resident_id,
    name: row.display_name,
    href: `/residents/${row.resident_id}`,
    subtitle: tiered ? (rooms.get(row.resident_id) ?? 'No room') : undefined,
    sharePaise: row.share_paise,
    // Nothing is owed, or can be paid, until the month is charged.
    paidIntoKittyPaise: null,
    stillOwedPaise: null,
    status: null,
    canPay: false,
  }))
  return (
    <Panel
      title={`Resident Collection · ${formatMonth(month)}`}
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={
        tiered
          ? "Each resident taking part is charged their room's base rent. A partial month never reduces a share."
          : 'Everyone taking part is charged a full equal share. A partial month never reduces a share.'
      }
    >
      {preview.blocked_reason && (
        <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
          {preview.blocked_reason}
        </p>
      )}
      {items.length > 0 && (
        <ResidentCollectionTable caption={`Rent shares for ${formatMonth(month)}`} items={items} />
      )}
      {preview.excluded_residents.length > 0 && (
        <p className="m-0 text-[11px] text-[var(--text-secondary)]">
          Not taking part:{' '}
          {preview.excluded_residents.map((resident) => resident.display_name).join(', ')}.
        </p>
      )}
    </Panel>
  )
}

function maskAccount(account: string): string {
  return account.length > 4 ? `****${account.slice(-4)}` : account
}

/** The landlord's side of the month, worded as the collection table words a resident's. */
const PAYOUT: Record<RentDisbursementStatus, { tone: Tone; label: string }> = {
  pending: { tone: 'warning', label: 'Unpaid' },
  partially_disbursed: { tone: 'warning', label: 'Part paid' },
  disbursed: { tone: 'success', label: 'Paid' },
}

function LandlordPanel({
  config,
  cycle,
  onPayout,
  onViewStatement,
}: {
  config: RentLeaseConfig
  cycle: RentCycle | null
  onPayout: () => void
  onViewStatement: () => void
}) {
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  const tranches = cycle?.disbursements ?? []
  const advanced = tranches.some((tranche) => tranche.disbursed_by === 'resident_advance')
  // Open until the landlord has been paid in full: the rent can go in tranches.
  const owing = cycle != null && cycle.disbursement_status !== 'disbursed'
  const status = cycle ? PAYOUT[cycle.disbursement_status] : null
  const overdue = overdueNotice(cycle?.due_date)
  const beneficiary = config.payout_beneficiary_name || config.landlord_name
  const bank = [
    config.landlord_bank_ifsc,
    config.landlord_bank_account && maskAccount(config.landlord_bank_account),
  ]
    .filter(Boolean)
    .join(' · ')
  const destination = [
    beneficiary !== config.landlord_name ? `To ${beneficiary}` : null,
    config.landlord_upi_id || bank || 'No bank account or UPI ID on file',
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <Panel
      title="Landlord Payout"
      icon={<Building2 className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What is owed to the landlord, and payouts made to them. Select the row for each payment made."
    >
      {/* Laid out as the collection table above, so the two read column for
          column: what was due, what has been paid, what is still owed. */}
      <div className="overflow-x-auto min-w-0">
        <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
          <caption className="sr-only">Rent owed to the landlord</caption>
          <thead>
            <tr className={TABLE_HEAD}>
              <th scope="col" className="w-[28%] pb-3 pr-3 font-bold text-center">
                Landlord / Destination
              </th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Total Rent</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Paid to Owner</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Still Owed</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Status</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {/* Once the month is generated, the row opens the landlord's payout
                statement, as a resident's row opens theirs; the button in it
                does its own thing instead. */}
            <tr
              className={
                cycle
                  ? 'align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors'
                  : 'align-middle'
              }
              {...(cycle && {
                tabIndex: 0,
                title: 'View the landlord payout statement',
                onClick: onViewStatement,
                onKeyDown: (event: React.KeyboardEvent<HTMLTableRowElement>) => {
                  if (event.target !== event.currentTarget) return
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onViewStatement()
                  }
                },
              })}
            >
              <th scope="row" className="py-3 pr-3 text-left font-normal">
                <span className="flex items-center gap-2.5 min-w-0">
                  <Avatar name={config.landlord_name} />
                  <span className="min-w-0">
                    <span className="block font-semibold text-[var(--text-primary)] truncate">
                      {config.landlord_name}
                    </span>
                    <span
                      className="block text-[11px] text-[var(--text-tertiary)] tabular truncate"
                      title={destination}
                    >
                      {destination}
                    </span>
                  </span>
                </span>
              </th>
              <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                <Rupees paise={cycle ? cycle.total_rent_paise : config.monthly_rent_paise} />
              </td>
              <td className="py-3 pr-3 text-center">
                {cycle ? <Rupees paise={cycle.disbursed_paise} /> : dash}
              </td>
              <td className="py-3 pr-3 text-center">
                {cycle ? (
                  <>
                    <Rupees paise={cycle.remaining_disbursement_paise} />
                    {cycle.remaining_disbursement_paise > 0 && overdue && (
                      <span className="block text-[10px] font-semibold text-[var(--danger)] tabular">
                        {overdue}
                      </span>
                    )}
                  </>
                ) : (
                  dash
                )}
              </td>
              <td className="py-3 pr-3 text-center whitespace-nowrap">
                {cycle && status ? (
                  <Pill tone={status.tone}>
                    {cycle.disbursement_status === 'disbursed' ? (
                      <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
                    ) : (
                      <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
                    )}
                    {status.label}
                  </Pill>
                ) : (
                  <Pill>Not billed</Pill>
                )}
              </td>
              <td className="py-3 text-center whitespace-nowrap">
                {/* Always there, as on each resident's row; off until the month is
                    charged and once the landlord has been paid in full. */}
                <button
                  type="button"
                  className={BUTTON_SECONDARY}
                  disabled={!owing || !cycle}
                  onClick={(event) => {
                    event.stopPropagation()
                    onPayout()
                  }}
                  aria-label="Pay now to landlord"
                >
                  <CreditCard className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Pay Now
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {advanced && (
        <p className="m-0 text-[11px] text-[var(--text-secondary)]">
          A resident who paid the landlord directly is credited what they paid as a provider
          advance, and is still charged their own share.
        </p>
      )}
    </Panel>
  )
}

/* --------------------------------------------------- rent statement modal -- */

/** One payment towards a share: into the kitty, or the part of a direct payment to the landlord that covered it. */
interface StatementEntry {
  id: string
  date: string
  amount_paise: number
  modeLabel: string | null
  reference: string | null
}

/** One resident's rent for the month: what they owe, and every payment towards it. */
function RentStatementModal({
  month,
  dueDate,
  participant,
  disbursements,
  onClose,
}: {
  month: string
  dueDate: string | null
  participant: RentParticipant
  disbursements: RentDisbursement[]
  onClose: () => void
}) {
  const status = COLLECTION[participant.collection_status]
  const funded = participant.collection_status === 'funded_payout'
  // Paying the landlord directly counts towards their share, as paying into the kitty does.
  const coveredPaise = Math.min(participant.share_paise, participant.settled_paise + participant.advanced_paise)
  const stillOwedPaise = Math.max(0, participant.share_paise - coveredPaise)
  const overdue = stillOwedPaise > 0 ? overdueNotice(dueDate) : null
  const modes = new Map(PAYMENT_MODES.map((mode) => [mode.value, mode.label]))
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  const history: StatementEntry[] = participant.payments.map((payment) => ({
    id: payment.payment_id,
    date: payment.payment_date,
    amount_paise: payment.amount_paise,
    modeLabel: payment.payment_mode ? (modes.get(payment.payment_mode) ?? null) : null,
    reference: payment.reference,
  }))
  // What paying the landlord directly covered of the share, tranche by tranche.
  let advanceCover = Math.max(0, coveredPaise - participant.settled_paise)
  for (const tranche of disbursements) {
    if (advanceCover <= 0) break
    if (tranche.disbursed_by === 'resident_advance' && tranche.funding_resident_id === participant.resident_id) {
      const amount = Math.min(tranche.amount_paise, advanceCover)
      advanceCover -= amount
      history.push({
        id: tranche.disbursement_id,
        date: tranche.disbursed_at,
        amount_paise: amount,
        modeLabel: 'Direct to Landlord',
        reference: tranche.disbursement_reference,
      })
    }
  }
  if (advanceCover > 0) {
    // An advance recorded before the month's payouts were kept as tranches.
    history.push({
      id: 'advance-cover',
      date: participant.last_payment_date ?? `${month}-01`,
      amount_paise: advanceCover,
      modeLabel: 'Direct to Landlord',
      reference: null,
    })
  }
  history.sort((a, b) => a.date.localeCompare(b.date))
  return (
    <Modal
      title="Rent Statement"
      description={`${participant.display_name} · ${formatMonth(month)}`}
      onClose={onClose}
    >
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat label="Share" value={<Rupees paise={participant.share_paise} />} />
          <MiniStat label="Paid into Kitty" value={<Rupees paise={coveredPaise} />} />
          <MiniStat
            label="Still Owed"
            value={<Rupees paise={stillOwedPaise} />}
            note={
              funded ? (
                'Covered by paying the landlord'
              ) : overdue ? (
                <span className="font-semibold text-[var(--danger)]">{overdue}</span>
              ) : undefined
            }
          />
        </div>

        {participant.advanced_paise > 0 && (
          <Callout>
            {participant.display_name} paid {formatInr(participant.advanced_paise)} to the
            landlord directly this month
            {funded ? ', which covers their share' : ''}.
          </Callout>
        )}

        <section aria-label="Payment history" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Payment History</h3>
            <Pill tone={status.tone}>
              {participant.collection_status === 'paid' || funded ? (
                <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
              ) : (
                <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
              )}
              {status.label}
            </Pill>
          </div>
          {history.length === 0 ? (
            <EmptyBlock title="No payments recorded yet for this month." />
          ) : (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[480px] border-collapse">
                <caption className="sr-only">
                  Payments towards {participant.display_name}'s rent
                </caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Date</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Amount</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Mode</th>
                    <th scope="col" className="pb-3 font-bold text-center">Reference / UTR</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {history.map((entry) => (
                    <tr key={entry.id} className="align-middle">
                      <td className="py-2.5 pr-3 text-center tabular">{formatDate(entry.date)}</td>
                      <td className="py-2.5 pr-3 text-center font-bold text-[var(--text-primary)]">
                        <Rupees paise={entry.amount_paise} />
                      </td>
                      <td className="py-2.5 pr-3 text-center">{entry.modeLabel ?? dash}</td>
                      <td className="py-2.5 text-center tabular break-all text-[var(--text-primary)]">
                        {entry.reference || dash}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </Modal>
  )
}

/* ----------------------------------------------- landlord statement modal -- */

/** The landlord's side of the month: what is due to them, and every payment made. */
function LandlordStatementModal({
  month,
  config,
  cycle,
  onClose,
}: {
  month: string
  config: RentLeaseConfig
  cycle: RentCycle
  onClose: () => void
}) {
  const status = PAYOUT[cycle.disbursement_status]
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  return (
    <Modal
      title="Landlord Payout Statement"
      description={`${config.landlord_name} · ${formatMonth(month)}`}
      onClose={onClose}
    >
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat label="Total Rent" value={<Rupees paise={cycle.total_rent_paise} />} />
          <MiniStat label="Paid to Owner" value={<Rupees paise={cycle.disbursed_paise} />} />
          <MiniStat
            label="Still Owed"
            value={<Rupees paise={cycle.remaining_disbursement_paise} />}
          />
        </div>

        <section aria-label="Disbursement history" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">
              Disbursement History
            </h3>
            <Pill tone={status.tone}>
              {cycle.disbursement_status === 'disbursed' ? (
                <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
              ) : (
                <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
              )}
              {status.label}
            </Pill>
          </div>
          {cycle.disbursements.length === 0 ? (
            <EmptyBlock title="No payouts recorded yet for this month." />
          ) : (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[560px] border-collapse">
                <caption className="sr-only">Payments to the landlord</caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Date</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Amount</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Funded From</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Reference / UTR</th>
                    <th scope="col" className="pb-3 font-bold text-center">Note</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {cycle.disbursements.map((tranche) => (
                    <tr key={tranche.disbursement_id} className="align-middle">
                      <td className="py-2.5 pr-3 text-center tabular">
                        {formatDate(tranche.disbursed_at)}
                      </td>
                      <td className="py-2.5 pr-3 text-center font-bold text-[var(--text-primary)]">
                        <Rupees paise={tranche.amount_paise} />
                      </td>
                      <td className="py-2.5 pr-3 text-center">
                        {tranche.disbursed_by === 'pool'
                          ? 'House Kitty'
                          : `${tranche.funding_resident_name ?? tranche.funding_resident_id} (advance)`}
                      </td>
                      <td className="py-2.5 pr-3 text-center tabular break-all">
                        {tranche.disbursement_reference}
                      </td>
                      <td className="py-2.5 text-center text-[var(--text-secondary)]">
                        {tranche.note ?? dash}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </Modal>
  )
}

/* ----------------------------------------------------- rent payment modal -- */

type PaymentMode = 'upi' | 'bank_transfer' | 'cash' | 'other'

const PAYMENT_MODES: Array<{ value: PaymentMode; label: string }> = [
  { value: 'upi', label: 'UPI' },
  { value: 'bank_transfer', label: 'Bank transfer' },
  { value: 'cash', label: 'Cash' },
  { value: 'other', label: 'Other' },
]

function RentPaymentModal({
  month,
  participant,
  apartment,
  onClose,
  onRecorded,
}: {
  month: string
  participant: RentParticipant
  apartment: Apartment | null
  onClose: () => void
  onRecorded: (cycle: RentCycle) => void
}) {
  const owed = participant.outstanding_paise
  const [amount, setAmount] = useState(paiseToInput(owed))
  const [amountPaise, setAmountPaise] = useState(owed)
  const [amountOk, setAmountOk] = useState(true)
  const [date, setDate] = useState(todayIso())
  const [mode, setMode] = useState<PaymentMode>('upi')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')

  const record = useMutation(
    () =>
      api.post<RentCycle>(`/api/rent/cycle/${month}/pay`, {
        resident_id: participant.resident_id,
        amount_paise: amountPaise,
        payment_date: date,
        payment_mode: mode,
        reference: reference.trim() || null,
        note: note.trim() || null,
      }),
    { onSuccess: onRecorded },
  )
  // Every payment into the kitty carries the bank's or UPI app's transaction ID.
  const valid = amountOk && amountPaise > 0 && Boolean(date) && Boolean(reference.trim())

  return (
    <Modal
      title="Record Rent Payment"
      description={`${participant.display_name} · ${formatMonth(month)} rent · ${formatInr(owed)} outstanding`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={record.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={!valid || record.pending}
            onClick={() => void record.run(undefined)}
          >
            {record.pending ? 'Recording…' : 'Record Payment'}
          </button>
        </>
      }
    >
      <div className="stack">
        {record.error && <ErrorNotice error={record.error} />}
        {/* Residents pay their share into the House Kitty. */}
        <TransferDestination
          name={apartment?.pool_name || 'House Kitty'}
          amountPaise={amountOk ? amountPaise : null}
          bankAccount={apartment?.pool_bank_account}
          bankIfsc={apartment?.pool_bank_ifsc}
          upiId={apartment?.pool_upi_id}
          missing={
            <>
              No bank account or UPI ID is recorded for the House Kitty.{' '}
              <Link to="/payments" className="font-semibold">
                Add them on Payment Pools
              </Link>
              .
            </>
          }
        />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount"
            required
            value={amount}
            hint={
              amountOk && amountPaise > owed
                ? `${formatInr(amountPaise - owed)} more than this month's share. The extra goes to ${participant.display_name}'s other charges, oldest first.`
                : amountOk && amountPaise > 0 && amountPaise < owed
                  ? `Part payment: ${formatInr(owed - amountPaise)} of this month's share will still be owed.`
                  : `Pre-filled with the outstanding share. It is applied to ${formatMonth(month)} rent first.`
            }
            onChange={(raw, parsed) => {
              setAmount(raw)
              setAmountPaise(parsed.paise)
              setAmountOk(parsed.ok)
            }}
          />
          <DateField label="Payment date" required value={date} onChange={setDate} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField<PaymentMode>
            label="Payment mode"
            value={mode}
            options={PAYMENT_MODES}
            onChange={setMode}
          />
          <TextField
            label="Reference / UTR"
            required
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="e.g. UPI ref or bank UTR"
          />
        </div>
        <TextField
          label="Note"
          value={note}
          onChange={setNote}
          maxLength={500}
          placeholder="Optional remark (e.g. GPay transfer)"
        />
      </div>
    </Modal>
  )
}

/* ----------------------------------------------------------- payout modal -- */

function PayoutModal({
  month,
  cycle,
  config,
  onClose,
  onRecorded,
}: {
  month: string
  cycle: RentCycle
  config: RentLeaseConfig
  onClose: () => void
  onRecorded: (cycle: RentCycle) => void
}) {
  const residents = useResource<{ items: Resident[] }>('/api/residents')
  const total = cycle.total_rent_paise
  const remaining = cycle.remaining_disbursement_paise
  // The landlord is due on a fixed day and flatmates pay when they can, so a
  // first payout offers what has been collected so far; after that, the rest.
  const fromCollection =
    cycle.disbursements.length === 0 && cycle.collected_paise > 0 && cycle.collected_paise < total
  const suggested = fromCollection ? cycle.collected_paise : remaining
  const [amount, setAmount] = useState(paiseToInput(suggested))
  const [amountPaise, setAmountPaise] = useState(suggested)
  const [amountOk, setAmountOk] = useState(true)
  const [mode, setMode] = useState<RentDisbursementMode>('pool')
  const [funder, setFunder] = useState('')
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')

  const record = useMutation(
    () =>
      api.post<RentCycle>(`/api/rent/cycle/${month}/disburse`, {
        amount_paise: amountPaise,
        disbursement_mode: mode,
        funding_resident_id: mode === 'resident_advance' ? funder : null,
        reference: reference.trim(),
        disbursed_date: date,
        note: note.trim() || null,
      }),
    { onSuccess: onRecorded },
  )

  const tooMuch = amountOk && amountPaise > remaining
  const amountValid = amountOk && amountPaise > 0 && amountPaise <= remaining
  const valid =
    amountValid &&
    Boolean(date) &&
    reference.trim().length > 0 &&
    (mode === 'pool' || Boolean(funder))

  return (
    <Modal
      title="Record Landlord Payout"
      description={`${formatMonth(month)} · ${formatInr(remaining)} still owed to ${config.landlord_name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={record.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={!valid || record.pending}
            onClick={() => void record.run(undefined)}
          >
            {record.pending ? 'Recording…' : 'Record Payout'}
          </button>
        </>
      }
    >
      <div className="stack">
        {record.error && <ErrorNotice error={record.error} />}
        {/* Laid out as a resident's payment: where the money goes, then what was paid. */}
        <TransferDestination
          label="Payout destination"
          name={config.payout_beneficiary_name || config.landlord_name}
          amountPaise={amountValid ? amountPaise : null}
          bankAccount={config.landlord_bank_account}
          bankIfsc={config.landlord_bank_ifsc}
          upiId={config.landlord_upi_id}
          missing={
            <>
              No bank account or UPI ID is recorded for the landlord.{' '}
              <Link to="/payments" className="font-semibold">
                Add them on Payment Pools
              </Link>
              .
            </>
          }
        />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount"
            required
            value={amount}
            error={tooMuch ? `More than the ${formatInr(remaining)} still owed to the landlord.` : null}
            hint={
              fromCollection
                ? `Pre-filled with collected funds. You can pay any amount up to ${formatInr(remaining)}.`
                : `Pre-filled with the balance still owed. You can pay any amount up to ${formatInr(remaining)}.`
            }
            onChange={(raw, parsed) => {
              setAmount(raw)
              setAmountPaise(parsed.paise)
              setAmountOk(parsed.ok)
            }}
          />
          <DateField label="Payment date" required value={date} onChange={setDate} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField<RentDisbursementMode>
            label="Who funded this payout?"
            value={mode}
            options={[
              { value: 'pool', label: 'House Kitty (direct transfer)' },
              { value: 'resident_advance', label: 'A resident paid on the household’s behalf' },
            ]}
            onChange={setMode}
          />
          <TextField
            label="Reference / UTR"
            required
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="e.g. 428919028471"
          />
        </div>
        {mode === 'resident_advance' && (
          <SelectField
            label="Funding resident"
            required
            value={funder}
            placeholder={residents.data ? 'Choose a resident' : 'Loading…'}
            options={(residents.data?.items ?? []).map((resident) => ({
              value: resident.resident_id,
              label: resident.display_name,
            }))}
            onChange={setFunder}
            hint="Credited this payout as a provider advance. They still owe their own share."
          />
        )}
        <TextField
          label="Note"
          value={note}
          onChange={setNote}
          maxLength={500}
          placeholder="e.g. Balance after the late shares came in"
        />
      </div>
    </Modal>
  )
}
