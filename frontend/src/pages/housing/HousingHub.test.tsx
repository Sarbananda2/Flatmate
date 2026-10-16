/**
 * Housing: the title, and the six tabs below it,
 * with the open tab kept in the address. The tabs are the app's own tested
 * sections, so these tests only check that each one is the one shown.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HousingHub } from './HousingHub'
import { ToastProvider } from '../../components/Toast'
import { installFetchStub, type Recorded } from '../../test/server'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const rooms = {
  items: [],
  total: 3,
  summary: {
    total_rooms: 3,
    occupied_rooms: 2,
    vacant_rooms: 1,
    with_attached_washroom: 2,
    with_balcony: 1,
    rooms_with_base_rent: 0,
    total_base_rent_paise: 0,
    linked_slots: 3,
  },
}

const lease = {
  config: {
    monthly_rent_paise: 2850000,
    lease_start_date: '2026-04-01',
    lease_end_date: '2027-02-28',
    lifecycle: {
      current_month_index: 7,
      duration_months: 11,
      is_expired: false,
      is_notice_window_active: false,
    },
  },
}

const deposits = {
  is_lease_configured: true,
  master_deposit_paise: 15000000,
  funding_mode: 'primary_signatory',
  is_balanced: true,
  total_slots_count: 3,
  vacant_slots_count: 0,
  total_funded_paise: 15000000,
  unfunded_gap_paise: 0,
  slots: [],
  proposal: [],
  unassigned_residents: [],
}

const contacts = {
  items: [
    {
      contact_id: 'owner-1',
      role: 'owner',
      name: 'Kumar Yadav',
      phone: '+91 98765 43210',
      upi_id: 'kumar.yadav@okhdfcbank',
    },
  ],
  total: 1,
  roles_on_file: ['owner'],
}

const documents = { items: [], total: 0, total_bytes: 0, categories_on_file: [] }

const apartment = {
  apartment_id: 'flat',
  name: 'Flat 1805, CG6, Supertech Capetown',
  currency_code: 'INR',
  timezone: 'Asia/Kolkata',
  created_at: '2026-10-01T10:00:00+05:30',
  flat_number: '1805',
  tower_block: 'CG6',
  society_name: 'Supertech Capetown',
  locality_street: 'Sector 74',
  city: 'Noida',
  state: 'Uttar Pradesh',
  pincode: '201301',
  premises_address: '1805, CG6, Supertech Capetown, Sector 74, Noida, Uttar Pradesh - 201301',
}

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname + location.search}</output>
}

function open(path = '/housing', overrides: Record<string, unknown> = {}) {
  stub = installFetchStub({
    routes: {
      '/api/property/rooms': rooms,
      '/api/rent/config': lease,
      '/api/rent/deposits': deposits,
      '/api/property/contacts': contacts,
      '/api/property/documents': documents,
      '/api/residents': { items: [], total: 0 },
      '/api/rent/lease/terms': { items: [] },
      ...overrides,
    } as never,
  })
  render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Routes>
          <Route
            path="/housing"
            element={<HousingHub apartment={apartment} onApartmentChanged={() => {}} />}
          />
        </Routes>
        <Where />
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('HousingHub layout', () => {
  it('goes straight from the title into the tabs, with no summary tiles', async () => {
    open()
    expect(screen.getByRole('heading', { level: 1, name: 'Housing' })).toBeInTheDocument()
    for (const name of ['Apartment', 'Physical Space', 'Active Lease', 'Security Deposit', 'Landlord / Owner']) {
      expect(screen.queryByRole('region', { name })).toBeNull()
    }
    expect(screen.queryByLabelText('Housing summary')).toBeNull()
    expect(screen.getByRole('tablist', { name: 'Housing' })).toBeInTheDocument()
  })

  it('shows the apartment in full on the tenancy tab, with its own Edit', async () => {
    open()
    const profile = await screen.findByLabelText('Apartment profile')
    for (const text of ['Flat / Unit', '1805', 'Tower / Block', 'CG6', 'Supertech Capetown',
      'Sector 74', 'Noida, Uttar Pradesh', 'PIN Code', '201301']) {
      expect(profile).toHaveTextContent(text)
    }
    expect(profile).not.toHaveTextContent(/Currency|Time Zone|Asia\/Kolkata/)
    expect(screen.getByRole('button', { name: 'Edit apartment' })).toBeInTheDocument()
  })

  it('edits the tenancy terms in a dialog named like the card, leaving the other cards as they are', async () => {
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Edit tenancy terms' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Master Tenancy Terms' })).toBeInTheDocument()
    expect(within(within(dialog).getByRole('tablist', { name: 'Edit steps' })).getAllByRole('tab')).toHaveLength(4)
    expect(screen.getByLabelText('Apartment profile')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit apartment' })).toBeInTheDocument()
  })
})

describe('HousingHub tabs', () => {
  it('opens on the lease and moves between tabs through the address', async () => {
    open()
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    // Define the rooms, fund the deposit, then allot the rooms.
    expect(tabs).toEqual([
      'Tenancy & Master Lease',
      'Rooms Inventory',
      'Security Deposit',
      'Room Allotment',
      'Stakeholders & Contacts',
      'Document Vault',
    ])
    expect(screen.getByRole('tab', { name: /Tenancy & Master Lease/ })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('button', { name: 'Edit tenancy terms' })).toBeInTheDocument()

    const expectations: Array<[RegExp, string, string]> = [
      [/Rooms Inventory/, '?tab=rooms', '3 rooms defined'],
      [/Security Deposit/, '?tab=deposits', 'Household Security Deposit Setup'],
      [/Room Allotment/, '?tab=allotment', '2 of 3 rooms allotted · 1 vacant'],
      [/Stakeholders & Contacts/, '?tab=contacts', 'Add Contact'],
      [/Document Vault/, '?tab=documents', 'No documents yet'],
    ]
    for (const [name, query, marker] of expectations) {
      await userEvent.click(screen.getByRole('tab', { name }))
      expect(screen.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByLabelText('location')).toHaveTextContent(`/housing${query}`)
      expect(await screen.findAllByText(marker)).not.toHaveLength(0)
    }
    await userEvent.click(screen.getByRole('tab', { name: /Tenancy & Master Lease/ }))
    expect(screen.getByLabelText('location')).toHaveTextContent(/^\/housing$/)
  })

  it.each([
    ['rooms', /Rooms Inventory/],
    ['deposits', /Security Deposit/],
    ['allotment', /Room Allotment/],
    ['contacts', /Stakeholders & Contacts/],
    ['documents', /Document Vault/],
  ])('opens on the %s tab named in the address', (tab, name) => {
    open(`/housing?tab=${tab}`)
    expect(screen.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
  })

  it('shows the Room Allotment panel on its tab', async () => {
    open('/housing?tab=allotment')
    expect(await screen.findByRole('heading', { name: 'Room Allotment' })).toBeInTheDocument()
    // No rooms yet in this flat: it points to where they are defined.
    expect(await screen.findByRole('link', { name: 'Go to Rooms Inventory' })).toHaveAttribute(
      'href',
      '/housing?tab=rooms',
    )
  })

  it('treats an unknown tab as the lease', () => {
    open('/housing?tab=nonsense')
    expect(screen.getByRole('tab', { name: /Tenancy & Master Lease/ })).toHaveAttribute('aria-selected', 'true')
  })
})
