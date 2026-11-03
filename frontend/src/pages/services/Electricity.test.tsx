/**
 * The electricity screen logs the prepaid meters' recharges, adds up what was
 * recharged and the AC, and shows the split the backend works out from them:
 * each flatmate's AC and an equal share of the rest. It sends every recharge,
 * the AC and every payment in integer paise, exactly as the operator entered them.
 *
 * The household's own month: Sarbananda recharges UPPCL ₹2,000.00 and the DG
 * ₹500.00, Arnab UPPCL ₹1,500.00 -- ₹4,000.00 in all; Sarbananda's AC ₹971.05,
 * Arnab's ₹895.48, Anagarag's none. The spare paisa goes in resident order.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Electricity } from './Electricity'
import { ToastProvider } from '../../components/Toast'
import { RESIDENTS, installFetchStub, type Recorded } from '../../test/server'

const MONTH = '2026-11'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const PEOPLE = [
  { resident_id: 'anagarag', display_name: 'Anagarag' },
  { resident_id: 'arnab', display_name: 'Arnab' },
  { resident_id: 'sarbananda', display_name: 'Sarbananda' },
]

function share(residentId: string, name: string, common: number, ac: number, overrides: Record<string, unknown> = {}) {
  return {
    resident_id: residentId,
    display_name: name,
    ac_paise: ac,
    common_share_paise: common,
    total_paise: common + ac,
    paid_paise: 0,
    settled_paise: 0,
    outstanding_paise: common + ac,
    status: 'unpaid',
    paid_date: null,
    payments: [],
    net_balance_paise: -(common + ac),
    ...overrides,
  }
}

function topUp(id: string, key: 'uppcl' | 'dg', paise: number, on: string, by: [string, string] | null, reference: string) {
  return {
    top_up_id: id,
    provider_key: key,
    provider_name: key === 'uppcl' ? 'UPPCL (Grid)' : 'Diesel Generator (DG)',
    amount_paise: paise,
    recharged_on: on,
    reference,
    paid_by: by ? 'flatmate' : 'kitty',
    resident_id: by?.[0] ?? null,
    resident_name: by?.[1] ?? null,
    note: null,
  }
}

const reimbursement = {
  recipient_resident_id: 'sarbananda',
  recipient_name: 'Sarbananda',
  total_fronted_paise: 250000,
  payer_share_paise: 168220,
  net_reimbursement_owed_paise: 81780,
  reimbursed_so_far_paise: 0,
  pending_reimbursement_paise: 81780,
  kitty_cash_available_paise: 0,
  reimbursable_paise: 0,
  can_reimburse: false,
  reimbursements: [],
}

/** A payment into the kitty; `top_up_id` when it was a share paid with a recharge. */
const paidIn = (paymentId: string, date: string, paise: number, extra: Record<string, unknown> = {}) => ({
  payment_id: paymentId,
  payment_date: date,
  amount_paise: paise,
  payment_mode: null,
  reference: null,
  note: 'Electricity for November 2026',
  top_up_id: null,
  ...extra,
})

/** November recharged and its AC set. */
const recorded = (overrides: Record<string, unknown> = {}) => ({
  settlement_month: MONTH,
  service_period: '2026-10',
  participants: PEOPLE,
  components: [
    { id: 'uppcl', name: 'UPPCL (Grid)', amount_paise: 350000 },
    { id: 'dg', name: 'Diesel Generator (DG)', amount_paise: 50000 },
  ],
  ac_allocations: { anagarag: 0, arnab: 89548, sarbananda: 97105 },
  total_paise: 400000,
  total_ac_paise: 186653,
  common_remainder_paise: 213347,
  common_share_paise: 71115,
  shares: [
    share('anagarag', 'Anagarag', 71116, 0),
    share('arnab', 'Arnab', 71116, 89548, {
      settled_paise: 150000,
      paid_paise: 150000,
      outstanding_paise: 10664,
      status: 'partial',
      net_balance_paise: -10664,
    }),
    share('sarbananda', 'Sarbananda', 71115, 97105, {
      settled_paise: 168220,
      paid_paise: 168220,
      outstanding_paise: 0,
      status: 'paid',
      net_balance_paise: 81780,
    }),
  ],
  payouts: [],
  top_ups: [
    topUp('svcdisb_1', 'uppcl', 200000, '2026-11-03', ['sarbananda', 'Sarbananda'], 'UTR-1'),
    topUp('svcdisb_2', 'uppcl', 150000, '2026-11-12', ['arnab', 'Arnab'], 'UTR-2'),
    topUp('svcdisb_3', 'dg', 50000, '2026-11-20', ['sarbananda', 'Sarbananda'], 'UTR-3'),
  ],
  collected_paise: 318220,
  collected_count: 1,
  reimbursements: [reimbursement],
  expense: { expense_id: 'electricity-1', description: 'Electricity for November 2026', amount_paise: 400000, statement_id: null },
  foreign_expense: null,
  statement_status: 'draft',
  bill_state: 'live',
  can_record: true,
  transactions_open: true,
  ...overrides,
})

/** November with nothing recharged yet. */
const empty = recorded({
  components: [
    { id: 'uppcl', name: 'UPPCL (Grid)', amount_paise: 0 },
    { id: 'dg', name: 'Diesel Generator (DG)', amount_paise: 0 },
  ],
  ac_allocations: {},
  total_paise: 0,
  total_ac_paise: 0,
  common_remainder_paise: 0,
  common_share_paise: 0,
  shares: PEOPLE.map((person) =>
    share(person.resident_id, person.display_name, 0, 0, { status: null, outstanding_paise: 0, net_balance_paise: 0 }),
  ),
  top_ups: [],
  collected_paise: 0,
  collected_count: 0,
  reimbursements: [],
  expense: null,
  bill_state: 'not_billed',
})

/** Recharged ₹4,000.00, with no AC set yet. */
const recharged = recorded({
  ac_allocations: { anagarag: 0, arnab: 0, sarbananda: 0 },
  total_ac_paise: 0,
  common_remainder_paise: 400000,
  common_share_paise: 133333,
  shares: [share('anagarag', 'Anagarag', 133334, 0), share('arnab', 'Arnab', 133333, 0), share('sarbananda', 'Sarbananda', 133333, 0)],
})

const preview = {
  settlement_month: MONTH,
  components: recorded().components,
  total_paise: 400000,
  total_ac_paise: 186653,
  common_remainder_paise: 213347,
  common_share_paise: 71115,
  residents: [
    { resident_id: 'anagarag', display_name: 'Anagarag', common_share_paise: 71116, ac_paise: 0, responsibility_paise: 71116 },
    { resident_id: 'arnab', display_name: 'Arnab', common_share_paise: 71116, ac_paise: 89548, responsibility_paise: 160664 },
    { resident_id: 'sarbananda', display_name: 'Sarbananda', common_share_paise: 71115, ac_paise: 97105, responsibility_paise: 168220 },
  ],
}

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname + location.search}</output>
}

/** The page at `path`: `/?tab=allocations` opens on the recharges and AC. */
function show(payload: unknown, routes: Record<string, unknown> = {}, path = '/') {
  stub = installFetchStub({
    routes: {
      [`/api/electricity/month/${MONTH}`]: payload,
      '/api/residents': RESIDENTS,
      [`POST /api/electricity/month/${MONTH}/preview`]: preview,
      'POST /api/electricity/bills': recorded(),
      'POST /api/electricity/record-payment': recorded(),
      'POST /api/electricity/disburse-payout': recorded(),
      [`DELETE /api/electricity/month/${MONTH}/top-ups/svcdisb_3`]: recorded(),
      ...routes,
    } as never,
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Electricity month={MONTH} />
        <Where />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path.split('?')[0] === path)

const section = async (title: string) => (await screen.findByRole('heading', { name: title })).closest('section')!

/** A flatmate's share of what's left, and what they are responsible for in all, as their row shows them. */
const shares = (row: HTMLElement) =>
  within(row)
    .getAllByRole('cell')
    .slice(1)
    .map((cell) => cell.textContent)

const ALLOCATIONS = '/?tab=allocations'

describe('Electricity', () => {
  it('opens on the settlement, and moves to the recharges and AC through the address', async () => {
    show(recorded())
    expect(await screen.findByRole('tab', { name: 'Monthly Settlement' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tablist', { name: 'Electricity' })).toBeInTheDocument()
    // What each flatmate owes the kitty, and what the kitty owes back.
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
      'Resident Collection',
      'Reimbursement',
    ])
    expect(screen.getByLabelText('location')).toHaveTextContent(/^\/$/)

    await userEvent.click(screen.getByRole('tab', { name: 'Top-ups & AC Allocations' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=allocations')
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
      'Meter Top-ups / Recharges',
      'AC Allocations & Responsibility',
    ])
  })

  it('lists every recharge of the month, and what they come to', async () => {
    show(recorded(), {}, ALLOCATIONS)
    const panel = await section('Meter Top-ups / Recharges')
    const log = within(panel).getByRole('table', { name: 'Meter recharges for November 2026' })
    const rows = within(log).getAllByRole('row').slice(1)
    // Date, meter, who paid, amount: the reference is in each recharge's details.
    expect(rows.map((row) => row.textContent)).toEqual([
      '3 Nov 2026UPPCL (Grid)Sarbananda₹2,000.00',
      '12 Nov 2026UPPCL (Grid)Arnab₹1,500.00',
      '20 Nov 2026Diesel Generator (DG)Sarbananda₹500.00',
    ])
    expect(within(log).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Date',
      'Meter / Provider',
      'Paid By',
      'Amount',
      'Actions',
    ])
    const totals = within(panel).getByLabelText('Recharge subtotals')
    expect(totals).toHaveTextContent('UPPCL Recharges₹3,500.00')
    expect(totals).toHaveTextContent('DG Recharges₹500.00')
    expect(totals).toHaveTextContent('Total Prepaid Electricity₹4,000.00')
  })

  it('logs a recharge from its dialog, and shows it in the list', async () => {
    let logged = false
    show(
      empty,
      {
        [`/api/electricity/month/${MONTH}`]: () => (logged ? recorded({ top_ups: [recorded().top_ups[2]] }) : empty),
        'POST /api/electricity/disburse-payout': () => {
          logged = true
          return recorded()
        },
      },
      ALLOCATIONS,
    )
    const panel = await section('Meter Top-ups / Recharges')
    expect(panel).toHaveTextContent('No recharge logged for November 2026 yet.')
    // The AC waits for a recharge to come out of.
    expect(await section('AC Allocations & Responsibility')).toHaveTextContent('Log a meter recharge first')

    await userEvent.click(within(panel).getByRole('button', { name: 'Add Meter Recharge' }))
    const dialog = screen.getByRole('dialog', { name: 'Add Meter Recharge' })
    const log = within(dialog).getByRole('button', { name: 'Log Recharge' })
    expect(log).toBeDisabled()
    await userEvent.selectOptions(within(dialog).getByLabelText(/^Meter/), 'dg')
    await userEvent.selectOptions(within(dialog).getByLabelText(/^Paid by/), 'sarbananda')
    expect(dialog).toHaveTextContent('What they paid beyond their own share, the kitty pays back to them.')
    await userEvent.type(within(dialog).getByLabelText(/^Amount/), '500')
    fireEvent.change(within(dialog).getByLabelText(/^Recharged on/), { target: { value: '2026-11-20' } })
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UTR-3')
    await userEvent.click(log)
    await waitFor(() => expect(sent('POST', '/api/electricity/disburse-payout')).toBeDefined())
    expect(sent('POST', '/api/electricity/disburse-payout')!.body).toEqual({
      settlement_month: MONTH,
      provider: 'dg',
      disbursement_mode: 'resident_advance',
      funding_resident_id: 'sarbananda',
      amount_paise: 50000,
      disbursed_date: '2026-11-20',
      reference: 'UTR-3',
      notes: null,
    })
    expect(await screen.findByText('Recharge logged')).toBeInTheDocument()
    const list = await within(await section('Meter Top-ups / Recharges')).findByRole('table', {
      name: 'Meter recharges for November 2026',
    })
    expect(within(list).getByRole('row', { name: /Diesel Generator/ })).toHaveTextContent('Sarbananda₹500.00')
  })

  it('logs a recharge from the kitty with no flatmate behind it', async () => {
    show(empty, {}, ALLOCATIONS)
    await userEvent.click(
      within(await section('Meter Top-ups / Recharges')).getByRole('button', { name: 'Add Meter Recharge' }),
    )
    const dialog = screen.getByRole('dialog', { name: 'Add Meter Recharge' })
    await userEvent.selectOptions(within(dialog).getByLabelText(/^Paid by/), 'kitty')
    await userEvent.type(within(dialog).getByLabelText(/^Amount/), '1000')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UTR-K')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Log Recharge' }))
    await waitFor(() => expect(sent('POST', '/api/electricity/disburse-payout')).toBeDefined())
    const body = sent('POST', '/api/electricity/disburse-payout')!.body as Record<string, unknown>
    expect(body).toMatchObject({ provider: 'uppcl', disbursement_mode: 'pool', amount_paise: 100000 })
    expect(body).not.toHaveProperty('funding_resident_id')
  })

  it('opens a recharge from its row, with all its details', async () => {
    show(recorded({ top_ups: [{ ...recorded().top_ups[1], note: 'Meter low before the weekend' }, recorded().top_ups[2]] }), {}, ALLOCATIONS)
    const log = within(await section('Meter Top-ups / Recharges')).getByRole('table', {
      name: 'Meter recharges for November 2026',
    })
    await userEvent.click(within(log).getByRole('row', { name: /Arnab/ }))
    const dialog = screen.getByRole('dialog', { name: 'Meter Recharge' })
    expect(dialog).toHaveTextContent('UPPCL (Grid) · 12 Nov 2026')
    const details = within(dialog).getByLabelText('Recharge details')
    expect(details).toHaveTextContent('Amount₹1,500.00')
    expect(details).toHaveTextContent('Meter / ProviderUPPCL (Grid)')
    expect(details).toHaveTextContent('Recharged On12 Nov 2026')
    expect(details).toHaveTextContent('Paid ByArnabFrom their own account')
    expect(details).toHaveTextContent('Reference / UTRUTR-2')
    expect(details).toHaveTextContent('NoteMeter low before the weekend')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens a recharge from the keyboard, and says when the kitty paid it', async () => {
    show(recorded({ top_ups: [topUp('svcdisb_9', 'dg', 30000, '2026-11-25', null, 'UTR-K')] }), {}, ALLOCATIONS)
    const log = within(await section('Meter Top-ups / Recharges')).getByRole('table', {
      name: 'Meter recharges for November 2026',
    })
    within(log).getByRole('row', { name: /Diesel Generator/ }).focus()
    await userEvent.keyboard('{Enter}')
    const kitty = screen.getByRole('dialog', { name: 'Meter Recharge' })
    expect(within(kitty).getByLabelText('Recharge details')).toHaveTextContent('Paid ByHouse KittyFrom the House Kitty')
    expect(within(kitty).getByLabelText('Recharge details')).not.toHaveTextContent('Note')
  })

  it('removes a recharge from its details, once confirmed', async () => {
    show(recorded(), {}, ALLOCATIONS)
    const log = within(await section('Meter Top-ups / Recharges')).getByRole('table', {
      name: 'Meter recharges for November 2026',
    })
    await userEvent.click(within(log).getByRole('row', { name: /Diesel Generator/ }))
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Meter Recharge' })).getByRole('button', { name: 'Remove Recharge' }))
    expect(screen.queryByRole('dialog', { name: 'Meter Recharge' })).toBeNull()
    const confirm = screen.getByRole('dialog', { name: 'Remove this recharge?' })
    await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(sent('DELETE', `/api/electricity/month/${MONTH}/top-ups/svcdisb_3`)).toBeDefined())
  })

  it('removes a recharge logged by mistake, once confirmed', async () => {
    show(recorded(), {}, ALLOCATIONS)
    const panel = await section('Meter Top-ups / Recharges')
    await userEvent.click(within(panel).getByRole('button', { name: 'Remove Diesel Generator (DG) recharge of ₹500.00' }))
    // Its button removes it without also opening it.
    expect(screen.queryByRole('dialog', { name: 'Meter Recharge' })).toBeNull()
    const confirm = screen.getByRole('dialog', { name: 'Remove this recharge?' })
    expect(confirm).toHaveTextContent('Diesel Generator (DG) recharge of ₹500.00 on 20 Nov 2026, paid by Sarbananda.')
    await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(sent('DELETE', `/api/electricity/month/${MONTH}/top-ups/svcdisb_3`)).toBeDefined())
    expect(await screen.findByText('Recharge removed')).toBeInTheDocument()
  })

  it("takes each flatmate's AC out of what was recharged, beside what it leaves them, and saves it in paise", async () => {
    show(recharged, {}, ALLOCATIONS)
    const panel = await section('AC Allocations & Responsibility')
    const table = within(panel).getByRole('table', { name: 'AC and responsibility for November 2026' })
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Flatmate',
      'Individual AC Cost',
      'Shared Remainder',
      'Net Responsibility',
    ])
    const row = (name: string) => within(table).getByRole('row', { name: new RegExp(`^${name}`) })
    // Every flatmate has an AC to fill in, ₹0 until they do: the whole recharge is shared.
    for (const name of ['Anagarag', 'Arnab', 'Sarbananda']) {
      expect(within(row(name)).getByLabelText(`${name}'s AC`)).toHaveValue('')
      expect(within(row(name)).getByLabelText(`${name}'s AC`)).toHaveAttribute('placeholder', '0.00')
    }
    expect(shares(row('Sarbananda'))).toEqual(['₹1,333.33', '₹1,333.33'])
    expect(panel).not.toHaveTextContent('Stored as integer paise')
    const sums = within(panel).getByLabelText('AC and the shared remainder')
    expect(sums).toHaveTextContent('Total Recharged₹4,000.00')
    const status = within(panel).getByRole('status')
    // Nothing to say until the AC changes.
    expect(status).toBeEmptyDOMElement()
    const save = within(panel).getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()

    fireEvent.change(within(table).getByLabelText("Sarbananda's AC"), { target: { value: '971.05' } })
    fireEvent.change(within(table).getByLabelText("Arnab's AC"), { target: { value: '895.48' } })
    expect(sums).toHaveTextContent('Sum of ACs₹1,866.53')
    expect(sums).toHaveTextContent('Shared Remainder₹2,133.47')
    // The AC and what's left of it come to what was recharged.
    expect(within(table).getByRole('row', { name: /^Total/ })).toHaveTextContent('Total₹1,866.53₹2,133.47₹4,000.00')
    // Each share is the backend's, once typing pauses.
    expect(table).toHaveAttribute('aria-busy', 'true')
    expect(status).toHaveTextContent("Unsaved changes: working out each flatmate's share…")
    await waitFor(() => expect(shares(row('Sarbananda'))).toEqual(['₹711.15', '₹1,682.20']))
    expect(shares(row('Arnab'))).toEqual(['₹711.16', '₹1,606.64'])
    expect(shares(row('Anagarag'))).toEqual(['₹711.16', '₹711.16'])
    expect(table).not.toHaveAttribute('aria-busy')
    expect(status).toHaveTextContent('Unsaved changes: the shares above are a preview until you save.')
    expect(sent('POST', `/api/electricity/month/${MONTH}/preview`)!.body).toEqual({
      ac_allocations: { anagarag: 0, arnab: 89548, sarbananda: 97105 },
    })

    // Cleared, a flatmate's AC is back to ₹0.
    fireEvent.change(within(table).getByLabelText("Arnab's AC"), { target: { value: '' } })
    expect(sums).toHaveTextContent('Shared Remainder₹3,028.95')
    fireEvent.change(within(table).getByLabelText("Arnab's AC"), { target: { value: '895.48' } })

    // Saved from the foot of the card.
    expect(save).toBeEnabled()
    await userEvent.click(save)
    await waitFor(() => expect(sent('POST', '/api/electricity/bills')).toBeDefined())
    expect(sent('POST', '/api/electricity/bills')!.body).toEqual({
      settlement_month: MONTH,
      ac_allocations: { anagarag: 0, arnab: 89548, sarbananda: 97105 },
    })
    expect(await screen.findByText('AC allocations saved')).toBeInTheDocument()
    // Saved, the month is collected and paid back on its settlement.
    expect(screen.getByLabelText('location')).toHaveTextContent(/^\/$/)
    expect(await screen.findByRole('heading', { name: 'Resident Collection' })).toBeInTheDocument()
  })

  it('refuses AC that comes to more than was recharged', async () => {
    show(recorded({ total_paise: 100000 }), {}, ALLOCATIONS)
    const panel = await section('AC Allocations & Responsibility')
    fireEvent.change(within(panel).getByLabelText("Arnab's AC"), { target: { value: '1200' } })
    expect(within(panel).getByRole('alert')).toHaveTextContent('The AC sum, ₹2,171.05, exceeds the ₹1,000.00 recharged.')
    // Nothing is left to share, so no share is worked out.
    expect(within(panel).getByLabelText('AC and the shared remainder')).toHaveTextContent('Shared Remainder−₹1,171.05')
    expect(shares(within(panel).getByRole('row', { name: /^Arnab/ }))).toEqual(['—Not worked out', '—Not worked out'])
    expect(sent('POST', `/api/electricity/month/${MONTH}/preview`)).toBeUndefined()
    expect(within(panel).getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it("opens a flatmate's statement from their row: their share, what they recharged, and their payments", async () => {
    show(
      recorded({
        shares: [
          share('anagarag', 'Anagarag', 71116, 0),
          share('arnab', 'Arnab', 71116, 89548),
          share('sarbananda', 'Sarbananda', 71115, 97105, {
            settled_paise: 168220,
            paid_paise: 168220,
            outstanding_paise: 0,
            status: 'paid',
            net_balance_paise: 81780,
            payments: [paidIn('payment_s1', '2026-11-03', 168220, { reference: 'UTR-1', top_up_id: 'svcdisb_1' })],
          }),
        ],
      }),
    )
    const owed = within(await section('Resident Collection')).getByRole('table', {
      name: 'Electricity owed for November 2026',
    })
    await userEvent.click(within(owed).getByRole('row', { name: /Sarbananda/ }))
    const statement = screen.getByRole('dialog', { name: 'Electricity Statement' })
    expect(statement).toHaveTextContent('Sarbananda · November 2026')
    expect(statement).toHaveTextContent('Share₹1,682.20Common: ₹711.15 · AC: ₹971.05')
    expect(statement).toHaveTextContent('Paid into Kitty₹1,682.20')
    expect(statement).toHaveTextContent('Still Owed₹0.00')
    // They recharged beyond their share: the kitty pays them back the rest.
    expect(statement).toHaveTextContent(
      'Sarbananda advanced ₹2,500.00 for prepaid meter top-ups, ₹817.80 beyond their share of ₹1,682.20, which will be reimbursed via the House Kitty.',
    )
    const history = within(statement).getByRole('table', { name: "Payments towards Sarbananda's electricity share" })
    expect(within(history).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Date',
      'Amount',
      'Mode',
      'Reference / UTR',
      'Actions',
    ])
    // Their share paid with a recharge goes only with the recharge.
    expect(within(history).getAllByRole('row')[1]).toHaveTextContent('3 Nov 2026₹1,682.20Meter rechargeUTR-1—')
    expect(within(history).queryByRole('button', { name: /^Delete/ })).toBeNull()
    expect(statement).toHaveTextContent('A share paid with a meter recharge goes with that recharge')
  })

  it("deletes a flatmate's payment from their statement, once confirmed", async () => {
    show(
      recorded({
        shares: [
          share('anagarag', 'Anagarag', 71116, 0, {
            settled_paise: 30000,
            paid_paise: 30000,
            outstanding_paise: 41116,
            status: 'partial',
            net_balance_paise: -41116,
            payments: [paidIn('payment_g1', '2026-11-12', 30000, { payment_mode: 'upi', reference: 'UPI-7' })],
          }),
          share('arnab', 'Arnab', 71116, 89548),
          share('sarbananda', 'Sarbananda', 71115, 97105),
        ],
      }),
      { 'DELETE /api/electricity/payments/payment_g1': recorded() },
    )
    const owed = within(await section('Resident Collection')).getByRole('table', {
      name: 'Electricity owed for November 2026',
    })
    const row = within(owed).getByRole('row', { name: /Anagarag/ })
    row.focus()
    await userEvent.keyboard('{Enter}')
    const statement = screen.getByRole('dialog', { name: 'Electricity Statement' })
    // No AC: their share is all common.
    expect(statement).toHaveTextContent('Share₹711.16Common: ₹711.16Paid')
    expect(statement).not.toHaveTextContent('advanced')
    const history = within(statement).getByRole('table', { name: "Payments towards Anagarag's electricity share" })
    expect(within(history).getAllByRole('row')[1]).toHaveTextContent('12 Nov 2026₹300.00UPIUPI-7')

    await userEvent.click(within(history).getByRole('button', { name: 'Delete the ₹300.00 payment of 12 Nov 2026' }))
    const confirm = screen.getByRole('dialog', { name: 'Delete this payment?' })
    expect(confirm).toHaveTextContent("Anagarag's ₹300.00 payment into the House Kitty of 12 Nov 2026.")
    await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(sent('DELETE', '/api/electricity/payments/payment_g1')).toBeDefined())
    expect(await screen.findByText('Payment deleted')).toBeInTheDocument()
  })

  it('shows a statement with nothing recorded yet', async () => {
    show(empty)
    const owed = within(await section('Resident Collection')).getByRole('table', {
      name: 'Electricity owed for November 2026',
    })
    await userEvent.click(within(owed).getByRole('row', { name: /Arnab/ }))
    const statement = screen.getByRole('dialog', { name: 'Electricity Statement' })
    // Nothing is owed or paid until the month is recorded.
    expect(statement).toHaveTextContent('Paid into Kitty—')
    expect(statement).toHaveTextContent('Still Owed—')
    expect(statement).toHaveTextContent('No payments recorded yet for this month.')
  })

  it("keeps a finalized month's payments in its statements", async () => {
    show(
      recorded({
        bill_state: 'frozen',
        can_record: false,
        transactions_open: false,
        statement_status: 'finalized_correction_window',
        shares: [
          share('anagarag', 'Anagarag', 71116, 0, {
            settled_paise: 30000,
            paid_paise: 30000,
            outstanding_paise: 41116,
            status: 'partial',
            payments: [paidIn('payment_g1', '2026-11-12', 30000, { payment_mode: 'upi' })],
          }),
          share('arnab', 'Arnab', 71116, 89548),
          share('sarbananda', 'Sarbananda', 71115, 97105),
        ],
      }),
    )
    const owed = within(await section('Resident Collection')).getByRole('table', {
      name: 'Electricity owed for November 2026',
    })
    await userEvent.click(within(owed).getByRole('row', { name: /Anagarag/ }))
    const history = within(screen.getByRole('dialog', { name: 'Electricity Statement' })).getByRole('table')
    expect(within(history).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Date',
      'Amount',
      'Mode',
      'Reference / UTR',
    ])
    expect(within(history).queryByRole('button')).toBeNull()
  })

  it('shows what each flatmate owes the kitty, and what it owes back', async () => {
    show(recorded())
    const collection = await section('Resident Collection')
    const owed = within(collection).getByRole('table', { name: 'Electricity owed for November 2026' })
    // Responsible for their common share and their AC.
    expect(within(owed).getByRole('row', { name: /Sarbananda/ })).toHaveTextContent('Receives ₹817.80')
    expect(within(owed).getByRole('row', { name: /Arnab/ })).toHaveTextContent('Owes ₹106.64')
    expect(within(owed).getByRole('row', { name: /Anagarag/ })).toHaveTextContent('Owes ₹711.16')
    // Sarbananda recharged beyond their own share: the kitty owes back the rest.
    const kitty = within(await section('Reimbursement')).getByRole('table', {
      name: 'Reimbursements from the House Kitty',
    })
    expect(within(kitty).getByRole('row', { name: /Sarbananda/ })).toHaveTextContent('₹817.80')

    await userEvent.click(within(owed).getByRole('button', { name: 'Pay now for Arnab' }))
    const dialog = screen.getByRole('dialog', { name: 'Record Payment into House Kitty' })
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('106.64')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UPI-7')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payment' }))
    await waitFor(() => expect(sent('POST', '/api/electricity/record-payment')).toBeDefined())
    expect(sent('POST', '/api/electricity/record-payment')!.body).toMatchObject({
      settlement_month: MONTH,
      resident_id: 'arnab',
      amount_paise: 10664,
    })
  })

  it('keeps a finalized month as it was entered', async () => {
    show(
      recorded({ bill_state: 'frozen', can_record: false, transactions_open: false, statement_status: 'finalized_correction_window' }),
      {},
      ALLOCATIONS,
    )
    expect(await screen.findByText('Statement finalized')).toBeInTheDocument()
    const panel = await section('Meter Top-ups / Recharges')
    expect(within(panel).queryByRole('button', { name: 'Add Meter Recharge' })).toBeNull()
    expect(within(panel).queryByRole('button', { name: /^Remove/ })).toBeNull()
    // Its recharges can still be read, not removed.
    await userEvent.click(within(panel).getByRole('row', { name: /Arnab/ }))
    const details = screen.getByRole('dialog', { name: 'Meter Recharge' })
    expect(within(details).queryByRole('button', { name: 'Remove Recharge' })).toBeNull()
    await userEvent.click(within(details).getByRole('button', { name: 'Close' }))
    // Its AC is read, not edited, beside what it left each flatmate.
    const ac = await section('AC Allocations & Responsibility')
    expect(within(ac).queryByRole('textbox')).toBeNull()
    const table = within(ac).getByRole('table', { name: 'AC and responsibility for November 2026' })
    expect(within(table).getByRole('row', { name: /^Arnab/ })).toHaveTextContent('Arnab₹895.48₹711.16₹1,606.64')
    expect(within(ac).queryByRole('button', { name: 'Save' })).toBeNull()
  })
})
