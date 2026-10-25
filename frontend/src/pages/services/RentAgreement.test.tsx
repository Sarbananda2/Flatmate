/**
 * The rental agreement card: read-only by default, saying which model the
 * household uses and how the month's shares follow from it.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AgreementModelPicker, RentAgreementCard } from './RentAgreement'
import { ToastProvider } from '../../components/Toast'
import { installFetchStub, type Recorded } from '../../test/server'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const config = {
  requires_landlord_receipt: true,
  rent_split_mode: 'equal',
} as never

describe('AgreementModelPicker', () => {
  it('lets only the built models be chosen', async () => {
    const onChange = vi.fn()
    render(<AgreementModelPicker value="equal" onChange={onChange} roomTieredAvailable={false} />)
    const group = screen.getByRole('radiogroup', { name: 'Rental agreement structure' })
    const models = within(group).getAllByRole('radio')
    expect(models).toHaveLength(5)
    expect(within(group).getByRole('radio', { name: 'Single Master Lease (Equal Split)' })).toBeChecked()
    expect(within(group).getByText('Needs Room Base Rents')).toBeInTheDocument()
    expect(within(group).getAllByText('Under Development')).toHaveLength(3)
    for (const model of models.slice(2)) expect(model).toBeDisabled()
    await userEvent.click(within(group).getByRole('radio', { name: 'Room-Tiered Fixed Pricing' }))
    expect(onChange).toHaveBeenCalledWith('room_tiered')
  })
})

describe('RentAgreementCard', () => {
  it('says when the month is charged with another split than the model', () => {
    stub = installFetchStub({ routes: {} })
    render(
      <ToastProvider>
        <RentAgreementCard
          config={{ ...(config as object), rent_split_mode: 'room_tiered' } as never}
          cycle={{ split_mode: 'equal', participant_count: 3, base_share_paise: 950000 } as never}
          preview={null}
          month="2026-10"
          onSaved={() => {}}
        />
      </ToastProvider>,
    )
    const agreement = screen.getByLabelText('Rental agreement')
    expect(agreement).toHaveTextContent('Room-Tiered Fixed Pricing')
    expect(agreement).toHaveTextContent(
      'October 2026 is charged with Single Master Lease (Equal Split).',
    )
    // The month's own split decides its share line.
    expect(agreement).toHaveTextContent('Equal share among 3 participants (₹9,500.00/resident)')
    // Receipts are not tracked, even where the lease once said the landlord issues them.
    expect(agreement).not.toHaveTextContent(/receipt/i)
  })
})
