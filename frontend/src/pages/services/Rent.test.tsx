/**
 * The rent screen sends money to the backend: every body it posts carries
 * integer paise and the exact fields the operator chose. The bank reference
 * recorded with each payout is the proof of payment, so the screen never asks
 * for the landlord's receipt (DEBT-008), whatever an older lease says.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RentMonth } from './Rent'
import type { Apartment } from '../../api/types'
import { ToastProvider } from '../../components/Toast'
import { RESIDENTS, installFetchStub, type Recorded } from '../../test/server'

const MONTH = '2026-10'

const config = {
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
  rent_split_mode: 'equal',
  created_at: '2026-10-01T09:00:00+05:30',
  updated_at: '2026-10-01T09:00:00+05:30',
}

function participant(id: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    resident_id: id,
    display_name: name,
    share_paise: 1500000,
    settled_paise: 0,
    outstanding_paise: 1500000,
    last_payment_date: null,
    collection_status: 'unpaid',
    receipt_available: false,
    advanced_paise: 0,
    payments: [],
    ...overrides,
  }
}

function cycle(overrides: Record<string, unknown> = {}) {
  return {
    cycle_id: 'cycle-1',
    apartment_id: 'home',
    settlement_month: MONTH,
    service_period: MONTH,
    expense_id: 'rent-oct',
    description: 'Rent for October 2026',
    due_date: '2026-10-05',
    charge_due_date: '2026-10-31',
    total_rent_paise: 4500000,
    participant_count: 3,
    base_share_paise: 1500000,
    remainder_paise: 0,
    allocated_paise: 4500000,
    collected_paise: 3000000,
    collected_count: 2,
    participants: [
      participant('alice', 'Alice', {
        settled_paise: 1500000,
        outstanding_paise: 0,
        last_payment_date: '2026-10-02',
        collection_status: 'paid',
      }),
      participant('bob', 'Bob', {
        settled_paise: 1500000,
        outstanding_paise: 0,
        last_payment_date: '2026-10-03',
        collection_status: 'paid',
      }),
      participant('chris', 'Chris'),
    ],
    excluded_residents: [],
    disbursement_status: 'pending',
    disbursed_at: null,
    disbursement_reference: null,
    disbursed_by: null,
    funding_resident_id: null,
    funding_resident_name: null,
    funding_payment_id: null,
    disbursed_paise: 0,
    remaining_disbursement_paise: 4500000,
    disbursements: [],
    lease_rent_paise: 4500000,
    matches_lease_rent: true,
    landlord_receipt: null,
    landlord_receipt_status: 'not_required',
    statement: null,
    created_at: '2026-10-01T09:00:00+05:30',
    ...overrides,
  }
}

const preview = {
  settlement_month: MONTH,
  mode: 'create',
  existing_expense_id: null,
  existing_expense_description: null,
  due_date: '2026-10-05',
  total_rent_paise: 4500000,
  participant_count: 3,
  base_share_paise: 1500000,
  remainder_paise: 0,
  participants: [
    { resident_id: 'alice', display_name: 'Alice', share_paise: 1500000 },
    { resident_id: 'bob', display_name: 'Bob', share_paise: 1500000 },
    { resident_id: 'chris', display_name: 'Chris', share_paise: 1500000 },
  ],
  excluded_residents: [],
  can_generate: true,
  blocked_reason: null,
  matches_lease_rent: true,
  statement: null,
  split_mode: 'equal',
  room_tiered: {
    available: false,
    blocked_reason: 'All participating residents must occupy a room with a configured base rent.',
    room_total_paise: 0,
    lease_rent_paise: 4500000,
    shares: [],
  },
}

/** A preview where the rooms' base rents add up to the lease rent. */
const tieredPreview = {
  ...preview,
  room_tiered: {
    available: true,
    blocked_reason: null,
    room_total_paise: 4500000,
    lease_rent_paise: 4500000,
    shares: [
      { resident_id: 'alice', display_name: 'Alice', room_id: 'r1', room_number: 'Room 1', base_rent_paise: 2000000 },
      { resident_id: 'bob', display_name: 'Bob', room_id: 'r2', room_number: 'Room 2', base_rent_paise: 1500000 },
      { resident_id: 'chris', display_name: 'Chris', room_id: 'r3', room_number: 'Room 3', base_rent_paise: 1000000 },
    ],
  },
}

function overview(overrides: Record<string, unknown> = {}) {
  return {
    settlement_month: MONTH,
    month_label: 'October 2026',
    config,
    cycle: cycle(),
    preview: null,
    ...overrides,
  }
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function renderRent(
  payload: Record<string, unknown>,
  routes: Record<string, unknown> = {},
  apartment: Apartment | null = null,
) {
  stub = installFetchStub({
    routes: {
      [`/api/rent/cycle/${MONTH}`]: payload,
      '/api/residents': RESIDENTS,
      [`POST /api/rent/cycle/${MONTH}/disburse`]: cycle({ disbursement_status: 'disbursed' }),
      [`POST /api/rent/cycle/${MONTH}/generate`]: cycle(),
      [`POST /api/rent/cycle/${MONTH}/pay`]: cycle(),
      'POST /api/rent/config': config,
      ...routes,
    } as never,
  })
  return render(
    <MemoryRouter>
      <ToastProvider>
        <RentMonth month={MONTH} apartment={apartment} />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const posted = (suffix: string) =>
  stub!.calls.find((call) => call.method === 'POST' && call.path.endsWith(suffix))

/** A summary card at the top of the page, by its label. */
const stat = (label: string) => screen.getByText(label).parentElement!

/** The landlord's row in the landlord panel's summary table. */
const landlordRow = () =>
  within(screen.getByRole('table', { name: 'Rent owed to the landlord' })).getAllByRole('row', {
    name: /Rajesh Kumar Sharma/,
  })[0]

describe('RentMonth', () => {
  it('shows the rent, the equal share, and what has been collected', async () => {
    renderRent(overview())
    // The period bar names the month, so the title does not repeat it; and the
    // month's state is read from the tables, with no pills above them.
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(/^Rent$/)
    expect(
      screen.queryByText(/Payout Pending|Disbursed to Landlord|Partially Disbursed|^Term:/),
    ).not.toBeInTheDocument()
    expect(stat('Contract Rent')).toHaveTextContent('₹45,000.00')
    expect(stat('Equal Share (1/3)')).toHaveTextContent('₹15,000.00')
    expect(stat('Internal Collection')).toHaveTextContent('₹30,000.00')
    expect(stat('Internal Collection')).toHaveTextContent('2 of 3 covered · 66.7%')
    expect(stat('Landlord Due Date')).toHaveTextContent('5 Oct 2026')
    expect(stat('Landlord Due Date')).toHaveTextContent('Rajesh Kumar Sharma')

    const table = screen.getByRole('table', { name: 'Rent shares and collection' })
    const chris = within(table).getByRole('row', { name: /Chris/ })
    expect(chris).toHaveTextContent('₹15,000.00')
    expect(chris).toHaveTextContent('Unpaid')

    // Collecting from residents and paying the landlord are read apart: the
    // collection above, and the landlord's own row, with nothing paid yet.
    expect(landlordRow()).toHaveTextContent('₹45,000.00₹0.00₹45,000.00Unpaid')
    expect(screen.queryByRole('table', { name: 'Payments to the landlord' })).not.toBeInTheDocument()
  })

  it('offers no HRA slip to residents who have paid', async () => {
    renderRent(overview())
    const table = await screen.findByRole('table', { name: 'Rent shares and collection' })
    // Every row has the payment button: off once the share is paid, on while some is owed.
    const alice = within(table).getByRole('row', { name: /Alice/ })
    expect(within(alice).getByRole('button', { name: 'Pay now for Alice' })).toBeDisabled()
    const chris = within(table).getByRole('row', { name: /Chris/ })
    expect(within(chris).getByRole('button', { name: 'Pay now for Chris' })).toBeEnabled()
    // The status says paid, with no date beside it.
    expect(within(alice).getByText('Paid').textContent).toBe('Paid')
    expect(table).not.toHaveTextContent('Settled')
    expect(screen.queryByText(/HRA slip/)).not.toBeInTheDocument()
  })

  describe("a resident's rent statement", () => {
    const payment = (overrides: Record<string, unknown> = {}) => ({
      payment_id: 'pay-1',
      payment_date: '2026-10-08',
      amount_paise: 500000,
      payment_mode: 'upi',
      reference: 'UPI 428391765102',
      note: 'First part',
      created_at: '2026-10-08T10:00:00+05:30',
      ...overrides,
    })
    // Chris paid ₹5,000 by UPI after the due date, then ₹4,000 in cash.
    const partPaid = () =>
      cycle({
        collected_paise: 3900000,
        participants: cycle().participants.map((row) =>
          row.resident_id === 'chris'
            ? {
                ...row,
                settled_paise: 900000,
                outstanding_paise: 600000,
                last_payment_date: '2026-10-12',
                collection_status: 'partial',
                payments: [
                  payment(),
                  payment({
                    payment_id: 'pay-2',
                    payment_date: '2026-10-12',
                    amount_paise: 400000,
                    payment_mode: 'cash',
                    reference: null,
                    note: null,
                  }),
                ],
              }
            : row,
        ),
      })
    const chrisRow = async () =>
      within(await screen.findByRole('table', { name: 'Rent shares and collection' })).getByRole(
        'row',
        { name: /Chris/ },
      )

    it('opens from the resident\'s row with what they owe and each payment', async () => {
      renderRent(overview({ cycle: partPaid() }))
      await userEvent.click(await chrisRow())
      const dialog = await screen.findByRole('dialog', { name: 'Rent Statement' })
      expect(dialog).toHaveTextContent('Chris · October 2026')
      expect(within(dialog).getByText('Share').parentElement).toHaveTextContent('₹15,000.00')
      expect(within(dialog).getByText('Paid to Pool').parentElement).toHaveTextContent('₹9,000.00')
      expect(within(dialog).getByText('Still Owed').parentElement).toHaveTextContent('₹6,000.00')

      const history = within(dialog).getByRole('region', { name: 'Payment history' })
      expect(history).toHaveTextContent('Part paid')
      const table = within(history).getByRole('table', { name: "Payments towards Chris's rent" })
      expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
        'Date',
        'Amount',
        'Mode',
        'Reference / UTR',
      ])
      const rows = within(table).getAllByRole('row').slice(1)
      expect(rows.map((row) => row.textContent)).toEqual([
        '8 Oct 2026₹5,000.00UPIUPI 428391765102',
        '12 Oct 2026₹4,000.00Cash—',
      ])
      // Closed by its own ✕: there is no footer of actions.
      expect(within(dialog).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
        'Close dialog',
      ])
      await userEvent.click(within(dialog).getByRole('button', { name: 'Close dialog' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('says when nothing has been paid yet', async () => {
      renderRent(overview())
      await userEvent.click(await chrisRow())
      const dialog = await screen.findByRole('dialog', { name: 'Rent Statement' })
      expect(dialog).toHaveTextContent('No payments recorded yet for this month.')
      expect(within(dialog).queryByRole('table')).not.toBeInTheDocument()
      expect(dialog).toHaveTextContent('Unpaid')
    })

    it('opens from the keyboard too', async () => {
      renderRent(overview({ cycle: partPaid() }))
      const row = await chrisRow()
      row.focus()
      await userEvent.keyboard('{Enter}')
      expect(await screen.findByRole('dialog', { name: 'Rent Statement' })).toBeInTheDocument()
    })

    it('is not opened by the name link or the payment button in the row', async () => {
      renderRent(overview())
      const row = await chrisRow()
      await userEvent.click(within(row).getByRole('link', { name: 'Chris' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      await userEvent.click(within(row).getByRole('button', { name: 'Pay now for Chris' }))
      expect(await screen.findByRole('dialog', { name: 'Record Rent Payment' })).toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: 'Rent Statement' })).not.toBeInTheDocument()
    })

    it('explains a share covered by paying the landlord directly', async () => {
      renderRent(
        overview({
          cycle: cycle({
            participants: cycle().participants.map((row) =>
              row.resident_id === 'alice'
                ? {
                    ...row,
                    settled_paise: 0,
                    outstanding_paise: 1500000,
                    collection_status: 'funded_payout',
                    advanced_paise: 4500000,
                  }
                : row,
            ),
          }),
        }),
      )
      const table = await screen.findByRole('table', { name: 'Rent shares and collection' })
      await userEvent.click(within(table).getByRole('row', { name: /Alice/ }))
      const dialog = await screen.findByRole('dialog', { name: 'Rent Statement' })
      expect(dialog).toHaveTextContent(
        'Alice paid ₹45,000.00 to the landlord directly this month, which covers their share.',
      )
      expect(within(dialog).getByText('Still Owed').parentElement).toHaveTextContent(
        'Covered by paying the landlord',
      )
      expect(dialog).toHaveTextContent('No payments recorded yet for this month.')
    })
  })

  it('records a rent payment pre-filled with the outstanding share', async () => {
    const afterPayment = cycle({
      collected_paise: 4500000,
      collected_count: 3,
      participants: cycle().participants.map((row) =>
        row.resident_id === 'chris'
          ? { ...row, settled_paise: 1500000, outstanding_paise: 0, collection_status: 'paid' }
          : row,
      ),
      payment: {
        payment_id: 'p1',
        resident_id: 'chris',
        amount_paise: 1500000,
        applied_to_rent_paise: 1500000,
        applied_to_other_charges_paise: 0,
        unapplied_credit_paise: 0,
      },
    })
    let paid = false
    renderRent(overview(), {
      // The page reloads after the payment and must show the new figures.
      [`/api/rent/cycle/${MONTH}`]: () => (paid ? overview({ cycle: afterPayment }) : overview()),
      [`POST /api/rent/cycle/${MONTH}/pay`]: () => {
        paid = true
        return afterPayment
      },
    })

    const table = await screen.findByRole('table', { name: 'Rent shares and collection' })
    // Every resident has the action; only one who still owes rent can use it.
    const actions = within(table).getAllByRole('button', { name: /Pay now for/ })
    expect(actions).toHaveLength(3)
    expect(actions.filter((button) => !(button as HTMLButtonElement).disabled)).toEqual([
      within(table).getByRole('button', { name: 'Pay now for Chris' }),
    ])
    await userEvent.click(
      within(table).getByRole('button', { name: 'Pay now for Chris' }),
    )

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Chris · October 2026 rent · ₹15,000.00 outstanding')
    expect(within(dialog).getByLabelText(/Amount paid/)).toHaveValue('15000.00')
    expect(within(dialog).getByLabelText(/Payment date/)).not.toHaveValue('')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UPI-778')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payment' }))

    await waitFor(() => expect(posted('/pay')).toBeDefined())
    expect(posted('/pay')!.body).toMatchObject({
      resident_id: 'chris',
      amount_paise: 1500000,
      payment_mode: 'upi',
      reference: 'UPI-778',
      note: null,
    })
    expect(await screen.findByText('3 of 3 covered · 100.0%')).toBeInTheDocument()
    // Everyone has paid, so every row's payment button is off.
    for (const button of within(
      screen.getByRole('table', { name: 'Rent shares and collection' }),
    ).getAllByRole('button', { name: /Pay now for/ })) {
      expect(button).toBeDisabled()
    }
  })

  it('shows where to pay the household pool, or where to add its details', async () => {
    const apartment: Apartment = {
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
    const { unmount } = renderRent(overview(), {}, apartment)
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now for Chris' }))
    let dialog = screen.getByRole('dialog', { name: 'Record Rent Payment' })
    let destination = within(dialog).getByRole('region', { name: 'Payment destination' })
    expect(destination).toHaveTextContent('Transfer toFlat 402 Kitty₹15,000.00')
    for (const detail of ['Account123456789012', 'IFSCSBIN0005678', 'UPI IDflat402@okicici']) {
      expect(destination).toHaveTextContent(detail)
    }
    expect(within(destination).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()
    unmount()
    stub?.restore()

    // Nothing on file for the pool yet.
    renderRent(overview(), {}, { ...apartment, pool_name: 'House Kitty', pool_bank_account: null, pool_bank_ifsc: null, pool_upi_id: null })
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now for Chris' }))
    dialog = screen.getByRole('dialog', { name: 'Record Rent Payment' })
    destination = within(dialog).getByRole('region', { name: 'Payment destination' })
    expect(destination).toHaveTextContent('Transfer toHouse Kitty')
    expect(within(destination).getByRole('link', { name: 'Add them on Payment Pools' })).toHaveAttribute(
      'href',
      '/payments',
    )
  })

  it('says where the money goes when the amount is not exactly the share', async () => {
    renderRent(overview())
    await userEvent.click(
      await screen.findByRole('button', { name: 'Pay now for Chris' }),
    )
    const dialog = await screen.findByRole('dialog')
    const amount = within(dialog).getByLabelText(/Amount paid/)
    const record = within(dialog).getByRole('button', { name: 'Record Payment' })

    await userEvent.clear(amount)
    expect(record).toBeDisabled()
    await userEvent.type(amount, '6000')
    expect(dialog).toHaveTextContent('Part payment: ₹9,000.00 of this month\'s share will still be owed.')
    await userEvent.clear(amount)
    await userEvent.type(amount, '16500.50')
    expect(dialog).toHaveTextContent('₹1,500.50 more than this month\'s share')
    // The amount is fine, but a payment is not recorded without its transaction ID.
    const transaction = within(dialog).getByLabelText(/Reference \/ UTR/)
    expect(transaction).toBeRequired()
    expect(record).toBeDisabled()
    await userEvent.type(transaction, '   ')
    expect(record).toBeDisabled()
    await userEvent.clear(transaction)
    await userEvent.type(transaction, 'UPI-9876543210')
    expect(record).toBeEnabled()
    await userEvent.click(record)
    await waitFor(() => expect(posted('/pay')).toBeDefined())
    expect(posted('/pay')!.body).toMatchObject({
      amount_paise: 1650050,
      reference: 'UPI-9876543210',
    })
  })

  it('asks for the same Reference / UTR whatever the payment mode', async () => {
    renderRent(overview())
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now for Chris' }))
    const dialog = await screen.findByRole('dialog')
    for (const mode of ['upi', 'bank_transfer', 'cash', 'other']) {
      await userEvent.selectOptions(within(dialog).getByLabelText(/Payment mode/), mode)
      const reference = within(dialog).getByLabelText(/Reference \/ UTR/)
      expect(reference).toBeRequired()
      expect(reference).toHaveAttribute('placeholder', 'e.g. UPI ref or bank UTR')
    }
  })

  it("sends the payout to the account's holder, and says where to add a missing account", async () => {
    renderRent(
      overview({
        config: {
          ...config,
          payout_beneficiary_name: 'Sunita Sharma',
          landlord_bank_account: null,
          landlord_bank_ifsc: null,
          landlord_upi_id: null,
        },
      }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now to landlord' }))
    const dialog = await screen.findByRole('dialog')
    const destination = within(dialog).getByRole('region', { name: 'Payout destination' })
    expect(destination).toHaveTextContent('Sunita Sharma')
    expect(destination).not.toHaveTextContent('Rajesh Kumar Sharma')
    expect(destination).toHaveTextContent(
      'No bank account or UPI ID is recorded for the landlord. Add them on Payment Pools.',
    )
    expect(within(destination).getByRole('link', { name: 'Add them on Payment Pools' })).toHaveAttribute(
      'href',
      '/payments',
    )
  })

  it('asks only for the amount, date, funder, and reference', async () => {
    renderRent(overview())
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now to landlord' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText(/Landlord's receipt/)).not.toBeInTheDocument()

    // Where to send it, in full, and what recording it commits to.
    const destination = within(dialog).getByRole('region', { name: 'Payout destination' })
    expect(destination).toHaveTextContent('Rajesh Kumar Sharma')
    expect(destination).toHaveTextContent('₹30,000.00')
    expect(destination).toHaveTextContent('50100012348912')
    expect(destination).toHaveTextContent('HDFC0000128')
    expect(destination).toHaveTextContent('rajeshsharma@okaxis')
    expect(within(destination).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()

    // Laid out as a resident's payment: where it goes first, then what was paid,
    // with the balance still owed in the heading and no summary or explainer.
    expect(dialog).toHaveTextContent('October 2026 · ₹45,000.00 still owed to Rajesh Kumar Sharma')
    const amount = within(dialog).getByLabelText(/^Amount/)
    expect(destination.compareDocumentPosition(amount) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(dialog).queryByLabelText('Landlord payout so far')).not.toBeInTheDocument()
    expect(dialog).not.toHaveTextContent('Outstanding flatmate rent shares')
    expect(dialog).not.toHaveTextContent('Nothing is added to the ledger')

    // A first tranche of what the residents have paid in.
    expect(amount).toHaveValue('30000.00')
    expect(dialog).toHaveTextContent(
      'Pre-filled with collected funds. You can pay any amount up to ₹45,000.00.',
    )

    const confirm = within(dialog).getByRole('button', { name: 'Record Payout' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/Payment reference/), '329188401928')
    await userEvent.selectOptions(
      within(dialog).getByLabelText(/Who funded this payout/),
      'resident_advance',
    )
    expect(confirm).toBeDisabled()
    await userEvent.selectOptions(within(dialog).getByLabelText(/Funding resident/), 'alice')
    expect(dialog).toHaveTextContent(
      'Credited this payout as a provider advance. They still owe their own share.',
    )
    await userEvent.click(confirm)

    await waitFor(() => expect(posted('/disburse')).toBeDefined())
    expect(posted('/disburse')!.body).toMatchObject({
      amount_paise: 3000000,
      disbursement_mode: 'resident_advance',
      funding_resident_id: 'alice',
      reference: '329188401928',
      note: null,
    })
    expect(posted('/disburse')!.body).not.toHaveProperty('landlord_receipt')
  })

  it('offers the whole rent on a first payout when nothing has been collected', async () => {
    renderRent(overview({ cycle: cycle({ collected_paise: 0, collected_count: 0 }) }))
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now to landlord' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('45000.00')
    expect(dialog).toHaveTextContent(
      'Pre-filled with the balance still owed. You can pay any amount up to ₹45,000.00.',
    )
  })

  const tranche = (overrides: Record<string, unknown> = {}) => ({
    disbursement_id: 'rentdisb-1',
    cycle_id: 'cycle-1',
    amount_paise: 4500000,
    disbursed_at: '2026-10-05',
    disbursement_reference: '329188401928',
    disbursed_by: 'pool',
    funding_resident_id: null,
    funding_resident_name: null,
    funding_payment_id: null,
    note: null,
    created_at: '2026-10-05T10:00:00+05:30',
    ...overrides,
  })
  const paidCycle = (overrides: Record<string, unknown> = {}) =>
    cycle({
      disbursement_status: 'disbursed',
      disbursed_at: '2026-10-05',
      disbursement_reference: '329188401928',
      disbursed_by: 'pool',
      disbursed_paise: 4500000,
      remaining_disbursement_paise: 0,
      disbursements: [tranche()],
      ...overrides,
    })
  // The landlord paid what had come in by the due date; the rest is to follow.
  const partCycle = (overrides: Record<string, unknown> = {}) =>
    cycle({
      disbursement_status: 'partially_disbursed',
      disbursed_at: '2026-10-05',
      disbursement_reference: 'UTR-FIRST',
      disbursed_by: 'pool',
      disbursed_paise: 3000000,
      remaining_disbursement_paise: 1500000,
      disbursements: [
        tranche({
          amount_paise: 3000000,
          disbursement_reference: 'UTR-FIRST',
          note: 'What had come in by the 5th',
        }),
      ],
      ...overrides,
    })
  // A lease saved before receipts were dropped may still say the landlord
  // issues them, and its months may still report one as pending.
  const receiptsOn = { ...config, requires_landlord_receipt: true }

  it('asks for no landlord receipt, even on a lease that once expected one', async () => {
    renderRent(overview({ config: receiptsOn }))
    await userEvent.click(await screen.findByRole('button', { name: 'Pay now to landlord' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText(/receipt/i)).not.toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText(/Payment reference/), 'UTR1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payout' }))

    await waitFor(() => expect(posted('/disburse')).toBeDefined())
    expect(posted('/disburse')!.body).toMatchObject({ disbursement_mode: 'pool', reference: 'UTR1' })
    expect(posted('/disburse')!.body).not.toHaveProperty('landlord_receipt')
  })

  it('shows no receipt badge or action on a paid month', async () => {
    renderRent(
      overview({ config: receiptsOn, cycle: paidCycle({ landlord_receipt_status: 'pending' }) }),
    )
    await screen.findByRole('table', { name: 'Rent owed to the landlord' })
    expect(screen.queryByText(/Receipt Pending|Receipt Attached/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Landlord's receipt/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Attach|Replace/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Download/ })).not.toBeInTheDocument()
  })

  it('sends lease setup and the tenancy terms to Housing', async () => {
    renderRent(overview({ config: null, cycle: null }))
    expect(await screen.findByRole('link', { name: 'Set up lease' })).toHaveAttribute(
      'href',
      '/housing?tab=lease',
    )
    // With no lease there is no rent to divide yet.
    expect(screen.queryByRole('radiogroup', { name: 'Rental agreement structure' })).toBeNull()
  })

  it('shows the rental agreement read-only, with no inputs on the page', async () => {
    renderRent(overview({ cycle: null, preview }))
    const agreement = await screen.findByLabelText('Rental agreement')
    expect(agreement).toHaveTextContent('Single Master Lease (Equal Split)')
    expect(agreement).toHaveTextContent('Equal share among 3 participants (₹15,000.00/resident)')
    expect(agreement).not.toHaveTextContent(/receipt/i)
    // The choices only appear once the operator asks to change the model.
    expect(screen.queryByRole('radiogroup', { name: 'Rental agreement structure' })).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByRole('radio', { name: 'Room-Tiered' })).toBeNull()
  })

  it('changes the agreement model in a dialog that saves only that', async () => {
    renderRent(overview({ cycle: null, preview: tieredPreview }), {
      'POST /api/rent/config': { ...config, rent_split_mode: 'room_tiered' },
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Edit agreement model' }))
    const dialog = screen.getByRole('dialog')
    // The dialog keeps the name of the card it edits.
    expect(within(dialog).getByRole('heading', { name: 'Rental Agreement Structure' })).toBeInTheDocument()
    const save = within(dialog).getByRole('button', { name: 'Save Model' })
    expect(save).toBeDisabled()
    const group = within(dialog).getByRole('radiogroup', { name: 'Rental agreement structure' })
    expect(within(group).getByRole('radio', { name: 'Single Master Lease (Equal Split)' })).toBeChecked()
    // This month's room rents add up, so room-tiered can be chosen.
    expect(within(group).getByText('Room Rents Add Up')).toBeInTheDocument()
    expect(within(group).getByRole('radio', { name: 'Square Footage / Floor-Area Ratio' })).toBeDisabled()
    expect(within(dialog).queryByRole('switch')).toBeNull()
    await userEvent.click(within(group).getByRole('radio', { name: 'Room-Tiered Fixed Pricing' }))
    await userEvent.click(save)
    await waitFor(() => expect(posted('/api/rent/config')).toBeDefined())
    expect(posted('/api/rent/config')!.body).toEqual({ rent_split_mode: 'room_tiered' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('cancels a model change without saving', async () => {
    renderRent(overview({ cycle: null, preview }))
    await userEvent.click(await screen.findByRole('button', { name: 'Edit agreement model' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(posted('/api/rent/config')).toBeUndefined()
  })

  it('says why a room-tiered model cannot generate rent yet', async () => {
    renderRent(
      overview({
        config: { ...config, rent_split_mode: 'room_tiered' },
        cycle: null,
        preview: {
          ...preview,
          split_mode: 'room_tiered',
          can_generate: false,
          blocked_reason: 'The sum of room rents (₹40,000.00) does not equal the monthly lease rent (₹45,000.00).',
          room_tiered: {
            ...preview.room_tiered,
            blocked_reason:
              'The sum of room rents (₹40,000.00) does not equal the monthly lease rent (₹45,000.00).',
          },
        },
      }),
    )
    const agreement = await screen.findByLabelText('Rental agreement')
    expect(agreement).toHaveTextContent('Room-Tiered Fixed Pricing')
    expect(agreement).toHaveTextContent("Each resident pays their room's base rent.")
    expect(agreement).toHaveTextContent('does not equal the monthly lease rent')
    expect(screen.getByRole('button', { name: 'Generate October 2026 rent' })).toBeDisabled()
  })

  it('generates rent without the residents the operator left out', async () => {
    renderRent(overview({ cycle: null, preview }))
    await userEvent.click(
      await screen.findByRole('checkbox', { name: 'Chris takes part in October 2026 rent' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Generate October 2026 rent' }))
    await waitFor(() => expect(posted('/generate')).toBeDefined())
    // The split is the household's saved model, so none is sent.
    expect(posted('/generate')!.body).toEqual({ excluded_resident_ids: ['chris'] })
  })

  it('shows each share with its room when the model is room-tiered', async () => {
    renderRent(
      overview({
        config: { ...config, rent_split_mode: 'room_tiered' },
        cycle: null,
        preview: { ...tieredPreview, split_mode: 'room_tiered' },
      }),
    )
    expect(await screen.findByText('Room 1')).toBeInTheDocument()
    expect(screen.getByText(/charged their room's base rent/)).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'Room-Tiered' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Generate October 2026 rent' }))
    await waitFor(() => expect(posted('/generate')).toBeDefined())
    expect(posted('/generate')!.body).toEqual({ excluded_resident_ids: [] })
  })

  it('does not offer generation when the backend says it is blocked', async () => {
    renderRent(
      overview({
        cycle: null,
        preview: {
          ...preview,
          can_generate: false,
          blocked_reason: 'The 2026-10 statement has been finalized.',
        },
      }),
    )
    expect(await screen.findByText('The 2026-10 statement has been finalized.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate October 2026 rent' })).toBeDisabled()
  })

  it('shows a paid month as fully disbursed, with no HRA receipt on offer', async () => {
    renderRent(overview({ cycle: paidCycle() }))
    await screen.findByRole('table', { name: 'Rent owed to the landlord' })
    // Paid in full: the button stays on the row, switched off.
    expect(screen.getByRole('button', { name: 'Pay now to landlord' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /HRA receipt/ })).not.toBeInTheDocument()
    expect(stub!.calls.some((call) => call.path.includes('/receipt/'))).toBe(false)
    expect(stat('Landlord Due Date')).toHaveTextContent('Paid 5 Oct 2026')
    expect(landlordRow()).toHaveTextContent('₹45,000.00₹45,000.00₹0.00Paid')
    expect(landlordRow()).not.toHaveTextContent('5 Oct 2026')
    // The payment itself is in the landlord's statement, not on the page.
    expect(screen.queryByRole('table', { name: 'Payments to the landlord' })).not.toBeInTheDocument()
    await userEvent.click(landlordRow())
    const dialog = await screen.findByRole('dialog', { name: 'Landlord Payout Statement' })
    const payments = within(dialog).getByRole('table', { name: 'Payments to the landlord' })
    expect(within(payments).getByRole('row', { name: /329188401928/ })).toHaveTextContent('₹45,000.00')
  })

  describe('paying the landlord in tranches', () => {
    it("lays out the landlord's side as the collection table, with the one payout button in it", async () => {
      renderRent(overview())
      const table = await screen.findByRole('table', { name: 'Rent owed to the landlord' })
      expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
        'Landlord / Destination',
        'Total Rent',
        'Paid to Owner',
        'Still Owed',
        'Status',
        'Actions',
      ])
      const landlord = within(table).getByRole('row', { name: /Rajesh Kumar Sharma/ })
      expect(landlord).toHaveTextContent('rajeshsharma@okaxis')
      expect(landlord).toHaveTextContent('₹45,000.00₹0.00₹45,000.00Unpaid')
      // Nothing paid yet, so there are no payments to list beneath it.
      expect(screen.queryByRole('table', { name: 'Payments to the landlord' })).not.toBeInTheDocument()

      const payout = screen.getAllByRole('button', { name: 'Pay now to landlord' })
      expect(payout).toHaveLength(1)
      expect(within(landlord).getByRole('button', { name: 'Pay now to landlord' })).toBe(payout[0])
      expect(payout[0]).toBeEnabled()
      expect(payout[0]).toHaveTextContent('Pay Now')
    })

    it('shows the landlord with nothing owed before the month is generated', async () => {
      renderRent(overview({ cycle: null, preview }))
      const table = await screen.findByRole('table', { name: 'Rent owed to the landlord' })
      const landlord = within(table).getByRole('row', { name: /Rajesh Kumar Sharma/ })
      expect(landlord).toHaveTextContent('₹45,000.00')
      expect(landlord).toHaveTextContent('Not generated')
      expect(within(landlord).getByRole('button', { name: 'Pay now to landlord' })).toBeDisabled()
      // Nothing to show until the month is generated, so the row opens nothing.
      await userEvent.click(landlord)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('shows a part-paid month and still offers the next payout', async () => {
      renderRent(overview({ cycle: partCycle() }))
      await screen.findByRole('table', { name: 'Rent owed to the landlord' })
      expect(screen.getByRole('button', { name: 'Pay now to landlord' })).toBeEnabled()
      expect(stat('Landlord Due Date')).toHaveTextContent('₹15,000.00 still to pay')
      expect(landlordRow()).toHaveTextContent('₹45,000.00₹30,000.00₹15,000.00Part paid')
    })

    it('lists each tranche with who funded it and the totals', async () => {
      renderRent(
        overview({
          cycle: paidCycle({
            disbursed_at: '2026-10-09',
            disbursement_reference: 'UTR-SECOND',
            disbursed_by: 'mixed',
            disbursements: [
              tranche({
                amount_paise: 3000000,
                disbursement_reference: 'UTR-FIRST',
                note: 'What had come in by the 5th',
              }),
              tranche({
                disbursement_id: 'rentdisb-2',
                amount_paise: 1500000,
                disbursed_at: '2026-10-09',
                disbursement_reference: 'UTR-SECOND',
                disbursed_by: 'resident_advance',
                funding_resident_id: 'alice',
                funding_resident_name: 'Alice',
                funding_payment_id: 'pay-1',
              }),
            ],
          }),
        }),
      )
      // The row says where the month stands; its statement lists the payments.
      expect(await screen.findByRole('table', { name: 'Rent owed to the landlord' })).toBeInTheDocument()
      expect(landlordRow()).toHaveTextContent('Paid')
      expect(landlordRow()).not.toHaveTextContent('9 Oct 2026')
      expect(within(landlordRow()).getByRole('button', { name: 'Pay now to landlord' })).toBeDisabled()
      await userEvent.click(landlordRow())
      const dialog = await screen.findByRole('dialog', { name: 'Landlord Payout Statement' })
      expect(dialog).toHaveTextContent('Rajesh Kumar Sharma · October 2026')
      expect(within(dialog).getByText('Total Rent').parentElement).toHaveTextContent('₹45,000.00')
      expect(within(dialog).getByText('Paid to Owner').parentElement).toHaveTextContent('₹45,000.00')
      expect(within(dialog).getByText('Still Owed').parentElement).toHaveTextContent('₹0.00')
      const history = within(dialog).getByRole('region', { name: 'Disbursement history' })
      expect(history).toHaveTextContent('Paid')
      const table = within(history).getByRole('table', { name: 'Payments to the landlord' })
      expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
        'Date',
        'Amount',
        'Funded From',
        'Reference / UTR',
        'Note',
      ])
      const first = within(table).getByRole('row', { name: /UTR-FIRST/ })
      expect(first).toHaveTextContent('5 Oct 2026')
      expect(first).toHaveTextContent('₹30,000.00')
      expect(first).toHaveTextContent('House Kitty')
      expect(first).toHaveTextContent('What had come in by the 5th')
      const second = within(table).getByRole('row', { name: /UTR-SECOND/ })
      expect(second).toHaveTextContent('9 Oct 2026')
      expect(second).toHaveTextContent('₹15,000.00')
      expect(second).toHaveTextContent('Alice (advance)')
      // Closed by its own ✕: there is no footer of actions.
      expect(within(dialog).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
        'Close dialog',
      ])
    })

    it('opens an empty statement before the landlord is paid, but not from the payout button', async () => {
      renderRent(overview())
      await userEvent.click(
        within(await screen.findByRole('table', { name: 'Rent owed to the landlord' })).getByRole(
          'button',
          { name: 'Pay now to landlord' },
        ),
      )
      const payout = await screen.findByRole('dialog', { name: 'Record Landlord Payout' })
      expect(screen.queryByRole('dialog', { name: 'Landlord Payout Statement' })).not.toBeInTheDocument()
      await userEvent.click(within(payout).getByRole('button', { name: 'Cancel' }))

      await userEvent.click(landlordRow())
      const dialog = await screen.findByRole('dialog', { name: 'Landlord Payout Statement' })
      expect(within(dialog).getByText('Still Owed').parentElement).toHaveTextContent('₹45,000.00')
      expect(within(dialog).getByRole('region', { name: 'Disbursement history' })).toHaveTextContent(
        'Unpaid',
      )
      expect(dialog).toHaveTextContent('No payouts recorded yet for this month.')
      expect(within(dialog).queryByRole('table')).not.toBeInTheDocument()
    })

    it('opens the landlord statement from the keyboard too', async () => {
      renderRent(overview({ cycle: partCycle() }))
      await screen.findByRole('table', { name: 'Rent owed to the landlord' })
      landlordRow().focus()
      await userEvent.keyboard('{Enter}')
      const dialog = await screen.findByRole('dialog', { name: 'Landlord Payout Statement' })
      expect(within(dialog).getByRole('row', { name: /UTR-FIRST/ })).toHaveTextContent('₹30,000.00')
    })

    it('offers the balance as the next tranche and refuses more than is owed', async () => {
      renderRent(overview({ cycle: partCycle() }))
      await userEvent.click(await screen.findByRole('button', { name: 'Pay now to landlord' }))
      const dialog = await screen.findByRole('dialog')
      expect(dialog).toHaveTextContent('October 2026 · ₹15,000.00 still owed to Rajesh Kumar Sharma')

      const amount = within(dialog).getByLabelText(/^Amount/)
      expect(amount).toHaveValue('15000.00')
      expect(dialog).toHaveTextContent(
        'Pre-filled with the balance still owed. You can pay any amount up to ₹15,000.00.',
      )

      const confirm = within(dialog).getByRole('button', { name: 'Record Payout' })
      await userEvent.type(within(dialog).getByLabelText(/Payment reference/), 'UTR-SECOND')
      expect(confirm).toBeEnabled()

      await userEvent.clear(amount)
      await userEvent.type(amount, '16000')
      expect(within(dialog).getByText('More than the ₹15,000.00 still owed to the landlord.')).toBeInTheDocument()
      expect(confirm).toBeDisabled()

      await userEvent.clear(amount)
      await userEvent.type(amount, '0')
      expect(confirm).toBeDisabled()

      await userEvent.clear(amount)
      await userEvent.type(amount, '5000')
      await userEvent.type(within(dialog).getByLabelText(/^Note/), 'Chris paid late')
      expect(confirm).toBeEnabled()
      await userEvent.click(confirm)

      await waitFor(() => expect(posted('/disburse')).toBeDefined())
      expect(posted('/disburse')!.body).toMatchObject({
        amount_paise: 500000,
        disbursement_mode: 'pool',
        reference: 'UTR-SECOND',
        note: 'Chris paid late',
      })
    })
  })

  describe('the contract term', () => {
    const term = (lifecycle: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) => ({
      ...config,
      monthly_rent_paise: 2850000,
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
      lifecycle: {
        as_of_date: '2027-01-11',
        term_number: 1,
        current_month_index: 10,
        duration_months: 11,
        elapsed_percent: 85,
        days_until_expiry: 48,
        notice_deadline_date: '2027-01-29',
        is_notice_window_active: false,
        is_expired: false,
        lock_in_end_date: '2026-09-30',
        is_within_lock_in: false,
        projected_renewal_rent_paise: 2992500,
        ...lifecycle,
      },
      ...extra,
    })
    const inWindow = { is_notice_window_active: true, days_until_expiry: 20, current_month_index: 11 }

    it('shows no lease term banner before the notice window', async () => {
      renderRent(overview({ config: term() }))
      await screen.findByRole('table', { name: 'Rent shares and collection' })
      expect(screen.queryByRole('region', { name: 'Lease term' })).not.toBeInTheDocument()
    })

    it('shows no lease term banner for a lease with no start date', async () => {
      renderRent(overview())
      await screen.findByRole('table', { name: 'Rent shares and collection' })
      expect(screen.queryByText(/^Term:/)).not.toBeInTheDocument()
      expect(screen.queryByRole('region', { name: 'Lease term' })).not.toBeInTheDocument()
    })

    it('offers both choices when the notice window opens and renews at the escalated rent', async () => {
      const renewed = term({}, { term_number: 2, monthly_rent_paise: 2992500, lease_end_date: '2028-01-31' })
      renderRent(overview({ config: term(inWindow) }), {
        'POST /api/rent/lease/renew': renewed,
      })
      const banner = await screen.findByRole('region', { name: 'Lease term' })
      expect(banner).toHaveTextContent('Notice Window Open: Master Lease Ends on 28 Feb 2027')
      expect(
        within(banner).getByRole('button', { name: 'Plan to Vacate (Serve Notice)' }),
      ).toBeInTheDocument()

      await userEvent.click(
        within(banner).getByRole('button', { name: 'Renew Tenure (+5% Escalation)' }),
      )
      const dialog = await screen.findByRole('dialog')
      expect(within(dialog).getByLabelText(/New term starts/)).toHaveValue('2027-03-01')
      expect(within(dialog).getByLabelText('Duration')).toHaveValue('11')
      expect(within(dialog).getByLabelText(/New monthly rent/)).toHaveValue('29925.00')
      expect(dialog).toHaveTextContent('1 Mar 2027 – 31 Jan 2028')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm Renewal' }))

      await waitFor(() => expect(posted('/lease/renew')).toBeDefined())
      expect(posted('/lease/renew')!.body).toEqual({
        new_start_date: '2027-03-01',
        duration_months: 11,
        new_monthly_rent_paise: 2992500,
        escalation_type: 'percent',
        escalation_percent: 5,
      })
      expect(await screen.findByText('Lease renewed for term 2')).toBeInTheDocument()
    })

    it('refuses a renewal that starts before the current term ends', async () => {
      renderRent(overview({ config: term(inWindow) }))
      await userEvent.click(await screen.findByRole('button', { name: /Renew Tenure/ }))
      const dialog = await screen.findByRole('dialog')
      const start = within(dialog).getByLabelText(/New term starts/)
      await userEvent.clear(start)
      await userEvent.type(start, '2027-02-15')
      expect(within(dialog).getByText(/must start after the current one ends/)).toBeInTheDocument()
      expect(within(dialog).getByRole('button', { name: 'Confirm Renewal' })).toBeDisabled()
    })

    it('records notice to vacate with the deposit recovery worked out', async () => {
      renderRent(overview({ config: term(inWindow) }), {
        'POST /api/rent/lease/vacate': term(inWindow, {
          lease_status: 'notice_served',
          notice_served_date: '2027-01-29',
          target_handover_date: '2027-02-28',
        }),
      })
      await userEvent.click(
        await screen.findByRole('button', { name: 'Plan to Vacate (Serve Notice)' }),
      )
      const dialog = await screen.findByRole('dialog')
      const served = within(dialog).getByLabelText(/Notice served on/)
      await userEvent.clear(served)
      await userEvent.type(served, '2027-01-29')
      expect(within(dialog).getByLabelText(/Target handover date/)).toHaveValue('2027-02-28')
      expect(dialog).toHaveTextContent('~₹45,000.00 each between 3 residents')

      await userEvent.type(within(dialog).getByLabelText('Expected deductions'), '15000')
      expect(dialog).toHaveTextContent('Net Refund₹1,20,000.00')
      expect(dialog).toHaveTextContent('~₹40,000.00 each between 3 residents')
      await userEvent.type(within(dialog).getByLabelText('Notes'), '30-day notice provided')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm Notice Served' }))

      await waitFor(() => expect(posted('/lease/vacate')).toBeDefined())
      expect(posted('/lease/vacate')!.body).toEqual({
        notice_served_date: '2027-01-29',
        target_handover_date: '2027-02-28',
        notes: '30-day notice provided Expected deposit deductions: ₹15,000.00.',
      })
      expect(await screen.findByText('Notice to vacate recorded')).toBeInTheDocument()
    })

    it('keeps a reminder once the term has ended and still shows the rent', async () => {
      renderRent(
        overview({ config: term({ is_expired: true, days_until_expiry: -12, current_month_index: 11 }) }),
      )
      const banner = await screen.findByRole('region', { name: 'Lease term' })
      expect(banner).toHaveTextContent('Lease Term Ended on 28 Feb 2027')
      expect(banner).toHaveTextContent('Rent keeps generating at the current rate')
      expect(screen.getByRole('table', { name: 'Rent shares and collection' })).toBeInTheDocument()
    })

    it('shows the planned handover once notice is served, with renewing still open', async () => {
      renderRent(
        overview({
          config: term(inWindow, {
            lease_status: 'notice_served',
            notice_served_date: '2027-01-29',
            target_handover_date: '2027-02-28',
          }),
        }),
      )
      const banner = await screen.findByRole('region', { name: 'Lease term' })
      expect(banner).toHaveTextContent('Notice Served: Handover Planned for 28 Feb 2027')
      expect(within(banner).queryByRole('button', { name: /Plan to Vacate/ })).not.toBeInTheDocument()
      expect(within(banner).getByRole('button', { name: /Renew Tenure/ })).toBeInTheDocument()
    })
  })
})
