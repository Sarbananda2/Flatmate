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
import {
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
    paid_to_party: null,
    is_primary_signatory: false,
    transactions: [],
    room_id: null,
    room_number: null,
    has_attached_washroom: null,
    has_balcony: null,
    base_rent_paise: null,
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
  paid_to_party: 'resident',
}

const fronted = {
  ...funded,
  is_primary_signatory: true,
  funded_date: null,
  payment_method: 'direct_to_landlord',
  payment_reference: null,
  paid_to_resident_id: null,
  // The signatory's own share went to the landlord, as the backend names them.
  paid_to_display_name: 'Landlord (Kumar Yadav)',
  paid_to_party: 'landlord',
}

/** The two standing payees, as the summary carries them. */
const LANDLORD = {
  instance_id: '__landlord__',
  party: 'landlord',
  name: 'Kumar Yadav',
  bank_account: '50100123456789',
  bank_ifsc: 'HDFC0001234',
  upi_id: 'kumar.yadav@okhdfcbank',
}
const POOL = {
  instance_id: '__pool__',
  party: 'pool',
  name: 'Household Pool',
  bank_account: null,
  bank_ifsc: null,
  upi_id: null,
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
    primary_signatory_upi_id: null,
    landlord_instance: LANDLORD,
    pool_instance: POOL,
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

/** A read-only details dialog's facts, read back as "label: value". */
const factsIn = (dialog: HTMLElement) =>
  within(dialog)
    .getAllByRole('term')
    .map((term) => `${term.textContent}: ${term.nextElementSibling!.textContent}`)

/** A deposit's row in the Household Escrow Pool table, found by its resident. */
const slotRow = (name: RegExp) =>
  within(screen.getByRole('table', { name: 'Household deposit slots' })).getByRole('row', { name })

/** The Deposit Details dialog read back in its parts: the amounts, then the payment. */
function depositParts(dialog: HTMLElement) {
  const parts = {
    amounts: factsIn(within(dialog).getByLabelText('Deposit amounts')),
    payment: factsIn(within(dialog).getByLabelText('Payment details')),
  }
  // In that order, with no fact outside them.
  expect(factsIn(dialog)).toEqual([...parts.amounts, ...parts.payment])
  return parts
}

/** Opens a deposit's details from its resident button: the resident's name, or Vacant. */
async function openDeposit(name: string) {
  await userEvent.click(await screen.findByRole('button', { name }))
  return screen.getByRole('dialog', { name: 'Deposit Details' })
}

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
    // The whole group is marked invalid, not just captioned.
    expect(screen.getByRole('radiogroup', { name: 'How was the deposit funded?' })).toHaveAttribute(
      'aria-invalid',
      'true',
    )
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

  it('takes the deposit amount here when the lease has none yet', async () => {
    renderSection({ ...unconfigured, master_deposit_paise: null, proposal: [] }, {
      'POST /api/rent/config': {},
    })
    const form = await screen.findByRole('form', { name: 'Security deposit amount' })
    expect(form.parentElement!.closest('form')).toBeNull()
    const save = within(form).getByRole('button', { name: 'Save Deposit' })
    expect(save).toBeDisabled()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await userEvent.type(within(form).getByLabelText(/Security Deposit Held by Landlord/), '0')
    expect(within(form).getByText('The deposit must be more than zero.')).toBeInTheDocument()
    await userEvent.clear(within(form).getByLabelText(/Security Deposit Held by Landlord/))
    await userEvent.type(within(form).getByLabelText(/Security Deposit Held by Landlord/), '1,50,000')
    await userEvent.click(save)
    await waitFor(() => expect(posted('/api/rent/config')).toBeDefined())
    // Only the deposit is sent; the rest of the lease is left as it is.
    expect(posted('/api/rent/config')!.body).toEqual({ security_deposit_paise: 15000000 })
  })

  it('shows the deposit, and edits it in a dialog until slots divide it', async () => {
    renderSection(unconfigured, { 'POST /api/rent/config': {} })
    expect(await screen.findByText(/Deposit held by the landlord:/)).toHaveTextContent('₹1,50,000.00')
    // Shown, not edited, on the page.
    expect(screen.queryByLabelText(/Security Deposit Held by Landlord/)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Edit deposit amount' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Deposit held by the landlord' })).toBeInTheDocument()
    const amount = within(dialog).getByLabelText(/Security Deposit Held by Landlord/)
    expect(amount).toHaveValue('150000.00')
    await userEvent.clear(amount)
    await userEvent.type(amount, '1,60,000')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(posted('/api/rent/config')).toBeDefined())
    expect(posted('/api/rent/config')!.body).toEqual({ security_deposit_paise: 16000000 })
  })

  it('saves only the amount from its dialog, never the setup around it', async () => {
    // The mode is saved and the proposed slots add up, so the setup could save as it stands.
    renderSection({ ...unconfigured, funding_mode: 'direct_co_tenants' }, {
      ...routes,
      'POST /api/rent/config': {},
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Edit deposit amount' }))
    const dialog = screen.getByRole('dialog')
    expect(document.querySelector('form form')).toBeNull()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(posted('/api/rent/config')).toBeDefined())
    expect(posted('/slots')).toBeUndefined()
  })

  it('points to the lease before there is one', async () => {
    renderSection({ ...unconfigured, is_lease_configured: false })
    expect(await screen.findByText('No lease on file yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Set up lease' })).toHaveAttribute(
      'href',
      '/housing?tab=lease',
    )
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
  })
})

describe('the escrow pool', () => {
  it('shows the capital exposure ribbon and centres every table header', async () => {
    renderSection(summary())
    const ribbon = await screen.findByRole('region', { name: 'Capital exposure' })
    expect(ribbon).toHaveTextContent('Master Deposit Fronted₹1,50,000.00By Alice')
    expect(ribbon).toHaveTextContent('Reimbursements Collected₹1,00,000.00')
    expect(ribbon).toHaveTextContent('Signatory Capital at Risk₹50,000.00')
    expect(ribbon).toHaveTextContent('Unfunded Gap₹0.00')
    // Each label is also its own tooltip.
    expect(within(ribbon).getByText('Signatory Capital at Risk')).toHaveAttribute(
      'title',
      'Signatory Capital at Risk',
    )

    const table = screen.getByRole('table', { name: 'Household deposit slots' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Resident',
      'Target Deposit',
      'Funded Status',
      'Paid Date',
      'Actions',
    ])
    for (const header of headers) expect(header).toHaveClass('text-center')

    // One line a cell, the resident first: who, the target, the status, the
    // day it was paid, and what can be done. No room: that is Rooms Inventory's.
    const bob = slotRow(/Bob/)
    const resident = within(within(bob).getByRole('rowheader')).getByRole('button')
    expect(resident).toHaveAccessibleName('Bob')
    expect(resident).toHaveTextContent(/Bob$/)
    const [target, status, paidOn, actions] = within(bob).getAllByRole('cell')
    expect(target).toHaveTextContent(/^₹50,000\.00$/)
    expect(status).toHaveTextContent(/^Funded$/)
    expect(paidOn).toHaveTextContent(/^2 Oct 2026$/)
    expect(actions).toHaveTextContent(/^Vacate$/)
    // How and to whom it was paid is in the deposit's details.
    expect(bob).not.toHaveTextContent('UTR428919028471')
    expect(bob).not.toHaveTextContent('Room 2')
    expect(bob).not.toHaveTextContent('Slot 2')

    const alice = slotRow(/Alice/)
    expect(alice).not.toHaveTextContent('Primary signatory')
    const aliceCells = within(alice).getAllByRole('cell')
    expect(aliceCells[2]).toHaveTextContent(/^—$/)
    // The signatory fronted their own share: nothing to do on the row.
    expect(aliceCells[3]).toHaveTextContent(/^—$/)
    expect(within(aliceCells[3]).queryByRole('button')).toBeNull()
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
    const actionCell = (name: RegExp) => within(slotRow(name)).getAllByRole('cell')[3]
    const actions = (name: RegExp) =>
      within(actionCell(name))
        .queryAllByRole('button')
        .map((button) => button.textContent)
    // Every action shows its icon and a short name, in every row.
    // The signatory fronted their own slot: it is settled with the landlord.
    expect(actions(/Alice/)).toEqual([])
    expect(actionCell(/Alice/)).toHaveTextContent('—')
    // Nothing paid yet: it can be paid, vacated, or handed to someone else.
    expect(actions(/Bob/)).toEqual(['Pay', 'Vacate', 'Reassign'])
    // Funded: it has to be settled before anyone else can take it.
    expect(actions(/Chris/)).toEqual(['Vacate'])
    expect(actions(/Vacant/)).toEqual(['Assign'])
    // The full name is the tooltip.
    const titles = within(actionCell(/Bob/))
      .getAllByRole('button')
      .map((button) => button.getAttribute('title'))
    expect(titles).toEqual(['Record Payment', 'Vacate & Settle', 'Reassign Occupant'])
    expect(slotRow(/Vacant/)).toHaveTextContent('Vacant')
    expect(screen.getByRole('region', { name: 'Capital exposure' })).toHaveTextContent(
      'Unfunded Gap₹1,00,000.001 slot vacant',
    )
    // Each button keeps its name on one line; a slot with many actions wraps
    // onto a second line rather than hiding any names.
    const actionButtons = [/Bob/, /Chris/, /Vacant/].flatMap((who) =>
      within(actionCell(who)).getAllByRole('button'),
    )
    for (const button of actionButtons) {
      expect(button).toHaveClass('whitespace-nowrap')
      expect(button).toHaveAttribute('title')
      expect(button.closest('span')).toHaveClass('flex-wrap')
    }
    for (const header of within(table).getAllByRole('columnheader')) {
      expect(header).toHaveClass('px-2')
    }
  })

  it("opens a deposit's details from its row: the money, and how it was paid", async () => {
    renderSection(
      summary({
        slots: [
          slot(1, 'alice', 'Alice', fronted),
          // Linked to a room with amenities, which the deposit tab leaves to Rooms Inventory.
          slot(2, 'bob', 'Bob', {
            ...funded,
            room_id: 'room-2',
            room_number: 'Room 2',
            has_attached_washroom: true,
            has_balcony: true,
          }),
          slot(3, null, null),
        ],
      }),
    )
    await screen.findByRole('table', { name: 'Household deposit slots' })

    // A click anywhere on the row opens it.
    await userEvent.click(within(slotRow(/Bob/)).getAllByRole('cell')[1])
    let dialog = screen.getByRole('dialog', { name: 'Deposit Details' })
    expect(within(dialog).getByText('Bob', { selector: '.card-hint' })).toBeInTheDocument()
    // The status over the three amounts, then who, whom and how it was paid.
    const amounts = within(dialog).getByLabelText('Deposit amounts')
    expect(within(amounts.parentElement!).getByText('Funded', { selector: 'span' })).toBeInTheDocument()
    expect(depositParts(dialog)).toEqual({
      amounts: ['Target Deposit: ₹50,000.00', 'Funded: ₹50,000.00', 'Outstanding Balance: ₹0.00'],
      payment: [
        'Resident: Bob',
        'Paid To: Alice',
        'Paid Date: 2 Oct 2026',
        'Payment Method: UPI',
        'Reference / UTR: UTR428919028471',
      ],
    })
    expect(within(dialog).getByText('UTR428919028471')).toHaveClass('font-mono')
    // Nothing about the room, and nothing to change: Close is the only action.
    for (const roomFact of ['Room 2', 'Attached Washroom', 'Balcony', 'Occupied Since', 'Slot 2']) {
      expect(dialog).not.toHaveTextContent(roomFact)
    }
    expect(within(dialog).queryByRole('textbox')).toBeNull()
    expect(
      within(dialog)
        .getAllByRole('button')
        .map((button) => button.getAttribute('aria-label') ?? button.textContent),
    ).toEqual(['Close dialog', 'Close'])
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    // From the keyboard: the signatory's own share, fronted to the landlord.
    const aliceSlot = screen.getByRole('button', { name: 'Alice' })
    expect(aliceSlot).toHaveAttribute('aria-haspopup', 'dialog')
    aliceSlot.focus()
    await userEvent.keyboard('{Enter}')
    dialog = screen.getByRole('dialog', { name: 'Deposit Details' })
    expect(within(dialog).getByText('Primary signatory')).toBeInTheDocument()
    expect(depositParts(dialog)).toEqual({
      amounts: ['Target Deposit: ₹50,000.00', 'Funded: ₹50,000.00', 'Outstanding Balance: ₹0.00'],
      payment: [
        'Resident: Alice (Primary signatory)',
        'Paid To: Landlord (Kumar Yadav)',
        'Paid Date: Fronted with master deposit',
        'Payment Method: Direct to landlord',
        'Reference / UTR: Not recorded',
      ],
    })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(aliceSlot).toHaveFocus()

    // A vacant share.
    dialog = await openDeposit('Vacant')
    expect(within(dialog).getByText('Vacant deposit share')).toBeInTheDocument()
    expect(depositParts(dialog)).toEqual({
      amounts: ['Target Deposit: ₹50,000.00', 'Funded: ₹0.00', 'Outstanding Balance: ₹0.00'],
      payment: [
        'Resident: Vacant',
        'Paid To: —',
        'Paid Date: Not paid yet',
        'Payment Method: Not recorded',
        'Reference / UTR: Not recorded',
      ],
    })
  })

  it('names who a co-tenant paid: the landlord, or the household pool', async () => {
    renderSection(
      summary({
        funding_mode: 'direct_co_tenants',
        primary_signatory_id: null,
        primary_signatory_name: null,
        signatory_capital_exposure_paise: null,
        slots: [
          // Recorded the old way: nobody named, so the landlord was paid.
          slot(1, 'alice', 'Alice', {
            ...funded,
            payment_method: 'direct_to_landlord',
            payment_reference: null,
            paid_to_resident_id: null,
            paid_to_display_name: null,
            paid_to_party: null,
          }),
          slot(2, 'bob', 'Bob', {
            ...funded,
            paid_to_resident_id: null,
            paid_to_display_name: 'Household Pool',
            paid_to_party: 'pool',
          }),
        ],
      }),
    )
    let dialog = await openDeposit('Alice')
    expect(factsIn(dialog)).toContain('Paid To: Landlord (Kumar Yadav)')
    // A legacy method is still shown for the rows recorded with it.
    expect(factsIn(dialog)).toContain('Payment Method: Direct to landlord')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    dialog = await openDeposit('Bob')
    expect(factsIn(dialog)).toContain('Paid To: Household Pool')
    expect(factsIn(dialog)).toContain('Payment Method: UPI')
  })

  it("opens a row's action straight away, not the slot's details", async () => {
    renderSection(summary({ slots: [slot(2, 'bob', 'Bob')] }))
    await userEvent.click(await screen.findByRole('button', { name: 'Record payment for Room 2 from Bob' }))
    const [dialog, ...others] = screen.getAllByRole('dialog')
    expect(others).toHaveLength(0)
    expect(dialog).not.toHaveAccessibleName('Deposit Details')
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
    const [row] = activityRows()
    expect(within(row).getByRole('rowheader')).toHaveTextContent(/Bob$/)
    expect(within(row).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'Paid in',
      '2 Oct 2026',
      '₹50,000.00',
    ])
    // Direct co-tenants with nobody else named: Bob paid the landlord.
    await userEvent.click(row)
    const dialog = screen.getByRole('dialog', { name: 'Deposit Transaction Details' })
    expect(factsIn(dialog).slice(0, 2)).toEqual(['From: Bob', 'To: Landlord'])
  })

  it('says so when the slots no longer match the deposit on the lease', async () => {
    renderSection(summary({ is_balanced: false, master_deposit_paise: 18000000 }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The slots add up to ₹1,50,000.00, not the ₹1,80,000.00 deposit on the lease.',
    )
  })
})

describe('recording a payment', () => {
  const owing = summary({
    primary_signatory_upi_id: 'alice@okaxis',
    slots: [slot(1, 'alice', 'Alice', fronted), slot(2, 'bob', 'Bob')],
  })

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
    // Reimbursements go to the signatory: no choice of payee, and their UPI ID to pay.
    expect(within(dialog).queryByLabelText('Paid to')).toBeNull()
    const destination = within(dialog).getByRole('region', { name: 'Payment destination' })
    expect(destination).toHaveTextContent('Transfer toAlice₹50,000.00')
    expect(destination).toHaveTextContent('UPI IDalice@okaxis')
    // How it was sent is a transfer channel; who it went to is not a method.
    expect(
      within(within(dialog).getByLabelText('Payment method'))
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['UPI', 'Bank transfer', 'Cash'])

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
      paid_to_party: 'resident',
      paid_to_resident_id: 'alice',
    })
  })

  it('lets co-tenants pay the landlord or the household pool, and shows where to send it', async () => {
    renderSection(
      summary({
        funding_mode: 'direct_co_tenants',
        primary_signatory_id: null,
        primary_signatory_name: null,
        signatory_capital_exposure_paise: null,
        slots: [slot(2, 'bob', 'Bob')],
      }),
      { 'POST /api/rent/deposits/slots/slot-2/fund': summary() },
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Record payment for Room 2 from Bob' }),
    )
    const dialog = await screen.findByRole('dialog', { name: 'Record Deposit Payment' })
    const paidTo = within(dialog).getByLabelText('Paid to')
    expect(within(paidTo).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Landlord (Kumar Yadav)',
      'Household Pool',
    ])
    expect(paidTo).toHaveValue('landlord')
    expect(within(dialog).queryByRole('option', { name: 'Direct to landlord' })).toBeNull()

    // The landlord's details, from the lease.
    const destination = () => within(dialog).getByRole('region', { name: 'Payment destination' })
    expect(destination()).toHaveTextContent('Transfer toKumar Yadav')
    for (const detail of ['Account50100123456789', 'IFSCHDFC0001234', 'UPI IDkumar.yadav@okhdfcbank']) {
      expect(destination()).toHaveTextContent(detail)
    }

    // The pool has nothing on file yet: it says where to add it.
    await userEvent.selectOptions(paidTo, 'pool')
    expect(destination()).toHaveTextContent('Transfer toHousehold Pool')
    expect(destination()).not.toHaveTextContent('Kumar Yadav')
    expect(within(destination()).getByRole('link', { name: /Add them on Payments/ })).toHaveAttribute(
      'href',
      '/payments',
    )

    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payment' }))
    await waitFor(() => expect(posted('/fund')).toBeDefined())
    expect(posted('/fund')!.body).toEqual({
      amount_paise: SHARE,
      paid_date: TODAY,
      payment_method: 'upi',
      payment_reference: null,
      paid_to_party: 'pool',
      paid_to_resident_id: null,
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
    expect(date).toHaveAttribute('aria-invalid', 'true')
    expect(save).toBeDisabled()
    await userEvent.clear(date)
    expect(within(dialog).getByText('Enter a date.')).toBeInTheDocument()
    await userEvent.type(date, '1999-12-31')
    expect(within(dialog).getByText('That date is too far in the past.')).toBeInTheDocument()
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
    // Pressing confirm without a path points at what is missing instead of sending.
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm Settlement' }))
    expect(within(dialog).getByText('Choose how the slot is being settled.')).toBeInTheDocument()
    expect(
      within(dialog).getByRole('radiogroup', { name: 'How is the slot being settled?' }),
    ).toHaveAttribute('aria-invalid', 'true')
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
      await screen.findByRole('button', { name: 'Assign occupant of Room 2' }),
    )
    const dialog = await screen.findByRole('dialog', { name: 'Assign Occupant' })
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
      '/housing?tab=deposits',
    )
  })
})

describe('rooms', () => {
  it('are left to Rooms Inventory: no room, amenities or room changes on the deposit tab', async () => {
    renderSection(
      summary({
        slots: [
          slot(1, 'alice', 'Alice', {
            slot_label: 'Master Bedroom',
            room_id: 'room-1',
            room_number: 'Room 1',
            has_attached_washroom: true,
            has_balcony: true,
          }),
          slot(2, 'bob', 'Bob'),
        ],
      }),
    )
    const table = await screen.findByRole('table', { name: 'Household deposit slots' })
    for (const roomFact of ['Room 1', 'Master Bedroom', 'Attached Washroom', 'Balcony']) {
      expect(table).not.toHaveTextContent(roomFact)
    }
    const dialog = await openDeposit('Alice')
    expect(dialog).not.toHaveTextContent('Room 1')
    expect(within(dialog).queryByRole('button', { name: /Change Room|Link Room/ })).toBeNull()
    // The room list is never asked for.
    expect(stub!.calls.some((call) => call.path.startsWith('/api/property/rooms'))).toBe(false)
  })
})

/** The card a panel heading names. */
const panel = (title: string) => screen.getByRole('heading', { name: title }).closest('section')!

/** The body rows of the Recent Deposit Activity table, newest first. */
function activityRows() {
  const table = screen.getByRole('table', { name: 'Recent deposit activity' })
  const [, body] = within(table).getAllByRole('rowgroup')
  return within(body).getAllByRole('row')
}

describe('recent deposit activity', () => {
  const tx = (id: string, type: string, amount: number, date: string, extra = {}) => ({
    tx_id: id,
    slot_id: 'slot-2',
    resident_id: 'bob',
    resident_display_name: 'Bob',
    tx_type: type,
    amount_paise: amount,
    tx_date: date,
    counterparty_resident_id: null,
    counterparty_display_name: null,
    payment_method: 'upi',
    reference: null,
    notes: null,
    ...extra,
  })

  it('is a card of its own beside the escrow pool, and leaves out entries that moved no money', async () => {
    renderSection(
      summary({
        slots: [
          slot(2, 'bob', 'Bob', {
            slot_label: 'Back bedroom',
            room_id: 'room-2',
            room_number: 'Room 2',
            transactions: [
              tx('t1', 'funding_payment', SHARE, '2026-10-02'),
              tx('t2', 'move_out_refund', 0, '2026-10-05'),
              tx('t3', 'move_out_refund', 4000000, '2026-10-06'),
            ],
          }),
        ],
      }),
    )
    await screen.findByRole('table', { name: 'Recent deposit activity' })
    const pool = panel('Household Escrow Pool')
    const activity = panel('Recent Deposit Activity')
    // Not a box inside the pool: the two cards sit side by side in one stack.
    expect(pool.contains(activity)).toBe(false)
    expect(activity.parentElement).toBe(pool.parentElement)
    expect(within(pool).queryByRole('table', { name: 'Recent deposit activity' })).toBeNull()
    expect(within(activity).getByText(/Payments, refunds and deductions recorded/)).toBeInTheDocument()
    // The table sits straight in the card, with no second box around it.
    expect(within(activity).getByRole('table').parentElement!.parentElement).toBe(activity)
    const rows = activityRows()
    expect(rows).toHaveLength(2)
    expect(activity).not.toHaveTextContent('₹0.00')
    expect(rows[0]).toHaveTextContent('Refunded on leaving')
    expect(rows[1]).toHaveTextContent('Paid in')
    // No room, linked or named, as in the pool above.
    expect(activity).not.toHaveTextContent('Room 2')
    expect(activity).not.toHaveTextContent('Back bedroom')
  })

  it('is left out until some money has moved', async () => {
    renderSection(
      summary({
        slots: [slot(2, 'bob', 'Bob', { transactions: [tx('t1', 'move_out_refund', 0, '2026-10-05')] })],
      }),
    )
    expect(await screen.findByRole('heading', { name: 'Household Escrow Pool' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Recent Deposit Activity' })).toBeNull()
    expect(screen.queryByRole('table', { name: 'Recent deposit activity' })).toBeNull()
  })

  // Bob's slot, with Alice the primary signatory and Chris taking the slot over.
  const entries = [
    tx('t1', 'funding_payment', SHARE, '2026-10-02', {
      counterparty_resident_id: 'alice',
      counterparty_display_name: 'Alice',
      payment_method: 'bank_transfer',
      reference: 'UTR123',
    }),
    tx('t2', 'move_out_deduction', 500000, '2026-10-08', {
      payment_method: null,
      notes: 'Broken wardrobe hinge,\nrepaired by the landlord',
    }),
    tx('t3', 'move_out_refund', 1500000, '2026-10-08', {
      counterparty_resident_id: 'alice',
      counterparty_display_name: 'Alice',
      payment_method: null,
    }),
    tx('t4', 'direct_replacement_transfer', 3000000, '2026-10-09', {
      counterparty_resident_id: 'chris',
      counterparty_display_name: 'Chris',
      payment_method: null,
      reference: 'NEFT-9',
    }),
  ]
  const renderEntries = () =>
    renderSection(summary({ slots: [slot(2, 'bob', 'Bob', { transactions: entries })] }))

  it('leads each row with the resident under four centred column headers', async () => {
    renderEntries()
    const table = await screen.findByRole('table', { name: 'Recent deposit activity' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Resident',
      'Activity',
      'Date',
      'Amount',
    ])
    for (const header of headers) expect(header).toHaveClass('text-center')

    const rows = activityRows()
    for (const row of rows) {
      // Whose deposit it is heads the row, as the button that opens it.
      const resident = within(row).getByRole('rowheader')
      expect(resident).toHaveTextContent(/Bob$/)
      expect(within(resident).getByRole('button')).toHaveAttribute('aria-haspopup', 'dialog')
    }
    expect(rows.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))).toEqual([
      ['Paid by replacement', '9 Oct 2026', '₹30,000.00'],
      ['Refunded on leaving', '8 Oct 2026', '₹15,000.00'],
      ['Deducted on leaving', '8 Oct 2026', '₹5,000.00'],
      ['Paid in', '2 Oct 2026', '₹50,000.00'],
    ])
    // Who paid whom is in each entry's details, not repeated on every row.
    expect(table).not.toHaveTextContent('Alice')
    expect(table).not.toHaveTextContent('Chris')
  })

  it("shows who paid whom in each entry's details", async () => {
    renderEntries()
    await screen.findByRole('table', { name: 'Recent deposit activity' })
    // Opens an entry from its resident and reads back who paid whom.
    const fromTo = async (activity: string) => {
      await userEvent.click(screen.getByRole('button', { name: new RegExp(`^Bob, ${activity},`) }))
      const dialog = screen.getByRole('dialog', { name: 'Deposit Transaction Details' })
      const facts = factsIn(dialog)
      await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
      return facts.slice(0, 2)
    }
    // Chris, coming in, pays Bob, leaving, directly.
    expect(await fromTo('Paid by replacement')).toEqual(['From: Chris', 'To: Bob'])
    // Alice, who holds the deposit, refunds Bob.
    expect(await fromTo('Refunded on leaving')).toEqual(['From: Alice', 'To: Bob'])
    // Kept from Bob by the signatory, who is named by the summary.
    expect(await fromTo('Deducted on leaving')).toEqual(['From: Bob', 'To: Alice'])
    // Bob reimburses Alice.
    expect(await fromTo('Paid in')).toEqual(['From: Bob', 'To: Alice'])
  })

  it('opens a row in a read-only dialog, and Close dismisses it', async () => {
    renderEntries()
    await screen.findByRole('table', { name: 'Recent deposit activity' })
    const rowCount = activityRows().length

    // A click anywhere on the row opens it.
    await userEvent.click(within(activityRows()[3]).getAllByRole('cell')[2])
    let dialog = screen.getByRole('dialog', { name: 'Deposit Transaction Details' })
    expect(within(dialog).getByText('Bob · 2 Oct 2026')).toBeInTheDocument()
    expect(within(dialog).getByText('Paid in')).toBeInTheDocument()
    expect(within(dialog).getAllByText('₹50,000.00')).toHaveLength(1)
    expect(factsIn(dialog)).toEqual([
      'From: Bob',
      'To: Alice',
      'Date: 2 Oct 2026',
      'Payment Method: Bank transfer',
      'Reference / UTR: UTR123',
      'Notes / Reason: None',
    ])
    expect(within(dialog).getByText('UTR123')).toHaveClass('font-mono')
    // Read-only: nothing to type in, and the only action is to close.
    expect(within(dialog).queryByRole('textbox')).toBeNull()
    expect(
      within(dialog)
        .getAllByRole('button')
        .map((button) => button.getAttribute('aria-label') ?? button.textContent),
    ).toEqual(['Close dialog', 'Close'])
    // The table does not grow a row to show it.
    expect(activityRows()).toHaveLength(rowCount)

    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    // From the keyboard, on the resident: a deduction, kept from the deposit, the note in full.
    const deduction = screen.getByRole('button', { name: /^Bob, Deducted on leaving,/ })
    deduction.focus()
    await userEvent.keyboard('{Enter}')
    dialog = screen.getByRole('dialog', { name: 'Deposit Transaction Details' })
    expect(factsIn(dialog)).toEqual([
      'From: Bob',
      'To: Alice',
      'Date: 8 Oct 2026',
      'Payment Method: Retained from deposit',
      'Reference / UTR: Not recorded',
      'Notes / Reason: Broken wardrobe hinge,\nrepaired by the landlord',
    ])
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    // Focus goes back to the row it came from.
    expect(deduction).toHaveFocus()
  })
})
