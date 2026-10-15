/**
 * Setup's saved settings open read-only; inputs appear only in the Edit
 * Settings dialog, which saves just what changed.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToastProvider } from '../components/Toast'
import { SettingsCard } from './Setup'
import { installFetchStub, type Recorded } from '../test/server'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const apartment = {
  apartment_id: 'flat',
  name: 'Flat 1805, Supertech Capetown',
  currency_code: 'INR',
  timezone: 'Asia/Kolkata',
  created_at: '2026-10-01T10:00:00+05:30',
}

function open(onSaved = () => {}) {
  stub = installFetchStub({
    routes: {
      '/api/settings': { default_apartment_id: 'flat', server: { operator_name: 'sarbananda' } },
      'PATCH /api/apartments/flat': { ...apartment, name: 'Flat 1805' },
    } as never,
  })
  render(
    <ToastProvider>
      <SettingsCard apartments={[apartment]} defaultApartmentId="flat" onSaved={onSaved} />
    </ToastProvider>,
  )
}

const patched = () => stub!.calls.find((call) => call.method === 'PATCH')

describe('Setup settings', () => {
  it('open read-only and edit in a dialog', async () => {
    open()
    const settings = await screen.findByLabelText('Settings')
    expect(settings).toHaveTextContent('Flat 1805, Supertech Capetown')
    expect(settings).toHaveTextContent('INR')
    expect(settings).toHaveTextContent('Asia/Kolkata')
    await waitFor(() => expect(settings).toHaveTextContent('sarbananda'))
    expect(screen.queryByLabelText(/Apartment name/)).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Edit settings' }))
    const dialog = screen.getByRole('dialog')
    // The dialog keeps the name of the card it edits.
    expect(within(dialog).getByRole('heading', { name: 'Settings' })).toBeInTheDocument()
    const name = within(dialog).getByLabelText(/Apartment name/)
    await userEvent.clear(name)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    expect(within(dialog).getByText('A name is required.')).toBeInTheDocument()
    expect(patched()).toBeUndefined()

    await userEvent.type(name, 'Flat 1805')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(patched()).toBeDefined())
    // Only what changed is sent.
    expect(patched()!.body).toEqual({ name: 'Flat 1805' })
  })
})
