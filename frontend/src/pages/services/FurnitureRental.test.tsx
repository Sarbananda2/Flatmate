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
    { resident_id: 'alice', display_name: 'Alice', taking_part: true, common_share_paise: 56667, private_paise: 0, private_items: [], total_paise: 56667 },
    { resident_id: 'bob', display_name: 'Bob', taking_part: true, common_share_paise: 56667, private_paise: 50000, private_items: ['Queen bed'], total_paise: 106667 },
    { resident_id: 'chris', display_name: 'Chris', taking_part: true, common_share_paise: 56666, private_paise: 0, private_items: [], total_paise: 56666 },
  ],
  excluded_residents: [],
  blocked_reason: null,
  expense: null,
  statement_status: 'draft',
  can_generate: true,
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
    expect(await screen.findByText('Billed')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View in Expenses' })).toHaveAttribute('href', '/expenses')
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
