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
  AlertTriangle,
  ArrowUpRight,
  Building2,
  CalendarDays,
  Check,
  Clock,
  Download,
  FileText,
  Plus,
  Printer,
  Settings,
  Upload,
  Users,
} from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection, Callout, ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, Field, MoneyInput, SelectField, TextField } from '../../components/Form'
import { useToast } from '../../components/Toast'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  CopyButton,
  EmptyBlock,
  MiniStat,
  Panel,
  Pill,
  Rupees,
  type Tone,
} from '../../components/ui'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { LeaseNoticeBanner, LeaseTermPill } from './LeaseLifecycle'
import { formatDate, formatInr, formatMonth, paiseToInput, todayIso } from '../../lib/format'
import type {
  RentCollectionStatus,
  RentCycle,
  RentDisbursementMode,
  RentLeaseConfig,
  RentMonthOverview,
  RentParticipant,
  RentPreview,
  RentReceipt,
  Resident,
} from '../../api/types'

const RECEIPT_TYPES = ['application/pdf', 'image/jpeg', 'image/png', 'image/webp']
const MAX_RECEIPT_BYTES = 5 * 1024 * 1024

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold'

export default function RentPage() {
  const { month } = useApp()
  return <RentMonth key={month} month={month} />
}

/** A share of the whole to one decimal place, for display only. */
function percent(part: number, whole: number): string {
  if (whole <= 0) return '0.0%'
  const tenths = Math.round((part * 1000) / whole)
  // Rounding must never make an incomplete collection read as complete.
  return `${((part < whole ? Math.min(tenths, 999) : tenths) / 10).toFixed(1)}%`
}

export function RentMonth({ month }: { month: string }) {
  const toast = useToast()
  const [excluded, setExcluded] = useState<string[]>([])
  const overview = useResource<RentMonthOverview>(`/api/rent/cycle/${month}`, {
    exclude: excluded.join(',') || undefined,
  })
  const [dialog, setDialog] = useState<'payout' | 'attach' | null>(null)
  const [receiptFor, setReceiptFor] = useState<RentParticipant | null>(null)
  const [payingFor, setPayingFor] = useState<RentParticipant | null>(null)

  const generate = useMutation(
    () =>
      api.post<RentCycle>(`/api/rent/cycle/${month}/generate`, {
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
        const pending = cycle?.disbursement_status === 'pending'
        return (
          <Page
            title={`Rent · ${formatMonth(month)}`}
            subtitle="What residents have paid towards this month's rent, and whether the landlord has been paid, under the master lease."
            breadcrumbs={[{ label: 'Housing & Utilities', to: '/services' }, { label: 'Rent' }]}
          >
            {/* Status on the left, actions on the right, on a row of their own so
                the two never wrap around the title. */}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                {config && <LeaseTermPill config={config} />}
                <DisbursementPill cycle={cycle} configured={Boolean(config)} />
                {cycle && <LandlordReceiptPill status={cycle.landlord_receipt_status} />}
              </div>
              <div className="flex flex-wrap items-center gap-2 ml-auto">
                <Link to="/services/rent/config" className={BUTTON_SECONDARY}>
                  <Settings className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Lease Settings
                </Link>
                {pending && (
                  <button
                    type="button"
                    className={BUTTON_PRIMARY}
                    onClick={() => setDialog('payout')}
                  >
                    <ArrowUpRight className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                    Record Landlord Payout
                  </button>
                )}
              </div>
            </div>

            {!config ? (
              <Panel
                title="Set up the lease"
                icon={<Building2 className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
              >
                <EmptyBlock
                  title="No lease on file yet"
                  body="Enter the landlord and the monthly rent once. Each month's rent is then split equally between the residents taking part."
                  action={
                    <Link to="/services/rent/config" className={BUTTON_PRIMARY}>
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
                {cycle ? (
                  <CyclePanel
                    cycle={cycle}
                    config={config}
                    onReceipt={setReceiptFor}
                    onPay={setPayingFor}
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
                  month={month}
                  onAttach={() => setDialog('attach')}
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
                      (updated.landlord_receipt_status === 'pending'
                        ? 'HRA receipts are available. Attach the landlord’s receipt when it arrives.'
                        : 'HRA receipts are now available for each resident.'),
                  )
                  overview.reload()
                }}
              />
            )}
            {dialog === 'attach' && cycle && (
              <AttachReceiptModal
                month={month}
                cycle={cycle}
                onClose={() => setDialog(null)}
                onAttached={(updated) => {
                  setDialog(null)
                  toast.success(
                    updated.replaced_landlord_receipt
                      ? 'Landlord receipt replaced'
                      : 'Landlord receipt attached',
                    updated.landlord_receipt?.filename,
                  )
                  overview.reload()
                }}
              />
            )}
            {payingFor && cycle && (
              <RentPaymentModal
                month={month}
                participant={payingFor}
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
            {receiptFor && (
              <ReceiptModal
                month={month}
                participant={receiptFor}
                onClose={() => setReceiptFor(null)}
              />
            )}
          </Page>
        )
      }}
    </AsyncSection>
  )
}

function DisbursementPill({ cycle, configured }: { cycle: RentCycle | null; configured: boolean }) {
  if (!configured) return <Pill>Lease not set up</Pill>
  if (!cycle) return <Pill>Rent not generated</Pill>
  if (cycle.disbursement_status === 'disbursed') {
    return (
      <Pill tone="success">
        <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
        Disbursed
      </Pill>
    )
  }
  return (
    <Pill tone="warning">
      <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
      Disbursement Pending
    </Pill>
  )
}

/** Shown only when the lease expects a receipt and the landlord has been paid. */
function LandlordReceiptPill({ status }: { status: RentCycle['landlord_receipt_status'] }) {
  if (status === 'pending') {
    return (
      <Pill tone="warning">
        <AlertTriangle className="w-3 h-3 shrink-0" aria-hidden="true" />
        Receipt Pending
      </Pill>
    )
  }
  if (status === 'attached') {
    return (
      <Pill tone="success">
        <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
        Receipt Attached
      </Pill>
    )
  }
  return null
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
  aside,
}: {
  total: number
  count: number
  base: number
  remainder: number
  aside?: React.ReactNode
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
      {aside && <div>{aside}</div>}
    </div>
  )
}

function CyclePanel({
  cycle,
  config,
  onReceipt,
  onPay,
}: {
  cycle: RentCycle
  config: RentLeaseConfig
  onReceipt: (participant: RentParticipant) => void
  onPay: (participant: RentParticipant) => void
}) {
  const share = percent(cycle.collected_paise, cycle.total_rent_paise)
  return (
    <Panel
      title="Equal Split & Internal Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each resident owes for this month's rent, and how much of it has reached the pool."
      actions={
        <>
          <Pill>Service period {cycle.service_period}</Pill>
          <Link className={BUTTON_QUIET} to="/expenses" title={cycle.description}>
            View in expenses
          </Link>
        </>
      }
    >
      <SplitRule
        total={cycle.total_rent_paise}
        count={cycle.participant_count}
        base={cycle.base_share_paise}
        remainder={cycle.remainder_paise}
        aside={
          <>
            Collected:{' '}
            <strong className="tabular text-[var(--text-primary)]">
              {formatInr(cycle.collected_paise)}
            </strong>{' '}
            / <span className="tabular">{formatInr(cycle.total_rent_paise)}</span>
          </>
        }
      />

      {!cycle.matches_lease_rent && (
        <p className="m-0 text-xs text-[var(--warning)]">
          This month was charged at {formatInr(cycle.total_rent_paise)}; the lease now says{' '}
          {formatInr(config.monthly_rent_paise)}. A generated month keeps its own amount.
        </p>
      )}

      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-[11px] font-medium text-[var(--text-secondary)]">
          <span>Internal collection</span>
          <span className="tabular">{share} collected</span>
        </div>
        <div
          className="h-2 rounded-full bg-[var(--bg-elevated)] overflow-hidden"
          role="progressbar"
          aria-label="Rent collected from residents"
          aria-valuemin={0}
          aria-valuemax={cycle.total_rent_paise}
          aria-valuenow={cycle.collected_paise}
          aria-valuetext={`${share} collected`}
        >
          <div
            className="h-full rounded-full bg-[var(--success)] transition-all"
            style={{ width: share }}
          />
        </div>
      </div>

      <div className="overflow-x-auto min-w-0">
        <table className="w-full table-fixed text-left text-xs min-w-[960px] border-collapse">
          <caption className="sr-only">Rent shares and collection</caption>
          <thead>
            <tr className={TABLE_HEAD}>
              <th scope="col" className="w-[26%] pb-3 pr-3 font-bold text-center">Resident</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Share</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Paid to pool</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Still owed</th>
              <th scope="col" className="w-[16%] pb-3 pr-3 font-bold text-center">Status</th>
              <th scope="col" className="w-[16%] pb-3 pr-3 font-bold text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {cycle.participants.map((participant) => {
              const status = COLLECTION[participant.collection_status]
              const funded = participant.collection_status === 'funded_payout'
              const settled = funded || participant.collection_status === 'paid'
              return (
                <tr key={participant.resident_id} className="align-middle">
                  <th scope="row" className="py-3 pr-3 text-left font-normal">
                    <span className="flex items-center gap-2.5 min-w-0">
                      <Avatar name={participant.display_name} />
                      <span className="min-w-0">
                        <Link
                          className="block font-semibold text-[var(--text-primary)] truncate"
                          to={`/residents/${participant.resident_id}`}
                        >
                          {participant.display_name}
                        </Link>
                        <span className="block text-[11px] text-[var(--text-tertiary)] tabular">
                          1 / {cycle.participant_count}
                        </span>
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
                      {participant.collection_status === 'paid' &&
                        participant.last_payment_date &&
                        ` · ${formatDate(participant.last_payment_date)}`}
                    </Pill>
                  </td>
                  <td className="py-3 text-center whitespace-nowrap">
                    <span className="inline-flex flex-wrap items-center justify-center gap-2">
                      {(participant.collection_status === 'unpaid' ||
                        participant.collection_status === 'partial') && (
                        <button
                          type="button"
                          className={BUTTON_SECONDARY}
                          onClick={() => onPay(participant)}
                          aria-label={`Record rent payment from ${participant.display_name}`}
                        >
                          <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                          Record Payment
                        </button>
                      )}
                      {participant.receipt_available ? (
                        <button
                          type="button"
                          className={BUTTON_QUIET}
                          onClick={() => onReceipt(participant)}
                          aria-label={`View HRA receipt for ${participant.display_name}`}
                        >
                          <FileText className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                          View HRA Slip
                        </button>
                      ) : (
                        // Only worth saying to someone who has paid and is
                        // waiting on the landlord, and only when the lease
                        // tracks the landlord's receipts.
                        config.requires_landlord_receipt &&
                        settled && (
                          <span className="text-[11px] text-[var(--text-tertiary)]">
                            HRA slip after payout
                          </span>
                        )
                      )}
                    </span>
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

  return (
    <Panel
      title={`Rent for ${formatMonth(month)} has not been generated`}
      icon={<CalendarDays className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={
        adopting
          ? 'Rent for this month is already recorded as an expense. Generating links it here; it is not charged a second time.'
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
      {preview.participant_count > 0 && (
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
                      {excluded.includes(row.resident_id) ? 'Left out' : 'Full share'}
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

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {label}
      </dt>
      <dd className="m-0 mt-0.5 text-xs font-semibold text-[var(--text-primary)] break-words">
        {children}
      </dd>
    </div>
  )
}

function maskAccount(account: string): string {
  return account.length > 4 ? `****${account.slice(-4)}` : account
}

function LandlordPanel({
  config,
  cycle,
  month,
  onAttach,
}: {
  config: RentLeaseConfig
  cycle: RentCycle | null
  month: string
  onAttach: () => void
}) {
  const none = <span className="font-normal text-[var(--text-tertiary)]">Not on file</span>
  const disbursed = cycle?.disbursement_status === 'disbursed'
  const bank = [
    config.landlord_bank_ifsc,
    config.landlord_bank_account && maskAccount(config.landlord_bank_account),
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <Panel
      title="Landlord & Disbursement"
      icon={<Building2 className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Who the rent goes to, and how this month's payout was made."
    >
      <dl className="m-0 grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-4">
        <Detail label="Landlord">{config.landlord_name}</Detail>
        <Detail label="Landlord PAN (for HRA)">
          {config.landlord_pan ? <span className="tabular">{config.landlord_pan}</span> : none}
        </Detail>
        <Detail label="Bank account">
          {bank ? <span className="tabular">{bank}</span> : none}
        </Detail>
        <Detail label="UPI ID">
          {config.landlord_upi_id ? (
            <span className="tabular">{config.landlord_upi_id}</span>
          ) : (
            none
          )}
        </Detail>
        <Detail label="Phone">{config.landlord_phone ?? none}</Detail>
        <Detail label="Landlord receipts">
          {config.requires_landlord_receipt ? 'Issued — attached when it arrives' : 'Not issued'}
        </Detail>
        <Detail label="Disbursement">
          {!cycle
            ? 'Rent not generated'
            : !disbursed
              ? 'Pending'
              : cycle.disbursed_by === 'pool'
                ? `From the pool · ${formatDate(cycle.disbursed_at)}`
                : `Paid by ${cycle.funding_resident_name} · ${formatDate(cycle.disbursed_at)}`}
        </Detail>
        {disbursed && cycle && (
          <Detail label="Bank reference / UTR">
            <span className="tabular">{cycle.disbursement_reference}</span>
          </Detail>
        )}
      </dl>
      {cycle && cycle.landlord_receipt_status !== 'not_required' && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-3.5 py-3">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-bold text-[var(--text-primary)]">
                Landlord's receipt
              </span>
              <LandlordReceiptPill status={cycle.landlord_receipt_status} />
            </div>
            <p className="m-0 text-[11px] text-[var(--text-secondary)] break-words">
              {cycle.landlord_receipt ? (
                <>
                  {cycle.landlord_receipt.filename} · attached{' '}
                  <span className="tabular">
                    {formatDate(cycle.landlord_receipt.uploaded_at)}
                  </span>
                </>
              ) : (
                'The payout is recorded. Attach the receipt whenever the landlord sends it.'
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            {cycle.landlord_receipt && (
              <a className={BUTTON_QUIET} href={`/api/rent/cycle/${month}/landlord-receipt`}>
                <Download className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                Download
              </a>
            )}
            {/* Attaching is offered only while the lease expects receipts; a file
                attached earlier stays downloadable after that is turned off. */}
            {config.requires_landlord_receipt &&
              (cycle.landlord_receipt ? (
                <button type="button" className={BUTTON_SECONDARY} onClick={onAttach}>
                  <Upload className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Replace
                </button>
              ) : (
                <button type="button" className={BUTTON_PRIMARY} onClick={onAttach}>
                  <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Attach Landlord Receipt
                </button>
              ))}
          </div>
        </div>
      )}
      {disbursed && cycle?.disbursed_by === 'resident_advance' && (
        <p className="m-0 text-[11px] text-[var(--text-secondary)]">
          {cycle.funding_resident_name} is credited the whole{' '}
          <span className="tabular">{formatInr(cycle.total_rent_paise)}</span> as a provider
          advance and is still charged their own share.
        </p>
      )}
    </Panel>
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
  onClose,
  onRecorded,
}: {
  month: string
  participant: RentParticipant
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
  const valid = amountOk && amountPaise > 0 && Boolean(date)

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
            label="Reference"
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="UPI or bank reference"
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

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the file.'))
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.readAsDataURL(file)
  })
}

function receiptFileError(file: File | null): string | null {
  if (!file) return null
  if (!RECEIPT_TYPES.includes(file.type)) return 'Choose a PDF, JPEG, PNG, or WebP file.'
  if (file.size > MAX_RECEIPT_BYTES) return 'That file is over 5 MB.'
  return null
}

async function toUpload(file: File) {
  return {
    filename: file.name,
    content_type: file.type,
    content_base64: await readAsBase64(file),
  }
}

function ReceiptFileField({
  file,
  onChange,
  required,
  hint,
}: {
  file: File | null
  onChange: (file: File | null) => void
  required?: boolean
  hint: string
}) {
  return (
    <Field label="Landlord's receipt" required={required} error={receiptFileError(file)} hint={hint}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          type="file"
          accept={RECEIPT_TYPES.join(',')}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        />
      )}
    </Field>
  )
}

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
  const [mode, setMode] = useState<RentDisbursementMode>('pool')
  const [funder, setFunder] = useState('')
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const expectsReceipt = config.requires_landlord_receipt

  const record = useMutation(
    async () =>
      api.post<RentCycle>(`/api/rent/cycle/${month}/disburse`, {
        disbursement_mode: mode,
        funding_resident_id: mode === 'resident_advance' ? funder : null,
        reference: reference.trim(),
        disbursed_date: date,
        landlord_receipt: expectsReceipt && file ? await toUpload(file) : null,
      }),
    { onSuccess: onRecorded },
  )

  const valid =
    Boolean(date) &&
    reference.trim().length > 0 &&
    (mode === 'pool' || Boolean(funder)) &&
    // The receipt is optional, but a file that was chosen must be usable.
    !receiptFileError(file)
  const funderName = residents.data?.items.find((r) => r.resident_id === funder)?.display_name
  const hasBank = Boolean(config.landlord_bank_account || config.landlord_bank_ifsc)

  return (
    <Modal
      title="Record Landlord Payout"
      description={`${formatMonth(month)} · ${formatInr(cycle.total_rent_paise)} to ${config.landlord_name}`}
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
            {record.pending ? 'Recording…' : 'Confirm & Post Payout'}
          </button>
        </>
      }
    >
      <div className="stack">
        {record.error && <ErrorNotice error={record.error} />}
        <section
          aria-label="Payout destination"
          className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-3 min-w-0"
        >
          <div className="flex items-start justify-between gap-3 min-w-0">
            <div className="min-w-0">
              <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                Transfer to
              </div>
              <div className="text-sm font-bold text-[var(--text-primary)] truncate">
                {config.landlord_name}
              </div>
            </div>
            <Rupees
              paise={cycle.total_rent_paise}
              className="shrink-0 text-base font-bold text-[var(--text-primary)]"
            />
          </div>
          {hasBank || config.landlord_upi_id ? (
            <dl className="m-0 space-y-2 text-xs">
              {config.landlord_bank_account && (
                <PayoutDetail label="Account" value={config.landlord_bank_account} />
              )}
              {config.landlord_bank_ifsc && (
                <PayoutDetail label="IFSC" value={config.landlord_bank_ifsc} />
              )}
              {config.landlord_upi_id && (
                <PayoutDetail label="UPI ID" value={config.landlord_upi_id} />
              )}
            </dl>
          ) : (
            <p className="m-0 text-xs text-[var(--text-secondary)]">
              No bank account or UPI ID is on the lease. Add them in Lease Settings to see them
              here.
            </p>
          )}
        </section>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <DateField label="Payment date" required value={date} onChange={setDate} />
          <TextField
            label="Payment reference / UTR"
            required
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="e.g. 428919028471"
            hint="The bank UTR or UPI transaction ID."
          />
        </div>
        <SelectField<RentDisbursementMode>
          label="Who funded this payout?"
          value={mode}
          options={[
            { value: 'pool', label: 'Apartment pool (direct transfer)' },
            { value: 'resident_advance', label: 'A resident paid on the household’s behalf' },
          ]}
          onChange={setMode}
        />
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
            hint="Credited the whole rent as a provider advance. They still owe their own share."
          />
        )}
        {expectsReceipt && (
          <ReceiptFileField
            file={file}
            onChange={setFile}
            hint="Optional. Attach it now if you have it, or later from this page when the landlord sends it. PDF or photo, up to 5 MB."
          />
        )}
        <Callout tone="warn" title="This cannot be undone">
          Recording the payout locks this month's rent cycle: it cannot be edited or removed
          afterwards, and it issues an immutable HRA receipt for each resident.{' '}
          {mode === 'pool'
            ? 'Nothing is added to the ledger: the rent is already a cost to the pool.'
            : `${funderName ?? 'The resident'} is credited ${formatInr(cycle.total_rent_paise)} and keeps their own share.`}
        </Callout>
      </div>
    </Modal>
  )
}

/** One line of where to send the rent, in full, with a copy button. */
function PayoutDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 min-w-0">
      <dt className="shrink-0 text-[var(--text-secondary)]">{label}</dt>
      <dd className="m-0 flex items-center gap-2 min-w-0">
        <span className="tabular font-semibold text-[var(--text-primary)] truncate select-all">
          {value}
        </span>
        <CopyButton text={value} label={`Copy ${label}`} iconOnly />
      </dd>
    </div>
  )
}

function AttachReceiptModal({
  month,
  cycle,
  onClose,
  onAttached,
}: {
  month: string
  cycle: RentCycle
  onClose: () => void
  onAttached: (cycle: RentCycle) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const replacing = Boolean(cycle.landlord_receipt)
  const attach = useMutation(
    async () =>
      api.post<RentCycle>(`/api/rent/cycle/${month}/landlord-receipt`, await toUpload(file!)),
    { onSuccess: onAttached },
  )
  return (
    <Modal
      title={replacing ? 'Replace Landlord Receipt' : 'Attach Landlord Receipt'}
      description={`${formatMonth(month)} · paid ${formatDate(cycle.disbursed_at)} · ref ${cycle.disbursement_reference}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={attach.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={!file || Boolean(receiptFileError(file)) || attach.pending}
            onClick={() => void attach.run(undefined)}
          >
            {attach.pending ? 'Uploading…' : replacing ? 'Replace Receipt' : 'Attach Receipt'}
          </button>
        </>
      }
    >
      <div className="stack">
        {attach.error && <ErrorNotice error={attach.error} />}
        <ReceiptFileField
          file={file}
          onChange={setFile}
          required
          hint="PDF or photo of the receipt the landlord issued, up to 5 MB."
        />
        {replacing && (
          <p className="m-0 text-[11px] text-[var(--text-secondary)]">
            This removes {cycle.landlord_receipt?.filename}. The swap is noted in the audit
            log. Nothing else about the recorded payout changes.
          </p>
        )}
      </div>
    </Modal>
  )
}

/* ---------------------------------------------------------- receipt modal -- */

function ReceiptModal({
  month,
  participant,
  onClose,
}: {
  month: string
  participant: RentParticipant
  onClose: () => void
}) {
  const receipt = useResource<RentReceipt>(
    `/api/rent/cycle/${month}/receipt/${encodeURIComponent(participant.resident_id)}`,
  )
  return (
    <Modal
      title="HRA Rent Receipt"
      description={`${participant.display_name} · ${formatMonth(month)}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose}>
            Close
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={!receipt.data}
            onClick={() => window.print()}
          >
            <Printer className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Print / PDF
          </button>
        </>
      }
    >
      <AsyncSection resource={receipt} loadingLabel="Loading receipt…">
        {(data) => (
          <div className="stack">
            {!data.hash_matches && (
              <p className="m-0 text-xs font-semibold text-[var(--danger)]" role="alert">
                This receipt no longer matches its verification hash. The stored record was
                changed outside the application; do not rely on it.
              </p>
            )}
            {data.landlord_pan_advised && (
              <p className="m-0 text-xs text-[var(--warning)]">
                No landlord PAN is on file. Employers ask for it when rent is above ₹1,00,000
                a year. Receipts already issued keep the details they were issued with.
              </p>
            )}
            {!data.share_settled && (
              <p className="m-0 text-xs text-[var(--text-secondary)]">
                {data.resident_name} has not yet settled this share with the household.
              </p>
            )}
            <ReceiptSheet receipt={data} />
          </div>
        )}
      </AsyncSection>
    </Modal>
  )
}

function ReceiptSheet({ receipt }: { receipt: RentReceipt }) {
  return (
    <article className="receipt-sheet rounded-xl border border-[var(--border-medium)] bg-[var(--bg-surface)] p-5 space-y-4 text-[var(--text-primary)]">
      <header className="flex items-start justify-between gap-4 border-b border-[var(--border-subtle)] pb-3">
        <div>
          <div className="text-sm font-bold tracking-wide">RENT RECEIPT</div>
          <div className="text-[11px] text-[var(--text-secondary)]">
            For House Rent Allowance, Section 10(13A)
          </div>
        </div>
        <div className="text-right">
          <div className="tabular text-xs font-bold">{receipt.receipt_number}</div>
          <div className="text-[11px] text-[var(--text-secondary)]">
            Date: <span className="tabular">{formatDate(receipt.disbursed_at)}</span>
          </div>
        </div>
      </header>
      <p className="m-0 text-sm leading-relaxed">
        Received from <strong>{receipt.resident_name}</strong> the sum of{' '}
        <strong className="tabular">{formatInr(receipt.amount_paise)}</strong> (
        {receipt.amount_in_words}), being their equal 1/{receipt.participant_count} share of
        the rent of <span className="tabular">{formatInr(receipt.total_rent_paise)}</span> for
        the premises at <strong>{receipt.premises}</strong> for the month of{' '}
        <strong>{receipt.month_label}</strong>.
      </p>
      <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <div>
          <dt className="inline font-bold">Landlord: </dt>
          <dd className="inline m-0">{receipt.landlord_name}</dd>
        </div>
        <div>
          <dt className="inline font-bold">Payment ref: </dt>
          <dd className="inline m-0 tabular break-all">{receipt.disbursement_reference}</dd>
        </div>
        <div>
          <dt className="inline font-bold">Landlord PAN: </dt>
          <dd className="inline m-0 tabular">{receipt.landlord_pan ?? 'Not provided'}</dd>
        </div>
        <div>
          <dt className="inline font-bold">Paid to landlord: </dt>
          <dd className="inline m-0 tabular">{formatDate(receipt.disbursed_at)}</dd>
        </div>
      </dl>
      <footer className="border-t border-[var(--border-subtle)] pt-3 space-y-1">
        <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
          Verification hash (SHA-256)
        </div>
        <div className="tabular text-[10px] break-all text-[var(--text-secondary)]">
          {receipt.verification_hash}
        </div>
        <div className="text-[10px] text-[var(--text-tertiary)]">
          Generated by the household's expense records, not issued by the landlord.
        </div>
      </footer>
    </article>
  )
}
