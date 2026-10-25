/**
 * The furniture rental screen shows what the backend worked out -- the month's
 * shares, the items, the deposit -- and sends exactly what the operator chose.
 * These tests pin both: the figures as returned, and every body it posts.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FurnitureRental } from './FurnitureRental'
import { ToastProvider } from '../../components/Toast'
import { RESIDENTS, installFetchStub, type Recorded } from '../../test/server'

const MONTH = '2026-11'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const item = (overrides: Record<string, unknown> = {}) => ({
  item_id: 'furn-sofa',
  service_id: 'svc-1',
  name: 'Sofa',
  category: 'living_room',
  monthly_rent_paise: 100000,
  allocation_scope: 'common',
  assigned_resident_id: null,
  assigned_resident_name: null,
  status: 'active',
  delivery_date: '2026-09-01',
  return_date: null,
  created_at: '2026-09-01T10:00:00+05:30',
  updated_at: '2026-09-01T10:00:00+05:30',
  ...overrides,
})

const items = [
  item(),
  item({ item_id: 'furn-fridge', name: 'Fridge', category: 'appliance', monthly_rent_paise: 70000 }),
  item({
    item_id: 'furn-bed',
    name: 'Queen bed',
    category: 'bedroom',
    monthly_rent_paise: 50000,
    allocation_scope: 'resident',
    assigned_resident_id: 'bob',
    assigned_resident_name: 'Bob',
  }),
  item({ item_id: 'furn-desk', name: 'Desk', category: 'workstation', status: 'pending_delivery', delivery_date: '2026-12-02' }),
]

const bill = (overrides: Record<string, unknown> = {}) => ({
  settlement_month: MONTH,
  billable_items: items.slice(0, 3),
  common_total_paise: 170000,
  private_total_paise: 50000,
  total_paise: 220000,
  participant_count: 3,
  base_share_paise: 56666,
  remainder_paise: 2,
  shares: [
    share('alice', 'Alice', 56667),
    share('bob', 'Bob', 56667, { private_paise: 50000, private_items: ['Queen bed'], total_paise: 106667 }),
    share('chris', 'Chris', 56666),
  ],
  excluded_residents: [],
  blocked_reason: null,
  expense: null,
  statement_status: 'draft',
  can_generate: true,
  vendor_payout: null,
  reimbursement: null,
  ...overrides,
})

function share(residentId: string, name: string, common: number, overrides: Record<string, unknown> = {}) {
  return {
    resident_id: residentId,
    display_name: name,
    taking_part: true,
    common_share_paise: common,
    private_paise: 0,
    private_items: [],
    total_paise: common,
    paid_paise: 0,
    outstanding_paise: 0,
    status: null,
    paid_date: null,
    payments: [],
    ...overrides,
  }
}

/** November recorded as an expense, with nobody yet paid. */
const billed = (overrides: Record<string, unknown> = {}) =>
  bill({
    expense: { expense_id: 'furniture-1', description: 'Furniture rental for November 2026', amount_paise: 220000, statement_id: null },
    can_generate: false,
    shares: bill().shares.map((row) => ({ ...row, status: 'unpaid', outstanding_paise: row.total_paise })),
    ...overrides,
  })

/** What the kitty owes Alice for paying Furlenco, with Bob's and part of Chris's share paid in. */
const OWED_TO_ALICE = {
  recipient_resident_id: 'alice',
  recipient_name: 'Alice',
  total_fronted_paise: 220000,
  payer_share_paise: 56667,
  net_reimbursement_owed_paise: 163333,
  reimbursed_so_far_paise: 0,
  pending_reimbursement_paise: 163333,
  kitty_cash_available_paise: 136667,
  reimbursable_paise: 136667,
  can_reimburse: true,
  reimbursements: [],
}

/** Alice paid Furlenco the whole bill; Bob has paid into the kitty and Chris part of his share. */
const paidByAlice = (overrides: Record<string, unknown> = {}) =>
  billed({
    shares: [
      share('alice', 'Alice', 56667, { paid_paise: 56667, status: 'funded_payout', paid_date: '2026-11-05' }),
      share('bob', 'Bob', 56667, {
        private_paise: 50000,
        private_items: ['Queen bed'],
        total_paise: 106667,
        paid_paise: 106667,
        status: 'paid',
        paid_date: '2026-11-08',
        payments: [
          {
            payment_id: 'pay-bob',
            payment_date: '2026-11-08',
            amount_paise: 106667,
            payment_mode: 'upi',
            reference: 'UPI-88',
            note: 'November furniture',
          },
        ],
      }),
      share('chris', 'Chris', 56666, {
        paid_paise: 30000,
        outstanding_paise: 26666,
        status: 'partial',
        paid_date: '2026-11-08',
        payments: [
          { payment_id: 'pay-chris', payment_date: '2026-11-08', amount_paise: 30000, payment_mode: 'cash', reference: null, note: null },
        ],
      }),
    ],
    vendor_payout: {
      payer_resident_id: 'alice',
      payer_name: 'Alice',
      amount_paise: 220000,
      payment_date: '2026-11-05',
      reference: 'UTR-4471',
    },
    reimbursement: OWED_TO_ALICE,
    ...overrides,
  })

const overview = (overrides: Record<string, unknown> = {}) => ({
  service: {
    service_id: 'svc-1',
    service_type: 'furniture_rental',
    display_name: 'Furniture rental',
    provider_name: 'Furlenco',
    status: 'active',
    billing_day_of_month: 5,
    period_rule: 'current_month',
    created_at: '2026-09-01T10:00:00+05:30',
    updated_at: '2026-09-01T10:00:00+05:30',
  },
  config: {
    provider_name: 'Furlenco',
    customer_id: 'FUR-2291',
    subscription_reference: null,
    deposit_paid_paise: 500000,
    deposit_refunded_paise: 0,
    deposit_status: 'held',
    expense_definition_id: 'furniture-rental',
    notes: null,
    updated_at: '2026-09-01T10:00:00+05:30',
  },
  items,
  month: bill(),
  monthly_total_paise: 220000,
  active_item_count: 3,
  next_billing_date: '2026-12-05',
  deposit_held_paise: 500000,
  ...overrides,
})

const notSetUp = {
  service: null,
  config: null,
  items: [],
  month: null,
  monthly_total_paise: 0,
  active_item_count: 0,
  next_billing_date: null,
  deposit_held_paise: 0,
}

function show(payload: Record<string, unknown>, routes: Record<string, unknown> = {}) {
  stub = installFetchStub({
    routes: {
      '/api/services/furniture': payload,
      '/api/residents': RESIDENTS,
      'POST /api/services/furniture/setup': overview(),
      'POST /api/services/furniture/items': item({ item_id: 'furn-new', name: 'Study chair' }),
      'PATCH /api/services/furniture/items/furn-sofa': item({ status: 'returned', return_date: '2026-11-20' }),
      'POST /api/services/furniture/generate-cycle': overview(),
      ...routes,
    } as never,
  })
  return render(
    <MemoryRouter>
      <ToastProvider>
        <FurnitureRental month={MONTH} />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path === path)

describe('FurnitureRental', () => {
  it('offers to set up the subscription, and sends its details', async () => {
    show(notSetUp)
    await userEvent.click(await screen.findByRole('button', { name: 'Set up furniture rental' }))
    const dialog = screen.getByRole('dialog', { name: 'Set up furniture rental' })
    await userEvent.selectOptions(within(dialog).getByLabelText('Provider'), 'Rentomojo')
    await userEvent.type(within(dialog).getByLabelText('Billing day'), '32')
    expect(within(dialog).getByText('Enter a day from 1 to 31.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
    await userEvent.clear(within(dialog).getByLabelText('Billing day'))
    await userEvent.type(within(dialog).getByLabelText('Billing day'), '5')
    await userEvent.type(within(dialog).getByLabelText('Customer ID'), 'RM-1182')
    await userEvent.clear(within(dialog).getByLabelText(/Deposit paid/))
    await userEvent.type(within(dialog).getByLabelText(/Deposit paid/), '5000')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(sent('POST', '/api/services/furniture/setup')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/setup')!.body).toEqual({
      provider_name: 'Rentomojo',
      customer_id: 'RM-1182',
      subscription_reference: null,
      billing_day_of_month: 5,
      deposit_paid_paise: 500000,
      deposit_refunded_paise: 0,
      notes: null,
    })
  })

  it('shows the subscription at a glance, with its actions in one menu', async () => {
    show(overview())
    const subscription = (await screen.findByRole('heading', { name: 'Furniture rental' })).closest('section')!
    expect(subscription).toHaveTextContent('ProviderFurlenco')
    expect(subscription).toHaveTextContent('Customer FUR-2291')
    expect(subscription).toHaveTextContent('Monthly total₹2,200.00')
    expect(subscription).toHaveTextContent('3 items in the flat')
    expect(subscription).toHaveTextContent('5 Dec 2026')
    expect(subscription).toHaveTextContent('Deposit held₹5,000.00')
    await userEvent.click(within(subscription).getByRole('button', { name: 'More options' }))
    expect(screen.getAllByRole('menuitem').map((option) => option.textContent)).toEqual([
      'Edit subscription',
      'End subscription',
    ])
  })

  it('lists each item with who pays for it and where it is', async () => {
    show(overview())
    const table = await screen.findByRole('table', { name: 'Rented items' })
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Item',
      'Category',
      'Paid by',
      'Monthly rent',
      'Status',
      'Actions',
    ])
    expect(within(table).getByRole('row', { name: /Sofa/ })).toHaveTextContent('Shared₹1,000.00In the flatSince 1 Sep 2026')
    expect(within(table).getByRole('row', { name: /Queen bed/ })).toHaveTextContent('BedroomBob₹500.00')
    expect(within(table).getByRole('row', { name: /Desk/ })).toHaveTextContent('Awaiting deliveryDue 2 Dec 2026')
  })

  it("splits the month's bill and records it", async () => {
    show(overview())
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    expect(within(shares).getByRole('row', { name: /Bob/ })).toHaveTextContent('₹566.67₹500.00Queen bed₹1,066.67')
    expect(within(shares).getByRole('row', { name: /Chris/ })).toHaveTextContent('₹566.66₹0.00₹566.66')
    expect(screen.getByText('3 taking part · 2 paise over')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Record November 2026 bill' }))
    const dialog = screen.getByRole('dialog', { name: 'Record the November 2026 bill' })
    expect(dialog).toHaveTextContent('₹2,200.00 as one expense')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record bill' }))
    await waitFor(() => expect(sent('POST', '/api/services/furniture/generate-cycle')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/generate-cycle')!.body).toEqual({ settlement_month: MONTH })
  })

  it('says when the month is already billed, or cannot be', async () => {
    const { unmount } = show(
      overview({
        month: bill({
          expense: { expense_id: 'furniture-1', description: 'Furniture rental for November 2026', amount_paise: 220000, statement_id: null },
          can_generate: false,
        }),
      }),
    )
    // The recorded bill shows each share's status instead of a footer saying it is billed.
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    expect(within(shares).getByRole('columnheader', { name: 'Status' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'View in Expenses' })).toBeNull()
    expect(screen.queryByText('Billed')).toBeNull()
    expect(screen.queryByRole('button', { name: /Record November 2026 bill/ })).toBeNull()
    unmount()
    stub?.restore()

    show(overview({ month: bill({ blocked_reason: 'Bob has a rented item of their own but is not taking part this month.', can_generate: false }) }))
    expect(await screen.findByText(/Bob has a rented item of their own/)).toHaveAttribute('role', 'status')
    expect(screen.getByRole('button', { name: 'Record November 2026 bill' })).toBeDisabled()
  })

  it('adds an item charged to one resident', async () => {
    show(overview())
    const panel = (await screen.findByRole('heading', { name: 'Rented Items' })).closest('section')!
    await userEvent.click(within(panel).getByRole('button', { name: 'Add item' }))
    const dialog = screen.getByRole('dialog', { name: 'Add a rented item' })
    const add = within(dialog).getByRole('button', { name: 'Add item' })
    expect(add).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/^Item/), 'Study chair')
    await userEvent.selectOptions(within(dialog).getByLabelText('Category'), 'workstation')
    await userEvent.type(within(dialog).getByLabelText(/Monthly rent/), '199')
    await waitFor(() => expect(within(dialog).getByRole('option', { name: 'Bob' })).toBeInTheDocument())
    await userEvent.selectOptions(within(dialog).getByLabelText('Paid by'), 'bob')
    await userEvent.click(add)

    await waitFor(() => expect(sent('POST', '/api/services/furniture/items')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/items')!.body).toEqual({
      name: 'Study chair',
      category: 'workstation',
      monthly_rent_paise: 19900,
      allocation_scope: 'resident',
      assigned_resident_id: 'bob',
      delivery_date: null,
      status: 'active',
    })
  })

  it('says when nobody has paid the provider, and records who did', async () => {
    show(overview({ month: billed() }), {
      'POST /api/services/furniture/mark-paid-by-resident': overview({ month: paidByAlice() }),
    })
    expect(await screen.findByText('Pending vendor payout')).toBeInTheDocument()
    const shares = screen.getByRole('table', { name: 'Shares for November 2026' })
    expect(within(shares).getByRole('row', { name: /Chris/ })).toHaveTextContent('Unpaid')

    await userEvent.click(screen.getByRole('button', { name: 'Mark Paid by Resident' }))
    const dialog = screen.getByRole('dialog', { name: 'Mark Paid by Resident' })
    expect(dialog).toHaveTextContent('Who paid Furlenco the November 2026 bill of ₹2,200.00')
    const record = within(dialog).getByRole('button', { name: 'Record payout' })
    expect(record).toBeDisabled()
    await waitFor(() => expect(within(dialog).getByRole('option', { name: 'Alice' })).toBeInTheDocument())
    await userEvent.selectOptions(within(dialog).getByLabelText(/^Paid by/), 'alice')
    await userEvent.type(within(dialog).getByLabelText('Reference / UTR'), 'UTR-4471')
    await userEvent.click(record)

    await waitFor(() => expect(sent('POST', '/api/services/furniture/mark-paid-by-resident')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/mark-paid-by-resident')!.body).toEqual({
      settlement_month: MONTH,
      payer_resident_id: 'alice',
      amount_paise: 220000,
      payment_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      reference: 'UTR-4471',
      notes: null,
    })
    expect(await screen.findByText('Vendor payout recorded')).toBeInTheDocument()
  })

  it('shows who paid the provider, what each flatmate has paid in, and reimburses from the kitty', async () => {
    show(overview({ month: paidByAlice() }), {
      'POST /api/services/furniture/reimburse-from-kitty': overview({ month: paidByAlice() }),
    })
    expect(await screen.findByRole('heading', { name: 'Reimbursement · Alice' })).toBeInTheDocument()
    expect(screen.queryByText(/Paid directly to Furlenco/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Mark Paid by Resident' })).toBeNull()

    const shares = screen.getByRole('table', { name: 'Shares for November 2026' })
    expect(within(shares).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Resident',
      'Shared items',
      'Own items',
      'Total',
      'Status',
      'Actions',
    ])
    expect(within(shares).getByRole('row', { name: /Alice/ })).toHaveTextContent('Paid FurlencoPay Now')
    expect(within(shares).getByRole('row', { name: /Bob/ })).toHaveTextContent('Paid into KittyPay Now')
    expect(within(shares).getByRole('row', { name: /Chris/ })).toHaveTextContent('Part paidPay Now')

    const card = screen.getByRole('heading', { name: 'Reimbursement · Alice' }).closest('section')!
    expect(card).toHaveTextContent('Fronted₹2,200.00')
    expect(card).toHaveTextContent('Owed back₹1,633.33Less their own share of ₹566.67')
    expect(card).toHaveTextContent('Paid back₹0.00₹1,633.33 still owed')
    expect(card).toHaveTextContent('Kitty cash available₹1,366.67')

    await userEvent.click(within(card).getByRole('button', { name: 'Reimburse ₹1,366.67 from Kitty to Alice' }))
    const dialog = screen.getByRole('dialog', { name: 'Reimburse Alice' })
    expect(dialog).toHaveTextContent('₹1,366.67 from the kitty to Alice')
    expect(dialog).toHaveTextContent('₹266.66 more is owed once the rest have paid.')
    await userEvent.type(within(dialog).getByLabelText('Reference / UTR'), 'UPI-88')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record reimbursement' }))

    await waitFor(() => expect(sent('POST', '/api/services/furniture/reimburse-from-kitty')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/reimburse-from-kitty')!.body).toEqual({
      settlement_month: MONTH,
      recipient_resident_id: 'alice',
      amount_paise: 136667,
      payment_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      reference: 'UPI-88',
      notes: null,
    })
    expect(await screen.findByText('Reimbursement recorded')).toBeInTheDocument()
  })

  it('keeps each share to its status, with Pay Now for whoever still owes', async () => {
    show(overview({ month: paidByAlice() }))
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    expect(within(shares).getByRole('columnheader', { name: 'Resident' })).toHaveClass('text-left')
    // No dates or part-paid amounts under the chips: they are in the share's details.
    expect(shares).not.toHaveTextContent('Nov 2026')
    expect(shares).not.toHaveTextContent('of ₹')
    expect(within(shares).getByRole('button', { name: 'Pay now for Alice' })).toBeDisabled()
    expect(within(shares).getByRole('button', { name: 'Pay now for Bob' })).toBeDisabled()
    expect(within(shares).getByRole('button', { name: 'Pay now for Chris' })).toBeEnabled()
  })

  it("opens a share's details from its row, by click or by keyboard", async () => {
    show(overview({ month: paidByAlice() }))
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    await userEvent.click(within(shares).getByRole('row', { name: /Bob/ }))
    const bob = screen.getByRole('dialog', { name: 'Bob · November 2026 Share Details' })
    expect(bob).toHaveTextContent('StatusPaid into KittyTotal month share₹1,066.67')
    expect(bob).toHaveTextContent('Common items share₹566.67Private items share₹500.00Queen bed')
    expect(bob).toHaveTextContent(
      'Paid amount₹1,066.67Payment date8 Nov 2026Payment modeUPIReference / UTRUPI-88NotesNovember furniture',
    )
    expect(within(bob).queryByRole('button', { name: /Pay Now/ })).toBeNull()
    await userEvent.click(within(bob).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    within(shares).getByRole('row', { name: /Chris/ }).focus()
    await userEvent.keyboard('{Enter}')
    const chris = screen.getByRole('dialog', { name: 'Chris · November 2026 Share Details' })
    expect(chris).toHaveTextContent('Paid amount₹300.00Payment date8 Nov 2026Payment modeCashReference / UTR—')
    await userEvent.click(within(chris).getByRole('button', { name: 'Pay Now' }))
    expect(screen.getByRole('dialog', { name: 'Record Payment into House Kitty' })).toBeInTheDocument()
  })

  it('records a payment into the House Kitty against the share', async () => {
    show(overview({ month: paidByAlice() }), {
      'POST /api/services/furniture/record-payment': overview({ month: paidByAlice() }),
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now for Chris' }))
    expect(screen.queryByRole('dialog', { name: /Share Details/ })).toBeNull()
    const dialog = screen.getByRole('dialog', { name: 'Record Payment into House Kitty' })
    expect(dialog).toHaveTextContent('Chris · November 2026 furniture rental share · ₹266.66 outstanding')
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('266.66')
    const record = within(dialog).getByRole('button', { name: 'Record Payment' })
    expect(record).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/^Reference \/ UTR/), 'UPI-301')
    await userEvent.click(record)

    await waitFor(() => expect(sent('POST', '/api/services/furniture/record-payment')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/record-payment')!.body).toEqual({
      settlement_month: MONTH,
      resident_id: 'chris',
      amount_paise: 26666,
      payment_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      payment_mode: 'upi',
      reference: 'UPI-301',
      note: null,
    })
    expect(await screen.findByText('Payment recorded')).toBeInTheDocument()
  })

  it('calls the household money the Kitty, never the pool', async () => {
    show(overview({ month: paidByAlice() }))
    await screen.findByRole('table', { name: 'Shares for November 2026' })
    expect(document.body).toHaveTextContent('Paid into Kitty')
    expect(document.body).toHaveTextContent('Kitty cash available')
    expect(document.body).not.toHaveTextContent(/pool/i)
    for (const open of ['Pay now for Chris', 'Reimburse ₹1,366.67 from Kitty to Alice']) {
      await userEvent.click(screen.getByRole('button', { name: open }))
      expect(document.body).not.toHaveTextContent(/pool/i)
      await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    }
  })

  it('waits for the kitty to collect before reimbursing, and says when it is done', async () => {
    const { unmount } = show(
      overview({
        month: paidByAlice({
          reimbursement: { ...OWED_TO_ALICE, kitty_cash_available_paise: 0, reimbursable_paise: 0, can_reimburse: false },
        }),
      }),
    )
    expect(await screen.findByRole('button', { name: 'Reimburse from Kitty to Alice' })).toBeDisabled()
    unmount()
    stub?.restore()

    show(
      overview({
        month: paidByAlice({
          reimbursement: {
            ...OWED_TO_ALICE,
            reimbursed_so_far_paise: 163333,
            pending_reimbursement_paise: 0,
            kitty_cash_available_paise: 0,
            reimbursable_paise: 0,
            can_reimburse: false,
          },
        }),
      }),
    )
    const card = (await screen.findByRole('heading', { name: 'Reimbursement · Alice' })).closest('section')!
    expect(card).toHaveTextContent('Alice has been paid back in full.')
    expect(within(card).queryByRole('button', { name: /Reimburse/ })).toBeNull()
  })

  it('records the day an item went back', async () => {
    show(overview())
    await userEvent.click(await screen.findByRole('button', { name: 'Return Sofa' }))
    const dialog = screen.getByRole('dialog', { name: 'Return Sofa' })
    expect(dialog).toHaveTextContent('Nothing is prorated')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record return' }))
    await waitFor(() => expect(sent('PATCH', '/api/services/furniture/items/furn-sofa')).toBeDefined())
    expect(sent('PATCH', '/api/services/furniture/items/furn-sofa')!.body).toMatchObject({ status: 'returned' })
  })
})
