/** The searchable transaction ledger: expenses, payments, and adjustments together. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Download, FileBarChart, History, SlidersHorizontal } from 'lucide-react'
import { clsx } from 'clsx'
import { useApp } from '../components/Layout'
import { AsyncSection, Pagination } from '../components/Primitives'
import {
  BUTTON_SECONDARY,
  EmptyBlock,
  MiniStat,
  Panel,
  Pill,
  Rupees,
  type Tone,
} from '../components/ui'
import { CheckboxField, DateField, SelectField, TextField } from '../components/Form'
import { downloadCsv } from '../api/client'
import { useDebounced, useResource } from '../lib/useApi'
import { PAYMENT_KIND_LABELS, formatDate, formatMonth } from '../lib/format'
import type { Paged, TransactionRow } from '../api/types'

const PAGE_SIZE = 50

const RECORD_TABS = [
  { value: '', label: 'All Records' },
  { value: 'expense', label: 'Expenses' },
  { value: 'payment', label: 'Transfers & Payments' },
  { value: 'adjustment', label: 'Adjustments' },
]

function typeLabel(row: TransactionRow): { tone: Tone; label: string } {
  if (row.transaction_type === 'payment') {
    return { tone: 'success', label: PAYMENT_KIND_LABELS[row.subtype] ?? row.subtype }
  }
  if (row.transaction_type === 'expense') {
    return { tone: 'accent', label: row.subtype === 'electricity' ? 'Electricity' : 'Expense' }
  }
  return { tone: 'warning', label: row.subtype === 'credit' ? 'Credit adjustment' : 'Debit adjustment' }
}

function statusTone(status: string): Tone {
  if (status === 'closed' || status === 'active' || status === 'posted') return 'success'
  if (status === 'draft' || status === 'unattached') return 'neutral'
  return 'warning'
}

interface FilterOptions {
  residents: Array<{ resident_id: string; display_name: string; is_active: number }>
  categories: string[]
  settlement_periods: string[]
  transaction_types: string[]
  statuses: string[]
}

export default function TransactionsPage() {
  const { month } = useApp()
  const options = useResource<FilterOptions>('/api/transactions/filter-options')

  const [search, setSearch] = useState('')
  const [useMonthFilter, setUseMonthFilter] = useState(false)
  const [residentId, setResidentId] = useState('')
  const [category, setCategory] = useState('')
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [sort, setSort] = useState('date_desc')
  const [offset, setOffset] = useState(0)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const debouncedSearch = useDebounced(search)

  const reset = () => {
    setSearch('')
    setUseMonthFilter(false)
    setResidentId('')
    setCategory('')
    setType('')
    setStatus('')
    setDateFrom('')
    setDateTo('')
    setOffset(0)
  }

  const query = {
    settlement_period: useMonthFilter ? month : undefined,
    resident_id: residentId || undefined,
    category: category || undefined,
    transaction_type: type || undefined,
    status: status || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    search: debouncedSearch || undefined,
    sort,
    limit: PAGE_SIZE,
    offset,
  }
  const transactions = useResource<
    Paged<TransactionRow> & {
      totals_by_type: Record<string, { count: number; total_paise: number }>
    }
  >('/api/transactions', query)

  const totals = transactions.data?.totals_by_type ?? {}
  const activeFilters = [
    search,
    residentId,
    category,
    status,
    dateFrom,
    dateTo,
    useMonthFilter ? 'month' : '',
  ].filter(Boolean).length

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MiniStat
          label="Total spent"
          value={<Rupees paise={totals.expense?.total_paise ?? 0} />}
          note={`${totals.expense?.count ?? 0} household expenses`}
        />
        <MiniStat
          label="Transfers & payments"
          value={<Rupees paise={totals.payment?.total_paise ?? 0} />}
          note={`${totals.payment?.count ?? 0} — these settle, they are not spend`}
        />
        <MiniStat
          label="Adjustments"
          value={<Rupees paise={totals.adjustment?.total_paise ?? 0} signed />}
          note={`${totals.adjustment?.count ?? 0} signed corrections`}
        />
        <MiniStat
          label="Matching records"
          value={<span className="tabular">{transactions.data?.total ?? '—'}</span>}
          note={useMonthFilter ? `Settling in ${formatMonth(month)}` : 'Across all months'}
        />
      </div>

      <Panel
        headingLevel={1}
        title="Activity Ledger"
        hint="One consolidated record of every cost, payment, and adjustment. The three kinds are totalled separately, because adding them would count the same rupee twice."
        actions={
          <>
            <button
              type="button"
              className={BUTTON_SECONDARY}
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen((open) => !open)}
            >
              <SlidersHorizontal className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Filters{activeFilters > 0 ? ` (${activeFilters})` : ''}
            </button>
            <button
              type="button"
              className={BUTTON_SECONDARY}
              onClick={() =>
                downloadCsv('/api/transactions/export/csv', {
                  settlement_period: useMonthFilter ? month : undefined,
                  resident_id: residentId || undefined,
                  transaction_type: type || undefined,
                })
              }
            >
              <Download className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Export CSV
            </button>
            <Link className={BUTTON_SECONDARY} to="/reports">
              <FileBarChart className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Reports & Exports
            </Link>
            <Link className={BUTTON_SECONDARY} to="/audit">
              <History className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Audit Trail
            </Link>
          </>
        }
      >
        <div
          className="inline-flex max-w-full overflow-x-auto rounded-xl p-1 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-xs font-medium"
          role="group"
          aria-label="Record type"
        >
          {RECORD_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              aria-pressed={type === tab.value}
              onClick={() => {
                setType(tab.value)
                setOffset(0)
              }}
              className={clsx(
                'px-3.5 py-1 rounded-lg whitespace-nowrap cursor-pointer transition-colors',
                type === tab.value
                  ? 'bg-[var(--accent)] text-white font-semibold'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {filtersOpen && (
          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-3">
            <div className="filters">
              <TextField
                label="Search"
                type="search"
                value={search}
                onChange={(value) => {
                  setSearch(value)
                  setOffset(0)
                }}
                placeholder="Description, counterparty, ID…"
              />
              <SelectField
                label="Resident"
                value={residentId}
                options={(options.data?.residents ?? []).map((resident) => ({
                  value: resident.resident_id,
                  label: resident.display_name,
                }))}
                onChange={(value) => {
                  setResidentId(value)
                  setOffset(0)
                }}
                placeholder="Anyone"
                hint="Matches their own transactions and expenses they hold a share of."
              />
              <SelectField
                label="Category"
                value={category}
                options={(options.data?.categories ?? []).map((value) => ({ value, label: value }))}
                onChange={(value) => {
                  setCategory(value)
                  setOffset(0)
                }}
                placeholder="All categories"
              />
              <SelectField
                label="Status"
                value={status}
                options={(options.data?.statuses ?? []).map((value) => ({
                  value,
                  label: value.replace(/_/g, ' '),
                }))}
                onChange={(value) => {
                  setStatus(value)
                  setOffset(0)
                }}
                placeholder="All statuses"
              />
              <DateField
                label="From date"
                value={dateFrom}
                onChange={(value) => {
                  setDateFrom(value)
                  setOffset(0)
                }}
              />
              <DateField
                label="To date"
                value={dateTo}
                onChange={(value) => {
                  setDateTo(value)
                  setOffset(0)
                }}
              />
              <SelectField
                label="Sort"
                value={sort}
                options={[
                  { value: 'date_desc', label: 'Newest first' },
                  { value: 'date_asc', label: 'Oldest first' },
                  { value: 'amount_desc', label: 'Largest amount' },
                  { value: 'amount_asc', label: 'Smallest amount' },
                ]}
                onChange={setSort}
              />
              <CheckboxField
                label={`Only ${formatMonth(month)}`}
                checked={useMonthFilter}
                onChange={(value) => {
                  setUseMonthFilter(value)
                  setOffset(0)
                }}
              />
            </div>
            <div className="flex justify-end">
              <button type="button" className={BUTTON_SECONDARY} onClick={reset}>
                Clear all
              </button>
            </div>
          </div>
        )}

        <AsyncSection
          resource={transactions}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyBlock
              title="No transactions match"
              body="Widen the filters, or record a transaction from the toolbar above."
            />
          }
        >
          {(data) => (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[620px] border-collapse">
                <caption className="sr-only">Transaction ledger</caption>
                <thead>
                  <tr className="border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold text-center">
                    <th scope="col" className="pb-3 pr-3 font-bold">Date</th>
                    <th scope="col" className="pb-3 pr-3 font-bold">Type</th>
                    <th scope="col" className="pb-3 pr-3 font-bold">Category</th>
                    <th scope="col" className="pb-3 pr-3 font-bold">Description</th>
                    <th scope="col" className="pb-3 pr-3 font-bold">Paid By</th>
                    <th scope="col" className="pb-3 pr-3 font-bold">Statement</th>
                    <th scope="col" className="pb-3 font-bold">Amount</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {data.items.map((row) => {
                    const kind = typeLabel(row)
                    return (
                      <tr
                        key={`${row.transaction_type}-${row.transaction_id}`}
                        className="hover:bg-[var(--bg-elevated)] transition-colors align-top"
                      >
                        <td className="py-3.5 pr-3 tabular text-[var(--text-secondary)] whitespace-nowrap">
                          {formatDate(row.transaction_date)}
                        </td>
                        <td className="py-3.5 pr-3 whitespace-nowrap">
                          <Pill tone={kind.tone} className="rounded-md">
                            {kind.label}
                          </Pill>
                        </td>
                        <td className="py-3.5 pr-3 text-[var(--text-primary)] whitespace-nowrap">
                          {row.category || <span className="text-[var(--text-tertiary)]">—</span>}
                        </td>
                        <th scope="row" className="py-3.5 pr-3 text-left font-normal min-w-[14rem]">
                          <span className="block font-semibold text-[var(--text-primary)]">
                            {PAYMENT_KIND_LABELS[row.description] ?? row.description}
                          </span>
                          <span className="block text-[11px] text-[var(--text-secondary)]">
                            {row.effect}
                          </span>
                          <span className="block tabular text-[10px] text-[var(--text-tertiary)] break-all">
                            {row.transaction_id}
                          </span>
                        </th>
                        <td className="py-3.5 pr-3 text-[var(--text-secondary)] whitespace-nowrap">
                          {row.resident_id ? (
                            <Link to={`/residents/${row.resident_id}`}>
                              {row.resident_display_name || row.resident_id}
                            </Link>
                          ) : (
                            row.counterparty || '—'
                          )}
                          {row.resident_id && row.counterparty && (
                            <span className="block text-[11px] text-[var(--text-tertiary)]">
                              {row.counterparty}
                            </span>
                          )}
                        </td>
                        <td className="py-3.5 pr-3 whitespace-nowrap">
                          <span className="block tabular text-[var(--text-secondary)]">
                            {formatMonth(row.settlement_period)}
                          </span>
                          <Pill tone={statusTone(row.status)} className="mt-1">
                            {row.status.replace(/_/g, ' ')}
                          </Pill>
                        </td>
                        <td className="py-3.5 text-center font-bold text-[var(--text-primary)]">
                          <Rupees
                            paise={row.amount_paise}
                            signed={row.transaction_type === 'adjustment'}
                            tone={row.transaction_type === 'payment' ? 'success' : undefined}
                          />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>

        {transactions.data && (
          <div className="-mx-4 sm:-mx-6 -mb-4 sm:-mb-6">
            <Pagination
              total={transactions.data.total}
              limit={PAGE_SIZE}
              offset={offset}
              onChange={setOffset}
              noun="transactions"
            />
          </div>
        )}
      </Panel>
    </div>
  )
}
