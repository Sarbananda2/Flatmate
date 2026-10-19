/**
 * Shared presentational components.
 *
 * Two rules these encode, so no screen has to remember them:
 *
 * - A financial amount never relies on colour alone. `Money` renders an
 *   explicit sign and, where the distinction matters, a label beside it.
 * - Every list has a real loading, error, and empty state. `AsyncSection`
 *   makes rendering one unavoidable.
 */
import { type ReactNode, useEffect, useId, useRef } from 'react'
import { X } from 'lucide-react'
import { ApiError } from '../api/client'
import { formatInr } from '../lib/format'
import { CardActions, type CardActionItem } from './ui'

/* ------------------------------------------------------------------ money -- */

export type MoneyTone = 'auto' | 'plain' | 'credit' | 'owed-to' | 'owed-by'

export function Money({
  paise,
  tone = 'plain',
  sign = 'auto',
  title,
}: {
  paise: number | null | undefined
  /** `auto` colours a signed balance; `plain` leaves a magnitude uncoloured. */
  tone?: MoneyTone
  sign?: 'auto' | 'always' | 'none'
  title?: string
}) {
  let className = 'amount'
  if (paise === null || paise === undefined) {
    return <span className="amount amount--zero">—</span>
  }
  const resolved =
    tone === 'auto' ? (paise > 0 ? 'owed-to' : paise < 0 ? 'owed-by' : 'plain') : tone
  if (paise === 0 && tone !== 'credit') className += ' amount--zero'
  else if (resolved === 'owed-to') className += ' amount--owed-to'
  else if (resolved === 'owed-by') className += ' amount--owed-by'
  else if (resolved === 'credit') className += ' amount--credit'
  return (
    <span className={className} title={title}>
      {formatInr(paise, { sign })}
    </span>
  )
}

/**
 * A net balance with the words that say what the sign means, so the figure is
 * readable without interpreting a colour.
 */
export function NetBalance({ paise, name }: { paise: number; name?: string }) {
  const who = name ?? 'this account'
  const label =
    paise > 0
      ? `the household owes ${who}`
      : paise < 0
        ? `${who} owes the household`
        : 'settled'
  return (
    <span className="row" style={{ gap: 6 }}>
      <Money paise={paise} tone="auto" sign="always" />
      <span className="subtle nowrap">{label}</span>
    </span>
  )
}

/* ------------------------------------------------------------------ badge -- */

export type BadgeTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' | 'credit'

const BADGE_GLYPHS: Record<BadgeTone, string> = {
  neutral: '•',
  accent: '◆',
  ok: '✓',
  warn: '!',
  danger: '✕',
  credit: '＋',
}

export function Badge({
  children,
  tone = 'neutral',
  glyph,
  title,
}: {
  children: ReactNode
  tone?: BadgeTone
  glyph?: string
  title?: string
}) {
  return (
    <span className={`badge badge--${tone}`} data-glyph={glyph ?? BADGE_GLYPHS[tone]} title={title}>
      {children}
    </span>
  )
}

const STATEMENT_TONES: Record<string, BadgeTone> = {
  draft: 'neutral',
  finalized_correction_window: 'warn',
  closed: 'ok',
  not_created: 'neutral',
}

const STATEMENT_LABELS: Record<string, string> = {
  draft: 'Draft',
  finalized_correction_window: 'Finalized · correctable',
  closed: 'Closed',
  not_created: 'Not created',
  unattached: 'Not in a statement',
}

export function StatementStatusBadge({ status }: { status: string | null | undefined }) {
  const key = status ?? 'not_created'
  return (
    <Badge tone={STATEMENT_TONES[key] ?? 'neutral'}>
      {STATEMENT_LABELS[key] ?? key}
    </Badge>
  )
}

/* ----------------------------------------------------------------- states -- */

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span className="row" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  )
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="state-body">{label}</span>
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: ApiError | Error
  onRetry?: () => void
}) {
  const apiError = error instanceof ApiError ? error : null
  return (
    <div className="state" role="alert">
      <span className="state-icon" aria-hidden="true">
        ⚠
      </span>
      <span className="state-title">Could not load this</span>
      <span className="state-body">{error.message}</span>
      {apiError && apiError.code !== 'network_error' && (
        <span className="id-chip">{apiError.code}</span>
      )}
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

/**
 * The first-run state.
 *
 * A brand-new database legitimately has no apartment, so every screen's first
 * load fails with `no_apartment`. That is not an error the operator caused, and
 * showing them "Could not load this" on their first visit would be wrong. The
 * plain anchor rather than a router Link keeps this component router-free.
 */
export function NoApartmentState() {
  return (
    <div className="state">
      <span className="state-icon" aria-hidden="true">
        ⌂
      </span>
      <span className="state-title">Welcome — let's set up the household</span>
      <span className="state-body">
        Nothing has been created yet. Start by adding the apartment, then the
        residents sharing it, then the expense categories you want to track.
        Nothing about those categories is built in: rent, electricity, groceries
        and the rest are records you create and can rename or retire at any time.
      </span>
      <a className="btn btn--primary" href="/setup">
        Go to setup
      </a>
    </div>
  )
}

export function EmptyState({
  icon = '∅',
  title,
  body,
  action,
}: {
  icon?: string
  title: string
  body?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="state">
      <span className="state-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="state-title">{title}</span>
      {body && <span className="state-body">{body}</span>}
      {action}
    </div>
  )
}

/** Renders exactly one of loading / error / empty / content. */
export function AsyncSection<T>({
  resource,
  isEmpty,
  empty,
  children,
  loadingLabel,
}: {
  resource: { data: T | null; error: ApiError | null; initialLoading: boolean; reload: () => void }
  isEmpty?: (data: T) => boolean
  empty?: ReactNode
  children: (data: T) => ReactNode
  loadingLabel?: string
}) {
  if (resource.initialLoading) return <LoadingState label={loadingLabel} />
  if (resource.error?.code === 'no_apartment') return <NoApartmentState />
  if (resource.error) return <ErrorState error={resource.error} onRetry={resource.reload} />
  if (!resource.data) return <LoadingState label={loadingLabel} />
  if (isEmpty?.(resource.data) && empty) return <>{empty}</>
  return <>{children(resource.data)}</>
}

/* ---------------------------------------------------------------- callout -- */

export function Callout({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'warn' | 'danger' | 'ok'
  title?: string
  children: ReactNode
}) {
  const glyph = { info: 'i', warn: '!', danger: '✕', ok: '✓' }[tone]
  return (
    <div className={`callout callout--${tone}`} role={tone === 'danger' ? 'alert' : undefined}>
      <span className="callout-icon" aria-hidden="true">
        {glyph}
      </span>
      <div className="callout-body">
        {title && <div className="callout-title">{title}</div>}
        {children}
      </div>
    </div>
  )
}

export function ErrorNotice({ error }: { error: ApiError | null }) {
  if (!error) return null
  const fields = error.fieldErrors
  return (
    <Callout tone="danger" title={fields.length ? 'Please correct these fields' : 'That did not save'}>
      {fields.length ? (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {fields.map((field) => (
            <li key={`${field.field}-${field.message}`}>
              <strong>{field.field}</strong>: {field.message}
            </li>
          ))}
        </ul>
      ) : (
        <>
          {error.message}
          {error.code && error.code !== 'validation_error' && (
            <>
              {' '}
              <span className="id-chip">{error.code}</span>
            </>
          )}
        </>
      )}
    </Callout>
  )
}

/* ------------------------------------------------------------------ cards -- */

export function Card({
  title,
  hint,
  actions,
  menuActions,
  footer,
  flush,
  children,
  id,
}: {
  title?: ReactNode
  hint?: ReactNode
  actions?: ReactNode
  /** Header actions per Rule U9: one is an icon button, more fold into a menu. */
  menuActions?: CardActionItem[]
  footer?: ReactNode
  flush?: boolean
  children: ReactNode
  id?: string
}) {
  const hasActions = Boolean(actions) || Boolean(menuActions?.length)
  return (
    <section className="card" id={id} aria-labelledby={id ? `${id}-title` : undefined}>
      {(title || hasActions) && (
        <header className="card-header">
          {/* The title and hint take what is left; the actions keep their size,
              so a long hint wraps beside them and never pushes them below. */}
          <div className="card-header-text">
            {title && (
              <h2 className="card-title" id={id ? `${id}-title` : undefined}>
                {title}
              </h2>
            )}
            {hint && <div className="card-hint">{hint}</div>}
          </div>
          {hasActions && (
            <div className="card-header-actions">
              {actions}
              {menuActions && <CardActions actions={menuActions} />}
            </div>
          )}
        </header>
      )}
      <div className={flush ? 'card-body card-body--flush' : 'card-body'}>{children}</div>
      {footer && <footer className="card-footer">{footer}</footer>}
    </section>
  )
}

export function Stat({
  label,
  value,
  note,
}: {
  label: string
  value: ReactNode
  note?: ReactNode
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  )
}

/* ----------------------------------------------------------------- dialog -- */

export function Modal({
  title,
  description,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string
  description?: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
}) {
  const titleId = useId()
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    // The page behind a dialog must not scroll while it is open.
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = panel.current?.querySelector<HTMLElement>(
      'input, select, textarea, button, [href], [tabindex]:not([tabindex="-1"])',
    )
    focusable?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !panel.current) return
      // Keep Tab inside the dialog, so keyboard users cannot land behind it.
      const targets = Array.from(
        panel.current.querySelectorAll<HTMLElement>(
          'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => element.offsetParent !== null)
      if (targets.length === 0) return
      const first = targets[0]
      const last = targets[targets.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.body.style.overflow = originalOverflow
      document.removeEventListener('keydown', onKeyDown, true)
      previous?.focus?.()
    }
  }, [onClose])

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className={wide ? 'modal modal--wide' : 'modal'}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panel}
      >
        <header className="modal-header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <div className="card-hint">{description}</div>}
          </div>
          <button
            type="button"
            className="btn btn--ghost p-1.5 rounded-md -mr-1 -mt-0.5 shrink-0"
            onClick={onClose}
            aria-label="Close dialog"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-footer">{footer}</footer>}
      </div>
    </div>
  )
}

export function ConfirmDialog({
  title,
  body,
  confirmLabel = 'Confirm',
  tone = 'primary',
  pending,
  error,
  onConfirm,
  onCancel,
  children,
  confirmDisabled,
}: {
  title: string
  body: ReactNode
  confirmLabel?: string
  tone?: 'primary' | 'danger'
  pending?: boolean
  error?: ApiError | null
  onConfirm: () => void
  onCancel: () => void
  children?: ReactNode
  confirmDisabled?: boolean
}) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel} disabled={pending}>
            Cancel
          </button>
          <button
            type="button"
            className={tone === 'danger' ? 'btn btn--danger' : 'btn btn--primary'}
            onClick={onConfirm}
            disabled={pending || confirmDisabled}
          >
            {pending ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="stack">
        {error && <ErrorNotice error={error} />}
        <div>{body}</div>
        {children}
      </div>
    </Modal>
  )
}

/* ------------------------------------------------------------- pagination -- */

export function Pagination({
  total,
  limit,
  offset,
  onChange,
  noun = 'rows',
}: {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
  noun?: string
}) {
  if (total <= limit) {
    return (
      <div className="pagination">
        <span>
          {total} {noun}
        </span>
      </div>
    )
  }
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  return (
    <nav className="pagination" aria-label={`${noun} pages`}>
      <span>
        {offset + 1}–{Math.min(offset + limit, total)} of {total} {noun}
      </span>
      <span className="row">
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={offset === 0}
        >
          ← Previous
        </button>
        <span className="subtle nowrap">
          Page {page} of {pages}
        </span>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => onChange(offset + limit)}
          disabled={offset + limit >= total}
        >
          Next →
        </button>
      </span>
    </nav>
  )
}
