/**
 * The broadband screen shows what the backend worked out -- each flatmate's
 * equal share of the plan's fixed rate, what has been paid into the kitty and
 * out to the ISP -- sends exactly what the operator chose, and deletes a
 * payment or payout recorded by mistake only while the month is open.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Broadband } from './Broadband'
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
    total_paise: 39300,
    paid_paise: 0,
    settled_paise: 0,
    outstanding_paise: 39300,
    status: 'unpaid',
    paid_date: null,
    payments: [],
    ...overrides,
  }
}

const bill = (overrides: Record<string, unknown> = {}) => ({
  settlement_month: MONTH,
  total_paise: 117900,
  participant_count: 3,
  base_share_paise: 39300,
  remainder_paise: 0,
  shares: [share('alice', 'Alice'), share('bob', 'Bob'), share('chris', 'Chris')],
  blocked_reason: null,
  due_date: '2026-11-05',
  expense: {
    expense_id: 'broadband-1',
    description: 'Broadband & WiFi for November 2026',
    amount_paise: 117900,
    statement_id: null,
  },
  statement_status: 'draft',
  bill_state: 'live',
  transactions_open: true,
  collected_paise: 0,
  collected_count: 0,
  payout_status: 'pending',
  disbursed_paise: 0,
  remaining_disbursement_paise: 117900,
  disbursements: [],
  reimbursement: null,
  reimbursements: [],
  ...overrides,
})

const config = {
  provider_name: 'Airtel Xstream Fiber',
  account_number: 'AX-2048-7731',
  monthly_rate_paise: 117900,
  speed_mbps: 200,
  billing_day_of_month: 5,
  expense_definition_id: 'broadband',
  wifi_ssid: 'Capetown-1805',
  notes: 'Router on the living room shelf. Disney+ Hotstar bundled.',
  updated_at: '2026-11-01T10:00:00+05:30',
}

const overview = (overrides: Record<string, unknown> = {}) => ({
  service: {
    service_id: 'svc-b',
    service_type: 'broadband',
    display_name: 'Broadband & WiFi',
    provider_name: 'Airtel Xstream Fiber',
    status: 'active',
    billing_day_of_month: 5,
    period_rule: 'current_month',
    created_at: '2026-11-01T10:00:00+05:30',
    updated_at: '2026-11-01T10:00:00+05:30',
  },
  config,
  month: bill(),
  next_billing_date: '2026-12-05',
  ...overrides,
})

const notSetUp = { service: null, config: null, month: null, next_billing_date: null }

/** Alice has paid her share into the kitty; Bob paid Airtel ₹786.00, his share and ₹393.00 beyond it. */
const paidMonth = (overrides: Record<string, unknown> = {}) =>
  bill({
    collected_paise: 78600,
    collected_count: 2,
    payout_status: 'partially_disbursed',
    disbursed_paise: 78600,
    remaining_disbursement_paise: 39300,
    shares: [
      share('alice', 'Alice', {
        paid_paise: 39300,
        settled_paise: 39300,
        outstanding_paise: 0,
        status: 'paid',
        payments: [
          {
            payment_id: 'pay-alice',
            payment_date: '2026-11-04',
            amount_paise: 39300,
            payment_mode: 'upi',
            reference: 'UPI-55',
          },
        ],
      }),
      share('bob', 'Bob', { paid_paise: 39300, settled_paise: 39300, outstanding_paise: 0, status: 'paid' }),
      share('chris', 'Chris'),
    ],
    disbursements: [
      {
        disbursement_id: 'svcdisb-share',
        amount_paise: 39300,
        disbursed_at: '2026-11-05',
        disbursement_reference: 'UTR-9',
        disbursed_by: 'pool',
        funding_resident_id: null,
        funding_resident_name: null,
        funding_payment_id: null,
        note: "Bob's allotted share via House Kitty",
        created_at: '2026-11-05T10:00:00+05:30',
      },
      {
        disbursement_id: 'svcdisb-extra',
        amount_paise: 39300,
        disbursed_at: '2026-11-05',
        disbursement_reference: 'UTR-9',
        disbursed_by: 'resident_advance',
        funding_resident_id: 'bob',
        funding_resident_name: 'Bob',
        funding_payment_id: 'pay-bob-extra',
        note: "Extra above Bob's share (reimbursable via House Kitty)",
        created_at: '2026-11-05T10:00:00+05:30',
      },
    ],
    ...overrides,
  })

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname + location.search}</output>
}

function show(payload: Record<string, unknown>, routes: Record<string, unknown> = {}, path = '/') {
  stub = installFetchStub({
    routes: {
      '/api/services/broadband': payload,
      '/api/residents': RESIDENTS,
      'POST /api/services/broadband/setup': overview(),
      'POST /api/services/broadband/record-payment': overview(),
      'POST /api/services/broadband/disburse-payout': overview(),
      'POST /api/services/broadband/reimburse-from-kitty': overview(),
      ...routes,
    } as never,
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Broadband month={MONTH} />
        <Where />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path.split('?')[0] === path)

describe('Broadband', () => {
  it('opens on the monthly settlement and moves between tabs through the address', async () => {
    show(overview())
    const tabs = await screen.findByRole('tablist', { name: 'Broadband & WiFi' })
    expect(within(tabs).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Monthly Settlement',
      'Plan & WiFi',
      'Connection & Settings',
    ])
    expect(within(tabs).getByRole('tab', { name: 'Monthly Settlement' })).toHaveAttribute('aria-selected', 'true')
    const headings = within(screen.getByRole('tabpanel')).getAllByRole('heading').map((h) => h.textContent)
    expect(headings).toEqual(['Resident Collection', 'ISP Payout'])

    await userEvent.click(within(tabs).getByRole('tab', { name: 'Plan & WiFi' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=plan')
    const isp = screen.getByLabelText('ISP connection')
    expect(isp).toHaveTextContent('ProviderAirtel Xstream Fiber')
    expect(isp).toHaveTextContent('Account NumberAX-2048-7731')
    expect(isp).toHaveTextContent('Speed200 Mbps')
    expect(isp).toHaveTextContent('Due DateDay 5 of each month · next 5 Dec 2026')
    expect(isp).toHaveTextContent('Monthly Rate₹1,179.00 · ₹393.00 each')
    const wifi = screen.getByLabelText('WiFi network')
    expect(wifi).toHaveTextContent('Network Name (SSID)Capetown-1805')
    expect(wifi).toHaveTextContent('Router & NotesRouter on the living room shelf. Disney+ Hotstar bundled.')
    expect(screen.getByText('The WiFi password is not stored here. Share it with flatmates in person.')).toBeInTheDocument()

    await userEvent.click(within(tabs).getByRole('tab', { name: 'Connection & Settings' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=config')
    expect(screen.getByRole('form', { name: 'Broadband plan' })).toBeInTheDocument()
  })

  it('sums up the month: the bill, what the kitty holds, and the payout', async () => {
    show(overview({ month: paidMonth() }))
    const summary = await screen.findByRole('region', { name: 'Month summary' })
    expect(summary).toHaveTextContent('Total Bill₹1,179.00₹393.00 each · due 5 Nov 2026')
    expect(summary).toHaveTextContent('Paid into Kitty₹786.002 of 3 flatmates paid')
    expect(summary).toHaveTextContent('Payout StatusPart paid₹393.00 still owed to Airtel Xstream Fiber')
  })

  it('says a month ahead is not billed yet', async () => {
    show(overview({ month: bill({ expense: null, bill_state: 'upcoming' }) }))
    const summary = await screen.findByRole('region', { name: 'Month summary' })
    expect(summary).toHaveTextContent('Paid into Kitty—Not billed yet')
    expect(summary).toHaveTextContent('Payout Status—')
    const table = screen.getByRole('table', { name: 'Broadband shares for November 2026' })
    expect(within(table).getByRole('button', { name: 'Pay now for Alice' })).toBeDisabled()
  })

  it("lists each flatmate's equal share and what has been collected", async () => {
    show(overview({ month: paidMonth() }))
    const table = await screen.findByRole('table', { name: 'Broadband shares for November 2026' })
    expect(within(table).getByRole('row', { name: /Alice/ })).toHaveTextContent('Alice₹393.00₹393.00₹0.00Paid')
    expect(within(table).getByRole('row', { name: /Chris/ })).toHaveTextContent('Chris₹393.00₹0.00₹393.00Unpaid')
    expect(within(table).getByRole('button', { name: 'Pay now for Alice' })).toBeDisabled()
    expect(within(table).getByRole('button', { name: 'Pay now for Chris' })).toBeEnabled()
  })

  it("records a flatmate's payment into the House Kitty", async () => {
    show(overview())
    const table = await screen.findByRole('table', { name: 'Broadband shares for November 2026' })
    await userEvent.click(within(table).getByRole('button', { name: 'Pay now for Bob' }))
    const dialog = screen.getByRole('dialog', { name: 'Record Payment into House Kitty' })
    expect(dialog).toHaveTextContent('Bob · November 2026 broadband share · ₹393.00 outstanding')
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('393.00')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UPI-55')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payment' }))
    await waitFor(() => expect(sent('POST', '/api/services/broadband/record-payment')).toBeDefined())
    expect(sent('POST', '/api/services/broadband/record-payment')!.body).toMatchObject({
      settlement_month: MONTH,
      resident_id: 'bob',
      amount_paise: 39300,
      payment_mode: 'upi',
      reference: 'UPI-55',
    })
    expect(await screen.findByText('Payment recorded')).toBeInTheDocument()
  })

  it('records a payout to the ISP, from the kitty or by a resident', async () => {
    show(overview({ month: bill({ collected_paise: 78600, collected_count: 2 }) }))
    const panel = (await screen.findByRole('heading', { name: 'ISP Payout' })).closest('section')!
    const row = within(
      within(panel).getByRole('table', { name: 'The broadband bill owed to Airtel Xstream Fiber' }),
    ).getByRole('row', { name: /Airtel Xstream Fiber/ })
    expect(row).toHaveTextContent('Airtel Xstream FiberAccount AX-2048-7731₹1,179.00₹0.00₹1,179.00')
    await userEvent.click(within(row).getByRole('button', { name: /Pay now/ }))
    const dialog = screen.getByRole('dialog', { name: 'Record ISP Payout' })
    expect(dialog).toHaveTextContent('November 2026 · ₹1,179.00 still owed to Airtel Xstream Fiber')
    // A first payout offers what the kitty has collected.
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('786.00')
    await userEvent.click(within(dialog).getByRole('radio', { name: /Paid by a resident/ }))
    expect(dialog).toHaveTextContent('A resident paid the ISP from their own account.')
    await userEvent.selectOptions(within(dialog).getByRole('combobox', { name: /^Paid by/ }), 'bob')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UTR-9')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payout' }))
    await waitFor(() => expect(sent('POST', '/api/services/broadband/disburse-payout')).toBeDefined())
    expect(sent('POST', '/api/services/broadband/disburse-payout')!.body).toMatchObject({
      settlement_month: MONTH,
      disbursement_mode: 'resident_advance',
      funding_resident_id: 'bob',
      reference: 'UTR-9',
      amount_paise: 78600,
    })
    expect(await screen.findByText('ISP payout recorded')).toBeInTheDocument()
  })

  it('pays back from the kitty a resident who paid the ISP beyond their share', async () => {
    const owed = {
      recipient_resident_id: 'bob',
      recipient_name: 'Bob',
      total_fronted_paise: 78600,
      payer_share_paise: 39300,
      net_reimbursement_owed_paise: 39300,
      reimbursed_so_far_paise: 0,
      pending_reimbursement_paise: 39300,
      kitty_cash_available_paise: 39300,
      reimbursable_paise: 39300,
      can_reimburse: true,
      reimbursements: [],
    }
    show(overview({ month: paidMonth({ reimbursements: [owed], reimbursement: owed }) }))
    const panel = (await screen.findByRole('heading', { name: 'Reimbursement' })).closest('section')!
    await userEvent.click(within(panel).getByRole('button', { name: /Reimburse/ }))
    const dialog = screen.getByRole('dialog', { name: 'Reimburse Bob' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record reimbursement' }))
    await waitFor(() => expect(sent('POST', '/api/services/broadband/reimburse-from-kitty')).toBeDefined())
    const body = sent('POST', '/api/services/broadband/reimburse-from-kitty')!.body as Record<string, unknown>
    expect(body).toMatchObject({ settlement_month: MONTH, recipient_resident_id: 'bob', amount_paise: 39300 })
    expect(body).not.toHaveProperty('note')
  })

  describe('deleting what was recorded by mistake', () => {
    const deleted = () => stub!.calls.find((call) => call.method === 'DELETE')

    it("deletes a flatmate's payment from their statement while the month is open", async () => {
      show(overview({ month: paidMonth() }), {
        'DELETE /api/services/broadband/payments/pay-alice': overview(),
      })
      const table = await screen.findByRole('table', { name: 'Broadband shares for November 2026' })
      await userEvent.click(within(table).getByRole('row', { name: /Alice/ }))
      const statement = await screen.findByRole('dialog', { name: 'Broadband Statement' })
      expect(statement).toHaveTextContent('Alice · November 2026')
      expect(within(statement).getByRole('table', { name: "Payments towards Alice's broadband share" })).toHaveTextContent(
        '4 Nov 2026₹393.00UPIUPI-55',
      )
      await userEvent.click(within(statement).getByRole('button', { name: 'Delete the ₹393.00 payment of 4 Nov 2026' }))
      const confirm = screen.getByRole('dialog', { name: 'Delete this payment?' })
      expect(confirm).toHaveTextContent("Alice's ₹393.00 payment into the House Kitty of 4 Nov 2026.")
      await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
      await waitFor(() => expect(deleted()!.path).toBe('/api/services/broadband/payments/pay-alice'))
      expect(await screen.findByText('Payment deleted')).toBeInTheDocument()
    })

    it("deletes a resident's payout to the ISP from its statement", async () => {
      show(overview({ month: paidMonth() }), {
        'DELETE /api/services/broadband/disbursements/svcdisb-extra': overview(),
      })
      const panel = (await screen.findByRole('heading', { name: 'ISP Payout' })).closest('section')!
      await userEvent.click(within(panel).getByRole('row', { name: /Airtel Xstream Fiber/ }))
      const statement = await screen.findByRole('dialog', { name: 'ISP Payout Statement' })
      const buttons = within(statement).getAllByRole('button', { name: 'Delete the ₹393.00 payout of 5 Nov 2026' })
      await userEvent.click(buttons[1])
      const confirm = screen.getByRole('dialog', { name: 'Delete this payout?' })
      expect(confirm).toHaveTextContent(
        "Bob's ₹393.00 payout to Airtel Xstream Fiber of 5 Nov 2026, with their share paid through the House Kitty.",
      )
      await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
      await waitFor(() => expect(deleted()!.path).toBe('/api/services/broadband/disbursements/svcdisb-extra'))
      expect(await screen.findByText('Payout deleted')).toBeInTheDocument()
    })

    it('offers nothing to delete once the month is closed', async () => {
      show(overview({ month: paidMonth({ transactions_open: false, statement_status: 'finalized' }) }))
      const table = await screen.findByRole('table', { name: 'Broadband shares for November 2026' })
      await userEvent.click(within(table).getByRole('row', { name: /Alice/ }))
      const statement = await screen.findByRole('dialog', { name: 'Broadband Statement' })
      expect(within(statement).queryByRole('button', { name: /^Delete/ })).toBeNull()
    })
  })

  it('changes the plan from its settings', async () => {
    show(overview(), {}, '/?tab=config')
    const form = await screen.findByRole('form', { name: 'Broadband plan' })
    expect(within(form).getByLabelText(/^Provider name/)).toHaveValue('Airtel Xstream Fiber')
    expect(within(form).getByLabelText(/^Monthly rate/)).toHaveValue('1179.00')
    expect(within(form).getByLabelText(/^Speed \(Mbps\)/)).toHaveValue(200)
    expect(within(form).getByLabelText(/^Due day/)).toHaveValue(5)
    expect(within(form).getByLabelText(/^Wi-Fi SSID/)).toHaveValue('Capetown-1805')

    const speed = within(form).getByLabelText(/^Speed \(Mbps\)/)
    await userEvent.clear(speed)
    await userEvent.type(speed, '0')
    expect(within(form).getByText('Enter a speed in whole Mbps.')).toBeInTheDocument()
    const day = within(form).getByLabelText(/^Due day/)
    await userEvent.clear(day)
    await userEvent.type(day, '40')
    expect(within(form).getByText('Enter a day from 1 to 31.')).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Save plan' })).toBeDisabled()

    await userEvent.clear(speed)
    await userEvent.type(speed, '300')
    await userEvent.clear(day)
    await userEvent.type(day, '7')
    const rate = within(form).getByLabelText(/^Monthly rate/)
    await userEvent.clear(rate)
    await userEvent.type(rate, '1415.82')
    await userEvent.clear(within(form).getByLabelText(/^Wi-Fi SSID/))
    await userEvent.click(within(form).getByRole('button', { name: 'Save plan' }))
    await waitFor(() => expect(sent('POST', '/api/services/broadband/setup')).toBeDefined())
    expect(sent('POST', '/api/services/broadband/setup')!.body).toEqual({
      provider_name: 'Airtel Xstream Fiber',
      account_number: 'AX-2048-7731',
      monthly_rate_paise: 141582,
      speed_mbps: 300,
      billing_day_of_month: 7,
      wifi_ssid: null,
      notes: 'Router on the living room shelf. Disney+ Hotstar bundled.',
    })
    expect(await screen.findByText('Plan saved')).toBeInTheDocument()
  })

  it('asks for the plan before anything else', async () => {
    show(notSetUp)
    expect(await screen.findByRole('heading', { name: 'Set up broadband' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).toBeNull()
    const form = screen.getByRole('form', { name: 'Broadband plan' })
    await userEvent.type(within(form).getByLabelText(/^Monthly rate/), '1179')
    await userEvent.click(within(form).getByRole('button', { name: 'Set up broadband' }))
    await waitFor(() => expect(sent('POST', '/api/services/broadband/setup')).toBeDefined())
    expect(sent('POST', '/api/services/broadband/setup')!.body).toEqual({
      provider_name: 'Airtel Xstream Fiber',
      account_number: null,
      monthly_rate_paise: 117900,
      speed_mbps: null,
      billing_day_of_month: null,
      wifi_ssid: null,
      notes: null,
    })
  })
})
