/** The frame the sign-up and login pages share: the emblem and one centred card. */
import type { ReactNode } from 'react'
import { Building2 } from 'lucide-react'

export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
export const MIN_PASSWORD_LENGTH = 8

export function AuthCard({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <main className="min-h-screen flex items-center justify-center px-4 py-10 bg-[var(--bg-base)]">
      <section
        className="w-full max-w-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-6 sm:p-8 shadow-xs space-y-6"
        aria-labelledby="auth-title"
      >
        <div className="space-y-3">
          <span className="flex w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-700 to-indigo-500 text-white items-center justify-center shadow-xs">
            <Building2 className="w-5 h-5 shrink-0" aria-hidden="true" />
          </span>
          <div className="space-y-1">
            <h1 id="auth-title" className="m-0 text-xl font-bold tracking-tight text-[var(--text-primary)]">
              {title}
            </h1>
            <p className="m-0 text-sm text-[var(--text-secondary)]">{subtitle}</p>
          </div>
        </div>
        {children}
      </section>
    </main>
  )
}
