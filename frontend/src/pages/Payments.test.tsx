/**
 * Payments: the household pool's own account, and where a payment is sent.
 *
 * The pool's name, bank account, IFSC and UPI ID are shown read-only and
 * edited in a dialog named like the card, checked as the backend checks them.
 * Recording a payment into the pool, or an advance paid to the landlord, shows
 * where to send the money.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HouseholdPoolCard, PaymentDialog } from './Payments'
import { ToastProvider } from '../components/Toast'
import { RESIDENTS, installFetchStub, type Recorded } from '../test/server'
import type { Apartment } from '../api/types'

const APARTMENT: Apartment = {
  apartment_id: 'home',
  name: 'Flat 402',
  currency_code: 'INR',
  timezone: 'Asia/Kolkata',
  created_at: '2026-10-01T10:00:00+05:30',
  pool_name: 'Flat 402 Kitty',
  pool_bank_account: '123456789012',
  pool_bank_ifsc: 'SBIN0005678',
  pool_upi_id: 'flat402@okicici',
}

const LEASE = {
  config: {
    landlord_name: 'Kumar Yadav',
    landlord_bank_account: '50100123456789',
    landlord_bank_ifsc: 'HDFC0001234',
    landlord_upi_id: 'kumar.yadav@okhdfcbank',
    monthly_rent_paise: 2850000,
  },
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const patched = () => stub!.calls.find((call) => call.method === 'PATCH')

function showCard(apartment: Apartment = APARTMENT, onSaved = vi.fn()) {
  stub = installFetchStub({
    routes: { 'PATCH /api/apartments/home': { ...apartment } } as never,
  })
  render(
    <MemoryRouter>
      <ToastProvider>
        <HouseholdPoolCard apartment={apartment} onSaved={onSaved} />
      </ToastProvider>
    </MemoryRouter>,
  )
  return onSaved
}

function showDialog(kind: 'to_pool' | 'provider_advance', lease: unknown = LEASE) {
  stub = installFetchStub({
    routes: {
      '/api/residents': RESIDENTS,
      '/api/rent/config': lease,
      '/api/expenses': { items: [], total: 0, limit: 200, offset: 0 },
    } as never,
  })
  render(
    <MemoryRouter>
      <PaymentDialog initialKind={kind} apartment={APARTMENT} onClose={() => {}} onSaved={() => {}} />
    </MemoryRouter>,
  )
  return screen.getByRole('dialog', { name: 'Record a payment' })
}

/** A details list read back as "label: value". */
const factsIn = (element: HTMLElement) =>
  within(element)
    .getAllByRole('term')
    .map((term) => `${term.textContent}: ${term.nextElementSibling!.textContent}`)

describe('HouseholdPoolCard', () => {
  it('shows the pool account read-only, with the UPI ID to copy', () => {
    showCard()
    expect(screen.getByRole('heading', { name: 'Household Pool Account' })).toBeInTheDocument()
    expect(
      screen.getByText('Where residents send pool payments, rent shares, and settlement transfers.'),
    ).toBeInTheDocument()
    const facts = screen.getByLabelText('Household pool account')
    expect(factsIn(facts).slice(0, 3)).toEqual([
      'Pool Name: Flat 402 Kitty',
      'Bank Account: 123456789012',
      'IFSC: SBIN0005678',
    ])
    // The UPI ID, then its copy button.
    expect(factsIn(facts)[3]).toMatch(/^UPI ID: flat402@okicici/)
    expect(within(facts).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()
    // Read-only until Edit: nothing to type in.
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('says what is not recorded yet', () => {
    showCard({ ...APARTMENT, pool_name: 'Household Pool', pool_bank_account: null, pool_bank_ifsc: null, pool_upi_id: null })
    expect(factsIn(screen.getByLabelText('Household pool account'))).toEqual([
      'Pool Name: Household Pool',
      'Bank Account: Not recorded',
      'IFSC: Not recorded',
      'UPI ID: Not recorded',
    ])
  })

  it('edits the pool in a dialog named like the card, checking each field first', async () => {
    const onSaved = showCard()
    await userEvent.click(screen.getByRole('button', { name: 'Edit household pool' }))
    const dialog = screen.getByRole('dialog', { name: 'Household Pool Account' })
    const field = (label: string) => within(dialog).getByLabelText(new RegExp(`^${label}`))
    expect(field('Pool Name')).toHaveValue('Flat 402 Kitty')
    expect(field('UPI ID')).toHaveValue('flat402@okicici')

    await userEvent.clear(field('Bank Account'))
    await userEvent.type(field('Bank Account'), '1234')
    await userEvent.clear(field('IFSC'))
    await userEvent.type(field('IFSC'), 'hdfc1001234')
    await userEvent.clear(field('UPI ID'))
    await userEvent.type(field('UPI ID'), 'flat402')
    expect(within(dialog).getByText('A bank account number is 9 to 18 digits.')).toBeInTheDocument()
    expect(within(dialog).getByText('An IFSC looks like HDFC0001234.')).toBeInTheDocument()
    expect(within(dialog).getByText('A UPI ID looks like name@bank.')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(patched()).toBeUndefined()

    await userEvent.clear(field('Pool Name'))
    await userEvent.type(field('Pool Name'), ' Kitty ')
    await userEvent.clear(field('Bank Account'))
    await userEvent.type(field('Bank Account'), '987654321')
    await userEvent.clear(field('IFSC'))
    // Typed in lower case, saved as an IFSC is written.
    await userEvent.type(field('IFSC'), 'hdfc0001234')
    expect(field('IFSC')).toHaveValue('HDFC0001234')
    await userEvent.clear(field('UPI ID'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.path).toBe('/api/apartments/home')
    expect(patched()!.body).toEqual({
      pool_name: 'Kitty',
      pool_bank_account: '987654321',
      pool_bank_ifsc: 'HDFC0001234',
      pool_upi_id: null,
    })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(await screen.findByText('Household pool saved')).toBeInTheDocument()
  })

  it('will not save a pool without a name', async () => {
    showCard()
    await userEvent.click(screen.getByRole('button', { name: 'Edit household pool' }))
    const dialog = screen.getByRole('dialog', { name: 'Household Pool Account' })
    await userEvent.clear(within(dialog).getByLabelText(/^Pool Name/))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(within(dialog).getByText('Give the pool a name.')).toBeInTheDocument()
    expect(patched()).toBeUndefined()
  })
})

describe('PaymentDialog', () => {
  it('shows where to pay the household pool', async () => {
    const dialog = showDialog('to_pool')
    const destination = within(dialog).getByRole('region', { name: 'Payment destination' })
    expect(destination).toHaveTextContent('Transfer toFlat 402 Kitty')
    for (const detail of ['Account123456789012', 'IFSCSBIN0005678', 'UPI IDflat402@okicici']) {
      expect(destination).toHaveTextContent(detail)
    }
    expect(within(destination).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/^Counterparty/)).toHaveAttribute('placeholder', 'Flat 402 Kitty')

    // The amount, once entered, is shown with it.
    await userEvent.type(within(dialog).getByLabelText(/^Amount/), '1,500')
    expect(destination).toHaveTextContent('₹1,500.00')
  })

  it('offers the landlord as the provider, filling in the name and showing where to pay', async () => {
    const dialog = showDialog('provider_advance')
    const provider = await within(dialog).findByLabelText('Provider')
    expect(within(provider).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Landlord (Kumar Yadav)',
      'Other provider',
    ])
    expect(provider).toHaveValue('other')
    expect(within(dialog).queryByRole('region', { name: 'Payment destination' })).toBeNull()

    await userEvent.selectOptions(provider, 'landlord')
    expect(within(dialog).getByLabelText(/^Provider paid/)).toHaveValue('Kumar Yadav')
    const destination = within(dialog).getByRole('region', { name: 'Payment destination' })
    expect(destination).toHaveTextContent('Transfer toKumar Yadav')
    for (const detail of ['Account50100123456789', 'IFSCHDFC0001234', 'UPI IDkumar.yadav@okhdfcbank']) {
      expect(destination).toHaveTextContent(detail)
    }

    // Back to another provider: the landlord's name and details go.
    await userEvent.selectOptions(provider, 'other')
    expect(within(dialog).getByLabelText(/^Provider paid/)).toHaveValue('')
    expect(within(dialog).queryByRole('region', { name: 'Payment destination' })).toBeNull()
  })

  it('offers no landlord before there is a lease', async () => {
    const dialog = showDialog('provider_advance', { config: null })
    await waitFor(() => expect(stub!.calls.some((call) => call.path === '/api/rent/config')).toBe(true))
    expect(within(dialog).queryByLabelText('Provider')).toBeNull()
  })
})
