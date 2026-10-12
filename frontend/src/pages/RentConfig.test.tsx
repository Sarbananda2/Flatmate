/**
 * The lease form sends money and identifiers to the backend, so these tests
 * pin the body it posts: integer paise, normalised PAN and IFSC, and the
 * receipts preference exactly as the operator set it.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RentConfigPage from './RentConfig'
import { ToastProvider } from '../components/Toast'
import { installFetchStub, type Recorded } from '../test/server'

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
      'POST /api/rent/config': (body: unknown) => {
        current = { ...saved, ...(body as object), updated_at: '2026-10-11T12:00:00+05:30' }
        return current
      },
    } as never,
  })
  return render(
    <MemoryRouter>
      <ToastProvider>
        <RentConfigPage />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const posted = () =>
  stub!.calls.find((call) => call.method === 'POST' && call.path === '/api/rent/config')

describe('RentConfigPage', () => {
  it('sets up a new lease with integer paise and normalised identifiers', async () => {
    renderPage(null)
    expect(await screen.findByRole('heading', { name: 'Lease Configuration' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Housing & Utilities' })).toHaveAttribute(
      'href',
      '/services',
    )
    const save = screen.getByRole('button', { name: 'Set Up Lease' })
    expect(save).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/Monthly rent/), '45,000.50')
    await userEvent.type(screen.getByLabelText(/Landlord name/), 'Rajesh Kumar Sharma')
    await userEvent.type(screen.getByLabelText('PAN'), 'abcps1234k')
    await userEvent.type(screen.getByLabelText('IFSC code'), 'hdfc0000128')
    await userEvent.selectOptions(screen.getByLabelText('Due day of month'), '7')
    await userEvent.click(
      screen.getByRole('switch', { name: 'Do you receive receipts from the landlord?' }),
    )
    await userEvent.click(save)

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toEqual({
      landlord_name: 'Rajesh Kumar Sharma',
      landlord_phone: null,
      landlord_email: null,
      landlord_pan: 'ABCPS1234K',
      landlord_bank_account: null,
      landlord_bank_ifsc: 'HDFC0000128',
      landlord_upi_id: null,
      monthly_rent_paise: 4500050,
      due_day_of_month: 7,
      requires_landlord_receipt: true,
      // A new lease leaves the rent category to the backend.
      expense_definition_id: null,
      // The term's defaults; the end date is left to the backend.
      lease_start_date: null,
      duration_months: 11,
      notice_period_days: 30,
      lock_in_months: 6,
      security_deposit_paise: null,
      escalation_type: 'percent',
      escalation_percent: 5,
      renewal_rent_paise: null,
    })
    expect(await screen.findByText('Lease set up')).toBeInTheDocument()
    // The form reloads from what was stored.
    expect(await screen.findByRole('button', { name: 'Save Configuration' })).toBeInTheDocument()
  })

  it('loads the stored lease and leaves its rent category to the backend', async () => {
    renderPage(saved)
    expect(await screen.findByLabelText(/Monthly rent/)).toHaveValue('45000.00')
    expect(screen.getByLabelText(/Landlord name/)).toHaveValue('Rajesh Kumar Sharma')
    expect(screen.getByLabelText('PAN')).toHaveValue('ABCPS1234K')
    expect(screen.getByLabelText('Due day of month')).toHaveValue('5')
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false')

    expect(screen.queryByLabelText('Expense definition')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Save Configuration' }))
    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({ expense_definition_id: null })
  })

  it('saves the contract term with the deposit in integer paise', async () => {
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
    await userEvent.type(screen.getByLabelText('Security Deposit Held by Landlord'), '1,35,000.50')
    await userEvent.click(save)

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      lease_start_date: '2026-03-01',
      duration_months: 12,
      notice_period_days: 45,
      lock_in_months: 3,
      security_deposit_paise: 13500050,
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
    expect(screen.getByLabelText('Security Deposit Held by Landlord')).toHaveValue('135000.00')
    expect(screen.getByText('Ends 28 Feb 2027.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Save Configuration' }))
    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      lease_start_date: '2026-04-01',
      security_deposit_paise: 13500000,
      escalation_type: 'percent',
      escalation_percent: 5,
      renewal_rent_paise: null,
    })
  })

  it('explains each invalid field and keeps the save disabled until it is fixed', async () => {
    renderPage(saved)
    const save = await screen.findByRole('button', { name: 'Save Configuration' })
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

    await check(/Landlord name/, '', 'Landlord name is required.', 'Kumar Yadav')
    await check(/Monthly rent/, '0', 'Monthly rent must be greater than zero.', '28500')
    await check('Phone', '12345', 'Enter a valid 10 to 15 digit phone number.', '+91 98765 43210')
    await check('Email', 'kumar@', 'Enter a valid email address.', 'kumar@example.com')
    await check('UPI ID', 'kumar', 'Enter a valid UPI ID (e.g. name@bank).', 'kumar.yadav@okhdfcbank')
    // Phone, email and UPI ID are optional: empty is fine.
    await check('Email', 'x', 'Enter a valid email address.', '')
    // A half-typed amount gets the money input's own, more specific message.
    await check(
      'Security Deposit Held by Landlord',
      '1.234',
      'At most two decimal places (paise).',
      '',
    )

    await userEvent.click(screen.getByRole('radio', { name: 'Fixed Amount' }))
    expect(save).toBeDisabled()
    expect(screen.queryByText('Enter the agreed renewed rent.')).not.toBeInTheDocument()
    await check(/Agreed Renewed Rent/, '0', 'Enter the agreed renewed rent.', '30000')
    expect(posted()).toBeUndefined()
  })

  it('does not flag a new lease before anything is typed', async () => {
    renderPage(null)
    expect(await screen.findByRole('button', { name: 'Set Up Lease' })).toBeDisabled()
    expect(screen.queryByText('Landlord name is required.')).not.toBeInTheDocument()
    expect(screen.queryByText('Monthly rent must be greater than zero.')).not.toBeInTheDocument()
  })

  it('shows the equal split as the one live rental model', async () => {
    renderPage(saved)
    const group = await screen.findByRole('radiogroup', { name: 'Rental agreement structure' })
    const models = within(group).getAllByRole('radio')
    expect(models).toHaveLength(5)
    expect(
      within(group).getByRole('radio', { name: 'Single Master Lease (Equal Split)' }),
    ).toBeChecked()
    expect(within(group).getByText('Active Model')).toBeInTheDocument()
    expect(within(group).getAllByText('Under Development')).toHaveLength(4)
    for (const model of models.slice(1)) {
      expect(model).not.toBeChecked()
      expect(model).toHaveAttribute('aria-disabled', 'true')
    }
  })

  it('refuses a malformed PAN or IFSC before saving', async () => {
    renderPage(saved)
    const pan = await screen.findByLabelText('PAN')
    const save = screen.getByRole('button', { name: 'Save Configuration' })
    expect(save).toBeEnabled()

    await userEvent.clear(pan)
    await userEvent.type(pan, 'abc123')
    expect(pan).toHaveValue('ABC123')
    expect(screen.getByText(/A PAN is ten characters/)).toBeInTheDocument()
    expect(save).toBeDisabled()

    await userEvent.clear(pan)
    expect(save).toBeEnabled()
    const ifsc = screen.getByLabelText('IFSC code')
    await userEvent.clear(ifsc)
    expect(save).toBeEnabled()
    await userEvent.type(ifsc, 'hdfc1')
    expect(ifsc).toHaveValue('HDFC1')
    expect(screen.getByText(/An IFSC is eleven characters/)).toBeInTheDocument()
    expect(save).toBeDisabled()
    expect(posted()).toBeUndefined()
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
    await userEvent.click(await screen.findByRole('button', { name: 'Save Configuration' }))
    expect(
      await screen.findByText(
        'The rent category must be an active expense definition of this apartment.',
      ),
    ).toBeInTheDocument()
  })
})
