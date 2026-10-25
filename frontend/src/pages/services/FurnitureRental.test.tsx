/**
 * The furniture rental screen shows what the backend worked out -- the month's
 * shares, the items, the deposit -- and sends exactly what the operator chose.
 * These tests pin both: the figures as returned, and every body it posts.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
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
  provider_name: 'Furlenco',
  service_display_name: 'Furniture rental',
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
  collected_paise: 0,
  collected_count: 0,
  payout_status: 'pending',
  disbursed_paise: 0,
  remaining_disbursement_paise: 0,
  disbursements: [],
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
    settled_paise: 0,
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
    remaining_disbursement_paise: 220000,
    ...overrides,
  })

/** Alice paying Furlenco the whole bill from her own account: her own share through the kitty... */
const ALICE_SHARE_VIA_KITTY = {
  disbursement_id: 'svcdisb_payment-alice',
  amount_paise: 56667,
  disbursed_at: '2026-11-05',
  disbursement_reference: 'UTR-4471',
  disbursed_by: 'pool',
  funding_resident_id: null,
  funding_resident_name: null,
  funding_payment_id: null,
  note: "Alice's allotted share via House Kitty",
  created_at: '2026-11-05T10:00:00+05:30',
}

/** ...and the extra beyond it, which the kitty pays back to her. */
const ALICE_ADVANCE = {
  disbursement_id: 'svcdisb-1',
  amount_paise: 163333,
  disbursed_at: '2026-11-05',
  disbursement_reference: 'UTR-4471',
  disbursed_by: 'resident_advance',
  funding_resident_id: 'alice',
  funding_resident_name: 'Alice',
  funding_payment_id: 'furniture-payout-1',
  note: null,
  created_at: '2026-11-05T10:00:00+05:30',
}

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
      share('alice', 'Alice', 56667, {
        paid_paise: 56667,
        settled_paise: 56667,
        status: 'paid',
        paid_date: '2026-11-05',
        payments: [
          {
            payment_id: 'payment-alice',
            payment_date: '2026-11-05',
            amount_paise: 56667,
            payment_mode: null,
            reference: 'UTR-4471',
            note: 'Furniture rental for November 2026',
          },
        ],
      }),
      share('bob', 'Bob', 56667, {
        private_paise: 50000,
        private_items: ['Queen bed'],
        total_paise: 106667,
        paid_paise: 106667,
        settled_paise: 106667,
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
        settled_paise: 30000,
        outstanding_paise: 26666,
        status: 'partial',
        paid_date: '2026-11-08',
        payments: [
          { payment_id: 'pay-chris', payment_date: '2026-11-08', amount_paise: 30000, payment_mode: 'cash', reference: null, note: null },
        ],
      }),
    ],
    collected_paise: 193334,
    collected_count: 2,
    payout_status: 'disbursed',
    disbursed_paise: 220000,
    remaining_disbursement_paise: 0,
    disbursements: [ALICE_SHARE_VIA_KITTY, ALICE_ADVANCE],
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

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname + location.search}</output>
}

/** The page at `path` -- `/?tab=items` opens on that tab -- with the address shown below it. */
function show(payload: Record<string, unknown>, routes: Record<string, unknown> = {}, path = '/') {
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
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <FurnitureRental month={MONTH} />
        <Where />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path === path)

describe('FurnitureRental', () => {
  it('opens on the monthly settlement and moves between tabs through the address', async () => {
    show(overview())
    expect(await screen.findByRole('tablist', { name: 'Furniture Rental' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Monthly Settlement',
      'Rented Items',
      'Subscription & Deposit',
    ])
    expect(screen.getByRole('tab', { name: /Monthly Settlement/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Resident Collection · November 2026' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Provider Payout · Furlenco' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Rented Items' })).toBeNull()

    const expectations: Array<[RegExp, string, string]> = [
      [/Rented Items/, '/?tab=items', 'Rented Items'],
      [/Subscription & Deposit/, '/?tab=subscription', 'Furniture rental'],
    ]
    for (const [name, address, heading] of expectations) {
      await userEvent.click(screen.getByRole('tab', { name }))
      expect(screen.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByLabelText('location')).toHaveTextContent(address)
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: /Resident Collection/ })).toBeNull()
    }
    await userEvent.click(screen.getByRole('tab', { name: /Monthly Settlement/ }))
    expect(screen.getByLabelText('location')).toHaveTextContent(/^\/$/)
  })

  it.each([
    ['items', /Rented Items/],
    ['subscription', /Subscription & Deposit/],
    ['nonsense', /Monthly Settlement/],
  ])('opens on the tab named in the address: %s', async (tab, name) => {
    show(overview(), {}, `/?tab=${tab}`)
    expect(await screen.findByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
  })

  it('has no tabs before furniture rental is set up', async () => {
    show(notSetUp)
    expect(await screen.findByRole('button', { name: 'Set up furniture rental' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).toBeNull()
  })

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
    show(overview(), {}, '/?tab=subscription')
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
    show(overview(), {}, '/?tab=items')
    const table = await screen.findByRole('table', { name: 'Rented items' })
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Item',
      'Provider',
      'Category',
      'Paid by',
      'Monthly rent',
      'Status',
      'Actions',
    ])
    expect(within(table).getByRole('row', { name: /Sofa/ })).toHaveTextContent('Shared₹1,000.00In the flatSince 1 Sep 2026')
    expect(within(table).getByRole('row', { name: /Queen bed/ })).toHaveTextContent('Queen bedFurlencoBedroomBob₹500.00')
    expect(within(table).getByRole('row', { name: /Desk/ })).toHaveTextContent('Awaiting deliveryDue 2 Dec 2026')
  })

  it("splits the month's bill and records it", async () => {
    show(overview())
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    // Before the bill is recorded there is nothing to pay in yet.
    expect(within(shares).getByRole('row', { name: /Bob/ })).toHaveTextContent('Bob₹1,066.67incl. ₹500.00 own———')
    expect(within(shares).getByRole('row', { name: /Chris/ })).toHaveTextContent('Chris₹566.66———')
    expect(within(shares).getByRole('button', { name: 'Pay now for Chris' })).toBeDisabled()
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
    show(overview(), {}, '/?tab=items')
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
      service_id: 'svc-1',
      name: 'Study chair',
      category: 'workstation',
      monthly_rent_paise: 19900,
      allocation_scope: 'resident',
      assigned_resident_id: 'bob',
      delivery_date: null,
      status: 'active',
    })
  })

  it('sums up the collection and the payout to the provider at the top', async () => {
    const { unmount } = show(overview({ month: billed() }))
    expect(await screen.findByText('Internal Collection')).toBeInTheDocument()
    expect(document.body).toHaveTextContent('0 of 3 covered · 0.0%')
    expect(document.body).toHaveTextContent('Provider PayoutUnpaidDue 5 Nov 2026')
    unmount()
    stub?.restore()

    show(overview({ month: paidByAlice() }))
    expect(await screen.findByText('Internal Collection')).toBeInTheDocument()
    expect(document.body).toHaveTextContent('Internal Collection₹1,933.342 of 3 covered · 87.9%')
    expect(document.body).toHaveTextContent('Provider PayoutPaidPaid 5 Nov 2026')
  })

  it('pays the provider from the House Kitty, what it has collected so far', async () => {
    const collecting = billed({ collected_paise: 106667, collected_count: 1 })
    show(overview({ month: collecting }), {
      'POST /api/services/furniture/disburse-payout': overview({
        month: billed({ payout_status: 'partially_disbursed', disbursed_paise: 106667, remaining_disbursement_paise: 113333 }),
      }),
    })
    const panel = (await screen.findByRole('heading', { name: 'Provider Payout · Furlenco' })).closest('section')!
    expect(within(panel).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Provider / Subscription',
      'Total Bill',
      'Paid to Provider',
      'Still Owed',
      'Status',
      'Actions',
    ])
    // Over the provider's avatar and name, as Resident is over each resident's.
    expect(within(panel).getByRole('columnheader', { name: 'Provider / Subscription' })).toHaveClass('text-left')
    expect(within(panel).getByRole('row', { name: /Furlenco/ })).toHaveTextContent(
      'FurlencoCustomer FUR-2291 · 3 items₹2,200.00₹0.00₹2,200.00UnpaidPay Now',
    )

    await userEvent.click(within(panel).getByRole('button', { name: 'Pay now to Furlenco' }))
    const dialog = screen.getByRole('dialog', { name: 'Record Provider Payout' })
    expect(dialog).toHaveTextContent('November 2026 · ₹2,200.00 still owed to Furlenco')
    expect(within(dialog).getByRole('radio', { name: /^House Kitty/ })).toBeChecked()
    expect(within(dialog).queryByRole('combobox', { name: /^Paid by/ })).toBeNull()
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('1066.67')
    const record = within(dialog).getByRole('button', { name: 'Record Payout' })
    expect(record).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/^Reference \/ UTR/), 'UTR-9')
    await userEvent.click(record)

    await waitFor(() => expect(sent('POST', '/api/services/furniture/disburse-payout')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/disburse-payout')!.body).toEqual({
      settlement_month: MONTH,
      service_id: 'svc-1',
      disbursement_mode: 'pool',
      funding_resident_id: null,
      reference: 'UTR-9',
      disbursed_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      amount_paise: 106667,
      notes: null,
    })
    expect(await screen.findByText('Provider payout recorded')).toBeInTheDocument()
    expect(screen.getByText('₹1,133.33 is still owed to Furlenco.')).toBeInTheDocument()
  })

  it("records a resident's advance to the provider", async () => {
    show(overview({ month: billed() }), {
      'POST /api/services/furniture/disburse-payout': overview({ month: paidByAlice() }),
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now to Furlenco' }))
    const dialog = screen.getByRole('dialog', { name: 'Record Provider Payout' })
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('2200.00')
    await userEvent.click(within(dialog).getByRole('radio', { name: /Paid by a resident/ }))
    await userEvent.type(within(dialog).getByLabelText(/^Reference \/ UTR/), 'UTR-4471')
    const record = within(dialog).getByRole('button', { name: 'Record Payout' })
    expect(record).toBeDisabled()
    await waitFor(() => expect(within(dialog).getByRole('option', { name: 'Alice' })).toBeInTheDocument())
    await userEvent.selectOptions(within(dialog).getByRole('combobox', { name: /^Paid by/ }), 'alice')
    await userEvent.click(record)

    await waitFor(() => expect(sent('POST', '/api/services/furniture/disburse-payout')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/disburse-payout')!.body).toMatchObject({
      disbursement_mode: 'resident_advance',
      funding_resident_id: 'alice',
      reference: 'UTR-4471',
      amount_paise: 220000,
    })
  })

  it('opens the payout statement from the provider row', async () => {
    show(overview({ month: paidByAlice() }))
    const panel = (await screen.findByRole('heading', { name: 'Provider Payout · Furlenco' })).closest('section')!
    expect(within(panel).getByRole('button', { name: 'Pay now to Furlenco' })).toBeDisabled()
    await userEvent.click(within(panel).getByRole('row', { name: /Furlenco/ }))
    const statement = screen.getByRole('dialog', { name: 'Provider Payout Statement' })
    expect(statement).toHaveTextContent('Furlenco · November 2026')
    expect(statement).toHaveTextContent('Total Bill₹2,200.00Paid to Provider₹2,200.00Still Owed₹0.00')
    // Her own share went out of the kitty; only the extra is her advance.
    expect(within(statement).getByRole('row', { name: /House Kitty/ })).toHaveTextContent(
      "5 Nov 2026₹566.67House KittyUTR-4471Alice's allotted share via House Kitty",
    )
    expect(within(statement).getByRole('row', { name: /Alice \(advance\)/ })).toHaveTextContent(
      '5 Nov 2026₹1,633.33Alice (advance)UTR-4471',
    )
  })

  it('leaves out the reimbursement card when the House Kitty paid the provider', async () => {
    show(
      overview({
        month: billed({
          payout_status: 'disbursed',
          disbursed_paise: 220000,
          remaining_disbursement_paise: 0,
          disbursements: [
            { ...ALICE_ADVANCE, disbursed_by: 'pool', funding_resident_id: null, funding_resident_name: null, funding_payment_id: null },
          ],
        }),
      }),
    )
    await screen.findByRole('heading', { name: 'Provider Payout · Furlenco' })
    expect(screen.queryByRole('heading', { name: /Reimbursement/ })).toBeNull()
  })

  it('shows who paid the provider, what each flatmate has paid in, and reimburses from the kitty', async () => {
    show(overview({ month: paidByAlice() }), {
      'POST /api/services/furniture/reimburse-from-kitty': overview({ month: paidByAlice() }),
    })
    expect(await screen.findByRole('heading', { name: 'Reimbursement · Alice' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Resident Collection · November 2026' })).toBeInTheDocument()

    const shares = screen.getByRole('table', { name: 'Shares for November 2026' })
    // Rent's six columns: who, their share, paid in, still owed, status, actions.
    expect(within(shares).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Resident',
      'Share',
      'Paid into Kitty',
      'Still owed',
      'Status',
      'Actions',
    ])
    expect(within(shares).getByRole('row', { name: /Alice/ })).toHaveTextContent('Alice₹566.67₹566.67₹0.00PaidPay Now')
    expect(within(shares).getByRole('row', { name: /Bob/ })).toHaveTextContent(
      'Bob₹1,066.67incl. ₹500.00 own₹1,066.67₹0.00PaidPay Now',
    )
    expect(within(shares).getByRole('row', { name: /Chris/ })).toHaveTextContent(
      'Chris₹566.66₹300.00₹266.66Part paidPay Now',
    )

    const card = screen.getByRole('heading', { name: 'Reimbursement · Alice' }).closest('section')!
    expect(card).toHaveTextContent('Total paid₹2,200.00Includes ₹566.67 allotted share')
    expect(card).toHaveTextContent('Extra (reimbursable)₹1,633.33To be reimbursed via House Kitty')
    expect(card).toHaveTextContent('Reimbursed so far₹0.00₹1,633.33 still owed')
    expect(card).toHaveTextContent('Kitty cash available₹1,366.67Collected from other flatmates')

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

  it("opens a resident's furniture statement from their row, by click or by keyboard", async () => {
    show(overview({ month: paidByAlice() }))
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    await userEvent.click(within(shares).getByRole('row', { name: /Bob/ }))
    const bob = screen.getByRole('dialog', { name: 'Furniture Statement' })
    expect(bob).toHaveTextContent('Bob · November 2026')
    expect(bob).toHaveTextContent(
      'Share₹1,066.67incl. ₹500.00 own itemsPaid into Kitty₹1,066.67Still Owed₹0.00',
    )
    expect(within(bob).getByRole('heading', { name: 'Payment History' })).toBeInTheDocument()
    expect(within(bob).getByText('Paid')).toBeInTheDocument()
    const history = within(bob).getByRole('table', { name: "Payments towards Bob's furniture share" })
    expect(within(history).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Date',
      'Amount',
      'Mode',
      'Reference / UTR',
    ])
    expect(within(history).getByRole('row', { name: /UPI-88/ })).toHaveTextContent('8 Nov 2026₹1,066.67UPIUPI-88')
    // Closed by its own ✕: there is no footer of actions, exactly matching Rent.
    expect(within(bob).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      'Close dialog',
    ])
    await userEvent.click(within(bob).getByRole('button', { name: 'Close dialog' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    // Alice paid Furlenco herself: her own share went through the kitty, the rest is extra.
    await userEvent.click(within(shares).getByRole('row', { name: /Alice/ }))
    const alice = screen.getByRole('dialog', { name: 'Furniture Statement' })
    expect(alice).toHaveTextContent('Paid into Kitty₹566.67Still Owed₹0.00')
    expect(alice).toHaveTextContent(
      'Alice paid an extra ₹1,633.33 beyond their allotted share of ₹566.67, which will be reimbursed via the House Kitty.',
    )
    const aliceHistory = within(alice).getByRole('table', { name: "Payments towards Alice's furniture share" })
    expect(within(aliceHistory).getByRole('row', { name: /UTR-4471/ })).toHaveTextContent('5 Nov 2026₹566.67—UTR-4471')
    expect(alice).not.toHaveTextContent(/Direct to/)
    await userEvent.click(within(alice).getByRole('button', { name: 'Close dialog' }))

    within(shares).getByRole('row', { name: /Chris/ }).focus()
    await userEvent.keyboard('{Enter}')
    const chris = screen.getByRole('dialog', { name: 'Furniture Statement' })
    expect(chris).toHaveTextContent('Chris · November 2026')
    expect(chris).toHaveTextContent('Share₹566.66Paid into Kitty₹300.00Still Owed₹266.66')
    expect(chris).not.toHaveTextContent('Shared items split')
    expect(within(chris).getByRole('row', { name: /Cash/ })).toHaveTextContent('8 Nov 2026₹300.00Cash—')
    expect(within(chris).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      'Close dialog',
    ])
    await userEvent.click(within(chris).getByRole('button', { name: 'Close dialog' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('records a payment into the House Kitty against the share', async () => {
    show(overview({ month: paidByAlice() }), {
      'POST /api/services/furniture/record-payment': overview({ month: paidByAlice() }),
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now for Chris' }))
    expect(screen.queryByRole('dialog', { name: 'Furniture Statement' })).toBeNull()
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
    const provider = screen.getByRole('heading', { name: 'Provider Payout · Furlenco' }).closest('section')!
    await userEvent.click(within(provider).getByRole('row', { name: /Furlenco/ }))
    expect(screen.getByRole('dialog', { name: 'Provider Payout Statement' })).not.toHaveTextContent(/pool/i)
  })

  it('offers the House Kitty, never the pool, as where a payout came from', async () => {
    show(overview({ month: billed() }))
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now to Furlenco' }))
    const dialog = screen.getByRole('dialog', { name: 'Record Provider Payout' })
    expect(dialog).toHaveTextContent('House Kitty')
    expect(document.body).not.toHaveTextContent(/pool/i)
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

  describe('with several rental providers', () => {
    const furlenco = overview()
    const rentomojo = {
      service: {
        ...furlenco.service,
        service_id: 'svc-2',
        provider_name: 'Rentomojo',
        billing_day_of_month: 10,
      },
      config: { ...furlenco.config, provider_name: 'Rentomojo', customer_id: 'RM-1', deposit_paid_paise: 200000 },
      monthly_total_paise: 50000,
      active_item_count: 1,
      next_billing_date: '2026-12-10',
      deposit_held_paise: 200000,
    }
    const services = [
      {
        service: furlenco.service,
        config: furlenco.config,
        monthly_total_paise: 170000,
        active_item_count: 2,
        next_billing_date: '2026-12-05',
        deposit_held_paise: 500000,
      },
      rentomojo,
    ]
    const bed = item({
      item_id: 'furn-bed',
      name: 'Queen bed',
      category: 'bedroom',
      monthly_rent_paise: 50000,
      allocation_scope: 'resident',
      assigned_resident_id: 'bob',
      assigned_resident_name: 'Bob',
      service_id: 'svc-2',
      provider_name: 'Rentomojo',
    })
    const payout = (overrides: Record<string, unknown>) => ({
      service_id: 'svc-1',
      display_name: 'Furniture rental',
      provider_name: 'Furlenco',
      customer_id: 'FUR-2291',
      subscription_reference: null,
      billing_day_of_month: 5,
      item_count: 2,
      total_paise: 170000,
      disbursed_paise: 170000,
      remaining_disbursement_paise: 0,
      payout_status: 'disbursed',
      disbursements: [],
      ...overrides,
    })
    // Furlenco has been paid; Rentomojo has not.
    const month = billed({
      payout_status: 'partially_disbursed',
      disbursed_paise: 170000,
      remaining_disbursement_paise: 50000,
      provider_payouts: [
        payout({}),
        payout({
          service_id: 'svc-2',
          provider_name: 'Rentomojo',
          customer_id: 'RM-1',
          item_count: 1,
          total_paise: 50000,
          disbursed_paise: 0,
          remaining_disbursement_paise: 50000,
          payout_status: 'pending',
        }),
      ],
    })
    const both = overview({ services, items: [item(), item({ item_id: 'furn-fridge', name: 'Fridge' }), bed], month })

    it('pays each provider its own part of the bill', async () => {
      show(both, { 'POST /api/services/furniture/disburse-payout': both })
      const panel = (await screen.findByRole('heading', { name: 'Provider Payout' })).closest('section')!
      expect(within(panel).getByRole('row', { name: /Furlenco/ })).toHaveTextContent(
        'FurlencoCustomer FUR-2291 · 2 items₹1,700.00₹1,700.00₹0.00PaidPay Now',
      )
      expect(within(panel).getByRole('row', { name: /Rentomojo/ })).toHaveTextContent(
        'RentomojoCustomer RM-1 · 1 item₹500.00₹0.00₹500.00UnpaidPay Now',
      )
      expect(within(panel).getByRole('button', { name: 'Pay now to Furlenco' })).toBeDisabled()

      await userEvent.click(within(panel).getByRole('button', { name: 'Pay now to Rentomojo' }))
      const dialog = screen.getByRole('dialog', { name: 'Record Provider Payout' })
      expect(dialog).toHaveTextContent('November 2026 · ₹500.00 still owed to Rentomojo')
      expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('500.00')
      await userEvent.type(within(dialog).getByLabelText(/^Reference \/ UTR/), 'UTR-R1')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payout' }))
      await waitFor(() => expect(sent('POST', '/api/services/furniture/disburse-payout')).toBeDefined())
      expect(sent('POST', '/api/services/furniture/disburse-payout')!.body).toMatchObject({
        service_id: 'svc-2',
        disbursement_mode: 'pool',
        amount_paise: 50000,
      })

      await userEvent.click(within(panel).getByRole('row', { name: /Furlenco/ }))
      expect(screen.getByRole('dialog', { name: 'Provider Payout Statement' })).toHaveTextContent(
        'Furlenco · November 2026Total Bill₹1,700.00Paid to Provider₹1,700.00Still Owed₹0.00',
      )
    })

    it("shows each item's provider, and asks which provider a new item is from", async () => {
      show(both, { 'POST /api/services/furniture/items': bed }, '/?tab=items')
      const table = await screen.findByRole('table', { name: 'Rented items' })
      expect(within(table).getByRole('row', { name: /Sofa/ })).toHaveTextContent('SofaFurlenco')
      expect(within(table).getByRole('row', { name: /Queen bed/ })).toHaveTextContent('Queen bedRentomojo')

      const panel = screen.getByRole('heading', { name: 'Rented Items' }).closest('section')!
      await userEvent.click(within(panel).getByRole('button', { name: 'Add item' }))
      const dialog = screen.getByRole('dialog', { name: 'Add a rented item' })
      const provider = within(dialog).getByLabelText(/^Rental provider/)
      expect(within(provider).getAllByRole('option').map((option) => option.textContent)).toEqual([
        'Furlenco',
        'Rentomojo',
      ])
      await userEvent.selectOptions(provider, 'svc-2')
      await userEvent.type(within(dialog).getByLabelText(/^Item/), 'Desk')
      await userEvent.type(within(dialog).getByLabelText(/Monthly rent/), '300')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Add item' }))
      await waitFor(() => expect(sent('POST', '/api/services/furniture/items')).toBeDefined())
      expect(sent('POST', '/api/services/furniture/items')!.body).toMatchObject({ service_id: 'svc-2', name: 'Desk' })
    })

    it('keeps a subscription for each provider, and adds another', async () => {
      show(both, {}, '/?tab=subscription')
      const furlencoCard = (await screen.findByRole('heading', { name: 'Furniture rental · Furlenco' })).closest('section')!
      expect(furlencoCard).toHaveTextContent('Deposit held₹5,000.00')
      const rentomojoCard = screen.getByRole('heading', { name: 'Furniture rental · Rentomojo' }).closest('section')!
      expect(rentomojoCard).toHaveTextContent('Monthly total₹500.00')
      expect(rentomojoCard).toHaveTextContent('Deposit held₹2,000.00')

      await userEvent.click(screen.getByRole('button', { name: 'Add rental provider' }))
      const dialog = screen.getByRole('dialog', { name: 'Add rental provider' })
      await userEvent.selectOptions(within(dialog).getByLabelText('Provider'), 'Rentomojo')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(sent('POST', '/api/services/furniture/setup')).toBeDefined())
      expect(sent('POST', '/api/services/furniture/setup')!.body).toMatchObject({
        provider_name: 'Rentomojo',
        create_new: true,
      })
    })
  })

  it('records the day an item went back', async () => {
    show(overview(), {}, '/?tab=items')
    await userEvent.click(await screen.findByRole('button', { name: 'Return Sofa' }))
    const dialog = screen.getByRole('dialog', { name: 'Return Sofa' })
    expect(dialog).toHaveTextContent('Nothing is prorated')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record return' }))
    await waitFor(() => expect(sent('PATCH', '/api/services/furniture/items/furn-sofa')).toBeDefined())
    expect(sent('PATCH', '/api/services/furniture/items/furn-sofa')!.body).toMatchObject({ status: 'returned' })
  })
})
