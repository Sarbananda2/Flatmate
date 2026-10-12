/**
 * The household's deposit slots on the Rent screen and the resident page.
 *
 * Money leaves these dialogs as integer paise, so the tests pin the bodies
 * they post, and the rules that stop a bad one being sent: the funding mode
 * and the settlement path are never pre-selected, the slots must total the
 * deposit exactly, a payment cannot exceed what is owed, deductions need a
 * reason and cannot exceed what was put in, and no date is in the future.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SecurityDepositsPage, {
  ResidentDepositCard,
  SecurityDepositSection,
} from './SecurityDeposits'
import { installFetchStub, type Recorded } from '../../test/server'
import { todayIso } from '../../lib/format'

const TODAY = todayIso()
const SHARE = 5000000
const MASTER = 15000000

function slot(number: number, id: string | null, name: string | null, overrides: Record<string, unknown> = {}) {
  return {
    slot_id: `slot-${number}`,
    slot_number: number,
    slot_label: `Room ${number}`,
    target_deposit_paise: SHARE,
    assigned_resident_id: id,
    assigned_display_name: name,
    assigned_since: id ? '2026-10-01' : null,
    status: id ? 'occupied_pending' : 'vacant',
    funded_amount_paise: 0,
    outstanding_paise: id ? SHARE : 0,
    funded_date: null,
    payment_method: null,
    payment_reference: null,
    paid_to_resident_id: null,
    paid_to_display_name: null,
    is_primary_signatory: false,
    transactions: [],
    ...overrides,
  }
}

const funded = {
  status: 'occupied_funded',
  funded_amount_paise: SHARE,
  outstanding_paise: 0,
  funded_date: '2026-10-02',
  payment_method: 'upi',
  payment_reference: 'UTR428919028471',
  paid_to_resident_id: 'alice',
  paid_to_display_name: 'Alice',
}

const fronted = {
  ...funded,
  is_primary_signatory: true,
  funded_date: null,
  payment_method: 'direct_to_landlord',
  payment_reference: null,
  paid_to_resident_id: null,
  paid_to_display_name: null,
}

const residents = [
  { resident_id: 'alice', display_name: 'Alice' },
  { resident_id: 'bob', display_name: 'Bob' },
  { resident_id: 'chris', display_name: 'Chris' },
]

const proposal = residents.map((resident, index) => ({
  slot_number: index + 1,
  slot_label: `Room ${index + 1}`,
  target_deposit_paise: SHARE,
  assigned_resident_id: resident.resident_id,
  assigned_display_name: resident.display_name,
}))

/** The spec's worked example: Alice fronted ₹1,50,000 and has ₹1,00,000 back. */
function summary(overrides: Record<string, unknown> = {}) {
  return {
    is_lease_configured: true,
    master_deposit_paise: MASTER,
    funding_mode: 'primary_signatory',
    primary_signatory_id: 'alice',
    primary_signatory_name: 'Alice',
    signatory_capital_exposure_paise: SHARE,
    reimbursements_received_paise: 2 * SHARE,
    retained_deductions_paise: 0,
    total_slots_count: 3,
    vacant_slots_count: 0,
    total_target_paise: MASTER,
    total_funded_paise: MASTER,
    unfunded_gap_paise: 0,
    is_balanced: true,
    unassigned_residents: [],
    proposal: [],
    slots: [
      slot(1, 'alice', 'Alice', fronted),
      slot(2, 'bob', 'Bob', funded),
      slot(3, 'chris', 'Chris', funded),
    ],
    ...overrides,
  }
}

const unconfigured = summary({
  funding_mode: null,
  primary_signatory_id: null,
  primary_signatory_name: null,
  signatory_capital_exposure_paise: null,
  reimbursements_received_paise: 0,
  total_slots_count: 0,
  total_target_paise: 0,
  total_funded_paise: 0,
  is_balanced: false,
  unassigned_residents: residents,
  proposal,
  slots: [],
})

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function renderSection(state: Record<string, unknown>, routes: Record<string, unknown> = {}) {
  stub = installFetchStub({ routes: { '/api/rent/deposits': state, ...routes } as never })
  return render(
    <MemoryRouter>
      <SecurityDepositSection />
    </MemoryRouter>,
  )
}

const posted = (suffix: string) =>
  stub!.calls.find((call) => call.method === 'POST' && call.path.endsWith(suffix))

describe('deposit setup', () => {
  const routes = {
    'POST /api/rent/deposits/config': summary(),
    'POST /api/rent/deposits/slots': summary(),
  }

  it('pre-selects no funding mode and will not save until one is chosen', async () => {
    renderSection(unconfigured, routes)
    const modes = await screen.findAllByRole('radio')
    expect(modes).toHaveLength(2)
    for (const mode of modes) expect(mode).not.toBeChecked()
    expect(screen.queryByRole('combobox', { name: /Primary Signatory/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Save Deposit Setup' }))
    expect(screen.getByText('Choose how the deposit was funded.')).toBeInTheDocument()
    expect(posted('/config')).toBeUndefined()
    expect(posted('/slots')).toBeUndefined()
  })

  it('needs a signatory for the fronted mode, then saves the mode and the slots', async () => {
    renderSection(unconfigured, routes)
    await userEvent.click(await screen.findByRole('radio', { name: /Primary Signatory Fronted/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save Deposit Setup' }))
    expect(screen.getByText('Select the primary lease signatory.')).toBeInTheDocument()
    expect(posted('/config')).toBeUndefined()

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: /Primary Signatory/ }),
      'alice',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Save Deposit Setup' }))
    await waitFor(() => expect(posted('/slots')).toBeDefined())
    expect(posted('/config')!.body).toEqual({
      funding_mode: 'primary_signatory',
      primary_signatory_resident_id: 'alice',
    })
    // The proposed equal split, one slot per resident, in integer paise.
    expect(posted('/slots')!.body).toEqual({
      slots: [
        { slot_number: 1, slot_label: 'Room 1', target_deposit_paise: SHARE, assigned_resident_id: 'alice' },
        { slot_number: 2, slot_label: 'Room 2', target_deposit_paise: SHARE, assigned_resident_id: 'bob' },
        { slot_number: 3, slot_label: 'Room 3', target_deposit_paise: SHARE, assigned_resident_id: 'chris' },
      ],
    })
  })

  it('takes custom labels, amounts and a vacant slot, with no signatory for co-tenants', async () => {
    renderSection(unconfigured, routes)
    const table = await screen.findByRole('table', { name: 'Proposed household deposit slots' })
    for (const header of within(table).getAllByRole('columnheader')) {
      expect(header).toHaveClass('text-center')
    }
    await userEvent.click(screen.getByRole('radio', { name: /Direct Co-Tenants/ }))

    const name = screen.getByLabelText('Name of slot 1')
    await userEvent.clear(name)
    await userEvent.type(name, ' Master bedroom ')
    const first = screen.getByLabelText('Target deposit for slot 1')
    await userEvent.clear(first)
    await userEvent.type(first, '70,000.50')
    const third = screen.getByLabelText('Target deposit for slot 3')
    await userEvent.clear(third)
    await userEvent.type(third, '29999.50')
    await userEvent.selectOptions(screen.getByLabelText('Occupant of slot 3'), '')
    expect(screen.getByRole('status')).toHaveTextContent('the slots add up to the deposit')
    await userEvent.click(screen.getByRole('button', { name: 'Save Deposit Setup' }))

    await waitFor(() => expect(posted('/slots')).toBeDefined())
    expect(posted('/config')!.body).toEqual({
      funding_mode: 'direct_co_tenants',
      primary_signatory_resident_id: null,
    })
    expect(posted('/slots')!.body).toEqual({
      slots: [
        { slot_number: 1, slot_label: 'Master bedroom', target_deposit_paise: 7000050, assigned_resident_id: 'alice' },
        { slot_number: 2, slot_label: 'Room 2', target_deposit_paise: SHARE, assigned_resident_id: 'bob' },
        { slot_number: 3, slot_label: 'Room 3', target_deposit_paise: 2999950, assigned_resident_id: null },
      ],
    })
  })

  it('refuses slots that do not total the deposit, are unnamed, or share an occupant', async () => {
    renderSection(unconfigured, routes)
    await userEvent.click(await screen.findByRole('radio', { name: /Direct Co-Tenants/ }))
    const save = screen.getByRole('button', { name: 'Save Deposit Setup' })
    const first = screen.getByLabelText('Target deposit for slot 1')

    await userEvent.clear(first)
    await userEvent.type(first, '50000.01')
    expect(screen.getByRole('status')).toHaveTextContent(
      '₹0.01 over the ₹1,50,000.00 deposit. The slots must add up to it exactly.',
    )
    await userEvent.click(save)
    expect(posted('/config')).toBeUndefined()

    await userEvent.clear(first)
    await userEvent.type(first, '49999')
    expect(screen.getByRole('status')).toHaveTextContent('₹1.00 short of the ₹1,50,000.00 deposit')
    await userEvent.clear(first)
    await userEvent.type(first, '0')
    expect(screen.getByText('Enter an amount above zero.')).toBeInTheDocument()
    await userEvent.clear(first)
    await userEvent.type(first, '50000')

    await userEvent.clear(screen.getByLabelText('Name of slot 2'))
    expect(screen.getByText('Give the slot a name.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Name of slot 2'), 'Room 2')
    await userEvent.selectOptions(screen.getByLabelText('Occupant of slot 2'), 'alice')
    expect(screen.getAllByText('Already assigned to another slot.')).toHaveLength(2)
    await userEvent.click(save)
    expect(posted('/config')).toBeUndefined()
    expect(posted('/slots')).toBeUndefined()
  })

  it('asks only for the slots once the mode is saved', async () => {
    renderSection(
      { ...unconfigured, funding_mode: 'direct_co_tenants' },
      { 'POST /api/rent/deposits/slots': summary() },
    )
    expect(await screen.findByText('Direct co-tenants')).toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save Deposit Setup' }))
    await waitFor(() => expect(posted('/slots')).toBeDefined())
    expect(posted('/config')).toBeUndefined()
  })

  it('points to Lease Settings when the lease has no deposit amount', async () => {
    renderSection({ ...unconfigured, master_deposit_paise: null, proposal: [] })
    expect(await screen.findByText(/no security deposit amount yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Enter it in Lease Settings.' })).toHaveAttribute(
      'href',
      '/services/rent/config',
    )
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('points to the lease before there is one', async () => {
    renderSection({ ...unconfigured, is_lease_configured: false })
    expect(await screen.findByText('No lease on file yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Set up lease' })).toHaveAttribute(
      'href',
      '/services/rent/config',
    )
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
  })
})

describe('SecurityDepositsPage', () => {
  it('is its own screen under Housing & Utilities', async () => {
    stub = installFetchStub({ routes: { '/api/rent/deposits': summary() } as never })
    render(
      <MemoryRouter>
        <SecurityDepositsPage />
      </MemoryRouter>,
    )
    expect(
      screen.getByRole('heading', { level: 1, name: 'Security Deposit Management' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Capital held in escrow with the landlord/)).toBeInTheDocument()
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByRole('link', { name: 'Housing & Utilities' })).toHaveAttribute(
      'href',
      '/services',
    )
    expect(within(crumbs).getByText('Security Deposits')).toBeInTheDocument()
    expect(await screen.findByRole('table', { name: 'Household deposit slots' })).toBeInTheDocument()
  })
})

describe('the escrow pool', () => {
  it('shows the capital exposure ribbon and centres every table header', async () => {
    renderSection(summary())
    const ribbon = await screen.findByRole('region', { name: 'Capital exposure' })
    expect(ribbon).toHaveTextContent('Master Deposit Fronted to Landlord₹1,50,000.00By Alice')
    expect(ribbon).toHaveTextContent('Total Slot Reimbursements Collected₹1,00,000.00')
    expect(ribbon).toHaveTextContent('Primary Signatory Net Capital at Risk₹50,000.00')
    expect(ribbon).toHaveTextContent('Vacant / Unfunded Gap₹0.00')

    const table = screen.getByRole('table', { name: 'Household deposit slots' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Slot / Space',
      'Target Deposit',
      'Current Occupant',
      'Funded Status',
      'Paid Date & Ref',
      'Actions',
    ])
    for (const header of headers) expect(header).toHaveClass('text-center')

    const bob = within(table).getByRole('row', { name: /Room 2/ })
    expect(bob).toHaveTextContent('Bob')
    expect(bob).toHaveTextContent('Funded')
    expect(bob).toHaveTextContent('2 Oct 2026')
    expect(bob).toHaveTextContent('UPI · UTR428919028471')
  })

  it('offers each slot only the actions that apply to it', async () => {
    renderSection(
      summary({
        vacant_slots_count: 1,
        unfunded_gap_paise: 2 * SHARE,
        unassigned_residents: [{ resident_id: 'dana', display_name: 'Dana' }],
        slots: [
          slot(1, 'alice', 'Alice', fronted),
          slot(2, 'bob', 'Bob'),
          slot(3, 'chris', 'Chris', funded),
          slot(4, null, null),
        ],
      }),
    )
    const table = await screen.findByRole('table', { name: 'Household deposit slots' })
    const actions = (name: RegExp) =>
      within(within(table).getByRole('row', { name }))
        .queryAllByRole('button')
        .map((button) => button.textContent)
    // The signatory fronted their own slot: it is settled with the landlord.
    expect(actions(/Room 1/)).toEqual([])
    // Nothing paid yet: it can be paid, vacated, or handed to someone else.
    expect(actions(/Room 2/)).toEqual(['Record Payment', 'Vacate & Settle', 'Reassign Occupant'])
    // Funded: it has to be settled before anyone else can take it.
    expect(actions(/Room 3/)).toEqual(['Vacate & Settle'])
    expect(actions(/Room 4/)).toEqual(['Reassign Occupant'])
    expect(within(table).getByRole('row', { name: /Room 4/ })).toHaveTextContent('Vacant')
    expect(screen.getByRole('region', { name: 'Capital exposure' })).toHaveTextContent(
      'Vacant / Unfunded Gap₹1,00,000.001 slot vacant',
    )
  })

  it('shows plain totals when flatmates pay the landlord directly, and recent activity', async () => {
    renderSection(
      summary({
        funding_mode: 'direct_co_tenants',
        primary_signatory_id: null,
        primary_signatory_name: null,
        signatory_capital_exposure_paise: null,
        total_funded_paise: SHARE,
        unfunded_gap_paise: 2 * SHARE,
        slots: [
          slot(1, 'alice', 'Alice'),
          slot(2, 'bob', 'Bob', {
            ...funded,
            transactions: [
              {
                tx_id: 'tx-1',
                slot_id: 'slot-2',
                resident_id: 'bob',
                resident_display_name: 'Bob',
                tx_type: 'funding_payment',
                amount_paise: SHARE,
                tx_date: '2026-10-02',
                counterparty_resident_id: null,
                counterparty_display_name: null,
                payment_method: 'upi',
                reference: null,
                notes: null,
              },
            ],
          }),
          slot(3, 'chris', 'Chris'),
        ],
      }),
    )
    const totals = await screen.findByRole('region', { name: 'Deposit totals' })
    expect(totals).toHaveTextContent('Total Funded₹50,000.00Of ₹1,50,000.00 across the slots')
    expect(totals).toHaveTextContent('Slots3 of 3 occupied')
    expect(screen.getByRole('region', { name: 'Recent deposit activity' })).toHaveTextContent(
      '2 Oct 2026 · Room 2 · Paid in · Bob₹50,000.00',
    )
  })

  it('says so when the slots no longer match the deposit on the lease', async () => {
    renderSection(summary({ is_balanced: false, master_deposit_paise: 18000000 }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The slots add up to ₹1,50,000.00, not the ₹1,80,000.00 deposit on the lease.',
    )
  })
})

describe('recording a payment', () => {
  const owing = summary({ slots: [slot(1, 'alice', 'Alice', fronted), slot(2, 'bob', 'Bob')] })

  async function open() {
    renderSection(owing, { 'POST /api/rent/deposits/slots/slot-2/fund': summary() })
    await userEvent.click(
      await screen.findByRole('button', { name: 'Record payment for Room 2 from Bob' }),
    )
    return screen.findByRole('dialog', { name: 'Record Deposit Payment' })
  }

  it('posts the amount in paise to the signatory, with no landlord option', async () => {
    const dialog = await open()
    expect(dialog).toHaveTextContent('Slot 2 (Room 2) · Bob')
    expect(dialog).toHaveTextContent('paid to Alice')
    const amount = within(dialog).getByLabelText(/Amount paid/)
    expect(amount).toHaveValue('50000.00')
    expect(
      within(dialog).queryByRole('option', { name: 'Direct to landlord' }),
    ).not.toBeInTheDocument()

    await userEvent.clear(amount)
    await userEvent.type(amount, '20,000')
    await userEvent.selectOptions(within(dialog).getByLabelText('Payment method'), 'bank_transfer')
    await userEvent.type(within(dialog).getByLabelText('Reference'), ' UTR-1 ')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payment' }))

    await waitFor(() => expect(posted('/fund')).toBeDefined())
    expect(posted('/fund')!.body).toEqual({
      amount_paise: 2000000,
      paid_date: TODAY,
      payment_method: 'bank_transfer',
      payment_reference: 'UTR-1',
      paid_to_resident_id: 'alice',
    })
  })

  it('refuses an amount of zero, more than is owed, or a future date', async () => {
    const dialog = await open()
    const amount = within(dialog).getByLabelText(/Amount paid/)
    const save = within(dialog).getByRole('button', { name: 'Record Payment' })

    await userEvent.clear(amount)
    expect(within(dialog).getByText('Enter the amount paid.')).toBeInTheDocument()
    expect(amount).toHaveAttribute('aria-invalid', 'true')
    expect(save).toBeDisabled()
    await userEvent.type(amount, '0')
    expect(within(dialog).getByText('The amount must be more than zero.')).toBeInTheDocument()
    await userEvent.clear(amount)
    await userEvent.type(amount, '50000.01')
    expect(
      within(dialog).getByText('That is more than the ₹50,000.00 still owed.'),
    ).toBeInTheDocument()
    expect(save).toBeDisabled()

    await userEvent.clear(amount)
    await userEvent.type(amount, '50000')
    expect(save).toBeEnabled()
    const date = within(dialog).getByLabelText(/Paid on/)
    await userEvent.clear(date)
    await userEvent.type(date, '2099-01-01')
    expect(within(dialog).getByText('The date cannot be in the future.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    expect(posted('/fund')).toBeUndefined()
  })
})

describe('vacating and settling a slot', () => {
  const leaving = summary({
    unassigned_residents: [{ resident_id: 'dana', display_name: 'Dana' }],
  })

  async function open(state = leaving) {
    renderSection(state, { 'POST /api/rent/deposits/slots/slot-2/vacate': summary() })
    await userEvent.click(
      await screen.findByRole('button', { name: 'Vacate and settle Room 2' }),
    )
    return screen.findByRole('dialog', { name: 'Vacate & Settle' })
  }

  it('assumes no settlement path and needs one chosen', async () => {
    const dialog = await open()
    for (const path of within(dialog).getAllByRole('radio')) expect(path).not.toBeChecked()
    const save = within(dialog).getByRole('button', { name: 'Confirm Settlement' })
    expect(save).toBeDisabled()
    expect(posted('/vacate')).toBeUndefined()
  })

  it('refunds what was put in less deductions, and needs a reason for them', async () => {
    const dialog = await open()
    await userEvent.click(within(dialog).getByRole('radio', { name: /Refund the leaver/ }))
    expect(dialog).toHaveTextContent('Refunded by Alice')
    const sum = within(dialog).getByLabelText('Settlement')
    expect(sum).toHaveTextContent('Refund to Bob₹50,000.00')
    const save = within(dialog).getByRole('button', { name: 'Confirm Settlement' })
    expect(save).toBeEnabled()

    const deductions = within(dialog).getByLabelText('Deductions')
    await userEvent.type(deductions, '50000.01')
    expect(
      within(dialog).getByText('Deductions cannot be more than the ₹50,000.00 put in.'),
    ).toBeInTheDocument()
    expect(save).toBeDisabled()

    await userEvent.clear(deductions)
    await userEvent.type(deductions, '5,000')
    expect(within(dialog).getByText('Say what the deductions are for.')).toBeInTheDocument()
    expect(save).toBeDisabled()
    expect(sum).toHaveTextContent('Refund to Bob₹45,000.00')

    await userEvent.type(within(dialog).getByLabelText(/Reason for deductions/), 'Repainting')
    await userEvent.click(save)
    await waitFor(() => expect(posted('/vacate')).toBeDefined())
    expect(posted('/vacate')!.body).toEqual({
      settlement_path: 'signatory_refund',
      deductions_paise: 500000,
      deduction_reason: 'Repainting',
      refund_amount_paise: 4500000,
      refund_date: TODAY,
      refund_reference: null,
      incoming_resident_id: null,
    })
  })

  it('transfers the slot to a chosen incoming resident who is not the leaver', async () => {
    const dialog = await open()
    await userEvent.click(within(dialog).getByRole('radio', { name: /Direct transfer/ }))
    const incoming = within(dialog).getByLabelText(/Incoming resident/)
    // Only residents with no slot of their own; never the leaver.
    expect(within(incoming).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Choose a resident',
      'Dana',
    ])
    const save = within(dialog).getByRole('button', { name: 'Confirm Settlement' })
    expect(save).toBeDisabled()

    await userEvent.selectOptions(incoming, 'dana')
    await userEvent.type(within(dialog).getByLabelText('Deductions'), '5000')
    await userEvent.type(within(dialog).getByLabelText(/Reason for deductions/), 'Wardrobe door')
    expect(within(dialog).getByLabelText('Settlement')).toHaveTextContent(
      'Replacement pays Bob₹45,000.00',
    )
    expect(dialog).toHaveTextContent('₹5,000.00 will still be owed on it')
    await userEvent.type(within(dialog).getByLabelText('Reference'), 'UPI-DANA-BOB')
    await userEvent.click(save)

    await waitFor(() => expect(posted('/vacate')).toBeDefined())
    expect(posted('/vacate')!.body).toEqual({
      settlement_path: 'direct_transfer',
      deductions_paise: 500000,
      deduction_reason: 'Wardrobe door',
      refund_amount_paise: 4500000,
      refund_date: TODAY,
      refund_reference: 'UPI-DANA-BOB',
      incoming_resident_id: 'dana',
    })
  })

  it('will not transfer when nothing would be handed over, or nobody can take the slot', async () => {
    const dialog = await open(summary())
    await userEvent.click(within(dialog).getByRole('radio', { name: /Direct transfer/ }))
    expect(dialog).toHaveTextContent('Nobody can take this slot over yet')
    await userEvent.type(within(dialog).getByLabelText('Deductions'), '50000')
    await userEvent.type(within(dialog).getByLabelText(/Reason for deductions/), 'Everything')
    expect(dialog).toHaveTextContent('Nothing would be handed over')
    expect(within(dialog).getByRole('button', { name: 'Confirm Settlement' })).toBeDisabled()
  })
})

describe('reassigning an occupant', () => {
  it('assigns a resident with no slot to a vacant one, keeping its label and target', async () => {
    renderSection(
      summary({
        vacant_slots_count: 1,
        unassigned_residents: [{ resident_id: 'dana', display_name: 'Dana' }],
        slots: [slot(1, 'alice', 'Alice', fronted), slot(2, null, null), slot(3, 'chris', 'Chris', funded)],
      }),
      { 'POST /api/rent/deposits/slots': summary() },
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Reassign occupant of Room 2' }),
    )
    const dialog = await screen.findByRole('dialog', { name: 'Reassign Occupant' })
    expect(dialog).toHaveTextContent('Slot 2 (Room 2) is vacant')

    await userEvent.click(within(dialog).getByRole('button', { name: 'Assign Occupant' }))
    expect(within(dialog).getByText('Choose who is taking the slot.')).toBeInTheDocument()
    expect(posted('/slots')).toBeUndefined()

    await userEvent.selectOptions(within(dialog).getByLabelText(/New occupant/), 'dana')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Assign Occupant' }))
    await waitFor(() => expect(posted('/slots')).toBeDefined())
    expect(posted('/slots')!.body).toEqual({
      slots: [
        { slot_number: 2, slot_label: 'Room 2', target_deposit_paise: SHARE, assigned_resident_id: 'dana' },
      ],
    })
  })
})

describe('ResidentDepositCard', () => {
  function renderCard(state: Record<string, unknown>, residentId: string) {
    stub = installFetchStub({ routes: { '/api/rent/deposits': state } as never })
    return render(
      <MemoryRouter>
        <ResidentDepositCard residentId={residentId} />
      </MemoryRouter>,
    )
  }

  it("shows the resident's slot, what they funded and to whom, and the exit action", async () => {
    renderCard(summary(), 'bob')
    expect(await screen.findByText('Assigned to Household Slot 2 (Room 2)')).toBeInTheDocument()
    const card = screen.getByText('Household Deposit Allocation').closest('section')!
    expect(card).toHaveTextContent('Target Deposit₹50,000.00')
    expect(card).toHaveTextContent('Funded₹50,000.00')
    expect(card).toHaveTextContent('Paid toAlice via UPI (UTR428919028471)')
    expect(within(card).getByRole('button', { name: 'Vacate & Settle Refund' })).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Record Payment' })).not.toBeInTheDocument()
  })

  it('offers no exit for the primary signatory, whose slot is settled with the landlord', async () => {
    renderCard(summary(), 'alice')
    const card = (await screen.findByText('Assigned to Household Slot 1 (Room 1)')).closest(
      'section',
    )!
    expect(card).toHaveTextContent('Paid toThe landlord, as primary signatory')
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
  })

  it('says so when the resident occupies no slot', async () => {
    renderCard(summary(), 'dana')
    expect(
      await screen.findByText(/This resident does not occupy a deposit slot/),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Security Deposits.' })).toHaveAttribute(
      'href',
      '/services/deposits',
    )
  })
})
