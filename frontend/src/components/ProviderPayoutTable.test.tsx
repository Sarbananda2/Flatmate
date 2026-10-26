/**
 * The Provider Payout table rent and furniture share: what each payee is owed,
 * what has been paid them, and the statement a billed row opens.
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  ProviderPayoutStatementModal,
  ProviderPayoutTable,
  type ProviderPayoutItem,
} from './ProviderPayoutTable'

const row = (overrides: Partial<ProviderPayoutItem> = {}): ProviderPayoutItem => ({
  id: 'svc-1',
  name: 'Furlenco',
  totalPaise: 220000,
  paidPaise: 70000,
  stillOwedPaise: 150000,
  status: 'partially_disbursed',
  canPay: true,
  ...overrides,
})

describe('ProviderPayoutTable', () => {
  it('reads column for column as the collection table, with the payee named on the left', () => {
    render(<ProviderPayoutTable caption="Owed" items={[row()]} />)
    const table = screen.getByRole('table', { name: 'Owed' })
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Provider',
      'Total Bill',
      'Paid to Provider',
      'Still Owed',
      'Status',
      'Actions',
    ])
    expect(within(table).getByRole('columnheader', { name: 'Provider' })).toHaveClass('text-left')
    expect(within(table).getByRole('row', { name: /Furlenco/ })).toHaveTextContent(
      'Furlenco₹2,200.00₹700.00₹1,500.00Part paidPay Now',
    )
  })

  it('names its columns for whoever is paid, and shows where the money goes', () => {
    render(
      <ProviderPayoutTable
        caption="Owed"
        columns={{ payee: 'Landlord / Destination', total: 'Total Rent', paid: 'Paid to Owner' }}
        items={[row({ name: 'Rajesh Kumar Sharma', subtitle: 'rajesh@okaxis', payLabel: 'Pay now to landlord' })]}
      />,
    )
    expect(screen.getAllByRole('columnheader').slice(0, 3).map((cell) => cell.textContent)).toEqual([
      'Landlord / Destination',
      'Total Rent',
      'Paid to Owner',
    ])
    expect(screen.getByRole('row', { name: /Rajesh/ })).toHaveTextContent('Rajesh Kumar Sharmarajesh@okaxis')
    expect(screen.getByRole('button', { name: 'Pay now to landlord' })).toBeEnabled()
  })

  it('shows nothing paid or owed before the month is billed, and opens nothing', async () => {
    const pay = vi.fn()
    render(
      <ProviderPayoutTable
        caption="Owed"
        items={[row({ paidPaise: null, stillOwedPaise: null, status: null, canPay: false, onPay: pay })]}
      />,
    )
    const furlenco = screen.getByRole('row', { name: /Furlenco/ })
    expect(furlenco).toHaveTextContent('Furlenco₹2,200.00——Not billed')
    expect(furlenco).not.toHaveAttribute('tabindex')
    expect(screen.getByRole('button', { name: 'Pay now to Furlenco' })).toBeDisabled()
  })

  it('opens the statement from the row, while Pay Now pays', async () => {
    const pay = vi.fn()
    const view = vi.fn()
    render(<ProviderPayoutTable caption="Owed" items={[row({ onPay: pay, onViewStatement: view })]} />)
    await userEvent.click(screen.getByRole('button', { name: 'Pay now to Furlenco' }))
    expect(pay).toHaveBeenCalledOnce()
    expect(view).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('row', { name: /Furlenco/ }))
    expect(view).toHaveBeenCalledOnce()
    screen.getByRole('row', { name: /Furlenco/ }).focus()
    await userEvent.keyboard('{Enter}')
    expect(view).toHaveBeenCalledTimes(2)
  })

  it('marks what is still owed as overdue, and only while something is', () => {
    const { rerender } = render(
      <ProviderPayoutTable caption="Owed" items={[row({ overdueNotice: '3 days overdue' })]} />,
    )
    expect(screen.getByText('3 days overdue')).toBeInTheDocument()
    rerender(
      <ProviderPayoutTable
        caption="Owed"
        items={[row({ stillOwedPaise: 0, status: 'disbursed', overdueNotice: '3 days overdue' })]}
      />,
    )
    expect(screen.queryByText('3 days overdue')).toBeNull()
  })
})

describe('ProviderPayoutStatementModal', () => {
  it("lists every payout to the payee, from the kitty or a resident's advance", () => {
    render(
      <ProviderPayoutStatementModal
        title="Provider Payout Statement"
        name="Furlenco"
        month="2026-11"
        totalPaise={220000}
        paidPaise={220000}
        stillOwedPaise={0}
        status="disbursed"
        disbursements={[
          {
            disbursement_id: 'd1',
            disbursed_at: '2026-11-05',
            amount_paise: 56667,
            disbursed_by: 'pool',
            funding_resident_id: null,
            funding_resident_name: null,
            disbursement_reference: 'UTR-4471',
            note: null,
          },
          {
            disbursement_id: 'd2',
            disbursed_at: '2026-11-05',
            amount_paise: 163333,
            disbursed_by: 'resident_advance',
            funding_resident_id: 'alice',
            funding_resident_name: 'Alice',
            disbursement_reference: 'UTR-4471',
            note: 'Paid from her account',
          },
        ]}
        onClose={() => {}}
      />,
    )
    const dialog = screen.getByRole('dialog', { name: 'Provider Payout Statement' })
    expect(dialog).toHaveTextContent('Furlenco · November 2026')
    expect(dialog).toHaveTextContent('Total Bill₹2,200.00Paid to Provider₹2,200.00Still Owed₹0.00')
    expect(dialog).toHaveTextContent('Disbursement HistoryPaid')
    const payouts = within(dialog).getByRole('table', { name: 'Payments to Furlenco' })
    expect(within(payouts).getAllByRole('row').slice(1).map((tr) => tr.textContent)).toEqual([
      '5 Nov 2026₹566.67House KittyUTR-4471—',
      '5 Nov 2026₹1,633.33Alice (advance)UTR-4471Paid from her account',
    ])
  })

  it('says when nothing has been paid yet', () => {
    render(
      <ProviderPayoutStatementModal
        title="Landlord Payout Statement"
        name="Rajesh Kumar Sharma"
        month="2026-10"
        columns={{ payee: 'Landlord / Destination', total: 'Total Rent', paid: 'Paid to Owner' }}
        totalPaise={4500000}
        paidPaise={0}
        stillOwedPaise={4500000}
        status="pending"
        disbursements={[]}
        onClose={() => {}}
      />,
    )
    const dialog = screen.getByRole('dialog', { name: 'Landlord Payout Statement' })
    expect(dialog).toHaveTextContent('Total Rent₹45,000.00Paid to Owner₹0.00')
    expect(dialog).toHaveTextContent('No payouts recorded yet for this month.')
  })
})
