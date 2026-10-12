/**
 * The residents directory: five columns that fit, no internal identifiers on
 * show, and a balance that reads as a word when there is nothing to settle.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import ResidentsPage from './Residents'
import { ToastProvider } from '../components/Toast'
import { RESIDENTS, installFetchStub } from '../test/server'

let stub: ReturnType<typeof installFetchStub> | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const [alice, bob, chris] = RESIDENTS.items

function renderPage() {
  stub = installFetchStub({
    routes: {
      '/api/residents': {
        total: 3,
        items: [
          { ...alice, phone: '+91 98765 43210', upi_id: 'alice@okaxis', email: 'alice@example.com' },
          { ...bob, email: 'bob@example.com' },
          { ...chris, is_active: 0, active_until: '2026-09-30' },
        ],
      },
      '/api/reconciliation': {
        accounts: [
          { account_id: 'alice', account_type: 'resident', net_position_paise: -150000 },
          { account_id: 'bob', account_type: 'resident', net_position_paise: 0 },
          { account_id: 'chris', account_type: 'resident', net_position_paise: 25000 },
        ],
      },
    },
  })
  return render(
    <MemoryRouter>
      <ToastProvider>
        <ResidentsPage />
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('ResidentsPage', () => {
  it('lists residents in five columns without the old banner or ID badges', async () => {
    renderPage()
    const table = await screen.findByRole('table', {
      name: 'Residents, how to reach them, and their balance',
    })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual(['Resident', 'Status', 'Contact & UPI', 'Net balance', 'Actions'])
    expect(screen.queryByText(/Nothing prorates/)).not.toBeInTheDocument()
    // The internal identifier is not shown; the name still links to the detail page.
    expect(within(table).queryByText('alice')).not.toBeInTheDocument()
    expect(within(table).getByRole('link', { name: 'Alice' })).toHaveAttribute(
      'href',
      '/residents/alice',
    )
  })

  it('shows contact details, a WhatsApp link, and a dash when there are none', async () => {
    renderPage()
    const table = await screen.findByRole('table')
    const aliceRow = within(table).getByRole('row', { name: /Alice/ })
    expect(aliceRow).toHaveTextContent('+91 98765 43210')
    // The UPI ID takes the second line when there is one; otherwise the email.
    expect(aliceRow).toHaveTextContent('alice@okaxis')
    expect(aliceRow).not.toHaveTextContent('alice@example.com')
    expect(within(aliceRow).getByRole('link', { name: 'WhatsApp Alice' })).toHaveAttribute(
      'href',
      'https://wa.me/919876543210',
    )
    expect(within(table).getByRole('row', { name: /Bob/ })).toHaveTextContent('bob@example.com')
    const chrisRow = within(table).getByRole('row', { name: /Chris/ })
    expect(within(chrisRow).getAllByRole('cell')[1]).toHaveTextContent('—')
  })

  it('states the balance once, as Settled or a signed amount, with actions by status', async () => {
    renderPage()
    const table = await screen.findByRole('table')
    const aliceRow = within(table).getByRole('row', { name: /Alice/ })
    expect(aliceRow).toHaveTextContent('Active')
    expect(await within(aliceRow).findByText(/1,500\.00/)).toBeInTheDocument()
    expect(aliceRow).toHaveTextContent(/[-−]₹1,500\.00/)
    expect(
      within(aliceRow).getByRole('button', { name: 'Record that Alice moved out' }),
    ).toBeInTheDocument()

    expect(within(table).getByRole('row', { name: /Bob/ })).toHaveTextContent('Settled')

    const chrisRow = within(table).getByRole('row', { name: /Chris/ })
    expect(chrisRow).toHaveTextContent('Moved out')
    expect(chrisRow).toHaveTextContent('+₹250.00')
    expect(within(chrisRow).getByRole('button', { name: 'Reactivate Chris' })).toBeInTheDocument()
    expect(within(chrisRow).getByRole('button', { name: 'Edit Chris' })).toBeInTheDocument()
  })
})
