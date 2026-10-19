/**
 * The resident contact card, the scan-to-pay QR code, and the profile editor.
 *
 * The links are what a phone acts on, so they are pinned exactly: a tel: number
 * with nothing but digits, a wa.me number in full international form, and a
 * UPI link that names the payee and never an amount.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  BuildingLogisticsCard,
  ContactCard,
  EditKycDialog,
  EditLogisticsDialog,
  EditProfileDialog,
  EditTenancyTermsDialog,
  KycComplianceCard,
  ProfessionalCard,
  TenancyTermsCard,
  UpiCard,
  maskedIdNumber,
  telHref,
  upiPayUri,
  vehicleSummary,
  whatsappHref,
} from './ResidentProfile'
import { ToastProvider } from '../components/Toast'
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
  profession: null,
  employer: null,
  work_mode: null,
  blood_group: 'O+',
  secondary_emergency_name: null,
  secondary_emergency_phone: null,
  secondary_emergency_relation: null,
  notice_period_days: null,
  lock_in_until: null,
  is_lease_signatory: 0,
  keys_issued_notes: null,
  vehicle_type: null,
  vehicle_reg_no: null,
  parking_slot: null,
  society_rfid_tag: null,
  id_document_type: null,
  id_document_number: null,
  home_city: null,
  home_state: null,
  police_verification_status: 'pending',
  society_noc_status: 'pending',
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
      blood_group: 'O+',
      secondary_emergency_name: null,
      secondary_emergency_relation: null,
      secondary_emergency_phone: null,
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

/* --------------------------------------------------------------- dossier -- */

const patched = () =>
  stub!.calls.find((call) => call.method === 'PATCH' && call.path === '/api/residents/bob')

/** A dossier card as the resident page shows it: with toasts, and a router for its links. */
function showCard(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <ToastProvider>{ui}</ToastProvider>
    </MemoryRouter>,
  )
}

describe('the emergency section', () => {
  it('shows the blood group and a second contact with a way to call them', () => {
    render(
      <ContactCard
        resident={{
          ...resident,
          secondary_emergency_name: 'Arjun Rao',
          secondary_emergency_relation: 'Brother',
          secondary_emergency_phone: '+91 99887 66554',
        }}
        onEdit={() => {}}
      />,
    )
    const emergency = screen.getByRole('region', { name: 'Emergency contact' })
    expect(emergency).toHaveTextContent('Blood: O+')
    expect(emergency).toHaveTextContent('Second contact')
    expect(emergency).toHaveTextContent('Arjun Rao · Brother')
    expect(
      within(emergency).getByRole('link', { name: 'Call second contact Arjun Rao' }),
    ).toHaveAttribute('href', 'tel:+919988766554')
  })

  it('saves a second contact and the blood group from the profile editor', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...resident } } })
    render(<EditProfileDialog resident={resident} onClose={() => {}} onSaved={() => {}} />)
    const dialog = screen.getByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByLabelText('Blood group'), 'AB-')
    await userEvent.type(within(dialog).getByLabelText('Second contact name'), 'Arjun Rao')
    await userEvent.type(within(dialog).getByLabelText('Second contact phone'), '99887 66554')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Profile' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toMatchObject({
      blood_group: 'AB-',
      secondary_emergency_name: 'Arjun Rao',
      secondary_emergency_relation: null,
      secondary_emergency_phone: '99887 66554',
    })
  })
})

describe('KYC & Verification', () => {
  const kyc = {
    ...resident,
    id_document_type: 'aadhaar' as const,
    id_document_number: '1234 5678 4821',
    home_city: 'Kolkata',
    home_state: 'West Bengal',
    police_verification_status: 'verified' as const,
    society_noc_status: 'pending' as const,
  }

  it('masks an identity number to its last four digits', () => {
    expect(maskedIdNumber('aadhaar', '1234 5678 4821')).toBe('•••• •••• 4821')
    expect(maskedIdNumber('passport', 'K1234567')).toBe('•••• 4567')
    expect(maskedIdNumber('aadhaar', null)).toBeNull()
  })

  it('shows the ID masked, the hometown, both checks, and the way to the vault', () => {
    showCard(<KycComplianceCard resident={kyc} onReload={() => {}} />)
    const card = screen.getByRole('heading', { name: 'KYC & Verification' }).closest('section')!
    expect(card).toHaveTextContent('Aadhaar (•••• •••• 4821)')
    expect(card).not.toHaveTextContent('1234 5678')
    expect(card).toHaveTextContent('Kolkata, West Bengal')
    expect(card).toHaveTextContent('Police verificationVerified')
    expect(card).toHaveTextContent('Society move-in NOCPending')
    expect(within(card).getByRole('link', { name: /View in Document Vault/ })).toHaveAttribute(
      'href',
      '/housing?tab=documents',
    )
  })

  it('keeps the number on file unless a new one is typed, and never shows it', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...kyc } } })
    render(<EditKycDialog resident={kyc} onClose={() => {}} onSaved={() => {}} />)
    const dialog = screen.getByRole('dialog')
    const number = within(dialog).getByLabelText('New ID number')
    expect(number).toHaveValue('')
    expect(number).toHaveAttribute('placeholder', '•••• •••• 4821 on file')
    expect(dialog.innerHTML).not.toContain('5678')

    await userEvent.selectOptions(within(dialog).getByLabelText('Society move-in NOC'), 'approved')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toEqual({
      id_document_type: 'aadhaar',
      home_city: 'Kolkata',
      home_state: 'West Bengal',
      police_verification_status: 'verified',
      society_noc_status: 'approved',
    })
  })

  it('checks a new Aadhaar number, and can remove the one on file', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...kyc } } })
    render(<EditKycDialog resident={kyc} onClose={() => {}} onSaved={() => {}} />)
    const dialog = screen.getByRole('dialog')
    const save = within(dialog).getByRole('button', { name: 'Save' })
    await userEvent.type(within(dialog).getByLabelText('New ID number'), '1234 5678')
    expect(within(dialog).getByText('An Aadhaar number is 12 digits.')).toBeInTheDocument()
    expect(save).toBeDisabled()

    await userEvent.click(within(dialog).getByLabelText('Remove the ID number on file'))
    expect(within(dialog).getByLabelText('New ID number')).toHaveValue('')
    expect(save).toBeEnabled()
    await userEvent.click(save)
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toMatchObject({ id_document_number: null })
  })
})

describe('Tenancy Terms', () => {
  it('reads the terms in words', () => {
    showCard(
      <TenancyTermsCard
        resident={{
          ...resident,
          is_lease_signatory: 1,
          notice_period_days: 30,
          lock_in_until: '2026-12-31',
          keys_issued_notes: '2 keys & AC remote',
        }}
        onReload={() => {}}
      />,
    )
    const card = screen.getByRole('heading', { name: 'Tenancy Terms' }).closest('section')!
    expect(card).toHaveTextContent('Primary Lease Signatory')
    expect(card).toHaveTextContent('30 days notice required')
    expect(card).toHaveTextContent('Lock-in until 31 Dec 2026')
    expect(card).toHaveTextContent('2 keys & AC remote')
  })

  it('says co-tenant and not recorded when nothing is set', () => {
    showCard(<TenancyTermsCard resident={resident} onReload={() => {}} />)
    const card = screen.getByRole('heading', { name: 'Tenancy Terms' }).closest('section')!
    expect(card).toHaveTextContent('Co-tenant')
    expect(within(card).getAllByText('Not recorded')).toHaveLength(3)
    expect(within(card).getByRole('button', { name: 'Edit tenancy terms' })).toHaveTextContent('Edit')
  })

  it('saves whole days and refuses anything else', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...resident } } })
    render(<EditTenancyTermsDialog resident={resident} onClose={() => {}} onSaved={() => {}} />)
    const dialog = screen.getByRole('dialog')
    const save = within(dialog).getByRole('button', { name: 'Save' })
    await userEvent.type(within(dialog).getByLabelText('Notice period (days)'), '30d')
    expect(save).toBeDisabled()
    await userEvent.clear(within(dialog).getByLabelText('Notice period (days)'))
    await userEvent.type(within(dialog).getByLabelText('Notice period (days)'), '30')
    await userEvent.click(within(dialog).getByLabelText(/Signs the master lease/))
    await userEvent.type(within(dialog).getByLabelText('Keys & handover'), '2 keys')
    await userEvent.click(save)
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toEqual({
      is_lease_signatory: true,
      notice_period_days: 30,
      lock_in_until: null,
      keys_issued_notes: '2 keys',
    })
  })
})

describe('Vehicle & Building Access', () => {
  it('names the vehicle with its registration', () => {
    expect(vehicleSummary({ vehicle_type: 'four_wheeler', vehicle_reg_no: 'DL 3C AB 1234' })).toBe(
      '4-Wheeler (DL 3C AB 1234)',
    )
    expect(vehicleSummary({ vehicle_type: 'two_wheeler', vehicle_reg_no: null })).toBe('2-Wheeler')
    expect(vehicleSummary({ vehicle_type: 'none', vehicle_reg_no: 'stale' })).toBe('No vehicle')
    expect(vehicleSummary({ vehicle_type: null, vehicle_reg_no: null })).toBeNull()

    showCard(
      <BuildingLogisticsCard
        resident={{
          ...resident,
          vehicle_type: 'four_wheeler',
          vehicle_reg_no: 'DL 3C AB 1234',
          parking_slot: 'B-12 (Stilt)',
          society_rfid_tag: '#RF-8821',
        }}
        onReload={() => {}}
      />,
    )
    const card = screen.getByRole('heading', { name: 'Vehicle & Building Access' }).closest('section')!
    expect(card).toHaveTextContent('4-Wheeler (DL 3C AB 1234)')
    expect(card).toHaveTextContent('B-12 (Stilt)')
    expect(card).toHaveTextContent('#RF-8821')
  })

  it('upper-cases the registration, and drops it for someone with no vehicle', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...resident } } })
    const { unmount } = render(
      <EditLogisticsDialog resident={resident} onClose={() => {}} onSaved={() => {}} />,
    )
    let dialog = screen.getByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByLabelText('Vehicle'), 'two_wheeler')
    await userEvent.type(within(dialog).getByLabelText('Registration number'), 'ka 05 mn 4321')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toMatchObject({ vehicle_type: 'two_wheeler', vehicle_reg_no: 'KA 05 MN 4321' })
    unmount()
    stub.calls.length = 0

    render(
      <EditLogisticsDialog
        resident={{ ...resident, vehicle_type: 'two_wheeler', vehicle_reg_no: 'KA 05 MN 4321' }}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    dialog = screen.getByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByLabelText('Vehicle'), 'none')
    expect(within(dialog).getByLabelText('Registration number')).toBeDisabled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toMatchObject({ vehicle_type: 'none', vehicle_reg_no: null })
  })
})

describe('Work & Profession', () => {
  it('shows the work mode as a badge and saves only its own fields', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...resident } } })
    const onReload = vi.fn()
    showCard(
      <ProfessionalCard
        resident={{ ...resident, profession: 'Data analyst', employer: 'Acme', work_mode: 'hybrid' }}
        onReload={onReload}
      />,
    )
    const card = screen.getByRole('heading', { name: 'Work & Profession' }).closest('section')!
    expect(card).toHaveTextContent('Data analyst')
    expect(card).toHaveTextContent('Work modeHybrid')

    await userEvent.click(within(card).getByRole('button', { name: 'Edit work and profession' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByLabelText('Work mode'), 'wfh')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toEqual({ profession: 'Data analyst', employer: 'Acme', work_mode: 'wfh' })
    await waitFor(() => expect(onReload).toHaveBeenCalledOnce())
    expect(await screen.findByText('Work details saved')).toBeInTheDocument()
  })
})
