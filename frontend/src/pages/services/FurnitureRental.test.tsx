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
    expect(screen.getByRole('heading', { name: 'Resident Collection' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Provider Payout' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Rented Items' })).toBeNull()

    const expectations: Array<[RegExp, string, string]> = [
      [/Rented Items/, '/?tab=items', 'Rented Items'],
      [/Subscription & Deposit/, '/?tab=subscription', 'Rental Subscriptions'],
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

  it('sums up every subscription and deposit across the household', async () => {
    show(overview(), {}, '/?tab=subscription')
    await screen.findByRole('heading', { name: 'Rental Subscriptions' })
    const stat = (label: string) => screen.getByText(label).parentElement!
    expect(stat('Active Providers')).toHaveTextContent('Active Providers1Furlenco')
    // In the flat now: the sofa, the fridge and Bob's bed. The desk is not delivered yet.
    expect(stat('Monthly Commitment')).toHaveTextContent('₹2,200.003 items (2 shared · 1 own)')
    expect(stat('Total Deposit Held')).toHaveTextContent('₹5,000.00₹5,000.00 paid across 1 provider')
    expect(stat('Next Billing Date')).toHaveTextContent('5 Dec 2026Furlenco · Day 5 each month')
  })

  it('shows each subscription as a card, with its actions in one menu', async () => {
    show(overview(), {}, '/?tab=subscription')
    const card = (await screen.findByRole('heading', { name: 'Furlenco' })).closest('article')!
    expect(card).toHaveTextContent('Customer FUR-2291')
    expect(within(card).getByText('Active')).toBeInTheDocument()
    // The header names the provider, so the figures do not repeat it.
    expect(within(card).queryByText('Provider')).toBeNull()
    expect(card).toHaveTextContent('Monthly total₹2,200.00')
    expect(card).toHaveTextContent('3 items in the flat')
    expect(card).toHaveTextContent('5 Dec 2026')
    expect(card).toHaveTextContent('Deposit held₹5,000.00')
    await userEvent.click(within(card).getByRole('button', { name: 'More options' }))
    expect(screen.getAllByRole('menuitem').map((option) => option.textContent)).toEqual([
      'Edit subscription',
      'End subscription',
    ])
    // Choosing from the card's menu is not also opening the card.
    await userEvent.click(screen.getByRole('menuitem', { name: 'Edit subscription' }))
    expect(screen.getByRole('dialog', { name: 'Edit subscription' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Furlenco Subscription' })).toBeNull()
  })

  it("opens a subscription's details, its deposit and what is rented from it", async () => {
    show(overview(), {}, '/?tab=subscription')
    await userEvent.click((await screen.findByRole('heading', { name: 'Furlenco' })).closest('article')!)
    const dialog = screen.getByRole('dialog', { name: 'Furlenco Subscription' })
    expect(dialog).toHaveTextContent('Customer FUR-2291')
    expect(dialog).toHaveTextContent('Monthly Rent₹2,200.003 active items')
    expect(dialog).toHaveTextContent('Next Billing5 Dec 2026Day 5 each month')
    expect(dialog).toHaveTextContent('Deposit Paid₹5,000.00No refund recorded yet')
    expect(dialog).toHaveTextContent('Deposit Held₹5,000.00Held by the provider')
    const account = within(dialog).getByLabelText('Account and contract details')
    expect(account).toHaveTextContent('Customer IDFUR-2291')
    expect(account).toHaveTextContent('Subscription Reference—')
    const rented = within(dialog).getByRole('table', { name: 'Items rented from Furlenco' })
    expect(within(rented).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Item',
      'Paid by',
      'Monthly rent',
      'Status',
    ])
    expect(within(rented).getByRole('row', { name: /Sofa/ })).toHaveTextContent('SofaShared₹1,000.00In the flat')
    expect(within(rented).getByRole('row', { name: /Queen bed/ })).toHaveTextContent('Queen bedBob₹500.00In the flat')
    expect(within(rented).getByRole('row', { name: /Desk/ })).toHaveTextContent('Awaiting delivery')

    // Its actions are there too.
    await userEvent.click(within(dialog).getByRole('button', { name: 'End subscription' }))
    expect(screen.queryByRole('dialog', { name: 'Furlenco Subscription' })).toBeNull()
    expect(screen.getByRole('dialog', { name: /End/ })).toBeInTheDocument()
  })

  it('opens a subscription card from the keyboard', async () => {
    show(overview(), {}, '/?tab=subscription')
    const card = (await screen.findByRole('heading', { name: 'Furlenco' })).closest('article')!
    card.focus()
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('dialog', { name: 'Furlenco Subscription' })).toBeInTheDocument()
  })

  it('lists each item with who pays for it and where it is', async () => {
    show(overview(), {}, '/?tab=items')
    const table = await screen.findByRole('table', { name: 'Rented items' })
    // How the month's bill is split sits above the items it is split from.
    expect(screen.getByText('Shared items').parentElement).toHaveTextContent('₹1,700.00Split equally')
    expect(screen.getByText('3 taking part · 2 paise over')).toBeInTheDocument()
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Item',
      'Paid by',
      'Monthly rent',
      'Status',
      'Actions',
    ])
    expect(within(table).getByRole('columnheader', { name: 'Item' })).toHaveClass('text-left')
    expect(within(table).getByRole('row', { name: /Sofa/ })).toHaveTextContent(
      'SofaFurlencoShared₹1,000.00In the flat',
    )
    expect(within(table).getByRole('row', { name: /Queen bed/ })).toHaveTextContent(
      'Queen bedFurlencoBob₹500.00In the flat',
    )
    // Dates belong to the item's details, not the row.
    expect(within(table).getByRole('row', { name: /Desk/ })).toHaveTextContent('Awaiting delivery')
    expect(within(table).getByRole('row', { name: /Desk/ })).not.toHaveTextContent('2 Dec 2026')
  })

  it("opens an item's details from its row, with its dates and actions", async () => {
    show(overview(), {}, '/?tab=items')
    const table = await screen.findByRole('table', { name: 'Rented items' })
    await userEvent.click(within(table).getByRole('row', { name: /Queen bed/ }))
    const dialog = screen.getByRole('dialog', { name: 'Queen bed' })
    expect(dialog).toHaveTextContent('Queen bedFurlenco')
    expect(dialog).toHaveTextContent('Monthly Rent₹500.00No proration — billed in full')
    expect(dialog).toHaveTextContent('Paid ByBobPersonal item')
    expect(dialog).toHaveTextContent('StatusIn the flat')
    const details = within(dialog).getByLabelText('Item details')
    expect(details).toHaveTextContent('Rental ProviderFurlenco')
    expect(details).not.toHaveTextContent('Category')
    expect(details).toHaveTextContent('Delivery Date1 Sep 2026')
    expect(details).toHaveTextContent('Return DateStill in the flat')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Edit item' }))
    expect(screen.queryByRole('dialog', { name: 'Queen bed' })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Edit Queen bed' })).toBeInTheDocument()
  })

  it('opens an item from the keyboard, and returns one from its details', async () => {
    show(overview(), {}, '/?tab=items')
    const table = await screen.findByRole('table', { name: 'Rented items' })
    within(table).getByRole('row', { name: /Desk/ }).focus()
    await userEvent.keyboard('{Enter}')
    const desk = screen.getByRole('dialog', { name: 'Desk' })
    expect(within(desk).getByLabelText('Item details')).toHaveTextContent('Return DateNot delivered yet')
    await userEvent.click(within(desk).getByRole('button', { name: 'Return item' }))
    expect(screen.getByRole('dialog', { name: 'Return Desk' })).toBeInTheDocument()
  })

  it("edits or returns an item from its row's menu, without opening it", async () => {
    show(overview({ items: [...items, item({ item_id: 'furn-lamp', name: 'Lamp', status: 'returned', return_date: '2026-10-31' })] }), {}, '/?tab=items')
    const table = await screen.findByRole('table', { name: 'Rented items' })
    await userEvent.click(within(within(table).getByRole('row', { name: /Sofa/ })).getByRole('button', { name: 'More options' }))
    expect(screen.getAllByRole('menuitem').map((option) => option.textContent)).toEqual(['Edit item', 'Return item'])
    await userEvent.click(screen.getByRole('menuitem', { name: 'Edit item' }))
    expect(screen.getByRole('dialog', { name: 'Edit Sofa' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Sofa' })).toBeNull()
    // A returned item can only be edited: its one action is a button of its own.
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Edit Sofa' })).getByRole('button', { name: 'Cancel' }))
    const lamp = within(table).getByRole('row', { name: /Lamp/ })
    expect(within(lamp).queryByRole('button', { name: 'More options' })).toBeNull()
    await userEvent.click(within(lamp).getByRole('button', { name: 'Edit item' }))
    expect(screen.getByRole('dialog', { name: 'Edit Lamp' })).toBeInTheDocument()
  })

  it("shows the month's split before it begins, with no bill to record by hand", async () => {
    show(overview({ month: bill({ bill_state: 'upcoming', can_generate: false }) }))
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    // Before the month bills there is nothing to pay in yet.
    expect(within(shares).getByRole('row', { name: /Bob/ })).toHaveTextContent('Bob₹1,066.67incl. ₹500.00 own———')
    expect(within(shares).getByRole('row', { name: /Chris/ })).toHaveTextContent('Chris₹566.66———')
    expect(within(shares).getByRole('button', { name: 'Pay now for Chris' })).toBeDisabled()
    // The split itself is on the Rented Items tab; this one is about settling it.
    expect(screen.queryByText('Shared items')).toBeNull()
    expect(screen.getByText('Internal Collection')).toBeInTheDocument()
    // An open month bills by itself, and follows its items: there is nothing to record.
    expect(screen.queryByRole('button', { name: /Record November 2026 bill/ })).toBeNull()
    expect(screen.queryByText('Not billed yet.')).toBeNull()
    expect(sent('POST', '/api/services/furniture/generate-cycle')).toBeUndefined()
  })

  it('shows a live bill with its statuses, or says why the month cannot be billed', async () => {
    const { unmount } = show(overview({ month: billed({ bill_state: 'live' }) }))
    // The recorded bill shows each share's status instead of a footer saying it is billed.
    const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
    expect(within(shares).getByRole('columnheader', { name: 'Status' })).toBeInTheDocument()
    expect(within(shares).getByRole('button', { name: 'Pay now for Chris' })).toBeEnabled()
    expect(screen.queryByRole('link', { name: 'View in Expenses' })).toBeNull()
    expect(screen.queryByText('Billed')).toBeNull()
    expect(screen.queryByRole('button', { name: /Record November 2026 bill/ })).toBeNull()
    unmount()
    stub?.restore()

    show(
      overview({
        month: bill({
          blocked_reason: 'Bob has a rented item of their own but is not taking part this month.',
          can_generate: false,
          bill_state: 'not_billed',
        }),
      }),
    )
    expect(await screen.findByText(/Bob has a rented item of their own/)).toHaveAttribute('role', 'status')
    expect(screen.queryByRole('button', { name: /Record November 2026 bill/ })).toBeNull()
  })

  it('adds an item charged to one resident', async () => {
    show(overview(), {}, '/?tab=items')
    const panel = (await screen.findByRole('heading', { name: 'Rented Items' })).closest('section')!
    await userEvent.click(within(panel).getByRole('button', { name: 'Add item' }))
    const dialog = screen.getByRole('dialog', { name: 'Add a rented item' })
    const add = within(dialog).getByRole('button', { name: 'Add item' })
    expect(add).toBeDisabled()
    // The item's category is not asked for: the backend files it as "other".
    expect(within(dialog).queryByLabelText('Category')).toBeNull()
    await userEvent.type(within(dialog).getByLabelText(/^Item/), 'Study chair')
    await userEvent.type(within(dialog).getByLabelText(/Monthly rent/), '199')
    await waitFor(() => expect(within(dialog).getByRole('option', { name: 'Bob' })).toBeInTheDocument())
    await userEvent.selectOptions(within(dialog).getByLabelText('Paid by'), 'bob')
    await userEvent.click(add)

    await waitFor(() => expect(sent('POST', '/api/services/furniture/items')).toBeDefined())
    expect(sent('POST', '/api/services/furniture/items')!.body).toEqual({
      service_id: 'svc-1',
      name: 'Study chair',
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
    const panel = (await screen.findByRole('heading', { name: 'Provider Payout' })).closest('section')!
    expect(within(panel).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Provider',
      'Total Bill',
      'Paid to Provider',
      'Still Owed',
      'Status',
      'Actions',
    ])
    // Over the provider's avatar and name, as Resident is over each resident's.
    expect(within(panel).getByRole('columnheader', { name: 'Provider' })).toHaveClass('text-left')
    expect(within(panel).getByRole('row', { name: /Furlenco/ })).toHaveTextContent(
      'Furlenco₹2,200.00₹0.00₹2,200.00UnpaidPay Now',
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
    const panel = (await screen.findByRole('heading', { name: 'Provider Payout' })).closest('section')!
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
    await screen.findByRole('heading', { name: 'Provider Payout' })
    expect(screen.queryByRole('heading', { name: /Reimbursement/ })).toBeNull()
  })

  it('shows who paid the provider, what each flatmate has paid in, and reimburses from the kitty', async () => {
    show(overview({ month: paidByAlice() }), {
      'POST /api/services/furniture/reimburse-from-kitty': overview({ month: paidByAlice() }),
    })
    expect(await screen.findByRole('heading', { name: 'Reimbursement' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Resident Collection' })).toBeInTheDocument()

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

    const card = screen.getByRole('heading', { name: 'Reimbursement' }).closest('section')!
    const owed = within(card).getByRole('table', { name: 'Reimbursements from the House Kitty' })
    expect(within(owed).getByRole('row', { name: /Alice/ })).toHaveTextContent(
      'Alice₹1,633.33₹0.00₹1,633.33UnpaidPay Now',
    )

    await userEvent.click(within(card).getByRole('button', { name: 'Reimburse from Kitty to Alice' }))
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

  it("opens a resident's reimbursement statement from their row", async () => {
    show(overview({ month: paidByAlice() }))
    const card = (await screen.findByRole('heading', { name: 'Reimbursement' })).closest('section')!
    await userEvent.click(within(card).getByRole('row', { name: /Alice/ }))
    const statement = screen.getByRole('dialog', { name: 'Reimbursement Statement' })
    expect(statement).toHaveTextContent('Alice · November 2026')
    expect(statement).toHaveTextContent('Total Paid₹2,200.00Extra Paid')
    expect(statement).toHaveTextContent('Extra Paid₹1,633.33Still Owed')
    expect(statement).toHaveTextContent('Still Owed₹1,633.33Kitty Cash Available')
    expect(statement).toHaveTextContent('Kitty Cash Available₹1,366.67Reimbursement History')
    const history = within(statement).getByRole('region', { name: 'Reimbursement history' })
    expect(history).toHaveTextContent('Unpaid')
    expect(history).toHaveTextContent('No reimbursements recorded yet for this month.')
    // Nothing is offered for deleting unless the month is open.
    expect(within(statement).queryByRole('button', { name: /Delete/ })).toBeNull()
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
    expect(document.body).toHaveTextContent('Paid from Kitty')
    expect(document.body).not.toHaveTextContent(/pool/i)
    for (const open of ['Pay now for Chris', 'Reimburse from Kitty to Alice']) {
      await userEvent.click(screen.getByRole('button', { name: open }))
      expect(document.body).not.toHaveTextContent(/pool/i)
      await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    }
    const provider = screen.getByRole('heading', { name: 'Provider Payout' }).closest('section')!
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
    const card = (await screen.findByRole('heading', { name: 'Reimbursement' })).closest('section')!
    // Paid back in full: the button stays on the row, switched off.
    expect(within(card).getByRole('row', { name: /Alice/ })).toHaveTextContent(
      'Alice₹1,633.33₹1,633.33₹0.00PaidPay Now',
    )
    expect(within(card).getByRole('button', { name: 'Reimburse from Kitty to Alice' })).toBeDisabled()
  })

  describe('deleting what was recorded by mistake', () => {
    const deleted = () => stub!.calls.find((call) => call.method === 'DELETE')
    const open = () => overview({ month: paidByAlice({ transactions_open: true }) })

    it("deletes a resident's payment from their statement while the month is open", async () => {
      show(open(), { 'DELETE /api/services/furniture/payments/pay-bob': open() })
      const shares = await screen.findByRole('table', { name: 'Shares for November 2026' })
      await userEvent.click(within(shares).getByRole('row', { name: /Bob/ }))
      const statement = await screen.findByRole('dialog', { name: 'Furniture Statement' })
      await userEvent.click(within(statement).getByRole('button', { name: 'Delete the ₹1,066.67 payment of 8 Nov 2026' }))
      const confirm = screen.getByRole('dialog', { name: 'Delete this payment?' })
      expect(confirm).toHaveTextContent("Bob's ₹1,066.67 payment of 8 Nov 2026.")
      await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
      await waitFor(() => expect(deleted()!.path).toBe('/api/services/furniture/payments/pay-bob'))
    })

    it('deletes a payout to the provider from its statement', async () => {
      show(open(), { 'DELETE /api/services/furniture/disbursements/svcdisb-1': open() })
      const panel = (await screen.findByRole('heading', { name: 'Provider Payout' })).closest('section')!
      await userEvent.click(within(panel).getByRole('row', { name: /Furlenco/ }))
      const statement = await screen.findByRole('dialog', { name: 'Provider Payout Statement' })
      await userEvent.click(within(statement).getByRole('button', { name: 'Delete the ₹1,633.33 payout of 5 Nov 2026' }))
      const confirm = screen.getByRole('dialog', { name: 'Delete this payout?' })
      expect(confirm).toHaveTextContent('₹1,633.33 paid to Furlenco on 5 Nov 2026 by Alice.')
      await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
      await waitFor(() => expect(deleted()!.path).toBe('/api/services/furniture/disbursements/svcdisb-1'))
    })

    it('deletes a reimbursement from its statement', async () => {
      const refund = { payment_id: 'furniture-1-reimbursement_1', amount_paise: 100000, payment_date: '2026-11-09', reference: 'UPI-R1' }
      const owed = { ...OWED_TO_ALICE, reimbursed_so_far_paise: 100000, pending_reimbursement_paise: 63333, reimbursements: [refund] }
      const month = overview({ month: paidByAlice({ transactions_open: true, reimbursement: owed, reimbursements: [owed] }) })
      show(month, { 'DELETE /api/services/furniture/payments/furniture-1-reimbursement_1': month })
      const card = (await screen.findByRole('heading', { name: 'Reimbursement' })).closest('section')!
      expect(within(card).getByRole('row', { name: /Alice/ })).toHaveTextContent('Part paid')
      await userEvent.click(within(card).getByRole('row', { name: /Alice/ }))
      const statement = screen.getByRole('dialog', { name: 'Reimbursement Statement' })
      await userEvent.click(within(statement).getByRole('button', { name: 'Delete the ₹1,000.00 reimbursement of 9 Nov 2026' }))
      const confirm = screen.getByRole('dialog', { name: 'Delete this reimbursement?' })
      expect(confirm).toHaveTextContent('₹1,000.00 paid back from the kitty to Alice on 9 Nov 2026.')
      await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
      await waitFor(() =>
        expect(deleted()!.path).toBe('/api/services/furniture/payments/furniture-1-reimbursement_1'),
      )
    })

    it('offers no delete once the month is finalized', async () => {
      show(overview({ month: paidByAlice({ transactions_open: false }) }))
      const panel = (await screen.findByRole('heading', { name: 'Provider Payout' })).closest('section')!
      await userEvent.click(within(panel).getByRole('row', { name: /Furlenco/ }))
      const statement = await screen.findByRole('dialog', { name: 'Provider Payout Statement' })
      expect(within(statement).queryByRole('button', { name: /Delete/ })).toBeNull()
    })
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
        'Furlenco₹1,700.00₹1,700.00₹0.00PaidPay Now',
      )
      expect(within(panel).getByRole('row', { name: /Rentomojo/ })).toHaveTextContent(
        'Rentomojo₹500.00₹0.00₹500.00UnpaidPay Now',
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
      const panel = (await screen.findByRole('heading', { name: 'Rental Subscriptions' })).closest('section')!
      const furlencoCard = within(panel).getByRole('heading', { name: 'Furniture rental · Furlenco' }).closest('article')!
      expect(furlencoCard).toHaveTextContent('Deposit held₹5,000.00')
      const rentomojoCard = within(panel).getByRole('heading', { name: 'Furniture rental · Rentomojo' }).closest('article')!
      expect(rentomojoCard).toHaveTextContent('Monthly total₹500.00')
      expect(rentomojoCard).toHaveTextContent('Deposit held₹2,000.00')
      expect(screen.getByText('Active Providers').parentElement).toHaveTextContent('2Furlenco, Rentomojo')
      expect(screen.getByText('Total Deposit Held').parentElement).toHaveTextContent(
        '₹7,000.00 paid across 2 providers',
      )

      // Each card has its own menu; adding a provider is the + in the panel's header.
      expect(within(rentomojoCard).getByRole('button', { name: 'More options' })).toBeInTheDocument()
      const add = within(panel).getByRole('button', { name: 'Add rental provider' })
      expect(furlencoCard).not.toContainElement(add)
      expect(rentomojoCard).not.toContainElement(add)
      await userEvent.click(add)
      const dialog = screen.getByRole('dialog', { name: 'Add rental provider' })
      await userEvent.selectOptions(within(dialog).getByLabelText('Provider'), 'Rentomojo')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(sent('POST', '/api/services/furniture/setup')).toBeDefined())
      expect(sent('POST', '/api/services/furniture/setup')!.body).toMatchObject({
        provider_name: 'Rentomojo',
        create_new: true,
      })
    })

    it("lists in a provider's details only what is rented from it", async () => {
      show(both, {}, '/?tab=subscription')
      await userEvent.click(
        (await screen.findByRole('heading', { name: 'Furniture rental · Rentomojo' })).closest('article')!,
      )
      const dialog = screen.getByRole('dialog', { name: 'Rentomojo Subscription' })
      expect(dialog).toHaveTextContent('Deposit Paid₹2,000.00')
      const rented = within(dialog).getByRole('table', { name: 'Items rented from Rentomojo' })
      expect(within(rented).getAllByRole('row').slice(1).map((row) => row.querySelector('th')!.textContent)).toEqual([
        'Queen bed',
      ])
      await userEvent.click(within(dialog).getByRole('button', { name: 'Edit subscription' }))
      expect(screen.getByRole('dialog', { name: 'Edit subscription' })).toBeInTheDocument()
    })
  })

  it('records the day an item went back', async () => {
    show(overview(), {}, '/?tab=items')
    const table = await screen.findByRole('table', { name: 'Rented items' })
    await userEvent.click(within(within(table).getByRole('row', { name: /Sofa/ })).getByRole('button', { name: 'More options' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Return item' }))
    const dialog = screen.getByRole('dialog', { name: 'Return Sofa' })
    expect(dialog).toHaveTextContent('Nothing is prorated')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record return' }))
    await waitFor(() => expect(sent('PATCH', '/api/services/furniture/items/furn-sofa')).toBeDefined())
    expect(sent('PATCH', '/api/services/furniture/items/furn-sofa')!.body).toMatchObject({ status: 'returned' })
  })
})
