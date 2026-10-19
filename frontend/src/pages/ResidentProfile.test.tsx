/**
 * The resident dossier's four cards and their editors.
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
  ContactWorkCard,
  HousingSpaceCard,
  KycEmergencyCard,
  StatusNotesCard,
  TenancyLogisticsCard,
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

const patched = () =>
  stub!.calls.find((call) => call.method === 'PATCH' && call.path === '/api/residents/bob')

/** A card as the resident page shows it: with toasts, and a router for its links. */
function showCard(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <ToastProvider>{ui}</ToastProvider>
    </MemoryRouter>,
  )
}

/** Opens a card's editor from its Edit button and returns the dialog. */
async function openEditor(card: HTMLElement, name: string) {
  await userEvent.click(within(card).getByRole('button', { name }))
  return screen.getByRole('dialog')
}

describe('the masks and summaries', () => {
  it('masks an identity number to its last four digits', () => {
    expect(maskedIdNumber('aadhaar', '1234 5678 4821')).toBe('•••• •••• 4821')
    expect(maskedIdNumber('passport', 'K1234567')).toBe('•••• 4567')
    expect(maskedIdNumber('aadhaar', null)).toBeNull()
  })

  it('names a vehicle with its registration', () => {
    expect(vehicleSummary({ vehicle_type: 'four_wheeler', vehicle_reg_no: 'DL 3C AB 1234' })).toBe(
      '4-Wheeler · DL 3C AB 1234',
    )
    expect(vehicleSummary({ vehicle_type: 'two_wheeler', vehicle_reg_no: null })).toBe('2-Wheeler')
    expect(vehicleSummary({ vehicle_type: 'none', vehicle_reg_no: 'stale' })).toBe('No vehicle')
    expect(vehicleSummary({ vehicle_type: null, vehicle_reg_no: null })).toBeNull()
  })
})

describe('Space & Housing', () => {
  const room = {
    room_id: 'room-2',
    apartment_id: 'home',
    room_number: '2',
    has_attached_washroom: true,
    has_balcony: false,
    base_rent_paise: 950000,
    assigned_resident_id: 'bob',
    assigned_display_name: 'Bob Rao',
    assigned_since: '2026-08-01',
    status: 'occupied',
    linked_deposit_slot: null,
  }
  const slot = {
    slot_id: 'slot-1',
    slot_number: 1,
    slot_label: 'Room 2',
    target_deposit_paise: 1500000,
    assigned_resident_id: 'bob',
    status: 'occupied_funded',
    funded_amount_paise: 1500000,
    outstanding_paise: 0,
  }
  const housing = (rooms: unknown[], slots: unknown[]) =>
    installFetchStub({
      routes: {
        '/api/property/rooms': {
          items: rooms,
          total: rooms.length,
          summary: { total_rooms: rooms.length, occupied_rooms: rooms.length, vacant_rooms: 0 },
        },
        '/api/rent/deposits': { is_lease_configured: true, slots },
      } as never,
    })

  it('shows the room, the handover, the deposit held and the stay, with the way to manage them', async () => {
    stub = housing([room], [slot])
    showCard(
      <HousingSpaceCard
        resident={{ ...resident, keys_issued_notes: '2 keys & AC remote' }}
        residentId="bob"
      />,
    )
    const card = screen.getByRole('region', { name: 'Space & Housing' })
    expect(await within(card).findByText(/Slot 1 \(Room 2\)/)).toBeInTheDocument()
    expect(await within(card).findByText('Attached Washroom')).toBeInTheDocument()
    expect(card).toHaveTextContent('Room 2 · ₹9,500.00 tier')
    expect(card).toHaveTextContent('Attached Washroom')
    expect(card).toHaveTextContent('2 keys & AC remote')
    expect(card).toHaveTextContent('₹15,000.00 of ₹15,000.00 held')
    expect(card).toHaveTextContent('Funded')
    expect(card).toHaveTextContent('From 1 Aug 2026')
    expect(card).toHaveTextContent('Living here')
    expect(within(card).getByRole('link', { name: 'Manage room allotment' })).toHaveAttribute(
      'href',
      '/housing?tab=allotment',
    )
    expect(within(card).getByRole('link', { name: /Deposit details/ })).toHaveAttribute(
      'href',
      '/housing?tab=deposits',
    )
  })

  it('says when there is no room or deposit slot', async () => {
    stub = housing([], [])
    showCard(<HousingSpaceCard resident={resident} residentId="bob" />)
    const card = screen.getByRole('region', { name: 'Space & Housing' })
    expect(await within(card).findByText('No room assigned')).toBeInTheDocument()
    expect(await within(card).findByText('No deposit slot')).toBeInTheDocument()
  })
})

describe('Contact & Work', () => {
  const working = { ...resident, profession: 'Data analyst', employer: 'Acme', work_mode: 'hybrid' as const }

  it('offers call, WhatsApp and mail shortcuts, the work, and the UPI ID with its QR on request', async () => {
    showCard(<ContactWorkCard resident={working} onReload={() => {}} />)
    const card = screen.getByRole('region', { name: 'Contact & Work' })
    expect(within(card).getByRole('link', { name: 'Call' })).toHaveAttribute('href', 'tel:+919876543210')
    expect(within(card).getByRole('link', { name: 'WhatsApp' })).toHaveAttribute(
      'href',
      'https://wa.me/919876543210',
    )
    expect(within(card).getByRole('link', { name: 'Mail' })).toHaveAttribute(
      'href',
      'mailto:bob@example.com',
    )
    expect(card).toHaveTextContent('Data analyst at Acme')
    expect(card).toHaveTextContent('Work modeHybrid')
    expect(within(card).getByText('bob.rao@okaxis')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()

    // The QR code is there when it is wanted, not taking up the card all the time.
    expect(within(card).queryByRole('img', { name: /UPI payment QR code/ })).toBeNull()
    const toggle = within(card).getByRole('button', { name: 'Show QR' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    expect(
      within(card).getByRole('img', { name: 'UPI payment QR code for Bob Rao' }),
    ).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Hide QR' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('says what is missing instead of showing dead shortcuts', () => {
    showCard(
      <ContactWorkCard resident={{ ...resident, phone: null, email: null, upi_id: null }} onReload={() => {}} />,
    )
    const card = screen.getByRole('region', { name: 'Contact & Work' })
    // Phone, email, profession, work mode and UPI ID.
    expect(within(card).getAllByText('Not recorded')).toHaveLength(5)
    expect(within(card).queryByRole('link')).not.toBeInTheDocument()
  })

  it('edits contact and work together, and refuses a malformed email or UPI ID', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...working } } })
    const onReload = vi.fn()
    showCard(<ContactWorkCard resident={working} onReload={onReload} />)
    const dialog = await openEditor(
      screen.getByRole('region', { name: 'Contact & Work' }),
      'Edit contact and work',
    )
    const save = within(dialog).getByRole('button', { name: 'Save' })
    await userEvent.clear(within(dialog).getByLabelText('UPI ID'))
    await userEvent.type(within(dialog).getByLabelText('UPI ID'), 'bob')
    expect(within(dialog).getByText('A UPI ID looks like name@bank.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    await userEvent.clear(within(dialog).getByLabelText('UPI ID'))
    await userEvent.clear(within(dialog).getByLabelText('Email'))
    await userEvent.type(within(dialog).getByLabelText('Email'), 'bob@')
    expect(save).toBeDisabled()
    await userEvent.clear(within(dialog).getByLabelText('Email'))
    await userEvent.selectOptions(within(dialog).getByLabelText('Work mode'), 'wfh')
    await userEvent.click(save)

    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toEqual({
      phone: '+91 98765 43210',
      email: null,
      upi_id: null,
      profession: 'Data analyst',
      employer: 'Acme',
      work_mode: 'wfh',
    })
    await waitFor(() => expect(onReload).toHaveBeenCalledOnce())
    expect(await screen.findByText('Contact and work saved')).toBeInTheDocument()
  })
})

describe('Tenancy & Logistics', () => {
  it('reads the terms and the building access in words', () => {
    showCard(
      <TenancyLogisticsCard
        resident={{
          ...resident,
          is_lease_signatory: 1,
          notice_period_days: 30,
          lock_in_until: '2026-12-31',
          parking_slot: 'B-12 (Stilt)',
          vehicle_type: 'four_wheeler',
          vehicle_reg_no: 'DL 3C AB 1234',
          society_rfid_tag: '#RF-8821',
        }}
        onReload={() => {}}
      />,
    )
    const card = screen.getByRole('region', { name: 'Tenancy & Logistics' })
    expect(card).toHaveTextContent('Primary Signatory')
    expect(card).toHaveTextContent('30 days notice required')
    expect(card).toHaveTextContent('Lock-in until 31 Dec 2026')
    expect(card).toHaveTextContent('B-12 (Stilt)')
    expect(card).toHaveTextContent('4-Wheeler · DL 3C AB 1234')
    expect(card).toHaveTextContent('#RF-8821')
  })

  it('says co-tenant and not recorded when nothing is set', () => {
    showCard(<TenancyLogisticsCard resident={resident} onReload={() => {}} />)
    const card = screen.getByRole('region', { name: 'Tenancy & Logistics' })
    expect(card).toHaveTextContent('Co-tenant')
    expect(within(card).getAllByText('Not recorded')).toHaveLength(5)
    // The header's one action is an icon, named by its tooltip (Rule U9).
    const edit = within(card).getByRole('button', { name: 'Edit tenancy and logistics' })
    expect(edit).toHaveAttribute('title', 'Edit tenancy and logistics')
    expect(edit.textContent).toBe('')
  })

  it('saves whole days, upper-cases the registration, and drops it without a vehicle', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...resident } } })
    showCard(<TenancyLogisticsCard resident={resident} onReload={() => {}} />)
    const dialog = await openEditor(
      screen.getByRole('region', { name: 'Tenancy & Logistics' }),
      'Edit tenancy and logistics',
    )
    const save = within(dialog).getByRole('button', { name: 'Save' })
    await userEvent.type(within(dialog).getByLabelText('Notice period (days)'), '30d')
    expect(within(dialog).getByText('Enter a whole number of days, such as 30.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    await userEvent.clear(within(dialog).getByLabelText('Notice period (days)'))
    await userEvent.type(within(dialog).getByLabelText('Notice period (days)'), '30')
    await userEvent.click(within(dialog).getByLabelText(/Signs the master lease/))
    await userEvent.type(within(dialog).getByLabelText('Keys & handover'), '2 keys')
    await userEvent.selectOptions(within(dialog).getByLabelText('Vehicle'), 'two_wheeler')
    await userEvent.type(within(dialog).getByLabelText('Registration number'), 'ka 05 mn 4321')
    await userEvent.type(within(dialog).getByLabelText('Parking slot'), 'B-12')
    await userEvent.click(save)

    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toEqual({
      is_lease_signatory: true,
      notice_period_days: 30,
      lock_in_until: null,
      keys_issued_notes: '2 keys',
      vehicle_type: 'two_wheeler',
      vehicle_reg_no: 'KA 05 MN 4321',
      parking_slot: 'B-12',
      society_rfid_tag: null,
    })
  })

  it('clears the registration of someone with no vehicle', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...resident } } })
    const riding = { ...resident, vehicle_type: 'two_wheeler' as const, vehicle_reg_no: 'KA 05 MN 4321' }
    showCard(<TenancyLogisticsCard resident={riding} onReload={() => {}} />)
    const dialog = await openEditor(
      screen.getByRole('region', { name: 'Tenancy & Logistics' }),
      'Edit tenancy and logistics',
    )
    await userEvent.selectOptions(within(dialog).getByLabelText('Vehicle'), 'none')
    expect(within(dialog).getByLabelText('Registration number')).toBeDisabled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toMatchObject({ vehicle_type: 'none', vehicle_reg_no: null })
  })
})

describe('KYC & Emergency', () => {
  const checked = {
    ...resident,
    id_document_type: 'aadhaar' as const,
    // No digits in common with the sample phone numbers, so a leak would show.
    id_document_number: '7070 3030 4821',
    home_city: 'Kolkata',
    home_state: 'West Bengal',
    police_verification_status: 'verified' as const,
    society_noc_status: 'pending' as const,
    secondary_emergency_name: 'Arjun Rao',
    secondary_emergency_relation: 'Brother',
    secondary_emergency_phone: '+91 99887 66554',
  }

  it('shows the ID masked, the checks, the blood group, both contacts and the vault', () => {
    showCard(<KycEmergencyCard resident={checked} onReload={() => {}} />)
    const card = screen.getByRole('region', { name: 'KYC & Emergency' })
    expect(card).toHaveTextContent('Aadhaar · •••• •••• 4821')
    expect(card).not.toHaveTextContent('7070')
    expect(card).not.toHaveTextContent('3030')
    expect(card).toHaveTextContent('Kolkata, West Bengal')
    expect(card).toHaveTextContent('Police verificationVerified')
    expect(card).toHaveTextContent('Society move-in NOCPending')
    expect(card).toHaveTextContent('Blood groupO+')

    const emergency = within(card).getByRole('region', { name: 'Emergency contact' })
    expect(emergency).toHaveTextContent('Meera Rao · Mother')
    expect(
      within(emergency).getByRole('link', { name: 'Call emergency contact Meera Rao' }),
    ).toHaveAttribute('href', 'tel:9123456780')
    expect(emergency).toHaveTextContent('Arjun Rao · Brother')
    expect(
      within(emergency).getByRole('link', { name: 'Call second contact Arjun Rao' }),
    ).toHaveAttribute('href', 'tel:+919988766554')
    expect(within(card).getByRole('link', { name: /View in Document Vault/ })).toHaveAttribute(
      'href',
      '/housing?tab=documents',
    )
  })

  it('says when no emergency contact is recorded', () => {
    showCard(
      <KycEmergencyCard
        resident={{
          ...resident,
          emergency_contact_name: null,
          emergency_contact_phone: null,
          emergency_contact_relation: null,
        }}
        onReload={() => {}}
      />,
    )
    expect(screen.getByText(/No emergency contact recorded/)).toBeInTheDocument()
  })

  it('keeps the ID number on file unless a new one is typed, and never shows it', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...checked } } })
    showCard(<KycEmergencyCard resident={checked} onReload={() => {}} />)
    const dialog = await openEditor(
      screen.getByRole('region', { name: 'KYC & Emergency' }),
      'Edit KYC and emergency',
    )
    const number = within(dialog).getByLabelText('New ID number')
    expect(number).toHaveValue('')
    expect(number).toHaveAttribute('placeholder', '•••• •••• 4821 on file')
    expect(dialog.innerHTML).not.toContain('7070')
    expect(dialog.innerHTML).not.toContain('3030')

    await userEvent.selectOptions(within(dialog).getByLabelText('Society move-in NOC'), 'approved')
    await userEvent.selectOptions(within(dialog).getByLabelText('Blood group'), 'AB-')
    await userEvent.clear(within(dialog).getByLabelText('Second contact relationship'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patched()).toBeDefined())
    expect(patched()!.body).toEqual({
      id_document_type: 'aadhaar',
      home_city: 'Kolkata',
      home_state: 'West Bengal',
      police_verification_status: 'verified',
      society_noc_status: 'approved',
      blood_group: 'AB-',
      emergency_contact_name: 'Meera Rao',
      emergency_contact_relation: 'Mother',
      emergency_contact_phone: '91234 56780',
      secondary_emergency_name: 'Arjun Rao',
      secondary_emergency_relation: null,
      secondary_emergency_phone: '+91 99887 66554',
    })
  })

  it('checks a new Aadhaar number, and can remove the one on file', async () => {
    stub = installFetchStub({ routes: { 'PATCH /api/residents/bob': { ...checked } } })
    showCard(<KycEmergencyCard resident={checked} onReload={() => {}} />)
    const dialog = await openEditor(
      screen.getByRole('region', { name: 'KYC & Emergency' }),
      'Edit KYC and emergency',
    )
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

describe('Notes', () => {
  it('is shown only when there is a note', () => {
    const { container } = render(<StatusNotesCard resident={resident} />)
    expect(container).toBeEmptyDOMElement()
    render(<StatusNotesCard resident={{ ...resident, notes: 'Night shifts on weekdays.' }} />)
    expect(screen.getByText('Night shifts on weekdays.')).toBeInTheDocument()
  })
})
