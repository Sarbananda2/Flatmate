/**
 * What each resident owes for a month's household bill -- rent, furniture,
 * any service -- and how much of it has come into the House Kitty: the
 * Resident Collection table every such screen shares, column for column.
 *
 * The whole row opens the resident's statement for the month (Rule U7); the
 * name's link and the Pay Now button in it do their own thing instead. Pay Now
 * is on every row, so the column reads the same down the table, and is off
 * once there is nothing to pay.
 */
import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Check, Clock, CreditCard } from 'lucide-react'
import { Avatar, BUTTON_SECONDARY, Pill, Rupees } from './ui'

export type CollectionStatus = 'paid' | 'partial' | 'unpaid'

export interface ResidentCollectionItem {
  id: string
  name: string
  /** The resident's own page: the name links to it without opening the statement. */
  href?: string
  subtitle?: ReactNode
  subtitleTone?: 'warning' | 'muted'
  sharePaise: number
  shareNote?: ReactNode
  /** Null shows a dash: nothing to pay in yet, or a share covered by paying the provider directly. */
  paidIntoKittyPaise: number | null
  stillOwedPaise: number | null
  /** Shown under what is still owed, e.g. "3 days overdue". */
  overdueNotice?: ReactNode
  /** Null shows a dash: nothing is owed until the month is billed. */
  status: CollectionStatus | null
  canPay: boolean
  onPay?: () => void
  onViewStatement?: () => void
}

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]'

const STATUS: Record<CollectionStatus, { label: string; tone: 'success' | 'warning' }> = {
  paid: { label: 'Paid', tone: 'success' },
  partial: { label: 'Part paid', tone: 'warning' },
  unpaid: { label: 'Unpaid', tone: 'warning' },
}

export function ResidentCollectionTable({ caption, items }: { caption: string; items: ResidentCollectionItem[] }) {
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  return (
    <div className="overflow-x-auto min-w-0">
      <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className={TABLE_HEAD}>
            <th scope="col" className="w-[28%] pb-3 pr-3 font-bold text-left">Resident</th>
            <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Share</th>
            <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Paid into Kitty</th>
            <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Still owed</th>
            <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Status</th>
            <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
          {items.map((row) => {
            const status = row.status ? STATUS[row.status] : null
            return (
              <tr
                key={row.id}
                className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                tabIndex={0}
                title={`View ${row.name}'s statement`}
                onClick={row.onViewStatement}
                onKeyDown={(event) => {
                  if (event.target !== event.currentTarget) return
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    row.onViewStatement?.()
                  }
                }}
              >
                <th scope="row" className="py-3 pr-3 text-left font-normal">
                  <span className="flex items-center gap-2.5 min-w-0">
                    <Avatar name={row.name} />
                    <span className="min-w-0">
                      {row.href ? (
                        <Link
                          className="block font-semibold text-[var(--text-primary)] truncate"
                          to={row.href}
                          onClick={(event) => event.stopPropagation()}
                        >
                          {row.name}
                        </Link>
                      ) : (
                        <span className="block font-semibold text-[var(--text-primary)] truncate">{row.name}</span>
                      )}
                      {row.subtitle && (
                        <span
                          className={
                            row.subtitleTone === 'warning'
                              ? 'block text-[11px] font-normal text-[var(--warning)] truncate'
                              : 'block text-[11px] font-normal text-[var(--text-tertiary)] truncate'
                          }
                        >
                          {row.subtitle}
                        </span>
                      )}
                    </span>
                  </span>
                </th>
                <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                  <Rupees paise={row.sharePaise} />
                  {row.shareNote && (
                    <span className="block text-[10px] font-normal text-[var(--text-tertiary)]">{row.shareNote}</span>
                  )}
                </td>
                <td className="py-3 pr-3 text-center">
                  {row.paidIntoKittyPaise != null ? <Rupees paise={row.paidIntoKittyPaise} /> : dash}
                </td>
                <td className="py-3 pr-3 text-center">
                  {row.stillOwedPaise != null ? (
                    <>
                      <Rupees paise={row.stillOwedPaise} />
                      {row.overdueNotice && (
                        <span className="block text-[10px] font-semibold text-[var(--danger)] tabular">
                          {row.overdueNotice}
                        </span>
                      )}
                    </>
                  ) : (
                    dash
                  )}
                </td>
                <td className="py-3 pr-3 text-center whitespace-nowrap">
                  {status ? (
                    <Pill tone={status.tone}>
                      {row.status === 'paid' ? (
                        <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
                      ) : (
                        <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
                      )}
                      {status.label}
                    </Pill>
                  ) : (
                    dash
                  )}
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
                    aria-label={`Pay now for ${row.name}`}
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
