/**
 * What the household owes whoever it pays for a month's bill -- the landlord
 * for rent, each provider for furniture, any service -- and the payouts made to
 * them: the Provider Payout table every such screen shares, laid out column for
 * column as the Resident Collection table above it, and the payout statement a
 * row opens.
 *
 * Once the month is billed, the whole row opens its payout statement (Rule U7);
 * the Pay Now button in it does its own thing instead. Pay Now is on every row,
 * so the column reads the same down the table, and is off until the month is
 * billed and once the payee is paid in full.
 */
import { type ReactNode } from 'react'
import { Check, Clock, CreditCard, Trash2 } from 'lucide-react'
import { Modal } from './Primitives'
import { Avatar, BUTTON_QUIET, BUTTON_SECONDARY, EmptyBlock, MiniStat, Pill, Rupees, type Tone } from './ui'
import { formatDate, formatInr, formatMonth } from '../lib/format'

/** Unpaid until the payee is paid anything; the bill can be paid in tranches. */
export type PayoutStatus = 'pending' | 'partially_disbursed' | 'disbursed'

/** The payee's side of the month, worded as the collection table words a resident's. */
export const PAYOUT_STATUS: Record<PayoutStatus, { tone: Tone; label: string }> = {
  pending: { tone: 'warning', label: 'Unpaid' },
  partially_disbursed: { tone: 'warning', label: 'Part paid' },
  disbursed: { tone: 'success', label: 'Paid' },
}

/** One payment to the payee, from the kitty or advanced by a resident. */
export interface PayoutTranche {
  disbursement_id: string
  disbursed_at: string
  amount_paise: number
  disbursed_by: 'pool' | 'resident_advance'
  funding_resident_id: string | null
  funding_resident_name: string | null
  disbursement_reference: string
  note: string | null
}

export interface ProviderPayoutItem {
  id: string
  /** The landlord, or the provider. */
  name: string
  /** Under the name, e.g. where the money goes. */
  subtitle?: string
  totalPaise: number
  /** Null shows a dash: nothing is paid or owed until the month is billed. */
  paidPaise: number | null
  stillOwedPaise: number | null
  /** Shown under what is still owed, e.g. "3 days overdue". */
  overdueNotice?: ReactNode
  /** Null shows "Not billed". */
  status: PayoutStatus | null
  canPay: boolean
  /** Pay Now's accessible name; "Pay now to <name>" unless given. */
  payLabel?: string
  onPay?: () => void
  /** Opens the payout statement; without it the row opens nothing. */
  onViewStatement?: () => void
}

/** The column headings that name what is paid, and to whom. */
export interface PayoutColumns {
  payee: string
  total: string
  paid: string
}

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]'

const DASH = <span className="text-[var(--text-tertiary)]">—</span>

function StatusPill({ status }: { status: PayoutStatus }) {
  const { tone, label } = PAYOUT_STATUS[status]
  return (
    <Pill tone={tone}>
      {status === 'disbursed' ? (
        <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
      ) : (
        <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
      )}
      {label}
    </Pill>
  )
}

export function ProviderPayoutTable({
  caption,
  columns = { payee: 'Provider', total: 'Total Bill', paid: 'Paid to Provider' },
  items,
}: {
  caption: string
  columns?: PayoutColumns
  items: ProviderPayoutItem[]
}) {
  return (
    <div className="overflow-x-auto min-w-0">
      <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className={TABLE_HEAD}>
            <th scope="col" className="w-[28%] pb-3 pr-3 font-bold text-left">{columns.payee}</th>
            <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">{columns.total}</th>
            <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">{columns.paid}</th>
            <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Still Owed</th>
            <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Status</th>
            <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
          {items.map((row) => {
            const view = row.onViewStatement
            return (
              <tr
                key={row.id}
                className={
                  view
                    ? 'align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors'
                    : 'align-middle'
                }
                {...(view && {
                  tabIndex: 0,
                  title: `View the ${row.name} payout statement`,
                  onClick: view,
                  onKeyDown: (event: React.KeyboardEvent<HTMLTableRowElement>) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      view()
                    }
                  },
                })}
              >
                <th scope="row" className="py-3 pr-3 text-left font-normal">
                  <span className="flex items-center gap-2.5 min-w-0">
                    <Avatar name={row.name} />
                    {row.subtitle ? (
                      <span className="min-w-0">
                        <span className="block font-semibold text-[var(--text-primary)] truncate">{row.name}</span>
                        <span
                          className="block text-[11px] text-[var(--text-tertiary)] tabular truncate"
                          title={row.subtitle}
                        >
                          {row.subtitle}
                        </span>
                      </span>
                    ) : (
                      <span className="font-semibold text-[var(--text-primary)] truncate">{row.name}</span>
                    )}
                  </span>
                </th>
                <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                  <Rupees paise={row.totalPaise} />
                </td>
                <td className="py-3 pr-3 text-center">
                  {row.paidPaise == null ? DASH : <Rupees paise={row.paidPaise} />}
                </td>
                <td className="py-3 pr-3 text-center">
                  {row.stillOwedPaise == null ? (
                    DASH
                  ) : (
                    <>
                      <Rupees paise={row.stillOwedPaise} />
                      {row.stillOwedPaise > 0 && row.overdueNotice && (
                        <span className="block text-[10px] font-semibold text-[var(--danger)] tabular">
                          {row.overdueNotice}
                        </span>
                      )}
                    </>
                  )}
                </td>
                <td className="py-3 pr-3 text-center whitespace-nowrap">
                  {row.status ? <StatusPill status={row.status} /> : <Pill>Not billed</Pill>}
                </td>
                <td className="py-3 text-center whitespace-nowrap">
                  <button
                    type="button"
                    className={BUTTON_SECONDARY}
                    disabled={!row.canPay}
                    onClick={(event) => {
                      event.stopPropagation()
                      row.onPay?.()
                    }}
                    aria-label={row.payLabel ?? `Pay now to ${row.name}`}
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
  )
}

/** One payee's side of the month: what was due, what has been paid, and every payout (Rule U7). */
export function ProviderPayoutStatementModal({
  title,
  name,
  month,
  columns = { payee: 'Provider', total: 'Total Bill', paid: 'Paid to Provider' },
  totalPaise,
  paidPaise,
  stillOwedPaise,
  status,
  disbursements,
  caption = `Payments to ${name}`,
  onDeleteDisbursement,
  onClose,
}: {
  title: string
  /** The landlord, or the provider. */
  name: string
  month: string
  columns?: PayoutColumns
  totalPaise: number
  paidPaise: number
  stillOwedPaise: number
  status: PayoutStatus
  disbursements: PayoutTranche[]
  /** The payouts table's accessible name; "Payments to <name>" unless given. */
  caption?: string
  /** Deletes a payout recorded by mistake, while the month is open; without it none is offered. */
  onDeleteDisbursement?: (tranche: PayoutTranche) => void
  onClose: () => void
}) {
  return (
    <Modal title={title} description={`${name} · ${formatMonth(month)}`} onClose={onClose}>
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat label={columns.total} value={<Rupees paise={totalPaise} />} />
          <MiniStat label={columns.paid} value={<Rupees paise={paidPaise} />} />
          <MiniStat label="Still Owed" value={<Rupees paise={stillOwedPaise} />} />
        </div>

        <section aria-label="Disbursement history" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Disbursement History</h3>
            <StatusPill status={status} />
          </div>
          {disbursements.length === 0 ? (
            <EmptyBlock title="No payouts recorded yet for this month." />
          ) : (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[560px] border-collapse">
                <caption className="sr-only">{caption}</caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Date</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Amount</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Funded From</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Reference / UTR</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Note</th>
                    {onDeleteDisbursement && <th scope="col" className="pb-3 font-bold text-center">Actions</th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {disbursements.map((tranche) => (
                    <tr key={tranche.disbursement_id} className="align-middle">
                      <td className="py-2.5 pr-3 text-center tabular">{formatDate(tranche.disbursed_at)}</td>
                      <td className="py-2.5 pr-3 text-center font-bold text-[var(--text-primary)]">
                        <Rupees paise={tranche.amount_paise} />
                      </td>
                      <td className="py-2.5 pr-3 text-center">
                        {tranche.disbursed_by === 'pool'
                          ? 'House Kitty'
                          : `${tranche.funding_resident_name ?? tranche.funding_resident_id} (advance)`}
                      </td>
                      <td className="py-2.5 pr-3 text-center tabular break-all">{tranche.disbursement_reference}</td>
                      <td className="py-2.5 pr-3 text-center text-[var(--text-secondary)]">{tranche.note ?? DASH}</td>
                      {onDeleteDisbursement && (
                        <td className="py-2.5 text-center">
                          <button
                            type="button"
                            className={BUTTON_QUIET}
                            onClick={() => onDeleteDisbursement(tranche)}
                            aria-label={`Delete the ${formatInr(tranche.amount_paise)} payout of ${formatDate(tranche.disbursed_at)}`}
                          >
                            <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                            Delete
                          </button>
                        </td>
                      )}
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
