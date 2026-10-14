/**
 * The umbrella hub only summarises: these tests check that the two live cards
 * show what the backend returned and that the planned services stay inert.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import { ServicesHub } from './ServicesHub'
import { installFetchStub } from '../../test/server'

const MONTH = '2026-10'

const config = {
  config_id: 'lease-1',
  landlord_name: 'Kumar Yadav',
  monthly_rent_paise: 2850000,
  due_day_of_month: 5,
}

let stub: ReturnType<typeof installFetchStub> | null = null

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

function renderHub(
  overview: { rent: unknown; electricity: unknown },
  deposits: Record<string, unknown> = noDeposits,
  rooms: Record<string, unknown> = noRooms,
) {
  stub = installFetchStub({
    routes: {
      '/api/services': { settlement_month: MONTH, ...overview },
      '/api/rent/deposits': deposits,
      '/api/property/rooms': rooms,
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
          disbursement_status: 'pending',
          total_rent_paise: 2850000,
          collected_paise: 2849999,
        },
      },
      electricity: { bill, bills_for_month: 1, latest_bill: bill },
    })

    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('Kumar Yadav')).toBeInTheDocument()
    expect(within(rent).getByText('Day 5')).toBeInTheDocument()
    expect(within(rent).getByText('Pending')).toBeInTheDocument()
    // One paisa short must not read as fully collected.
    expect(await within(rent).findByText('99% collected')).toBeInTheDocument()
    expect(within(rent).getByRole('link', { name: 'Open Rent Hub' })).toHaveAttribute('href', '/services/rent')
    expect(within(rent).queryByRole('link', { name: 'Config' })).toBeNull()

    const power = screen.getByRole('region', { name: 'Electricity' })
    expect(await within(power).findByText('BESCOM')).toBeInTheDocument()
    expect(within(power).getByText('Draft')).toBeInTheDocument()
    expect(within(power).getByRole('link', { name: 'Open Power Hub' })).toHaveAttribute(
      'href',
      '/services/electricity',
    )
  })

  it('shows how far through its term the lease is', async () => {
    renderHub({
      rent: {
        config: {
          ...config,
          lease_status: 'active',
          lifecycle: {
            duration_months: 11,
            elapsed_percent: 85,
            days_until_expiry: 48,
            is_notice_window_active: false,
            is_expired: false,
          },
        },
        cycle: null,
      },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })
    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('11-Month Term (85% elapsed)')).toBeInTheDocument()
    expect(within(rent).getByText('Expires in 48d')).toBeInTheDocument()
    expect(within(rent).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '85')
  })

  it('gives security deposits a card of their own, linking to their page', async () => {
    renderHub(
      { rent: { config, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      {
        is_lease_configured: true,
        master_deposit_paise: 15000000,
        funding_mode: 'primary_signatory',
        total_slots_count: 3,
        vacant_slots_count: 1,
        total_funded_paise: 7000000,
      },
    )
    const card = screen.getByRole('region', { name: 'Security Deposits' })
    expect(await within(card).findByText('₹1,50,000.00')).toBeInTheDocument()
    expect(within(card).getByText('Signatory fronted')).toBeInTheDocument()
    expect(within(card).getByText('2 of 3 occupied')).toBeInTheDocument()
    expect(within(card).getByText('₹70,000.00')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'Manage Deposits' })).toHaveAttribute(
      'href',
      '/services/deposits',
    )
  })

  it('says when the deposit has no lease or no setup yet', async () => {
    renderHub({ rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    const card = screen.getByRole('region', { name: 'Security Deposits' })
    expect(await within(card).findByText('No lease')).toBeInTheDocument()
    expect(within(card).getAllByText('Not set up')).toHaveLength(2)
  })

  it('shows the planned services as placeholders with no actions', async () => {
    renderHub({
      rent: { config: null, cycle: null },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })

    for (const name of ['Water & Tankers', 'Cooking Gas (LPG)', 'Broadband & WiFi', 'House Help & Cook']) {
      const card = screen.getByRole('region', { name })
      expect(within(card).getByText('Planned')).toBeInTheDocument()
      expect(within(card).queryByRole('link')).toBeNull()
    }
    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('No lease')).toBeInTheDocument()
    expect(within(rent).getAllByText('Not configured')).toHaveLength(4)
    expect(screen.queryByRole('link', { name: 'Config' })).toBeNull()
    expect(await screen.findByText('None entered yet')).toBeInTheDocument()
  })

  it('summarises the rooms and their occupancy', async () => {
    renderHub(
      { rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } },
      noDeposits,
      {
        items: [],
        total: 3,
        summary: {
          total_rooms: 3,
          occupied_rooms: 2,
          vacant_rooms: 1,
          with_attached_washroom: 2,
          with_balcony: 1,
        },
      },
    )
    const card = screen.getByRole('region', { name: 'Property & Rooms' })
    expect(await within(card).findByText('3 Rooms')).toBeInTheDocument()
    expect(within(card).getByText('2 of 3 Occupied')).toBeInTheDocument()
    expect(within(card).getByText('1 Vacant')).toBeInTheDocument()
    expect(
      within(card).getByText('2 with attached washroom · 1 with balcony'),
    ).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: /Manage Rooms/ })).toHaveAttribute(
      'href',
      '/services/rooms',
    )
  })

  it('says when no rooms have been added', async () => {
    renderHub({ rent: { config: null, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    const card = screen.getByRole('region', { name: 'Property & Rooms' })
    expect(await within(card).findAllByText('Not set up')).toHaveLength(4)
  })
})
