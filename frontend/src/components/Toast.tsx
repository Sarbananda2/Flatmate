/** Transient confirmations. Errors stay inline on the form; these are receipts. */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

export interface Toast {
  id: number
  tone: 'ok' | 'error' | 'info'
  title: string
  body?: string
}

interface ToastApi {
  push: (toast: Omit<Toast, 'id'>) => void
  success: (title: string, body?: string) => void
  info: (title: string, body?: string) => void
  failure: (title: string, body?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const push = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { ...toast, id }])
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id))
    }, toast.tone === 'error' ? 8000 : 4500)
  }, [])

  const value = useMemo<ToastApi>(
    () => ({
      push,
      success: (title, body) => push({ tone: 'ok', title, body }),
      info: (title, body) => push({ tone: 'info', title, body }),
      failure: (title, body) => push({ tone: 'error', title, body }),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>
            <span aria-hidden="true">
              {toast.tone === 'ok' ? '✓' : toast.tone === 'error' ? '⚠' : 'i'}
            </span>
            <div className="toast-body">
              <div className="toast-title">{toast.title}</div>
              {toast.body && <div className="subtle">{toast.body}</div>}
            </div>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}
            >
              Dismiss
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside a ToastProvider')
  return context
}
