/**
 * The resident contact card, the scan-to-pay QR code, and the profile editor.
 *
 * The links are what a phone acts on, so they are pinned exactly: a tel: number
 * with nothing but digits, a wa.me number in full international form, and a
 * UPI link that names the payee and never an amount.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  ContactCard,
  EditProfileDialog,
  UpiCard,
  telHref,
  upiPayUri,
  whatsappHref,
} from './ResidentProfile'
import { installFetchStub, type Recorded } from '../test/server'
import type { Resident } from '../api/types'

const resident: Resident = {
  resident_id: 'bob',
  apartment_id: 'home',
  display_name: 'Bob Rao',
  active_from: '2026-08-01',
  active_until: null,
  is_active: 1,
  email: 'bob@example.com',
  phone: '+91 98765 43210',
  notes: null,
  upi_id: 'bob.rao@okaxis',
  emergency_contact_name: 'Meera Rao',
  emergency_contact_phone: '91234 56780',
  emergency_contact_relation: 'Mother',
  created_at: null,
  updated_at: null,
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

describe('contact links', () => {
  it('builds a tel: link from the digits only', () => {
    expect(telHref('+91 98765 43210')).toBe('tel:+919876543210')
    expect(telHref('(080) 2345-6789')).toBe('tel:08023456789')
  })

  it('builds a wa.me link in international form, or none for a short number', () => {
    expect(whatsappHref('+91 98765 43210')).toBe('https://wa.me/919876543210')
    // A bare ten-digit number is taken to be Indian.
    expect(whatsappHref('98765 43210')).toBe('https://wa.me/919876543210')
    expect(whatsappHref('098765 43210')).toBe('https://wa.me/919876543210')
    expect(whatsappHref('+44 20 7946 0958')).toBe('https://wa.me/442079460958')
    expect(whatsappHref('12345')).toBeNull()
  })

  it('names the payee in the UPI link and never sets an amount', () => {
    const uri = upiPayUri('bob.rao@okaxis', 'Bob Rao & Co')
    expect(uri).toBe('upi://pay?pa=bob.rao@okaxis&pn=Bob%20Rao%20%26%20Co&cu=INR')
    expect(uri).not.toContain('am=')
  })
})

describe('ContactCard', () => {
  it('offers call, WhatsApp and mail shortcuts and shows the emergency contact', () => {
    render(<ContactCard resident={resident} onEdit={() => {}} />)
    const card = screen.getByRole('region', { name: 'Contact & Emergency' })
    const call = within(card).getAllByRole('link', { name: 'Call' })[0]
    expect(call).toHaveAttribute('href', 'tel:+919876543210')
    expect(within(card).getByRole('link', { name: 'WhatsApp' })).toHaveAttribute(
      'href',
      'https://wa.me/919876543210',
    )
    expect(within(card).getByRole('link', { name: 'Mail' })).toHaveAttribute(
      'href',
      'mailto:bob@example.com',
    )

    const emergency = within(card).getByRole('region', { name: 'Emergency contact' })
    expect(emergency).toHaveTextContent('Meera Rao · Mother')
    expect(emergency).toHaveTextContent('91234 56780')
    expect(
      within(emergency).getByRole('link', { name: 'Call emergency contact Meera Rao' }),
    ).toHaveAttribute('href', 'tel:9123456780')
  })

  it('says what is missing instead of showing dead shortcuts', () => {
    render(
      <ContactCard
        resident={{
          ...resident,
          phone: null,
          email: null,
          emergency_contact_name: null,
          emergency_contact_phone: null,
          emergency_contact_relation: null,
        }}
        onEdit={() => {}}
      />,
    )
    expect(screen.getAllByText('Not recorded')).toHaveLength(2)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText(/No emergency contact recorded/)).toBeInTheDocument()
  })

  it('opens the editor from Edit Profile', async () => {
    const onEdit = vi.fn()
    render(<ContactCard resident={resident} onEdit={onEdit} />)
    await userEvent.click(screen.getByRole('button', { name: 'Edit Profile' }))
    expect(onEdit).toHaveBeenCalledOnce()
  })
})

describe('UpiCard', () => {
  it('shows a QR code and the full UPI ID with a copy button', () => {
    render(<UpiCard resident={resident} />)
    const card = screen.getByRole('region', { name: 'Scan to Pay / UPI Settlement' })
    expect(
      within(card).getByRole('img', { name: 'UPI payment QR code for Bob Rao' }),
    ).toBeInTheDocument()
    expect(within(card).getByText('bob.rao@okaxis')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()
  })

  it('is not shown for a resident with no UPI ID', () => {
    const { container } = render(<UpiCard resident={{ ...resident, upi_id: null }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('EditProfileDialog', () => {
  const posted = () =>
    stub!.calls.find((call) => call.method === 'PATCH' && call.path === '/api/residents/bob')

  it('saves contact details and the emergency contact, sending blanks as null', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...resident } } })
    const onSaved = vi.fn()
    render(<EditProfileDialog resident={resident} onClose={() => {}} onSaved={onSaved} />)

    const dialog = screen.getByRole('dialog', { name: "Edit Bob Rao's profile" })
    expect(within(dialog).getByLabelText('Phone')).toHaveValue('+91 98765 43210')
    expect(within(dialog).getByLabelText('Next-of-kin name')).toHaveValue('Meera Rao')

    await userEvent.clear(within(dialog).getByLabelText('Email'))
    await userEvent.clear(within(dialog).getByLabelText('Relationship'))
    await userEvent.type(within(dialog).getByLabelText('Relationship'), ' Aunt ')
    await userEvent.clear(within(dialog).getByLabelText('Emergency phone'))
    await userEvent.type(within(dialog).getByLabelText('Emergency phone'), '+91 90000 11111')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Profile' }))

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toEqual({
      phone: '+91 98765 43210',
      email: null,
      upi_id: 'bob.rao@okaxis',
      emergency_contact_name: 'Meera Rao',
      emergency_contact_relation: 'Aunt',
      emergency_contact_phone: '+91 90000 11111',
    })
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce())
  })

  it('refuses a malformed email or UPI ID before saving', async () => {
    stub = installFetchStub({ routes: {} })
    render(<EditProfileDialog resident={resident} onClose={() => {}} onSaved={() => {}} />)
    const save = screen.getByRole('button', { name: 'Save Profile' })
    expect(save).toBeEnabled()

    const upi = screen.getByLabelText('UPI ID')
    await userEvent.clear(upi)
    await userEvent.type(upi, 'bob')
    expect(screen.getByText('A UPI ID looks like name@bank.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    await userEvent.clear(upi)
    expect(save).toBeEnabled()

    await userEvent.clear(screen.getByLabelText('Email'))
    await userEvent.type(screen.getByLabelText('Email'), 'bob@')
    expect(screen.getByText('Enter a valid email address.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    expect(posted()).toBeUndefined()
  })
})
