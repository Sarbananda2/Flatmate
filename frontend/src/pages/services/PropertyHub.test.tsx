/** Household & Property: three tabs, with the open one kept in the address. */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PropertyHubPage from './PropertyHub'
import { ToastProvider } from '../../components/Toast'
import { installFetchStub, type Recorded } from '../../test/server'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname + location.search}</output>
}

function open(path = '/services/property') {
  stub = installFetchStub({
    routes: {
      '/api/property/rooms': {
        items: [],
        total: 0,
        summary: { total_rooms: 0, occupied_rooms: 0, vacant_rooms: 0, with_attached_washroom: 0, with_balcony: 0 },
      },
      '/api/residents': { items: [], total: 0 },
      '/api/property/contacts': { items: [], total: 0, roles_on_file: [] },
      '/api/property/documents': { items: [], total: 0, total_bytes: 0, categories_on_file: [] },
    },
  })
  render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Routes>
          <Route path="/services/property" element={<PropertyHubPage />} />
        </Routes>
        <Where />
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('PropertyHubPage', () => {
  it('opens on the rooms and switches tabs through the address', async () => {
    open()
    expect(screen.getByRole('heading', { name: 'Household & Property' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Rooms Inventory/ })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('No rooms yet')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: /Stakeholders & Contacts/ }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/services/property?tab=contacts')
    expect(await screen.findByRole('button', { name: 'Add Contact' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: /Document Vault/ }))
    expect(screen.getByLabelText('location')).toHaveTextContent('?tab=documents')
    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
  })

  it('opens the tab named in the address, and falls back to rooms', async () => {
    open('/services/property?tab=documents')
    expect(screen.getByRole('tab', { name: /Document Vault/ })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
  })

  it('treats an unknown tab as the rooms', () => {
    open('/services/property?tab=nonsense')
    expect(screen.getByRole('tab', { name: /Rooms Inventory/ })).toHaveAttribute('aria-selected', 'true')
  })
})
