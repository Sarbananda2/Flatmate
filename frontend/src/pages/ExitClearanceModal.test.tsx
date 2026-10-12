/**
 * The guided move-out clearance and its slip.
 *
 * The wizard sends one request at the very end, so the tests pin that body --
 * integer paise, the chosen path, the offset choice -- and the rules that stop
 * a step being left with something wrong on it: valid dates, a reason for any
 * deduction, and a settlement path that is chosen, never assumed.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ClearanceSlip, ClearanceSlipPanel, ExitClearanceModal } from './ExitClearanceModal'
import { installFetchStub, type Recorded } from '../test/server'
import { todayIso } from '../lib/format'
import type { ExitClearance } from '../api/types'

const TODAY = todayIso()

const preview = {
  resident_id: 'bob',
  display_name: 'Bob',
  is_active: true,
  slot: {
    slot_id: 'slot-2',
    slot_number: 2,
    slot_label: 'Room 2',
    target_deposit_paise: 5000000,
    funded_amount_paise: 5000000,
  },
  funding_mode: 'primary_signatory',
  primary_signatory_id: 'alice',
  primary_signatory_name: 'Alice',
  ledger_outstanding_charges_paise: 240000,
  ledger_unapplied_credits_paise: 0,
  operating_net_balance_paise: -240000,
  default_move_out_date: TODAY,
  incoming_candidates: [{ resident_id: 'dana', display_name: 'Dana' }],
  allowed_settlement_paths: ['signatory_refund', 'direct_replacement_transfer'],
  blocking_reason: null,
}

const slip: ExitClearance = {
  clearance_id: 'clearance-1',
  resident_id: 'bob',
  resident_display_name: 'Bob',
  apartment_name: 'Flat 402',
  move_out_date: '2026-10-12',
  slot_id: 'slot-2',
  slot_number: 2,
  slot_label: 'Room 2',
  deposit_funded_paise: 5000000,
  deposit_deductions_paise: 500000,
  deduction_reason: 'Painting',
  deposit_refund_paise: 4500000,
  ledger_outstanding_paise: 240000,
  ledger_credit_paise: 0,
  net_ledger_offset_applied: true,
  ledger_offset_paise: -240000,
  net_settlement_paise: 4260000,
  settlement_direction: 'refund_to_resident',
  settlement_path: 'signatory_refund',
  settlement_date: '2026-10-12',
  settlement_reference: 'UTR428919028471',
  counterparty_resident_id: 'alice',
  counterparty_display_name: 'Alice',
  clearing_payment_id: 'payment-1',
  notes: null,
  payee_resident_id: 'bob',
  payee_display_name: 'Bob',
  payee_upi_id: 'bob@okaxis',
  upi_uri: 'upi://pay?pa=bob@okaxis&pn=Bob&am=42600.00&cu=INR',
  summary_text: '🏠 Flat 402 — Move-Out Clearance Statement\nResident: Bob (Room 2)',
  created_at: '2026-10-12T10:00:00+05:30',
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function open(state: Record<string, unknown> = preview, onCleared = vi.fn()) {
  stub = installFetchStub({
    routes: {
      '/api/residents/bob/clearance/preview': state,
      'POST /api/residents/bob/clearance': { ...slip },
    } as never,
  })
  render(
    <ExitClearanceModal
      residentId="bob"
      residentName="Bob"
      onClose={() => {}}
      onCleared={onCleared}
    />,
  )
  return onCleared
}

const posted = () =>
  stub!.calls.find((call) => call.method === 'POST' && call.path === '/api/residents/bob/clearance')
const next = () => userEvent.click(screen.getByRole('button', { name: 'Next' }))
const dialog = () => screen.getByRole('dialog')

describe('ExitClearanceModal', () => {
  it('walks the four steps and posts one settlement with integer paise', async () => {
    const onCleared = open()
    expect(await screen.findByLabelText(/Move-Out Date/)).toHaveValue(TODAY)
    expect(dialog()).toHaveTextContent('Bob · step 1 of 4: Handover')
    await userEvent.type(screen.getByLabelText(/Departure reason/), ' Moving to Pune ')
    await next()

    // Step 2: the slot, deductions, and a path that is not pre-selected.
    expect(dialog()).toHaveTextContent('Room 2 · Funded: ₹50,000.00')
    for (const path of screen.getAllByRole('radio')) expect(path).not.toBeChecked()
    await userEvent.type(screen.getByLabelText('Physical Deductions'), '5,000')
    await userEvent.type(screen.getByLabelText(/Deduction Reason/), 'Painting')
    await userEvent.click(screen.getByRole('radio', { name: /Signatory \/ Pool Refund/ }))
    await next()

    // Step 3: the live ledger balance, with the offset on by default.
    const ledger = screen.getByLabelText('Operating ledger')
    expect(ledger).toHaveTextContent('Unpaid charges₹2,400.00')
    expect(ledger).toHaveTextContent('Operating balance₹2,400.00 owed by them')
    expect(
      screen.getByRole('checkbox', { name: /Offset operating dues directly against deposit refund/ }),
    ).toBeChecked()
    await next()

    // Step 4: the worked example from the specification.
    const summary = screen.getByLabelText('Settlement summary')
    expect(summary).toHaveTextContent('Contributed Deposit+₹50,000.00')
    expect(summary).toHaveTextContent(/Room Deductions[-−]₹5,000.00/)
    expect(summary).toHaveTextContent(/Operating Ledger Offset[-−]₹2,400.00/)
    expect(summary).toHaveTextContent('Net Payout to Bob₹42,600.00')
    expect(posted()).toBeUndefined()
    await userEvent.type(screen.getByLabelText('Settlement UTR / reference'), 'UTR428919028471')
    await userEvent.click(
      screen.getByRole('button', { name: 'Confirm Move-Out & Settle Clearance' }),
    )

    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toEqual({
      move_out_date: TODAY,
      deductions_paise: 500000,
      deduction_reason: 'Painting',
      net_ledger_offset: true,
      settlement_path: 'signatory_refund',
      incoming_resident_id: null,
      settlement_date: TODAY,
      settlement_reference: 'UTR428919028471',
      notes: 'Moving to Pune',
    })
    // The slip replaces the wizard, and the page behind is told.
    expect(await screen.findByRole('dialog', { name: 'Clearance Complete' })).toBeInTheDocument()
    expect(onCleared).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: /Copy WhatsApp Summary/ })).toBeInTheDocument()
  })

  it('will not leave a step that has something wrong on it', async () => {
    open()
    const date = await screen.findByLabelText(/Move-Out Date/)
    await userEvent.clear(date)
    await next()
    expect(screen.getByText('Enter a date.')).toBeInTheDocument()
    expect(date).toHaveAttribute('aria-invalid', 'true')
    await userEvent.type(date, '2099-01-01')
    expect(screen.getByText('The date cannot be in the future.')).toBeInTheDocument()
    await next()
    expect(dialog()).toHaveTextContent('step 1 of 4')
    await userEvent.clear(date)
    await userEvent.type(date, TODAY)
    await next()

    // No path chosen: pointed out on Next, not sent.
    await next()
    expect(screen.getByText('Choose how the deposit is being settled.')).toBeInTheDocument()
    expect(
      screen.getByRole('radiogroup', { name: 'How is the deposit being settled?' }),
    ).toHaveAttribute('aria-invalid', 'true')
    expect(dialog()).toHaveTextContent('step 2 of 4')

    const deductions = screen.getByLabelText('Physical Deductions')
    await userEvent.type(deductions, '50000.01')
    expect(
      screen.getByText('Deductions cannot be more than the ₹50,000.00 put in.'),
    ).toBeInTheDocument()
    await userEvent.clear(deductions)
    await userEvent.type(deductions, '5000')
    await userEvent.click(screen.getByRole('radio', { name: /Signatory \/ Pool Refund/ }))
    await next()
    expect(screen.getByText('Say what the deductions are for.')).toBeInTheDocument()
    expect(dialog()).toHaveTextContent('step 2 of 4')
    expect(posted()).toBeUndefined()
  })

  it('needs a replacement for a direct transfer, and sends the dues without the offset', async () => {
    open()
    await screen.findByLabelText(/Move-Out Date/)
    await next()
    await userEvent.click(screen.getByRole('radio', { name: /Direct Replacement Transfer/ }))
    await next()
    expect(screen.getByText('Choose the resident taking the slot over.')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText(/Replacement resident/), 'dana')
    await next()

    await userEvent.click(screen.getByRole('checkbox', { name: /Offset operating dues/ }))
    expect(dialog()).toHaveTextContent('Their operating balance stays open')
    await next()
    const summary = screen.getByLabelText('Settlement summary')
    expect(summary).not.toHaveTextContent('Operating Ledger Offset')
    expect(summary).toHaveTextContent('Net Payout to Bob₹50,000.00')
    expect(dialog()).toHaveTextContent('₹2,400.00 is not included and stays open on the ledger')

    // Back keeps what was entered.
    await userEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByRole('checkbox', { name: /Offset operating dues/ })).not.toBeChecked()
    await next()
    await userEvent.click(
      screen.getByRole('button', { name: 'Confirm Move-Out & Settle Clearance' }),
    )
    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      settlement_path: 'direct_replacement_transfer',
      incoming_resident_id: 'dana',
      net_ledger_offset: false,
      deductions_paise: 0,
      deduction_reason: null,
    })
  })

  it('shows what the leaver owes when their dues exceed their deposit', async () => {
    open({
      ...preview,
      slot: { ...preview.slot, funded_amount_paise: 0 },
      operating_net_balance_paise: -240000,
    })
    await screen.findByLabelText(/Move-Out Date/)
    await next()
    await userEvent.click(screen.getByRole('radio', { name: /Signatory \/ Pool Refund/ }))
    await next()
    await next()
    expect(screen.getByLabelText('Settlement summary')).toHaveTextContent(
      'Net Payable by Bob₹2,400.00',
    )
  })

  it('runs a standalone clearance for a resident with no deposit slot', async () => {
    open({
      ...preview,
      slot: null,
      allowed_settlement_paths: ['standalone_clearance'],
      operating_net_balance_paise: 100000,
      ledger_outstanding_charges_paise: 0,
      ledger_unapplied_credits_paise: 100000,
    })
    await screen.findByLabelText(/Move-Out Date/)
    await next()
    expect(dialog()).toHaveTextContent('Bob holds no deposit slot')
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    await next()
    expect(screen.getByLabelText('Operating ledger')).toHaveTextContent('₹1,000.00 owed to them')
    await next()
    const summary = screen.getByLabelText('Settlement summary')
    expect(summary).not.toHaveTextContent('Contributed Deposit')
    expect(summary).toHaveTextContent('Net Payout to Bob₹1,000.00')
    await userEvent.click(
      screen.getByRole('button', { name: 'Confirm Move-Out & Settle Clearance' }),
    )
    await waitFor(() => expect(posted()).toBeDefined())
    expect(posted()!.body).toMatchObject({
      settlement_path: 'standalone_clearance',
      deductions_paise: 0,
      incoming_resident_id: null,
    })
  })

  it('explains why a resident cannot be cleared instead of starting the wizard', async () => {
    open({
      ...preview,
      blocking_reason:
        "The primary signatory's own deposit slot is settled with the landlord, so they cannot be cleared through this workflow.",
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('primary signatory')
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })
})

describe('ClearanceSlip', () => {
  it('shows the figures, a QR code for the net amount, and the share actions', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const print = vi.spyOn(window, 'print').mockImplementation(() => {})
    render(<ClearanceSlip clearance={slip} />)

    const figures = screen.getByLabelText('Clearance figures')
    expect(figures).toHaveTextContent('Contributed Deposit+₹50,000.00')
    expect(figures).toHaveTextContent(/Deductions \(Painting\)[-−]₹5,000.00/)
    expect(figures).toHaveTextContent(/Operating Ledger Offset[-−]₹2,400.00/)
    expect(figures).toHaveTextContent('Payable to Bob₹42,600.00')
    expect(screen.getByText(/UTR UTR428919028471/)).toBeInTheDocument()
    expect(screen.getByText(/settled with Alice/)).toBeInTheDocument()

    const pay = screen.getByRole('region', { name: 'Scan to pay' })
    expect(
      within(pay).getByRole('img', { name: 'UPI QR code to pay Bob ₹42,600.00' }),
    ).toBeInTheDocument()
    expect(pay).toHaveTextContent('bob@okaxis')

    await userEvent.click(screen.getByRole('button', { name: /Copy WhatsApp Summary/ }))
    expect(writeText).toHaveBeenCalledWith(slip.summary_text)
    await userEvent.click(screen.getByRole('button', { name: 'Print / Save as PDF' }))
    expect(print).toHaveBeenCalledOnce()
    print.mockRestore()
  })

  it('says why there is no QR code, and shows a ledger balance that was left out', () => {
    render(
      <ClearanceSlip
        clearance={{
          ...slip,
          net_ledger_offset_applied: false,
          ledger_offset_paise: 0,
          net_settlement_paise: 4500000,
          upi_uri: null,
          payee_upi_id: null,
        }}
      />,
    )
    expect(screen.getByText(/Bob has no UPI ID on file/)).toBeInTheDocument()
    expect(screen.getByLabelText('Clearance figures')).toHaveTextContent(
      /Operating ledger \(not included\)[-−]₹2,400.00/,
    )
    expect(screen.queryByRole('region', { name: 'Scan to pay' })).not.toBeInTheDocument()
  })
})

describe('ClearanceSlipPanel', () => {
  it('shows the slip for a resident who has been cleared, and nothing otherwise', async () => {
    stub = installFetchStub({
      routes: { '/api/residents/bob/clearance': { clearance: { ...slip } } } as never,
    })
    const { unmount } = render(<ClearanceSlipPanel residentId="bob" />)
    expect(await screen.findByText('Clearance Slip')).toBeInTheDocument()
    expect(screen.getByLabelText('Clearance figures')).toBeInTheDocument()
    unmount()
    stub.restore()

    stub = installFetchStub({
      routes: { '/api/residents/chris/clearance': { clearance: null } } as never,
    })
    const { container } = render(<ClearanceSlipPanel residentId="chris" />)
    await waitFor(() => expect(stub!.calls).toHaveLength(1))
    expect(container).toBeEmptyDOMElement()
  })
})
