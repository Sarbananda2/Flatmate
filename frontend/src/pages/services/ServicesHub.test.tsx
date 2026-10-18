/**
 * The umbrella hub only summarises: these tests check that the two live cards
 * show what the backend returned and that the planned services stay inert.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { cleanup, render, screen, within } from '@testing-library/react'
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

function renderHub(
  overview: { rent: unknown; electricity: unknown },
  deposits: Record<string, unknown> = noDeposits,
  rooms: Record<string, unknown> = noRooms,
  contacts: Record<string, unknown> = noContacts,
  documents: Record<string, unknown> = noDocuments,
) {
  stub = installFetchStub({
    routes: {
      '/api/services': { settlement_month: MONTH, ...overview },
      '/api/rent/deposits': deposits,
      '/api/property/rooms': rooms,
      '/api/property/contacts': contacts,
      '/api/property/documents': documents,
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
    expect(fact(rent, 'Payment completed by')).toHaveTextContent('Not settled yet')
    expect(within(rent).getByText('Pending')).toBeInTheDocument()
    // One paisa short must not read as fully collected.
    const kitty = within(rent).getByRole('progressbar', { name: 'Kitty Collection (99%)' })
    expect(kitty).toHaveAttribute('aria-valuenow', '99')
    expect(within(rent).getByText('₹0.01 remaining')).toBeInTheDocument()
    expect(within(rent).queryByText('100% collected')).toBeNull()
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

  it('shows the lease due day and no collection before the month is generated', async () => {
    renderHub({
      rent: { config, cycle: null },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })
    const rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('Kumar Yadav')).toBeInTheDocument()
    expect(fact(rent, 'Due date')).toHaveTextContent('Day 5')
    expect(fact(rent, 'Payment completed by')).toHaveTextContent('Not configured')
    expect(within(rent).queryByRole('progressbar')).toBeNull()
    // The lease's term is shown under Housing, not here.
    expect(within(rent).queryByText(/Month Term/)).toBeNull()
  })

  it('says how much is left to pay the landlord, and when it was paid in full', async () => {
    const month = {
      due_date: '2026-10-05',
      total_rent_paise: 2850000,
      collected_paise: 2850000,
    }
    renderHub({
      rent: {
        config,
        cycle: {
          ...month,
          disbursement_status: 'partially_disbursed',
          disbursed_at: '2026-10-05',
          remaining_disbursement_paise: 850000,
        },
      },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })
    let rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('Kumar Yadav')).toBeInTheDocument()
    expect(fact(rent, 'Payment completed by')).toHaveTextContent('Partially settled (₹8,500.00 left)')
    // Everyone has paid into the kitty, even though the landlord is still owed.
    expect(within(rent).getByRole('progressbar', { name: 'Kitty Collection (100%)' })).toHaveAttribute(
      'aria-valuenow',
      '100',
    )
    expect(within(rent).getByText('100% collected')).toBeInTheDocument()
    stub?.restore()
    cleanup()

    renderHub({
      rent: {
        config,
        cycle: {
          ...month,
          disbursement_status: 'disbursed',
          disbursed_at: '2026-10-09',
          remaining_disbursement_paise: 0,
        },
      },
      electricity: { bill: null, bills_for_month: 0, latest_bill: null },
    })
    rent = screen.getByRole('region', { name: 'Rent' })
    expect(await within(rent).findByText('Kumar Yadav')).toBeInTheDocument()
    expect(fact(rent, 'Payment completed by')).toHaveTextContent('9 Oct 2026')
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

  it('lists only monthly bills, with nothing that loops back to Housing', async () => {
    renderHub({ rent: { config, cycle: null }, electricity: { bill: null, bills_for_month: 0, latest_bill: null } })
    await screen.findByRole('region', { name: 'Rent' })
    for (const name of ['Housing Configuration', 'Security Deposits', 'Household & Property']) {
      expect(screen.queryByRole('region', { name })).toBeNull()
    }
    expect(screen.queryByRole('link', { name: /Housing/ })).toBeNull()
  })
})
