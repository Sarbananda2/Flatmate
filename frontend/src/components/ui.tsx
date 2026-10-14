/**
 * Building blocks for the token-based screens.
 *
 * These replace the class-based primitives one screen at a time. They carry
 * the two layout guards every new screen needs: text that can shrink is
 * truncated, and icons and pills never shrink.
 */
import { type ReactNode, useEffect, useState } from 'react'
import { Check, Copy, Pencil } from 'lucide-react'
import { clsx } from 'clsx'
import { formatInr } from '../lib/format'

export const BUTTON_PRIMARY =
  'inline-flex items-center justify-center gap-1.5 shrink-0 whitespace-nowrap rounded-xl bg-[var(--accent)] hover:bg-[var(--accent-hover)] px-3.5 py-2 text-xs font-semibold text-white no-underline shadow-xs transition-colors cursor-pointer active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed'

export const BUTTON_SECONDARY =
  'inline-flex items-center justify-center gap-1.5 shrink-0 whitespace-nowrap rounded-xl border border-[var(--border-medium)] bg-[var(--bg-surface)] hover:bg-[var(--bg-elevated)] px-3.5 py-2 text-xs font-semibold text-[var(--text-primary)] no-underline transition-colors cursor-pointer active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed'

export const BUTTON_QUIET =
  'inline-flex items-center justify-center gap-1.5 shrink-0 whitespace-nowrap rounded-lg border border-blue-500/20 bg-blue-500/10 hover:bg-blue-500/20 px-3 py-1.5 text-xs font-semibold text-blue-500 no-underline transition-colors cursor-pointer'

export function Panel({
  title,
  hint,
  icon,
  actions,
  children,
  className,
  headingLevel = 2,
}: {
  title?: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  headingLevel?: 1 | 2
}) {
  const Heading = headingLevel === 1 ? 'h1' : 'h2'
  return (
    <section
      className={clsx(
        'bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-4 sm:p-6 shadow-xs space-y-4 min-w-0',
        className,
      )}
    >
      {(title || actions) && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="min-w-0">
            {title && (
              <Heading className="font-bold text-base text-[var(--text-primary)] tracking-tight flex items-center gap-2 min-w-0">
                {icon}
                <span className="truncate">{title}</span>
              </Heading>
            )}
            {hint && <p className="m-0 mt-0.5 text-xs text-[var(--text-secondary)]">{hint}</p>}
          </div>
          {actions && (
            <div className="flex flex-wrap items-center gap-2 shrink-0 self-start sm:self-auto">
              {actions}
            </div>
          )}
        </div>
      )}
      {children}
    </section>
  )
}

export type Tone = 'neutral' | 'accent' | 'success' | 'danger' | 'warning'

const PILL_TONES: Record<Tone, string> = {
  neutral: 'bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-subtle)]',
  accent: 'bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/25',
  success: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25',
  danger: 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/25',
  warning: 'bg-amber-500/15 text-amber-800 dark:text-amber-300 border-amber-500/30',
}

export function Pill({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: Tone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 shrink-0 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-semibold',
        PILL_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

const STATEMENT_PILLS: Record<string, { tone: Tone; label: string }> = {
  not_created: { tone: 'neutral', label: 'No statement yet' },
  draft: { tone: 'accent', label: 'Draft' },
  finalized_correction_window: { tone: 'warning', label: 'Finalized · correctable' },
  closed: { tone: 'success', label: 'Closed' },
}

export function StatementPill({ status }: { status: string | null | undefined }) {
  const entry = STATEMENT_PILLS[status ?? 'not_created'] ?? {
    tone: 'neutral' as Tone,
    label: status ?? '—',
  }
  return <Pill tone={entry.tone}>{entry.label}</Pill>
}

/**
 * A rupee figure in tabular numerals. A signed balance carries its sign in the
 * text as well as its colour, so the colour is never the only signal.
 */
export function Rupees({
  paise,
  signed,
  tone,
  className,
}: {
  paise: number | null | undefined
  /** Colour by sign and always print it. */
  signed?: boolean
  tone?: 'success' | 'danger' | 'accent'
  className?: string
}) {
  const resolved =
    tone ?? (signed && paise ? (paise > 0 ? 'success' : 'danger') : undefined)
  return (
    <span
      className={clsx(
        'tabular whitespace-nowrap',
        resolved === 'success' && 'text-[var(--success)]',
        resolved === 'danger' && 'text-[var(--danger)]',
        resolved === 'accent' && 'text-[var(--accent)]',
        className,
      )}
    >
      {formatInr(paise, { sign: signed ? 'always' : 'auto' })}
    </span>
  )
}

export function Avatar({ name, tone = 'neutral' }: { name: string; tone?: Tone }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
  return (
    <span
      className={clsx(
        'w-10 h-10 rounded-full flex items-center justify-center font-bold text-xs border shrink-0',
        PILL_TONES[tone],
      )}
      aria-hidden="true"
    >
      {initials || '?'}
    </span>
  )
}

const COPY_ICON_BUTTON =
  'p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors inline-flex items-center justify-center shrink-0 cursor-pointer'

/** Copies text and confirms it in place for a moment. */
export function CopyButton({
  text,
  label,
  className,
  iconOnly,
}: {
  text: string
  label: string
  className?: string
  /** Show just the icon; the label stays as the accessible name and tooltip. */
  iconOnly?: boolean
}) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  useEffect(() => {
    if (state === 'idle') return
    const timer = window.setTimeout(() => setState('idle'), 1800)
    return () => window.clearTimeout(timer)
  }, [state])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
  }

  const status = state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : label

  if (iconOnly) {
    return (
      <button
        type="button"
        className={className ?? COPY_ICON_BUTTON}
        aria-label={label}
        title={label}
        onClick={() => void copy()}
      >
        {state === 'copied' ? (
          <Check className="w-3.5 h-3.5 shrink-0 text-emerald-500" aria-hidden="true" />
        ) : (
          <Copy className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
        )}
        <span className="sr-only" aria-live="polite">
          {status}
        </span>
      </button>
    )
  }

  return (
    <button type="button" className={className ?? BUTTON_QUIET} onClick={() => void copy()}>
      {state === 'copied' ? (
        <Check className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
      ) : (
        <Copy className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
      )}
      <span aria-live="polite">{status}</span>
    </button>
  )
}

export function MiniStat({
  label,
  value,
  note,
}: {
  label: string
  value: ReactNode
  note?: ReactNode
}) {
  return (
    <div className="p-3.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] shadow-xs min-w-0">
      <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)] truncate">
        {label}
      </div>
      <div className="mt-1 text-base font-bold text-[var(--text-primary)] truncate">{value}</div>
      {note && <div className="mt-0.5 text-[11px] text-[var(--text-secondary)]">{note}</div>}
    </div>
  )
}

export function EmptyBlock({
  title,
  body,
  action,
}: {
  title: string
  body?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--border-medium)] px-4 py-8 text-center space-y-2">
      <div className="text-sm font-bold text-[var(--text-primary)]">{title}</div>
      {body && (
        <div className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">{body}</div>
      )}
      {action && <div className="pt-1 flex justify-center">{action}</div>}
    </div>
  )
}

/**
 * The one Edit button every card uses: the word "Edit" on screen, and a name
 * that says what it edits for anyone using a screen reader.
 */
export function EditButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" className={BUTTON_SECONDARY} onClick={onClick} aria-label={label} title={label}>
      <Pencil className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
      Edit
    </button>
  )
}
