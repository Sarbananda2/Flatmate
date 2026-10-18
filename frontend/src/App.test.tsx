/**
 * Signing in. Nothing of the household shows until someone is signed in: a
 * new installation asks for the household admin, an existing one for a login.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { installFetchStub, type Recorded } from './test/server'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const ADMIN = {
  user_id: 'usr_1',
  email: 'asha@example.com',
  full_name: 'Asha Rao',
  role: 'household_admin' as const,
}
const NEW_INSTALL = { setup_required: true, authenticated: false, user: null }
const SIGNED_OUT = { setup_required: false, authenticated: false, user: null }
const SIGNED_IN = { setup_required: false, authenticated: true, user: ADMIN }

/** What the shell asks for once someone is signed in. */
const SHELL = {
  '/health': { status: 'ok', schema_version: 19 },
  '/api/apartments': { items: [], default_apartment_id: null },
  '/api/setup/state': { next_step: 'done', is_complete: true, apartment_count: 1 },
}

/** Where setup stands on an empty database, as the guided setup reads it. */
const SETUP_FROM_SCRATCH = {
  next_step: 'apartment',
  is_complete: false,
  completed_at: null,
  apartment_count: 0,
  apartment: null,
  apartment_id: null,
  resident_count: 0,
  active_resident_count: 0,
  residents: [],
  definition_count: 0,
  definitions: [],
  statement_count: 0,
  current_month: '2026-10',
  steps: [
    { key: 'apartment', label: 'Your apartment', done: false, summary: null },
    { key: 'residents', label: 'Who lives here', done: false, summary: null },
    { key: 'categories', label: 'What you pay for', done: false, summary: null },
    { key: 'finish', label: 'Ready', done: false, summary: null },
  ],
}

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname}</output>
}

function renderApp(
  path: string,
  routes: Record<string, unknown>,
  errors?: Record<string, { status: number; code: string; message: string }>,
) {
  stub = installFetchStub({ routes: { ...SHELL, ...routes } as never, errors })
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Where />
    </MemoryRouter>,
  )
}

const location = () => screen.getByLabelText('location').textContent
const posted = (path: string) =>
  stub!.calls.find((call) => call.method === 'POST' && call.path === path)

describe('a new installation', () => {
  it('asks for the household admin before anything else', async () => {
    renderApp('/payments', { '/api/auth/status': NEW_INSTALL })
    expect(
      await screen.findByRole('heading', { name: 'Create your Household Admin Account' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Set up your account to manage your apartment, residents, and shared expenses.'),
    ).toBeInTheDocument()
    expect(location()).toBe('/signup')
    // Nothing of the household is asked for until then.
    expect(stub!.calls.some((call) => call.path === '/api/apartments')).toBe(false)
  })

  it('checks the details before sending them', async () => {
    renderApp('/', { '/api/auth/status': NEW_INSTALL })
    const create = await screen.findByRole('button', { name: 'Create account' })
    await userEvent.click(create)
    expect(screen.getByText('Enter your full name.')).toBeInTheDocument()
    expect(screen.getByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Use at least 8 characters.')).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/Full name/), 'Asha Rao')
    await userEvent.type(screen.getByLabelText(/Email address/), 'asha.example.com')
    await userEvent.type(screen.getByLabelText(/^Password/), 'short')
    await userEvent.click(create)
    expect(screen.getByText('Enter a valid email address, like name@example.com.')).toBeInTheDocument()
    expect(screen.getByText('Use at least 8 characters.')).toBeInTheDocument()
    expect(posted('/api/auth/signup')).toBeUndefined()
  })

  it('signs the new admin in and opens the guided setup', async () => {
    renderApp('/', {
      '/api/auth/status': NEW_INSTALL,
      'POST /api/auth/signup': { user: ADMIN },
      '/api/setup/state': SETUP_FROM_SCRATCH,
      '/api/setup/catalogue': { items: [], total: 0, suggested_count: 0 },
    })
    await userEvent.type(await screen.findByLabelText(/Full name/), '  Asha Rao ')
    await userEvent.type(screen.getByLabelText(/Email address/), 'asha@example.com')
    await userEvent.type(screen.getByLabelText(/^Password/), 'correct horse battery')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    await waitFor(() => expect(location()).toBe('/welcome'))
    expect(posted('/api/auth/signup')!.body).toEqual({
      full_name: 'Asha Rao',
      email: 'asha@example.com',
      password: 'correct horse battery',
    })
    const signedIn = await screen.findByRole('group', { name: 'Signed in' })
    expect(signedIn).toHaveTextContent('Asha Rao')
    expect(signedIn).toHaveTextContent('Admin')
  })

  it('shows what the server says when it refuses', async () => {
    renderApp(
      '/',
      { '/api/auth/status': NEW_INSTALL },
      {
        'POST /api/auth/signup': {
          status: 409,
          code: 'signup_closed',
          message: 'A household admin has already signed up. Log in instead.',
        },
      },
    )
    await userEvent.type(await screen.findByLabelText(/Full name/), 'Asha Rao')
    await userEvent.type(screen.getByLabelText(/Email address/), 'asha@example.com')
    await userEvent.type(screen.getByLabelText(/^Password/), 'correct horse battery')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(
      await screen.findByText('A household admin has already signed up. Log in instead.'),
    ).toBeInTheDocument()
    expect(location()).toBe('/signup')
  })
})

describe('an existing household', () => {
  it('asks for a login, then goes back to the page that was asked for', async () => {
    renderApp('/payments', {
      '/api/auth/status': SIGNED_OUT,
      'POST /api/auth/login': { user: ADMIN },
    })
    expect(await screen.findByRole('heading', { name: 'Log in to your Household' })).toBeInTheDocument()
    expect(location()).toBe('/login')
    // There is no signing up once the household admin exists.
    expect(screen.queryByRole('button', { name: 'Create account' })).toBeNull()

    await userEvent.type(screen.getByLabelText(/Email address/), ' asha@example.com ')
    await userEvent.type(screen.getByLabelText(/^Password/), 'correct horse battery')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(location()).toBe('/payments'))
    expect(posted('/api/auth/login')!.body).toEqual({
      email: 'asha@example.com',
      password: 'correct horse battery',
    })
  })

  it('keeps the login page when the password is wrong, and says so', async () => {
    renderApp(
      '/',
      { '/api/auth/status': SIGNED_OUT },
      {
        'POST /api/auth/login': {
          status: 401,
          code: 'invalid_credentials',
          message: 'Invalid email or password.',
        },
      },
    )
    await userEvent.type(await screen.findByLabelText(/Email address/), 'asha@example.com')
    await userEvent.type(screen.getByLabelText(/^Password/), 'wrong password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByText('Invalid email or password.')).toBeInTheDocument()
    expect(location()).toBe('/login')
  })

  it('sends the signup page to the login once someone has signed up', async () => {
    renderApp('/signup', { '/api/auth/status': SIGNED_OUT })
    expect(await screen.findByRole('heading', { name: 'Log in to your Household' })).toBeInTheDocument()
    expect(location()).toBe('/login')
  })
})

describe('signed in', () => {
  it('shows who is signed in, as the admin, with a way to log out', async () => {
    renderApp('/residents', { '/api/auth/status': SIGNED_IN, 'POST /api/auth/logout': {} })
    const signedIn = await screen.findByRole('group', { name: 'Signed in' })
    expect(signedIn).toHaveTextContent('Asha Rao')
    expect(within(signedIn).getByText('Admin')).toBeInTheDocument()

    await userEvent.click(within(signedIn).getByRole('button', { name: 'Log out' }))
    await waitFor(() => expect(posted('/api/auth/logout')).toBeDefined())
    expect(await screen.findByRole('heading', { name: 'Log in to your Household' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Signed in' })).toBeNull()
  })

  it('does not show the login page to someone already signed in', async () => {
    renderApp('/login', { '/api/auth/status': SIGNED_IN })
    await screen.findByRole('group', { name: 'Signed in' })
    expect(location()).toBe('/')
  })

  it('goes back to the login page when the session has lapsed', async () => {
    renderApp(
      '/',
      { '/api/auth/status': SIGNED_IN },
      {
        'GET /api/apartments': { status: 401, code: 'authentication_required', message: 'Log in to continue.' },
      },
    )
    expect(await screen.findByRole('heading', { name: 'Log in to your Household' })).toBeInTheDocument()
  })
})

it('says so, with a retry, when the server cannot be asked who is signed in', async () => {
  renderApp('/', {}, {
    'GET /api/auth/status': {
      status: 503,
      code: 'database_unavailable',
      message: 'The database is not reachable.',
    },
  })
  expect(await screen.findByText('The database is not reachable.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Retry|Try again/ })).toBeInTheDocument()
})
