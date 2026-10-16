/**
 * Payments, credits, provider advances, and adjustments.
 *
 * Four transaction types, none of them a household expense:
 *
 * - into the pool — settles the payer's oldest open charges first;
 * - to a provider — funds an expense; link it so the payer is not credited twice;
 * - a refund out of the pool;
 * - a signed ledger adjustment, which is the only way to correct a closed statement.
 *
 * Reversals are mirrored entries, never edits, so the audit trail stays whole.
 *
 * The household pool's own account -- where residents send it money -- is
 * shown at the top, read-only, and edited in a dialog.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Page, useApp } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Modal,
  Money,
  Pagination,
  Stat,
} from '../components/Primitives'
import { DateField, MoneyInput, SelectField, TextAreaField, TextField } from '../components/Form'
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  CopyButton,
  DetailFact,
  EditButton,
  Panel,
  TransferDestination,
} from '../components/ui'
import { useToast } from '../components/Toast'
import { api, downloadCsv } from '../api/client'
import { useDebounced, useMutation, useResource } from '../lib/useApi'
import {
  ACCOUNT_MESSAGE,
  ACCOUNT_PATTERN,
  IFSC_MESSAGE,
  IFSC_PATTERN,
  UPI_MESSAGE,
  UPI_PATTERN,
  patternError,
} from '../lib/validation'
import {
  PAYMENT_KIND_LABELS,
  formatDate,
  formatInr,
  formatMonth,
  todayIso,
} from '../lib/format'
import type {
  AdjustmentRow,
  Apartment,
  CreditRow,
  ExpenseRow,
  Paged,
  PaymentDetail,
  PaymentKind,
  PaymentRow,
  ProviderAdvanceRow,
  RentLeaseConfig,
  Resident,
  StatementRow,
} from '../api/types'

const PAGE_SIZE = 25
type Tab = 'payments' | 'credits' | 'advances' | 'adjustments'

export default function PaymentsPage() {
  const { apartment, reloadApartment } = useApp()
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('payments')
  const residents = useResource<{ items: Resident[] }>('/api/residents')

  const [search, setSearch] = useState('')
  const [residentId, setResidentId] = useState('')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [offset, setOffset] = useState(0)
  const debouncedSearch = useDebounced(search)

  const payments = useResource<Paged<PaymentRow> & { total_amount_paise: number }>(
    '/api/payments',
    {
      resident_id: residentId || undefined,
      payment_kind: kind || undefined,
      status: status || undefined,
      search: debouncedSearch || undefined,
      limit: PAGE_SIZE,
      offset,
    },
  )
  const credits = useResource<{
    items: CreditRow[]
    total_paise: number
    by_resident_paise: Record<string, number>
  }>('/api/credits')
  const advances = useResource<{
    items: ProviderAdvanceRow[]
    total_paise: number
    unlinked_count: number
  }>('/api/provider-advances')
  const adjustments = useResource<Paged<AdjustmentRow>>('/api/adjustments', { limit: 200 })

  const [recording, setRecording] = useState<PaymentKind | null>(null)
  const [adjusting, setAdjusting] = useState(false)
  const [viewing, setViewing] = useState<string | null>(null)
  const [reversing, setReversing] = useState<PaymentRow | null>(null)
  const [reversingAdjustment, setReversingAdjustment] = useState<AdjustmentRow | null>(null)

  const refresh = () => {
    payments.reload()
    credits.reload()
    advances.reload()
    adjustments.reload()
  }

  return (
    <Page
      title="Payments & credits"
      subtitle="Money moving between residents, the household pool, and external providers. None of this is a household expense."
      breadcrumbs={[{ label: 'Record' }, { label: 'Payments & credits' }]}
      actions={
        <>
          <button type="button" className="btn" onClick={() => downloadCsv('/api/payments/export/csv')}>
            Export CSV
          </button>
          <button type="button" className="btn" onClick={() => setRecording('provider_advance')}>
            Paid a provider
          </button>
          <button type="button" className="btn btn--primary" onClick={() => setRecording('to_pool')}>
            Record a payment
          </button>
        </>
      }
    >
      <div className="grid grid--stats">
        <Stat
          label="Payments into the pool"
          value={formatInr(
            (payments.data?.items ?? [])
              .filter((row) => row.payment_kind === 'to_pool' && row.status === 'active')
              .reduce((sum, row) => sum + row.amount_paise, 0),
          )}
          note="On this page, active only"
        />
        <Stat
          label="Unapplied credits"
          value={formatInr(credits.data?.total_paise ?? 0)}
          note="Overpayments held for residents; they carry forward"
        />
        <Stat
          label="Provider advances"
          value={formatInr(advances.data?.total_paise ?? 0)}
          note={
            advances.data?.unlinked_count
              ? `${advances.data.unlinked_count} not linked to an expense`
              : 'All linked to the expense they fund'
          }
        />
        <Stat
          label="Net adjustments"
          value={formatInr(
            (adjustments.data?.items ?? []).reduce((sum, row) => sum + row.amount_paise, 0),
            { sign: 'always' },
          )}
          note="Signed corrections against residents"
        />
      </div>

      <HouseholdPoolCard apartment={apartment} onSaved={reloadApartment} />

      <Callout tone="info" title="These are four different things">
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          <li>
            <strong>Into the pool</strong> — a resident hands money to the household. It settles
            their oldest open charges first; anything left over stays a visible credit.
          </li>
          <li>
            <strong>Paid a provider</strong> — a resident paid an external bill on the household's
            behalf. <strong>Link it to the expense it funds</strong>; the linked advance then
            replaces that expense's payer field, so the same rupee is credited once.
          </li>
          <li>
            <strong>Refund</strong> — the pool pays money back out to a resident.
          </li>
          <li>
            <strong>Adjustment</strong> — a signed correction. Positive credits the resident,
            negative debits them. This is the only way to correct a closed statement.
          </li>
        </ul>
        A resident paying another resident directly is two of these, not an expense: record it as a
        payment into the pool plus a refund, or as a pair of adjustments.
      </Callout>

      <div className="tabs" role="tablist" aria-label="Payment views">
        {(
          [
            ['payments', 'All payments'],
            ['credits', `Unapplied credits${credits.data ? ` (${credits.data.items.length})` : ''}`],
            ['advances', `Provider advances${advances.data ? ` (${advances.data.items.length})` : ''}`],
            ['adjustments', `Adjustments${adjustments.data ? ` (${adjustments.data.total})` : ''}`],
          ] as Array<[Tab, string]>
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'payments' && (
        <>
          <Card title="Filters">
            <div className="filters">
              <TextField
                label="Search"
                type="search"
                value={search}
                onChange={(value) => {
                  setSearch(value)
                  setOffset(0)
                }}
                placeholder="Provider, reference, note…"
              />
              <SelectField
                label="Resident"
                value={residentId}
                options={(residents.data?.items ?? []).map((resident) => ({
                  value: resident.resident_id,
                  label: resident.display_name,
                }))}
                onChange={(value) => {
                  setResidentId(value)
                  setOffset(0)
                }}
                placeholder="Anyone"
              />
              <SelectField
                label="Kind"
                value={kind}
                options={[
                  { value: 'to_pool', label: PAYMENT_KIND_LABELS.to_pool },
                  { value: 'provider_advance', label: PAYMENT_KIND_LABELS.provider_advance },
                  { value: 'refund', label: PAYMENT_KIND_LABELS.refund },
                  { value: 'adjustment', label: PAYMENT_KIND_LABELS.adjustment },
                ]}
                onChange={(value) => {
                  setKind(value)
                  setOffset(0)
                }}
                placeholder="All kinds"
              />
              <SelectField
                label="Status"
                value={status}
                options={[
                  { value: 'active', label: 'Active' },
                  { value: 'reversed', label: 'Reversed' },
                  { value: 'reversal', label: 'Reversal entries' },
                ]}
                onChange={(value) => {
                  setStatus(value)
                  setOffset(0)
                }}
                placeholder="All statuses"
              />
            </div>
          </Card>

          <Card
            title="Payments"
            hint={
              payments.data
                ? `${payments.data.total} matching · ${formatInr(payments.data.total_amount_paise)} in total`
                : undefined
            }
            flush
            footer={
              payments.data ? (
                <Pagination
                  total={payments.data.total}
                  limit={PAGE_SIZE}
                  offset={offset}
                  onChange={setOffset}
                  noun="payments"
                />
              ) : undefined
            }
          >
            <AsyncSection
              resource={payments}
              isEmpty={(data) => data.items.length === 0}
              empty={
                <EmptyState
                  title="No payments match"
                  body="Record a resident's payment into the pool, or a bill they paid for the household."
                  action={
                    <button
                      type="button"
                      className="btn btn--primary"
                      onClick={() => setRecording('to_pool')}
                    >
                      Record a payment
                    </button>
                  }
                />
              }
            >
              {(data) => (
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Payments</caption>
                    <thead>
                      <tr>
                        <th scope="col">Date</th>
                        <th scope="col">Resident</th>
                        <th scope="col">Kind</th>
                        <th scope="col">Counterparty</th>
                        <th scope="col" className="num">
                          Amount
                        </th>
                        <th scope="col" className="num">
                          Applied
                        </th>
                        <th scope="col" className="num">
                          Credit left
                        </th>
                        <th scope="col">Status</th>
                        <th scope="col">
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((payment) => (
                        <tr key={payment.payment_id}>
                          <td className="nowrap">{formatDate(payment.payment_date)}</td>
                          <th scope="row">
                            <Link to={`/residents/${payment.payer_resident_id}`}>
                              {payment.payer_display_name ?? payment.payer_resident_id}
                            </Link>
                          </th>
                          <td>
                            <Badge
                              tone={
                                payment.payment_kind === 'to_pool'
                                  ? 'ok'
                                  : payment.payment_kind === 'provider_advance'
                                    ? 'accent'
                                    : 'warn'
                              }
                            >
                              {PAYMENT_KIND_LABELS[payment.payment_kind] ?? payment.payment_kind}
                            </Badge>
                          </td>
                          <td>
                            {payment.payee_or_provider ?? 'Household pool'}
                            {payment.expense_id && (
                              <div className="subtle">
                                funds: {payment.linked_expense_description ?? payment.expense_id}
                              </div>
                            )}
                            {payment.payment_kind === 'provider_advance' && !payment.expense_id && (
                              <div>
                                <Badge tone="warn">not linked to an expense</Badge>
                              </div>
                            )}
                          </td>
                          <td className="num">
                            <Money paise={payment.amount_paise} />
                          </td>
                          <td className="num">
                            <Money paise={payment.applied_paise} />
                            {payment.application_count > 0 && (
                              <div className="subtle">
                                {payment.application_count} charge
                                {payment.application_count === 1 ? '' : 's'}
                              </div>
                            )}
                          </td>
                          <td className="num">
                            <Money
                              paise={payment.unapplied_paise}
                              tone={payment.unapplied_paise ? 'credit' : 'plain'}
                            />
                          </td>
                          <td>
                            <Badge tone={payment.status === 'active' ? 'ok' : 'neutral'}>
                              {payment.status}
                            </Badge>
                          </td>
                          <td>
                            <div className="row" style={{ gap: 6 }}>
                              <button
                                type="button"
                                className="btn btn--sm"
                                onClick={() => setViewing(payment.payment_id)}
                              >
                                Details
                              </button>
                              {payment.status === 'active' && (
                                <button
                                  type="button"
                                  className="btn btn--sm"
                                  onClick={() => setReversing(payment)}
                                >
                                  Reverse
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </AsyncSection>
          </Card>
        </>
      )}

      {tab === 'credits' && (
        <Card
          title="Unapplied credits"
          hint="An overpayment stays a visible credit until you apply it. It is never absorbed automatically and never lost between months."
          flush
        >
          <AsyncSection
            resource={credits}
            isEmpty={(data) => data.items.length === 0}
            empty={
              <EmptyState icon="✓" title="No unapplied credits" body="Every payment is fully applied." />
            }
          >
            {(data) => (
              <div className="table-scroll">
                <table className="data">
                  <caption className="sr-only">Unapplied credits</caption>
                  <thead>
                    <tr>
                      <th scope="col">Resident</th>
                      <th scope="col">Payment date</th>
                      <th scope="col" className="num">
                        Paid
                      </th>
                      <th scope="col" className="num">
                        Applied
                      </th>
                      <th scope="col" className="num">
                        Credit left
                      </th>
                      <th scope="col">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((credit) => (
                      <tr key={credit.payment_id}>
                        <th scope="row">
                          <Link to={`/residents/${credit.payer_resident_id}`}>
                            {credit.display_name}
                          </Link>
                        </th>
                        <td className="nowrap">{formatDate(credit.payment_date)}</td>
                        <td className="num">
                          <Money paise={credit.amount_paise} />
                        </td>
                        <td className="num">
                          <Money paise={credit.applied_paise} />
                        </td>
                        <td className="num">
                          <Money paise={credit.unapplied_paise} tone="credit" />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={async () => {
                              const result = await api.post<{ total_applied_paise: number }>(
                                '/api/credits/apply',
                                { resident_id: credit.payer_resident_id },
                              )
                              refresh()
                              toast.success(
                                result.total_applied_paise
                                  ? `Applied ${formatInr(result.total_applied_paise)} to open charges`
                                  : 'No open charges to apply it to yet',
                              )
                            }}
                          >
                            Apply to open charges
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={4}>Total held as credit</td>
                      <td className="num">
                        <Money paise={data.total_paise} tone="credit" />
                      </td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </AsyncSection>
        </Card>
      )}

      {tab === 'advances' && (
        <Card
          title="Provider advances"
          hint="Money a resident paid to an external provider for the household. Linking one to its expense is what prevents a double credit."
          flush
        >
          <AsyncSection
            resource={advances}
            isEmpty={(data) => data.items.length === 0}
            empty={
              <EmptyState
                title="No provider advances"
                body="Record one when a resident pays a bill directly."
                action={
                  <button
                    type="button"
                    className="btn btn--primary"
                    onClick={() => setRecording('provider_advance')}
                  >
                    Record a provider payment
                  </button>
                }
              />
            }
          >
            {(data) => (
              <>
                {data.unlinked_count > 0 && (
                  <div style={{ padding: 'var(--space-4) var(--space-5) 0' }}>
                    <Callout tone="warn" title={`${data.unlinked_count} advance(s) are not linked to an expense`}>
                      An unlinked advance is credited on its own. If the expense it paid for also
                      names a payer, the resident is credited twice. Check the Reconciliation screen
                      — it names any advance that looks like a duplicate.
                    </Callout>
                  </div>
                )}
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Provider advances</caption>
                    <thead>
                      <tr>
                        <th scope="col">Date</th>
                        <th scope="col">Resident</th>
                        <th scope="col">Provider</th>
                        <th scope="col">Funds</th>
                        <th scope="col">Linked</th>
                        <th scope="col">Status</th>
                        <th scope="col" className="num">
                          Amount
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((advance) => (
                        <tr key={advance.payment_id}>
                          <td className="nowrap">{formatDate(advance.payment_date)}</td>
                          <th scope="row">
                            <Link to={`/residents/${advance.payer_resident_id}`}>
                              {advance.payer_display_name ?? advance.payer_resident_id}
                            </Link>
                          </th>
                          <td>{advance.payee_or_provider ?? '—'}</td>
                          <td>
                            {advance.expense_description ?? <span className="subtle">—</span>}
                            {advance.expense_settlement_period && (
                              <div className="subtle">
                                {formatMonth(advance.expense_settlement_period)}
                              </div>
                            )}
                          </td>
                          <td>
                            {advance.is_linked ? (
                              <Badge tone="ok">Linked</Badge>
                            ) : (
                              <Badge tone="warn">Standalone</Badge>
                            )}
                          </td>
                          <td>
                            <Badge tone={advance.status === 'active' ? 'ok' : 'neutral'}>
                              {advance.status}
                            </Badge>
                          </td>
                          <td className="num">
                            <Money paise={advance.amount_paise} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={6}>Total active advances</td>
                        <td className="num">
                          <Money paise={data.total_paise} />
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </>
            )}
          </AsyncSection>
        </Card>
      )}

      {tab === 'adjustments' && (
        <Card
          title="Ledger adjustments"
          hint="Signed corrections. Append-only: a mistake is corrected by its exact negative, never by editing the original."
          flush
          actions={
            <button type="button" className="btn btn--sm btn--primary" onClick={() => setAdjusting(true)}>
              Record an adjustment
            </button>
          }
        >
          <AsyncSection
            resource={adjustments}
            isEmpty={(data) => data.items.length === 0}
            empty={
              <EmptyState
                title="No adjustments"
                body="Use one to correct a closed statement or a historical balance."
                action={
                  <button type="button" className="btn btn--primary" onClick={() => setAdjusting(true)}>
                    Record an adjustment
                  </button>
                }
              />
            }
          >
            {(data) => (
              <div className="table-scroll">
                <table className="data">
                  <caption className="sr-only">Ledger adjustments</caption>
                  <thead>
                    <tr>
                      <th scope="col">Effective</th>
                      <th scope="col">Resident</th>
                      <th scope="col">Reason</th>
                      <th scope="col">Statement</th>
                      <th scope="col">Effect</th>
                      <th scope="col" className="num">
                        Amount
                      </th>
                      <th scope="col">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((adjustment) => (
                      <tr key={adjustment.adjustment_id}>
                        <td className="nowrap">{formatDate(adjustment.effective_date)}</td>
                        <th scope="row">
                          <Link to={`/residents/${adjustment.resident_id}`}>
                            {adjustment.display_name}
                          </Link>
                        </th>
                        <td>
                          {adjustment.reason}
                          <div className="subtle">by {adjustment.created_by}</div>
                        </td>
                        <td>
                          {adjustment.statement_id ? (
                            <Link to={`/statements/${adjustment.statement_id}`}>
                              {formatMonth(adjustment.settlement_month)}
                            </Link>
                          ) : (
                            <span className="subtle">—</span>
                          )}
                        </td>
                        <td>
                          {adjustment.reverses_adjustment_id ? (
                            <Badge tone="neutral">reversal</Badge>
                          ) : adjustment.reversed_by ? (
                            <Badge tone="neutral">reversed</Badge>
                          ) : adjustment.amount_paise > 0 ? (
                            <Badge tone="ok">credits resident</Badge>
                          ) : (
                            <Badge tone="warn">debits resident</Badge>
                          )}
                        </td>
                        <td className="num">
                          <Money paise={adjustment.amount_paise} tone="auto" sign="always" />
                        </td>
                        <td>
                          {!adjustment.reversed_by && !adjustment.reverses_adjustment_id && (
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => setReversingAdjustment(adjustment)}
                            >
                              Reverse
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={5}>Net effect</td>
                      <td className="num">
                        <Money
                          paise={data.items.reduce((sum, row) => sum + row.amount_paise, 0)}
                          tone="auto"
                          sign="always"
                        />
                      </td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </AsyncSection>
        </Card>
      )}

      {recording && (
        <PaymentDialog
          initialKind={recording}
          apartment={apartment}
          onClose={() => setRecording(null)}
          onSaved={(message, body) => {
            setRecording(null)
            refresh()
            toast.success(message, body)
          }}
        />
      )}
      {adjusting && (
        <AdjustmentDialog
          onClose={() => setAdjusting(false)}
          onSaved={() => {
            setAdjusting(false)
            refresh()
            toast.success('Adjustment recorded')
          }}
        />
      )}
      {viewing && <PaymentDetailDialog paymentId={viewing} onClose={() => setViewing(null)} />}
      {reversing && (
        <ReversePaymentDialog
          payment={reversing}
          onClose={() => setReversing(null)}
          onReversed={() => {
            setReversing(null)
            refresh()
            toast.success('Payment reversed', 'The charges it settled are open again.')
          }}
        />
      )}
      {reversingAdjustment && (
        <ReverseAdjustmentDialog
          adjustment={reversingAdjustment}
          onClose={() => setReversingAdjustment(null)}
          onReversed={() => {
            setReversingAdjustment(null)
            refresh()
            toast.success('Adjustment reversed')
          }}
        />
      )}
    </Page>
  )
}

/* ------------------------------------------------------- the household pool -- */

const POOL_TITLE = 'Household Pool Account'

/** The household pool's account, read-only, with its own Edit. */
export function HouseholdPoolCard({
  apartment,
  onSaved,
}: {
  apartment: Apartment | null
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const toast = useToast()
  if (!apartment) return null
  const notRecorded = <span className="text-[var(--text-tertiary)]">Not recorded</span>
  return (
    <Panel
      title={POOL_TITLE}
      hint="Where residents send pool payments, rent shares, and settlement transfers."
      actions={<EditButton label="Edit household pool" onClick={() => setEditing(true)} />}
    >
      <dl aria-label="Household pool account" className="m-0 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
        <DetailFact label="Pool Name">{apartment.pool_name || 'Household Pool'}</DetailFact>
        <DetailFact label="Bank Account">
          {apartment.pool_bank_account ? <span className="tabular">{apartment.pool_bank_account}</span> : notRecorded}
        </DetailFact>
        <DetailFact label="IFSC">
          {apartment.pool_bank_ifsc ? <span className="tabular">{apartment.pool_bank_ifsc}</span> : notRecorded}
        </DetailFact>
        <DetailFact label="UPI ID">
          {apartment.pool_upi_id ? (
            <span className="flex items-center gap-2 min-w-0">
              <span className="truncate">{apartment.pool_upi_id}</span>
              <CopyButton text={apartment.pool_upi_id} label="Copy UPI ID" iconOnly />
            </span>
          ) : (
            notRecorded
          )}
        </DetailFact>
      </dl>
      {editing && (
        <HouseholdPoolModal
          apartment={apartment}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            onSaved()
            toast.success('Household pool saved')
          }}
        />
      )}
    </Panel>
  )
}

function HouseholdPoolModal({
  apartment,
  onClose,
  onSaved,
}: {
  apartment: Apartment
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(apartment.pool_name || 'Household Pool')
  const [account, setAccount] = useState(apartment.pool_bank_account ?? '')
  const [ifsc, setIfsc] = useState(apartment.pool_bank_ifsc ?? '')
  const [upi, setUpi] = useState(apartment.pool_upi_id ?? '')
  const [submitted, setSubmitted] = useState(false)
  const errors = {
    name: !name.trim()
      ? 'Give the pool a name.'
      : name.trim().length > 200
        ? 'Keep it to 200 characters.'
        : null,
    account: patternError(account, ACCOUNT_PATTERN, ACCOUNT_MESSAGE),
    ifsc: patternError(ifsc.toUpperCase(), IFSC_PATTERN, IFSC_MESSAGE),
    upi: patternError(upi, UPI_PATTERN, UPI_MESSAGE),
  }
  const valid = Object.values(errors).every((error) => !error)
  const save = useMutation(
    () =>
      api.patch<Apartment>(`/api/apartments/${encodeURIComponent(apartment.apartment_id)}`, {
        pool_name: name.trim(),
        pool_bank_account: account.trim() || null,
        pool_bank_ifsc: ifsc.trim().toUpperCase() || null,
        pool_upi_id: upi.trim() || null,
      }),
    { onSuccess: onSaved },
  )
  // Format errors show as soon as something is typed; a missing name on submit.
  const shown = (error: string | null, value: string) => (submitted || value.trim() ? error : null)
  return (
    <Modal
      title={POOL_TITLE}
      description="Where residents send the household pool its money. Bank details and the UPI ID are optional."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form="household-pool-form" className={BUTTON_PRIMARY} disabled={save.pending}>
            {save.pending ? 'Saving…' : 'Save Changes'}
          </button>
        </>
      }
    >
      <form
        id="household-pool-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setSubmitted(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <TextField
          label="Pool Name"
          value={name}
          onChange={setName}
          required
          maxLength={210}
          error={submitted ? errors.name : null}
        />
        <TextField
          label="Bank Account"
          value={account}
          onChange={setAccount}
          inputMode="numeric"
          maxLength={18}
          placeholder="9 to 18 digits"
          error={shown(errors.account, account)}
        />
        <TextField
          label="IFSC"
          value={ifsc}
          onChange={(value) => setIfsc(value.toUpperCase())}
          maxLength={11}
          placeholder="HDFC0001234"
          error={shown(errors.ifsc, ifsc)}
        />
        <TextField
          label="UPI ID"
          value={upi}
          onChange={setUpi}
          maxLength={320}
          placeholder="household@okhdfcbank"
          error={shown(errors.upi, upi)}
        />
      </form>
    </Modal>
  )
}

/* -------------------------------------------------------- recording payments -- */

export function PaymentDialog({
  initialKind,
  apartment,
  onClose,
  onSaved,
}: {
  initialKind: PaymentKind
  apartment: Apartment | null
  onClose: () => void
  onSaved: (message: string, body?: string) => void
}) {
  const residents = useResource<{ items: Resident[] }>('/api/residents', { active_only: false })
  const lease = useResource<{ config: RentLeaseConfig | null }>('/api/rent/config')
  const landlord = lease.data?.config ?? null
  // A provider advance can be paid to the landlord, whose details are on the lease.
  const [provider, setProvider] = useState<'landlord' | 'other'>('other')
  const poolName = apartment?.pool_name || 'Household Pool'
  const [kind, setKind] = useState<PaymentKind>(initialKind)
  const [payer, setPayer] = useState('')
  const [amountRaw, setAmountRaw] = useState('')
  const [amountPaise, setAmountPaise] = useState(0)
  const [amountOk, setAmountOk] = useState(false)
  const [paymentDate, setPaymentDate] = useState(todayIso())
  const [payee, setPayee] = useState('')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const [expenseId, setExpenseId] = useState('')
  const [touched, setTouched] = useState(false)

  const fundableExpenses = useResource<Paged<ExpenseRow>>(
    kind === 'provider_advance' ? '/api/expenses' : null,
    { limit: 200, sort: 'expense_date_desc' },
  )

  const candidates = useMemo(
    () =>
      (fundableExpenses.data?.items ?? []).filter(
        (expense) => expense.linked_advance_paise < expense.amount_paise,
      ),
    [fundableExpenses.data],
  )
  const chosenExpense = candidates.find((expense) => expense.expense_id === expenseId)

  const save = useMutation(
    () =>
      api.post<{
        payment_id: string
        applications: Array<{ allocation_id: string; amount_paise: number }>
        unapplied_credit_paise: number
      }>('/api/payments', {
        payer_resident_id: payer,
        amount_paise: amountPaise,
        payment_date: paymentDate,
        payment_kind: kind,
        payee_or_provider: payee.trim() || null,
        reference: reference.trim() || null,
        note: note.trim() || null,
        expense_id: kind === 'provider_advance' && expenseId ? expenseId : null,
      }),
    {
      onSuccess: (result) => {
        const applied = result.applications.reduce((sum, row) => sum + row.amount_paise, 0)
        onSaved(
          'Payment recorded',
          kind === 'to_pool'
            ? result.unapplied_credit_paise
              ? `${formatInr(applied)} applied to open charges; ${formatInr(result.unapplied_credit_paise)} held as a credit.`
              : `${formatInr(applied)} applied to the oldest open charges.`
            : undefined,
        )
      },
    },
  )

  const payerError = touched && !payer ? 'Choose who paid.' : null
  const amountError = touched && (!amountOk || amountPaise <= 0) ? 'Enter an amount above zero.' : null
  const linkWarning =
    kind === 'provider_advance' && !expenseId
      ? 'Without a link, this advance is credited on its own. If the expense it paid for also names a payer, that resident will be credited twice.'
      : null
  const overLink =
    chosenExpense && amountOk
      ? chosenExpense.linked_advance_paise + amountPaise > chosenExpense.amount_paise
      : false
  const valid = Boolean(payer) && amountOk && amountPaise > 0 && !overLink

  return (
    <Modal
      title="Record a payment"
      description="A payment settles who funded what. It is never a household expense."
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form="payment-form" className="btn btn--primary" disabled={save.pending}>
            {save.pending ? 'Saving…' : 'Record payment'}
          </button>
        </>
      }
    >
      <form
        id="payment-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (valid) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="form-grid">
          <SelectField
            label="Kind of payment"
            value={kind}
            options={[
              { value: 'to_pool' as PaymentKind, label: PAYMENT_KIND_LABELS.to_pool },
              { value: 'provider_advance' as PaymentKind, label: PAYMENT_KIND_LABELS.provider_advance },
              { value: 'refund' as PaymentKind, label: PAYMENT_KIND_LABELS.refund },
            ]}
            onChange={setKind}
            hint={
              kind === 'to_pool'
                ? 'Applied to this resident’s oldest open charges first; any excess stays a credit.'
                : kind === 'provider_advance'
                  ? 'Funds a household expense. Link it below.'
                  : 'The pool pays money back out to this resident.'
            }
          />
          <SelectField
            label={kind === 'refund' ? 'Paid to' : 'Paid by'}
            value={payer}
            options={(residents.data?.items ?? []).map((resident) => ({
              value: resident.resident_id,
              label: resident.display_name,
            }))}
            onChange={setPayer}
            required
            error={payerError}
            placeholder="Choose a resident"
          />
          <MoneyInput
            label="Amount"
            value={amountRaw}
            onChange={(raw, parsed) => {
              setAmountRaw(raw)
              setAmountOk(parsed.ok)
              setAmountPaise(parsed.ok ? parsed.paise : 0)
            }}
            required
            error={amountError}
          />
          <DateField
            label="Date"
            value={paymentDate}
            onChange={setPaymentDate}
            required
          />
          {kind === 'provider_advance' && landlord && (
            <SelectField<'landlord' | 'other'>
              label="Provider"
              value={provider}
              options={[
                { value: 'landlord', label: `Landlord (${landlord.landlord_name})` },
                { value: 'other', label: 'Other provider' },
              ]}
              onChange={(next) => {
                setProvider(next)
                if (next === 'landlord') setPayee(landlord.landlord_name)
                else if (payee === landlord.landlord_name) setPayee('')
              }}
            />
          )}
          <TextField
            label={kind === 'provider_advance' ? 'Provider paid' : 'Counterparty'}
            value={payee}
            onChange={setPayee}
            hint={kind === 'to_pool' ? 'Optional. Who collected it.' : undefined}
            placeholder={kind === 'to_pool' ? poolName : undefined}
            maxLength={200}
          />
          <TextField
            label="Reference"
            value={reference}
            onChange={setReference}
            hint="Transaction ID or receipt number."
            maxLength={200}
          />
        </div>

        {kind === 'to_pool' && (
          <TransferDestination
            name={poolName}
            amountPaise={amountOk && amountPaise > 0 ? amountPaise : null}
            bankAccount={apartment?.pool_bank_account}
            bankIfsc={apartment?.pool_bank_ifsc}
            upiId={apartment?.pool_upi_id}
            missing="No bank account or UPI ID is recorded for the household pool yet. Add them under Household Pool Account on this page."
          />
        )}
        {kind === 'provider_advance' && landlord && provider === 'landlord' && (
          <TransferDestination
            name={landlord.landlord_name}
            amountPaise={amountOk && amountPaise > 0 ? amountPaise : null}
            bankAccount={landlord.landlord_bank_account}
            bankIfsc={landlord.landlord_bank_ifsc}
            upiId={landlord.landlord_upi_id}
            missing="No bank account or UPI ID is on the lease. Add them under Housing, Tenancy & Master Lease, to see them here."
          />
        )}

        {kind === 'provider_advance' && (
          <fieldset>
            <legend>Which expense does this fund?</legend>
            <div className="stack">
              <SelectField
                label="Expense"
                value={expenseId}
                options={candidates.map((expense) => ({
                  value: expense.expense_id,
                  label: `${formatDate(expense.expense_date)} · ${expense.description} · ${formatInr(expense.amount_paise)}${
                    expense.linked_advance_paise
                      ? ` (${formatInr(expense.linked_advance_paise)} already linked)`
                      : ''
                  }`,
                }))}
                onChange={setExpenseId}
                placeholder="Not linked to an expense"
                hint="Only expenses not yet fully funded by a linked advance are listed."
              />
              {chosenExpense && (
                <Callout tone={overLink ? 'danger' : 'ok'} title={
                  overLink ? 'This would exceed the expense' : 'This advance will be the funding record'
                }>
                  {overLink ? (
                    <>
                      {chosenExpense.description} is {formatInr(chosenExpense.amount_paise)} and
                      already has {formatInr(chosenExpense.linked_advance_paise)} of linked
                      advances. An advance funds an expense; it cannot exceed it.
                    </>
                  ) : (
                    <>
                      Linking it means the expense's payer field is deliberately ignored for this
                      expense, so {residents.data?.items.find((r) => r.resident_id === payer)?.display_name ?? 'the payer'}{' '}
                      is credited once, not twice.
                    </>
                  )}
                </Callout>
              )}
              {linkWarning && <Callout tone="warn" title="No expense linked">{linkWarning}</Callout>}
            </div>
          </fieldset>
        )}

        {kind === 'to_pool' && (
          <Callout tone="info" title="How this will be applied">
            It settles this resident's open charges oldest first — by charge due date, then by the
            order the charges were created. Anything beyond their open charges stays an unapplied
            credit and carries forward; it is never spread over future charges automatically.
          </Callout>
        )}

        <TextAreaField label="Note" value={note} onChange={setNote} rows={2} />
      </form>
    </Modal>
  )
}

function PaymentDetailDialog({ paymentId, onClose }: { paymentId: string; onClose: () => void }) {
  const resource = useResource<PaymentDetail>(`/api/payments/${encodeURIComponent(paymentId)}`)
  return (
    <Modal
      title="Payment"
      description={<span className="id-chip">{paymentId}</span>}
      onClose={onClose}
      wide
      footer={
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      <AsyncSection resource={resource}>
        {(data) => (
          <div className="stack">
            <dl className="kv">
              <dt>Resident</dt>
              <dd>{data.payer_display_name ?? data.payer_resident_id}</dd>
              <dt>Kind</dt>
              <dd>{PAYMENT_KIND_LABELS[data.payment_kind] ?? data.payment_kind}</dd>
              <dt>Date</dt>
              <dd>{formatDate(data.payment_date)}</dd>
              <dt>Amount</dt>
              <dd>
                <Money paise={data.amount_paise} />
              </dd>
              <dt>Applied to charges</dt>
              <dd>
                <Money paise={data.applied_paise} />
              </dd>
              <dt>Held as credit</dt>
              <dd>
                <Money paise={data.unapplied_paise} tone={data.unapplied_paise ? 'credit' : 'plain'} />
              </dd>
              <dt>Status</dt>
              <dd>
                <Badge tone={data.status === 'active' ? 'ok' : 'neutral'}>{data.status}</Badge>
              </dd>
              {data.linked_expense && (
                <>
                  <dt>Funds expense</dt>
                  <dd>
                    {data.linked_expense.description} ({formatInr(data.linked_expense.amount_paise)},{' '}
                    {formatMonth(data.linked_expense.settlement_period)})
                  </dd>
                </>
              )}
              {data.reference && (
                <>
                  <dt>Reference</dt>
                  <dd className="mono">{data.reference}</dd>
                </>
              )}
              {data.note && (
                <>
                  <dt>Note</dt>
                  <dd>{data.note}</dd>
                </>
              )}
              {data.reversal && (
                <>
                  <dt>Reversed by</dt>
                  <dd>
                    <span className="id-chip">{data.reversal.payment_id}</span>{' '}
                    {formatDate(data.reversal.payment_date)}
                  </dd>
                </>
              )}
            </dl>

            <div className="card" style={{ borderRadius: 'var(--radius)' }}>
              <div className="card-header">
                <div>
                  <div className="card-title">Exactly which charges this settles</div>
                  <div className="card-hint">Oldest due date first, then creation order.</div>
                </div>
              </div>
              {data.applications.length === 0 ? (
                <EmptyState
                  title="Not applied to any charge"
                  body={
                    data.payment_kind === 'to_pool'
                      ? 'There were no open charges when it was recorded, so the whole amount is held as a credit.'
                      : 'This kind of payment is not applied to personal charges; it funds the household.'
                  }
                />
              ) : (
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Payment applications</caption>
                    <thead>
                      <tr>
                        <th scope="col">Charge from</th>
                        <th scope="col">Settlement month</th>
                        <th scope="col" className="num">
                          Charge
                        </th>
                        <th scope="col" className="num">
                          Applied
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.applications.map((application) => (
                        <tr key={application.payment_application_id}>
                          <th scope="row">{application.expense_description}</th>
                          <td className="nowrap">{formatMonth(application.settlement_period)}</td>
                          <td className="num">
                            <Money paise={application.charge_amount_paise} />
                          </td>
                          <td className="num">
                            <Money paise={application.amount_paise} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={3}>Total applied</td>
                        <td className="num">
                          <Money paise={data.applied_paise} />
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </AsyncSection>
    </Modal>
  )
}

function ReversePaymentDialog({
  payment,
  onClose,
  onReversed,
}: {
  payment: PaymentRow
  onClose: () => void
  onReversed: () => void
}) {
  const [reason, setReason] = useState('')
  const [touched, setTouched] = useState(false)
  const reverse = useMutation(
    () =>
      api.post(`/api/payments/${encodeURIComponent(payment.payment_id)}/reverse`, {
        reason: reason.trim(),
      }),
    { onSuccess: onReversed },
  )
  const reasonError = touched && !reason.trim() ? 'A reason is required.' : null

  return (
    <ConfirmDialog
      title="Reverse this payment?"
      confirmLabel="Reverse payment"
      tone="danger"
      pending={reverse.pending}
      error={reverse.error}
      confirmDisabled={!reason.trim()}
      onCancel={onClose}
      onConfirm={() => {
        setTouched(true)
        if (reason.trim()) void reverse.run(undefined)
      }}
      body={
        <div className="stack">
          <p style={{ margin: 0 }}>
            The original payment of <Money paise={payment.amount_paise} /> is kept and marked
            reversed, and a mirrored reversal entry is written pointing back at it. Both stay in the
            ledger, both are excluded from net positions, and the charges this payment settled open
            again.
          </p>
          <Callout tone="info">
            Nothing is deleted. That is what keeps the audit trail complete while the money nets to
            nothing.
          </Callout>
        </div>
      }
    >
      <TextField
        label="Reason for the reversal"
        value={reason}
        onChange={setReason}
        required
        error={reasonError}
        hint="Stored on the reversal entry and in the audit trail."
        maxLength={500}
      />
    </ConfirmDialog>
  )
}

function AdjustmentDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const residents = useResource<{ items: Resident[] }>('/api/residents', { active_only: false })
  const statements = useResource<{ items: StatementRow[] }>('/api/statements', { limit: 100 })
  const [residentId, setResidentId] = useState('')
  const [direction, setDirection] = useState<'credit' | 'debit'>('credit')
  const [amountRaw, setAmountRaw] = useState('')
  const [amountPaise, setAmountPaise] = useState(0)
  const [amountOk, setAmountOk] = useState(false)
  const [reason, setReason] = useState('')
  const [statementId, setStatementId] = useState('')
  const [effectiveDate, setEffectiveDate] = useState(todayIso())
  const [touched, setTouched] = useState(false)

  const signed = direction === 'credit' ? amountPaise : -amountPaise
  const save = useMutation(
    () =>
      api.post<{ adjustment_id: string }>('/api/adjustments', {
        resident_id: residentId,
        amount_paise: signed,
        reason: reason.trim(),
        statement_id: statementId || null,
        effective_date: effectiveDate,
      }),
    { onSuccess: onSaved },
  )

  const residentError = touched && !residentId ? 'Choose a resident.' : null
  const amountError = touched && (!amountOk || amountPaise <= 0) ? 'Enter an amount above zero.' : null
  const reasonError = touched && !reason.trim() ? 'A reason is required.' : null
  const valid = Boolean(residentId && reason.trim()) && amountOk && amountPaise > 0

  return (
    <Modal
      title="Record a ledger adjustment"
      description="A signed correction against one resident. Append-only: correcting it later means reversing it, not editing it."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form="adjustment-form" className="btn btn--primary" disabled={save.pending}>
            {save.pending ? 'Saving…' : 'Record adjustment'}
          </button>
        </>
      }
    >
      <form
        id="adjustment-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (valid) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="form-grid">
          <SelectField
            label="Resident"
            value={residentId}
            options={(residents.data?.items ?? []).map((resident) => ({
              value: resident.resident_id,
              label: resident.display_name,
            }))}
            onChange={setResidentId}
            required
            error={residentError}
            placeholder="Choose a resident"
          />
          <SelectField
            label="Direction"
            value={direction}
            options={[
              { value: 'credit' as const, label: 'Credit — the household owes them more' },
              { value: 'debit' as const, label: 'Debit — they owe the household more' },
            ]}
            onChange={setDirection}
          />
          <MoneyInput
            label="Amount"
            value={amountRaw}
            onChange={(raw, parsed) => {
              setAmountRaw(raw)
              setAmountOk(parsed.ok)
              setAmountPaise(parsed.ok ? parsed.paise : 0)
            }}
            required
            error={amountError}
            hint="Enter a positive amount and choose the direction above."
          />
          <DateField
            label="Effective date"
            value={effectiveDate}
            onChange={setEffectiveDate}
            required
            hint="Used when reconstructing a historical position."
          />
          <SelectField
            label="Related statement"
            value={statementId}
            options={(statements.data?.items ?? []).map((statement) => ({
              value: statement.statement_id,
              label: `${formatMonth(statement.settlement_month)} · ${statement.status}`,
            }))}
            onChange={setStatementId}
            placeholder="Not related to a statement"
            hint="Linking it to a closed statement documents the correction without rewriting it."
          />
        </div>
        <TextAreaField
          label="Reason"
          value={reason}
          onChange={setReason}
          required
          error={reasonError}
          rows={2}
          placeholder="Electricity share corrected after the September statement closed"
        />
        {amountOk && amountPaise > 0 && (
          <Callout tone="info" title="Effect on the ledger">
            This will change that resident's net position by{' '}
            <Money paise={signed} tone="auto" sign="always" /> exactly once, and move the household
            pool by the opposite amount so the accounts still sum to zero.
          </Callout>
        )}
      </form>
    </Modal>
  )
}

function ReverseAdjustmentDialog({
  adjustment,
  onClose,
  onReversed,
}: {
  adjustment: AdjustmentRow
  onClose: () => void
  onReversed: () => void
}) {
  const [reason, setReason] = useState('')
  const reverse = useMutation(
    () =>
      api.post(`/api/adjustments/${encodeURIComponent(adjustment.adjustment_id)}/reverse`, {
        reason: reason.trim(),
      }),
    { onSuccess: onReversed },
  )
  return (
    <ConfirmDialog
      title="Reverse this adjustment?"
      confirmLabel="Reverse adjustment"
      pending={reverse.pending}
      error={reverse.error}
      confirmDisabled={!reason.trim()}
      onCancel={onClose}
      onConfirm={() => void reverse.run(undefined)}
      body={
        <>
          This writes a new adjustment of exactly{' '}
          <Money paise={-adjustment.amount_paise} sign="always" /> for{' '}
          {adjustment.display_name}, pointing back at the original. The original stays in the
          ledger unchanged.
        </>
      }
    >
      <TextField
        label="Reason for the reversal"
        value={reason}
        onChange={setReason}
        required
        maxLength={500}
      />
    </ConfirmDialog>
  )
}
