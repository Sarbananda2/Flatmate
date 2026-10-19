/**
 * The Month Hub.
 *
 * Every figure here comes from the backend's reconciliation of real records.
 * The hub answers three questions for the month in context: how far through
 * its settlement cycle it is, who should pay whom, and where each resident
 * stands. Nothing on this screen changes the accounts.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  ArrowRightLeft,
  CheckCircle2,
  Info,
  Lock,
  MessageCircle,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useApp } from '../components/Layout'
import { AsyncSection, ErrorNotice, Modal } from '../components/Primitives'
import { DateField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { api } from '../api/client'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  CopyButton,
  EmptyBlock,
  MiniStat,
  Panel,
  Rupees,
  StatementPill,
  TransferDestination,
} from '../components/ui'
import { useMutation, useResource } from '../lib/useApi'
import {
  formatDate,
  formatHoursRemaining,
  formatInr,
  formatMonth,
  todayIso,
} from '../lib/format'
import type {
  Dashboard,
  DashboardWarning,
  ElectricityBillSummary,
  ReconciliationAccount,
  StatementSnapshot,
  TransferSuggestion,
  WhatsAppSummary,
} from '../api/types'

interface SnapshotEnvelope {
  snapshot: StatementSnapshot
  snapshot_hash: string | null
  hash_matches: boolean
}

export default function DashboardPage() {
  const { month } = useApp()
  const resource = useResource<Dashboard>('/api/dashboard', { settlement_period: month })

  return (
    <AsyncSection resource={resource}>
      {(data) => <MonthHub data={data} month={month} onChanged={resource.reload} />}
    </AsyncSection>
  )
}

function MonthHub({
  data,
  month,
  onChanged,
}: {
  data: Dashboard
  month: string
  onChanged: () => void
}) {
  const toast = useToast()
  const bills = useResource<{ items: ElectricityBillSummary[] }>('/api/electricity/bills', {
    limit: 100,
  })
  const snapshot = useResource<SnapshotEnvelope>(
    data.statement && data.statement.status !== 'draft'
      ? `/api/statements/${encodeURIComponent(data.statement.statement_id)}/snapshot`
      : null,
  )
  const [sharing, setSharing] = useState(false)
  const [settling, setSettling] = useState<TransferSuggestion | null>(null)

  const monthBills = (bills.data?.items ?? []).filter((bill) => bill.settlement_period === month)
  const residents = data.accounts.filter((account) => account.account_type === 'resident')
  const checksum = data.accounts.reduce((sum, account) => sum + account.net_position_paise, 0)
  const errors = data.warnings.filter((warning) => warning.severity === 'error')
  const notices = data.warnings.filter((warning) => warning.severity !== 'error')

  return (
    <div className="space-y-6">
      {errors.length > 0 && (
        <WarningList tone="danger" title="The accounts do not add up yet" warnings={errors} />
      )}
      {notices.length > 0 && (
        <WarningList
          tone={notices.some((warning) => warning.severity === 'warning') ? 'warning' : 'info'}
          title={
            notices.some((warning) => warning.severity === 'warning')
              ? 'Worth a look'
              : 'Getting started'
          }
          warnings={notices}
        />
      )}

      <Panel
        headingLevel={1}
        title={`${formatMonth(month)} Settlement Cycle`}
        hint="Four stages take the month from logged costs to a sealed, immutable statement."
        actions={<StatementPill status={data.statement_status} />}
      >
        <LifecycleStages
          data={data}
          bills={monthBills}
          snapshotHash={snapshot.data?.snapshot_hash ?? null}
        />
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Panel
          className="lg:col-span-2"
          title="Suggested Peer-to-Peer Transfers"
          icon={<ArrowRightLeft className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
          hint="The fewest payments that square every balance. Advice only: recording a payment is a separate action."
          actions={
            data.suggested_transfers.length > 0 ? (
              <button
                type="button"
                onClick={() => setSharing(true)}
                className="inline-flex items-center gap-1.5 shrink-0 whitespace-nowrap rounded-xl bg-emerald-600 hover:bg-emerald-700 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs transition-colors cursor-pointer"
              >
                <MessageCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                Share WhatsApp Summary
              </button>
            ) : undefined
          }
        >
          {data.suggested_transfers.length === 0 ? (
            <EmptyBlock title="Nothing to settle" body="Every account is square." />
          ) : (
            <ul className="list-none m-0 p-0 space-y-3">
              {data.suggested_transfers.map((transfer) => (
                <TransferRow
                  key={`${transfer.from_account}-${transfer.to_account}-${transfer.amount_paise}`}
                  transfer={transfer}
                  note={upiNote(transfer, data.apartment.name, month)}
                  onSettle={() => setSettling(transfer)}
                />
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Resident Balances" hint="Each resident's cumulative position, across all months.">
          {residents.length === 0 ? (
            <EmptyBlock
              title="No residents yet"
              body="Add the people sharing this apartment before recording expenses."
              action={
                <Link className={BUTTON_PRIMARY} to="/residents">
                  Add residents
                </Link>
              }
            />
          ) : (
            <ul className="list-none m-0 p-0 space-y-3">
              {residents.map((account) => {
                const net = account.net_position_paise
                return (
                  <li key={account.account_id}>
                    <Link
                      to={`/residents/${account.account_id}`}
                      className={clsx(
                        'p-3.5 rounded-xl border flex items-center justify-between gap-2 min-w-0 no-underline transition-colors hover:border-[var(--border-medium)]',
                        net > 0 && 'border-emerald-500/30 bg-[var(--success-glow)]',
                        net < 0 && 'border-rose-500/30 bg-[var(--danger-glow)]',
                        net === 0 && 'border-[var(--border-subtle)] bg-[var(--bg-elevated)]',
                      )}
                    >
                      <span className="min-w-0">
                        <span className="block text-xs font-bold text-[var(--text-primary)] truncate">
                          {account.display_name}
                          {!account.is_active && (
                            <span className="font-normal text-[var(--text-tertiary)]"> · moved out</span>
                          )}
                        </span>
                        <span
                          className={clsx(
                            'block text-[11px] font-semibold truncate',
                            net > 0 && 'text-[var(--success)]',
                            net < 0 && 'text-[var(--danger)]',
                            net === 0 && 'text-[var(--text-secondary)]',
                          )}
                        >
                          {net > 0
                            ? `Household owes ${account.display_name}`
                            : net < 0
                              ? `${account.display_name} owes household`
                              : 'Settled'}
                        </span>
                      </span>
                      <Rupees paise={net} signed className="text-xs font-bold shrink-0" />
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
          <Link
            to="/reconciliation"
            title="Open the full reconciliation"
            className="pt-3 border-t border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-2 text-[11px] font-medium text-[var(--text-secondary)] no-underline hover:text-[var(--text-primary)] transition-colors"
          >
            <span>
              Net Checksum:{' '}
              <strong className="tabular text-[var(--text-primary)]">{formatInr(checksum)}</strong>
            </span>
            {checksum === 0 ? (
              <span className="inline-flex items-center gap-1 shrink-0 font-semibold text-[var(--success)]">
                <ShieldCheck className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                Verified
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 shrink-0 font-semibold text-[var(--danger)]">
                <ShieldAlert className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                Does not balance
              </span>
            )}
          </Link>
        </Panel>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MiniStat
          label="Outstanding charges"
          value={<Rupees paise={data.all_time.outstanding_charges_paise} />}
          note="All months, not yet settled by a payment"
        />
        <MiniStat
          label="Unapplied credits"
          value={<Rupees paise={data.all_time.unapplied_credits_paise} />}
          note="Overpayments carried forward"
        />
        <MiniStat
          label="Provider advances"
          value={<Rupees paise={data.all_time.provider_advances_paise} />}
          note="Residents' money paid to providers"
        />
        <MiniStat
          label="Paid into pool"
          value={<Rupees paise={data.month.resident_pool_payments_paise} />}
          note={`Resident transfers in ${formatMonth(month)}`}
        />
      </div>

      {sharing && (
        <ShareSummaryDialog
          statementId={data.statement?.statement_id ?? null}
          fallbackText={whatsAppSummary(data, monthBills, month)}
          onClose={() => setSharing(false)}
        />
      )}
      {settling && (
        <SettleDialog
          transfer={settling}
          pool={data.accounts.find((account) => account.account_type === 'pool')}
          onClose={() => setSettling(null)}
          onSettled={() => {
            setSettling(null)
            toast.success('Settlement recorded')
            onChanged()
          }}
        />
      )}
    </div>
  )
}

/* ------------------------------------------------------------- lifecycle -- */

type StageState = 'done' | 'active' | 'upcoming'

function LifecycleStages({
  data,
  bills,
  snapshotHash,
}: {
  data: Dashboard
  bills: ElectricityBillSummary[]
  snapshotHash: string | null
}) {
  const statement = data.statement
  const status = data.statement_status
  const sealed = status === 'finalized_correction_window' || status === 'closed'
  const statementLink = statement ? `/statements/${statement.statement_id}` : '/statements'

  const deadline = statement?.correction_deadline ? Date.parse(statement.correction_deadline) : NaN
  const hoursLeft = Number.isNaN(deadline) ? 0 : (deadline - Date.now()) / 3_600_000

  const billTotal = bills.reduce((sum, bill) => sum + bill.amount_paise, 0)
  const billWh = bills.reduce((sum, bill) => sum + bill.total_energy_wh, 0)

  const done = [data.month.expense_count > 0, bills.length > 0, sealed, status === 'closed']
  // A month can be sealed without an electricity bill, so once the statement
  // is sealed the only stage left to act on is the close.
  const active = sealed ? (done[3] ? -1 : 3) : done.findIndex((isDone) => !isDone)
  const stateOf = (index: number): StageState =>
    done[index] ? 'done' : index === active ? 'active' : 'upcoming'

  return (
    <ol className="list-none m-0 p-0 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
      <Stage
        state={stateOf(0)}
        title="1. Logging Costs"
        to="/expenses"
        headline={`${data.month.expense_count} cost${data.month.expense_count === 1 ? '' : 's'} logged`}
        detail={`${formatInr(data.month.household_expenses_paise)} total spend`}
        tabularDetail
      />
      <Stage
        state={stateOf(1)}
        title="2. Utility Metering"
        to="/services/electricity"
        headline={
          bills.length === 0
            ? 'No electricity bill'
            : bills.length === 1
              ? 'Electricity bill entered'
              : `${bills.length} electricity bills`
        }
        detail={
          bills.length === 0
            ? 'Nothing settles this month yet'
            : `${formatInr(billTotal)} (${(billWh / 1000).toFixed(billWh % 1000 === 0 ? 0 : 1)} kWh)`
        }
        tabularDetail={bills.length > 0}
      />
      <Stage
        state={stateOf(2)}
        title="3. Hashed Statement"
        to={statementLink}
        headline={
          sealed ? 'Snapshot sealed' : status === 'draft' ? 'Draft under review' : 'No statement yet'
        }
        detail={
          sealed
            ? snapshotHash
              ? `SHA-256 #${snapshotHash.slice(0, 6)}…${snapshotHash.slice(-4)}`
              : 'SHA-256 stored'
            : status === 'draft'
              ? 'Review, then finalize to seal it'
              : 'Create a draft to begin'
        }
        tabularDetail={sealed}
      />
      <Stage
        state={stateOf(3)}
        title="4. Permanent Close"
        to={statementLink}
        locked={!sealed}
        headline={
          status === 'closed' ? 'Closed and immutable' : sealed ? 'Correction window' : 'Locked'
        }
        detail={
          status === 'closed'
            ? `Closed ${formatDate(statement?.closed_at)}`
            : sealed
              ? hoursLeft > 0
                ? formatHoursRemaining(hoursLeft)
                : 'Window expired — ready to close'
              : 'Opens once the statement is finalized'
        }
      />
    </ol>
  )
}

function Stage({
  state,
  title,
  headline,
  detail,
  to,
  locked,
  tabularDetail,
}: {
  state: StageState
  title: string
  headline: string
  detail: string
  to: string
  locked?: boolean
  tabularDetail?: boolean
}) {
  return (
    <li className="min-w-0">
      <Link
        to={to}
        aria-current={state === 'active' ? 'step' : undefined}
        className={clsx(
          'block h-full p-3.5 rounded-xl min-w-0 no-underline transition-colors',
          state === 'done' && 'border border-emerald-500/30 bg-[var(--success-glow)]',
          state === 'active' && 'border-2 border-blue-500 bg-[var(--accent-glow)] shadow-xs',
          state === 'upcoming' &&
            'border border-[var(--border-subtle)] bg-[var(--bg-elevated)] opacity-70 hover:opacity-100',
        )}
      >
        <span
          className={clsx(
            'text-[11px] font-bold flex items-center justify-between gap-2 min-w-0',
            state === 'done' && 'text-[var(--success)]',
            state === 'active' && 'text-[var(--accent)]',
            state === 'upcoming' && 'text-[var(--text-tertiary)]',
          )}
        >
          <span className="min-w-0 truncate">{title}</span>
          {state === 'done' && (
            <>
              <CheckCircle2 className="w-4 h-4 shrink-0" aria-hidden="true" />
              <span className="sr-only">Done</span>
            </>
          )}
          {state === 'active' && (
            <span className="shrink-0 text-[9px] uppercase tracking-wider font-extrabold px-1.5 py-0.5 bg-blue-500 text-white rounded">
              Active
            </span>
          )}
          {state === 'upcoming' && locked && (
            <Lock className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          )}
        </span>
        <span className="block text-xs font-bold text-[var(--text-primary)] mt-1.5 truncate">
          {headline}
        </span>
        <span
          className={clsx(
            'block text-[11px] text-[var(--text-secondary)] font-medium truncate',
            tabularDetail && 'tabular',
          )}
        >
          {detail}
        </span>
      </Link>
    </li>
  )
}

/* -------------------------------------------------------------- transfers -- */

/** The house kitty's account in the reconciliation; every other account is a resident. */
const POOL_ACCOUNT_ID = '__pool__'

function upiNote(transfer: TransferSuggestion, apartmentName: string, month: string): string {
  return `${transfer.from_display_name} to ${transfer.to_display_name}: ${formatInr(transfer.amount_paise)} — ${apartmentName}, ${formatMonth(month)} settlement`
}

function TransferRow({
  transfer,
  note,
  onSettle,
}: {
  transfer: TransferSuggestion
  note: string
  onSettle: () => void
}) {
  // The breakdown behind a transfer is the payer's statement, or the payee's
  // when the pool is paying them out.
  const debtorId = transfer.from_account !== POOL_ACCOUNT_ID ? transfer.from_account : null
  const creditorId = transfer.to_account !== POOL_ACCOUNT_ID ? transfer.to_account : null
  const targetResident = debtorId
    ? { id: debtorId, name: transfer.from_display_name }
    : creditorId
      ? { id: creditorId, name: transfer.to_display_name }
      : null
  const party = (id: string | null, name: string) =>
    id ? (
      <Link
        to={`/residents/${encodeURIComponent(id)}`}
        className="truncate hover:underline text-[var(--text-primary)] focus-visible:underline"
      >
        {name}
      </Link>
    ) : (
      <span className="truncate">{name}</span>
    )
  return (
    <li className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 sm:p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] hover:border-blue-500/40 transition-colors min-w-0">
      <div className="flex items-center gap-3.5 min-w-0">
        <Avatar name={transfer.from_display_name} tone="danger" />
        <div className="min-w-0">
          <div className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5 min-w-0">
            {party(debtorId, transfer.from_display_name)}
            <ArrowRight className="w-3.5 h-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
            <span className="sr-only">pays</span>
            {party(creditorId, transfer.to_display_name)}
          </div>
          <Rupees paise={transfer.amount_paise} tone="danger" className="block text-sm font-bold mt-0.5" />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 shrink-0">
        <CopyButton text={note} label="Copy UPI Note" />
        {targetResident && (
          <Link
            to={`/statements/residents/${encodeURIComponent(targetResident.id)}`}
            className={BUTTON_SECONDARY}
            title={`View monthly statement breakdown for ${targetResident.name}`}
            aria-label={`View Statement for ${targetResident.name}`}
          >
            View Statement
          </Link>
        )}
        <button type="button" className={BUTTON_QUIET} onClick={onSettle}>
          Mark Settle
        </button>
      </div>
    </li>
  )
}

function whatsAppSummary(data: Dashboard, bills: ElectricityBillSummary[], month: string): string {
  const lines = [
    `🏠 *${data.apartment.name} — ${formatMonth(month)} settlement*`,
    `💰 *Household spend:* ${formatInr(data.month.household_expenses_paise)}`,
  ]
  if (bills.length > 0) {
    const total = bills.reduce((sum, bill) => sum + bill.amount_paise, 0)
    lines.push(`⚡ *Electricity:* ${formatInr(total)}`)
  }
  lines.push('━━━━━━━━━━━━━━━━━━━━━', '👥 *Transfers to settle:*')
  for (const transfer of data.suggested_transfers) {
    lines.push(
      `🔴 *${transfer.from_display_name} pays ${transfer.to_display_name}:* ${formatInr(transfer.amount_paise)}`,
    )
  }
  lines.push('━━━━━━━━━━━━━━━━━━━━━', '_Generated by Apartment Expense Manager_')
  return lines.join('\n')
}

/**
 * Recording a suggested transfer as paid. This is the one action on the hub
 * that writes to the ledger, so it asks before it does.
 */
function SettleDialog({
  transfer,
  pool,
  onClose,
  onSettled,
}: {
  transfer: TransferSuggestion
  /** The pool's account, which also says where to pay it. */
  pool: ReconciliationAccount | undefined
  onClose: () => void
  onSettled: () => void
}) {
  const poolAccountId = pool?.account_id
  const [paymentDate, setPaymentDate] = useState(todayIso())
  const [reference, setReference] = useState('')

  const settle = useMutation(
    () => {
      const common = {
        amount_paise: transfer.amount_paise,
        payment_date: paymentDate,
        reference: reference.trim() || null,
      }
      if (transfer.to_account === poolAccountId) {
        return api.post('/api/payments', {
          ...common,
          payer_resident_id: transfer.from_account,
          payment_kind: 'to_pool',
        })
      }
      if (transfer.from_account === poolAccountId) {
        return api.post('/api/payments', {
          ...common,
          payer_resident_id: transfer.to_account,
          payment_kind: 'refund',
        })
      }
      return api.post('/api/payments/settle-p2p', {
        ...common,
        from_resident_id: transfer.from_account,
        to_resident_id: transfer.to_account,
      })
    },
    { onSuccess: onSettled },
  )

  return (
    <Modal
      title="Mark this transfer as settled"
      description="Record it only once the money has actually moved. It can be reversed later from Payments, with a reason."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={settle.pending}>
            Cancel
          </button>
          <button
            type="submit"
            form="settle-form"
            className={BUTTON_PRIMARY}
            disabled={settle.pending || !paymentDate}
          >
            {settle.pending ? 'Recording…' : 'Record settlement'}
          </button>
        </>
      }
    >
      <form
        id="settle-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          void settle.run(undefined)
        }}
      >
        <ErrorNotice error={settle.error} />
        <p className="m-0 text-sm text-[var(--text-primary)]">
          <strong>{transfer.from_display_name}</strong> paid{' '}
          <strong>{transfer.to_display_name}</strong>{' '}
          <Rupees paise={transfer.amount_paise} className="font-bold" />.
        </p>
        {pool && transfer.to_account === pool.account_id && (
          <TransferDestination
            name={pool.display_name}
            amountPaise={transfer.amount_paise}
            bankAccount={pool.bank_account}
            bankIfsc={pool.bank_ifsc}
            upiId={pool.upi_id}
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
        )}
        <div className="form-grid">
          <DateField
            label="Paid on"
            value={paymentDate}
            onChange={setPaymentDate}
            required
          />
          <TextField
            label="Reference"
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="UPI reference number"
          />
        </div>
      </form>
    </Modal>
  )
}

function ShareSummaryDialog({
  statementId,
  fallbackText,
  onClose,
}: {
  statementId: string | null
  fallbackText: string
  onClose: () => void
}) {
  // With a statement, the summary comes from the backend so it carries each
  // resident's share, UPI links, and the sealed hash. Without one there is
  // only the live dashboard to describe.
  const summary = useResource<WhatsAppSummary>(
    statementId ? `/api/statements/${encodeURIComponent(statementId)}/share/whatsapp` : null,
  )
  const waiting = Boolean(statementId) && summary.initialLoading
  const text = summary.data?.text ?? fallbackText

  return (
    <Modal
      title="WhatsApp Group Summary"
      description="Copy this into the flat's group chat. Sharing it changes nothing in the accounts."
      onClose={onClose}
      footer={
        waiting ? undefined : (
          <>
            <a
              className={BUTTON_SECONDARY}
              href={summary.data?.share_url ?? `https://wa.me/?text=${encodeURIComponent(text)}`}
              target="_blank"
              rel="noreferrer"
            >
              Open WhatsApp
            </a>
            <CopyButton
              text={text}
              label="Copy to Clipboard"
              className="inline-flex items-center gap-1.5 shrink-0 whitespace-nowrap rounded-xl bg-emerald-600 hover:bg-emerald-700 px-4 py-2 text-xs font-bold text-white shadow-xs transition-colors cursor-pointer"
            />
          </>
        )
      }
    >
      {waiting ? (
        <p className="m-0 text-xs text-[var(--text-secondary)]" role="status">
          Preparing the summary…
        </p>
      ) : (
        <pre className="m-0 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 font-mono text-xs text-[var(--text-primary)] whitespace-pre-wrap break-words leading-relaxed select-all max-h-64 overflow-y-auto">
          {text}
        </pre>
      )}
    </Modal>
  )
}

/* --------------------------------------------------------------- warnings -- */

function WarningList({
  tone,
  title,
  warnings,
}: {
  tone: 'danger' | 'warning' | 'info'
  title: string
  warnings: DashboardWarning[]
}) {
  const Icon = tone === 'info' ? Info : AlertTriangle
  return (
    <div
      role={tone === 'danger' ? 'alert' : undefined}
      className={clsx(
        'rounded-2xl border p-4 flex gap-3 min-w-0',
        tone === 'danger' && 'border-rose-500/30 bg-[var(--danger-glow)]',
        tone === 'warning' && 'border-amber-500/30 bg-amber-500/10',
        tone === 'info' && 'border-blue-500/25 bg-[var(--accent-glow)]',
      )}
    >
      <Icon
        className={clsx(
          'w-4 h-4 shrink-0 mt-0.5',
          tone === 'danger' && 'text-[var(--danger)]',
          tone === 'warning' && 'text-[var(--warning)]',
          tone === 'info' && 'text-[var(--accent)]',
        )}
        aria-hidden="true"
      />
      <div className="min-w-0 space-y-1">
        <div className="text-sm font-bold text-[var(--text-primary)]">{title}</div>
        <ul className="list-disc m-0 pl-4 space-y-0.5 text-xs text-[var(--text-primary)]">
          {warnings.map((warning) => (
            <li key={`${warning.code}-${warning.entity_id}`}>
              {warning.message}
              {warning.suggested_action && (
                <span className="text-[var(--text-secondary)]"> {warning.suggested_action}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
