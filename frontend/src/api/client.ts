/**
 * Typed fetch wrapper.
 *
 * Every backend error arrives as `{ error: { code, message, details } }`, so the
 * client turns a failure into an `ApiError` carrying all three. Screens show the
 * message and can branch on the code without matching strings.
 */
import type { ApiErrorBody } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details ?? {}
  }

  /** Field-level messages from a request-validation failure, if any. */
  get fieldErrors(): Array<{ field: string; message: string }> {
    const fields = this.details?.fields
    return Array.isArray(fields) ? (fields as Array<{ field: string; message: string }>) : []
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>

function buildUrl(path: string, query?: Query): string {
  const url = new URL(path, window.location.origin)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === null || value === undefined || value === '') continue
      url.searchParams.set(key, String(value))
    }
  }
  return url.pathname + (url.search ? url.search : '')
}

async function request<T>(method: string, path: string, options: {
  query?: Query
  body?: unknown
  signal?: AbortSignal
} = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers: options.body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(0, {
      code: 'network_error',
      message:
        'Could not reach the backend. Check that it is running on the configured port.',
      details: { cause: String(cause) },
    })
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let parsed: unknown = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = null
    }
  }

  if (!response.ok) {
    const envelope = parsed as { error?: ApiErrorBody } | null
    throw new ApiError(
      response.status,
      envelope?.error ?? {
        code: `http_${response.status}`,
        message: text?.slice(0, 300) || response.statusText || 'Request failed.',
        details: {},
      },
    )
  }
  return parsed as T
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) =>
    request<T>('GET', path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>('POST', path, { body, query }),
  patch: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>('PATCH', path, { body, query }),
  put: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>('PUT', path, { body, query }),
  delete: <T>(path: string, query?: Query) => request<T>('DELETE', path, { query }),
}

/** Open a CSV download in a new tab. The backend sets the filename. */
export function downloadCsv(path: string, query?: Query): void {
  window.open(buildUrl(path, query), '_blank', 'noopener')
}

export function csvUrl(path: string, query?: Query): string {
  return buildUrl(path, query)
}
