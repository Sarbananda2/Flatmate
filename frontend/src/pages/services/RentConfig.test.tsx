/**
 * The Tenancy & Master Lease tab. Its form sends only the tenancy terms, in
 * integer paise: the landlord's details are edited on the owner contact, the
 * deposit on the Deposit tab and the receipt preference on the Rent screen,
 * and the backend keeps whatever a save leaves out.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LeaseContractSection, RentalAgreementStructurePanel } from './RentConfig'
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

function renderPage(config: typeof saved | null) {
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
    } as never,
  })
  return render(
    <MemoryRouter>
      <ToastProvider>
        <LeaseContractSection />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const posted = () =>
  stub!.calls.find((call) => call.method === 'POST' && call.path === '/api/rent/config')

describe('LeaseContractSection', () => {
  it('sets up a new lease with its landlord, in integer paise', async () => {
    renderPage(null)
    const save = await screen.findByRole('button', { name: 'Set Up Lease' })
    expect(save).toBeDisabled()
    // Nothing that belongs elsewhere is asked for.
    for (const label of ['PAN', 'IFSC code', 'UPI ID', 'Security Deposit Held by Landlord']) {
      expect(screen.queryByLabelText(label)).toBeNull()
    }
    expect(screen.queryByRole('switch')).toBeNull()

    await userEvent.type(screen.getByLabelText(/Monthly rent/), '45,000.50')
    await userEvent.type(screen.getByLabelText(/Landlord name/), ' Rajesh Kumar Sharma ')
    await userEvent.selectOptions(screen.getByLabelText('Due day of month'), '7')
    await userEvent.click(save)

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toEqual({
      landlord_name: 'Rajesh Kumar Sharma',
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
    // The form reloads from what was stored.
    expect(await screen.findByRole('button', { name: 'Save Tenancy Terms' })).toBeInTheDocument()
  })

  it('shows the landlord as a card that points to the owner contact', async () => {
    renderPage(saved)
    const card = await screen.findByRole('region', { name: 'Landlord' })
    expect(card).toHaveTextContent('Rajesh Kumar Sharma')
    expect(card).toHaveTextContent('+91 98765 43210')
    expect(card).toHaveTextContent('rajeshsharma@okaxis')
    expect(card).toHaveTextContent('A/c ****8912 · HDFC0000128')
    expect(card).toHaveTextContent('PAN ABCPS1234K')
    expect(
      within(card).getByRole('link', { name: /Edit in Stakeholders & Contacts/ }),
    ).toHaveAttribute('href', '/housing?tab=contacts')
    // No landlord inputs once the lease exists.
    expect(screen.queryByLabelText(/Landlord name/)).toBeNull()
    expect(screen.queryByRole('link', { name: 'Open monthly rent' })).toBeNull()
  })

  it('saves only the tenancy terms, leaving the rest of the lease to the backend', async () => {
    renderPage(saved)
    expect(await screen.findByLabelText(/Monthly rent/)).toHaveValue('45000.00')
    expect(screen.getByLabelText('Due day of month')).toHaveValue('5')
    expect(screen.queryByLabelText('Expense definition')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Save Tenancy Terms' }))
    await waitFor(() => expect(posted()).toBeDefined())
    const body = posted()!.body as Record<string, unknown>
    expect(body).toMatchObject({ monthly_rent_paise: 4500000, expense_definition_id: null })
    for (const field of [
      'landlord_name',
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
    await userEvent.type(screen.getByLabelText(/Landlord name/), 'Kumar Yadav')

    // The date is shown in full; the labelled control is still a date input.
    expect(screen.getByText('Select date')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Lease Start Date'), '2026-03-01')
    expect(screen.getByText('1 March 2026')).toBeInTheDocument()
    expect(screen.getByLabelText('Lease Start Date')).toHaveValue('2026-03-01')
    expect(screen.getByText('Ends 31 Jan 2027.')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Agreement Duration'), '12')
    expect(screen.getByText('Ends 28 Feb 2027.')).toBeInTheDocument()

    const notice = screen.getByLabelText('Notice Period (Days)')
    const save = screen.getByRole('button', { name: 'Set Up Lease' })
    await userEvent.clear(notice)
    expect(save).toBeDisabled()
    await userEvent.type(notice, '45')
    await userEvent.clear(screen.getByLabelText('Lock-in Period (Months)'))
    await userEvent.type(screen.getByLabelText('Lock-in Period (Months)'), '3')
    await userEvent.click(save)

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
    await userEvent.type(screen.getByLabelText(/Landlord name/), 'Kumar Yadav')
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
    expect(await screen.findByLabelText('Lease Start Date')).toHaveValue('2026-04-01')
    expect(screen.getByLabelText('Agreement Duration')).toHaveValue('11')
    expect(screen.getByText('Ends 28 Feb 2027.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Save Tenancy Terms' }))
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
    const save = await screen.findByRole('button', { name: 'Save Tenancy Terms' })
    expect(save).toBeEnabled()
    // A stored lease opens with nothing flagged.
    expect(screen.queryByText(/is required|Enter a valid|must be greater/)).not.toBeInTheDocument()

    const check = async (label: string | RegExp, bad: string, message: string, good: string) => {
      const input = screen.getByLabelText(label)
      await userEvent.clear(input)
      if (bad) await userEvent.type(input, bad)
      expect(screen.getByText(message)).toBeInTheDocument()
      expect(input).toHaveAttribute('aria-invalid', 'true')
      expect(save).toBeDisabled()
      await userEvent.clear(input)
      if (good) await userEvent.type(input, good)
      expect(screen.queryByText(message)).not.toBeInTheDocument()
      expect(save).toBeEnabled()
    }

    await check(/Monthly rent/, '0', 'Monthly rent must be greater than zero.', '28500')
    await check('Notice Period (Days)', '400', 'Enter a whole number of days, 0 to 365.', '30')
    await check('Lock-in Period (Months)', '61', 'Enter a whole number of months, 0 to 60.', '6')

    await userEvent.click(screen.getByRole('radio', { name: 'Fixed Amount' }))
    expect(save).toBeDisabled()
    expect(screen.queryByText('Enter the agreed renewed rent.')).not.toBeInTheDocument()
    await check(/Agreed Renewed Rent/, '0', 'Enter the agreed renewed rent.', '30000')
    expect(posted()).toBeUndefined()
  })

  it('needs the landlord name to set up a new lease', async () => {
    renderPage(null)
    const save = await screen.findByRole('button', { name: 'Set Up Lease' })
    await userEvent.type(screen.getByLabelText(/Monthly rent/), '28500')
    const name = screen.getByLabelText(/Landlord name/)
    await userEvent.type(name, 'x')
    await userEvent.clear(name)
    expect(screen.getByText('Landlord name is required.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    await userEvent.type(name, 'Kumar Yadav')
    expect(save).toBeEnabled()
  })

  it('does not flag a new lease before anything is typed', async () => {
    renderPage(null)
    expect(await screen.findByRole('button', { name: 'Set Up Lease' })).toBeDisabled()
    expect(screen.queryByText('Landlord name is required.')).not.toBeInTheDocument()
    expect(screen.queryByText('Monthly rent must be greater than zero.')).not.toBeInTheDocument()
  })

  it('leaves how the rent is divided and receipted to the Rent screen', async () => {
    renderPage(saved)
    await screen.findByRole('button', { name: /Save/ })
    // The lease says what the flat costs; who pays which part is set with the rent.
    expect(screen.queryByRole('radiogroup', { name: 'Rental agreement structure' })).toBeNull()
    expect(screen.queryByText('Rental Agreement Structure')).toBeNull()
    expect(screen.queryByText('Receipt Preferences')).toBeNull()
    expect(screen.getByText('Master Contract')).toBeInTheDocument()
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
    await userEvent.click(await screen.findByRole('button', { name: 'Save Tenancy Terms' }))
    expect(
      await screen.findByText(
        'The rent category must be an active expense definition of this apartment.',
      ),
    ).toBeInTheDocument()
  })
})

describe('RentalAgreementStructurePanel', () => {
  it('shows the equal split as the default and room-tiered as chosen per month', () => {
    render(<RentalAgreementStructurePanel />)
    const group = screen.getByRole('radiogroup', { name: 'Rental agreement structure' })
    const models = within(group).getAllByRole('radio')
    expect(models).toHaveLength(5)
    expect(
      within(group).getByRole('radio', { name: 'Single Master Lease (Equal Split)' }),
    ).toBeChecked()
    expect(within(group).getByText('Default Model')).toBeInTheDocument()
    expect(within(group).getByText('Needs Room Base Rents')).toBeInTheDocument()
    expect(within(group).getAllByText('Under Development')).toHaveLength(3)
    for (const model of models.slice(1)) {
      expect(model).not.toBeChecked()
      expect(model).toHaveAttribute('aria-disabled', 'true')
    }
  })

  it('says room-tiered is available when the room rents add up', () => {
    render(<RentalAgreementStructurePanel roomTieredAvailable />)
    expect(screen.getByText('Available This Month')).toBeInTheDocument()
  })
})
