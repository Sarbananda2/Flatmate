/**
 * One expense in full: its shares, what has been settled, and how it was funded.
 *
 * The funding line is stated explicitly because it is the distinction that
 * keeps the accounts honest: an expense, a resident's share of it, and whoever
 * paid for it are three separate facts.
 */
import { Link } from 'react-router-dom'
import {
  AsyncSection,
  Badge,
  Callout,
  Modal,
  Money,
  StatementStatusBadge,
} from '../components/Primitives'
import { useResource } from '../lib/useApi'
import {
  ALLOCATION_METHOD_LABELS,
  PAYMENT_KIND_LABELS,
  PERIOD_RULE_HELP,
  PERIOD_RULE_LABELS,
  formatDate,
  formatInr,
  formatMonth,
  whToKwh,
} from '../lib/format'
import type { ExpenseDetail } from '../api/types'

export function ExpenseDetailDialog({
  expenseId,
  onClose,
}: {
  expenseId: string
  onClose: () => void
}) {
  const resource = useResource<ExpenseDetail>(`/api/expenses/${encodeURIComponent(expenseId)}`)
  return (
    <Modal
      title={resource.data?.description ?? 'Expense'}
      description={<span className="id-chip">{expenseId}</span>}
      onClose={onClose}
      wide
      footer={
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      <AsyncSection resource={resource}>{(data) => <Body data={data} />}</AsyncSection>
    </Modal>
  )
}

function Body({ data }: { data: ExpenseDetail }) {
  const allocatedTotal = data.allocations.reduce((sum, row) => sum + row.amount_paise, 0)
  const settledTotal = data.allocations.reduce((sum, row) => sum + row.settled_paise, 0)
  const balances = allocatedTotal === data.amount_paise
  const linkedAdvances = data.linked_payments.filter(
    (payment) => payment.payment_kind === 'provider_advance' && payment.status === 'active',
  )
  const linkedTotal = linkedAdvances.reduce((sum, payment) => sum + payment.amount_paise, 0)

  return (
    <div className="stack">
      {!balances && (
        <Callout tone="danger" title="This expense does not balance">
          The expense is {formatInr(data.amount_paise)} but its shares add up to{' '}
          {formatInr(allocatedTotal)}. Reconciliation will refuse to report settlement figures
          until this is corrected.
        </Callout>
      )}

      <dl className="kv">
        <dt>Amount</dt>
        <dd>
          <Money paise={data.amount_paise} />
        </dd>
        <dt>Transaction date</dt>
        <dd>{formatDate(data.expense_date)}</dd>
        <dt>Service period</dt>
        <dd>
          {formatMonth(data.service_period)}{' '}
          <span className="subtle">— the month the money was for</span>
        </dd>
        <dt>Settlement month</dt>
        <dd>
          {formatMonth(data.settlement_period)}{' '}
          <span className="subtle">— the statement that charges it</span>
        </dd>
        <dt>Period rule</dt>
        <dd>
          <Badge tone="accent" title={PERIOD_RULE_HELP[data.period_rule ?? '']}>
            {PERIOD_RULE_LABELS[data.period_rule ?? ''] ?? data.period_rule ?? '—'}
          </Badge>
        </dd>
        <dt>Charge due date</dt>
        <dd>
          {formatDate(data.due_date)}{' '}
          <span className="subtle">— payments settle the oldest due date first</span>
        </dd>
        <dt>Allocation method</dt>
        <dd>
          {ALLOCATION_METHOD_LABELS[data.allocation_method ?? ''] ?? data.allocation_method ?? '—'}
        </dd>
        <dt>Category</dt>
        <dd>{data.definition_name ? `${data.definition_name} · ${data.category}` : '—'}</dd>
        <dt>Provider</dt>
        <dd>{data.provider_name ?? '—'}</dd>
        {data.reference && (
          <>
            <dt>Reference</dt>
            <dd className="mono">{data.reference}</dd>
          </>
        )}
        <dt>Statement</dt>
        <dd>
          {data.statement ? (
            <Link to={`/statements/${data.statement.statement_id}`}>
              {formatMonth(data.statement.settlement_month)}{' '}
              <StatementStatusBadge status={data.statement.status} />
            </Link>
          ) : (
            <StatementStatusBadge status="unattached" />
          )}
        </dd>
        {data.notes && (
          <>
            <dt>Notes</dt>
            <dd>{data.notes}</dd>
          </>
        )}
      </dl>

      <Callout tone={linkedAdvances.length ? 'info' : 'info'} title="How this expense was funded">
        {linkedAdvances.length > 0 ? (
          <>
            A provider advance of {formatInr(linkedTotal)} is linked to this expense, so{' '}
            <strong>the linked advance is the funding record</strong> and the expense's payer field
            is deliberately ignored. The same payment is credited once.
            {linkedTotal !== data.amount_paise && (
              <>
                {' '}
                The remaining {formatInr(data.amount_paise - linkedTotal)} is treated as funded by
                the household pool.
              </>
            )}
          </>
        ) : data.payer_resident_id ? (
          <>
            <strong>{data.payer_display_name ?? data.payer_resident_id}</strong> funded this expense
            and is credited {formatInr(data.amount_paise)} for it, separately from their own share
            of {formatInr(
              data.allocations.find((row) => row.resident_id === data.payer_resident_id)
                ?.amount_paise ?? 0,
            )}
            . If you also record a provider advance for this bill, link it to this expense so the
            credit is not counted twice.
          </>
        ) : (
          <>
            Funded by the household pool. No resident is credited for paying it, so residents owe
            the pool their shares.
          </>
        )}
      </Callout>

      <div className="card" style={{ borderRadius: 'var(--radius)' }}>
        <div className="card-header">
          <div className="card-title">Resident shares</div>
          <Badge tone={balances ? 'ok' : 'danger'}>
            {balances ? 'Balances exactly' : 'Does not balance'}
          </Badge>
        </div>
        <div className="table-scroll">
          <table className="data">
            <caption className="sr-only">Resident shares of this expense</caption>
            <thead>
              <tr>
                <th scope="col">Resident</th>
                <th scope="col" className="num">
                  Share
                </th>
                <th scope="col" className="num">
                  Settled
                </th>
                <th scope="col" className="num">
                  Outstanding
                </th>
              </tr>
            </thead>
            <tbody>
              {data.allocations.map((allocation) => (
                <tr key={allocation.allocation_id}>
                  <th scope="row">
                    <Link to={`/residents/${allocation.resident_id}`}>
                      {allocation.display_name}
                    </Link>
                    {allocation.resident_id === data.payer_resident_id && (
                      <>
                        {' '}
                        <Badge tone="accent">funded this</Badge>
                      </>
                    )}
                  </th>
                  <td className="num">
                    <Money paise={allocation.amount_paise} />
                  </td>
                  <td className="num">
                    <Money paise={allocation.settled_paise} />
                  </td>
                  <td className="num">
                    <Money
                      paise={allocation.outstanding_paise}
                      tone={allocation.outstanding_paise ? 'owed-by' : 'plain'}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>Total</td>
                <td className="num">
                  <Money paise={allocatedTotal} tone={balances ? 'plain' : 'owed-by'} />
                </td>
                <td className="num">
                  <Money paise={settledTotal} />
                </td>
                <td className="num">
                  <Money paise={allocatedTotal - settledTotal} tone="owed-by" />
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      {data.components.length > 1 && (
        <div className="card" style={{ borderRadius: 'var(--radius)' }}>
          <div className="card-header">
            <div className="card-title">Bill components</div>
          </div>
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Bill components</caption>
              <thead>
                <tr>
                  <th scope="col">Component</th>
                  <th scope="col">Allocation</th>
                  <th scope="col" className="num">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.components.map((component) => (
                  <tr key={component.component_id}>
                    <th scope="row">{component.name}</th>
                    <td>
                      {ALLOCATION_METHOD_LABELS[component.allocation_method] ??
                        component.allocation_method}
                    </td>
                    <td className="num">
                      <Money paise={component.amount_paise} />
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2}>Total</td>
                  <td className="num">
                    <Money
                      paise={data.components.reduce((sum, row) => sum + row.amount_paise, 0)}
                    />
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      )}

      {data.readings.length > 0 && (
        <div className="card" style={{ borderRadius: 'var(--radius)' }}>
          <div className="card-header">
            <div>
              <div className="card-title">Smart-plug readings</div>
              <div className="card-hint">
                Operator-entered allocation weights, not a replacement for the bill. Guest usage is
                included in the host's reading.
              </div>
            </div>
          </div>
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Smart-plug readings</caption>
              <thead>
                <tr>
                  <th scope="col">Resident</th>
                  <th scope="col">Device</th>
                  <th scope="col">Period</th>
                  <th scope="col" className="num">
                    kWh
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.readings.map((reading) => (
                  <tr key={reading.reading_id}>
                    <th scope="row">{reading.resident_id}</th>
                    <td>{reading.device_label}</td>
                    <td className="nowrap">
                      {formatDate(reading.period_start)} – {formatDate(reading.period_end)}
                    </td>
                    <td className="num">{reading.kwh ?? whToKwh(reading.energy_wh)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {data.linked_payments.length > 0 && (
        <div className="card" style={{ borderRadius: 'var(--radius)' }}>
          <div className="card-header">
            <div className="card-title">Payments linked to this expense</div>
          </div>
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Linked payments</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Paid by</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.linked_payments.map((payment) => (
                  <tr key={payment.payment_id}>
                    <td className="nowrap">{formatDate(payment.payment_date)}</td>
                    <td>
                      {PAYMENT_KIND_LABELS[payment.payment_kind] ?? payment.payment_kind}
                    </td>
                    <td>{payment.payer_resident_id}</td>
                    <td>
                      <Badge tone={payment.status === 'active' ? 'ok' : 'neutral'}>
                        {payment.status}
                      </Badge>
                    </td>
                    <td className="num">
                      <Money paise={payment.amount_paise} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
