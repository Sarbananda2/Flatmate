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
  ArrowRight,
  Building2,
  CalendarDays,
  Check,
  Clock,
  CreditCard,
  Settings,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { RentAgreementCard } from './RentAgreement'
import { AsyncSection, Callout, ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, MoneyInput, SelectField, TextField } from '../../components/Form'
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

export function RentMonth({ month, apartment = null }: { month: string; apartment?: Apartment | null }) {
  const toast = useToast()
  const [excluded, setExcluded] = useState<string[]>([])
  const overview = useResource<RentMonthOverview>(`/api/rent/cycle/${month}`, {
    exclude: excluded.join(',') || undefined,
  })
  const [dialog, setDialog] = useState<'payout' | null>(null)
  const [payingFor, setPayingFor] = useState<RentParticipant | null>(null)
  const [viewingStatement, setViewingStatement] = useState<RentParticipant | null>(null)
  const [viewingLandlordStatement, setViewingLandlordStatement] = useState(false)

  const generate = useMutation(
    () =>
      api.post<RentCycle>(`/api/rent/cycle/${month}/generate`, {
        // The split is the household's agreement model, saved on the lease.
        excluded_resident_ids: excluded,
      }),
    {
      onSuccess: (cycle) => {
        toast.success(
          `Rent for ${formatMonth(month)} generated`,
          cycle.adopted_existing_expense
            ? 'The rent expense already recorded for this month was linked, not duplicated.'
            : `${formatInr(cycle.total_rent_paise)} split between ${cycle.participant_count}.`,
        )
        setExcluded([])
        overview.reload()
      },
    },
  )

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
                    onPay={setPayingFor}
                    onViewStatement={setViewingStatement}
                  />
                ) : (
                  preview && (
                    <PreviewPanel
                      month={month}
                      preview={preview}
                      excluded={excluded}
                      onToggle={(residentId, takesPart) =>
                        setExcluded((current) =>
                          takesPart
                            ? current.filter((id) => id !== residentId)
                            : [...current, residentId],
                        )
                      }
                      generating={generate.pending}
                      error={generate.error}
                      onGenerate={() => void generate.run(undefined)}
                    />
                  )
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
                  month={month}
                  onSaved={overview.reload}
                />
                <DepositOverview config={config} />
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
                // Read from the month as it now stands, not as it was when opened.
                participant={
                  cycle.participants.find(
                    (row) => row.resident_id === viewingStatement.resident_id,
                  ) ?? viewingStatement
                }
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

/** The lease's deposit at a glance. Its slots and escrow have a screen of their own. */
function DepositOverview({ config }: { config: RentLeaseConfig }) {
  return (
    <Panel
      title="Security Deposit"
      icon={<ShieldCheck className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Held in escrow by the landlord for the length of the lease. Not part of this month's rent."
      actions={
        <Link to="/housing?tab=deposits" className={BUTTON_SECONDARY}>
          Manage Deposit Slots &amp; Escrow
          <ArrowRight className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
        </Link>
      }
    >
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-[var(--text-secondary)]">
        <span>Total deposit</span>
        {config.security_deposit_paise != null ? (
          <Rupees
            paise={config.security_deposit_paise}
            className="text-base font-bold text-[var(--text-primary)]"
          />
        ) : (
          <span className="font-semibold text-[var(--text-tertiary)]">Not entered on the lease</span>
        )}
      </div>
    </Panel>
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
            : 'Starts once rent is generated'
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
  funded_payout: { tone: 'accent', label: 'Paid the landlord' },
}

function SplitRule({
  total,
  count,
  base,
  remainder,
}: {
  total: number
  count: number
  base: number
  remainder: number
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-3.5 py-2.5 text-xs text-[var(--text-secondary)]">
      <div>
        Equal split:{' '}
        <strong className="tabular text-[var(--text-primary)]">
          {formatInr(total)} ÷ {count} = {formatInr(base)}
        </strong>{' '}
        each · Remainder:{' '}
        <span className="tabular">
          {remainder} paise
          {remainder > 0 && ', one each in resident order, the landlord payer last'}
        </span>
      </div>
    </div>
  )
}

function CyclePanel({
  cycle,
  config,
  onPay,
  onViewStatement,
}: {
  cycle: RentCycle
  config: RentLeaseConfig
  onPay: (participant: RentParticipant) => void
  onViewStatement: (participant: RentParticipant) => void
}) {
  return (
    <Panel
      title="Equal Split & Internal Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each resident owes for this month's rent, and how much of it has reached the pool."
    >

      {!cycle.matches_lease_rent && (
        <p className="m-0 text-xs text-[var(--warning)]">
          This month was charged at {formatInr(cycle.total_rent_paise)}; the lease now says{' '}
          {formatInr(config.monthly_rent_paise)}. A generated month keeps its own amount.
        </p>
      )}

      <div className="overflow-x-auto min-w-0">
        <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
          <caption className="sr-only">Rent shares and collection</caption>
          <thead>
            <tr className={TABLE_HEAD}>
              <th scope="col" className="w-[28%] pb-3 pr-3 font-bold text-center">Resident</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Share</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Paid to pool</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Still owed</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Status</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {cycle.participants.map((participant) => {
              const status = COLLECTION[participant.collection_status]
              const funded = participant.collection_status === 'funded_payout'
              const settled = funded || participant.collection_status === 'paid'
              return (
                // The whole row opens the resident's statement for the month; the
                // link and the button in it do their own thing instead.
                <tr
                  key={participant.resident_id}
                  className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                  tabIndex={0}
                  title={`View ${participant.display_name}'s rent statement`}
                  onClick={() => onViewStatement(participant)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onViewStatement(participant)
                    }
                  }}
                >
                  <th scope="row" className="py-3 pr-3 text-left font-normal">
                    <span className="flex items-center gap-2.5 min-w-0">
                      <Avatar name={participant.display_name} />
                      <span className="min-w-0">
                        <Link
                          className="block font-semibold text-[var(--text-primary)] truncate"
                          to={`/residents/${participant.resident_id}`}
                          onClick={(event) => event.stopPropagation()}
                        >
                          {participant.display_name}
                        </Link>
                      </span>
                    </span>
                  </th>
                  <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                    <Rupees paise={participant.share_paise} />
                  </td>
                  <td className="py-3 pr-3 text-center">
                    <Rupees paise={participant.settled_paise} />
                  </td>
                  <td className="py-3 pr-3 text-center">
                    {funded ? (
                      <span className="text-[var(--text-tertiary)]">—</span>
                    ) : (
                      <Rupees paise={participant.outstanding_paise} />
                    )}
                  </td>
                  <td className="py-3 pr-3 text-center whitespace-nowrap">
                    <Pill tone={status.tone}>
                      {participant.collection_status === 'paid' ||
                      participant.collection_status === 'funded_payout' ? (
                        <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
                      ) : (
                        <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
                      )}
                      {status.label}
                    </Pill>
                  </td>
                  <td className="py-3 text-center whitespace-nowrap">
                    {/* On every row, so the column reads the same down the table;
                        there is nothing to record once the share is covered. */}
                    <button
                      type="button"
                      className={BUTTON_SECONDARY}
                      disabled={settled || participant.outstanding_paise <= 0}
                      onClick={(event) => {
                        event.stopPropagation()
                        onPay(participant)
                      }}
                      aria-label={`Pay now for ${participant.display_name}`}
                    >
                      <CreditCard className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                      Pay Now
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {cycle.excluded_residents.length > 0 && (
        <p className="m-0 text-[11px] text-[var(--text-secondary)]">
          Not taking part this month:{' '}
          {cycle.excluded_residents.map((resident) => resident.display_name).join(', ')}.
        </p>
      )}
    </Panel>
  )
}

function PreviewPanel({
  month,
  preview,
  excluded,
  onToggle,
  generating,
  error,
  onGenerate,
}: {
  month: string
  preview: RentPreview
  excluded: string[]
  onToggle: (residentId: string, takesPart: boolean) => void
  generating: boolean
  error: ReturnType<typeof useMutation>['error']
  onGenerate: () => void
}) {
  const adopting = preview.mode === 'adopt'
  const rows = [
    ...preview.participants.map((row) => ({ ...row, takesPart: true })),
    // In adopt mode the participants are whoever the existing expense charges.
    ...(adopting ? [] : preview.excluded_residents).map((row) => ({
      ...row,
      share_paise: 0,
      takesPart: false,
    })),
  ].sort((a, b) => a.display_name.localeCompare(b.display_name))
  // The split follows the agreement model; it is changed on the agreement card.
  const tiered = preview.split_mode === 'room_tiered'
  const rooms = new Map(preview.room_tiered.shares.map((row) => [row.resident_id, row.room_number]))

  return (
    <Panel
      title={`Rent for ${formatMonth(month)} has not been generated`}
      icon={<CalendarDays className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={
        adopting
          ? 'Rent for this month is already recorded as an expense. Generating links it here; it is not charged a second time.'
          : tiered
            ? "Each resident taking part is charged their room's base rent. A partial month never reduces a share."
            : 'Everyone taking part is charged a full equal share. A partial month never reduces a share.'
      }
      actions={
        <button
          type="button"
          className={BUTTON_PRIMARY}
          disabled={generating || !preview.can_generate}
          onClick={onGenerate}
        >
          {generating
            ? 'Generating…'
            : adopting
              ? 'Link existing rent'
              : `Generate ${formatMonth(month)} rent`}
        </button>
      }
    >
      {error && <ErrorNotice error={error} />}
      {preview.blocked_reason && (
        <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
          {preview.blocked_reason}
        </p>
      )}
      {adopting && !preview.matches_lease_rent && (
        <p className="m-0 text-xs text-[var(--warning)]">
          The recorded expense is {formatInr(preview.total_rent_paise)}, which differs from the
          lease. It is linked as recorded.
        </p>
      )}
      {preview.participant_count > 0 && !tiered && (
        <SplitRule
          total={preview.total_rent_paise}
          count={preview.participant_count}
          base={preview.base_share_paise}
          remainder={preview.remainder_paise}
        />
      )}
      <div className="overflow-x-auto min-w-0">
        <table className="w-full text-left text-xs min-w-[420px] border-collapse">
          <caption className="sr-only">Shares that generating rent would charge</caption>
          <thead>
            <tr className={TABLE_HEAD}>
              <th scope="col" className="pb-3 pr-3 font-bold text-center">Resident</th>
              <th scope="col" className="pb-3 pr-3 font-bold text-center">Takes part</th>
              <th scope="col" className="pb-3 font-bold text-center">Share</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {rows.map((row) => (
              <tr key={row.resident_id} className="align-middle">
                <th scope="row" className="py-3 pr-3 text-left font-semibold text-[var(--text-primary)]">
                  {row.display_name}
                  {tiered && row.takesPart && (
                    <span className="block text-[11px] font-normal text-[var(--text-tertiary)]">
                      {rooms.get(row.resident_id) ?? 'No room'}
                    </span>
                  )}
                </th>
                <td className="py-3 pr-3">
                  {adopting ? (
                    <Pill tone="success">Charged</Pill>
                  ) : (
                    <label className="inline-flex items-center gap-2 cursor-pointer text-[var(--text-secondary)]">
                      <input
                        type="checkbox"
                        className="w-auto"
                        checked={row.takesPart}
                        disabled={generating}
                        onChange={(event) => onToggle(row.resident_id, event.target.checked)}
                        aria-label={`${row.display_name} takes part in ${formatMonth(month)} rent`}
                      />
                      {excluded.includes(row.resident_id)
                        ? 'Left out'
                        : tiered
                          ? 'Room rent'
                          : 'Full share'}
                    </label>
                  )}
                </td>
                <td className="py-3 text-right font-bold text-[var(--text-primary)]">
                  <Rupees paise={row.share_paise} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
      title="Landlord & Disbursement"
      icon={<Building2 className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="How much of this month's rent has reached the landlord. Select the row for each payment made."
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
                {cycle ? <Rupees paise={cycle.remaining_disbursement_paise} /> : dash}
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
                  <Pill>Not generated</Pill>
                )}
              </td>
              <td className="py-3 text-center whitespace-nowrap">
                {/* Always there, as on each resident's row; off until the month is
                    generated and once the landlord has been paid in full. */}
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

/** One resident's rent for the month: what they owe, and every payment towards it. */
function RentStatementModal({
  month,
  participant,
  onClose,
}: {
  month: string
  participant: RentParticipant
  onClose: () => void
}) {
  const status = COLLECTION[participant.collection_status]
  const funded = participant.collection_status === 'funded_payout'
  const modes = new Map(PAYMENT_MODES.map((mode) => [mode.value, mode.label]))
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  return (
    <Modal
      title="Rent Statement"
      description={`${participant.display_name} · ${formatMonth(month)}`}
      onClose={onClose}
    >
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat label="Share" value={<Rupees paise={participant.share_paise} />} />
          <MiniStat label="Paid to Pool" value={<Rupees paise={participant.settled_paise} />} />
          <MiniStat
            label="Still Owed"
            value={funded ? dash : <Rupees paise={participant.outstanding_paise} />}
            note={funded ? 'Covered by paying the landlord' : undefined}
          />
        </div>

        {participant.advanced_paise > 0 && (
          <Callout>
            {participant.display_name} paid {formatInr(participant.advanced_paise)} to the
            landlord directly this month
            {funded ? ', which covers their share' : ''}. It is credited to them as an
            advance, not as a payment to the pool, so it is not listed below.
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
          {participant.payments.length === 0 ? (
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
                  {participant.payments.map((payment) => (
                    <tr key={payment.payment_id} className="align-middle">
                      <td className="py-2.5 pr-3 text-center tabular">
                        {formatDate(payment.payment_date)}
                      </td>
                      <td className="py-2.5 pr-3 text-center font-bold text-[var(--text-primary)]">
                        <Rupees paise={payment.amount_paise} />
                      </td>
                      <td className="py-2.5 pr-3 text-center">
                        {payment.payment_mode ? modes.get(payment.payment_mode) : dash}
                      </td>
                      <td className="py-2.5 text-center tabular break-all text-[var(--text-primary)]">
                        {payment.reference || dash}
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
  // Every payment into the pool carries the bank's or UPI app's transaction ID.
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
        {/* Residents pay their share into the household pool. */}
        <TransferDestination
          name={apartment?.pool_name || 'House Kitty'}
          amountPaise={amountOk ? amountPaise : null}
          bankAccount={apartment?.pool_bank_account}
          bankIfsc={apartment?.pool_bank_ifsc}
          upiId={apartment?.pool_upi_id}
          missing={
            <>
              No bank account or UPI ID is recorded for the household pool.{' '}
              <Link to="/payments" className="font-semibold">
                Add them on Payment Pools
              </Link>
              .
            </>
          }
        />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount paid"
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
              { value: 'pool', label: 'Apartment pool (direct transfer)' },
              { value: 'resident_advance', label: 'A resident paid on the household’s behalf' },
            ]}
            onChange={setMode}
          />
          <TextField
            label="Payment reference / UTR"
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
