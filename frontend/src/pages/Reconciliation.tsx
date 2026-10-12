/**
 * Full household reconciliation.
 *
 * One auditable calculation across every recorded transaction. The ledger
 * entries behind each figure are listed, so any number can be traced back to
 * the record that produced it, and any ambiguity is named as a specific
 * exception rather than silently balanced away.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Page } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  EmptyState,
  Money,
  Pagination,
  Stat,
} from '../components/Primitives'
import { DateField, SelectField } from '../components/Form'
import { downloadCsv } from '../api/client'
import { useResource } from '../lib/useApi'
import { formatDate, formatInr, formatMonth } from '../lib/format'
import type { LedgerEntry, Paged, Reconciliation } from '../api/types'

const ENTRY_KIND_LABELS: Record<string, string> = {
  expense_share: 'Resident share of an expense',
  expense_incurred: 'Household expense incurred',
  expense_funded_by_payer: 'Expense funded by a resident',
  provider_advance: 'Provider advance',
  pool_payment: 'Payment into the pool',
  refund_to_resident: 'Refund out of the pool',
  legacy_payment_adjustment: 'Legacy payment adjustment',
  ledger_adjustment: 'Signed ledger adjustment',
}

const PAGE_SIZE = 50

export default function ReconciliationPage() {
  const [asOf, setAsOf] = useState('')
  const [accountFilter, setAccountFilter] = useState('')
  const [kindFilter, setKindFilter] = useState('')
  const [offset, setOffset] = useState(0)

  const reconciliation = useResource<Reconciliation>('/api/reconciliation', {
    as_of_date: asOf || undefined,
  })
  const entries = useResource<Paged<LedgerEntry> & { sum_paise: number }>(
    '/api/reconciliation/entries',
    {
      as_of_date: asOf || undefined,
      account_id: accountFilter || undefined,
      kind: kindFilter || undefined,
      limit: PAGE_SIZE,
      offset,
    },
  )

  return (
    <Page
      title="Reconciliation"
      subtitle="Every recorded transaction across every month, reconciled into one set of balances that always sums to zero."
      breadcrumbs={[{ label: 'Overview' }, { label: 'Reconciliation' }]}
      actions={
        <button
          type="button"
          className="btn"
          onClick={() =>
            downloadCsv('/api/reconciliation/entries/export/csv', {
              as_of_date: asOf || undefined,
            })
          }
        >
          Export ledger entries
        </button>
      }
    >
      <Card
        title="As-of date"
        hint="Leave empty for the current position. Set a date to reconstruct the balances as they stood then — dated expenses, payments, and adjustments after that date are excluded."
      >
        <div className="filters">
          <DateField
            label="Reconcile as of"
            value={asOf}
            onChange={(value) => {
              setAsOf(value)
              setOffset(0)
            }}
            hint="Re-running this with no new transactions always gives the same answer."
          />
        </div>
      </Card>

      <AsyncSection resource={reconciliation}>
        {(data) => {
          const residents = data.accounts.filter((account) => account.account_type === 'resident')
          const pool = data.accounts.find((account) => account.account_type === 'pool')
          const errors = data.validation.issues.filter((issue) => issue.severity === 'error')
          const warnings = data.validation.issues.filter((issue) => issue.severity === 'warning')

          return (
            <>
              {errors.length > 0 && (
                <Callout tone="danger" title="These records must be corrected by hand">
                  <p style={{ margin: '0 0 8px' }}>
                    The application will not invent a balancing entry to make the totals agree.
                    Each of these names the specific record at fault.
                  </p>
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {errors.map((issue) => (
                      <li key={`${issue.code}-${issue.entity_id}`}>
                        <strong>{issue.message}</strong>{' '}
                        <span className="id-chip">
                          {issue.entity_type} {issue.entity_id}
                        </span>
                        {issue.suggested_action && (
                          <div className="muted">{issue.suggested_action}</div>
                        )}
                      </li>
                    ))}
                  </ul>
                </Callout>
              )}

              {warnings.length > 0 && (
                <Callout tone="warn" title="Exceptions needing operator review">
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {warnings.map((issue) => (
                      <li key={`${issue.code}-${issue.entity_id}`}>
                        {issue.message}{' '}
                        <span className="id-chip">
                          {issue.entity_type} {issue.entity_id}
                        </span>
                        {issue.suggested_action && (
                          <div className="muted">{issue.suggested_action}</div>
                        )}
                      </li>
                    ))}
                  </ul>
                </Callout>
              )}

              {errors.length === 0 && warnings.length === 0 && (
                <Callout tone="ok" title="The ledger reconciles cleanly">
                  Every expense's shares add up to it exactly, no payment is over-applied, every
                  provider advance is accounted for once, and all {data.accounts.length} accounts
                  sum to zero.
                </Callout>
              )}

              <div className="grid grid--stats">
                <Stat
                  label="Household expenses"
                  value={formatInr(data.totals.household_expenses_paise)}
                  note="All months on record"
                />
                <Stat
                  label="Allocated to residents"
                  value={formatInr(data.totals.allocated_charges_paise)}
                  note="Equals household expenses exactly, by construction"
                />
                <Stat
                  label="Outstanding charges"
                  value={formatInr(data.totals.outstanding_charges_paise)}
                  note="Not yet settled by a payment"
                />
                <Stat
                  label="Unapplied credits"
                  value={formatInr(data.totals.unapplied_credits_paise)}
                  note="Overpayments held and carried forward"
                />
                <Stat
                  label="Funded by residents"
                  value={formatInr(
                    data.totals.expense_funding_paise + data.totals.provider_advances_paise,
                  )}
                  note="Expense payers plus provider advances, counted once each"
                />
                <Stat
                  label="All accounts sum to"
                  value={formatInr(data.net_positions_sum_paise)}
                  note={`${data.entry_count} ledger entries`}
                />
              </div>

              <Card
                title="Account positions"
                hint="Positive means the account is owed money; negative means it owes money. Charged, funded, paid in, credits, and adjustments are the components of each net balance."
                flush
              >
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Account positions</caption>
                    <thead>
                      <tr>
                        <th scope="col">Account</th>
                        <th scope="col" className="num">
                          Charged
                        </th>
                        <th scope="col" className="num">
                          Settled
                        </th>
                        <th scope="col" className="num">
                          Outstanding
                        </th>
                        <th scope="col" className="num">
                          Paid in
                        </th>
                        <th scope="col" className="num">
                          Funded
                        </th>
                        <th scope="col" className="num">
                          Credit
                        </th>
                        <th scope="col" className="num">
                          Adjustments
                        </th>
                        <th scope="col" className="num">
                          Net balance
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {residents.map((account) => (
                        <tr key={account.account_id}>
                          <th scope="row">
                            <Link to={`/residents/${account.account_id}`}>
                              {account.display_name}
                            </Link>
                            {!account.is_active && (
                              <>
                                {' '}
                                <Badge tone="neutral">moved out</Badge>
                              </>
                            )}
                          </th>
                          <td className="num">
                            <Money paise={account.allocated_charges_paise ?? 0} />
                          </td>
                          <td className="num">
                            <Money paise={account.settled_paise ?? 0} />
                          </td>
                          <td className="num">
                            <Money
                              paise={account.outstanding_charges_paise ?? 0}
                              tone={account.outstanding_charges_paise ? 'owed-by' : 'plain'}
                            />
                          </td>
                          <td className="num">
                            <Money paise={account.pool_payments_paise ?? 0} />
                          </td>
                          <td className="num">
                            <Money paise={account.funding_credits_paise ?? 0} />
                          </td>
                          <td className="num">
                            <Money
                              paise={account.unapplied_credit_paise ?? 0}
                              tone={account.unapplied_credit_paise ? 'credit' : 'plain'}
                            />
                          </td>
                          <td className="num">
                            <Money
                              paise={account.adjustments_paise ?? 0}
                              tone="auto"
                              sign="always"
                            />
                          </td>
                          <td className="num">
                            <Money paise={account.net_position_paise} tone="auto" sign="always" />
                          </td>
                        </tr>
                      ))}
                      {pool && (
                        <tr>
                          <th scope="row">
                            {pool.display_name}
                            <div className="subtle">
                              the collection pool; the balancing account
                            </div>
                          </th>
                          <td className="num" colSpan={7}>
                            <span className="subtle">
                              expenses incurred <Money paise={pool.expenses_incurred_paise ?? 0} /> ·
                              cash received <Money paise={pool.cash_received_paise ?? 0} /> ·
                              funded by residents{' '}
                              <Money paise={pool.funded_by_residents_paise ?? 0} />
                            </span>
                          </td>
                          <td className="num">
                            <Money paise={pool.net_position_paise} tone="auto" sign="always" />
                          </td>
                        </tr>
                      )}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={8}>All accounts together</td>
                        <td className="num">
                          <Money paise={data.net_positions_sum_paise} />
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </Card>

              <Card
                title="Suggested settlement transfers"
                hint="Advice only. Nothing here changes a record, and no transfer ever exceeds the balance it settles."
                flush
              >
                {data.transfers.length === 0 ? (
                  <EmptyState icon="✓" title="Nothing to settle" body="Every account is square." />
                ) : (
                  <div className="table-scroll">
                    <table className="data">
                      <caption className="sr-only">Suggested transfers</caption>
                      <thead>
                        <tr>
                          <th scope="col">From</th>
                          <th scope="col">To</th>
                          <th scope="col" className="num">
                            Amount
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.transfers.map((transfer) => (
                          <tr
                            key={`${transfer.from_account}-${transfer.to_account}-${transfer.amount_paise}`}
                          >
                            <td>{transfer.from_display_name}</td>
                            <td>{transfer.to_display_name}</td>
                            <td className="num">
                              <Money paise={transfer.amount_paise} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card title="Household expenses by settlement month" flush>
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Expenses by settlement month</caption>
                    <thead>
                      <tr>
                        <th scope="col">Settlement month</th>
                        <th scope="col" className="num">
                          Household expenses
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(data.household_expenses_paise_by_settlement_period)
                        .sort(([a], [b]) => b.localeCompare(a))
                        .map(([period, total]) => (
                          <tr key={period}>
                            <th scope="row">{formatMonth(period)}</th>
                            <td className="num">
                              <Money paise={total} />
                            </td>
                          </tr>
                        ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td>All months</td>
                        <td className="num">
                          <Money paise={data.totals.household_expenses_paise} />
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </Card>

              <Card
                title="Ledger entries"
                hint="The signed entries this reconciliation is built from. Each one names the record it came from."
                flush
                footer={
                  entries.data ? (
                    <Pagination
                      total={entries.data.total}
                      limit={PAGE_SIZE}
                      offset={offset}
                      onChange={setOffset}
                      noun="entries"
                    />
                  ) : undefined
                }
              >
                <div className="card-body">
                  <div className="filters">
                    <SelectField
                      label="Account"
                      value={accountFilter}
                      options={data.accounts.map((account) => ({
                        value: account.account_id,
                        label: account.display_name,
                      }))}
                      onChange={(value) => {
                        setAccountFilter(value)
                        setOffset(0)
                      }}
                      placeholder="All accounts"
                    />
                    <SelectField
                      label="Entry kind"
                      value={kindFilter}
                      options={Object.entries(ENTRY_KIND_LABELS).map(([value, label]) => ({
                        value,
                        label,
                      }))}
                      onChange={(value) => {
                        setKindFilter(value)
                        setOffset(0)
                      }}
                      placeholder="All kinds"
                    />
                  </div>
                </div>
                <AsyncSection
                  resource={entries}
                  isEmpty={(payload) => payload.items.length === 0}
                  empty={<EmptyState title="No entries match" />}
                >
                  {(payload) => (
                    <div className="table-scroll">
                      <table className="data">
                        <caption className="sr-only">Ledger entries</caption>
                        <thead>
                          <tr>
                            <th scope="col">Date</th>
                            <th scope="col">Account</th>
                            <th scope="col">Entry</th>
                            <th scope="col">Description</th>
                            <th scope="col">Source record</th>
                            <th scope="col" className="num">
                              Amount
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {payload.items.map((entry, index) => (
                            <tr key={`${entry.source_type}-${entry.source_id}-${entry.account_id}-${entry.kind}-${index}`}>
                              <td className="nowrap">{formatDate(entry.date)}</td>
                              <td>
                                {data.accounts.find(
                                  (account) => account.account_id === entry.account_id,
                                )?.display_name ?? entry.account_id}
                              </td>
                              <td>{ENTRY_KIND_LABELS[entry.kind] ?? entry.kind}</td>
                              <td>{entry.description}</td>
                              <td>
                                <span className="id-chip">
                                  {entry.source_type} {entry.source_id}
                                </span>
                              </td>
                              <td className="num">
                                <Money paise={entry.amount_paise} tone="auto" sign="always" />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                        <tfoot>
                          <tr>
                            <td colSpan={5}>Sum of all matching entries</td>
                            <td className="num">
                              <Money paise={payload.sum_paise} />
                            </td>
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  )}
                </AsyncSection>
              </Card>

              <Card title="What this calculation does and does not do">
                <ul className="legend-list">
                  {data.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                  <li>
                    An expense, a resident's share of it, and whoever funded it are three separate
                    entries — never three independent expenses.
                  </li>
                  <li>
                    Re-running this with no new transactions produces an identical result; the
                    ordering and tie-breaks are total, so nothing depends on a random identifier.
                  </li>
                  <li>
                    A reversed payment and its mirrored reversal are both excluded, so the money
                    nets to nothing while both rows stay in the audit trail.
                  </li>
                </ul>
              </Card>
            </>
          )
        }}
      </AsyncSection>
    </Page>
  )
}
