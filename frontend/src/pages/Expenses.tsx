/** General expenses: search, filter, record, edit, and inspect allocations. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Page, useApp } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Card,
  ConfirmDialog,
  EmptyState,
  Money,
  Pagination,
  StatementStatusBadge,
} from '../components/Primitives'
import { CheckboxField, SelectField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { api, downloadCsv } from '../api/client'
import { useDebounced, useMutation, useResource } from '../lib/useApi'
import {
  ALLOCATION_METHOD_LABELS,
  PERIOD_RULE_LABELS,
  formatDate,
  formatInr,
  formatMonth,
} from '../lib/format'
import type { ExpenseRow, Paged, Resident } from '../api/types'
import { EditExpenseDialog, ExpenseFormDialog } from './ExpenseForm'
import { ExpenseDetailDialog } from './ExpenseDetail'

const PAGE_SIZE = 25

export default function ExpensesPage() {
  const { month } = useApp()
  const toast = useToast()
  const residents = useResource<{ items: Resident[] }>('/api/residents')

  const [search, setSearch] = useState('')
  const [residentId, setResidentId] = useState('')
  const [kind, setKind] = useState('')
  const [useMonthFilter, setUseMonthFilter] = useState(true)
  const [unattachedOnly, setUnattachedOnly] = useState(false)
  const [sort, setSort] = useState('expense_date_desc')
  const [offset, setOffset] = useState(0)
  const debouncedSearch = useDebounced(search)

  const query = {
    settlement_period: useMonthFilter ? month : undefined,
    resident_id: residentId || undefined,
    expense_kind: kind || undefined,
    search: debouncedSearch || undefined,
    unattached_only: unattachedOnly || undefined,
    sort,
    limit: PAGE_SIZE,
    offset,
  }
  const expenses = useResource<Paged<ExpenseRow> & { total_amount_paise: number }>(
    '/api/expenses',
    query,
  )

  const [creating, setCreating] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [viewingId, setViewingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<ExpenseRow | null>(null)

  return (
    <Page
      title="Expenses"
      subtitle="Household costs and how each one is shared. An expense's period rule decides which statement month charges it."
      breadcrumbs={[{ label: 'Record' }, { label: 'Expenses' }]}
      actions={
        <>
          <button
            type="button"
            className="btn"
            onClick={() =>
              downloadCsv('/api/expenses/export/csv', {
                settlement_period: useMonthFilter ? month : undefined,
              })
            }
          >
            Export CSV
          </button>
          <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
            Record an expense
          </button>
        </>
      }
    >
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
            placeholder="Description, provider, reference…"
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
            hint="Matches expenses this resident holds a share of."
          />
          <SelectField
            label="Kind"
            value={kind}
            options={[
              { value: 'general', label: 'General' },
              { value: 'electricity', label: 'Electricity' },
            ]}
            onChange={(value) => {
              setKind(value)
              setOffset(0)
            }}
            placeholder="All kinds"
          />
          <SelectField
            label="Sort"
            value={sort}
            options={[
              { value: 'expense_date_desc', label: 'Newest first' },
              { value: 'expense_date_asc', label: 'Oldest first' },
              { value: 'amount_desc', label: 'Largest amount' },
              { value: 'amount_asc', label: 'Smallest amount' },
              { value: 'description_asc', label: 'Description A–Z' },
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
            hint="Filters by settlement month."
          />
          <CheckboxField
            label="Not in a statement"
            checked={unattachedOnly}
            onChange={(value) => {
              setUnattachedOnly(value)
              setOffset(0)
            }}
          />
        </div>
      </Card>

      <Card
        title="Expenses"
        hint={
          expenses.data
            ? `${expenses.data.total} matching · ${formatInr(expenses.data.total_amount_paise)} in total`
            : undefined
        }
        flush
        footer={
          expenses.data ? (
            <Pagination
              total={expenses.data.total}
              limit={PAGE_SIZE}
              offset={offset}
              onChange={setOffset}
              noun="expenses"
            />
          ) : undefined
        }
      >
        <AsyncSection
          resource={expenses}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No expenses match"
              body={
                useMonthFilter
                  ? `Nothing settles in ${formatMonth(month)} yet. Record one, or clear the month filter.`
                  : 'Record the first household expense.'
              }
              action={
                <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
                  Record an expense
                </button>
              }
            />
          }
        >
          {(data) => (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Expenses</caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Description</th>
                    <th scope="col">Service → settlement</th>
                    <th scope="col">Split</th>
                    <th scope="col">Funded by</th>
                    <th scope="col">Statement</th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((expense) => {
                    const funding = expense.linked_advance_paise
                      ? 'linked advance'
                      : expense.payer_display_name
                        ? expense.payer_display_name
                        : 'household pool'
                    const locked = expense.statement_status === 'closed'
                    return (
                      <tr key={expense.expense_id}>
                        <td className="nowrap">{formatDate(expense.expense_date)}</td>
                        <th scope="row">
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            style={{ padding: 0, fontWeight: 600 }}
                            onClick={() => setViewingId(expense.expense_id)}
                          >
                            {expense.description}
                          </button>
                          <div className="subtle">
                            {expense.definition_name ?? 'No category'}
                            {expense.expense_kind === 'electricity' && (
                              <>
                                {' '}
                                <Badge tone="accent" glyph="⚡">
                                  Electricity
                                </Badge>
                              </>
                            )}
                            {Boolean(expense.generated_from_recurrence) && (
                              <>
                                {' '}
                                <Badge tone="neutral">recurring</Badge>
                              </>
                            )}
                          </div>
                        </th>
                        <td className="nowrap">
                          {formatMonth(expense.service_period)}
                          <div className="subtle">
                            → {formatMonth(expense.settlement_period)}
                            {expense.period_rule && (
                              <>
                                {' · '}
                                {PERIOD_RULE_LABELS[expense.period_rule] ?? expense.period_rule}
                              </>
                            )}
                          </div>
                        </td>
                        <td>
                          {ALLOCATION_METHOD_LABELS[expense.allocation_method ?? ''] ??
                            expense.allocation_method ??
                            '—'}
                          <div className="subtle">
                            {expense.allocation_count} share
                            {expense.allocation_count === 1 ? '' : 's'}
                            {expense.allocated_paise !== expense.amount_paise && (
                              <>
                                {' · '}
                                <span style={{ color: 'var(--legacy-danger)' }}>does not balance</span>
                              </>
                            )}
                          </div>
                        </td>
                        <td>{funding}</td>
                        <td>
                          {expense.statement_id ? (
                            <Link to={`/statements/${expense.statement_id}`}>
                              <StatementStatusBadge status={expense.statement_status} />
                            </Link>
                          ) : (
                            <StatementStatusBadge status="unattached" />
                          )}
                        </td>
                        <td className="num">
                          <Money paise={expense.amount_paise} />
                        </td>
                        <td>
                          <div className="row" style={{ gap: 6 }}>
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => setViewingId(expense.expense_id)}
                            >
                              View
                            </button>
                            {expense.expense_kind === 'electricity' ? (
                              <Link className="btn btn--sm" to="/services/electricity">
                                Bills
                              </Link>
                            ) : (
                              <button
                                type="button"
                                className="btn btn--sm"
                                disabled={locked}
                                title={
                                  locked ? 'This statement is closed. Use a ledger adjustment.' : undefined
                                }
                                onClick={() => setEditingId(expense.expense_id)}
                              >
                                Edit
                              </button>
                            )}
                            <button
                              type="button"
                              className="btn btn--sm"
                              disabled={
                                expense.statement_status === 'closed' ||
                                expense.statement_status === 'finalized_correction_window'
                              }
                              onClick={() => setDeletingId(expense)}
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={6}>Shown on this page</td>
                    <td className="num">
                      <Money
                        paise={data.items.reduce((sum, row) => sum + row.amount_paise, 0)}
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

      {creating && (
        <ExpenseFormDialog
          defaultSettlementPeriod={month}
          onClose={() => setCreating(false)}
          onSaved={(message) => {
            setCreating(false)
            expenses.reload()
            toast.success(message)
          }}
        />
      )}
      {editingId && (
        <EditExpenseDialog
          expenseId={editingId}
          defaultSettlementPeriod={month}
          onClose={() => setEditingId(null)}
          onSaved={(message) => {
            setEditingId(null)
            expenses.reload()
            toast.success(message)
          }}
        />
      )}
      {viewingId && (
        <ExpenseDetailDialog
          expenseId={viewingId}
          onClose={() => setViewingId(null)}
        />
      )}
      {deletingId && (
        <DeleteExpenseDialog
          expense={deletingId}
          onClose={() => setDeletingId(null)}
          onDeleted={() => {
            setDeletingId(null)
            expenses.reload()
            toast.success('Expense deleted')
          }}
        />
      )}
    </Page>
  )
}

function DeleteExpenseDialog({
  expense,
  onClose,
  onDeleted,
}: {
  expense: ExpenseRow
  onClose: () => void
  onDeleted: () => void
}) {
  const [reason, setReason] = useState('')
  const remove = useMutation(
    () =>
      api.delete(`/api/expenses/${encodeURIComponent(expense.expense_id)}`, {
        reason: reason || undefined,
      }),
    { onSuccess: onDeleted },
  )
  return (
    <ConfirmDialog
      title={`Delete “${expense.description}”?`}
      confirmLabel="Delete expense"
      tone="danger"
      pending={remove.pending}
      error={remove.error}
      onCancel={onClose}
      onConfirm={() => void remove.run(undefined)}
      body={
        <>
          This removes the expense and its resident shares. It is only possible while the expense
          is unattached or in a draft statement, and only if no payment has been applied to its
          charges. The deletion itself is recorded in the audit trail.
        </>
      }
    >
      <TextField
        label="Reason (optional)"
        value={reason}
        onChange={setReason}
        hint="Recorded in the audit trail."
        maxLength={500}
      />
    </ConfirmDialog>
  )
}
