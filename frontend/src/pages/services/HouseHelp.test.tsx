/**
 * The house help screen shows what the backend worked out -- each flatmate's
 * share by role, each worker's salary and what has been paid to them -- and
 * sends exactly what the operator chose: the month's attendance, a payment
 * into the kitty, a payout to a worker, a helper and their roles.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HouseHelp } from './HouseHelp'
import { ToastProvider } from '../../components/Toast'
import { RESIDENTS, installFetchStub, type Recorded } from '../../test/server'

const MONTH = '2026-11'
const BASE = '/api/services/househelp'

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

const people = { alice: 'Alice', bob: 'Bob', chris: 'Chris' } as const

/** A role on the month: Cleaning is everyone's, Cooking only Alice's and Bob's. */
function role(id: 'cleaning' | 'cooking', overrides: Record<string, unknown> = {}) {
  const cleaning = id === 'cleaning'
  const base = cleaning ? 300000 : 450000
  return {
    role_id: id,
    role_name: cleaning ? 'Cleaning' : 'Cooking',
    worker_id: 'sunita',
    worker_name: 'Sunita',
    split_method: cleaning ? 'equal' : 'opt_in',
    base_salary_paise: base,
    allowed_leaves_per_month: 2,
    days_absent: 0,
    excess_leaves: 0,
    excess_leave_action: 'deduct',
    deduction_paise: 0,
    bonus_or_extra_paise: 0,
    extra_notes: null,
    final_salary_paise: base,
    sharers: cleaning ? ['alice', 'bob', 'chris'] : ['alice', 'bob'],
    shares: cleaning ? { alice: 100000, bob: 100000, chris: 100000 } : { alice: 225000, bob: 225000 },
    saved: true,
    ...overrides,
  }
}

function share(id: keyof typeof people, overrides: Record<string, unknown> = {}) {
  const cooks = id !== 'chris'
  const total = cooks ? 325000 : 100000
  return {
    resident_id: id,
    display_name: people[id],
    total_paise: total,
    paid_paise: 0,
    settled_paise: 0,
    outstanding_paise: total,
    status: 'unpaid',
    paid_date: null,
    payments: [],
    breakdown: [
      { role_id: 'cleaning', role_name: 'Cleaning', worker_id: 'sunita', worker_name: 'Sunita', share_paise: 100000 },
      ...(cooks
        ? [{ role_id: 'cooking', role_name: 'Cooking', worker_id: 'sunita', worker_name: 'Sunita', share_paise: 225000 }]
        : []),
    ],
    net_balance_paise: -total,
    ...overrides,
  }
}

function worker(overrides: Record<string, unknown> = {}) {
  return {
    worker_id: 'sunita',
    name: 'Sunita',
    phone: '98100 12345',
    upi_id: 'sunita@okaxis',
    is_active: true,
    role_ids: ['cleaning', 'cooking'],
    total_due_paise: 750000,
    paid_to_worker_paise: 0,
    still_owed_paise: 750000,
    payout_status: 'pending',
    payouts: [],
    ...overrides,
  }
}

const overview = (overrides: Record<string, unknown> = {}) => ({
  settlement_month: MONTH,
  days_in_month: 30,
  service: {
    service_id: 'svc-h',
    service_type: 'domestic_help',
    display_name: 'House Help',
    status: 'active',
    expense_definition_id: 'house-help',
    created_at: '2026-11-01T10:00:00+05:30',
    updated_at: '2026-11-01T10:00:00+05:30',
  },
  active_worker_count: 1,
  saved: true,
  bill_state: 'live',
  can_save: true,
  statement_status: null,
  transactions_open: true,
  participants: [
    { resident_id: 'alice', display_name: 'Alice' },
    { resident_id: 'bob', display_name: 'Bob' },
    { resident_id: 'chris', display_name: 'Chris' },
  ],
  roles: [role('cleaning'), role('cooking')],
  roles_not_on_month: [],
  unassigned_roles: [],
  total_paise: 750000,
  blocked_reason: null,
  workers: [worker()],
  shares: [share('alice'), share('bob'), share('chris')],
  expense: { expense_id: 'househelp-1', description: 'House Help for November 2026', amount_paise: 750000, statement_id: null },
  foreign_expense: null,
  collected_paise: 0,
  collected_count: 0,
  payout_status: 'pending',
  disbursed_paise: 0,
  remaining_disbursement_paise: 750000,
  reimbursement: null,
  reimbursements: [],
  ...overrides,
})

/** The staff as `GET /workers` lists them. */
const staffRole = (id: 'cleaning' | 'cooking') => ({
  role_id: id,
  worker_id: 'sunita',
  worker_name: 'Sunita',
  role_name: id === 'cleaning' ? 'Cleaning' : 'Cooking',
  monthly_salary_paise: id === 'cleaning' ? 300000 : 450000,
  split_method: id === 'cleaning' ? 'equal' : 'opt_in',
  participant_ids: id === 'cleaning' ? [] : ['alice', 'bob'],
  participants:
    id === 'cleaning'
      ? []
      : [
          { resident_id: 'alice', display_name: 'Alice' },
          { resident_id: 'bob', display_name: 'Bob' },
        ],
  created_at: '2026-11-01T10:00:00+05:30',
})

const staff = {
  items: [
    {
      worker_id: 'sunita',
      name: 'Sunita',
      phone: '98100 12345',
      upi_id: 'sunita@okaxis',
      notes: 'Mornings',
      is_active: true,
      allowed_leaves_per_month: 2,
      created_at: '2026-11-01T10:00:00+05:30',
      updated_at: '2026-11-01T10:00:00+05:30',
      roles: [staffRole('cleaning'), staffRole('cooking')],
    },
  ],
  unassigned_roles: [] as unknown[],
}

/** Utensils, set up before anyone is hired to do it. */
const utensils = {
  ...staffRole('cleaning'),
  role_id: 'utensils',
  worker_id: null,
  worker_name: null,
  role_name: 'Utensils',
  monthly_salary_paise: 150000,
}

function Where() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname + location.search}</output>
}

function show(payload: Record<string, unknown>, routes: Record<string, unknown> = {}, path = '/') {
  stub = installFetchStub({
    routes: {
      [`${BASE}/month/${MONTH}`]: payload,
      [`${BASE}/workers`]: staff,
      '/api/residents': RESIDENTS,
      [`POST ${BASE}/month/${MONTH}/attendance`]: overview(),
      [`POST ${BASE}/record-payment`]: overview(),
      [`POST ${BASE}/disburse-payout`]: overview(),
      [`POST ${BASE}/reimburse-from-kitty`]: overview(),
      [`POST ${BASE}/workers`]: { ...staff.items[0], worker_id: 'ramesh', name: 'Ramesh', roles: [] },
      [`POST ${BASE}/roles`]: { ...staffRole('cleaning'), role_id: 'ironing', role_name: 'Ironing' },
      [`PATCH ${BASE}/roles/cooking`]: staffRole('cooking'),
      [`PUT ${BASE}/roles/cooking/participants`]: staffRole('cooking'),
      ...routes,
    } as never,
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <HouseHelp month={MONTH} />
        <Where />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sentAll = (method: string, path: string) =>
  stub!.calls.filter((call) => call.method === method && call.path.split('?')[0] === path)
const sent = (method: string, path: string) => sentAll(method, path)[0]

describe('HouseHelp', () => {
  it('opens on the monthly settlement and moves between tabs through the address', async () => {
    show(overview())
    const tabs = await screen.findByRole('tablist', { name: 'House Help & Cook' })
    expect(within(tabs).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Monthly Settlement',
      'Attendance & Salaries',
      'Staff & Roles',
    ])
    expect(within(tabs).getByRole('tab', { name: 'Monthly Settlement' })).toHaveAttribute('aria-selected', 'true')
    expect(within(screen.getByRole('tabpanel')).getAllByRole('heading')[0]).toHaveTextContent('Resident Collection')

    await userEvent.click(within(tabs).getByRole('tab', { name: 'Attendance & Salaries' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=attendance')
    expect(await screen.findByRole('table', { name: 'Helper attendance for November 2026' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Role salaries for November 2026' })).toBeInTheDocument()

    await userEvent.click(within(tabs).getByRole('tab', { name: 'Staff & Roles' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=staff')
    expect(await screen.findByRole('table', { name: "The household's helpers" })).toBeInTheDocument()

    await userEvent.click(within(tabs).getByRole('tab', { name: 'Monthly Settlement' }))
    expect(screen.getByLabelText('location')).toHaveTextContent(/^\/$/)
  })

  it("lists each flatmate's share by role, and each worker's salary", async () => {
    show(overview())
    const shares = await screen.findByRole('table', { name: 'House help shares for November 2026' })
    expect(within(shares).getByRole('row', { name: /Alice/ })).toHaveTextContent(
      'Alice₹3,250.00Cleaning + Cooking₹0.00₹3,250.00Unpaid',
    )
    expect(within(shares).getByRole('row', { name: /Chris/ })).toHaveTextContent('Chris₹1,000.00Cleaning₹0.00')
    const salaries = screen.getByRole('table', { name: 'House help salaries for November 2026' })
    const sunita = within(salaries).getByRole('row', { name: /Sunita/ })
    expect(sunita).toHaveTextContent('Sunita98100 12345 · sunita@okaxis₹7,500.00₹0.00₹7,500.00Unpaid')
    expect(within(sunita).getByRole('button', { name: 'Pay now to Sunita' })).toBeEnabled()
  })

  it('previews a month not saved yet, with nothing to pay', async () => {
    show(
      overview({
        saved: false,
        bill_state: 'not_billed',
        expense: null,
        roles: [role('cleaning', { saved: false }), role('cooking', { saved: false })],
        workers: [worker({ payout_status: null, still_owed_paise: 0 })],
        shares: [share('alice', { status: null }), share('bob', { status: null }), share('chris', { status: null })],
      }),
    )
    expect(
      await screen.findByText("Not saved yet: a preview at full salary. Save November 2026's attendance to bill it."),
    ).toBeInTheDocument()
    const shares = screen.getByRole('table', { name: 'House help shares for November 2026' })
    expect(within(shares).getByRole('button', { name: 'Pay now for Alice' })).toBeDisabled()
    const salaries = screen.getByRole('table', { name: 'House help salaries for November 2026' })
    expect(within(salaries).getByRole('row', { name: /Sunita/ })).toHaveTextContent('Not billed')
  })

  it("opens a flatmate's statement from their row, and a payment can be deleted", async () => {
    show(
      overview({
        shares: [
          share('alice', {
            settled_paise: 100000,
            outstanding_paise: 225000,
            status: 'partial',
            payments: [
              {
                payment_id: 'pay-1',
                payment_date: '2026-11-05',
                amount_paise: 100000,
                payment_mode: 'upi',
                reference: 'UPI-7',
                note: null,
                payout_id: null,
              },
            ],
          }),
          share('bob'),
          share('chris'),
        ],
      }),
      { [`DELETE ${BASE}/payments/pay-1`]: overview() },
    )
    const shares = await screen.findByRole('table', { name: 'House help shares for November 2026' })
    await userEvent.click(within(shares).getByRole('row', { name: /Alice/ }))
    const statement = screen.getByRole('dialog', { name: 'House Help Statement' })
    expect(statement).toHaveTextContent('Alice · November 2026')
    expect(statement).toHaveTextContent('Cleaning: ₹1,000.00 · Cooking: ₹2,250.00')
    expect(within(statement).getByRole('table', { name: "Payments towards Alice's house help share" })).toHaveTextContent(
      '5 Nov 2026₹1,000.00UPIUPI-7',
    )
    await userEvent.click(within(statement).getByRole('button', { name: 'Delete the ₹1,000.00 payment of 5 Nov 2026' }))
    const confirm = screen.getByRole('dialog', { name: 'Delete this payment?' })
    expect(confirm).toHaveTextContent("Alice's ₹1,000.00 payment into the House Kitty of 5 Nov 2026.")
    await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(sent('DELETE', `${BASE}/payments/pay-1`)).toBeDefined())
    expect(await screen.findByText('Payment deleted')).toBeInTheDocument()
  })

  it("records a flatmate's payment into the House Kitty", async () => {
    show(overview())
    const shares = await screen.findByRole('table', { name: 'House help shares for November 2026' })
    await userEvent.click(within(shares).getByRole('button', { name: 'Pay now for Bob' }))
    const dialog = screen.getByRole('dialog', { name: 'Record Payment into House Kitty' })
    expect(dialog).toHaveTextContent('Bob · November 2026 house help share · ₹3,250.00 outstanding')
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('3250.00')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UPI-55')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payment' }))
    await waitFor(() => expect(sent('POST', `${BASE}/record-payment`)).toBeDefined())
    expect(sent('POST', `${BASE}/record-payment`)!.body).toMatchObject({
      settlement_month: MONTH,
      resident_id: 'bob',
      amount_paise: 325000,
      payment_mode: 'upi',
      reference: 'UPI-55',
    })
    expect(await screen.findByText('Payment recorded')).toBeInTheDocument()
  })

  it('pays a worker, by a flatmate whom the kitty pays back the rest', async () => {
    show(overview())
    const salaries = await screen.findByRole('table', { name: 'House help salaries for November 2026' })
    await userEvent.click(within(salaries).getByRole('button', { name: 'Pay now to Sunita' }))
    const dialog = screen.getByRole('dialog', { name: 'Pay Worker' })
    expect(dialog).toHaveTextContent('November 2026 · ₹7,500.00 still owed to Sunita')
    // Where to send it: her UPI ID.
    expect(within(dialog).getByRole('region', { name: 'Payment destination' })).toHaveTextContent('sunita@okaxis')
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveValue('7500.00')
    await userEvent.click(within(dialog).getByRole('radio', { name: /Paid by a resident/ }))
    expect(dialog).toHaveTextContent('A resident paid the worker from their own account.')
    await userEvent.selectOptions(within(dialog).getByRole('combobox', { name: /^Paid by/ }), 'bob')
    await userEvent.type(within(dialog).getByLabelText(/Reference \/ UTR/), 'UPI-SAL-11')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record Payout' }))
    await waitFor(() => expect(sent('POST', `${BASE}/disburse-payout`)).toBeDefined())
    expect(sent('POST', `${BASE}/disburse-payout`)!.body).toMatchObject({
      settlement_month: MONTH,
      worker_id: 'sunita',
      disbursement_mode: 'resident_advance',
      funding_resident_id: 'bob',
      reference: 'UPI-SAL-11',
      amount_paise: 750000,
    })
    expect(await screen.findByText('Worker payout recorded')).toBeInTheDocument()
  })

  it("opens a worker's payout statement from their row", async () => {
    const payouts = [
      {
        payout_id: 'svcdisb_1',
        worker_id: 'sunita',
        amount_paise: 300000,
        paid_on: '2026-11-03',
        reference: 'UTR-1',
        paid_by: 'kitty',
        resident_id: null,
        resident_name: null,
        note: null,
      },
      {
        payout_id: 'svcdisb_payment_2',
        worker_id: 'sunita',
        amount_paise: 450000,
        paid_on: '2026-11-07',
        reference: 'UPI-2',
        paid_by: 'resident',
        resident_id: 'bob',
        resident_name: 'Bob',
        note: null,
      },
    ]
    show(
      overview({
        workers: [
          worker({ paid_to_worker_paise: 750000, still_owed_paise: 0, payout_status: 'disbursed', payouts }),
        ],
      }),
      { [`DELETE ${BASE}/month/${MONTH}/disbursements/svcdisb_payment_2`]: overview() },
    )
    const salaries = await screen.findByRole('table', { name: 'House help salaries for November 2026' })
    const row = within(salaries).getByRole('row', { name: /Sunita/ })
    expect(within(row).getByRole('button', { name: 'Pay now to Sunita' })).toBeDisabled()
    await userEvent.click(row)
    const statement = screen.getByRole('dialog', { name: 'Worker Payout Statement' })
    const history = within(statement).getByRole('table', { name: 'Payouts to Sunita' })
    expect(within(history).getAllByRole('row').slice(1).map((line) => line.textContent)).toEqual([
      '3 Nov 2026₹3,000.00House KittyUTR-1—Delete',
      '7 Nov 2026₹4,500.00Bob (advance)UPI-2—Delete',
    ])
    await userEvent.click(within(statement).getByRole('button', { name: 'Delete the ₹4,500.00 payout of 7 Nov 2026' }))
    const confirm = screen.getByRole('dialog', { name: 'Delete this payout?' })
    expect(confirm).toHaveTextContent(
      "Bob's ₹4,500.00 payout to Sunita of 7 Nov 2026, with their share paid through the House Kitty.",
    )
    await userEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() =>
      expect(sent('DELETE', `${BASE}/month/${MONTH}/disbursements/svcdisb_payment_2`)).toBeDefined(),
    )
  })

  it('pays back from the kitty a flatmate who paid a worker beyond their share', async () => {
    const owed = {
      recipient_resident_id: 'bob',
      recipient_name: 'Bob',
      total_fronted_paise: 750000,
      payer_share_paise: 325000,
      net_reimbursement_owed_paise: 425000,
      reimbursed_so_far_paise: 0,
      pending_reimbursement_paise: 425000,
      kitty_cash_available_paise: 425000,
      reimbursable_paise: 425000,
      can_reimburse: true,
      reimbursements: [],
    }
    show(overview({ reimbursements: [owed], reimbursement: owed }))
    const panel = (await screen.findByRole('heading', { name: 'Reimbursement' })).closest('section')!
    await userEvent.click(within(panel).getByRole('button', { name: /Reimburse/ }))
    const dialog = screen.getByRole('dialog', { name: 'Reimburse Bob' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record reimbursement' }))
    await waitFor(() => expect(sent('POST', `${BASE}/reimburse-from-kitty`)).toBeDefined())
    const body = sent('POST', `${BASE}/reimburse-from-kitty`)!.body as Record<string, unknown>
    expect(body).toMatchObject({ settlement_month: MONTH, recipient_resident_id: 'bob', amount_paise: 425000 })
    // The day it was paid back goes as the endpoint names it.
    expect(body.disbursed_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(body).not.toHaveProperty('payment_date')
    expect(body).not.toHaveProperty('note')
  })

  it("saves a month's attendance: a helper's leave deducted from each role or waived, a bonus on top", async () => {
    show(overview(), {}, '/?tab=attendance')
    // Each helper's days in one table; each role's salary in the other.
    const attendance = await screen.findByRole('table', { name: 'Helper attendance for November 2026' })
    const salaries = screen.getByRole('table', { name: 'Role salaries for November 2026' })
    const sunita = within(attendance).getByRole('row', { name: /^Sunita/ })
    const cleaning = within(salaries).getByRole('row', { name: /^Cleaning/ })
    const cooking = within(salaries).getByRole('row', { name: /^Cooking/ })
    const total = within(salaries).getByRole('row', { name: /^Total/ })
    // As saved, until edited.
    expect(sunita).toHaveTextContent('Sunita2 days allowed0Deduct pro-rata')
    expect(cleaning).toHaveTextContent('CleaningSunita₹3,000.00——₹3,000.00')
    expect(total).toHaveTextContent('Total₹7,500.00——₹7,500.00')
    expect(within(attendance).queryByRole('spinbutton')).toBeNull()
    expect(within(salaries).queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Save/ })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Edit Attendance' }))
    expect(screen.queryByRole('button', { name: 'Edit Attendance' })).toBeNull()
    const save = screen.getByRole('button', { name: 'Save Attendance' })
    // Nothing changed yet.
    expect(save).toBeDisabled()
    // Away five days, two allowed: three days off each role, at that role's day's pay.
    const days = within(sunita).getByLabelText(/Sunita days absent/)
    await userEvent.clear(days)
    await userEvent.type(days, '5')
    expect(cleaning).toHaveTextContent('₹3,000.00−₹300.00for 3 days')
    expect(cleaning).toHaveTextContent('₹2,700.00')
    expect(cooking).toHaveTextContent('₹4,500.00−₹450.00for 3 days')
    expect(cooking).toHaveTextContent('₹4,050.00')
    expect(total).toHaveTextContent('Total₹7,500.00−₹750.00—₹6,750.00')
    expect(within(salaries).queryByLabelText(/days absent/)).toBeNull()
    expect(screen.getByText('Unsaved changes: the salaries below are a preview until you save.')).toBeInTheDocument()

    // Cancelled, it is as saved again.
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(sunita).toHaveTextContent('Sunita2 days allowed0Deduct pro-rata')
    expect(total).toHaveTextContent('Total₹7,500.00——₹7,500.00')

    // Five days, waived for both roles.
    await userEvent.click(screen.getByRole('button', { name: 'Edit Attendance' }))
    const again = within(sunita).getByLabelText(/Sunita days absent/)
    expect(again).toHaveValue(0)
    await userEvent.clear(again)
    await userEvent.type(again, '5')
    await userEvent.selectOptions(
      within(sunita).getByRole('combobox', { name: /Sunita leave beyond the allowance/ }),
      'waive',
    )
    expect(total).toHaveTextContent('Total₹7,500.00——₹7,500.00')
    // Too many days: nothing to save until it is a day of the month.
    await userEvent.clear(again)
    await userEvent.type(again, '31')
    expect(sunita).toHaveTextContent('0 to 30')
    expect(screen.getByRole('button', { name: 'Save Attendance' })).toBeDisabled()
    await userEvent.clear(again)
    await userEvent.type(again, '5')

    await userEvent.click(screen.getByRole('button', { name: 'Save Attendance' }))
    await waitFor(() => expect(sent('POST', `${BASE}/month/${MONTH}/attendance`)).toBeDefined())
    // Her days on each of her roles, and the bonuses as saved.
    expect(sent('POST', `${BASE}/month/${MONTH}/attendance`)!.body).toEqual({
      records: [
        {
          role_id: 'cleaning',
          days_absent: 5,
          excess_leave_action: 'waive',
          bonus_or_extra_paise: 0,
          deduction_paise: null,
          extra_notes: null,
        },
        {
          role_id: 'cooking',
          days_absent: 5,
          excess_leave_action: 'waive',
          bonus_or_extra_paise: 0,
          deduction_paise: null,
          extra_notes: null,
        },
      ],
    })
    expect(await screen.findByText('Attendance saved')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Attendance' })).toBeNull()

    // A ₹200 bonus on the cleaning, saved on its own with the attendance as saved.
    await userEvent.click(screen.getByRole('button', { name: 'Edit Salaries & Bonuses' }))
    expect(within(attendance).queryByRole('spinbutton')).toBeNull()
    await userEvent.type(within(cleaning).getByLabelText(/Cleaning \(Sunita\) bonus or extra/), '200')
    expect(cleaning).toHaveTextContent('₹3,200.00')
    expect(cooking).toHaveTextContent('₹4,500.00')
    expect(total).toHaveTextContent('Total₹7,500.00—₹200.00₹7,700.00')
    await userEvent.click(screen.getByRole('button', { name: 'Save Salaries & Bonuses' }))
    await waitFor(() => expect(sentAll('POST', `${BASE}/month/${MONTH}/attendance`)).toHaveLength(2))
    expect(sentAll('POST', `${BASE}/month/${MONTH}/attendance`)[1].body).toEqual({
      records: [
        {
          role_id: 'cleaning',
          days_absent: 0,
          excess_leave_action: 'deduct',
          bonus_or_extra_paise: 20000,
          deduction_paise: null,
          extra_notes: null,
        },
        {
          role_id: 'cooking',
          days_absent: 0,
          excess_leave_action: 'deduct',
          bonus_or_extra_paise: 0,
          deduction_paise: null,
          extra_notes: null,
        },
      ],
    })
    expect(await screen.findByText('Salaries & bonuses saved')).toBeInTheDocument()
  })

  it("keeps each helper's days their own", async () => {
    const bathrooms = role('cleaning', {
      role_id: 'bathrooms',
      role_name: 'Bathrooms',
      worker_id: 'ramesh',
      worker_name: 'Ramesh',
      allowed_leaves_per_month: 0,
      base_salary_paise: 120000,
      final_salary_paise: 120000,
    })
    show(overview({ roles: [role('cleaning'), bathrooms], total_paise: 420000 }), {}, '/?tab=attendance')
    const attendance = await screen.findByRole('table', { name: 'Helper attendance for November 2026' })
    const salaries = screen.getByRole('table', { name: 'Role salaries for November 2026' })
    expect(
      within(attendance)
        .getAllByRole('row')
        .slice(1)
        .map((row) => [row.querySelector('th')?.textContent, row.querySelector('td')?.textContent]),
    ).toEqual([
      ['Sunita', '2 days allowed'],
      ['Ramesh', 'No leave allowed'],
    ])
    // Ramesh away a day: only his role pays for it, ₹40 of a ₹1,200 month.
    await userEvent.click(screen.getByRole('button', { name: 'Edit Attendance' }))
    const ramesh = within(attendance).getByRole('row', { name: /^Ramesh/ })
    const days = within(ramesh).getByLabelText(/Ramesh days absent/)
    await userEvent.clear(days)
    await userEvent.type(days, '1')
    expect(within(salaries).getByRole('row', { name: /^Bathrooms/ })).toHaveTextContent('BathroomsRamesh₹1,200.00−₹40.00for 1 day')
    expect(within(salaries).getByRole('row', { name: /^Cleaning/ })).toHaveTextContent('CleaningSunita₹3,000.00—')
    await userEvent.click(screen.getByRole('button', { name: 'Save Attendance' }))
    await waitFor(() => expect(sent('POST', `${BASE}/month/${MONTH}/attendance`)).toBeDefined())
    const body = sent('POST', `${BASE}/month/${MONTH}/attendance`)!.body as {
      records: Array<{ role_id: string; days_absent: number }>
    }
    expect(body.records.map((record) => [record.role_id, record.days_absent])).toEqual([
      ['cleaning', 0],
      ['bathrooms', 1],
    ])
  })

  it('saves a month not saved yet, and adds a role new this month to one that is', async () => {
    show(
      overview({
        saved: false,
        bill_state: 'not_billed',
        expense: null,
        roles: [role('cleaning', { saved: false })],
      }),
      {},
      '/?tab=attendance',
    )
    expect(
      await screen.findByText('Not saved yet: November 2026 is billed once its attendance or salaries are saved.'),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Edit Attendance' }))
    expect(screen.getByText('Not saved yet: saving bills November 2026.')).toBeInTheDocument()
    // Nothing to change, but saving bills the month.
    await userEvent.click(screen.getByRole('button', { name: 'Save Attendance' }))
    await waitFor(() => expect(sent('POST', `${BASE}/month/${MONTH}/attendance`)).toBeDefined())
    expect(sent('POST', `${BASE}/month/${MONTH}/attendance`)!.body).toMatchObject({
      records: [{ role_id: 'cleaning', days_absent: 0, excess_leave_action: 'deduct' }],
    })
  })

  it('offers a role not on a saved month, and saves it with the month', async () => {
    const { saved: _saved, sharers: _sharers, shares: _shares, ...cooking } = role('cooking')
    show(
      overview({
        roles: [role('cleaning')],
        roles_not_on_month: [cooking],
        unassigned_roles: [{ role_id: 'utensils', role_name: 'Utensils', monthly_salary_paise: 150000 }],
      }),
      {},
      '/?tab=attendance',
    )
    const salaries = await screen.findByRole('table', { name: 'Role salaries for November 2026' })
    // With an active helper, a role waiting for one is left to the Staff tab.
    expect(screen.queryByText(/No active helpers are currently assigned/)).toBeNull()
    expect(within(salaries).queryByRole('row', { name: /^Cooking/ })).toBeNull()
    // Offered while the salaries are edited.
    expect(screen.queryByRole('button', { name: 'Add Cooking (Sunita)' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Edit Salaries & Bonuses' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add Cooking (Sunita)' }))
    expect(within(salaries).getByRole('row', { name: /^Cooking/ })).toHaveTextContent('CookingSunita · new this month')
    // The same helper: still one row of attendance.
    expect(within(screen.getByRole('table', { name: 'Helper attendance for November 2026' })).getAllByRole('row')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: 'Save Salaries & Bonuses' }))
    await waitFor(() => expect(sent('POST', `${BASE}/month/${MONTH}/attendance`)).toBeDefined())
    const body = sent('POST', `${BASE}/month/${MONTH}/attendance`)!.body as { records: Array<{ role_id: string }> }
    expect(body.records.map((record) => record.role_id)).toEqual(['cleaning', 'cooking'])
  })

  it('shows a finalized month as it was saved, with nothing to change', async () => {
    show(
      overview({
        bill_state: 'frozen',
        can_save: false,
        statement_status: 'finalized_correction_window',
        roles: [
          role('cleaning', {
            days_absent: 5,
            excess_leaves: 3,
            deduction_paise: 30000,
            final_salary_paise: 270000,
          }),
          role('cooking', {
            days_absent: 5,
            excess_leaves: 3,
            deduction_paise: 45000,
            final_salary_paise: 405000,
          }),
        ],
      }),
      {},
      '/?tab=attendance',
    )
    const attendance = await screen.findByRole('table', { name: 'Helper attendance for November 2026' })
    const salaries = screen.getByRole('table', { name: 'Role salaries for November 2026' })
    expect(screen.getByText('The November 2026 statement has been finalized: its salaries are final.')).toBeInTheDocument()
    for (const table of [attendance, salaries]) {
      expect(within(table).queryByRole('textbox')).toBeNull()
      expect(within(table).queryByRole('spinbutton')).toBeNull()
      expect(within(table).queryByRole('combobox')).toBeNull()
    }
    expect(within(attendance).getByRole('row', { name: /^Sunita/ })).toHaveTextContent(
      'Sunita2 days allowed5Deduct pro-rata',
    )
    expect(within(salaries).getByRole('row', { name: /^Cleaning/ })).toHaveTextContent(
      'CleaningSunita₹3,000.00−₹300.00for 3 days—₹2,700.00',
    )
    expect(within(salaries).getByRole('row', { name: /^Total/ })).toHaveTextContent('Total₹7,500.00−₹750.00—₹6,750.00')
    expect(screen.queryByRole('button', { name: /^Edit/ })).toBeNull()
  })

  it('lists the helpers and their roles, and adds a helper', async () => {
    show(overview(), {}, '/?tab=staff')
    const helpers = await screen.findByRole('table', { name: "The household's helpers" })
    expect(within(helpers).getByRole('row', { name: /Sunita/ })).toHaveTextContent(
      'Sunita98100 12345 · sunita@okaxisMornings2 days allowedActive',
    )
    const roles = screen.getByRole('table', { name: "The helpers' roles" })
    // The role, who does it, and its salary: the split is in the role's dialog.
    expect(within(roles).getByRole('row', { name: /Cooking/ })).toHaveTextContent(/^CookingSunita₹4,500\.00$/)
    expect(within(roles).getByRole('row', { name: /Cleaning/ })).toHaveTextContent(/^CleaningSunita₹3,000\.00$/)

    await userEvent.click(screen.getByRole('button', { name: 'Add Helper' }))
    const dialog = screen.getByRole('dialog', { name: 'Add Helper' })
    expect(within(dialog).queryByRole('switch')).toBeNull()
    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Ramesh')
    await userEvent.type(within(dialog).getByLabelText(/^Phone/), '98990 11111')
    // Two days off a month unless said otherwise; days only.
    const leaves = within(dialog).getByLabelText(/^Allowed leaves per month/)
    expect(leaves).toHaveValue(2)
    await userEvent.clear(leaves)
    await userEvent.type(leaves, '32')
    expect(within(dialog).getByRole('button', { name: 'Add Helper' })).toBeDisabled()
    await userEvent.clear(leaves)
    await userEvent.type(leaves, '3')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add Helper' }))
    await waitFor(() => expect(sent('POST', `${BASE}/workers`)).toBeDefined())
    expect(sent('POST', `${BASE}/workers`)!.body).toEqual({
      name: 'Ramesh',
      phone: '98990 11111',
      upi_id: null,
      notes: null,
      allowed_leaves_per_month: 3,
    })
    expect(await screen.findByText('Helper added')).toBeInTheDocument()
  })

  it('makes a helper who has left inactive', async () => {
    show(overview(), { [`PATCH ${BASE}/workers/sunita`]: { ...staff.items[0], is_active: false } }, '/?tab=staff')
    const helpers = await screen.findByRole('table', { name: "The household's helpers" })
    await userEvent.click(within(helpers).getByRole('row', { name: /Sunita/ }))
    const dialog = screen.getByRole('dialog', { name: 'Edit Helper' })
    const status = within(dialog).getByRole('switch', { name: 'Helper status: Active' })
    expect(status).toBeChecked()
    expect(status).toHaveAccessibleDescription('Inactive helpers do not join new monthly calculations.')
    await userEvent.click(status)
    expect(within(dialog).getByRole('switch', { name: 'Helper status: Inactive' })).not.toBeChecked()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(sent('PATCH', `${BASE}/workers/sunita`)).toBeDefined())
    expect(sent('PATCH', `${BASE}/workers/sunita`)!.body).toEqual({
      name: 'Sunita',
      phone: '98100 12345',
      upi_id: 'sunita@okaxis',
      notes: 'Mornings',
      allowed_leaves_per_month: 2,
      is_active: false,
    })
  })

  it('adds a role shared only by the flatmates who opted in', async () => {
    show(overview(), {}, '/?tab=staff')
    await screen.findByRole('table', { name: "The helpers' roles" })
    await userEvent.click(screen.getByRole('button', { name: 'Add Role' }))
    const dialog = screen.getByRole('dialog', { name: 'Add Role' })
    await userEvent.type(within(dialog).getByLabelText(/^Role name/), 'Ironing')
    // The only helper is chosen already; leave is theirs, not the role's.
    expect(within(dialog).getByRole('combobox', { name: /^Worker/ })).toHaveValue('sunita')
    expect(within(dialog).queryByLabelText(/Allowed leaves/)).toBeNull()
    await userEvent.type(within(dialog).getByLabelText(/^Base salary/), '800')
    await userEvent.click(within(dialog).getByRole('radio', { name: /Opt-in flatmates/ }))
    const add = within(dialog).getByRole('button', { name: 'Add Role' })
    expect(within(dialog).getByText('Choose at least one flatmate.')).toBeInTheDocument()
    expect(add).toBeDisabled()
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: 'Chris' }))
    await userEvent.click(add)
    await waitFor(() => expect(sent('POST', `${BASE}/roles`)).toBeDefined())
    expect(sent('POST', `${BASE}/roles`)!.body).toEqual({
      worker_id: 'sunita',
      role_name: 'Ironing',
      monthly_salary_paise: 80000,
      split_method: 'opt_in',
      participants: ['chris'],
    })
    expect(await screen.findByText('Role added')).toBeInTheDocument()
  })

  it('changes who shares a role, and deletes one', async () => {
    show(overview(), { [`DELETE ${BASE}/roles/cooking`]: { workers: staff.items, unassigned_roles: [] } }, '/?tab=staff')
    const roles = await screen.findByRole('table', { name: "The helpers' roles" })
    await userEvent.click(within(roles).getByRole('row', { name: /Cooking/ }))
    const dialog = screen.getByRole('dialog', { name: 'Edit Role' })
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: 'Chris' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(sent('PUT', `${BASE}/roles/cooking/participants`)).toBeDefined())
    expect(sent('PATCH', `${BASE}/roles/cooking`)!.body).toEqual({
      role_name: 'Cooking',
      monthly_salary_paise: 450000,
      split_method: 'opt_in',
    })
    expect(sent('PUT', `${BASE}/roles/cooking/participants`)!.body).toEqual({ participants: ['alice', 'bob', 'chris'] })
    expect(await screen.findByText('Role saved')).toBeInTheDocument()

    await userEvent.click(within(roles).getByRole('row', { name: /Cooking/ }))
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Edit Role' })).getByRole('button', { name: 'Delete Role' }))
    const confirm = screen.getByRole('dialog', { name: 'Delete this role?' })
    expect(confirm).toHaveTextContent('Cooking (Sunita), ₹4,500.00 a month.')
    await userEvent.click(within(confirm).getByRole('button', { name: 'Delete Role' }))
    await waitFor(() => expect(sent('DELETE', `${BASE}/roles/cooking`)).toBeDefined())
    expect(await screen.findByText('Role deleted')).toBeInTheDocument()
  })

  it('adds a role with no helper yet', async () => {
    show(overview(), { [`POST ${BASE}/roles`]: utensils }, '/?tab=staff')
    await screen.findByRole('table', { name: "The helpers' roles" })
    await userEvent.click(screen.getByRole('button', { name: 'Add Role' }))
    const dialog = screen.getByRole('dialog', { name: 'Add Role' })
    await userEvent.type(within(dialog).getByLabelText(/^Role name/), 'Utensils')
    await userEvent.selectOptions(within(dialog).getByRole('combobox', { name: /^Worker/ }), 'No helper yet')
    expect(within(dialog).getByText('On no month’s salaries until it has a helper.')).toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText(/^Base salary/), '1500')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add Role' }))
    await waitFor(() => expect(sent('POST', `${BASE}/roles`)).toBeDefined())
    expect(sent('POST', `${BASE}/roles`)!.body).toEqual({
      role_name: 'Utensils',
      monthly_salary_paise: 150000,
      split_method: 'equal',
    })
    expect(await screen.findByText('Role added')).toBeInTheDocument()
  })

  it('lists a role waiting for its helper, and gives it one', async () => {
    show(
      overview(),
      {
        [`${BASE}/workers`]: { ...staff, unassigned_roles: [utensils] },
        [`PATCH ${BASE}/roles/utensils`]: { ...utensils, worker_id: 'sunita', worker_name: 'Sunita' },
      },
      '/?tab=staff',
    )
    const roles = await screen.findByRole('table', { name: "The helpers' roles" })
    const row = within(roles).getByRole('row', { name: /Utensils/ })
    expect(row).toHaveTextContent('UtensilsNo helper yet₹1,500.00')
    await userEvent.click(row)
    const dialog = screen.getByRole('dialog', { name: 'Edit Role' })
    expect(within(dialog).getByText('Utensils · No helper yet')).toBeInTheDocument()
    await userEvent.selectOptions(within(dialog).getByRole('combobox', { name: /^Worker/ }), 'Sunita')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(sent('PATCH', `${BASE}/roles/utensils`)).toBeDefined())
    expect(sent('PATCH', `${BASE}/roles/utensils`)!.body).toEqual({
      role_name: 'Utensils',
      monthly_salary_paise: 150000,
      split_method: 'equal',
      worker_id: 'sunita',
    })
    expect(await screen.findByText('Utensils (Sunita)')).toBeInTheDocument()
  })

  it('says when no active helper is left to do the roles', async () => {
    show(
      overview({
        active_worker_count: 0,
        unassigned_roles: [{ role_id: 'utensils', role_name: 'Utensils', monthly_salary_paise: 150000 }],
      }),
      {},
      '/?tab=attendance',
    )
    expect(await screen.findByText(/No active helpers are currently assigned/)).toHaveTextContent(
      'No active helpers are currently assigned. Assign a helper to each role under Staff & Roles to track attendance and salaries.',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Staff & Roles' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=staff')
  })

  it('points to Staff & Roles before anyone has a role', async () => {
    show(
      overview({
        service: null,
        active_worker_count: 0,
        saved: false,
        bill_state: 'not_billed',
        roles: [],
        workers: [],
        shares: [],
        expense: null,
        total_paise: 0,
      }),
      { [`${BASE}/workers`]: { items: [], unassigned_roles: [] } },
    )
    expect(await screen.findByText('No helper has a role yet.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Go to Staff & Roles' }))
    expect(screen.getByLabelText('location')).toHaveTextContent('/?tab=staff')
  })
})
