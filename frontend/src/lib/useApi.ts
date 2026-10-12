/**
 * Data-loading hooks.
 *
 * `useResource` keeps the loading, error, and empty states explicit so every
 * screen can render all three without inventing its own convention. A reload is
 * triggered by bumping a nonce, which also lets a mutation refresh exactly the
 * views it affected.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, type Query } from '../api/client'

export interface Resource<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  /** True only on the first load, so a refresh does not blank the screen. */
  initialLoading: boolean
  reload: () => void
}

export function useResource<T>(
  path: string | null,
  query?: Query,
  deps: unknown[] = [],
): Resource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState<boolean>(path !== null)
  const [loaded, setLoaded] = useState(false)
  const [nonce, setNonce] = useState(0)
  const queryKey = JSON.stringify(query ?? {})
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    if (path === null) {
      setLoading(false)
      return
    }
    const controller = new AbortController()
    setLoading(true)
    api
      .get<T>(path, query, controller.signal)
      .then((result) => {
        if (controller.signal.aborted || !mounted.current) return
        setData(result)
        setError(null)
        setLoaded(true)
      })
      .catch((cause) => {
        if (controller.signal.aborted || !mounted.current) return
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setError(cause instanceof ApiError ? cause : new ApiError(0, {
          code: 'unexpected_error',
          message: String(cause),
          details: {},
        }))
        setLoaded(true)
      })
      .finally(() => {
        if (!controller.signal.aborted && mounted.current) setLoading(false)
      })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, queryKey, nonce, ...deps])

  const reload = useCallback(() => setNonce((value) => value + 1), [])
  return useMemo(
    () => ({ data, error, loading, initialLoading: loading && !loaded, reload }),
    [data, error, loading, loaded, reload],
  )
}

export interface Mutation<TArgs, TResult> {
  run: (args: TArgs) => Promise<TResult | undefined>
  pending: boolean
  error: ApiError | null
  reset: () => void
}

/**
 * Wrap a write so a screen can disable its submit button, show the server's
 * message inline, and surface field-level validation without a try/catch in
 * every handler.
 */
export function useMutation<TArgs, TResult>(
  action: (args: TArgs) => Promise<TResult>,
  options: { onSuccess?: (result: TResult, args: TArgs) => void } = {},
): Mutation<TArgs, TResult> {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const onSuccess = options.onSuccess

  const run = useCallback(
    async (args: TArgs) => {
      setPending(true)
      setError(null)
      try {
        const result = await action(args)
        onSuccess?.(result, args)
        return result
      } catch (cause) {
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError(0, { code: 'unexpected_error', message: String(cause), details: {} }),
        )
        return undefined
      } finally {
        setPending(false)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [action, onSuccess],
  )

  return { run, pending, error, reset: () => setError(null) }
}

/** Debounce a value, for search boxes that would otherwise query every keystroke. */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}
