/**
 * Stakeholders & Contacts: the grouping, the one-click shortcuts, and the
 * form's checks, which match the backend's so a mistake is caught before it
 * is sent.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ContactsSection } from './PropertyContacts'
import { ToastProvider } from '../../components/Toast'
import { installFetchStub, type Recorded } from '../../test/server'
import type { HouseholdContact } from '../../api/types'

function contact(overrides: Partial<HouseholdContact>): HouseholdContact {
  return {
    contact_id: 'contact-x',
    apartment_id: 'home',
    role: 'caretaker',
    name: 'Someone',
    phone: null,
    email: null,
    pan: null,
    bank_account_no: null,
    bank_ifsc: null,
    upi_id: null,
    agency_name: null,
    fee_amount_paise: null,
    fee_status: null,
    fee_display: null,
    responsibilities: null,
    notes: null,
    created_at: '2026-10-01T10:00:00+05:30',
    updated_at: '2026-10-01T10:00:00+05:30',
    ...overrides,
  }
}

const OWNER = contact({
  contact_id: 'owner-1',
  role: 'owner',
  name: 'Kumar Yadav',
  phone: '+91 98765 43210',
  email: 'kumar@example.com',
  pan: 'ABCDE1234F',
  bank_account_no: '123456789012',
  bank_ifsc: 'HDFC0001234',
})
const BROKER = contact({
  contact_id: 'broker-1',
  role: 'broker',
  name: 'Ravi',
  agency_name: 'NestAway Realty',
  phone: '9876500000',
  fee_amount_paise: 2850000,
  fee_status: 'pending',
  fee_display: '₹28,500.00',
})
const CARETAKER = contact({
  contact_id: 'caretaker-1',
  name: 'Mohan',
  phone: '9876511111',
  responsibilities: 'Plumbing, gate passes',
})

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function open(items: HouseholdContact[] = [OWNER, BROKER, CARETAKER], extra = {}) {
  stub = installFetchStub({
    routes: {
      '/api/property/contacts': { items, total: items.length, roles_on_file: [] },
      ...extra,
    } as never,
  })
  render(
    <MemoryRouter>
      <ToastProvider>
        <ContactsSection />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path === path)

/** The step pills in the contact dialog, by name. */
const stepNames = (dialog: HTMLElement) =>
  within(within(dialog).getByRole('tablist', { name: 'Contact steps' }))
    .getAllByRole('tab')
    .map((tab) => tab.textContent)

const goTo = (dialog: HTMLElement, step: string) =>
  userEvent.click(within(dialog).getByRole('tab', { name: step }))

describe('ContactsSection', () => {
  it('groups contacts into owner, broker, and caretaker & society cards', async () => {
    open()
    const owner = await screen.findByRole('list', { name: 'Property Owner (Landlord)' })
    const card = within(owner).getByRole('listitem', { name: 'Kumar Yadav' })
    expect(card).toHaveTextContent('PAN ABCDE1234F')
    // The owner is paid through Payment Pools, so their bank details are kept there.
    expect(card).not.toHaveTextContent('123456789012')
    expect(card).not.toHaveTextContent('HDFC0001234')
    expect(within(card).getByRole('link', { name: 'Payment Pools' })).toHaveAttribute('href', '/payments')
    expect(within(card).getByRole('link', { name: 'Call Kumar Yadav' })).toHaveAttribute(
      'href',
      'tel:+919876543210',
    )
    expect(within(card).getByRole('link', { name: 'WhatsApp Kumar Yadav' })).toHaveAttribute(
      'href',
      'https://wa.me/919876543210',
    )
    expect(within(card).getByRole('link', { name: 'Mail Kumar Yadav' })).toHaveAttribute(
      'href',
      'mailto:kumar@example.com',
    )

    const broker = screen.getByRole('listitem', { name: 'Ravi' })
    expect(broker).toHaveTextContent('Broker · NestAway Realty')
    expect(broker).toHaveTextContent('₹28,500.00')
    expect(within(broker).getByText('Pending')).toBeInTheDocument()
    expect(within(broker).queryByRole('link', { name: /Mail/ })).toBeNull()

    const society = screen.getByRole('list', { name: 'Caretaker & Society' })
    expect(within(society).getByRole('listitem', { name: 'Mohan' })).toHaveTextContent(
      'Plumbing, gate passes',
    )
  })

  it('says what to add when a group is empty', async () => {
    open([])
    expect(await screen.findByText(/Add the owner, with their PAN/)).toBeInTheDocument()
    expect(screen.getByText(/Add the broker who found the flat/)).toBeInTheDocument()
  })

  it('adds an owner over three steps, checking each field', async () => {
    open([], { 'POST /api/property/contacts': OWNER })
    await userEvent.click(await screen.findByRole('button', { name: 'Add Contact' }))
    const dialog = screen.getByRole('dialog')
    // Until a role is chosen there is only the first step.
    expect(stepNames(dialog)).toEqual(['1. Contact Identity'])
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(within(dialog).getByText('Choose what this contact is to the household.')).toBeInTheDocument()
    expect(within(dialog).getByText('Enter their name.')).toBeInTheDocument()

    await userEvent.selectOptions(within(dialog).getByLabelText(/Role/), 'owner')
    expect(stepNames(dialog)).toEqual([
      '1. Contact Identity',
      '2. Tax Identity (HRA)',
      '3. Payout & Banking',
    ])
    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Kumar Yadav')
    await userEvent.type(within(dialog).getByLabelText(/Phone/), '12345')
    // A malformed value is pointed out as it is typed.
    expect(within(dialog).getByText('Enter a phone number of 7 to 15 digits.')).toBeInTheDocument()
    await userEvent.clear(within(dialog).getByLabelText(/Phone/))
    await userEvent.type(within(dialog).getByLabelText(/Phone/), '+91 98765 43210')
    await userEvent.type(within(dialog).getByLabelText(/Email/), 'kumar@example.com')
    // Step 1 holds only identity.
    expect(within(dialog).queryByLabelText(/PAN/)).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(within(dialog).getByRole('tab', { name: '2. Tax Identity (HRA)' })).toHaveAttribute('aria-selected', 'true')
    expect(within(dialog).getByText(/Required for flatmates claiming HRA/)).toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText(/PAN/), 'ABCD1234F')
    expect(within(dialog).getByText('A PAN looks like ABCDE1234F.')).toBeInTheDocument()
    await userEvent.clear(within(dialog).getByLabelText(/PAN/))
    await userEvent.type(within(dialog).getByLabelText(/PAN/), 'abcde1234f')
    expect(within(dialog).getByRole('button', { name: 'Back' })).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(within(dialog).getByLabelText(/Bank account number/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/IFSC/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/UPI ID/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Notes/)).toBeInTheDocument()
    // Moving between steps saved nothing.
    expect(sent('POST', '/api/property/contacts')).toBeUndefined()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Add Contact' }))
    await waitFor(() => expect(sent('POST', '/api/property/contacts')).toBeDefined())
    expect(sent('POST', '/api/property/contacts')!.body).toEqual({
      role: 'owner',
      name: 'Kumar Yadav',
      phone: '+91 98765 43210',
      email: 'kumar@example.com',
      notes: null,
      pan: 'ABCDE1234F',
      bank_account_no: null,
      bank_ifsc: null,
      upi_id: null,
      agency_name: null,
      fee_amount_paise: null,
      fee_status: null,
      responsibilities: null,
    })
    expect(await screen.findByText('Kumar Yadav added')).toBeInTheDocument()
  })

  it('marks a step holding a bad value, and goes there on save', async () => {
    open([OWNER], { 'PATCH /api/property/contacts/owner-1': OWNER })
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Kumar Yadav' }))
    const dialog = screen.getByRole('dialog')
    const phone = within(dialog).getByLabelText(/Phone/)
    await userEvent.clear(phone)
    await userEvent.type(phone, '12345')
    await goTo(dialog, '3. Payout & Banking')
    expect(within(dialog).getByRole('tab', { name: '1. Contact Identity (needs attention)' })).toBeInTheDocument()
    // Saving takes the operator to the step to fix rather than sending anything.
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(within(dialog).getByRole('tab', { name: '1. Contact Identity' })).toHaveAttribute('aria-selected', 'true')
    expect(within(dialog).getByText('Enter a phone number of 7 to 15 digits.')).toBeInTheDocument()
    expect(sent('PATCH', '/api/property/contacts/owner-1')).toBeUndefined()
  })

  it('asks a broker for their agency and fee, in integer paise', async () => {
    open([], { 'POST /api/property/contacts': BROKER })
    await userEvent.click(await screen.findByRole('button', { name: 'Add Contact' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByLabelText(/Role/), 'broker')
    expect(stepNames(dialog)).toEqual([
      '1. Contact Identity',
      '2. Brokerage & Agency',
      '3. Payout & Notes',
    ])
    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Ravi')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(within(dialog).queryByLabelText(/PAN/)).toBeNull()
    await userEvent.type(within(dialog).getByLabelText('Agency'), 'NestAway Realty')
    await userEvent.type(within(dialog).getByLabelText(/Brokerage fee/), '28500.50')
    await userEvent.selectOptions(within(dialog).getByLabelText(/Fee status/), 'paid')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(within(dialog).queryByLabelText(/Bank account number/)).toBeNull()
    await userEvent.type(within(dialog).getByLabelText(/UPI ID/), 'ravi@okaxis')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add Contact' }))
    await waitFor(() => expect(sent('POST', '/api/property/contacts')).toBeDefined())
    const body = sent('POST', '/api/property/contacts')!.body as Record<string, unknown>
    expect(body.fee_amount_paise).toBe(2850050)
    expect(body.fee_status).toBe('paid')
    expect(body.agency_name).toBe('NestAway Realty')
    expect(body.upi_id).toBe('ravi@okaxis')
    expect(body.pan).toBeNull()
  })

  it('gives other roles two steps, and follows a change of role', async () => {
    open([CARETAKER], { 'PATCH /api/property/contacts/caretaker-1': CARETAKER })
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Mohan' }))
    const dialog = screen.getByRole('dialog')
    expect(stepNames(dialog)).toEqual(['1. Contact Identity', '2. Duties & Notes'])
    await goTo(dialog, '2. Duties & Notes')
    expect(within(dialog).getByLabelText(/Responsibilities/)).toHaveValue('Plumbing, gate passes')
    await goTo(dialog, '1. Contact Identity')
    await userEvent.selectOptions(within(dialog).getByLabelText(/Role/), 'owner')
    expect(stepNames(dialog)).toHaveLength(3)
    await userEvent.selectOptions(within(dialog).getByLabelText(/Role/), 'caretaker')
    expect(stepNames(dialog)).toEqual(['1. Contact Identity', '2. Duties & Notes'])
  })

  it('saves only from the last step', async () => {
    open([CARETAKER], { 'PATCH /api/property/contacts/caretaker-1': CARETAKER })
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Mohan' }))
    const dialog = screen.getByRole('dialog')
    // The dialog is named like the contact's own card.
    expect(within(dialog).getByRole('heading', { name: 'Mohan' })).toBeInTheDocument()
    // No Save before the end of the form, in the header or the footer.
    expect(within(dialog).queryByRole('button', { name: 'Save Changes' })).toBeNull()
    const phone = within(dialog).getByLabelText(/Phone/)
    await userEvent.clear(phone)
    await userEvent.type(phone, '9876522222')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(within(dialog).getAllByRole('button', { name: 'Save Changes' })).toHaveLength(1)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(sent('PATCH', '/api/property/contacts/caretaker-1')).toBeDefined())
    expect(sent('PATCH', '/api/property/contacts/caretaker-1')!.body).toMatchObject({
      role: 'caretaker',
      name: 'Mohan',
      phone: '9876522222',
      responsibilities: 'Plumbing, gate passes',
    })
  })

  it('removes a contact after confirmation', async () => {
    open([CARETAKER], { 'DELETE /api/property/contacts/caretaker-1': {} })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove Mohan' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove Contact' }))
    await waitFor(() => expect(sent('DELETE', '/api/property/contacts/caretaker-1')).toBeDefined())
    expect(await screen.findByText('Mohan removed')).toBeInTheDocument()
  })
})
