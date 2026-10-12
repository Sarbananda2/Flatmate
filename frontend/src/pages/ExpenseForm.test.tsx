/**
 * The expense form is where an allocation becomes a set of charges, so these
 * tests cover the behaviours that protect the accounts:
 *
 * - the breakdown is calculated by the backend and shown before saving;
 * - the form refuses to submit while the shares do not total the amount;
 * - a resident who does not take part is given an explicit zero, not omitted;
 * - the amount is sent as integer paise, never a float.
 */
import { describe, expect, it, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ExpenseFormDialog } from './ExpenseForm'
import { DEFINITIONS, NO_STATEMENTS, RESIDENTS, installFetchStub, type Recorded } from '../test/server'

function equalPreview(body: unknown) {
  const request = body as {
    amount_paise: number
    participants: string[] | null
    payer_resident_id: string | null
  }
  const participants = request.participants ?? ['alice', 'bob', 'chris']
  const base = Math.floor(request.amount_paise / participants.length)
  let remainder = request.amount_paise - base * participants.length
  const shares: Record<string, number> = { alice: 0, bob: 0, chris: 0 }
  // Mirror the engine: remainder paise go to non-payers first.
  const order = [
    ...participants.filter((id) => id !== request.payer_resident_id),
    ...participants.filter((id) => id === request.payer_resident_id),
  ]
  for (const id of participants) shares[id] = base
  for (const id of order) {
    if (remainder <= 0) break
    shares[id] += 1
    remainder -= 1
  }
  return {
    amount_paise: request.amount_paise,
    method: 'equal',
    shares_paise: shares,
    participants: [...participants].sort(),
    total_paise: Object.values(shares).reduce((a, b) => a + b, 0),
    basis: { rounding_rule: 'remainder paise go to non-payers first, in resident-ID order' },
    warnings: [],
    lines: RESIDENTS.items.map((resident) => ({
      resident_id: resident.resident_id,
      display_name: resident.display_name,
      amount_paise: shares[resident.resident_id],
      is_participant: participants.includes(resident.resident_id),
    })),
  }
}

let stub: { calls: Recorded[]; restore: () => void } | null = null

afterEach(() => {
  stub?.restore()
  stub = null
})

function setup(saved: (body: unknown) => void = () => {}) {
  stub = installFetchStub({
    routes: {
      '/api/residents': RESIDENTS,
      '/api/expense-definitions': DEFINITIONS,
      '/api/statements': NO_STATEMENTS,
      'POST /api/expenses/allocation-preview': (body) => equalPreview(body),
      'POST /api/expenses': (body) => {
        saved(body)
        return { expense_id: 'expense_1', ...(body as object) }
      },
    },
  })
  return stub
}

describe('ExpenseFormDialog', () => {
  it('shows the calculated breakdown before anything is saved', async () => {
    setup()
    render(
      <ExpenseFormDialog
        defaultSettlementPeriod="2026-09"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    await screen.findByLabelText(/Description/)
    await userEvent.type(screen.getByLabelText(/Description/), 'Shared groceries')
    await userEvent.type(screen.getByLabelText(/^Amount/), '1000.01')

    const breakdown = await screen.findByText('Allocation breakdown')
    expect(breakdown).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Balances exactly')).toBeInTheDocument())
    // 100001 paise over three residents: 33334, 33334, 33333 — the two
    // remainder paise go to the first non-payers in resident-ID order.
    const table = screen.getByRole('table', { name: 'Calculated resident shares' })
    expect(within(table).getAllByText('₹333.34')).toHaveLength(2)
    expect(within(table).getByText('₹333.33')).toBeInTheDocument()
    // Nothing has been posted to /api/expenses yet.
    expect(stub!.calls.filter((call) => call.path === '/api/expenses' && call.method === 'POST'))
      .toHaveLength(0)
  })

  it('sends the amount as integer paise, never a float', async () => {
    let posted: unknown = null
    setup((body) => {
      posted = body
    })
    render(
      <ExpenseFormDialog
        defaultSettlementPeriod="2026-09"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    await screen.findByLabelText(/Description/)
    await userEvent.type(screen.getByLabelText(/Description/), 'Shared groceries')
    await userEvent.type(screen.getByLabelText(/^Amount/), '1250.75')
    await waitFor(() => expect(screen.getByText('Balances exactly')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Record expense' }))

    await waitFor(() => expect(posted).not.toBeNull())
    const body = posted as { amount_paise: number }
    expect(body.amount_paise).toBe(125075)
    expect(Number.isInteger(body.amount_paise)).toBe(true)
  })

  it('will not submit while custom shares do not total the amount', async () => {
    let posted: unknown = null
    setup((body) => {
      posted = body
    })
    render(
      <ExpenseFormDialog
        defaultSettlementPeriod="2026-09"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    await screen.findByLabelText(/Description/)
    await userEvent.type(screen.getByLabelText(/Description/), 'Repairs')
    await userEvent.type(screen.getByLabelText(/^Amount/), '100.00')
    await userEvent.selectOptions(screen.getByLabelText(/Allocation method/), 'custom')

    const alice = screen.getByLabelText('Alice')
    await userEvent.type(alice, '40')
    const bob = screen.getByLabelText('Bob')
    await userEvent.type(bob, '50')

    expect(await screen.findByText(/The shares do not add up yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Record expense' }))
    expect(posted).toBeNull()
  })

  it('gives a non-participant an explicit zero share rather than leaving them out', async () => {
    setup()
    render(
      <ExpenseFormDialog
        defaultSettlementPeriod="2026-09"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    await screen.findByLabelText(/Description/)
    await userEvent.type(screen.getByLabelText(/Description/), 'Just the two of us')
    await userEvent.type(screen.getByLabelText(/^Amount/), '100.00')
    await userEvent.click(screen.getByRole('checkbox', { name: /Chris/ }))

    await waitFor(() => expect(screen.getByText('Balances exactly')).toBeInTheDocument())
    const table = screen.getByRole('table', { name: 'Calculated resident shares' })
    const chrisRow = within(table).getByRole('row', { name: /Chris/ })
    expect(chrisRow).toHaveTextContent('No — zero share')
    expect(chrisRow).toHaveTextContent('₹0.00')
  })

  it('explains that funding an expense is separate from owing a share of it', async () => {
    setup()
    render(
      <ExpenseFormDialog
        defaultSettlementPeriod="2026-09"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    await screen.findByLabelText(/Paid by/)
    await userEvent.type(screen.getByLabelText(/^Amount/), '300.00')
    await userEvent.selectOptions(screen.getByLabelText(/Paid by/), 'alice')
    expect(
      await screen.findByText(/still charged only their own share/),
    ).toBeInTheDocument()
  })

  it('surfaces a server validation error on the form', async () => {
    stub = installFetchStub({
      routes: {
        '/api/residents': RESIDENTS,
        '/api/expense-definitions': DEFINITIONS,
        '/api/statements': NO_STATEMENTS,
        'POST /api/expenses/allocation-preview': (body) => equalPreview(body),
      },
      errors: {
        'POST /api/expenses': {
          status: 422,
          code: 'allocation_total_mismatch',
          message: 'Resident allocations must sum exactly to the expense amount.',
        },
      },
    })
    render(
      <ExpenseFormDialog
        defaultSettlementPeriod="2026-09"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    await screen.findByLabelText(/Description/)
    await userEvent.type(screen.getByLabelText(/Description/), 'Groceries')
    await userEvent.type(screen.getByLabelText(/^Amount/), '100.00')
    await waitFor(() => expect(screen.getByText('Balances exactly')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Record expense' }))
    expect(
      await screen.findByText(/must sum exactly to the expense amount/),
    ).toBeInTheDocument()
  })
})
