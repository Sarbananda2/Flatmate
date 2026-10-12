/**
 * A tiny fetch stub for interaction tests.
 *
 * It answers the handful of endpoints a screen needs and records what was
 * posted, so a test can assert the exact body the UI sent — which is where a
 * money bug would show up as a float or a rounded paisa.
 */
import { vi } from 'vitest'

export interface Recorded {
  method: string
  path: string
  body: unknown
}

/** A canned body, or a handler that builds one from what was posted. */
export type RouteHandler = (body: unknown, method: string) => unknown
export type Route = RouteHandler | Record<string, unknown> | unknown[]

export interface StubOptions {
  routes: Record<string, Route>
  errors?: Record<string, { status: number; code: string; message: string; details?: unknown }>
}

export function installFetchStub(options: StubOptions): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = []
  const original = globalThis.fetch

  const resolve = (method: string, path: string) =>
    options.routes[`${method} ${path}`] ?? options.routes[path]

  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const path = url.split('?')[0]
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path, body })

    const failure = options.errors?.[`${method} ${path}`]
    if (failure) {
      return new Response(
        JSON.stringify({
          error: { code: failure.code, message: failure.message, details: failure.details ?? {} },
        }),
        { status: failure.status, headers: { 'Content-Type': 'application/json' } },
      )
    }

    const handler = resolve(method, path)
    if (handler === undefined) {
      return new Response(
        JSON.stringify({ error: { code: 'not_found', message: `No stub for ${method} ${path}`, details: {} } }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      )
    }
    const payload = typeof handler === 'function' ? handler(body, method) : handler
    return new Response(JSON.stringify(payload), {
      status: method === 'POST' ? 201 : 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  return {
    calls,
    restore: () => {
      globalThis.fetch = original
    },
  }
}

export const RESIDENTS = {
  items: [
    {
      resident_id: 'alice',
      apartment_id: 'home',
      display_name: 'Alice',
      active_from: '2026-08-01',
      active_until: null,
      is_active: 1,
      email: null,
      phone: null,
      notes: null,
      created_at: null,
      updated_at: null,
    },
    {
      resident_id: 'bob',
      apartment_id: 'home',
      display_name: 'Bob',
      active_from: '2026-08-01',
      active_until: null,
      is_active: 1,
      email: null,
      phone: null,
      notes: null,
      created_at: null,
      updated_at: null,
    },
    {
      resident_id: 'chris',
      apartment_id: 'home',
      display_name: 'Chris',
      active_from: '2026-08-01',
      active_until: null,
      is_active: 1,
      email: null,
      phone: null,
      notes: null,
      created_at: null,
      updated_at: null,
    },
  ],
  total: 3,
}

export const DEFINITIONS = {
  items: [
    {
      expense_definition_id: 'groceries',
      apartment_id: 'home',
      name: 'Groceries',
      category: 'Food',
      period_rule: 'previous_month',
      default_allocation_method: 'equal',
      is_recurring: false,
      is_active: true,
      recurrence: 'none',
      default_amount_paise: null,
      default_provider_name: null,
      default_participants: null,
      default_weights: null,
      day_of_month: null,
      notes: null,
      sort_order: 70,
    },
  ],
  total: 1,
  period_rules: ['previous_month', 'current_month', 'transaction_date', 'custom'],
  allocation_methods: ['equal', 'weighted', 'custom', 'measured', 'host_guest'],
  recurrences: ['none', 'monthly'],
}

export const NO_STATEMENTS = { items: [], total: 0, limit: 50, offset: 0, available_months: [] }
