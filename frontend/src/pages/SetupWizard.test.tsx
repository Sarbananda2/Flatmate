/**
 * The guided setup.
 *
 * The behaviour worth testing is the recovery one: the step shown must come
 * from the server's view of what exists, not from anything the browser
 * remembered. So these tests drive the resume point purely by changing the
 * stubbed `/api/setup/state`, and one of them wipes localStorage first.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SetupWizardPage from './SetupWizard'
import { ToastProvider } from '../components/Toast'
import { AppContext, Layout, type AppContextValue } from '../components/Layout'
import { installFetchStub, type Recorded } from '../test/server'
import type { SetupStepKey } from '../api/types'

const CATALOGUE = {
  total: 3,
  suggested_count: 2,
  items: [
    {
      definition_id: 'rent',
      name: 'Rent',
      category: 'Housing',
      period_rule: 'current_month' as const,
      default_allocation_method: 'equal' as const,
      recurrence: 'monthly' as const,
      sort_order: 10,
      suggested: true,
      hint: "October's rent is charged in the October statement.",
    },
    {
      definition_id: 'electricity',
      name: 'Electricity',
      category: 'Utilities',
      period_rule: 'previous_month' as const,
      default_allocation_method: 'measured' as const,
      recurrence: 'monthly' as const,
      sort_order: 20,
      suggested: true,
      hint: 'The bill that arrives in October is for September.',
    },
    {
      definition_id: 'dth',
      name: 'DTH or cable',
      category: 'Utilities',
      period_rule: 'current_month' as const,
      default_allocation_method: 'equal' as const,
      recurrence: 'monthly' as const,
      sort_order: 30,
      suggested: false,
      hint: 'A television subscription.',
    },
  ],
}

/** The apartment as step 1 saved it: named after its society and flat. */
const LAKEVIEW = {
  apartment_id: 'flat-402',
  name: 'Lakeview 402',
  currency_code: 'INR',
  timezone: 'Asia/Kolkata',
  created_at: '2026-10-11T10:00:00+05:30',
  flat_number: '402',
  tower_block: null,
  society_name: 'Lakeview',
  locality_street: null,
  city: 'Pune',
  state: null,
  pincode: null,
}

/** Step 1's fields, by their labels as the operator reads them. */
const addressLabels = () =>
  screen.getAllByRole('textbox').map((box) => box.closest('.field')?.querySelector('label')?.textContent)

function state(next: SetupStepKey, overrides: Record<string, unknown> = {}) {
  const done = (key: SetupStepKey) =>
    ['apartment', 'residents', 'categories', 'finish'].indexOf(key) <
    ['apartment', 'residents', 'categories', 'finish'].indexOf(next)
  return {
    next_step: next,
    is_complete: next === 'done',
    completed_at: next === 'done' ? '2026-10-11T12:00:00+05:30' : null,
    apartment_count: next === 'apartment' ? 0 : 1,
    apartment: next === 'apartment' ? null : LAKEVIEW,
    apartment_id: next === 'apartment' ? null : 'flat-402',
    resident_count: done('residents') || next === 'done' ? 3 : 0,
    active_resident_count: done('residents') || next === 'done' ? 3 : 0,
    residents: done('residents') || next === 'done'
      ? [
          { resident_id: 'alice', display_name: 'Alice', is_active: 1 },
          { resident_id: 'bob', display_name: 'Bob', is_active: 1 },
          { resident_id: 'chris', display_name: 'Chris', is_active: 1 },
        ]
      : [],
    definition_count: done('categories') || next === 'done' ? 2 : 0,
    definitions: done('categories') || next === 'done'
      ? [
          { expense_definition_id: 'rent', name: 'Rent', category: 'Housing',
            period_rule: 'current_month' as const },
          { expense_definition_id: 'electricity', name: 'Electricity',
            category: 'Utilities', period_rule: 'previous_month' as const },
        ]
      : [],
    statement_count: 0,
    current_month: '2026-10',
    steps: [
      { key: 'apartment' as const, label: 'Your apartment', done: done('apartment') || next !== 'apartment',
        summary: next === 'apartment' ? null : 'Lakeview 402' },
      { key: 'residents' as const, label: 'Who lives here',
        done: done('residents') || next === 'done',
        summary: done('residents') || next === 'done' ? 'Alice, Bob, Chris' : null },
      { key: 'categories' as const, label: 'What you pay for',
        done: done('categories') || next === 'done',
        summary: done('categories') || next === 'done' ? '2 categories' : null },
      { key: 'finish' as const, label: 'Ready', done: next === 'done', summary: null },
    ],
    ...overrides,
  }
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
/** How often the wizard asked the shell to fetch the apartment and setup again. */
let shellReloads = 0

/** What the Layout would give the page; only the reload is looked at. */
const appContext = (): AppContextValue => ({
  month: '2026-10',
  setMonth: () => {},
  availableMonths: [],
  apartment: null,
  health: null,
  setup: null,
  reloadApartment: () => {
    shellReloads += 1
  },
})

beforeEach(() => {
  window.localStorage.clear()
  shellReloads = 0
})

afterEach(() => {
  stub?.restore()
  stub = null
  window.localStorage.clear()
})

function renderAt(next: SetupStepKey, extraRoutes: Record<string, unknown> = {}) {
  stub = installFetchStub({
    routes: {
      '/api/setup/state': state(next),
      '/api/setup/catalogue': CATALOGUE,
      'POST /api/setup/apartment': { apartment_id: 'flat-402', state: state('residents') },
      'POST /api/setup/residents': { created_count: 3, state: state('categories') },
      'POST /api/setup/categories': { created_count: 3, state: state('finish') },
      'POST /api/setup/finish': { statement_id: 'stmt-1', is_complete: true },
      ...extraRoutes,
    },
  })
  return render(
    <MemoryRouter initialEntries={['/welcome']}>
      <ToastProvider>
        <AppContext.Provider value={appContext()}>
          <SetupWizardPage />
        </AppContext.Provider>
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('SetupWizardPage', () => {
  it('starts at the apartment step on an empty database, asking for its address', async () => {
    renderAt('apartment')
    expect(await screen.findByText('Step 1 · Where is your apartment?')).toBeInTheDocument()
    expect(addressLabels()).toEqual([
      'Flat / Unit Number* (required)',
      'Tower / Wing / Block',
      'Society / Complex Name* (required)',
      'Locality / Sector / Street',
      'City* (required)',
      'State',
      'PIN Code',
    ])
  })

  it('never asks the operator to invent an identifier, or talks about one', async () => {
    renderAt('apartment')
    await screen.findByLabelText(/^Flat \/ Unit Number/)
    expect(screen.queryByLabelText(/Apartment ID/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Apartment name/)).not.toBeInTheDocument()
    expect(screen.queryByText(/identifier|exports/)).not.toBeInTheDocument()
    expect(
      screen.getByText(/These will be used on your household dashboard, lease terms, and HRA rent receipts\./),
    ).toBeInTheDocument()
  })

  it('sends the address part by part, and the backend names the apartment', async () => {
    renderAt('apartment')
    await userEvent.type(await screen.findByLabelText(/^Flat \/ Unit Number/), '1805')
    await userEvent.type(screen.getByLabelText(/^Tower \/ Wing/), 'CG6')
    await userEvent.type(screen.getByLabelText(/^Society \/ Complex/), ' Supertech Capetown ')
    await userEvent.type(screen.getByLabelText(/^City/), 'Noida')
    await userEvent.type(screen.getByLabelText(/^PIN Code/), '201301')
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path === '/api/setup/apartment')).toBe(true),
    )
    const call = stub!.calls.find((item) => item.path === '/api/setup/apartment')
    expect(call!.body).toEqual({
      flat_number: '1805',
      tower_block: 'CG6',
      society_name: 'Supertech Capetown',
      locality_street: null,
      city: 'Noida',
      state: null,
      pincode: '201301',
    })
  })

  it('needs the flat, the society and the city, and checks a PIN code as it is typed', async () => {
    renderAt('apartment')
    await userEvent.type(await screen.findByLabelText(/^PIN Code/), '0123')
    expect(screen.getByText('A PIN code is 6 digits and does not start with 0.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    for (const message of ['Enter flat/unit number.', 'Enter society/complex name.', 'Enter city.']) {
      expect(screen.getByText(message)).toBeInTheDocument()
    }
    expect(stub!.calls.some((call) => call.path === '/api/setup/apartment')).toBe(false)
  })

  // ---------------------------------------------------------------- resume --

  it('resumes at the step the server reports, not at the beginning', async () => {
    renderAt('categories')
    expect(await screen.findByText(/What does the household pay for/)).toBeInTheDocument()
    expect(screen.queryByText(/What is this place called/)).not.toBeInTheDocument()
  })

  it('resumes correctly even with no browser storage at all', async () => {
    // A private window, a cleared browser, or a different machine.
    window.localStorage.clear()
    renderAt('categories')
    expect(await screen.findByText(/What does the household pay for/)).toBeInTheDocument()
    // What is already saved is read off the stepper, from the server.
    const stepper = screen.getByRole('list', { name: 'Setup progress' })
    expect(within(stepper).getByText(/Lakeview 402/)).toBeInTheDocument()
  })

  it('tells the operator what is already saved when resuming', async () => {
    renderAt('categories')
    await screen.findByText(/What does the household pay for/)
    const stepper = screen.getByRole('list', { name: 'Setup progress' })
    expect(within(stepper).getByText('Alice, Bob, Chris')).toBeInTheDocument()
    // The stepper says it once; there is no second banner repeating it.
    expect(screen.queryByText(/Your progress is saved/)).not.toBeInTheDocument()
  })

  it('reassures the operator that closing it is safe', async () => {
    renderAt('residents')
    // Said once, in the subtitle at the top, not repeated under the card.
    expect(await screen.findByText(/Each one saves as you go/)).toBeInTheDocument()
    expect(screen.queryByText(/Need to stop\?/)).not.toBeInTheDocument()
  })

  // ------------------------------------------------------------- residents --

  it('removes a resident box, keeping what was typed in the others', async () => {
    renderAt('residents')
    await userEvent.type(await screen.findByLabelText('Resident 1'), 'Alice')
    await userEvent.type(screen.getByLabelText('Resident 2'), 'Bob')
    await userEvent.type(screen.getByLabelText('Resident 3'), 'Chris')
    expect(screen.getByLabelText('Resident 1')).toHaveAttribute('placeholder', 'e.g. Alice')

    await userEvent.click(screen.getByRole('button', { name: 'Remove resident 2' }))
    expect(screen.getByLabelText('Resident 1')).toHaveValue('Alice')
    expect(screen.getByLabelText('Resident 2')).toHaveValue('Chris')
    expect(screen.queryByLabelText('Resident 3')).not.toBeInTheDocument()

    // The last box cannot be removed.
    await userEvent.click(screen.getByRole('button', { name: 'Remove resident 2' }))
    expect(screen.getByLabelText('Resident 1')).toHaveValue('Alice')
    expect(screen.queryByRole('button', { name: /Remove resident/ })).not.toBeInTheDocument()
  })

  it('takes everyone at once and ignores the blank boxes', async () => {
    renderAt('residents')
    await userEvent.type(await screen.findByLabelText('Resident 1'), 'Alice')
    await userEvent.type(screen.getByLabelText('Resident 2'), 'Bob')
    // Resident 3 deliberately left empty.
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path === '/api/setup/residents')).toBe(true),
    )
    const call = stub!.calls.find((item) => item.path === '/api/setup/residents')
    expect(call!.body).toEqual({ names: ['Alice', 'Bob'] })
  })

  it('will not continue with nobody entered', async () => {
    renderAt('residents')
    await screen.findByLabelText('Resident 1')
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('Add at least one person.')).toBeInTheDocument()
    expect(stub!.calls.some((call) => call.path === '/api/setup/residents')).toBe(false)
  })

  // ------------------------------------------------------------ categories --

  it('pre-ticks the suggested essentials so the common case is one click', async () => {
    renderAt('categories')
    await screen.findByText(/What does the household pay for/)
    // The ticks are put in once the catalogue has arrived, a render after it shows.
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: 'Include Rent' })).toBeChecked(),
    )
    expect(screen.getByRole('checkbox', { name: 'Include Electricity' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Include DTH or cable' })).not.toBeChecked()
    expect(
      screen.getByRole('button', { name: /Add 2 and continue/ }),
    ).toBeInTheDocument()
  })

  it('shows which month each essential is charged in, and why', async () => {
    renderAt('categories')
    await screen.findByText(/What does the household pay for/)
    const table = screen.getByRole('table', { name: 'Essentials to set up' })
    const electricity = within(table).getByRole('row', { name: /Electricity/ })
    expect(electricity).toHaveTextContent('Previous month')
    expect(electricity).toHaveTextContent(/arrives in October is for September/)
  })

  it('submits the ticked essentials with their rules and optional amounts', async () => {
    renderAt('categories')
    await screen.findByText(/What does the household pay for/)
    await userEvent.type(screen.getByLabelText('Typical amount for Rent'), '45000')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Include DTH or cable' }))
    await userEvent.click(screen.getByRole('button', { name: /Add 3 and continue/ }))

    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path === '/api/setup/categories')).toBe(true),
    )
    const body = stub!.calls.find((c) => c.path === '/api/setup/categories')!.body as {
      items: Array<{ name: string; period_rule: string; default_amount_paise?: number }>
    }
    expect(body.items.map((item) => item.name)).toEqual(['Rent', 'Electricity', 'DTH or cable'])
    const rent = body.items.find((item) => item.name === 'Rent')!
    expect(rent.default_amount_paise).toBe(4_500_000)
    expect(Number.isInteger(rent.default_amount_paise)).toBe(true)
    expect(body.items.find((item) => item.name === 'Electricity')!.period_rule)
      .toBe('previous_month')
    // An amount left blank is simply absent, not zero.
    expect(body.items.find((item) => item.name === 'DTH or cable')!.default_amount_paise)
      .toBeUndefined()
  })

  it('lets the operator add something not in the list', async () => {
    renderAt('categories')
    await screen.findByText(/What does the household pay for/)
    await userEvent.click(screen.getByRole('button', { name: /Something else you pay for/ }))
    await userEvent.type(screen.getByLabelText(/Your own category 1/), 'Terrace garden')
    await userEvent.click(screen.getByRole('button', { name: /Add 3 and continue/ }))
    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path === '/api/setup/categories')).toBe(true),
    )
    const body = stub!.calls.find((c) => c.path === '/api/setup/categories')!.body as {
      items: Array<{ name: string }>
    }
    expect(body.items.map((item) => item.name)).toContain('Terrace garden')
  })

  it('says plainly that no category is built into the application', async () => {
    renderAt('categories')
    expect(
      await screen.findByText(/None of these is built into the application/),
    ).toBeInTheDocument()
  })

  // ----------------------------------------------------------------- finish --

  it('summarises everything and offers to open the first statement', async () => {
    renderAt('finish')
    const heading = await screen.findByText(/That is everything/)
    // Scope to the summary card: the stepper also shows the resident names.
    const summary = heading.closest('.card') as HTMLElement
    expect(within(summary).getByText('Alice, Bob, Chris')).toBeInTheDocument()
    expect(within(summary).getByText('Rent, Electricity')).toBeInTheDocument()
    const openStatement = screen.getByRole('checkbox', { name: /Open the October 2026 statement/ })
    expect(openStatement).toBeChecked()
  })

  it('finishes, opening the statement for the current month', async () => {
    renderAt('finish')
    await userEvent.click(await screen.findByRole('button', { name: 'Finish setup' }))
    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path === '/api/setup/finish')).toBe(true),
    )
    expect(stub!.calls.find((c) => c.path === '/api/setup/finish')!.body).toEqual({
      create_statement_for: '2026-10',
    })
  })

  it('can finish without opening a statement', async () => {
    renderAt('finish')
    await userEvent.click(
      await screen.findByRole('checkbox', { name: /Open the October 2026 statement/ }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Finish setup' }))
    await waitFor(() =>
      expect(stub!.calls.some((call) => call.path === '/api/setup/finish')).toBe(true),
    )
    expect(stub!.calls.find((c) => c.path === '/api/setup/finish')!.body).toEqual({
      create_statement_for: null,
    })
  })

  it('shows a completed setup as done rather than prompting again', async () => {
    renderAt('done')
    expect(await screen.findByText('All set')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Finish setup' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to the dashboard' })).toBeInTheDocument()
  })

  it('has the shell fetch the setup state again once setup is finished', async () => {
    renderAt('finish')
    await userEvent.click(await screen.findByRole('button', { name: 'Finish setup' }))
    await waitFor(() => expect(shellReloads).toBe(1))
  })

  it('has the shell fetch the setup state again on the way to the dashboard', async () => {
    renderAt('done')
    await userEvent.click(await screen.findByRole('link', { name: 'Go to the dashboard' }))
    expect(shellReloads).toBe(1)
  })

  // ------------------------------------------------------------ going back --

  describe('going back', () => {
    const calls = (method: string, path: string) =>
      stub!.calls.filter((call) => call.method === method && call.path === path)

    it('reopens a finished step from the stepper and returns to where setup stands', async () => {
      renderAt('categories')
      await screen.findByText(/What does the household pay for/)
      const stepper = screen.getByRole('list', { name: 'Setup progress' })
      // The step still to come cannot be opened.
      expect(within(stepper).queryByRole('button', { name: /Ready/ })).not.toBeInTheDocument()

      await userEvent.click(within(stepper).getByRole('button', { name: /Your apartment/ }))
      expect(screen.getByText('Step 1 · Where is your apartment?')).toBeInTheDocument()
      // The address as it was saved, ready to change.
      expect(screen.getByLabelText(/^Flat \/ Unit Number/)).toHaveValue('402')
      expect(screen.getByLabelText(/^Society \/ Complex/)).toHaveValue('Lakeview')
      expect(screen.getByLabelText(/^City/)).toHaveValue('Pune')
      expect(within(stepper).getAllByRole('listitem')[0]).toHaveAttribute('aria-current', 'step')

      await userEvent.click(within(stepper).getByRole('button', { name: /What you pay for/ }))
      expect(screen.getByText(/What does the household pay for/)).toBeInTheDocument()
    })

    it('steps back one at a time with the Back buttons', async () => {
      renderAt('finish')
      await screen.findByText(/That is everything/)
      await userEvent.click(screen.getByRole('button', { name: 'Back' }))
      expect(screen.getByText(/What does the household pay for/)).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: 'Back' }))
      expect(screen.getByText(/Who lives here\?/)).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: 'Back' }))
      expect(screen.getByText('Step 1 · Where is your apartment?')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
    })

    it('changes the address instead of creating a second apartment, the name following it', async () => {
      renderAt('categories', { 'PATCH /api/apartments/flat-402': { apartment_id: 'flat-402' } })
      await screen.findByText(/What does the household pay for/)
      await userEvent.click(screen.getByRole('button', { name: /Your apartment/ }))
      const flat = screen.getByLabelText(/^Flat \/ Unit Number/)
      await userEvent.clear(flat)
      await userEvent.type(flat, '403')
      await userEvent.type(screen.getByLabelText(/^State/), 'Maharashtra')
      await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

      await waitFor(() => expect(calls('PATCH', '/api/apartments/flat-402')).toHaveLength(1))
      // Its name was the one its address gave it, so it follows the address.
      expect(calls('PATCH', '/api/apartments/flat-402')[0].body).toEqual({
        flat_number: '403',
        tower_block: null,
        society_name: 'Lakeview',
        locality_street: null,
        city: 'Pune',
        state: 'Maharashtra',
        pincode: null,
        name: 'Lakeview 403',
      })
      expect(calls('POST', '/api/setup/apartment')).toHaveLength(0)
      // Continuing moves on to the next step, not straight to the end.
      expect(await screen.findByText(/Who lives here\?/)).toBeInTheDocument()
    })

    it('keeps a name of its own when the address changes', async () => {
      renderAt('categories', {
        '/api/setup/state': state('categories', { apartment: { ...LAKEVIEW, name: 'Our Flat' } }),
        'PATCH /api/apartments/flat-402': { apartment_id: 'flat-402' },
      })
      await screen.findByText(/What does the household pay for/)
      await userEvent.click(screen.getByRole('button', { name: /Your apartment/ }))
      await userEvent.type(screen.getByLabelText(/^PIN Code/), '411001')
      await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
      await waitFor(() => expect(calls('PATCH', '/api/apartments/flat-402')).toHaveLength(1))
      expect(calls('PATCH', '/api/apartments/flat-402')[0].body).toMatchObject({ pincode: '411001' })
      expect(calls('PATCH', '/api/apartments/flat-402')[0].body).not.toHaveProperty('name')
    })

    it('saves nothing when a revisited step is continued unchanged', async () => {
      renderAt('finish')
      await screen.findByText(/That is everything/)
      await userEvent.click(screen.getByRole('button', { name: /Your apartment/ }))
      await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

      expect(await screen.findByText(/Who lives here\?/)).toBeInTheDocument()
      const saved = screen.getByRole('region', { name: 'Residents already added' })
      expect(within(saved).getByLabelText('Name of Alice')).toHaveValue('Alice')
      expect(screen.getByLabelText('New flatmate 1')).toHaveValue('')
      await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

      expect(await screen.findByText(/What does the household pay for/)).toBeInTheDocument()
      const posts = stub!.calls.filter((call) => call.method !== 'GET')
      expect(posts).toHaveLength(0)
    })

    it('adds only the new person when residents are revisited', async () => {
      // Names typed on the first pass must not come back as boxes to add again.
      window.localStorage.setItem(
        'aem.setup-draft',
        JSON.stringify({ residentNames: ['Alice', 'Bob', 'Chris'] }),
      )
      renderAt('finish')
      await screen.findByText(/That is everything/)
      await userEvent.click(screen.getByRole('button', { name: /Who lives here/ }))
      expect(screen.getByRole('heading', { name: 'Add new flatmates' })).toBeInTheDocument()
      expect(screen.getByLabelText('New flatmate 1')).toHaveValue('')
      expect(screen.queryByLabelText('New flatmate 2')).not.toBeInTheDocument()

      await userEvent.type(screen.getByLabelText('New flatmate 1'), 'Dana')
      await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

      await waitFor(() => expect(calls('POST', '/api/setup/residents')).toHaveLength(1))
      expect(calls('POST', '/api/setup/residents')[0].body).toEqual({ names: ['Dana'] })
      expect(await screen.findByText(/What does the household pay for/)).toBeInTheDocument()
    })

    it('renames a saved resident in place on Enter or blur, and not when unchanged', async () => {
      renderAt('finish', { 'PATCH /api/residents/bob': { resident_id: 'bob' } })
      await screen.findByText(/That is everything/)
      await userEvent.click(screen.getByRole('button', { name: /Who lives here/ }))

      const bob = screen.getByLabelText('Name of Bob')
      await userEvent.click(bob)
      await userEvent.tab()
      expect(calls('PATCH', '/api/residents/bob')).toHaveLength(0)

      await userEvent.clear(bob)
      await userEvent.type(bob, '  Bobby {Enter}')
      await waitFor(() => expect(calls('PATCH', '/api/residents/bob')).toHaveLength(1))
      expect(calls('PATCH', '/api/residents/bob')[0].body).toEqual({ display_name: 'Bobby' })
      // Enter saved the name; it did not submit the step.
      expect(screen.getByText(/Who lives here\?/)).toBeInTheDocument()

      // An emptied box goes back to the saved name and saves nothing.
      const alice = screen.getByLabelText('Name of Alice')
      await userEvent.clear(alice)
      await userEvent.tab()
      expect(alice).toHaveValue('Alice')
      expect(stub!.calls.filter((call) => call.path === '/api/residents/alice')).toHaveLength(0)
    })

    it('removes a saved resident by marking them moved out, and can restore them', async () => {
      let current = state('finish')
      renderAt('finish', {
        '/api/setup/state': () => current,
        'POST /api/residents/chris/deactivate': () => {
          current = {
            ...current,
            residents: current.residents.map((r) =>
              r.resident_id === 'chris' ? { ...r, is_active: 0 } : r,
            ),
          }
          return { resident_id: 'chris' }
        },
        'POST /api/residents/chris/reactivate': { resident_id: 'chris' },
      })
      await screen.findByText(/That is everything/)
      await userEvent.click(screen.getByRole('button', { name: /Who lives here/ }))

      await userEvent.click(screen.getByRole('button', { name: 'Remove Chris' }))
      await waitFor(() => expect(screen.queryByLabelText('Name of Chris')).not.toBeInTheDocument())
      expect(calls('POST', '/api/residents/chris/deactivate')[0].body).toEqual({})
      expect(screen.getByText('Removed (kept as moved out):')).toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: 'Restore Chris' }))
      await waitFor(() =>
        expect(calls('POST', '/api/residents/chris/reactivate')).toHaveLength(1),
      )
    })

    it('will not remove the last remaining resident', async () => {
      const one = state('finish')
      renderAt('finish', {
        '/api/setup/state': {
          ...one,
          residents: one.residents.map((r, index) => ({ ...r, is_active: index === 0 ? 1 : 0 })),
        },
      })
      await screen.findByText(/That is everything/)
      await userEvent.click(screen.getByRole('button', { name: /Who lives here/ }))
      expect(screen.getByRole('button', { name: 'Remove Alice' })).toBeDisabled()
      expect(screen.getByText('At least one resident is required.')).toBeInTheDocument()
    })

    it('adds only the new categories when they are revisited', async () => {
      renderAt('finish')
      await screen.findByText(/That is everything/)
      await userEvent.click(screen.getByRole('button', { name: 'Back' }))
      expect(screen.getByText(/Unticking one does not remove it/)).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: /^Add \d+ and continue$|^Continue$/ }))

      await waitFor(() => expect(screen.getByText(/That is everything/)).toBeInTheDocument())
      const sent = calls('POST', '/api/setup/categories')
      for (const call of sent) {
        const names = (call.body as { items: Array<{ name: string }> }).items.map((i) => i.name)
        expect(names).not.toContain('Rent')
        expect(names).not.toContain('Electricity')
      }
    })
  })
})

/* ------------------------------------------------- the shell after setup -- */

describe('the setup banner after leaving the guided setup', () => {
  const BANNER = /Setup is half finished/

  /** The app's own shell around the wizard and a stand-in dashboard. */
  function renderShell(serverState: () => unknown, welcome: JSX.Element) {
    stub = installFetchStub({
      routes: {
        '/health': { status: 'ok', schema_version: 16 },
        '/api/apartments': { items: [LAKEVIEW], default_apartment_id: 'flat-402' },
        '/api/setup/state': () => serverState(),
        '/api/setup/catalogue': CATALOGUE,
        'POST /api/setup/finish': { statement_id: null, is_complete: true },
      } as never,
    })
    render(
      <MemoryRouter initialEntries={['/welcome']}>
        <ToastProvider>
          <Layout>
            <Routes>
              <Route path="/welcome" element={welcome} />
              <Route path="/" element={<p>Dashboard</p>} />
            </Routes>
          </Layout>
        </ToastProvider>
      </MemoryRouter>,
    )
  }
  const setupFetches = () =>
    stub!.calls.filter((call) => call.method === 'GET' && call.path === '/api/setup/state').length

  it('is not left behind when setup finished while the shell held an older state', async () => {
    let current: SetupStepKey = 'residents'
    renderShell(
      () => state(current),
      <Link to="/">Leave setup</Link>,
    )
    await waitFor(() => expect(setupFetches()).toBe(1))
    // Setup is completed elsewhere; the shell still holds "residents".
    current = 'done'
    await userEvent.click(screen.getByRole('link', { name: 'Leave setup' }))
    expect(await screen.findByText('Dashboard')).toBeInTheDocument()
    expect(screen.queryByText(BANNER)).toBeNull()
    await waitFor(() => expect(setupFetches()).toBe(2))
    expect(screen.queryByText(BANNER)).toBeNull()
  })

  it('still shows when setup really is unfinished', async () => {
    renderShell(() => state('residents'), <Link to="/">Leave setup</Link>)
    await waitFor(() => expect(setupFetches()).toBe(1))
    await userEvent.click(screen.getByRole('link', { name: 'Leave setup' }))
    const banner = (await screen.findByText(BANNER)).closest('[role="status"]')
    expect(banner).toHaveTextContent('Next: who lives here.')
  })

  it('does not follow the operator from Finish setup to the dashboard', async () => {
    let current: SetupStepKey = 'finish'
    renderShell(() => state(current), <SetupWizardPage />)
    const finish = await screen.findByRole('button', { name: 'Finish setup' })
    current = 'done'
    await userEvent.click(finish)
    expect(await screen.findByText('Dashboard')).toBeInTheDocument()
    expect(screen.queryByText(BANNER)).toBeNull()
    await waitFor(() => expect(screen.queryByText(BANNER)).toBeNull())
  })
})
