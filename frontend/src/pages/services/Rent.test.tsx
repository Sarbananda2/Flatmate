/**
 * The rent screen has one workflow switch -- whether the landlord issues
 * receipts -- and it sends money to the backend. These tests cover both: the
 * receipt upload appears only when the lease asks for it, and every body the
 * screen posts carries integer paise and the exact fields the operator chose.
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
      [`POST /api/rent/cycle/${MONTH}/landlord-receipt`]: cycle({
        disbursement_status: 'disbursed',
        landlord_receipt_status: 'attached',
      }),
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

describe('RentMonth', () => {
  it('shows the rent, the equal share, and what has been collected', async () => {
    renderRent(overview())
    expect(await screen.findByText('Disbursement Pending')).toBeInTheDocument()
    expect(screen.getByText('Equal Share (1/3)')).toBeInTheDocument()
    expect(screen.getByText('2 of 3 covered · 66.7%')).toBeInTheDocument()
    expect(screen.getByText('5 Oct 2026')).toBeInTheDocument()

    const table = screen.getByRole('table', { name: 'Rent shares and collection' })
    const chris = within(table).getByRole('row', { name: /Chris/ })
    expect(chris).toHaveTextContent('₹15,000.00')
    expect(chris).toHaveTextContent('Unpaid')
    // The landlord issues no receipts on this lease, so no row mentions a slip.
    expect(table).not.toHaveTextContent('HRA slip after payout')
  })

  it('mentions the HRA slip only to residents who have paid, when receipts are tracked', async () => {
    renderRent(overview({ config: receiptsOn }))
    const table = await screen.findByRole('table', { name: 'Rent shares and collection' })
    expect(within(table).getByRole('row', { name: /Alice/ })).toHaveTextContent(
      'HRA slip after payout',
    )
    const chris = within(table).getByRole('row', { name: /Chris/ })
    expect(chris).not.toHaveTextContent('HRA slip after payout')
    expect(
      within(chris).getByRole('button', { name: 'Record rent payment from Chris' }),
    ).toBeInTheDocument()
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
    // Only residents who still owe rent get the action.
    expect(within(table).getAllByRole('button', { name: /Record rent payment from/ })).toHaveLength(1)
    await userEvent.click(
      within(table).getByRole('button', { name: 'Record rent payment from Chris' }),
    )

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Chris · October 2026 rent · ₹15,000.00 outstanding')
    expect(within(dialog).getByLabelText(/Amount paid/)).toHaveValue('15000.00')
    expect(within(dialog).getByLabelText(/Payment date/)).not.toHaveValue('')
    await userEvent.type(within(dialog).getByLabelText('Reference'), 'UPI-778')
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
    expect(
      within(screen.getByRole('table', { name: 'Rent shares and collection' })).queryByRole(
        'button',
        { name: /Record rent payment from/ },
      ),
    ).not.toBeInTheDocument()
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
    await userEvent.click(await screen.findByRole('button', { name: 'Record rent payment from Chris' }))
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
    renderRent(overview(), {}, { ...apartment, pool_name: 'Household Pool', pool_bank_account: null, pool_bank_ifsc: null, pool_upi_id: null })
    await userEvent.click(await screen.findByRole('button', { name: 'Record rent payment from Chris' }))
    dialog = screen.getByRole('dialog', { name: 'Record Rent Payment' })
    destination = within(dialog).getByRole('region', { name: 'Payment destination' })
    expect(destination).toHaveTextContent('Transfer toHousehold Pool')
    expect(within(destination).getByRole('link', { name: /Add them on Payments/ })).toHaveAttribute(
      'href',
      '/payments',
    )
  })

  it('says where the money goes when the amount is not exactly the share', async () => {
    renderRent(overview())
    await userEvent.click(
      await screen.findByRole('button', { name: 'Record rent payment from Chris' }),
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
    await userEvent.click(record)
    await waitFor(() => expect(posted('/pay')).toBeDefined())
    expect((posted('/pay')!.body as { amount_paise: number }).amount_paise).toBe(1650050)
  })

  it('asks only for date, funder, and reference when the landlord issues no receipts', async () => {
    renderRent(overview())
    await userEvent.click(await screen.findByRole('button', { name: 'Record Landlord Payout' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText(/Landlord's receipt/)).not.toBeInTheDocument()

    // Where to send it, in full, and what recording it commits to.
    const destination = within(dialog).getByRole('region', { name: 'Payout destination' })
    expect(destination).toHaveTextContent('Rajesh Kumar Sharma')
    expect(destination).toHaveTextContent('₹45,000.00')
    expect(destination).toHaveTextContent('50100012348912')
    expect(destination).toHaveTextContent('HDFC0000128')
    expect(destination).toHaveTextContent('rajeshsharma@okaxis')
    expect(within(destination).getByRole('button', { name: 'Copy UPI ID' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('locks this month\'s rent cycle')
    expect(dialog).toHaveTextContent('Nothing is added to the ledger')

    const confirm = within(dialog).getByRole('button', { name: 'Confirm & Post Payout' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/Payment reference/), '329188401928')
    await userEvent.selectOptions(
      within(dialog).getByLabelText(/Who funded this payout/),
      'resident_advance',
    )
    expect(confirm).toBeDisabled()
    await userEvent.selectOptions(within(dialog).getByLabelText(/Funding resident/), 'alice')
    await userEvent.click(confirm)

    await waitFor(() => expect(posted('/disburse')).toBeDefined())
    expect(posted('/disburse')!.body).toMatchObject({
      disbursement_mode: 'resident_advance',
      funding_resident_id: 'alice',
      reference: '329188401928',
      landlord_receipt: null,
    })
  })

  const paidCycle = (overrides: Record<string, unknown> = {}) =>
    cycle({
      disbursement_status: 'disbursed',
      disbursed_at: '2026-10-05',
      disbursement_reference: '329188401928',
      disbursed_by: 'pool',
      ...overrides,
    })
  const receiptsOn = { ...config, requires_landlord_receipt: true }
  const attachedReceipt = {
    filename: 'receipt-oct.pdf',
    content_type: 'application/pdf',
    size_bytes: 7,
    sha256: 'b'.repeat(64),
    uploaded_at: '2026-10-09T18:00:00+05:30',
  }

  it('records a payout without the receipt even when the lease expects one', async () => {
    renderRent(overview({ config: receiptsOn }))
    await userEvent.click(await screen.findByRole('button', { name: 'Record Landlord Payout' }))
    const dialog = await screen.findByRole('dialog')
    const upload = within(dialog).getByLabelText(/Landlord's receipt/)
    expect(upload).not.toBeRequired()
    const confirm = within(dialog).getByRole('button', { name: 'Confirm & Post Payout' })

    await userEvent.type(within(dialog).getByLabelText(/Payment reference/), 'UTR1')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)

    await waitFor(() => expect(posted('/disburse')).toBeDefined())
    expect(posted('/disburse')!.body).toMatchObject({
      disbursement_mode: 'pool',
      funding_resident_id: null,
      reference: 'UTR1',
      landlord_receipt: null,
    })
  })

  it('sends the receipt with the payout when one is chosen', async () => {
    renderRent(overview({ config: receiptsOn }))
    await userEvent.click(await screen.findByRole('button', { name: 'Record Landlord Payout' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/Payment reference/), 'UTR1')
    await userEvent.upload(
      within(dialog).getByLabelText(/Landlord's receipt/),
      new File(['receipt'], 'receipt.pdf', { type: 'application/pdf' }),
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm & Post Payout' }))

    await waitFor(() => expect(posted('/disburse')).toBeDefined())
    expect((posted('/disburse')!.body as { landlord_receipt: unknown }).landlord_receipt).toEqual({
      filename: 'receipt.pdf',
      content_type: 'application/pdf',
      content_base64: btoa('receipt'),
    })
  })

  it('flags a paid month whose receipt has not arrived and lets it be attached', async () => {
    renderRent(
      overview({
        config: receiptsOn,
        cycle: paidCycle({ landlord_receipt_status: 'pending' }),
      }),
    )
    expect((await screen.findAllByText('Receipt Pending')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('link', { name: /Download/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Attach Landlord Receipt' }))
    const dialog = await screen.findByRole('dialog')
    const attach = within(dialog).getByRole('button', { name: 'Attach Receipt' })
    expect(attach).toBeDisabled()
    await userEvent.upload(
      within(dialog).getByLabelText(/Landlord's receipt/),
      new File(['receipt'], 'receipt-oct.pdf', { type: 'application/pdf' }),
    )
    await userEvent.click(attach)

    await waitFor(() => expect(posted('/landlord-receipt')).toBeDefined())
    expect(posted('/landlord-receipt')!.body).toEqual({
      filename: 'receipt-oct.pdf',
      content_type: 'application/pdf',
      content_base64: btoa('receipt'),
    })
  })

  it('offers download and replace once the receipt is attached', async () => {
    renderRent(
      overview({
        config: receiptsOn,
        cycle: paidCycle({
          landlord_receipt_status: 'attached',
          landlord_receipt: attachedReceipt,
        }),
      }),
    )
    expect((await screen.findAllByText('Receipt Attached')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Receipt Pending')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Download/ })).toHaveAttribute(
      'href',
      `/api/rent/cycle/${MONTH}/landlord-receipt`,
    )
    expect(screen.queryByRole('button', { name: 'Attach Landlord Receipt' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('This removes receipt-oct.pdf')
    await userEvent.upload(
      within(dialog).getByLabelText(/Landlord's receipt/),
      new File(['v2'], 'corrected.png', { type: 'image/png' }),
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Replace Receipt' }))
    await waitFor(() => expect(posted('/landlord-receipt')).toBeDefined())
    expect((posted('/landlord-receipt')!.body as { filename: string }).filename).toBe(
      'corrected.png',
    )
  })

  it('refuses an unusable file before anything is sent', async () => {
    renderRent(
      overview({
        config: receiptsOn,
        cycle: paidCycle({ landlord_receipt_status: 'pending' }),
      }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Attach Landlord Receipt' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.upload(
      within(dialog).getByLabelText(/Landlord's receipt/),
      new File(['<html>'], 'receipt.html', { type: 'text/html' }),
      { applyAccept: false },
    )
    expect(within(dialog).getByText('Choose a PDF, JPEG, PNG, or WebP file.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Attach Receipt' })).toBeDisabled()
  })

  it('shows no receipt badge or action when the landlord issues none', async () => {
    renderRent(overview({ cycle: paidCycle() }))
    expect(await screen.findByText('Disbursed')).toBeInTheDocument()
    expect(screen.queryByText('Receipt Pending')).not.toBeInTheDocument()
    expect(screen.queryByText('Receipt Attached')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Attach Landlord Receipt' })).not.toBeInTheDocument()
    expect(screen.queryByText("Landlord's receipt")).not.toBeInTheDocument()
  })

  it('shows the deposit total and links to its own screen instead of embedding it', async () => {
    renderRent(overview({ config: { ...config, security_deposit_paise: 15000000 } }))
    const link = await screen.findByRole('link', { name: 'Manage Deposit Slots & Escrow' })
    expect(link).toHaveAttribute('href', '/housing?tab=deposits')
    const panel = link.closest('section')!
    expect(panel).toHaveTextContent('Total deposit₹1,50,000.00')
    // The slots table lives on the deposits screen now, and is not fetched here.
    expect(screen.queryByRole('table', { name: 'Household deposit slots' })).not.toBeInTheDocument()
    expect(stub!.calls.some((call) => call.path === '/api/rent/deposits')).toBe(false)
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

  it('shows the master tenancy in one line, with the way to its terms', async () => {
    renderRent(
      overview({
        config: {
          ...config,
          lease_start_date: '2026-04-01',
          lease_end_date: '2027-02-28',
          notice_period_days: 30,
          lifecycle: { current_month_index: 7, duration_months: 11 },
        },
        cycle: null,
        preview,
      }),
    )
    const card = await screen.findByRole('region', { name: 'Master tenancy' })
    expect(card).toHaveTextContent('1 Apr 2026 – 28 Feb 2027 (Month 7 of 11)')
    expect(card).toHaveTextContent('Landlord: Rajesh Kumar Sharma')
    expect(card).toHaveTextContent('30-day notice')
    expect(
      within(card).getByRole('link', { name: /Manage Master Tenancy & Renewal/ }),
    ).toHaveAttribute('href', '/housing?tab=lease')
  })

  it('shows the rental agreement read-only, with no inputs on the page', async () => {
    renderRent(overview({ cycle: null, preview }))
    const agreement = await screen.findByLabelText('Rental agreement')
    expect(agreement).toHaveTextContent('Single Master Lease (Equal Split)')
    expect(agreement).toHaveTextContent('Equal share among 3 participants (₹15,000.00/resident)')
    expect(agreement).toHaveTextContent('Requires Landlord Receipts: No')
    // The choices only appear once the operator asks to change the model.
    expect(screen.queryByRole('radiogroup', { name: 'Rental agreement structure' })).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByRole('radio', { name: 'Room-Tiered' })).toBeNull()
  })

  it('changes the agreement model and receipts in a dialog that saves only those', async () => {
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
    await userEvent.click(within(group).getByRole('radio', { name: 'Room-Tiered Fixed Pricing' }))
    await userEvent.click(
      within(dialog).getByRole('switch', { name: 'Do you receive receipts from the landlord?' }),
    )
    await userEvent.click(save)
    await waitFor(() => expect(posted('/api/rent/config')).toBeDefined())
    expect(posted('/api/rent/config')!.body).toEqual({
      rent_split_mode: 'room_tiered',
      requires_landlord_receipt: true,
    })
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

  it('shows a printable HRA receipt once the landlord has been paid', async () => {
    const paid = cycle({
      disbursement_status: 'disbursed',
      disbursed_at: '2026-10-04',
      disbursement_reference: '329188401928',
      disbursed_by: 'pool',
      participants: cycle().participants.map((row) => ({ ...row, receipt_available: true })),
    })
    renderRent(overview({ cycle: paid }), {
      [`/api/rent/cycle/${MONTH}/receipt/alice`]: {
        receipt_id: 'r1',
        receipt_number: 'RCPT-2026-10-01',
        cycle_id: 'cycle-1',
        settlement_month: MONTH,
        month_label: 'October 2026',
        resident_id: 'alice',
        resident_name: 'Alice',
        premises: 'Flat 402',
        amount_paise: 1500000,
        amount_in_words: 'Fifteen Thousand Rupees Only',
        total_rent_paise: 4500000,
        participant_count: 3,
        landlord_name: 'Rajesh Kumar Sharma',
        landlord_pan: 'ABCPS1234K',
        landlord_pan_advised: false,
        disbursed_at: '2026-10-04',
        disbursed_by: 'pool',
        disbursement_reference: '329188401928',
        share_settled: true,
        issued_at: '2026-10-04T10:00:00+05:30',
        verification_hash: 'a'.repeat(64),
        hash_matches: true,
      },
    })
    expect(await screen.findByText('Disbursed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record Landlord Payout' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'View HRA receipt for Alice' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('RCPT-2026-10-01')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Fifteen Thousand Rupees Only')
    expect(dialog).toHaveTextContent('ABCPS1234K')
    expect(dialog).toHaveTextContent('a'.repeat(64))
    expect(within(dialog).getByRole('button', { name: /Print/ })).toBeEnabled()
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

    it('shows the term in the header and no banner before the notice window', async () => {
      renderRent(overview({ config: term() }))
      expect(
        await screen.findByText('Term: Month 10 of 11 (Expires 28 Feb 2027)'),
      ).toBeInTheDocument()
      expect(screen.queryByRole('region', { name: 'Lease term' })).not.toBeInTheDocument()
    })

    it('shows no term pill or banner for a lease with no start date', async () => {
      renderRent(overview())
      expect(await screen.findByText('Disbursement Pending')).toBeInTheDocument()
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
      expect(screen.getByText('Term: Month 11 of 11 (Expired 28 Feb 2027)')).toBeInTheDocument()
      expect(screen.getByText('Disbursement Pending')).toBeInTheDocument()
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
