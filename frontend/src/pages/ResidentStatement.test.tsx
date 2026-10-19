/**
 * A flatmate's monthly statement and how it is shared.
 *
 * The figures are the backend's, so these tests pin what the screen adds: the
 * right month is asked for, the message that is copied is exactly the one the
 * backend wrote, the QR code carries the backend's payment link, and nobody is
 * shown a code to pay something that is already paid.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FlatmateSharesPanel, MonthlyStatementCard, StatementSlip } from './ResidentStatement'
import { ToastProvider } from '../components/Toast'
import { installFetchStub, type Recorded } from '../test/server'
import type { ResidentMonthlyStatement } from '../api/types'

const bob: ResidentMonthlyStatement = {
  resident_id: 'bob',
  display_name: 'Bob',
  is_active: true,
  room_label: 'Room 2',
  apartment_name: 'Flat 402',
  settlement_month: '2026-10',
  statement_id: 'stmt-2026-10',
  statement_status: 'draft',
  items: [
    {
      expense_id: 'rent-2026-10',
      category: 'Rent',
      description: 'Rent for October 2026',
      expense_date: '2026-10-01',
      service_period: '2026-10',
      allocation_method: 'equal',
      total_expense_paise: 4500000,
      resident_share_paise: 1500000,
      notes: null,
      components: [],
    },
    {
      expense_id: 'electricity-2026-10',
      category: 'Electricity',
      description: 'September electricity bill',
      expense_date: '2026-10-08',
      service_period: '2026-09',
      allocation_method: 'measured',
      total_expense_paise: 600000,
      resident_share_paise: 145000,
      notes: null,
      components: [
        { name: 'Energy charges', allocation_method: 'measured', total_paise: 510000, resident_share_paise: 115000 },
        { name: 'Fixed charges', allocation_method: 'equal', total_paise: 90000, resident_share_paise: 30000 },
      ],
    },
  ],
  total_charges_paise: 1645000,
  payments: [
    {
      payment_id: 'pay-bob-oct',
      payment_date: '2026-10-05',
      payment_kind: 'to_pool',
      payment_mode: 'upi',
      amount_paise: 1300000,
      reference: 'UPI/4289',
      description: 'Payment into the household pool',
    },
  ],
  total_payments_paise: 1300000,
  brought_forward_paise: 0,
  net_due_paise: 345000,
  direction: 'owed_to_pool',
  payable_now_paise: 345000,
  recipient_upi_id: 'alice@okaxis',
  recipient_name: 'Alice',
  upi_uri: 'upi://pay?pa=alice@okaxis&pn=Alice&am=3450.00&cu=INR&tn=October%202026%20Flat%20402',
  whatsapp_text: '🏠 Flat 402 — October 2026 Statement\n👤 Resident: Bob (Room 2)\n👉 Net Due: ₹3,450.00 (Owed to Pool)',
}

const chris: ResidentMonthlyStatement = {
  ...bob,
  resident_id: 'chris',
  display_name: 'Chris',
  room_label: null,
  payments: [],
  total_payments_paise: 1700000,
  brought_forward_paise: -20000,
  net_due_paise: -75000,
  direction: 'owed_to_resident',
  payable_now_paise: 0,
  upi_uri: null,
  whatsapp_text: '🏠 Flat 402 — October 2026 Statement\n👤 Resident: Chris',
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function clipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  return writeText
}

const show = (ui: React.ReactElement) =>
  render(
    <MemoryRouter>
      <ToastProvider>{ui}</ToastProvider>
    </MemoryRouter>,
  )

describe('StatementSlip', () => {
  it('itemizes the charges, the credits and the net due', () => {
    show(<StatementSlip statement={bob} />)
    expect(screen.getByTestId('net-due-badge')).toHaveTextContent('₹3,450.00 Owed to Pool')

    const charges = screen.getByRole('table', { name: 'Itemized charges' })
    const rent = within(charges).getByRole('row', { name: /Rent/ })
    expect(rent).toHaveTextContent('Oct 2026')
    expect(rent).toHaveTextContent('Equal shares')
    expect(rent).toHaveTextContent('₹45,000.00')
    expect(rent).toHaveTextContent('₹15,000.00')
    const power = within(charges).getByRole('row', { name: /Electricity/ })
    expect(power).toHaveTextContent('Sep 2026')
    expect(power).toHaveTextContent('Energy charges ₹1,150.00 + Fixed charges ₹300.00')

    const credits = screen.getByRole('table', { name: 'Payments credited' })
    expect(within(credits).getByRole('row', { name: /Into the pool/ })).toHaveTextContent(
      'UPI/4289',
    )
    const figures = screen.getByLabelText('Statement figures')
    expect(figures).toHaveTextContent('Total charges₹16,450.00')
    expect(figures).toHaveTextContent('Net due to the pool₹3,450.00')
    expect(screen.getByText(/Draft — these figures can still change/)).toBeInTheDocument()
  })

  it('centres every table header', () => {
    show(<StatementSlip statement={bob} />)
    const headers = screen.getAllByRole('columnheader')
    expect(headers.length).toBe(9)
    for (const header of headers) expect(header).toHaveClass('text-center')
  })

  it('copies exactly the message the backend wrote, and says so', async () => {
    const writeText = clipboard()
    show(<StatementSlip statement={bob} />)
    await userEvent.click(screen.getByRole('button', { name: /Copy WhatsApp Breakdown/ }))
    expect(writeText).toHaveBeenCalledWith(bob.whatsapp_text)
    expect(await screen.findByText('WhatsApp summary copied for Bob')).toBeInTheDocument()
  })

  it('reveals a UPI code carrying the backend payment link', async () => {
    show(<StatementSlip statement={bob} />)
    expect(screen.queryByLabelText('Scan to pay')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Scan to Pay UPI' }))
    const section = screen.getByLabelText('Scan to pay')
    expect(
      within(section).getByRole('img', { name: 'UPI QR code to pay Alice ₹3,450.00' }),
    ).toBeInTheDocument()
    expect(section).toHaveTextContent('alice@okaxis')
    await userEvent.click(screen.getByRole('button', { name: 'Hide UPI Code' }))
    expect(screen.queryByLabelText('Scan to pay')).not.toBeInTheDocument()
  })

  it('shows no code when there is nothing to pay', async () => {
    show(<StatementSlip statement={chris} />)
    expect(screen.getByTestId('net-due-badge')).toHaveTextContent('₹750.00 Owed to Chris')
    expect(screen.getByLabelText('Statement figures')).toHaveTextContent(
      'Brought forward (credit)−₹200.00',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Scan to Pay UPI' }))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText(/there is nothing for them to pay/)).toBeInTheDocument()
  })

  it('says when a past statement has been paid since', () => {
    show(
      <StatementSlip
        statement={{ ...bob, statement_status: 'closed', payable_now_paise: 0, upi_uri: null }}
      />,
    )
    expect(screen.getByText('Paid in full since this statement.')).toBeInTheDocument()
    expect(screen.queryByText(/Draft — these figures/)).not.toBeInTheDocument()
  })

  it('prints the statement', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => {})
    show(<StatementSlip statement={bob} />)
    await userEvent.click(screen.getByRole('button', { name: 'Print / Save PDF' }))
    expect(print).toHaveBeenCalledOnce()
    print.mockRestore()
  })
})

describe('MonthlyStatementCard', () => {
  it('starts on the given month and steps to the neighbouring ones', async () => {
    stub = installFetchStub({
      routes: {
        '/api/residents/bob/statements/2026-10': bob,
        '/api/residents/bob/statements/2026-09': {
          ...bob,
          settlement_month: '2026-09',
          items: [],
          payments: [],
          total_charges_paise: 0,
          total_payments_paise: 0,
          net_due_paise: 0,
          direction: 'settled',
          payable_now_paise: 0,
        },
      } as never,
    })
    show(<MonthlyStatementCard residentId="bob" initialMonth="2026-10" />)
    expect(await screen.findByTestId('net-due-badge')).toHaveTextContent('Owed to Pool')
    const switcher = screen.getByRole('group', { name: 'Statement month' })
    expect(switcher).toHaveTextContent('October 2026')

    await userEvent.click(within(switcher).getByRole('button', { name: 'Previous month' }))
    expect(switcher).toHaveTextContent('September 2026')
    await waitFor(() => expect(screen.getByTestId('net-due-badge')).toHaveTextContent('Settled'))
    expect(screen.getByText('No charges settle in September 2026.')).toBeInTheDocument()

    await userEvent.click(within(switcher).getByRole('button', { name: 'Next month' }))
    await waitFor(() =>
      expect(screen.getByTestId('net-due-badge')).toHaveTextContent('Owed to Pool'),
    )
    expect(stub.calls.map((call) => call.path)).toEqual([
      '/api/residents/bob/statements/2026-10',
      '/api/residents/bob/statements/2026-09',
      '/api/residents/bob/statements/2026-10',
    ])
  })
})

describe('FlatmateSharesPanel', () => {
  function open() {
    stub = installFetchStub({
      routes: {
        '/api/statements/2026-10/flatmates': {
          settlement_month: '2026-10',
          statement_status: 'draft',
          items: [bob, chris],
          totals: {
            total_charges_paise: 3290000,
            total_payments_paise: 3000000,
            brought_forward_paise: -20000,
            net_due_paise: 270000,
          },
        },
      } as never,
    })
    show(<FlatmateSharesPanel month="2026-10" />)
  }

  it('lists each flatmate with their share, credits and net due', async () => {
    open()
    const table = await screen.findByRole('table', { name: 'Flatmate shares for October 2026' })
    expect(
      within(table).getAllByRole('columnheader').map((header) => header.textContent),
    ).toEqual(['Resident', 'Month Charges', 'Paid / Credited', 'Net Due', 'Status', 'Actions'])
    for (const header of within(table).getAllByRole('columnheader')) {
      expect(header).toHaveClass('text-center')
    }
    const bobRow = within(table).getByRole('row', { name: /Bob/ })
    expect(bobRow).toHaveTextContent('Room 2')
    expect(bobRow).toHaveTextContent('₹16,450.00')
    expect(bobRow).toHaveTextContent('₹13,000.00')
    expect(bobRow).toHaveTextContent('₹3,450.00')
    expect(bobRow).toHaveTextContent('Owes the pool')
    // The name leads to Bob's full statement and ledger.
    expect(within(bobRow).getByRole('link', { name: 'Bob' })).toHaveAttribute(
      'href',
      '/statements/residents/bob',
    )
    const chrisRow = within(table).getByRole('row', { name: /Chris/ })
    expect(chrisRow).toHaveTextContent('₹750.00')
    expect(chrisRow).toHaveTextContent('Owed by the pool')
    expect(chrisRow).toHaveTextContent('₹200.00 credit brought forward')
  })

  it('copies the right flatmate’s message straight from the row', async () => {
    const writeText = clipboard()
    open()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Copy WhatsApp for Chris' }),
    )
    expect(writeText).toHaveBeenCalledWith(chris.whatsapp_text)
    expect(await screen.findByText('WhatsApp summary copied for Chris')).toBeInTheDocument()
  })

  it('opens a flatmate’s full statement in a dialog', async () => {
    open()
    await userEvent.click(
      await screen.findByRole('button', { name: 'View statement for Bob' }),
    )
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Bob — October 2026')
    expect(within(dialog).getByTestId('net-due-badge')).toHaveTextContent('₹3,450.00 Owed to Pool')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
