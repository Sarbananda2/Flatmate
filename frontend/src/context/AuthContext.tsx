/**
 * Who is signed in, and the means to sign up, log in and log out.
 *
 * The session itself is an HTTP-only cookie the browser sends with every API
 * call; this only tracks what the server says about it. When any call is
 * turned away for want of a session, the user is treated as signed out.
 */
import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { ApiError, UNAUTHORIZED_EVENT, api } from '../api/client'
import type { AuthStatus, AuthUser } from '../api/types'

export interface AuthContextValue {
  user: AuthUser | null
  /** Nobody has signed up yet, so the first visitor creates the household admin. */
  setupRequired: boolean
  /** Still asking the server who is signed in. */
  loading: boolean
  /** Why the server could not be asked, when it could not. */
  error: ApiError | null
  retry: () => void
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  signupAdmin: (data: { email: string; fullName: string; password: string }) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [setupRequired, setSetupRequired] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let current = true
    setLoading(true)
    api
      .get<AuthStatus>('/api/auth/status')
      .then((status) => {
        if (!current) return
        setUser(status.user)
        setSetupRequired(status.setup_required)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (!current) return
        setError(cause instanceof ApiError ? cause : new ApiError(0, {
          code: 'unexpected_error', message: String(cause), details: {},
        }))
      })
      .finally(() => {
        if (current) setLoading(false)
      })
    return () => {
      current = false
    }
  }, [attempt])

  useEffect(() => {
    const signedOut = () => setUser(null)
    window.addEventListener(UNAUTHORIZED_EVENT, signedOut)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, signedOut)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const { user: signedIn } = await api.post<{ user: AuthUser }>('/api/auth/login', {
      email: email.trim(),
      password,
    })
    setUser(signedIn)
    setSetupRequired(false)
  }, [])

  const signupAdmin = useCallback(
    async ({ email, fullName, password }: { email: string; fullName: string; password: string }) => {
      const { user: created } = await api.post<{ user: AuthUser }>('/api/auth/signup', {
        full_name: fullName.trim(),
        email: email.trim(),
        password,
      })
      setUser(created)
      setSetupRequired(false)
    },
    [],
  )

  const logout = useCallback(async () => {
    try {
      await api.post('/api/auth/logout')
    } finally {
      // Signed out here even if the server could not be told: the cookie is
      // the server's to clear, and a lapsed one is refused anyway.
      setUser(null)
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      setupRequired,
      loading,
      error,
      retry: () => setAttempt((count) => count + 1),
      login,
      logout,
      signupAdmin,
    }),
    [user, setupRequired, loading, error, login, logout, signupAdmin],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside the AuthProvider')
  return context
}

/** The auth context where there is one: a page rendered on its own in a test has none. */
export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext)
}
