import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Page } from '../components/Layout'
import { DateField } from '../components/Form'
import {
  AsyncSection,
  Badge,
  Card,
  EmptyState,
  Money,
  NetBalance,
  Stat,
  StatementStatusBadge,
} from '../components/Primitives'
import { useToast } from '../components/Toast'
import { useResource } from '../lib/useApi'
import { api } from '../api/client'
import {
  PAYMENT_KIND_LABELS,
  formatDate,
  formatInr,
  formatMonth,
  parseRupees,
} from '../lib/format'
import type { OpeningBalanceItem, ResidentDetail } from '../api/types'
import { ContactCard, EditProfileDialog, UpiCard } from './ResidentProfile'

export default function ResidentDetailPage() {
  const { residentId = '' } = useParams()
  const resource = useResource<ResidentDetail>(
    residentId ? `/api/residents/${encodeURIComponent(residentId)}` : null,
  )

  return (
    <Page
      title={resource.data?.resident.display_name ?? 'Resident'}
      subtitle="Everything charged to this resident, everything they paid, and what remains."
      breadcrumbs={[
        { label: 'Residents', to: '/residents' },
        { label: resource.data?.resident.display_name ?? residentId },
      ]}
    >
      <AsyncSection resource={resource}>
        {(data) => <Body data={data} residentId={residentId} onReload={() => resource.reload()} />}
      </AsyncSection>
    </Page>
  )
}

function Body({
  data,
  residentId,
  onReload,
}: {
  data: ResidentDetail
  residentId: string
  onReload: () => void
}) {
  const account = data.account
  const active = Boolean(data.resident.is_active)
  const openCharges = data.charges.filter((charge) => charge.outstanding_paise > 0)

  const openingResource = useResource<{
    items: OpeningBalanceItem[]
    is_editable: boolean
    locked_by_month: string | null
  }>('/api/opening-balances')

  const openingBal = openingResource.data?.items.find((i) => i.resident_id === residentId)
  const isEditable = openingResource.data?.is_editable ?? true

  const toast = useToast()
  const [editingProfile, setEditingProfile] = useState(false)
  const [editingOpening, setEditingOpening] = useState(false)
  const [openingAmountStr, setOpeningAmountStr] = useState('')
  const [openingDirection, setOpeningDirection] = useState<'owed_by_resident' | 'owed_to_resident'>('owed_by_resident')
  const [openingDate, setOpeningDate] = useState(new Date().toISOString().slice(0, 10))
  const [openingNote, setOpeningNote] = useState('')
  const [openingSubmitting, setOpeningSubmitting] = useState(false)
  const [openingError, setOpeningError] = useState<string | null>(null)

  function startEditOpening() {
    if (openingBal) {
      setOpeningAmountStr((Math.abs(openingBal.amount_paise) / 100).toFixed(2))
      setOpeningDirection(openingBal.amount_paise < 0 ? 'owed_by_resident' : 'owed_to_resident')
      setOpeningDate(openingBal.as_of_date)
      setOpeningNote(openingBal.note ?? '')
    } else {
      setOpeningAmountStr('')
      setOpeningDirection('owed_by_resident')
      setOpeningDate(new Date().toISOString().slice(0, 10))
      setOpeningNote('')
    }
    setOpeningError(null)
    setEditingOpening(true)
  }

  async function handleSaveOpening(e: React.FormEvent) {
    e.preventDefault()
    setOpeningError(null)
    const parsed = parseRupees(openingAmountStr)
    const paise = parsed.paise
    if (!parsed.ok || paise <= 0) {
      setOpeningError('Please enter a valid amount greater than zero.')
      return
    }
    const signedPaise = openingDirection === 'owed_by_resident' ? -paise : paise
    setOpeningSubmitting(true)
    try {
      await api.post('/api/opening-balances', {
        resident_id: residentId,
        amount_paise: signedPaise,
        as_of_date: openingDate,
        note: openingNote.trim() || null,
      })
      setEditingOpening(false)
      openingResource.reload()
      onReload()
    } catch (err: any) {
      setOpeningError(err?.message ?? 'Failed to save opening balance.')
    } finally {
      setOpeningSubmitting(false)
    }
  }

  async function handleDeleteOpening() {
    if (!confirm('Are you sure you want to remove this opening balance?')) return
    setOpeningSubmitting(true)
    try {
      await api.delete(`/api/opening-balances/${encodeURIComponent(residentId)}`)
      setEditingOpening(false)
      openingResource.reload()
      onReload()
    } catch (err: any) {
      alert(err?.message ?? 'Failed to delete opening balance.')
    } finally {
      setOpeningSubmitting(false)
    }
  }

  return (
    <>
      <div className="grid grid--stats">
        <Stat
          label="Net balance"
          value={formatInr(account?.net_position_paise ?? 0, { sign: 'always' })}
          note={
            (account?.net_position_paise ?? 0) > 0
              ? 'The household owes this resident'
              : (account?.net_position_paise ?? 0) < 0
                ? 'This resident owes the household'
                : 'Settled'
          }
        />
        <Stat
          label="Total charged"
          value={formatInr(account?.allocated_charges_paise ?? 0)}
          note="Their share of every household expense"
        />
        <Stat
          label="Outstanding charges"
          value={formatInr(account?.outstanding_charges_paise ?? 0)}
          note={`${openCharges.length} charge${openCharges.length === 1 ? '' : 's'} not fully settled`}
        />
        <Stat
          label="Paid into the pool"
          value={formatInr(account?.pool_payments_paise ?? 0)}
          note="Transfers toward their own settlement"
        />
        <Stat
          label="Unapplied credit"
          value={formatInr(data.unapplied_credit_total_paise)}
          note="Overpayments held for them; they carry forward"
        />
        <Stat
          label="Funded for the household"
          value={formatInr(account?.funding_credits_paise ?? 0)}
          note="Expenses they paid for, plus provider advances"
        />
      </div>

      <div className="grid grid--halves">
        <ContactCard resident={data.resident} onEdit={() => setEditingProfile(true)} />
        <UpiCard resident={data.resident} />
      </div>

      {editingProfile && (
        <EditProfileDialog
          resident={data.resident}
          onClose={() => setEditingProfile(false)}
          onSaved={() => {
            setEditingProfile(false)
            onReload()
            toast.success('Profile saved')
          }}
        />
      )}

      <div className="grid grid--halves">
        <Card title="Status">
          <div className="stack stack--tight">
            <div className="row">
              {active ? <Badge tone="ok">Living here</Badge> : <Badge tone="neutral">Moved out</Badge>}
              <span className="subtle">
                {data.resident.active_from ? `From ${formatDate(data.resident.active_from)}` : 'Move-in date not recorded'}
                {data.resident.active_until ? ` until ${formatDate(data.resident.active_until)}` : ''}
              </span>
            </div>
            {account && (
              <div>
                <NetBalance paise={account.net_position_paise} name={data.resident.display_name} />
              </div>
            )}
            {data.resident.notes && <p className="muted" style={{ margin: 0 }}>{data.resident.notes}</p>}
          </div>
        </Card>

        <Card
          title="Opening balance / Prior dues"
          hint="Balances carried over from before this application was used."
          actions={
            isEditable && (
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                onClick={startEditOpening}
              >
                {openingBal ? 'Edit' : 'Set opening balance'}
              </button>
            )
          }
        >
          {openingBal ? (
            <div className="stack stack--tight">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="subtle">Initial position:</span>
                <Money
                  paise={openingBal.amount_paise}
                  sign="always"
                  tone={openingBal.amount_paise < 0 ? 'owed-by' : 'credit'}
                />
              </div>
              <div className="subtle">
                {openingBal.amount_paise < 0
                  ? 'Resident owed the household prior to start'
                  : 'Household owed this resident prior to start'}
                {openingBal.as_of_date && ` (as of ${formatDate(openingBal.as_of_date)})`}
              </div>
              {openingBal.amount_paise < 0 && (
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <span className="subtle">Settlement progress:</span>
                  <span>
                    <Money paise={openingBal.settled_paise} /> settled ·{' '}
                    <Money paise={openingBal.outstanding_paise} tone="owed-by" /> remaining
                  </span>
                </div>
              )}
              {openingBal.note && <p className="muted" style={{ margin: 0 }}>{openingBal.note}</p>}
              {!isEditable && (
                <div className="subtle" style={{ fontStyle: 'italic' }}>
                  Locked because statements have been finalized. Post-close corrections use ledger adjustments.
                </div>
              )}
            </div>
          ) : (
            <div className="stack stack--tight">
              <span className="subtle">No opening balance recorded for this resident.</span>
              {isEditable ? (
                <p className="muted" style={{ margin: 0, fontSize: '0.875rem' }}>
                  If this resident owed dues or was owed credit before using this app, you can enter it here.
                </p>
              ) : (
                <span className="subtle" style={{ fontStyle: 'italic' }}>
                  Locked because a statement has already been finalized.
                </span>
              )}
            </div>
          )}
        </Card>
      </div>

      {editingOpening && (
        <div className="modal-backdrop">
          <div className="modal">
            <h3>{openingBal ? 'Edit opening balance' : 'Set opening balance'}</h3>
            <p className="muted">
              Enter any initial balance {data.resident.display_name} carried before recording expenses in this app.
            </p>
            {openingError && <div className="alert alert--danger">{openingError}</div>}
            <form onSubmit={handleSaveOpening} className="stack">
              <label className="field">
                <span className="field__label">Balance direction</span>
                <select
                  value={openingDirection}
                  onChange={(e) => setOpeningDirection(e.target.value as any)}
                  className="input"
                >
                  <option value="owed_by_resident">Resident owes the household (Debt / Prior dues)</option>
                  <option value="owed_to_resident">Household owes the resident (Prior credit / Advance)</option>
                </select>
              </label>

              <label className="field">
                <span className="field__label">Amount (₹)</span>
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  required
                  placeholder="e.g. 1500.00"
                  value={openingAmountStr}
                  onChange={(e) => setOpeningAmountStr(e.target.value)}
                  className="input"
                />
              </label>

              <DateField
                label="As-of date"
                required
                value={openingDate}
                onChange={setOpeningDate}
              />

              <label className="field">
                <span className="field__label">Note (optional)</span>
                <input
                  type="text"
                  maxLength={500}
                  placeholder="e.g. Carry forward from August offline ledger"
                  value={openingNote}
                  onChange={(e) => setOpeningNote(e.target.value)}
                  className="input"
                />
              </label>

              <div className="row" style={{ justifyContent: 'space-between', marginTop: '1rem' }}>
                {openingBal && (
                  <button
                    type="button"
                    className="btn btn--danger"
                    onClick={handleDeleteOpening}
                    disabled={openingSubmitting}
                  >
                    Delete opening balance
                  </button>
                )}
                <div className="row" style={{ gap: '0.5rem', marginLeft: 'auto' }}>
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => setEditingOpening(false)}
                    disabled={openingSubmitting}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn btn--primary"
                    disabled={openingSubmitting}
                  >
                    {openingSubmitting ? 'Saving...' : 'Save balance'}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}

      <Card
        title="Charges by settlement month"
        hint="A charge is this resident's share of a household expense. Settled means a payment has been applied to it."
        flush
      >
        {Object.keys(data.charges_by_settlement_period).length === 0 ? (
          <EmptyState title="No charges yet" body="Nothing has been allocated to this resident." />
        ) : (
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Charges by settlement month</caption>
              <thead>
                <tr>
                  <th scope="col">Settlement month</th>
                  <th scope="col" className="num">
                    Charged
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
                {Object.entries(data.charges_by_settlement_period).map(([period, totals]) => (
                  <tr key={period}>
                    <th scope="row">{formatMonth(period)}</th>
                    <td className="num">
                      <Money paise={totals.charged_paise} />
                    </td>
                    <td className="num">
                      <Money paise={totals.settled_paise} />
                    </td>
                    <td className="num">
                      <Money
                        paise={totals.outstanding_paise}
                        tone={totals.outstanding_paise ? 'owed-by' : 'plain'}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Individual charges"
        hint="Oldest first — this is the order a payment is applied in."
        flush
      >
        {data.charges.length === 0 ? (
          <EmptyState title="No charges" />
        ) : (
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Charges</caption>
              <thead>
                <tr>
                  <th scope="col">Due</th>
                  <th scope="col">Expense</th>
                  <th scope="col">Settlement month</th>
                  <th scope="col">Statement</th>
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
                {data.charges.map((charge, idx) => (
                  <tr key={charge.allocation_id ?? `opening-${idx}`}>
                    <td className="nowrap">{formatDate(charge.due_date ?? charge.expense_date)}</td>
                    <td>
                      {charge.description}
                      {charge.expense_id && (
                        <div>
                          <span className="id-chip">{charge.expense_id}</span>
                        </div>
                      )}
                    </td>
                    <td className="nowrap">{formatMonth(charge.settlement_period)}</td>
                    <td>
                      {charge.statement_id ? (
                        <Link to={`/statements/${charge.statement_id}`}>
                          <StatementStatusBadge status={charge.statement_status} />
                        </Link>
                      ) : (
                        <StatementStatusBadge status="unattached" />
                      )}
                    </td>
                    <td className="num">
                      <Money paise={charge.amount_paise} />
                    </td>
                    <td className="num">
                      <Money paise={charge.settled_paise} />
                    </td>
                    <td className="num">
                      <Money
                        paise={charge.outstanding_paise}
                        tone={charge.outstanding_paise ? 'owed-by' : 'plain'}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={4}>Total</td>
                  <td className="num">
                    <Money paise={data.charges.reduce((sum, row) => sum + row.amount_paise, 0)} />
                  </td>
                  <td className="num">
                    <Money paise={data.charges.reduce((sum, row) => sum + row.settled_paise, 0)} />
                  </td>
                  <td className="num">
                    <Money
                      paise={data.charges.reduce((sum, row) => sum + row.outstanding_paise, 0)}
                      tone="owed-by"
                    />
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>

      <div className="grid grid--halves">
        <Card title="Payments" flush>
          {data.payments.length === 0 ? (
            <EmptyState title="No payments recorded" />
          ) : (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Payments</caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Kind</th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                    <th scope="col" className="num">
                      Applied
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.payments.map((payment) => (
                    <tr key={payment.payment_id}>
                      <td className="nowrap">{formatDate(payment.payment_date)}</td>
                      <td>
                        <Badge tone={payment.status === 'active' ? 'accent' : 'neutral'}>
                          {PAYMENT_KIND_LABELS[payment.payment_kind] ?? payment.payment_kind}
                        </Badge>
                        {payment.status !== 'active' && (
                          <>
                            {' '}
                            <Badge tone="warn">{payment.status}</Badge>
                          </>
                        )}
                      </td>
                      <td className="num">
                        <Money paise={payment.amount_paise} />
                      </td>
                      <td className="num">
                        <Money paise={payment.applied_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          title="Unapplied credits"
          hint="An overpayment stays here as a visible credit until it is applied to a charge."
          flush
        >
          {data.unapplied_credits.length === 0 ? (
            <EmptyState icon="✓" title="No unapplied credit" />
          ) : (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Unapplied credits</caption>
                <thead>
                  <tr>
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
                  </tr>
                </thead>
                <tbody>
                  {data.unapplied_credits.map((credit) => (
                    <tr key={credit.payment_id}>
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
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  )
}
