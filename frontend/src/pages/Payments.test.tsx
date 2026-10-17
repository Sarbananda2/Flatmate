/**
 * Payment Pools: the House Kitty's and the landlord's accounts, and where a
 * payment is sent.
 *
 * Each account is shown read-only and edited in a dialog named like its card:
 * the pool's checked as the backend checks it, the landlord's through the
 * owner contact. The landlord's account may be in someone else's name, which
 * is saved beside the owner's and never in its place. Recording a payment into the pool, or an advance paid to the
 * landlord, shows where to send the money. The page is in the main navigation.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PaymentsPage, { HouseholdPoolCard, LandlordAccountCard, PaymentDialog } from './Payments'
import { Layout } from '../components/Layout'
import { ToastProvider } from '../components/Toast'
import { RESIDENTS, installFetchStub, type Recorded } from '../test/server'
import type {
  Apartment,
  HouseholdContact,
  LandlordStatement,
  PoolStatement,
  StatementEntry,
} from '../api/types'

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
    expect(screen.getByRole('heading', { name: 'House Kitty Account' })).toBeInTheDocument()
    expect(
      screen.getByText('Where residents send kitty payments, rent shares, and settlement transfers.'),
    ).toBeInTheDocument()
    const facts = screen.getByLabelText('House kitty account')
    expect(factsIn(facts).slice(0, 3)).toEqual([
      'Account Name: Flat 402 Kitty',
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
    showCard({ ...APARTMENT, pool_name: '', pool_bank_account: null, pool_bank_ifsc: null, pool_upi_id: null })
    expect(factsIn(screen.getByLabelText('House kitty account'))).toEqual([
      // No name stored: it is the House Kitty.
      'Account Name: House Kitty',
      'Bank Account: Not recorded',
      'IFSC: Not recorded',
      'UPI ID: Not recorded',
    ])
  })

  it('edits the pool in a dialog named like the card, checking each field first', async () => {
    const onSaved = showCard()
    await userEvent.click(screen.getByRole('button', { name: 'Edit house kitty' }))
    const dialog = screen.getByRole('dialog', { name: 'House Kitty Account' })
    const field = (label: string) => within(dialog).getByLabelText(new RegExp(`^${label}`))
    expect(within(dialog).getAllByRole('textbox').map((box) => box.closest('.field')?.querySelector('label')?.textContent)).toEqual([
      'Account Name* (required)',
      'Bank Account',
      'IFSC',
      'UPI ID',
    ])
    expect(field('Account Name')).toHaveValue('Flat 402 Kitty')
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

    await userEvent.clear(field('Account Name'))
    await userEvent.type(field('Account Name'), ' Kitty ')
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
    expect(await screen.findByText('House kitty saved')).toBeInTheDocument()
  })

  it('will not save a kitty without a name', async () => {
    showCard()
    await userEvent.click(screen.getByRole('button', { name: 'Edit house kitty' }))
    const dialog = screen.getByRole('dialog', { name: 'House Kitty Account' })
    await userEvent.clear(within(dialog).getByLabelText(/^Account Name/))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(within(dialog).getByText('Enter the account name.')).toBeInTheDocument()
    expect(patched()).toBeUndefined()
  })
})

describe('PaymentDialog', () => {
  it('shows where to pay the House Kitty', async () => {
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

  it("sends an advance to the account's holder while the landlord stays the provider", async () => {
    const dialog = showDialog('provider_advance', {
      config: { ...LEASE.config, payout_beneficiary_name: 'Anita Yadav' },
    })
    await userEvent.selectOptions(await within(dialog).findByLabelText('Provider'), 'landlord')
    expect(within(dialog).getByLabelText(/^Provider paid/)).toHaveValue('Kumar Yadav')
    expect(within(dialog).getByRole('region', { name: 'Payment destination' })).toHaveTextContent(
      'Transfer toAnita Yadav',
    )
  })

  it('offers no landlord before there is a lease', async () => {
    const dialog = showDialog('provider_advance', { config: null })
    await waitFor(() => expect(stub!.calls.some((call) => call.path === '/api/rent/config')).toBe(true))
    expect(within(dialog).queryByLabelText('Provider')).toBeNull()
  })
})

/* ------------------------------------------------------------- the landlord -- */

const OWNER: HouseholdContact = {
  contact_id: 'owner-1',
  apartment_id: 'home',
  role: 'owner',
  name: 'Kumar Yadav',
  phone: '+91 98765 43210',
  email: null,
  pan: null,
  bank_account_no: '50100123456789',
  bank_ifsc: 'HDFC0001234',
  upi_id: 'kumar.yadav@okhdfcbank',
  agency_name: null,
  fee_amount_paise: null,
  fee_status: null,
  fee_display: null,
  responsibilities: null,
  notes: null,
  created_at: '2026-10-01T10:00:00+05:30',
  updated_at: '2026-10-01T10:00:00+05:30',
}

function showLandlord(lease: unknown = LEASE, owners: HouseholdContact[] = [OWNER]) {
  stub = installFetchStub({
    routes: {
      '/api/rent/config': lease,
      '/api/property/contacts': { items: owners, total: owners.length, roles_on_file: owners.length ? ['owner'] : [] },
      'POST /api/rent/config': LEASE.config,
    } as never,
  })
  render(
    <MemoryRouter>
      <ToastProvider>
        <LandlordAccountCard />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const gets = (path: string) =>
  stub!.calls.filter((call) => call.method === 'GET' && call.path === path).length

describe('LandlordAccountCard', () => {
  it("shows the landlord's account read-only, with the UPI ID to copy", async () => {
    showLandlord()
    expect(screen.getByRole('heading', { name: 'Landlord Account' })).toBeInTheDocument()
    expect(
      screen.getByText('Where rent and direct security deposits are paid under the master lease.'),
    ).toBeInTheDocument()
    const facts = screen.getByLabelText('Landlord account')
    await waitFor(() => expect(facts).toHaveTextContent('Kumar Yadav'))
    expect(factsIn(facts).slice(0, 3)).toEqual([
      'Payout Beneficiary: Kumar Yadav',
      'Bank Account: 50100123456789',
      'IFSC: HDFC0001234',
    ])
    expect(factsIn(facts)[3]).toMatch(/^UPI ID: kumar\.yadav@okhdfcbank/)
    expect(within(facts).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it("falls back to the owner contact's details, then to Not recorded", async () => {
    // The lease names the landlord but has no bank details; the owner contact has them.
    showLandlord({ config: { landlord_name: 'Kumar Yadav', monthly_rent_paise: 2850000 } }, [
      { ...OWNER, upi_id: null },
    ])
    const facts = screen.getByLabelText('Landlord account')
    await waitFor(() => expect(facts).toHaveTextContent('50100123456789'))
    expect(factsIn(facts)).toEqual([
      'Payout Beneficiary: Kumar Yadav',
      'Bank Account: 50100123456789',
      'IFSC: HDFC0001234',
      'UPI ID: Not recorded',
    ])
  })

  it('says Not recorded before there is a lease, and points to where it is set up', async () => {
    showLandlord({ config: null }, [])
    await waitFor(() => expect(gets('/api/property/contacts')).toBe(1))
    const facts = screen.getByLabelText('Landlord account')
    await waitFor(() =>
      expect(factsIn(facts)).toEqual([
        'Payout Beneficiary: Not recorded',
        'Bank Account: Not recorded',
        'IFSC: Not recorded',
        'UPI ID: Not recorded',
      ]),
    )
    // The account is kept on the lease, so there is nothing to edit until there is one.
    expect(await screen.findByRole('link', { name: 'Set up the lease under Housing' })).toHaveAttribute(
      'href',
      '/housing',
    )
    expect(screen.queryByRole('button', { name: 'Edit landlord' })).toBeNull()
  })

  it('edits the landlord in the same dialog as the kitty, saving to the lease', async () => {
    showLandlord()
    await userEvent.click(await screen.findByRole('button', { name: 'Edit landlord' }))
    const dialog = screen.getByRole('dialog', { name: 'Landlord Account' })
    const field = (label: string) => within(dialog).getByLabelText(new RegExp(`^${label}`))
    // Whose account it is, as a choice: the landlord's own by default.
    const choice = within(dialog).getByRole('radiogroup', { name: 'Payout Beneficiary' })
    expect(within(choice).getByRole('radio', { name: 'Same as Landlord (Kumar Yadav)' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(within(choice).getByRole('radio', { name: 'Custom Beneficiary' })).toHaveAttribute('aria-checked', 'false')
    // The name is shown for the landlord's own account too, locked to theirs;
    // then the kitty dialog's payment fields.
    expect(within(dialog).getAllByRole('textbox').map((box) => box.closest('.field')?.querySelector('label')?.textContent)).toEqual([
      'Beneficiary Name',
      'Bank Account',
      'IFSC',
      'UPI ID',
    ])
    const locked = field('Beneficiary Name')
    expect(locked).toBeDisabled()
    expect(locked).toHaveValue('Kumar Yadav')
    expect(locked).not.toBeRequired()
    expect(locked).toHaveAccessibleDescription("🔒 Locked to the landlord's legal name. Switch to 'Custom Beneficiary' above to edit.")
    expect(field('Bank Account')).toHaveValue('50100123456789')
    expect(field('IFSC')).toHaveValue('HDFC0001234')
    expect(field('UPI ID')).toHaveValue('kumar.yadav@okhdfcbank')

    // The same checks, before anything is sent.
    await userEvent.clear(field('UPI ID'))
    await userEvent.type(field('UPI ID'), 'kumar')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(within(dialog).getByText('A UPI ID looks like name@bank.')).toBeInTheDocument()
    expect(stub!.calls.some((call) => call.method === 'POST')).toBe(false)

    await userEvent.clear(field('UPI ID'))
    await userEvent.clear(field('IFSC'))
    await userEvent.type(field('IFSC'), 'icic0004321')
    const before = { lease: gets('/api/rent/config'), contacts: gets('/api/property/contacts') }
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() =>
      expect(stub!.calls.find((call) => call.method === 'POST' && call.path === '/api/rent/config')).toBeDefined(),
    )
    // The landlord's own account: no beneficiary, and the landlord is not renamed.
    expect(stub!.calls.find((call) => call.method === 'POST')!.body).toEqual({
      payout_beneficiary_name: null,
      landlord_bank_account: '50100123456789',
      landlord_bank_ifsc: 'ICIC0004321',
      landlord_upi_id: null,
    })
    expect(await screen.findByText('Landlord account saved')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    // The lease and the owner contact it is copied to are both read again.
    await waitFor(() => expect(gets('/api/rent/config')).toBe(before.lease + 1))
    expect(gets('/api/property/contacts')).toBe(before.contacts + 1)
  })

  it('names a custom beneficiary, never renaming the landlord', async () => {
    showLandlord()
    await userEvent.click(await screen.findByRole('button', { name: 'Edit landlord' }))
    const dialog = screen.getByRole('dialog', { name: 'Landlord Account' })
    await userEvent.click(within(dialog).getByRole('radio', { name: 'Custom Beneficiary' }))
    // Unlocked, empty, and required once chosen.
    const name = within(dialog).getByLabelText(/^Beneficiary Name/)
    expect(name).toBeEnabled()
    expect(name).toBeRequired()
    expect(name).toHaveValue('')
    expect(name).toHaveAttribute('placeholder', 'e.g. Anita Sharma')
    expect(name).toHaveAccessibleDescription(
      'Whose bank account or UPI ID receives payouts (e.g., spouse, parent, family business).',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(within(dialog).getByText('Enter the beneficiary name.')).toBeInTheDocument()
    // Anything that reads as code is refused before it is sent, as the backend would.
    await userEvent.type(name, "'; DROP TABLE household_contact; --")
    expect(within(dialog).getByText('Beneficiary name contains invalid characters.')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(stub!.calls.some((call) => call.method === 'POST')).toBe(false)
    await userEvent.clear(name)

    await userEvent.type(name, ' Anita Yadav ')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(stub!.calls.some((call) => call.method === 'POST')).toBe(true))
    expect(stub!.calls.find((call) => call.method === 'POST')!.body).toEqual({
      payout_beneficiary_name: 'Anita Yadav',
      landlord_bank_account: '50100123456789',
      landlord_bank_ifsc: 'HDFC0001234',
      landlord_upi_id: 'kumar.yadav@okhdfcbank',
    })
  })

  it("shows only the payout beneficiary when the account is in someone else's name", async () => {
    showLandlord({ config: { ...LEASE.config, payout_beneficiary_name: 'Anita Yadav' } }, [
      { ...OWNER, beneficiary_name: 'Anita Yadav' },
    ])
    const facts = screen.getByLabelText('Landlord account')
    await waitFor(() => expect(facts).toHaveTextContent('Anita Yadav'))
    expect(factsIn(facts).slice(0, 3)).toEqual([
      'Payout Beneficiary: Anita Yadav',
      'Bank Account: 50100123456789',
      'IFSC: HDFC0001234',
    ])
    expect(factsIn(facts)).toHaveLength(4)
    // No landlord's name beside it.
    expect(facts).not.toHaveTextContent('Kumar Yadav')

    // The dialog opens on the beneficiary; choosing the landlord's own account clears it.
    await userEvent.click(screen.getByRole('button', { name: 'Edit landlord' }))
    const dialog = screen.getByRole('dialog', { name: 'Landlord Account' })
    expect(within(dialog).getByRole('radio', { name: 'Custom Beneficiary' })).toHaveAttribute('aria-checked', 'true')
    expect(within(dialog).getByLabelText(/^Beneficiary Name/)).toHaveValue('Anita Yadav')
    await userEvent.click(within(dialog).getByRole('radio', { name: 'Same as Landlord (Kumar Yadav)' }))
    // Locked back to the landlord's name.
    expect(within(dialog).getByLabelText(/^Beneficiary Name/)).toBeDisabled()
    expect(within(dialog).getByLabelText(/^Beneficiary Name/)).toHaveValue('Kumar Yadav')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(stub!.calls.some((call) => call.method === 'POST')).toBe(true))
    const body = stub!.calls.find((call) => call.method === 'POST')!.body
    expect(body).toMatchObject({ payout_beneficiary_name: null })
    expect(body).not.toHaveProperty('landlord_name')
  })
})

/* ----------------------------------------------------------- the navigation -- */

describe('Payment Pools in the main navigation', () => {
  it('sits between Residents and Statements, and is current on /payments', async () => {
    stub = installFetchStub({
      routes: {
        '/health': { status: 'ok', schema_version: 15 },
        '/api/apartments': { items: [APARTMENT], default_apartment_id: 'home' },
        '/api/setup/state': { next_step: 'done', is_complete: true, apartment_count: 1 },
      } as never,
    })
    render(
      <MemoryRouter initialEntries={['/payments']}>
        <ToastProvider>
          <Layout>
            <p>Payments page</p>
          </Layout>
        </ToastProvider>
      </MemoryRouter>,
    )
    const nav = screen.getByRole('navigation', { name: 'Main navigation' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual([
      'Month Hub',
      'Housing',
      'Services & Utilities',
      'Activity Ledger',
      'Residents',
      'Payment Pools',
      'Statements',
    ])
    const pools = within(nav).getByRole('link', { name: 'Payment Pools' })
    expect(pools).toHaveAttribute('href', '/payments')
    expect(pools).toHaveAttribute('aria-current', 'page')
    await screen.findByText('Payments page')
    // The page has its own actions and filters: no month bar, no generic Record button.
    await waitFor(() => expect(gets('/api/setup/state')).toBe(1))
    expect(screen.queryByText('Settlement Period')).toBeNull()
    expect(screen.queryByRole('button', { name: /Record Transaction/ })).toBeNull()
  })
})

/* --------------------------------------------------------- the passbooks -- */

function line(overrides: Partial<StatementEntry>): StatementEntry {
  return {
    entry_id: 'entry-x',
    account: 'pool',
    date: '2026-11-01',
    seq: 1,
    source_type: 'payment',
    category: 'Kitty Payment',
    description: 'Payment into House Kitty',
    counterparty_name: 'Bob',
    counterparty_resident_id: 'bob',
    direction: 'in',
    amount_paise: 100000,
    balance_paise: 100000,
    payment_mode: null,
    reference: null,
    payment_id: null,
    note: null,
    ...overrides,
  }
}

const POOL_STATEMENT: PoolStatement = {
  account: { instance_id: '__pool__', name: 'Flat 402 Kitty' },
  items: [
    line({
      entry_id: 'expense:broadband',
      date: '2026-11-05',
      seq: 4,
      source_type: 'expense',
      category: 'House Expense',
      description: 'Broadband',
      counterparty_name: 'ACT Fibernet',
      counterparty_resident_id: null,
      direction: 'out',
      amount_paise: 120000,
      balance_paise: -1920000,
    }),
    line({
      entry_id: 'rent:c1',
      date: '2026-11-04',
      seq: 3,
      source_type: 'rent_cycle',
      category: 'Rent Payout',
      description: 'Rent payout · November 2026',
      counterparty_name: 'Kumar Yadav',
      counterparty_resident_id: null,
      direction: 'out',
      amount_paise: 2850000,
      balance_paise: -1800000,
      reference: 'UTR-NOV',
    }),
    line({
      entry_id: 'payment:p2',
      date: '2026-11-03',
      seq: 2,
      category: 'Rent Collection',
      description: 'Rent for November 2026',
      counterparty_name: 'Alice',
      counterparty_resident_id: 'alice',
      amount_paise: 950000,
      balance_paise: 1050000,
      payment_mode: 'upi',
      reference: 'UPI-77',
      payment_id: 'p2',
      note: 'Rent for November 2026',
    }),
    line({ entry_id: 'payment:p1', payment_id: 'p1' }),
  ],
  total_in_paise: 1050000,
  total_out_paise: 2970000,
  closing_balance_paise: -1920000,
}

const LANDLORD_STATEMENT: LandlordStatement = {
  account: { instance_id: '__landlord__', name: 'Kumar Yadav' },
  items: [
    line({
      entry_id: 'rent:c1',
      account: 'landlord',
      date: '2026-11-04',
      seq: 2,
      source_type: 'rent_cycle',
      category: 'Rent Payout',
      description: 'Monthly rent · November 2026',
      counterparty_name: 'House Kitty',
      counterparty_resident_id: null,
      amount_paise: 2850000,
      balance_paise: 7850000,
    }),
    line({
      entry_id: 'deposit:d1',
      account: 'landlord',
      date: '2026-10-05',
      source_type: 'deposit',
      category: 'Security Deposit',
      description: 'Security deposit · Room 1',
      counterparty_name: 'Alice',
      counterparty_resident_id: 'alice',
      amount_paise: 5000000,
      balance_paise: 5000000,
    }),
  ],
  total_rent_paid_paise: 2850000,
  deposit_held_paise: 5000000,
  total_in_paise: 7850000,
  total_out_paise: 0,
  closing_balance_paise: 7850000,
}

const ALICE_RENT = {
  payment_id: 'p2',
  payer_resident_id: 'alice',
  payer_display_name: 'Alice',
  amount_paise: 950000,
  payment_date: '2026-11-03',
  payment_kind: 'to_pool',
  payee_or_provider: null,
  reference: 'UPI-77',
  note: 'Rent for November 2026',
  status: 'active',
  expense_id: null,
  reverses_payment_id: null,
  applied_paise: 950000,
  unapplied_paise: 0,
  application_count: 1,
  linked_expense_description: null,
}

function showPage() {
  stub = installFetchStub({
    routes: {
      '/health': { status: 'ok', schema_version: 15 },
      '/api/apartments': { items: [APARTMENT], default_apartment_id: 'home' },
      '/api/setup/state': { next_step: 'done', is_complete: true, apartment_count: 1 },
      '/api/residents': RESIDENTS,
      '/api/payments/pools/statement': {
        apartment_id: 'home',
        pool_statement: POOL_STATEMENT,
        landlord_statement: LANDLORD_STATEMENT,
      },
      '/api/payments': { items: [ALICE_RENT], total: 1, total_amount_paise: 950000, limit: 25, offset: 0 },
      '/api/payments/p2': { ...ALICE_RENT, applications: [], linked_expense: null, reversal: null },
      '/api/credits': { items: [], total: 0, total_paise: 0, by_resident_paise: {} },
      '/api/provider-advances': { items: [], total_paise: 0, unlinked_count: 0 },
      '/api/adjustments': { items: [], total: 0, limit: 200, offset: 0 },
      '/api/rent/config': LEASE,
      '/api/property/contacts': { items: [OWNER], total: 1, roles_on_file: ['owner'] },
    } as never,
  })
  render(
    <MemoryRouter initialEntries={['/payments']}>
      <ToastProvider>
        <Layout>
          <PaymentsPage />
        </Layout>
      </ToastProvider>
    </MemoryRouter>,
  )
}

/** A table's body rows, each read back as its cells' text. */
function bodyRows(table: HTMLElement) {
  const [, body] = within(table).getAllByRole('rowgroup')
  return within(body)
    .getAllByRole('row')
    .map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))
}

describe('Payment Pools page', () => {
  it('opens on the house kitty passbook, with a running balance', async () => {
    showPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Payment Pools' })).toBeInTheDocument()
    const tabs = within(screen.getByRole('tablist', { name: 'Payment views' })).getAllByRole('tab')
    await waitFor(() =>
      expect(tabs.map((tab) => tab.textContent)).toEqual([
        'House Kitty Statement',
        'Landlord Statement',
        'All Payments',
        'Unapplied Credits (0)',
        'Provider Advances (0)',
        'Adjustments (0)',
      ]),
    )
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')

    const table = await screen.findByRole('table', { name: 'House Kitty Statement' })
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Date',
      'Particulars',
      'Counterparty',
      'In / Credit (+)',
      'Out / Debit (−)',
      'Balance',
    ])
    expect(bodyRows(table)).toEqual([
      // A household expense the kitty paid itself.
      ['5 Nov 2026', 'House ExpenseBroadband', 'ACT Fibernet', '—', '−₹1,200.00', '−₹19,200.00'],
      ['4 Nov 2026', 'Rent PayoutRent payout · November 2026', 'Kumar Yadav', '—', '−₹28,500.00', '−₹18,000.00'],
      ['3 Nov 2026', 'Rent CollectionRent for November 2026', 'Alice', '+₹9,500.00', '—', '₹10,500.00'],
      ['1 Nov 2026', 'Kitty PaymentPayment into House Kitty', 'Bob', '+₹1,000.00', '—', '₹1,000.00'],
    ])
    expect(within(table).getByText('Totals and closing balance').closest('tr')).toHaveTextContent(
      '+₹10,500.00−₹29,700.00−₹19,200.00',
    )
    // The banner explaining the four kinds of record is no longer on the page.
    expect(screen.queryByText('These are four different things')).toBeNull()
  })

  it("shows each account's live balance at the foot of its card", async () => {
    showPage()
    const pool = await screen.findByLabelText('House kitty balance')
    expect(factsIn(pool)).toEqual([
      'Total In (+): ₹10,500.00',
      'Total Out (−): ₹29,700.00',
      'Kitty Balance: −₹19,200.00',
    ])
    expect(factsIn(screen.getByLabelText('Landlord balance'))).toEqual([
      'Rent Paid: ₹28,500.00',
      'Deposit Held: ₹50,000.00',
      'Total Paid: ₹78,500.00',
    ])
  })

  it('opens a passbook line in full, and from it the payment, which can be reversed', async () => {
    showPage()
    const table = await screen.findByRole('table', { name: 'House Kitty Statement' })
    // A click anywhere on the row opens it.
    const [, body] = within(table).getAllByRole('rowgroup')
    await userEvent.click(within(within(body).getAllByRole('row')[2]).getAllByRole('cell')[2])
    const dialog = screen.getByRole('dialog', { name: 'Statement Entry' })
    expect(dialog).toHaveTextContent('Flat 402 Kitty · 3 Nov 2026')
    expect(factsIn(dialog)).toEqual([
      'Date: 3 Nov 2026',
      'Account: Flat 402 Kitty',
      'Category: Rent Collection',
      'Direction: In / Credit (+)',
      'Description: Rent for November 2026',
      'Counterparty: Alice',
      'Amount: ₹9,500.00',
      'Running Balance: ₹10,500.00',
      'Payment Mode: UPI',
      'Reference / UTR: UPI-77',
    ])
    await userEvent.click(within(dialog).getByRole('button', { name: 'View payment' }))
    const payment = await screen.findByRole('dialog', { name: 'Payment' })
    await userEvent.click(await within(payment).findByRole('button', { name: 'Reverse' }))
    expect(screen.getByRole('dialog', { name: 'Reverse this payment?' })).toBeInTheDocument()
  })

  it('opens a line from the keyboard, and closes it again', async () => {
    showPage()
    const table = await screen.findByRole('table', { name: 'House Kitty Statement' })
    const trigger = within(table).getByRole('button', { name: /^Entry details: Rent Payout, 4 Nov 2026/ })
    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog')
    trigger.focus()
    await userEvent.keyboard('{Enter}')
    const dialog = screen.getByRole('dialog', { name: 'Statement Entry' })
    // Rent paid out of the pool has no payment of its own to open.
    expect(within(dialog).queryByRole('button', { name: 'View payment' })).toBeNull()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveFocus()
  })

  it('keeps the filters folded away until asked for, then narrows the passbook', async () => {
    showPage()
    const table = await screen.findByRole('table', { name: 'House Kitty Statement' })
    expect(screen.queryByLabelText('Direction')).toBeNull()
    const toggle = screen.getByRole('button', { name: 'Filters' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await userEvent.selectOptions(screen.getByLabelText('Direction'), 'out')
    expect(bodyRows(table).map((row) => row[1])).toEqual([
      'House ExpenseBroadband',
      'Rent PayoutRent payout · November 2026',
    ])
    expect(screen.getByRole('button', { name: 'Filters (1)' })).toBeInTheDocument()
  })

  it("shows the landlord's passbook on its own tab", async () => {
    showPage()
    await userEvent.click(await screen.findByRole('tab', { name: 'Landlord Statement' }))
    const table = await screen.findByRole('table', { name: 'Landlord Statement' })
    expect(bodyRows(table)).toEqual([
      ['4 Nov 2026', 'Rent PayoutMonthly rent · November 2026', 'House Kitty', '+₹28,500.00', '—', '₹78,500.00'],
      ['5 Oct 2026', 'Security DepositSecurity deposit · Room 1', 'Alice', '+₹50,000.00', '—', '₹50,000.00'],
    ])
  })

  it('explains the four kinds of record in a dialog, not on the page', async () => {
    showPage()
    await userEvent.click(await screen.findByRole('button', { name: 'How it works' }))
    const dialog = screen.getByRole('dialog', { name: 'How Payment Pools & Transfers Work' })
    for (const kind of ['Into the pool', 'Paid a provider', 'Refund', 'Adjustment']) {
      expect(within(dialog).getByText(kind)).toBeInTheDocument()
    }
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('lists all payments in six columns and opens one with Reverse in its footer', async () => {
    showPage()
    await userEvent.click(await screen.findByRole('tab', { name: 'All Payments' }))
    const table = await screen.findByRole('table', { name: 'Payments' })
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Date',
      'Resident',
      'Kind',
      'Counterparty',
      'Amount',
      'Status',
    ])
    // Applied, credit and the actions moved into the payment's own dialog.
    expect(within(table).queryByRole('button', { name: 'Reverse' })).toBeNull()
    await userEvent.click(within(table).getByRole('button', { name: /^Payment details: Alice, Into the pool/ }))
    const payment = await screen.findByRole('dialog', { name: 'Payment' })
    expect(await within(payment).findByRole('button', { name: 'Reverse' })).toBeInTheDocument()
  })
})

describe('the account cards with their passbooks', () => {
  it("puts the pool's totals under its account", () => {
    stub = installFetchStub({ routes: {} as never })
    render(
      <MemoryRouter>
        <ToastProvider>
          <HouseholdPoolCard apartment={APARTMENT} statement={POOL_STATEMENT} onSaved={() => {}} />
        </ToastProvider>
      </MemoryRouter>,
    )
    expect(screen.getByLabelText('House kitty balance')).toHaveClass('grid-cols-3')
    expect(factsIn(screen.getByLabelText('House kitty balance'))[2]).toBe('Kitty Balance: −₹19,200.00')
  })

  it('shows no strip until the passbook has loaded', () => {
    stub = installFetchStub({ routes: {} as never })
    render(
      <MemoryRouter>
        <ToastProvider>
          <HouseholdPoolCard apartment={APARTMENT} onSaved={() => {}} />
        </ToastProvider>
      </MemoryRouter>,
    )
    expect(screen.queryByLabelText('House kitty balance')).toBeNull()
  })
})
