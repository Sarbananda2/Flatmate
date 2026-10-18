/**
 * The Tenancy & Master Lease tab. Its form sends only the tenancy terms, in
 * integer paise: the landlord's details are edited on the owner contact and
 * the deposit on the Deposit tab, and the backend keeps whatever a save leaves
 * out.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LeaseContractSection } from './RentConfig'
import { ToastProvider } from '../../components/Toast'
import { installFetchStub, type Recorded } from '../../test/server'

const saved = {
  config_id: 'lease-1',
  apartment_id: 'home',
  expense_definition_id: 'rent',
  expense_definition_name: 'Rent',
  landlord_name: 'Rajesh Kumar Sharma',
  landlord_phone: '+91 98765 43210',
  landlord_email: null,
  landlord_pan: 'ABCPS1234K',
  landlord_bank_account: '50100012348912',
  landlord_bank_ifsc: 'HDFC0000128',
  landlord_upi_id: 'rajeshsharma@okaxis',
  monthly_rent_paise: 4500000,
  due_day_of_month: 5,
  requires_landlord_receipt: false,
  created_at: '2026-10-01T09:00:00+05:30',
  updated_at: '2026-10-01T09:00:00+05:30',
  lease_start_date: '2026-04-01',
  lease_end_date: '2027-02-28',
  duration_months: 11,
  notice_period_days: 30,
  lock_in_months: 6,
  security_deposit_paise: 13500000,
  escalation_type: 'percent',
  escalation_percent: 5,
  renewal_rent_paise: null,
  lease_status: 'active',
  notice_served_date: null,
  target_handover_date: null,
  term_number: 1,
  lifecycle: null,
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const OWNER = {
  contact_id: 'owner-1',
  apartment_id: 'home',
  role: 'owner',
  name: 'Rajesh Kumar Sharma',
  phone: '+91 98765 43210',
  email: null,
  pan: 'ABCPS1234K',
  bank_account_no: '50100012348912',
  bank_ifsc: 'HDFC0000128',
  upi_id: 'rajeshsharma@okaxis',
  agency_name: null,
  fee_amount_paise: null,
  fee_status: null,
  fee_display: null,
  responsibilities: null,
  notes: null,
  created_at: '2026-10-01T09:00:00+05:30',
  updated_at: '2026-10-01T09:00:00+05:30',
}

const APARTMENT = {
  apartment_id: 'home',
  name: 'Flat 1805, CG6, Supertech Capetown',
  currency_code: 'INR',
  timezone: 'Asia/Kolkata',
  created_at: '2026-10-01T09:00:00+05:30',
  flat_number: '1805',
  tower_block: 'CG6',
  society_name: 'Supertech Capetown',
  locality_street: null as string | null,
  city: 'Noida',
  state: 'Uttar Pradesh',
  pincode: '201301',
  premises_address: '1805, CG6, Supertech Capetown, Noida, Uttar Pradesh - 201301',
}

function renderPage(
  config: typeof saved | null,
  routes: Record<string, unknown> = {},
  apartment: typeof APARTMENT | null = null,
  onApartmentChanged = () => {},
) {
  let current = config
  stub = installFetchStub({
    routes: {
      '/api/rent/config': () => ({ config: current }),
      '/api/residents': { items: [], total: 0 },
      '/api/rent/lease/terms': {
        items: [
          {
            term_id: 'term-1',
            term_number: 1,
            start_date: '2025-04-01',
            end_date: '2026-02-28',
            monthly_rent_paise: 2700000,
            escalation_applied_percent: 5,
            archived_at: '2026-03-01T10:00:00+05:30',
          },
        ],
      },
      'POST /api/rent/config': (body: unknown) => {
        current = { ...saved, ...(body as object), updated_at: '2026-10-11T12:00:00+05:30' }
        return current
      },
      '/api/property/contacts': { items: [OWNER], total: 1, roles_on_file: ['owner'] },
      ...routes,
    } as never,
  })
  return render(
    <MemoryRouter>
      <ToastProvider>
        <LeaseContractSection apartment={apartment} onApartmentChanged={onApartmentChanged} />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const posted = () =>
  stub!.calls.find((call) => call.method === 'POST' && call.path === '/api/rent/config')

/** A saved lease opens read-only; editing it starts here. */
const editTerms = async () =>
  userEvent.click(await screen.findByRole('button', { name: 'Edit tenancy terms' }))

/** The editor shows one step at a time; its pills jump between them. */
/** One part of a new lease's landlord name. */
const landlordPart = (part: 'First Name' | 'Middle Name' | 'Last Name') =>
  within(screen.getByRole('group', { name: 'Landlord name' })).getByLabelText(new RegExp(`^${part}`))

const goToStep = (label: string) =>
  userEvent.click(screen.getByRole('tab', { name: new RegExp(`^${label.replace('.', '\\.')}`) }))

/** The tenancy editor saves only from its last step: go there and find its Save. */
const saveOnLastStep = async (name = 'Save Changes') => {
  await goToStep('4. Escalation Clause')
  return screen.getByRole('button', { name })
}

describe('LeaseContractSection', () => {
  it('sets up a new lease with its landlord, in integer paise', async () => {
    renderPage(null)
    await screen.findByLabelText(/Monthly rent/)
    // Save waits for the last step.
    expect(screen.queryByRole('button', { name: 'Set Up Lease' })).toBeNull()
    // Nothing that belongs elsewhere is asked for.
    for (const label of ['PAN', 'IFSC code', 'UPI ID', 'Security Deposit Held by Landlord']) {
      expect(screen.queryByLabelText(label)).toBeNull()
    }
    expect(screen.queryByRole('switch')).toBeNull()

    await userEvent.type(screen.getByLabelText(/Monthly rent/), '45,000.50')
    // The landlord's name in three parts, side by side.
    const group = screen.getByRole('group', { name: 'Landlord name' })
    expect(within(group).getAllByRole('textbox').map((box) => box.closest('.field')?.querySelector('label')?.textContent)).toEqual([
      'First Name* (required)',
      'Middle Name',
      'Last Name',
    ])
    expect(group).toHaveAccessibleDescription(
      'Their phone and PAN are added to the owner under Stakeholders & Contacts, and their bank details under Payment Pools.',
    )
    await userEvent.type(landlordPart('First Name'), ' Rajesh ')
    await userEvent.type(landlordPart('Middle Name'), 'Kumar')
    await userEvent.type(landlordPart('Last Name'), 'Sharma ')
    await userEvent.selectOptions(screen.getByLabelText('Due day of month'), '7')
    await userEvent.click(await saveOnLastStep('Set Up Lease'))

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toEqual({
      landlord_first_name: 'Rajesh',
      landlord_middle_name: 'Kumar',
      landlord_last_name: 'Sharma',
      monthly_rent_paise: 4500050,
      due_day_of_month: 7,
      // A new lease leaves the rent category to the backend.
      expense_definition_id: null,
      // The term's defaults; the end date is left to the backend.
      lease_start_date: null,
      duration_months: 11,
      notice_period_days: 30,
      lock_in_months: 6,
      escalation_type: 'percent',
      escalation_percent: 5,
      renewal_rent_paise: null,
    })
    expect(await screen.findByText('Lease set up')).toBeInTheDocument()
    // Once saved, the lease is shown read-only rather than left in the form.
    expect(await screen.findByRole('button', { name: 'Edit tenancy terms' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/Monthly rent/)).toBeNull()
  })

  it('shows the landlord in full on a card of its own', async () => {
    renderPage(saved)
    const card = await screen.findByLabelText('Landlord profile')
    // Who the landlord is, to reach them and for HRA receipts.
    for (const text of ['Rajesh Kumar Sharma', '+91 98765 43210', 'ABCPS1234K']) {
      expect(card).toHaveTextContent(text)
    }
    // Where to pay them is kept on Payment Pools, not here.
    for (const text of ['50100012348912', 'HDFC0000128', 'rajeshsharma@okaxis']) {
      expect(card).not.toHaveTextContent(text)
    }
    expect(within(card).queryByRole('button', { name: /Copy/ })).toBeNull()
    const panel = card.closest('section')!
    expect(within(panel).getByRole('heading', { name: 'Landlord Profile' })).toBeInTheDocument()
    expect(panel).toHaveTextContent('Bank & payout details are managed under Payment Pools.')
    expect(within(panel).getByRole('link', { name: 'Payment Pools' })).toHaveAttribute('href', '/payments')
    // No landlord inputs on the page itself.
    expect(screen.queryByRole('group', { name: 'Landlord name' })).toBeNull()
    expect(screen.queryByLabelText(/First Name/)).toBeNull()
    expect(screen.queryByRole('link', { name: 'Open monthly rent' })).toBeNull()
  })

  it('names whose account rent and deposits are remitted to, when it is not the landlord', async () => {
    renderPage({ ...saved, payout_beneficiary_name: 'Sunita Sharma' } as typeof saved)
    const card = await screen.findByLabelText('Landlord profile')
    // The landlord stays the owner on the card; the account holder is named under it.
    expect(within(card).getByText('Rajesh Kumar Sharma')).toBeInTheDocument()
    const panel = card.closest('section')!
    expect(panel).toHaveTextContent(
      'Rent & deposits remitted to: Sunita Sharma · Managed under Payment Pools.',
    )
    expect(within(panel).getByText('Sunita Sharma').tagName).toBe('STRONG')
    expect(within(panel).getByRole('link', { name: 'Payment Pools' })).toHaveAttribute('href', '/payments')
    expect(panel).not.toHaveTextContent('Bank & payout details')
  })

  it('edits the landlord in a dialog named like the card', async () => {
    renderPage(saved, { 'PATCH /api/property/contacts/owner-1': { ...OWNER, phone: '9123456780' } })
    await userEvent.click(await screen.findByRole('button', { name: 'Edit landlord' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Landlord Profile' })).toBeInTheDocument()
    // The landlord card holds the owner, so the role is shown rather than chosen.
    expect(within(dialog).getByText('Property Owner')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/Role/)).toBeNull()
    // Save waits for the last step.
    expect(within(dialog).queryByRole('button', { name: 'Save Changes' })).toBeNull()
    // Only the landlord is being edited: the tenancy terms stay read-only.
    expect(screen.getByRole('button', { name: 'Edit tenancy terms' })).toBeInTheDocument()

    const phone = within(dialog).getByLabelText(/Phone/)
    await userEvent.clear(phone)
    await userEvent.type(phone, '9123456780')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(within(dialog).getByLabelText(/PAN/)).toHaveValue('ABCPS1234K')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() =>
      expect(stub!.calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({
        role: 'owner',
        phone: '9123456780',
      }),
    )
    expect(await screen.findByText('Rajesh Kumar Sharma saved')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('cancels a landlord edit without saving', async () => {
    renderPage(saved)
    await userEvent.click(await screen.findByRole('button', { name: 'Edit landlord' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByLabelText('Landlord profile')).toBeInTheDocument()
    expect(stub!.calls.some((call) => call.method === 'PATCH')).toBe(false)
  })

  it('shows the apartment address part by part, without currency or time zone', async () => {
    renderPage(saved, {}, APARTMENT)
    const profile = await screen.findByLabelText('Apartment profile')
    const facts = Object.fromEntries(
      within(profile)
        .getAllByRole('term')
        .map((term) => [term.textContent, term.nextElementSibling?.textContent]),
    )
    expect(facts).toEqual({
      'Flat / Unit': '1805',
      'Tower / Block': 'CG6',
      'Society / Complex': 'Supertech Capetown',
      'Locality / Sector': 'Not recorded',
      'City & State': 'Noida, Uttar Pradesh',
      'PIN Code': '201301',
    })
    expect(screen.getByText('Apartment & Property Details')).toBeInTheDocument()
    expect(profile).not.toHaveTextContent(/Currency|Time Zone|INR|Asia\/Kolkata/)
  })

  it('edits the address in a modal, checking the PIN code as it is typed', async () => {
    let reloaded = false
    renderPage(
      saved,
      { 'PATCH /api/apartments/home': { ...APARTMENT, locality_street: 'Sector 74' } },
      APARTMENT,
      () => {
        reloaded = true
      },
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Edit apartment' }))
    const dialog = screen.getByRole('dialog')
    // The dialog keeps the name of the card it edits.
    expect(within(dialog).getByRole('heading', { name: 'Apartment & Property Details' })).toBeInTheDocument()
    for (const label of [
      'Flat Number',
      'Tower / Wing / Block',
      'Society / Complex Name',
      'Locality / Sector / Street',
      'City',
      'State',
    ]) {
      expect(within(dialog).getByLabelText(label)).toBeInTheDocument()
    }
    expect(within(dialog).getByLabelText('Flat Number')).toHaveValue('1805')
    await userEvent.type(within(dialog).getByLabelText('Locality / Sector / Street'), 'Sector 74')

    const pincode = within(dialog).getByLabelText(/PIN Code/)
    await userEvent.clear(pincode)
    await userEvent.type(pincode, '012345')
    expect(within(dialog).getByText('A PIN code is 6 digits and does not start with 0.')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(stub!.calls.some((call) => call.method === 'PATCH')).toBe(false)

    await userEvent.clear(pincode)
    await userEvent.type(pincode, '201301')
    expect(within(dialog).queryByText('A PIN code is 6 digits and does not start with 0.')).toBeNull()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() =>
      expect(stub!.calls.find((call) => call.method === 'PATCH')?.body).toEqual({
        flat_number: '1805',
        tower_block: 'CG6',
        society_name: 'Supertech Capetown',
        locality_street: 'Sector 74',
        city: 'Noida',
        state: 'Uttar Pradesh',
        pincode: '201301',
      }),
    )
    await waitFor(() => expect(reloaded).toBe(true))
  })

  it('sends a cleared address part as null and Cancel saves nothing', async () => {
    renderPage(saved, { 'PATCH /api/apartments/home': { ...APARTMENT, tower_block: null } }, APARTMENT)
    await userEvent.click(await screen.findByRole('button', { name: 'Edit apartment' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(stub!.calls.some((call) => call.method === 'PATCH')).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: 'Edit apartment' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.clear(within(dialog).getByLabelText('Tower / Wing / Block'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() =>
      expect(stub!.calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({ tower_block: null }),
    )
  })

  it('leaves out the completed terms until there are some', async () => {
    renderPage(saved, { '/api/rent/lease/terms': { items: [] } })
    await screen.findByRole('button', { name: 'Edit tenancy terms' })
    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path === '/api/rent/lease/terms')).toBe(true),
    )
    expect(screen.queryByText('Completed Terms')).toBeNull()
    expect(screen.queryByRole('table', { name: 'Completed lease terms' })).toBeNull()
  })

  it('saves only the tenancy terms, leaving the rest of the lease to the backend', async () => {
    renderPage(saved)
    await editTerms()
    expect(await screen.findByLabelText(/Monthly rent/)).toHaveValue('45000.00')
    expect(screen.getByLabelText('Due day of month')).toHaveValue('5')
    expect(screen.queryByLabelText('Expense definition')).not.toBeInTheDocument()

    await userEvent.click(await saveOnLastStep())
    await waitFor(() => expect(posted()).toBeDefined())
    const body = posted()!.body as Record<string, unknown>
    expect(body).toMatchObject({ monthly_rent_paise: 4500000, expense_definition_id: null })
    for (const field of [
      'landlord_name',
      'landlord_first_name',
      'landlord_middle_name',
      'landlord_last_name',
      'landlord_phone',
      'landlord_pan',
      'landlord_bank_account',
      'landlord_bank_ifsc',
      'landlord_upi_id',
      'requires_landlord_receipt',
      'security_deposit_paise',
    ]) {
      expect(body).not.toHaveProperty(field)
    }
  })

  it('lists the completed terms', async () => {
    renderPage(saved)
    const table = await screen.findByRole('table', { name: 'Completed lease terms' })
    for (const header of within(table).getAllByRole('columnheader')) {
      expect(header).toHaveClass('text-center')
    }
    const row = within(table).getByRole('row', { name: /1/ })
    expect(row).toHaveTextContent('1 Apr 2025')
    expect(row).toHaveTextContent('28 Feb 2026')
    expect(row).toHaveTextContent('₹27,000.00')
    expect(row).toHaveTextContent('5%')
  })

  it('saves the contract term', async () => {
    renderPage(null)
    await userEvent.type(await screen.findByLabelText(/Monthly rent/), '28500')
    await userEvent.type(landlordPart('First Name'), 'Kumar')

    await userEvent.click(screen.getByRole('button', { name: 'Next: Contract Term' }))
    // The date is shown in full; the labelled control is still a date input.
    expect(screen.getByText('Select date')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Lease Start Date'), '2026-03-01')
    expect(screen.getByText('1 March 2026')).toBeInTheDocument()
    expect(screen.getByLabelText('Lease Start Date')).toHaveValue('2026-03-01')
    expect(screen.getByText('Ends 31 Jan 2027.')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Agreement Duration'), '12')
    expect(screen.getByText('Ends 28 Feb 2027.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Next: Exit Protections' }))
    const notice = screen.getByLabelText('Notice Period (Days)')
    await userEvent.clear(notice)
    expect(screen.getByText('Enter a whole number of days, 0 to 365.')).toBeInTheDocument()
    await userEvent.type(notice, '45')
    await userEvent.clear(screen.getByLabelText('Lock-in Period (Months)'))
    await userEvent.type(screen.getByLabelText('Lock-in Period (Months)'), '3')
    await userEvent.click(await saveOnLastStep('Set Up Lease'))

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      lease_start_date: '2026-03-01',
      duration_months: 12,
      notice_period_days: 45,
      lock_in_months: 3,
    })
    expect(posted()!.body).not.toHaveProperty('lease_end_date')
  })

  it('projects the renewal rent for each escalation method', async () => {
    renderPage(null)
    await userEvent.type(await screen.findByLabelText(/Monthly rent/), '28500')
    await userEvent.type(landlordPart('First Name'), 'Kumar')
    await goToStep('4. Escalation Clause')
    // The banner disappears while a rate is half typed, so look it up each time.
    const banner = () => screen.getByRole('status', { name: 'Projected renewal rent' })
    expect(screen.getByRole('radio', { name: 'Percentage Rate' })).toBeChecked()
    expect(banner()).toHaveTextContent('+5.0% escalation')
    expect(banner()).toHaveTextContent('₹29,925.00 / month')
    expect(banner()).toHaveTextContent('Current ₹28,500.00 (+₹1,425.00 total)')

    const rate = screen.getByLabelText('Renewal Escalation Rate (%)')
    await userEvent.clear(rate)
    await userEvent.type(rate, '7.5')
    expect(banner()).toHaveTextContent('+7.5% escalation')
    expect(banner()).toHaveTextContent('₹30,637.50 / month')

    await userEvent.click(screen.getByRole('radio', { name: 'No Escalation' }))
    expect(screen.queryByLabelText('Renewal Escalation Rate (%)')).not.toBeInTheDocument()
    expect(banner()).toHaveTextContent('No escalation')
    expect(banner()).toHaveTextContent('₹28,500.00 / month')

    await userEvent.click(screen.getByRole('radio', { name: 'Fixed Amount' }))
    const save = screen.getByRole('button', { name: 'Set Up Lease' })
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Agreed Renewed Rent/), '30,000')
    expect(banner()).toHaveTextContent('Fixed renewal rent')
    expect(banner()).toHaveTextContent('₹30,000.00 / month')
    expect(banner()).toHaveTextContent('Current ₹28,500.00 (+₹1,500.00 total)')
    await userEvent.click(save)

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      escalation_type: 'fixed',
      renewal_rent_paise: 3000000,
    })
  })

  it('loads the stored term and sends no renewal rent unless it is fixed', async () => {
    renderPage(saved)
    await editTerms()
    await goToStep('2. Contract Term')
    expect(await screen.findByLabelText('Lease Start Date')).toHaveValue('2026-04-01')
    expect(screen.getByLabelText('Agreement Duration')).toHaveValue('11')
    expect(screen.getByText('Ends 28 Feb 2027.')).toBeInTheDocument()

    await userEvent.click(await saveOnLastStep())
    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      lease_start_date: '2026-04-01',
      escalation_type: 'percent',
      escalation_percent: 5,
      renewal_rent_paise: null,
    })
  })

  it('explains each invalid field and keeps the save disabled until it is fixed', async () => {
    renderPage(saved)
    await editTerms()
    // A stored lease opens with nothing flagged.
    expect(screen.queryByText(/is required|Enter a valid|must be greater/)).not.toBeInTheDocument()

    // Each bad value is explained on its own step, and Save waits until it is put right.
    const check = async (step: string, label: string | RegExp, bad: string, message: string, good: string) => {
      await goToStep(step)
      const input = screen.getByLabelText(label)
      await userEvent.clear(input)
      if (bad) await userEvent.type(input, bad)
      expect(screen.getByText(message)).toBeInTheDocument()
      expect(input).toHaveAttribute('aria-invalid', 'true')
      expect(await saveOnLastStep()).toBeDisabled()
      await goToStep(step)
      await userEvent.clear(screen.getByLabelText(label))
      if (good) await userEvent.type(screen.getByLabelText(label), good)
      expect(screen.queryByText(message)).not.toBeInTheDocument()
      expect(await saveOnLastStep()).toBeEnabled()
    }

    await check('1. Monthly Rent', /Monthly rent/, '0', 'Monthly rent must be greater than zero.', '28500')
    await check('3. Exit Protections', 'Notice Period (Days)', '400', 'Enter a whole number of days, 0 to 365.', '30')
    await check('3. Exit Protections', 'Lock-in Period (Months)', '61', 'Enter a whole number of months, 0 to 60.', '6')

    await goToStep('4. Escalation Clause')
    await userEvent.click(screen.getByRole('radio', { name: 'Fixed Amount' }))
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled()
    expect(screen.queryByText('Enter the agreed renewed rent.')).not.toBeInTheDocument()
    await check('4. Escalation Clause', /Agreed Renewed Rent/, '0', 'Enter the agreed renewed rent.', '30000')
    expect(posted()).toBeUndefined()
  })

  it("needs the landlord's first name to set up a new lease", async () => {
    renderPage(null)
    await userEvent.type(await screen.findByLabelText(/Monthly rent/), '28500')
    // A middle and last name are optional; without a first name there is no landlord.
    await userEvent.type(landlordPart('Last Name'), 'Yadav')
    expect(screen.getByText("Enter the landlord's first name.")).toBeInTheDocument()
    expect(await saveOnLastStep('Set Up Lease')).toBeDisabled()
    expect(screen.getByRole('tab', { name: '1. Monthly Rent (needs attention)' })).toBeInTheDocument()
    await goToStep('1. Monthly Rent')
    await userEvent.type(landlordPart('First Name'), 'Kumar')
    expect(await saveOnLastStep('Set Up Lease')).toBeEnabled()
  })

  it('does not flag a new lease before anything is typed', async () => {
    renderPage(null)
    await screen.findByLabelText(/Monthly rent/)
    expect(screen.queryByText("Enter the landlord's first name.")).not.toBeInTheDocument()
    expect(screen.queryByText('Monthly rent must be greater than zero.')).not.toBeInTheDocument()
    expect(await saveOnLastStep('Set Up Lease')).toBeDisabled()
  })

  it('leaves how the rent is divided to the Rent screen', async () => {
    renderPage(saved)
    await editTerms()
    // The lease says what the flat costs; who pays which part is set with the rent.
    expect(screen.queryByRole('radiogroup', { name: 'Rental agreement structure' })).toBeNull()
    expect(screen.queryByText('Rental Agreement Structure')).toBeNull()
    expect(screen.queryByText('Receipt Preferences')).toBeNull()
    expect(within(screen.getByRole('dialog')).getByRole('heading', { name: 'Master Tenancy Terms' })).toBeInTheDocument()
  })

  it('shows the server message when the save is refused', async () => {
    renderPage(saved)
    stub!.restore()
    stub = installFetchStub({
      routes: { '/api/rent/config': { config: saved } },
      errors: {
        'POST /api/rent/config': {
          status: 422,
          code: 'validation_error',
          message: 'The rent category must be an active expense definition of this apartment.',
        },
      },
    })
    await editTerms()
    await userEvent.click(await saveOnLastStep())
    expect(
      await screen.findByText(
        'The rent category must be an active expense definition of this apartment.',
      ),
    ).toBeInTheDocument()
  })
})

describe('the saved tenancy terms', () => {
  it('open read-only, with no inputs on the page', async () => {
    renderPage({
      ...saved,
      lifecycle: {
        as_of_date: '2026-10-14',
        term_number: 1,
        current_month_index: 7,
        duration_months: 11,
        elapsed_percent: 60,
        days_until_expiry: 137,
        notice_deadline_date: '2027-01-29',
        is_notice_window_active: false,
        is_expired: false,
        lock_in_end_date: '2026-10-01',
        is_within_lock_in: false,
        projected_renewal_rent_paise: 4725000,
      },
    } as never)
    const terms = await screen.findByLabelText('Tenancy terms')
    expect(terms).toHaveTextContent('₹45,000.00')
    expect(terms).toHaveTextContent('Due on the 5th of every month')
    expect(terms).toHaveTextContent('1 Apr 2026 to 28 Feb 2027')
    expect(terms).toHaveTextContent('11 Months')
    expect(within(terms).getByText('Month 7 of 11')).toBeInTheDocument()
    expect(terms).toHaveTextContent('Notice Period: 30 Days')
    expect(terms).toHaveTextContent('Lock-in: 6 Months (until 1 Oct 2026)')
    expect(terms).toHaveTextContent('5% increment on renewal')
    expect(terms).toHaveTextContent('Projected renewal: ₹47,250.00')
    for (const label of [/Monthly rent/, 'Lease Start Date', 'Notice Period (Days)']) {
      expect(screen.queryByLabelText(label)).toBeNull()
    }
    expect(screen.queryByRole('button', { name: 'Save Changes' })).toBeNull()
  })

  it('are edited in a dialog, and Cancel closes it without saving', async () => {
    renderPage(saved)
    // On the card itself there are no inputs.
    expect(await screen.findByLabelText('Tenancy terms')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Monthly rent/)).toBeNull()
    await editTerms()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Monthly rent/)).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByLabelText(/Monthly rent/)).toBeNull()
    expect(posted()).toBeUndefined()
  })

  it('return to read-only once the changes are saved', async () => {
    renderPage(saved)
    await editTerms()
    await goToStep('3. Exit Protections')
    const notice = screen.getByLabelText('Notice Period (Days)')
    await userEvent.clear(notice)
    await userEvent.type(notice, '45')
    await userEvent.click(await saveOnLastStep())
    expect(await screen.findByText('Tenancy terms saved')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Edit tenancy terms' })).toBeInTheDocument()
    expect(screen.getByLabelText('Tenancy terms')).toHaveTextContent('Notice Period: 45 Days')
  })
})

describe('the tenancy terms editor', () => {
  it('names its steps after the sections of the read-only card', async () => {
    renderPage(saved)
    const card = await screen.findByLabelText('Tenancy terms')
    const sections = Array.from(card.querySelectorAll('dt')).map((dt) => dt.textContent)
    expect(sections).toEqual(['Monthly Rent', 'Contract Term', 'Exit Protections', 'Escalation Clause'])
    await editTerms()
    const steps = within(screen.getByRole('tablist', { name: 'Edit steps' }))
      .getAllByRole('tab')
      .map((tab) => tab.textContent?.replace(/^\d+\.\s*/, ''))
    expect(steps).toEqual(sections)
  })

  it('opens as one container with three steps, showing only the first', async () => {
    renderPage(saved)
    await editTerms()
    // The dialog has the card's own name.
    expect(within(screen.getByRole('dialog')).getByRole('heading', { name: 'Master Tenancy Terms' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Edit Master Tenancy Terms/ })).toBeNull()
    for (const old of ['Master Contract', 'Contract Term & Exit Protections', 'Renewal & Escalation Clause']) {
      expect(screen.queryByRole('heading', { name: old })).toBeNull()
    }
    const steps = within(screen.getByRole('tablist', { name: 'Edit steps' })).getAllByRole('tab')
    expect(steps.map((tab) => tab.textContent)).toEqual([
      '1. Monthly Rent',
      '2. Contract Term',
      '3. Exit Protections',
      '4. Escalation Clause',
    ])
    expect(steps[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('1. Monthly Rent')
    expect(screen.getByLabelText(/Monthly rent/)).toBeInTheDocument()
    expect(screen.getByLabelText('Due day of month')).toBeInTheDocument()
    expect(screen.queryByLabelText('Lease Start Date')).toBeNull()
    expect(screen.queryByRole('radiogroup', { name: 'Escalation Method' })).toBeNull()
    // Cancel is in the header; Save waits for the last step.
    expect(screen.queryByRole('button', { name: 'Save Changes' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
  })

  it('moves through the steps with Next and Back, and the pills jump between them', async () => {
    renderPage(saved)
    await editTerms()
    await userEvent.click(screen.getByRole('button', { name: 'Next: Contract Term' }))
    expect(screen.getByRole('tab', { name: '2. Contract Term' })).toHaveAttribute('aria-selected', 'true')
    // The term step holds the term; the exit fields are a step of their own.
    expect(screen.getByLabelText('Lease Start Date')).toBeInTheDocument()
    expect(screen.getByLabelText('Agreement Duration')).toBeInTheDocument()
    expect(screen.queryByLabelText('Notice Period (Days)')).toBeNull()
    expect(screen.queryByLabelText(/Monthly rent/)).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Next: Exit Protections' }))
    expect(screen.getByRole('tab', { name: '3. Exit Protections' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Notice Period (Days)')).toBeInTheDocument()
    expect(screen.getByLabelText('Lock-in Period (Months)')).toBeInTheDocument()
    expect(screen.queryByLabelText('Lease Start Date')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Next: Escalation Clause' }))
    expect(screen.getByRole('radiogroup', { name: 'Escalation Method' })).toBeInTheDocument()
    // Next only moves between steps; reaching the last one saves nothing.
    expect(posted()).toBeUndefined()
    expect(within(screen.getByRole('dialog')).getByRole('heading', { name: 'Master Tenancy Terms' })).toBeInTheDocument()
    expect(screen.getByLabelText('Renewal Escalation Rate (%)')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Projected renewal rent' })).toBeInTheDocument()
    // The last step ends in Save rather than Next.
    expect(screen.queryByRole('button', { name: /^Next/ })).toBeNull()
    expect(screen.getAllByRole('button', { name: 'Save Changes' })).toHaveLength(1)

    await userEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByLabelText('Notice Period (Days)')).toBeInTheDocument()
    await goToStep('1. Monthly Rent')
    expect(screen.getByLabelText(/Monthly rent/)).toHaveValue('45000.00')
    await goToStep('4. Escalation Clause')
    expect(screen.getByRole('tab', { name: '4. Escalation Clause' })).toHaveAttribute('aria-selected', 'true')
  })

  it('keeps what was typed on another step, and saves it from the last step', async () => {
    renderPage(saved)
    await editTerms()
    const rent = screen.getByLabelText(/Monthly rent/)
    await userEvent.clear(rent)
    await userEvent.type(rent, '48000')
    await goToStep('3. Exit Protections')
    const notice = screen.getByLabelText('Notice Period (Days)')
    await userEvent.clear(notice)
    await userEvent.type(notice, '60')
    await goToStep('4. Escalation Clause')
    await userEvent.click(screen.getByRole('radio', { name: 'No Escalation' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      monthly_rent_paise: 4800000,
      notice_period_days: 60,
      escalation_type: 'none',
    })
    expect(await screen.findByRole('button', { name: 'Edit tenancy terms' })).toBeInTheDocument()
  })

  it('marks a step whose values stop the save', async () => {
    renderPage(saved)
    await editTerms()
    const rent = screen.getByLabelText(/Monthly rent/)
    await userEvent.clear(rent)
    await userEvent.type(rent, '0')
    // The step being edited shows its own message rather than a marker.
    expect(screen.getByRole('tab', { name: '1. Monthly Rent' })).toBeInTheDocument()
    await goToStep('2. Contract Term')
    expect(screen.getByRole('tab', { name: '1. Monthly Rent (needs attention)' })).toBeInTheDocument()
    expect(await saveOnLastStep()).toBeDisabled()
    await goToStep('1. Monthly Rent')
    await userEvent.clear(screen.getByLabelText(/Monthly rent/))
    await userEvent.type(screen.getByLabelText(/Monthly rent/), '45000')
    await goToStep('2. Contract Term')
    expect(screen.getByRole('tab', { name: '1. Monthly Rent' })).toBeInTheDocument()
    expect(await saveOnLastStep()).toBeEnabled()
  })
})
