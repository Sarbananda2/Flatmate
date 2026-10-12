/**
 * Finalization is irreversible, so these tests cover the guards around it:
 * an explicit confirmation dialog, a visible warning when expenses for the
 * month are still unattached, and a refusal while accounting errors remain.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StatementDetailPage from './StatementDetail'
import { ToastProvider } from '../components/Toast'
import { installFetchStub, type Recorded } from '../test/server'

const STATEMENT_ID = 'stmt-sep'

const baseStatement = {
  statement_id: STATEMENT_ID,
  apartment_id: 'home',
  settlement_month: '2026-09',
  status: 'draft' as const,
  created_at: '2026-09-01T09:00:00+05:30',
  finalized_at: null,
  correction_deadline: null,
  closed_at: null,
  finalized_by: null,
  correction_window_hours: 72,
  notes: null,
  has_snapshot: false,
  expense_count: 1,
  statement_total_paise: 500000,
  revision_count: 0,
  correction_window_open: false,
  correction_hours_remaining: 0,
  revisions: [],
}

function preview(overrides: Record<string, unknown> = {}) {
  return {
    ...baseStatement,
    snapshot_version: 2,
    generated_at: '2026-09-30T10:00:00+05:30',
    as_of_date: null,
    pool_account_id: '__pool__',
    residents: [
      { resident_id: 'alice', display_name: 'Alice', is_active: 1 },
      { resident_id: 'bob', display_name: 'Bob', is_active: 1 },
    ],
    expenses: [
      {
        expense_id: 'rent-sep',
        description: 'Rent',
        amount_paise: 500000,
        expense_date: '2026-09-01',
        service_period: '2026-09',
        settlement_period: '2026-09',
        period_rule: 'current_month',
        expense_kind: 'general',
        allocation_method: 'equal',
        category: 'Housing',
        definition_name: 'Rent',
        provider_name: 'Landlord',
        payer_resident_id: 'alice',
        payer_display_name: 'Alice',
        generated_from_recurrence: true,
        funding_source: 'payer_resident',
        linked_provider_advances: [],
        allocations: [
          { resident_id: 'alice', amount_paise: 250000 },
          { resident_id: 'bob', amount_paise: 250000 },
        ],
        allocation_details: [],
      },
    ],
    statement_charges_paise_by_resident: { alice: 250000, bob: 250000 },
    payments_in_month: [],
    adjustments: [],
    net_positions_paise_by_account: { alice: 250000, bob: -250000, __pool__: 0 },
    net_positions_sum_paise: 0,
    accounts: [
      {
        account_id: 'alice',
        account_type: 'resident' as const,
        display_name: 'Alice',
        is_active: true,
        net_position_paise: 250000,
        allocated_charges_paise: 250000,
        settled_paise: 0,
        outstanding_charges_paise: 250000,
        unapplied_credit_paise: 0,
        funding_credits_paise: 500000,
        pool_payments_paise: 0,
        adjustments_paise: 0,
      },
      {
        account_id: 'bob',
        account_type: 'resident' as const,
        display_name: 'Bob',
        is_active: true,
        net_position_paise: -250000,
        allocated_charges_paise: 250000,
        settled_paise: 0,
        outstanding_charges_paise: 250000,
        unapplied_credit_paise: 0,
        funding_credits_paise: 0,
        pool_payments_paise: 0,
        adjustments_paise: 0,
      },
      {
        account_id: '__pool__',
        account_type: 'pool' as const,
        display_name: 'Household pool',
        is_active: true,
        net_position_paise: 0,
        expenses_incurred_paise: 500000,
        funded_by_residents_paise: 500000,
      },
    ],
    transfers: [
      {
        from_account: 'bob',
        to_account: 'alice',
        amount_paise: 250000,
        from_display_name: 'Bob',
        to_display_name: 'Alice',
      },
    ],
    outstanding_charges_paise_by_resident: { alice: 250000, bob: 250000 },
    unapplied_credits_paise_by_resident: { alice: 0, bob: 0 },
    household_expenses_paise_by_settlement_period: { '2026-09': 500000 },
    totals: {
      household_expenses_paise: 500000,
      allocated_charges_paise: 500000,
      outstanding_charges_paise: 500000,
      unapplied_credits_paise: 0,
      provider_advances_paise: 0,
      expense_funding_paise: 500000,
      pool_payments_paise: 0,
      adjustments_paise: 0,
      pool_net_position_paise: 0,
    },
    entry_count: 6,
    validation: { issues: [], error_count: 0, warning_count: 0, is_reconcilable: true },
    notes: [],
    revisions: [],
    snapshot_hash: 'a'.repeat(64),
    warning: '',
    candidate_expenses: [],
    candidate_expense_total_paise: 0,
    blocking_issues: [],
    can_finalize: true,
    finalize_blocked_reason: null,
    ...overrides,
  }
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function renderPage(
  previewPayload: Record<string, unknown>,
  statementPayload: Record<string, unknown> = baseStatement,
) {
  stub = installFetchStub({
    routes: {
      '/health': { status: 'ok', version: '1.0.0', schema_version: 2 },
      '/api/apartments': { items: [], total: 0, default_apartment_id: null },
      [`/api/statements/${STATEMENT_ID}`]: statementPayload,
      [`/api/statements/${STATEMENT_ID}/preview`]: previewPayload,
      [`POST /api/statements/${STATEMENT_ID}/finalize`]: { status: 'finalized_correction_window' },
    },
  })
  return render(
    // A real <Route> is needed so useParams() resolves the statement id.
    <MemoryRouter initialEntries={[`/statements/${STATEMENT_ID}`]}>
      <ToastProvider>
        <Routes>
          <Route path="/statements/:statementId" element={<StatementDetailPage />} />
        </Routes>
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('StatementDetailPage', () => {
  it('shows the month total and the resident charges from the preview', async () => {
    renderPage(preview())
    expect(await screen.findByText('Statement total')).toBeInTheDocument()
    const totals = screen.getAllByText('₹5,000.00')
    expect(totals.length).toBeGreaterThan(0)
    expect(screen.getAllByText('₹2,500.00').length).toBeGreaterThan(0)
  })

  it('states which expense was funded by whom, separately from the shares', async () => {
    renderPage(preview())
    const table = await screen.findByRole('table', { name: 'Expenses in this statement' })
    const row = table.querySelector('tbody tr')
    expect(row).toHaveTextContent('Alice')
    expect(row).toHaveTextContent('recurring')
  })

  it('requires an explicit confirmation before finalizing', async () => {
    renderPage(preview())
    await userEvent.click(await screen.findByRole('button', { name: 'Finalize…' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(/not reversible/)
    expect(stub!.calls.some((call) => call.path.endsWith('/finalize'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Finalize statement' }))
    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path.endsWith('/finalize'))).toBe(true),
    )
    const call = stub!.calls.find((item) => item.path.endsWith('/finalize'))
    expect((call!.body as { confirm: boolean }).confirm).toBe(true)
  })

  it('warns when expenses for the month are still unattached', async () => {
    renderPage(
      preview({
        candidate_expenses: [
          {
            expense_id: 'groc-sep',
            description: 'Groceries',
            amount_paise: 120000,
            expense_date: '2026-09-04',
            settlement_period: '2026-09',
            service_period: '2026-08',
            expense_kind: 'general',
            payer_resident_id: 'bob',
          },
        ],
        candidate_expense_total_paise: 120000,
      }),
    )
    expect(
      await screen.findByText(/1 expense\(s\) settle in this month but are not attached/),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Finalize…' }))
    expect(
      await screen.findByText(/Some expenses for this month are not attached/),
    ).toBeInTheDocument()
  })

  it('blocks finalization while an accounting error stands', async () => {
    const issue = {
      code: 'allocation_total_mismatch',
      severity: 'error' as const,
      message: "Expense 'Rent' is 500000 paise but its resident allocations add up to 400000 paise.",
      entity_type: 'expense',
      entity_id: 'rent-sep',
      details: {},
      suggested_action: "Edit the expense's allocations so they sum to its amount.",
    }
    renderPage(
      preview({
        blocking_issues: [issue],
        validation: { issues: [issue], error_count: 1, warning_count: 0, is_reconcilable: false },
        can_finalize: false,
      }),
    )
    expect(
      await screen.findByText(/These must be resolved before finalizing/),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Finalize…' }))
    const confirm = await screen.findByRole('button', { name: 'Finalize statement' })
    expect(confirm).toBeDisabled()
    expect(stub!.calls.some((call) => call.path.endsWith('/finalize'))).toBe(false)
  })

  it('shows a closed statement as immutable and offers no finalize action', async () => {
    renderPage(
      preview({ status: 'closed', closed_at: '2026-10-04T10:00:00+05:30' }),
      { ...baseStatement, status: 'closed' as const, closed_at: '2026-10-04T10:00:00+05:30' },
    )
    expect(await screen.findByText(/This statement is closed/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Finalize…' })).not.toBeInTheDocument()
    expect(screen.getByText(/signed ledger adjustment/)).toBeInTheDocument()
  })
})
