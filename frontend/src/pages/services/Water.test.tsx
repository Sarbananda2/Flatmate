/**
 * The water supply screen shows what the backend worked out -- each flatmate's
 * equal share of the fixed rate, what has been paid into the kitty and out to
 * the society -- and sends exactly what the operator chose.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Water } from './Water'
import { ToastProvider } from '../../components/Toast'
import { RESIDENTS, installFetchStub, type Recorded } from '../../test/server'

const MONTH = '2026-11'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function share(residentId: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    resident_id: residentId,
    display_name: name,
    total_paise: 20000,
    paid_paise: 0,
    settled_paise: 0,
    outstanding_paise: 20000,
    status: 'unpaid',
    paid_date: null,
    payments: [],
    ...overrides,
  }
}

const bill = (overrides: Record<string, unknown> = {}) => ({
  settlement_month: MONTH,
  total_paise: 60000,
  participant_count: 3,
  base_share_paise: 20000,
  remainder_paise: 0,
  shares: [share('alice', 'Alice'), share('bob', 'Bob'), share('chris', 'Chris')],
  blocked_reason: null,
  due_date: '2026-11-10',
  expense: { expense_id: 'water-1', description: 'Water Supply for November 2026', amount_paise: 60000, statement_id: null },
  statement_status: 'draft',
  bill_state: 'live',
  transactions_open: true,
  collected_paise: 0,
  collected_count: 0,
  payout_status: 'pending',
  disbursed_paise: 0,
  remaining_disbursement_paise: 60000,
  disbursements: [],
  reimbursement: null,
  reimbursements: [],
  ...overrides,
})

const config = {
  provider_name: 'Society Water Supply',
  monthly_rate_paise: 60000,
  consumer_id: 'SOC-402',
  billing_day_of_month: 10,
  expense_definition_id: 'water',
  pipeline_notes: 'Overhead tank, filtered at the inlet',
  notes: null,
  updated_at: '2026-11-01T10:00:00+05:30',
}

const overview = (overrides: Record<string, unknown> = {}) => ({
  service: {
    service_id: 'svc-w',
    service_type: 'water_tanker',
    display_name: 'Water Supply',
    provider_name: 'Society Water Supply',
    status: 'active',
    billing_day_of_month: 10,
    period_rule: 'current_month',
    created_at: '2026-11-01T10:00:00+05:30',
    updated_at: '2026-11-01T10:00:00+05:30',
  },
  config,
  month: bill(),
  next_billing_date: '2026-11-10',
  ...overrides,
})

const notSetUp = { service: null, config: null, month: null, next_billing_date: null }

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname + location.search}</output>
}

function show(payload: Record<string, unknown>, routes: Record<string, unknown> = {}, path = '/') {
  stub = installFetchStub({
    routes: {
      '/api/services/water': payload,
      '/api/residents': RESIDENTS,
      'POST /api/services/water/setup': overview(),
      'POST /api/services/water/record-payment': overview(),
      'POST /api/services/water/disburse-payout': overview(),
      'POST /api/services/water/reimburse-from-kitty': overview(),
      ...routes,
    } as never,
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Water month={MONTH} />
        <Where />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path.split('?')[0] === path)

describe('Water', () => {
  it('opens on the monthly settlement and moves between tabs through the address', async () => {
    show(overview())
    const tabs = await screen.findByRole('tablist', { name: 'Water Supply' })
    expect(within(tabs).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Monthly Settlement',
      'Supply & Pipeline',
      'Connection & Settings',
    ])
    expect(within(tabs).getByRole('tab', { name: 'Monthly Settlement' })).toHaveAttribute('aria-selected', 'true')
    // Straight to the residents' collection, with nothing above it.
    expect(within(screen.getByRole('tabpanel')).getAllByRole('heading')[0]).toHaveTextContent('Resident Collection')

    await userEvent.click(within(tabs).getByRole('tab', { name: 'Supply & Pipeline' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=pipeline')
    const supply = screen.getByLabelText('Supply and pipeline')
    expect(supply).toHaveTextContent('Connection SourceSociety Main Pipeline Connection')
    expect(supply).toHaveTextContent('DistributionFiltration system line + Bathrooms/utilities line')
    expect(supply).toHaveTextContent('Tariff ModelFixed constant monthly contract (consumption-independent)')
    expect(supply).toHaveTextContent('Split Rule100% Equal share among all active flatmates')
    expect(supply).toHaveTextContent('This Month₹600.00 · ₹200.00 each')
    expect(supply).toHaveTextContent('Pipeline NotesOverhead tank, filtered at the inlet')

    await userEvent.click(within(tabs).getByRole('tab', { name: 'Connection & Settings' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=config')
    expect(screen.getByRole('form', { name: 'Water contract' })).toBeInTheDocument()
  })

  it("lists each flatmate's equal share and what has been collected", async () => {
    show(
      overview({
        month: bill({
          collected_paise: 20000,
          collected_count: 1,
          shares: [
            share('alice', 'Alice', { paid_paise: 20000, settled_paise: 20000, outstanding_paise: 0, status: 'paid' }),
            share('bob', 'Bob'),
            share('chris', 'Chris'),
          ],
        }),
      }),
    )
    const table = await screen.findByRole('table', { name: 'Water shares for November 2026' })
    expect(within(table).getByRole('row', { name: /Alice/ })).toHaveTextContent('Alice₹200.00₹200.00₹0.00Paid')
    expect(within(table).getByRole('row', { name: /Bob/ })).toHaveTextContent('Bob₹200.00₹0.00₹200.00Unpaid')
    expect(within(table).getByRole('button', { name: 'Pay now for Alice' })).toBeDisabled()
    expect(within(table).getByRole('button', { name: 'Pay now for Bob' })).toBeEnabled()
  })

  it("records a flatmate's payment into the House Kitty", async () => {
    show(overview())
    const table = await screen.findByRole('table', { name: 'Water shares for November 2026' })
    await userEvent.click(within(table).getByRole('button', { name: 'Pay now for Bob' }))
    const dialog = screen.getByRole('dialog', { name: 'Record Payment into House Kitty' })
    expect(dialog).toHaveTextContent('Bob · November 2026 water share · ₹200.00 outstanding')
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('200.00')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UPI-55')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payment' }))
    await waitFor(() => expect(sent('POST', '/api/services/water/record-payment')).toBeDefined())
    expect(sent('POST', '/api/services/water/record-payment')!.body).toMatchObject({
      settlement_month: MONTH,
      resident_id: 'bob',
      amount_paise: 20000,
      payment_mode: 'upi',
      reference: 'UPI-55',
    })
    expect(await screen.findByText('Payment recorded')).toBeInTheDocument()
  })

  it('records a payout to the society, from the kitty or by a resident', async () => {
    show(overview({ month: bill({ collected_paise: 40000, collected_count: 2 }) }))
    const panel = (await screen.findByRole('heading', { name: 'Society Payout' })).closest('section')!
    const row = within(within(panel).getByRole('table', { name: 'The water bill owed to Society Water Supply' })).getByRole(
      'row',
      { name: /Society Water Supply/ },
    )
    expect(row).toHaveTextContent('Society Water Supply₹600.00₹0.00₹600.00')
    await userEvent.click(within(row).getByRole('button', { name: /Pay now/ }))
    const dialog = screen.getByRole('dialog', { name: 'Record Society Payout' })
    expect(dialog).toHaveTextContent('November 2026 · ₹600.00 still owed to Society Water Supply')
    // A first payout offers what the kitty has collected.
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('400.00')
    await userEvent.click(within(dialog).getByRole('radio', { name: /Paid by a resident/ }))
    expect(dialog).toHaveTextContent('A resident paid the society from their own account.')
    await userEvent.selectOptions(within(dialog).getByRole('combobox', { name: /^Paid by/ }), 'bob')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UTR-9')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payout' }))
    await waitFor(() => expect(sent('POST', '/api/services/water/disburse-payout')).toBeDefined())
    expect(sent('POST', '/api/services/water/disburse-payout')!.body).toMatchObject({
      settlement_month: MONTH,
      disbursement_mode: 'resident_advance',
      funding_resident_id: 'bob',
      reference: 'UTR-9',
      amount_paise: 40000,
    })
    expect(await screen.findByText('Society payout recorded')).toBeInTheDocument()
  })

  it('pays back from the kitty a resident who paid the society beyond their share', async () => {
    const owed = {
      recipient_resident_id: 'bob',
      recipient_name: 'Bob',
      total_fronted_paise: 60000,
      payer_share_paise: 20000,
      net_reimbursement_owed_paise: 40000,
      reimbursed_so_far_paise: 0,
      pending_reimbursement_paise: 40000,
      kitty_cash_available_paise: 40000,
      reimbursable_paise: 40000,
      can_reimburse: true,
      reimbursements: [],
    }
    show(overview({ month: bill({ reimbursements: [owed], reimbursement: owed }) }))
    const panel = (await screen.findByRole('heading', { name: 'Reimbursement' })).closest('section')!
    await userEvent.click(within(panel).getByRole('button', { name: /Reimburse/ }))
    const dialog = screen.getByRole('dialog', { name: 'Reimburse Bob' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record reimbursement' }))
    await waitFor(() => expect(sent('POST', '/api/services/water/reimburse-from-kitty')).toBeDefined())
    const body = sent('POST', '/api/services/water/reimburse-from-kitty')!.body as Record<string, unknown>
    expect(body).toMatchObject({ settlement_month: MONTH, recipient_resident_id: 'bob', amount_paise: 40000 })
    expect(body).not.toHaveProperty('note')
  })

  it('changes the contract from its settings', async () => {
    show(overview(), {}, '/?tab=config')
    const form = await screen.findByRole('form', { name: 'Water contract' })
    expect(within(form).getByLabelText(/^Provider/)).toHaveValue('Society Water Supply')
    expect(within(form).getByLabelText(/^Fixed monthly rate/)).toHaveValue('600.00')
    expect(within(form).getByLabelText(/^Billing due day/)).toHaveValue(10)
    const rate = within(form).getByLabelText(/^Fixed monthly rate/)
    await userEvent.clear(rate)
    await userEvent.type(rate, '750')
    const day = within(form).getByLabelText(/^Billing due day/)
    await userEvent.clear(day)
    await userEvent.type(day, '40')
    expect(within(form).getByText('Enter a day from 1 to 31.')).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Save contract' })).toBeDisabled()
    await userEvent.clear(day)
    await userEvent.type(day, '12')
    await userEvent.click(within(form).getByRole('button', { name: 'Save contract' }))
    await waitFor(() => expect(sent('POST', '/api/services/water/setup')).toBeDefined())
    expect(sent('POST', '/api/services/water/setup')!.body).toEqual({
      provider_name: 'Society Water Supply',
      monthly_rate_paise: 75000,
      billing_day_of_month: 12,
      consumer_id: 'SOC-402',
      pipeline_notes: 'Overhead tank, filtered at the inlet',
      notes: null,
    })
    expect(await screen.findByText('Contract saved')).toBeInTheDocument()
  })

  it('asks for the contract before anything else', async () => {
    show(notSetUp)
    expect(await screen.findByRole('heading', { name: 'Set up the water supply' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).toBeNull()
    const form = screen.getByRole('form', { name: 'Water contract' })
    await userEvent.type(within(form).getByLabelText(/^Fixed monthly rate/), '600')
    await userEvent.click(within(form).getByRole('button', { name: 'Set up water supply' }))
    await waitFor(() => expect(sent('POST', '/api/services/water/setup')).toBeDefined())
    expect(sent('POST', '/api/services/water/setup')!.body).toMatchObject({
      provider_name: 'Society Water Supply',
      monthly_rate_paise: 60000,
      billing_day_of_month: null,
    })
  })
})
