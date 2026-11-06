/**
 * The umbrella hub only summarises: these tests check that the live cards show
 * what the backend returned, that the planned services stay inert, and that one
 * card failing leaves the others standing.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import { ServiceCardErrorBoundary, ServicesHub } from './ServicesHub'
import { installFetchStub } from '../../test/server'

const MONTH = '2026-10'

const config = {
  config_id: 'lease-1',
  landlord_name: 'Kumar Yadav',
  monthly_rent_paise: 2850000,
  due_day_of_month: 5,
}

let stub: ReturnType<typeof installFetchStub> | null = null

/** One line of a card's facts, label and value together. */
const fact = (card: HTMLElement, label: string) => within(card).getByText(label).parentElement!

afterEach(() => {
  stub?.restore()
  stub = null
})

const noDeposits = {
  is_lease_configured: false,
  master_deposit_paise: null,
  funding_mode: null,
  total_slots_count: 0,
  vacant_slots_count: 0,
  total_funded_paise: 0,
}

const noRooms = {
  items: [],
  total: 0,
  summary: {
    total_rooms: 0,
    occupied_rooms: 0,
    vacant_rooms: 0,
    with_attached_washroom: 0,
    with_balcony: 0,
  },
}

const noContacts = { items: [], total: 0, roles_on_file: [] }
const noDocuments = { items: [], total: 0, total_bytes: 0, categories_on_file: [] }
const noFurniture = {
  service: null,
  config: null,
  items: [],
  month: null,
  monthly_total_paise: 0,
  active_item_count: 0,
  next_billing_date: null,
  deposit_held_paise: 0,
}

const noWater = { service: null, config: null, month: null, next_billing_date: null }
const noBroadband = { service: null, config: null, month: null, next_billing_date: null }
const noHouseHelp = {
  service: null,
  active_worker_count: 0,
  saved: false,
  total_paise: 0,
  expense: null,
  shares: [],
  collected_count: 0,
  disbursed_paise: 0,
}

function renderHub(
  overview: { rent: unknown; electricity: unknown },
  deposits: Record<string, unknown> = noDeposits,
  rooms: Record<string, unknown> = noRooms,
  contacts: Record<string, unknown> = noContacts,
  documents: Record<string, unknown> = noDocuments,
  furniture: Record<string, unknown> = noFurniture,
  water: Record<string, unknown> = noWater,
  househelp: Record<string, unknown> = noHouseHelp,
  broadband: Record<string, unknown> = noBroadband,
) {
  stub = installFetchStub({
    routes: {
      '/api/services': { settlement_month: MONTH, ...overview },
      '/api/rent/deposits': deposits,
      '/api/property/rooms': rooms,
      '/api/property/contacts': contacts,
      '/api/property/documents': documents,
      '/api/services/furniture': furniture,
      '/api/services/water': water,
      [`/api/services/househelp/month/${MONTH}`]: househelp,
      '/api/services/broadband': broadband,
    },
  })
  render(
    <MemoryRouter>
      <ServicesHub month={MONTH} />
    </MemoryRouter>,
  )
}

describe('ServicesHub', () => {
  it('summarises the lease, the month cycle and the latest electricity bill', async () => {
    const bill = {
      expense_id: 'bill-1',
      amount_paise: 312000,
      expense_date: '2026-10-08',
      settlement_period: MONTH,
      statement_status: 'draft',
      provider_name: 'BESCOM',
      total_kwh: '412.5',
    }
    renderHub({
      rent: {
        config,
        cycle: {
          due_date: '2026-10-05',
          disbursement_status: 'pending',
          disbursed_at: null,
          remaining_disbursement_paise: 2850000,
          total_rent_paise: 2850000,
          collected_paise: 2849999,
        },
      },
      electricity: { bill, bills_for_month: 1, latest_bill: bill },
    })

    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('Kumar Yadav')).toBeInTheDocument()
    expect(fact(rent, 'Contract')).toHaveTextContent('₹28,500.00/mo')
    expect(fact(rent, 'Due date')).toHaveTextContent('5 Oct 2026')
    expect(within(rent).getByText('Pending')).toBeInTheDocument()
    // One paisa short must not read as fully collected.
    expect(fact(rent, 'Kitty collection')).toHaveTextContent('₹28,499.99 of ₹28,500.00 (99%)')
    // Four facts, and nothing more: no progress bar.
    expect(Array.from(rent.querySelectorAll('dt'), (term) => term.textContent)).toEqual([
      'Contract',
      'Landlord',
      'Due date',
      'Kitty collection',
    ])
    expect(within(rent).queryByRole('progressbar')).toBeNull()
    expect(within(rent).getByRole('link', { name: 'Open Rent Hub' })).toHaveAttribute('href', '/services/rent')
    expect(within(rent).queryByRole('link', { name: 'Config' })).toBeNull()

    const power = screen.getByRole('region', { name: 'Electricity' })
    expect(await within(power).findByText('BESCOM')).toBeInTheDocument()
    expect(within(power).getByText('Draft')).toBeInTheDocument()
    expect(fact(power, 'Usage split')).toHaveTextContent('AC by flatmate, the rest equally')
    expect(within(power).getByRole('link', { name: 'Open Power Hub' })).toHaveAttribute(
      'href',
      '/services/electricity',
    )
  })

  it('shows the lease due day and no collection for a month that is not billed', async () => {
    renderHub({
      rent: { config, cycle: null },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })
    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('Kumar Yadav')).toBeInTheDocument()
    expect(fact(rent, 'Due date')).toHaveTextContent('Day 5')
    expect(fact(rent, 'Kitty collection')).toHaveTextContent('Not billed')
    // The lease's term is shown under Housing, not here.
    expect(within(rent).queryByText(/Month Term/)).toBeNull()
  })

  it('says when everyone has paid into the kitty', async () => {
    renderHub({
      rent: {
        config,
        cycle: {
          due_date: '2026-10-05',
          disbursement_status: 'partially_disbursed',
          disbursed_at: '2026-10-05',
          remaining_disbursement_paise: 850000,
          total_rent_paise: 2850000,
          collected_paise: 2850000,
        },
      },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })
    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('Kumar Yadav')).toBeInTheDocument()
    // Everyone has paid into the kitty, even though the landlord is still owed.
    expect(fact(rent, 'Kitty collection')).toHaveTextContent('100% collected')
  })

  it('shows the planned services as placeholders with no actions', async () => {
    renderHub({
      rent: { config: null, cycle: null },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })

    const gas = screen.getByRole('region', { name: 'Cooking Gas (LPG)' })
    expect(within(gas).getByText('Planned')).toBeInTheDocument()
    expect(within(gas).queryByRole('link')).toBeNull()
    // House help and broadband are live.
    for (const name of ['Household Staff', 'Broadband & WiFi']) {
      expect(within(screen.getByRole('region', { name })).queryByText('Planned')).toBeNull()
    }
    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('No lease')).toBeInTheDocument()
    expect(within(rent).getAllByText('Not configured')).toHaveLength(4)
    expect(screen.queryByRole('link', { name: 'Config' })).toBeNull()
    expect(await screen.findByText('None entered yet')).toBeInTheDocument()
  })

  it('lists only monthly bills, with nothing that loops back to Housing', async () => {
    renderHub({ rent: { config, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    await screen.findByRole('region', { name: 'Rent' })
    for (const name of ['Housing Configuration', 'Security Deposits', 'Household & Property']) {
      expect(screen.queryByRole('region', { name })).toBeNull()
    }
    expect(screen.queryByRole('link', { name: /Housing/ })).toBeNull()
  })

  it('names every furniture rental provider', async () => {
    const service = { service_id: 'svc-1', service_type: 'furniture_rental', display_name: 'Furniture rental', status: 'active' }
    const config = { provider_name: 'Furlenco', customer_id: null, deposit_paid_paise: 0, deposit_refunded_paise: 0 }
    renderHub(
      { rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      noDeposits,
      noRooms,
      noContacts,
      noDocuments,
      {
        ...noFurniture,
        service,
        config,
        services: [
          { service, config },
          { service: { ...service, service_id: 'svc-2' }, config: { ...config, provider_name: 'Rentomojo' } },
        ],
        month: { expense: null },
      },
    )
    const card = screen.getByRole('region', { name: 'Furniture Rental' })
    expect(await within(card).findByText('Active')).toBeInTheDocument()
    expect(fact(card, 'Providers')).toHaveTextContent('Furlenco, Rentomojo')
  })

  it('summarises a live furniture subscription and its month', async () => {
    const furniture = {
      ...noFurniture,
      service: { service_id: 'svc-1', service_type: 'furniture_rental', display_name: 'Furniture rental', status: 'active' },
      config: { provider_name: 'Furlenco', customer_id: 'FUR-2291', deposit_paid_paise: 500000, deposit_refunded_paise: 0 },
      month: { expense: { expense_id: 'furniture-1', amount_paise: 220000 } },
      monthly_total_paise: 220000,
      active_item_count: 3,
    }
    renderHub(
      { rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      noDeposits,
      noRooms,
      noContacts,
      noDocuments,
      furniture,
    )
    const card = screen.getByRole('region', { name: 'Furniture Rental' })
    expect(await within(card).findByText('Active')).toBeInTheDocument()
    expect(fact(card, 'Provider')).toHaveTextContent('Furlenco')
    expect(fact(card, 'In the flat')).toHaveTextContent('3 items')
    expect(fact(card, 'Monthly total')).toHaveTextContent('₹2,200.00/mo')
    expect(within(card).getByText(/Billed/)).toHaveTextContent('Billed ₹2,200.00')
    expect(within(card).getByRole('link', { name: 'Open Furniture Hub' })).toHaveAttribute(
      'href',
      '/services/furniture',
    )
  })

  it('offers to set up furniture rental when there is none', async () => {
    renderHub({ rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    const card = screen.getByRole('region', { name: 'Furniture Rental' })
    expect(await within(card).findByText('Not set up')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'Set up furniture rental' })).toHaveAttribute(
      'href',
      '/services/furniture',
    )
    expect(within(card).queryByText('Provider')).toBeNull()
  })

  it('summarises the water contract and its month', async () => {
    const share = (id: string, name: string, paid: number) => ({
      resident_id: id,
      display_name: name,
      total_paise: 20000,
      paid_paise: paid,
      settled_paise: paid,
      outstanding_paise: 20000 - paid,
      status: paid ? 'paid' : 'unpaid',
      paid_date: null,
      payments: [],
    })
    renderHub(
      { rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      noDeposits,
      noRooms,
      noContacts,
      noDocuments,
      noFurniture,
      {
        service: { service_id: 'svc-w', service_type: 'water_tanker', display_name: 'Water Supply' },
        config: { provider_name: 'Society Water Supply', monthly_rate_paise: 60000, billing_day_of_month: 10 },
        month: {
          due_date: '2026-10-10',
          expense: { expense_id: 'water-1', amount_paise: 60000 },
          collected_count: 2,
          shares: [share('alice', 'Alice', 20000), share('bob', 'Bob', 20000), share('chris', 'Chris', 0)],
        },
        next_billing_date: '2026-11-10',
      },
    )
    const card = screen.getByRole('region', { name: 'Water Supply' })
    expect(await within(card).findByText('Society Water Supply')).toBeInTheDocument()
    expect(within(card).getByText('Active')).toBeInTheDocument()
    expect(fact(card, 'Contract rate')).toHaveTextContent('₹600.00/mo')
    expect(fact(card, 'Due date')).toHaveTextContent('10 Oct 2026')
    expect(fact(card, 'Kitty collection')).toHaveTextContent('2 of 3 covered')
    expect(within(card).getByRole('link', { name: 'Open Water Hub' })).toHaveAttribute('href', '/services/water')
  })

  it('offers to set up the water supply when there is none', async () => {
    renderHub({ rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    const card = screen.getByRole('region', { name: 'Water Supply' })
    expect(await within(card).findByText('Not set up')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'Set up water supply' })).toHaveAttribute('href', '/services/water')
  })

  it("summarises house help: the staff, the month's salaries and paying them", async () => {
    const share = (id: string, total: number) => ({ resident_id: id, display_name: id, total_paise: total })
    renderHub(
      { rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      noDeposits,
      noRooms,
      noContacts,
      noDocuments,
      noFurniture,
      noWater,
      {
        service: { service_id: 'svc-h', service_type: 'domestic_help', display_name: 'Household Staff' },
        active_worker_count: 2,
        saved: true,
        total_paise: 750000,
        expense: { expense_id: 'househelp-1', amount_paise: 750000 },
        shares: [share('alice', 325000), share('bob', 325000), share('chris', 100000)],
        collected_count: 1,
        disbursed_paise: 300000,
      },
    )
    const card = screen.getByRole('region', { name: 'Household Staff' })
    expect(await within(card).findByText('Active')).toBeInTheDocument()
    expect(fact(card, 'Staff')).toHaveTextContent('2 active helpers')
    expect(fact(card, 'Oct 2026 salaries')).toHaveTextContent('₹7,500.00')
    expect(fact(card, 'Kitty collection')).toHaveTextContent('1 of 3 covered')
    expect(fact(card, 'Workers paid')).toHaveTextContent('₹3,000.00 of ₹7,500.00')
    expect(within(card).getByRole('link', { name: 'Open Household Staff' })).toHaveAttribute(
      'href',
      '/services/househelp',
    )
  })

  it('says when a month of house help is not saved yet', async () => {
    renderHub(
      { rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      noDeposits,
      noRooms,
      noContacts,
      noDocuments,
      noFurniture,
      noWater,
      {
        ...noHouseHelp,
        service: { service_id: 'svc-h', service_type: 'domestic_help', display_name: 'Household Staff' },
        active_worker_count: 1,
        total_paise: 300000,
      },
    )
    const card = screen.getByRole('region', { name: 'Household Staff' })
    expect(await within(card).findByText('1 active helper')).toBeInTheDocument()
    expect(fact(card, 'Oct 2026 salaries')).toHaveTextContent('Attendance not saved')
    expect(fact(card, 'Kitty collection')).toHaveTextContent('Not billed')
  })

  it('offers to set up house help when there is none', async () => {
    renderHub({ rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    const none = screen.getByRole('region', { name: 'Household Staff' })
    expect(await within(none).findByText('Not set up')).toBeInTheDocument()
    expect(within(none).getByRole('link', { name: 'Set up household staff' })).toHaveAttribute(
      'href',
      '/services/househelp?tab=staff',
    )
  })

  it('summarises the broadband plan and its month', async () => {
    const share = (id: string, paid: number) => ({ resident_id: id, display_name: id, total_paise: 39300, paid_paise: paid })
    renderHub(
      { rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      noDeposits,
      noRooms,
      noContacts,
      noDocuments,
      noFurniture,
      noWater,
      noHouseHelp,
      {
        service: { service_id: 'svc-b', service_type: 'broadband', display_name: 'Broadband & WiFi' },
        config: { provider_name: 'Airtel Xstream Fiber', monthly_rate_paise: 117900, speed_mbps: 200 },
        month: {
          due_date: '2026-10-05',
          expense: { expense_id: 'broadband-1', amount_paise: 117900 },
          collected_count: 1,
          shares: [share('alice', 39300), share('bob', 0), share('chris', 0)],
        },
        next_billing_date: '2026-11-05',
      },
    )
    const card = screen.getByRole('region', { name: 'Broadband & WiFi' })
    expect(await within(card).findByText('Airtel Xstream Fiber')).toBeInTheDocument()
    expect(within(card).getByText('Active')).toBeInTheDocument()
    expect(fact(card, 'Plan')).toHaveTextContent('₹1,179.00/mo · 200 Mbps')
    expect(fact(card, 'Due date')).toHaveTextContent('5 Oct 2026')
    expect(fact(card, 'Kitty collection')).toHaveTextContent('1 of 3 covered')
    expect(within(card).getByRole('link', { name: 'Open Broadband' })).toHaveAttribute('href', '/services/broadband')
  })

  it('offers to set up broadband when there is none', async () => {
    renderHub({ rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    const card = screen.getByRole('region', { name: 'Broadband & WiFi' })
    expect(await within(card).findByText('Not set up')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'Set up broadband' })).toHaveAttribute('href', '/services/broadband')
  })

  it('keeps a card that fails to itself', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {})
    function Broken(): never {
      throw new Error('bad payload')
    }
    render(
      <>
        <ServiceCardErrorBoundary title="Furniture Rental">
          <Broken />
        </ServiceCardErrorBoundary>
        <ServiceCardErrorBoundary title="Rent">
          <section aria-label="Rent">Rent is fine</section>
        </ServiceCardErrorBoundary>
      </>,
    )
    const failed = screen.getByRole('region', { name: 'Furniture Rental' })
    expect(within(failed).getByRole('alert')).toHaveTextContent('This service could not be shown.')
    expect(screen.getByRole('region', { name: 'Rent' })).toHaveTextContent('Rent is fine')
    quiet.mockRestore()
  })
})
