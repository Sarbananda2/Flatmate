/**
 * Statements.
 *
 * The two records that make a month auditable sit side by side here: how the
 * electricity bill was split, and the sealed statement with its SHA-256 hash.
 * Both are read straight from what the backend stored.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Download, Fingerprint, ShieldAlert, ShieldCheck, Zap } from 'lucide-react'
import { useApp } from '../components/Layout'
import { AsyncSection, ErrorNotice, Modal } from '../components/Primitives'
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  CopyButton,
  EmptyBlock,
  Panel,
  Pill,
  Rupees,
  StatementPill,
} from '../components/ui'
import { TextAreaField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { api, csvUrl } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import {
  ALLOCATION_METHOD_LABELS,
  formatDate,
  formatDateTime,
  formatHoursRemaining,
  formatInr,
  formatMonth,
  formatMonthShort,
  whToKwh,
} from '../lib/format'
import type {
  ElectricityBillSummary,
  SavedElectricityBill,
  StatementRow,
  StatementSnapshot,
} from '../api/types'

export default function StatementsPage() {
  const { month } = useApp()
  const toast = useToast()
  const statements = useResource<{
    items: StatementRow[]
    total: number
    available_months: string[]
  }>('/api/statements', { limit: 100 })
  const [creating, setCreating] = useState(false)

  const closeExpired = useMutation(
    () => api.post<{ closed_count: number }>('/api/statements/close-expired'),
    {
      onSuccess: (result) => {
        statements.reload()
        toast.success(
          result.closed_count
            ? `Closed ${result.closed_count} statement${result.closed_count === 1 ? '' : 's'}`
            : 'No statement had an expired correction window',
        )
      },
    },
  )

  const existingForMonth =
    statements.data?.items.find((statement) => statement.settlement_month === month) ?? null

  return (
    <div className="space-y-6">
      <ErrorNotice error={closeExpired.error} />

      <MeterAllocationPanel month={month} />

      <VaultPanel
        month={month}
        statement={existingForMonth}
        loading={statements.initialLoading}
        onCreate={() => setCreating(true)}
      />

      <Panel
        title="All Statements"
        hint="One per settlement month: a draft while you work on it, finalized with a correction window, then closed and immutable."
        actions={
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={closeExpired.pending}
            onClick={() => void closeExpired.run(undefined)}
          >
            {closeExpired.pending ? 'Checking…' : 'Close expired windows'}
          </button>
        }
      >
        <AsyncSection
          resource={statements}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyBlock
              title="No statements yet"
              body={`Create a draft for ${formatMonth(month)} to start settling up.`}
              action={
                <button type="button" className={BUTTON_PRIMARY} onClick={() => setCreating(true)}>
                  Create a draft statement
                </button>
              }
            />
          }
        >
          {(data) => (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[620px] border-collapse">
                <caption className="sr-only">Statements</caption>
                <thead>
                  <tr className="border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold">
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Settlement month</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Status</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Expenses</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Total</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Finalized</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Correction window</th>
                    <th scope="col" className="pb-3 font-bold text-center">Revisions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {data.items.map((statement) => (
                    <tr
                      key={statement.statement_id}
                      className="hover:bg-[var(--bg-elevated)] transition-colors"
                    >
                      <th scope="row" className="py-3.5 pr-3 text-left font-semibold whitespace-nowrap">
                        <Link to={`/statements/${statement.statement_id}`}>
                          {formatMonth(statement.settlement_month)}
                        </Link>
                      </th>
                      <td className="py-3.5 pr-3">
                        <StatementPill status={statement.status} />
                      </td>
                      <td className="py-3.5 pr-3 text-right tabular text-[var(--text-secondary)]">
                        {statement.expense_count}
                      </td>
                      <td className="py-3.5 pr-3 text-right font-bold text-[var(--text-primary)]">
                        <Rupees paise={statement.statement_total_paise} />
                      </td>
                      <td className="py-3.5 pr-3 whitespace-nowrap text-[var(--text-secondary)]">
                        {statement.finalized_at ? (
                          <>
                            <span className="tabular">{formatDateTime(statement.finalized_at)}</span>
                            <span className="block text-[11px] text-[var(--text-tertiary)]">
                              by {statement.finalized_by ?? '—'}
                            </span>
                          </>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="py-3.5 pr-3 whitespace-nowrap text-[var(--text-secondary)]">
                        {statement.status === 'finalized_correction_window' ? (
                          statement.correction_window_open ? (
                            <Pill tone="warning">
                              {formatHoursRemaining(statement.correction_hours_remaining)}
                            </Pill>
                          ) : (
                            <Pill>expired — ready to close</Pill>
                          )
                        ) : statement.status === 'closed' ? (
                          <span className="tabular">closed {formatDateTime(statement.closed_at)}</span>
                        ) : (
                          'not finalized'
                        )}
                      </td>
                      <td className="py-3.5 text-right tabular text-[var(--text-secondary)]">
                        {statement.revision_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </Panel>

      {creating && (
        <CreateStatementDialog
          defaultMonth={month}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            statements.reload()
            toast.success('Draft statement created')
          }}
        />
      )}
    </div>
  )
}

/* ------------------------------------------------------- meter allocation -- */

function MeterAllocationPanel({ month }: { month: string }) {
  const bills = useResource<{ items: ElectricityBillSummary[] }>('/api/electricity/bills', {
    limit: 100,
  })
  const monthBills = (bills.data?.items ?? []).filter((bill) => bill.settlement_period === month)
  const bill = monthBills[0] ?? null

  return (
    <Panel
      headingLevel={1}
      title="Smart Meter Allocation"
      icon={<Zap className="w-4 h-4 shrink-0 text-[var(--warning)]" aria-hidden="true" />}
      hint="The bill and its charges are taken as given. Smart-plug readings only decide how the usage charge is shared."
      actions={
        <>
          {bill && (
            <Pill tone="accent">
              Service {formatMonthShort(bill.service_period)} → Settles {formatMonthShort(month)}
            </Pill>
          )}
          <Link className={BUTTON_SECONDARY} to="/services/electricity">
            {bill ? 'Bill history' : 'Enter a bill'}
          </Link>
        </>
      }
    >
      <AsyncSection resource={bills}>
        {() =>
          bill ? (
            <>
              <BillBreakdown expenseId={bill.expense_id} />
              {monthBills.length > 1 && (
                <p className="m-0 text-xs text-[var(--text-secondary)]">
                  {monthBills.length - 1} more bill{monthBills.length === 2 ? '' : 's'} settle in{' '}
                  {formatMonth(month)}. <Link to="/services/electricity">See them all.</Link>
                </p>
              )}
            </>
          ) : (
            <EmptyBlock
              title={`No electricity bill settles in ${formatMonth(month)}`}
              body="Enter the bill and each room's smart-plug reading to see how it is shared."
              action={
                <Link className={BUTTON_PRIMARY} to="/services/electricity">
                  Enter a bill
                </Link>
              }
            />
          )
        }
      </AsyncSection>
    </Panel>
  )
}

function BillBreakdown({ expenseId }: { expenseId: string }) {
  const resource = useResource<SavedElectricityBill>(
    `/api/electricity/bills/${encodeURIComponent(expenseId)}`,
  )
  return (
    <AsyncSection resource={resource}>
      {(data) => {
        const names = new Map(data.allocations.map((row) => [row.resident_id, row.display_name]))
        const nameOf = (residentId: string) => names.get(residentId) ?? residentId
        const totalWh = data.readings.reduce((sum, reading) => sum + reading.energy_wh, 0)
        const whByResident = new Map<string, number>()
        for (const reading of data.readings) {
          whByResident.set(
            reading.resident_id,
            (whByResident.get(reading.resident_id) ?? 0) + reading.energy_wh,
          )
        }
        const residentIds = Object.keys(data.resident_totals_paise).sort((a, b) =>
          nameOf(a).localeCompare(nameOf(b)),
        )
        const subCard =
          'p-4 sm:p-5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] space-y-3.5 min-w-0'
        const subHeading =
          'm-0 font-bold text-xs uppercase tracking-wider text-[var(--text-tertiary)]'

        return (
          <div className="space-y-4">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className={subCard}>
                <h3 className={subHeading}>1. Bill Components</h3>
                <ul className="list-none m-0 p-0 space-y-3 text-xs">
                  {data.components.map((component) => (
                    <li key={component.component_id} className="flex items-center justify-between gap-2">
                      <span className="min-w-0">
                        <span className="block font-semibold text-[var(--text-primary)] truncate">
                          {component.name}
                        </span>
                        <span className="block text-[11px] text-[var(--text-secondary)] truncate">
                          {ALLOCATION_METHOD_LABELS[component.allocation_method] ??
                            component.allocation_method}
                        </span>
                      </span>
                      <Rupees
                        paise={component.amount_paise}
                        className="font-bold text-[var(--text-primary)] shrink-0"
                      />
                    </li>
                  ))}
                </ul>
                <div className="pt-2 border-t border-[var(--border-subtle)] flex justify-between gap-2 font-bold text-sm text-[var(--text-primary)]">
                  <span>Total Bill</span>
                  <Rupees paise={data.expense.amount_paise} tone="accent" />
                </div>
              </div>

              <div className={subCard}>
                <h3 className={subHeading}>2. Smart-Plug Readings</h3>
                {data.readings.length === 0 ? (
                  <p className="m-0 text-xs text-[var(--text-secondary)]">
                    No readings were entered for this bill.
                  </p>
                ) : (
                  <ul className="list-none m-0 p-0 space-y-3 text-xs">
                    {data.readings.map((reading) => (
                      <li key={reading.reading_id} className="flex items-center justify-between gap-2">
                        <span className="min-w-0">
                          <span className="block font-semibold text-[var(--text-primary)] truncate">
                            {nameOf(reading.resident_id)}
                          </span>
                          <span className="block text-[11px] text-[var(--text-secondary)] truncate">
                            {reading.device_label}
                          </span>
                        </span>
                        <span className="tabular font-bold text-[var(--text-primary)] shrink-0">
                          {whToKwh(reading.energy_wh)} kWh
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="pt-2 border-t border-[var(--border-subtle)] flex justify-between gap-2 text-xs font-bold text-[var(--text-primary)]">
                  <span>Total Measured</span>
                  <span className="tabular">{whToKwh(totalWh)} kWh</span>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
              {residentIds.map((residentId) => {
                const parts = data.allocations
                  .filter((row) => row.resident_id === residentId && row.amount_paise !== 0)
                  .map((row) => `${row.component_name ?? 'Share'}: ${formatInr(row.amount_paise)}`)
                  .join(' + ')
                const wh = whByResident.get(residentId) ?? 0
                return (
                  <div
                    key={residentId}
                    className="p-4 border border-[var(--border-subtle)] rounded-xl bg-[var(--bg-elevated)] min-w-0"
                  >
                    <div className="text-xs font-bold text-[var(--text-primary)] flex justify-between items-center gap-2 min-w-0">
                      <span className="truncate">{nameOf(residentId)}</span>
                      {totalWh > 0 && (
                        <span className="tabular font-normal text-[var(--text-secondary)] text-[11px] shrink-0">
                          {((wh / totalWh) * 100).toFixed(1)}% of usage
                        </span>
                      )}
                    </div>
                    <Rupees
                      paise={data.resident_totals_paise[residentId]}
                      tone="accent"
                      className="block text-base font-bold mt-1"
                    />
                    <div className="tabular text-[11px] text-[var(--text-tertiary)] truncate mt-0.5" title={parts}>
                      {parts}
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-medium text-[var(--text-secondary)]">
              <span>
                Bill period{' '}
                <span className="tabular">
                  {formatDate(data.bill?.bill_period_start)} – {formatDate(data.bill?.bill_period_end)}
                </span>
              </span>
              {data.reconciles ? (
                <span className="inline-flex items-center gap-1 shrink-0 font-semibold text-[var(--success)]">
                  <ShieldCheck className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Shares add up to the bill exactly
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 shrink-0 font-semibold text-[var(--danger)]">
                  <ShieldAlert className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Shares do not add up to the bill
                </span>
              )}
            </div>
          </div>
        )
      }}
    </AsyncSection>
  )
}

/* ------------------------------------------------------------------ vault -- */

interface SnapshotEnvelope {
  snapshot: StatementSnapshot
  snapshot_hash: string | null
  hash_matches: boolean
}

function VaultPanel({
  month,
  statement,
  loading,
  onCreate,
}: {
  month: string
  statement: StatementRow | null
  loading: boolean
  onCreate: () => void
}) {
  const snapshot = useResource<SnapshotEnvelope>(
    statement?.has_snapshot
      ? `/api/statements/${encodeURIComponent(statement.statement_id)}/snapshot`
      : null,
  )

  return (
    <Panel
      title="SHA-256 Statement Vault"
      icon={<Fingerprint className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={`The sealed record for ${formatMonth(month)}. Once finalized, the snapshot is stored with its hash and never rewritten.`}
      actions={statement ? <StatementPill status={statement.status} /> : undefined}
    >
      {loading ? (
        <p className="m-0 text-xs text-[var(--text-secondary)]" role="status">
          Loading…
        </p>
      ) : !statement ? (
        <EmptyBlock
          title={`No statement for ${formatMonth(month)} yet`}
          body="Create a draft to collect the month's expenses, then finalize it to seal a hashed snapshot."
          action={
            <button type="button" className={BUTTON_PRIMARY} onClick={onCreate}>
              Create {formatMonth(month)} draft
            </button>
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
            <div className="min-w-0">
              <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                Statement total
              </div>
              <Rupees
                paise={statement.statement_total_paise}
                className="block text-base font-bold text-[var(--text-primary)]"
              />
            </div>
            <div className="min-w-0">
              <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                Expenses
              </div>
              <div className="tabular text-base font-bold text-[var(--text-primary)]">
                {statement.expense_count}
              </div>
            </div>
            <div className="min-w-0">
              <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                {statement.status === 'closed' ? 'Closed' : 'Finalized'}
              </div>
              <div className="tabular text-sm font-bold text-[var(--text-primary)] truncate">
                {formatDateTime(
                  statement.status === 'closed' ? statement.closed_at : statement.finalized_at,
                )}
              </div>
            </div>
          </div>

          {statement.has_snapshot ? (
            <AsyncSection resource={snapshot}>
              {(sealed) => (
                <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-3 min-w-0">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                      Snapshot hash
                    </span>
                    {sealed.hash_matches ? (
                      <span className="inline-flex items-center gap-1 shrink-0 text-[11px] font-semibold text-[var(--success)]">
                        <ShieldCheck className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                        Verified against the stored snapshot
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 shrink-0 text-[11px] font-semibold text-[var(--danger)]">
                        <ShieldAlert className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                        Hash mismatch — the stored snapshot was altered
                      </span>
                    )}
                  </div>
                  <div className="tabular text-xs text-[var(--text-primary)] break-all select-all">
                    {sealed.snapshot_hash ?? '—'}
                  </div>
                  {sealed.snapshot_hash && (
                    <CopyButton text={sealed.snapshot_hash} label="Copy hash" />
                  )}
                </div>
              )}
            </AsyncSection>
          ) : (
            <p className="m-0 rounded-xl border border-dashed border-[var(--border-medium)] px-4 py-3 text-xs text-[var(--text-secondary)]">
              Not sealed yet. This statement is a draft; finalizing it stores the snapshot and its
              SHA-256 hash.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Link className={BUTTON_PRIMARY} to={`/statements/${statement.statement_id}`}>
              {statement.status === 'draft' ? 'Review & finalize' : 'Open statement'}
            </Link>
            <a
              className={BUTTON_SECONDARY}
              href={csvUrl(`/api/statements/${encodeURIComponent(statement.statement_id)}/export/csv`)}
              target="_blank"
              rel="noreferrer"
            >
              <Download className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Download CSV Statement
            </a>
          </div>
        </>
      )}
    </Panel>
  )
}

function CreateStatementDialog({
  defaultMonth,
  onClose,
  onCreated,
}: {
  defaultMonth: string
  onClose: () => void
  onCreated: () => void
}) {
  const [settlementMonth, setSettlementMonth] = useState(defaultMonth)
  const [windowHours, setWindowHours] = useState('72')
  const [notes, setNotes] = useState('')

  const create = useMutation(
    () =>
      api.post<StatementRow>('/api/statements', {
        settlement_month: settlementMonth,
        correction_window_hours: Number(windowHours) || 72,
        notes: notes.trim() || null,
      }),
    { onSuccess: onCreated },
  )

  const hoursError =
    Number.isNaN(Number(windowHours)) || Number(windowHours) < 0
      ? 'Enter a whole number of hours.'
      : null

  return (
    <Modal
      title="Create a draft statement"
      description="A statement collects the expenses whose settlement period is this month, by each expense's own period rule."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={create.pending}>
            Cancel
          </button>
          <button type="submit" form="statement-form" className={BUTTON_PRIMARY} disabled={create.pending}>
            {create.pending ? 'Creating…' : 'Create draft'}
          </button>
        </>
      }
    >
      <form
        id="statement-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          if (!hoursError) void create.run(undefined)
        }}
      >
        <ErrorNotice error={create.error} />
        <div className="form-grid">
          <TextField
            label="Settlement month"
            type="month"
            value={settlementMonth}
            onChange={setSettlementMonth}
            required
            hint="Only one statement can exist per month."
          />
          <TextField
            label="Correction window (hours)"
            value={windowHours}
            onChange={setWindowHours}
            error={hoursError}
            hint="72 hours is the agreed reading of “three days”. It can be changed per statement."
          />
        </div>
        <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
      </form>
    </Modal>
  )
}
