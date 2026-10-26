/**
 * Building blocks for the token-based screens.
 *
 * These replace the class-based primitives one screen at a time. They carry
 * the two layout guards every new screen needs: text that can shrink is
 * truncated, and icons and pills never shrink.
 */
import { type CSSProperties, type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { Check, Copy, MoreVertical, Pencil } from 'lucide-react'
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
  menuActions,
  children,
  className,
  headingLevel = 2,
}: {
  title?: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  actions?: ReactNode
  /** Header actions per Rule U9: one is an icon button, more fold into a menu. */
  menuActions?: CardActionItem[]
  children: ReactNode
  className?: string
  headingLevel?: 1 | 2
}) {
  const Heading = headingLevel === 1 ? 'h1' : 'h2'
  const hasActions = Boolean(actions) || Boolean(menuActions?.length)
  return (
    <section
      className={clsx(
        'bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-4 sm:p-6 shadow-xs space-y-4 min-w-0',
        className,
      )}
    >
      {(title || hasActions) && (
        // The actions hold the top right; the title and hint take the rest (Rule U9).
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            {title && (
              <Heading className="font-bold text-base text-[var(--text-primary)] tracking-tight flex items-center gap-2 min-w-0">
                {icon}
                <span className="truncate">{title}</span>
              </Heading>
            )}
            {hint && <p className="m-0 mt-0.5 text-xs text-[var(--text-secondary)]">{hint}</p>}
          </div>
          {hasActions && (
            <div className="shrink-0 flex items-center gap-2">
              {actions}
              {menuActions && <CardActions actions={menuActions} />}
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

export function Avatar({
  name,
  tone = 'neutral',
  size = 'md',
}: {
  name: string
  tone?: Tone
  /** 'sm' keeps a one-line table row about as tall as its pills and buttons. */
  size?: 'sm' | 'md'
}) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
  return (
    <span
      className={clsx(
        'rounded-full flex items-center justify-center font-bold border shrink-0',
        size === 'sm' ? 'w-6 h-6 text-[10px]' : 'w-10 h-10 text-xs',
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
      <div
        className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)] truncate"
        title={typeof label === 'string' ? label : undefined}
      >
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
/**
 * Where to send a payment: the payee, the amount if known, and their bank
 * account, IFSC and UPI ID, each with a copy button. When none are recorded,
 * `missing` says where to add them.
 */
export function TransferDestination({
  name,
  amountPaise,
  bankAccount,
  bankIfsc,
  upiId,
  missing,
  label = 'Payment destination',
}: {
  name: string
  amountPaise?: number | null
  bankAccount?: string | null
  bankIfsc?: string | null
  upiId?: string | null
  missing: ReactNode
  label?: string
}) {
  return (
    <section
      aria-label={label}
      className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-3 min-w-0"
    >
      <div className="flex items-start justify-between gap-3 min-w-0">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
            Transfer to
          </div>
          <div className="text-sm font-bold text-[var(--text-primary)] truncate">{name}</div>
        </div>
        {amountPaise != null && (
          <Rupees paise={amountPaise} className="shrink-0 text-base font-bold text-[var(--text-primary)]" />
        )}
      </div>
      {bankAccount || bankIfsc || upiId ? (
        <dl className="m-0 space-y-2 text-xs">
          {bankAccount && <DestinationDetail label="Account" value={bankAccount} />}
          {bankIfsc && <DestinationDetail label="IFSC" value={bankIfsc} />}
          {upiId && <DestinationDetail label="UPI ID" value={upiId} />}
        </dl>
      ) : (
        <p className="m-0 text-xs text-[var(--text-secondary)]">{missing}</p>
      )}
    </section>
  )
}

/** One line of where to send money, in full, with a copy button. */
export function DestinationDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 min-w-0">
      <dt className="shrink-0 text-[var(--text-secondary)]">{label}</dt>
      <dd className="m-0 flex items-center gap-2 min-w-0">
        <span className="tabular font-semibold text-[var(--text-primary)] truncate select-all">
          {value}
        </span>
        <CopyButton text={value} label={`Copy ${label}`} iconOnly />
      </dd>
    </div>
  )
}

/** One fact in a read-only details dialog: the label above, the value below. */
export function DetailFact({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={clsx('min-w-0 space-y-1', className)}>
      <dt className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {label}
      </dt>
      <dd className="m-0 text-[var(--text-primary)]">{children}</dd>
    </div>
  )
}

/** A card's edit trigger (Rule U9): a 32×32 pencil, named by its tooltip. */
export function EditButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" className={HEADER_TRIGGER} onClick={onClick} aria-label={label} title={label}>
      <Pencil className="w-4 h-4 shrink-0" aria-hidden="true" />
    </button>
  )
}

/** One action in a card or panel header (Rule U9). */
export interface CardActionItem {
  label: string
  icon: React.ComponentType<{ className?: string }>
  /** What the action does; or give `to` for one that goes to another page. */
  onClick?: () => void
  /** A page to go to: rendered as a link, so it opens in a new tab like any other. */
  to?: string
  tone?: 'default' | 'danger'
  disabled?: boolean
}

const HEADER_TRIGGER =
  'inline-flex items-center justify-center w-8 h-8 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer shrink-0 disabled:opacity-50 disabled:cursor-not-allowed'

/**
 * A card header's actions (Rule U9): one action is a 32×32 icon button with
 * its name as tooltip and accessible name; two or more fold into a "More
 * options" menu, anchored bottom-right, that closes on an outside click or
 * Escape and is worked with the arrow keys, Home and End.
 *
 * ``floating`` is for a menu inside something that scrolls, such as a table
 * row: the menu is laid over the page instead of inside it, so the scrolling
 * box cannot clip it. It opens upwards when there is no room below, and closes
 * when the page scrolls or resizes rather than drifting from its button.
 */
export function CardActions({ actions, floating = false }: { actions?: CardActionItem[]; floating?: boolean }) {
  const [open, setOpen] = useState(false)
  const [placement, setPlacement] = useState<CSSProperties | undefined>(undefined)
  const wrapper = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const menuId = useId()

  const items = () =>
    Array.from(
      menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? [],
    )
  const close = (returnFocus: boolean) => {
    setOpen(false)
    if (returnFocus) trigger.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    // Opening the menu puts the keyboard on its first action.
    items()[0]?.focus()
    const onMouseDown = (event: MouseEvent) => {
      const target = event.target as Node
      // A floating menu is not inside the wrapper, so it is checked on its own.
      if (wrapper.current?.contains(target) || menu.current?.contains(target)) return
      setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close(true)
      }
    }
    const onMove = () => setOpen(false)
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKeyDown)
    if (floating) {
      window.addEventListener('scroll', onMove, true)
      window.addEventListener('resize', onMove)
    }
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open])

  /** Where a floating menu goes: under its button, or above it near the bottom of the screen. */
  const place = () => {
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return
    const right = window.innerWidth - rect.right
    const roomBelow = window.innerHeight - rect.bottom
    setPlacement(
      roomBelow < 48 + actions!.length * 36
        ? { position: 'fixed', right, bottom: window.innerHeight - rect.top + 6 }
        : { position: 'fixed', right, top: rect.bottom + 6 },
    )
  }

  if (!actions || actions.length === 0) return null

  if (actions.length === 1) {
    const [action] = actions
    const Icon = action.icon
    const icon = <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
    return action.to && !action.disabled ? (
      <Link to={action.to} className={HEADER_TRIGGER} title={action.label} aria-label={action.label}>
        {icon}
      </Link>
    ) : (
      <button
        type="button"
        onClick={action.onClick}
        disabled={action.disabled}
        title={action.label}
        aria-label={action.label}
        className={HEADER_TRIGGER}
      >
        {icon}
      </button>
    )
  }

  // A floating menu is laid over the page, outside any box that scrolls.
  const renderMenu = (list: ReactNode) => (floating ? createPortal(list, document.body) : list)

  const onMenuKeyDown = (event: React.KeyboardEvent) => {
    const list = items()
    const at = list.indexOf(document.activeElement as HTMLElement)
    const move = (index: number) => {
      event.preventDefault()
      list[(index + list.length) % list.length]?.focus()
    }
    if (event.key === 'ArrowDown') move(at + 1)
    else if (event.key === 'ArrowUp') move(at - 1)
    else if (event.key === 'Home') move(0)
    else if (event.key === 'End') move(list.length - 1)
    else if (event.key === 'Tab') setOpen(false)
  }

  return (
    <div className="relative inline-block text-left shrink-0" ref={wrapper}>
      <button
        ref={trigger}
        type="button"
        onClick={() => {
          if (floating && !open) place()
          setOpen((shown) => !shown)
        }}
        title="More options"
        aria-label="More options"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        className={HEADER_TRIGGER}
      >
        <MoreVertical className="w-4 h-4 shrink-0" aria-hidden="true" />
      </button>
      {open && renderMenu(
        <div
          ref={menu}
          id={menuId}
          role="menu"
          aria-orientation="vertical"
          aria-label="More options"
          onKeyDown={onMenuKeyDown}
          style={floating ? placement : undefined}
          className={clsx(
            !floating && 'absolute right-0 top-full mt-1.5',
            'w-48 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-1.5 shadow-lg z-30 focus:outline-none',
          )}
        >
          {actions.map((action) => {
            const Icon = action.icon
            const className = clsx(
              'w-full flex items-center gap-2.5 px-3 py-2 text-xs font-semibold rounded-lg text-left no-underline transition-colors cursor-pointer focus:outline-none focus-visible:bg-[var(--bg-elevated)]',
              action.tone === 'danger'
                ? 'text-[var(--danger)] hover:bg-rose-500/10'
                : 'text-[var(--text-primary)] hover:bg-[var(--bg-elevated)]',
              action.disabled && 'opacity-50 cursor-not-allowed',
            )
            const content = (
              <>
                <Icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                <span>{action.label}</span>
              </>
            )
            return action.to && !action.disabled ? (
              <Link
                key={action.label}
                to={action.to}
                role="menuitem"
                tabIndex={-1}
                className={className}
                onClick={() => close(false)}
              >
                {content}
              </Link>
            ) : (
              <button
                key={action.label}
                type="button"
                role="menuitem"
                tabIndex={-1}
                aria-disabled={action.disabled || undefined}
                className={className}
                onClick={() => {
                  if (action.disabled) return
                  close(true)
                  action.onClick?.()
                }}
              >
                {content}
              </button>
            )
          })}
        </div>,
      )}
    </div>
  )
}

/**
 * Step pills for an editor split into steps: the current step filled in, the
 * others outlined, and a red dot on any other step holding a value that stops
 * the save. They are tabs, so each one jumps straight to its step.
 */
export function StepPills<T extends string>({
  steps,
  current,
  flagged,
  onSelect,
  label,
  tabId,
  panelId,
  className,
}: {
  steps: ReadonlyArray<{ id: T; label: string }>
  current: T
  flagged: Partial<Record<T, boolean>>
  onSelect: (id: T) => void
  label: string
  tabId: (id: T) => string
  panelId: string
  className?: string
}) {
  return (
    <div className={clsx('flex flex-wrap gap-2', className)} role="tablist" aria-label={label}>
      {steps.map((entry) => {
        const selected = current === entry.id
        const attention = !selected && flagged[entry.id]
        return (
          <button
            key={entry.id}
            id={tabId(entry.id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId}
            onClick={() => onSelect(entry.id)}
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition-colors cursor-pointer',
              selected
                ? 'bg-[var(--accent)] border-[var(--accent)] text-white shadow-xs'
                : 'bg-[var(--bg-surface)] border-[var(--border-medium)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
            )}
          >
            {entry.label}
            {attention && (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--danger)]" aria-hidden="true" />
                <span className="sr-only"> (needs attention)</span>
              </>
            )}
          </button>
        )
      })}
    </div>
  )
}
