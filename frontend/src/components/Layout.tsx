/**
 * The application shell: the master ribbon, the canvas toolbar, and the month
 * in context. The selected settlement month lives here and is shared by every
 * screen, so moving between pages keeps the operator in the same month.
 */
import {
  Fragment,
  type ReactNode,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import { flushSync } from 'react-dom'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  Building2,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Moon,
  Plus,
  Settings,
  Sun,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useResource } from '../lib/useApi'
import { currentMonth, formatMonth, shiftMonth } from '../lib/format'
import type { Apartment, Health, SetupState } from '../api/types'
import { ExpenseFormDialog } from '../pages/ExpenseForm'
import { useToast } from './Toast'

type Section = 'hub' | 'housing' | 'services' | 'ledger' | 'residents' | 'statements' | 'settings'

const NAV_TABS: Array<{ to: string; label: string; section: Section }> = [
  { to: '/', label: 'Month Hub', section: 'hub' },
  { to: '/housing', label: 'Housing', section: 'housing' },
  { to: '/services', label: 'Services & Utilities', section: 'services' },
  { to: '/transactions', label: 'Activity Ledger', section: 'ledger' },
  { to: '/residents', label: 'Residents', section: 'residents' },
  { to: '/statements', label: 'Statements', section: 'statements' },
]

// Every screen belongs to one ribbon destination, so the ribbon still shows
// where the operator is on screens that have no tab of their own.
const SECTION_PREFIXES: Array<[string, Section]> = [
  ['/housing', 'housing'],
  ['/property', 'housing'],
  ['/deposits', 'housing'],
  ['/services', 'services'],
  ['/transactions', 'ledger'],
  ['/expenses', 'ledger'],
  ['/payments', 'ledger'],
  ['/audit', 'ledger'],
  ['/statements', 'statements'],
  ['/reports', 'statements'],
  ['/setup', 'settings'],
  ['/welcome', 'settings'],
  ['/residents', 'residents'],
  ['/definitions', 'settings'],
]

function sectionFor(pathname: string): Section {
  const match = SECTION_PREFIXES.find(
    ([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  )
  return match ? match[1] : 'hub'
}

interface AppContextValue {
  month: string
  setMonth: (month: string) => void
  availableMonths: string[]
  apartment: Apartment | null
  health: Health | null
  setup: SetupState | null
  reloadApartment: () => void
}

const AppContext = createContext<AppContextValue | null>(null)

export function useApp(): AppContextValue {
  const context = useContext(AppContext)
  if (!context) throw new Error('useApp must be used inside the Layout')
  return context
}

const MONTH_STORAGE_KEY = 'aem.settlement-month'
const THEME_STORAGE_KEY = 'aem.theme'

type Theme = 'dark' | 'light'

function readStoredMonth(): string {
  try {
    const stored = window.localStorage.getItem(MONTH_STORAGE_KEY)
    if (stored && /^\d{4}-\d{2}$/.test(stored)) return stored
  } catch {
    // A private window or blocked site data: fall back to the current month.
  }
  return currentMonth()
}

function readStoredTheme(): Theme {
  try {
    if (window.localStorage.getItem(THEME_STORAGE_KEY) === 'light') return 'light'
  } catch {
    // Same fallback as the month: the default theme.
  }
  return 'dark'
}

const ICON_BUTTON =
  'relative p-2 rounded-xl text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer active:scale-95'

export function Layout({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const toast = useToast()
  const [month, setMonthState] = useState(readStoredMonth)
  const [theme, setTheme] = useState<Theme>(readStoredTheme)
  const [recording, setRecording] = useState(false)
  // Bumped after a transaction is recorded so the screen underneath refetches.
  const [revision, setRevision] = useState(0)

  const health = useResource<Health>('/health')
  const apartments = useResource<{ items: Apartment[]; default_apartment_id: string | null }>(
    '/api/apartments',
  )
  const setup = useResource<SetupState>('/api/setup/state')

  const setMonth = (next: string) => {
    if (!/^\d{4}-\d{2}$/.test(next)) return
    setMonthState(next)
    try {
      window.localStorage.setItem(MONTH_STORAGE_KEY, next)
    } catch {
      // Per-viewer convenience only; the app works without it.
    }
  }

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme)
    } catch {
      // Per-viewer convenience only; the app works without it.
    }
  }, [theme])

  const apartment = useMemo(() => {
    const items = apartments.data?.items ?? []
    const preferred = apartments.data?.default_apartment_id
    return items.find((item) => item.apartment_id === preferred) ?? items[0] ?? null
  }, [apartments.data])

  const contextValue = useMemo<AppContextValue>(
    () => ({
      month,
      setMonth,
      availableMonths: [],
      apartment,
      health: health.data,
      setup: setup.data,
      reloadApartment: () => {
        apartments.reload()
        setup.reload()
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [month, apartment, health.data, setup.data, apartments.reload, setup.reload],
  )

  // A brand-new database has nothing to show, so send the operator straight to
  // the guided setup rather than to an empty dashboard. Only on the very first
  // step: once an apartment exists the app is usable, and an interrupted setup
  // is offered as a dismissible banner instead of a redirect.
  const onWizard = location.pathname === '/welcome'
  const needsFirstRun = setup.data?.next_step === 'apartment'
  useEffect(() => {
    if (needsFirstRun && !onWizard) navigate('/welcome', { replace: true })
  }, [needsFirstRun, onWizard, navigate])

  const setupIncomplete = Boolean(
    setup.data && !setup.data.is_complete && setup.data.next_step !== 'apartment',
  )

  const degraded = Boolean(health.data && health.data.status !== 'ok')
  const section = sectionFor(location.pathname)
  const isDark = theme === 'dark'

  /**
   * Switch theme with a circle spreading from the toggle. Browsers without
   * view transitions, and anyone who asked for reduced motion, get the plain
   * instant switch.
   */
  const toggleTheme = (event: React.MouseEvent<HTMLButtonElement>) => {
    const next: Theme = isDark ? 'light' : 'dark'
    const root = document.documentElement
    const startViewTransition = (
      document as Document & {
        startViewTransition?: (update: () => void) => { ready: Promise<void> }
      }
    ).startViewTransition
    if (
      !startViewTransition ||
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    ) {
      setTheme(next)
      return
    }

    // A keyboard press has no pointer position; start from the button itself.
    const box = event.currentTarget.getBoundingClientRect()
    const x = event.detail === 0 ? box.left + box.width / 2 : event.clientX
    const y = event.detail === 0 ? box.top + box.height / 2 : event.clientY
    const endRadius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y),
    )

    const transition = startViewTransition.call(document, () => {
      // The new snapshot is taken when this returns, so the theme must be on
      // the page by then rather than after React's next effect pass.
      flushSync(() => setTheme(next))
      root.dataset.theme = next
    })
    void transition.ready
      .then(() => {
        // Continuous fluid wave: an energetic launch from the button that
        // decelerates smoothly all the way into the far corners. One curve, no
        // intermediate keyframes, so the speed never changes abruptly. The new
        // theme always spreads outward over the old one.
        root.animate(
          {
            clipPath: [
              `circle(0px at ${x}px ${y}px)`,
              `circle(${endRadius}px at ${x}px ${y}px)`,
            ],
          },
          {
            duration: 850,
            easing: 'cubic-bezier(0.12, 0.8, 0.2, 1)',
            fill: 'both',
            pseudoElement: '::view-transition-new(root)',
          },
        )
      })
      .catch(() => {
        // View transition aborted or unsupported; the theme has still changed.
      })
  }
  // Rent, electricity and setup have their own workflows; the generic
  // transaction form would only compete with them there.
  const showRecord =
    section !== 'services' &&
    section !== 'housing' &&
    section !== 'settings' &&
    section !== 'residents'
  // The month means nothing on setup and configuration screens, so the picker
  // row is left out there altogether.
  const isConfigPage =
    section === 'housing' ||
    section === 'settings' ||
    section === 'residents'
  const showPeriodBar = !onWizard && !isConfigPage

  return (
    <AppContext.Provider value={contextValue}>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <div className="min-h-full flex flex-col">
        <header className="bg-[var(--bg-surface)] border-b border-[var(--border-subtle)] px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between md:grid md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:gap-x-4 sticky top-0 z-40 backdrop-blur-md min-h-[57px] print:hidden">
          {/* LEFT: emblem and apartment name */}
          <Link
            to="/"
            className="flex items-center gap-3 min-w-0 no-underline md:justify-self-start md:max-w-full"
            title={apartment?.name ?? undefined}
            aria-label={apartment?.name ?? 'Apartment Expense Manager'}
          >
            <span className="flex w-8 h-8 rounded-lg bg-gradient-to-tr from-blue-700 to-indigo-500 text-white items-center justify-center shrink-0 shadow-xs">
              <Building2 className="w-4 h-4 shrink-0" aria-hidden="true" />
            </span>
            <span
              className={clsx(
                'font-bold text-sm text-[var(--text-primary)] truncate lg:max-w-[300px]',
                // Beside the tabs there is only room for the name on wide
                // screens; the setup wizard has no tabs, so it can show sooner.
                onWizard ? 'hidden sm:block' : 'hidden lg:block',
              )}
            >
              {apartment?.name ?? 'Apartment Expense Manager'}
            </span>
          </Link>

          {/* CENTER: the middle column of a three-column grid whose side columns
              are equal, so the tabs sit at the ribbon's midpoint and neither
              side can run into them: the name truncates inside its own column.
              Below `md` five tabs do not fit beside the utilities, and they
              take a row of their own. */}
          {!onWizard && (
            <nav
              className="max-md:order-last max-md:w-full max-md:mt-2.5 max-md:justify-start max-md:overflow-x-auto md:col-start-2 flex items-center justify-center gap-1 bg-[var(--bg-elevated)] p-1 rounded-xl border border-[var(--border-subtle)] text-xs font-medium shadow-xs shrink-0"
              aria-label="Main navigation"
            >
              {NAV_TABS.map((tab) => {
                const active = tab.section === section
                return (
                  <Link
                    key={tab.to}
                    to={tab.to}
                    aria-current={active ? 'page' : undefined}
                    className={clsx(
                      'max-md:flex-1 text-center px-2 md:px-2.5 lg:px-4 py-1.5 rounded-lg transition-colors whitespace-nowrap cursor-pointer no-underline',
                      active
                        ? 'bg-[var(--accent)] text-white font-semibold shadow-xs'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                    )}
                  >
                    {tab.label}
                  </Link>
                )
              })}
            </nav>
          )}

          {/* RIGHT: theme toggle, then settings as the right-most element */}
          <div className="flex items-center gap-1.5 shrink-0 md:col-start-3 md:justify-self-end">
            <button
              type="button"
              onClick={toggleTheme}
              className={ICON_BUTTON}
              title="Toggle theme"
              aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
            >
              {isDark ? (
                <Moon className="w-4 h-4 shrink-0 text-amber-400" aria-hidden="true" />
              ) : (
                <Sun className="w-4 h-4 shrink-0 text-amber-500" aria-hidden="true" />
              )}
            </button>
            {!onWizard && (
              <Link
                to="/setup"
                aria-current={section === 'settings' ? 'page' : undefined}
                className={clsx(
                  ICON_BUTTON,
                  section === 'settings' && 'bg-[var(--bg-elevated)] text-[var(--text-primary)]',
                )}
                title={degraded ? 'Settings — needs attention' : 'Settings & setup'}
                aria-label={degraded ? 'Settings, needs attention' : 'Settings'}
              >
                <Settings className="w-4 h-4 shrink-0" aria-hidden="true" />
                {degraded && (
                  <span
                    className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-[var(--warning)] shrink-0"
                    aria-hidden="true"
                  />
                )}
              </Link>
            )}
          </div>
        </header>

        <main
          id="main"
          className="p-4 sm:p-6 md:p-8 space-y-6 max-w-6xl w-full mx-auto min-w-0 print:p-0 print:max-w-none"
        >
          {showPeriodBar && (
            <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
              <div className="flex items-center gap-3 min-w-0">
                <span className="hidden sm:inline text-xs font-medium text-[var(--text-secondary)] whitespace-nowrap">
                  Settlement Period
                </span>
                <div className="flex items-center bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl px-2.5 py-1.5 gap-2 shadow-xs">
                  <button
                    type="button"
                    onClick={() => setMonth(shiftMonth(month, -1))}
                    className="p-0.5 rounded-md text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer"
                    aria-label="Previous month"
                  >
                    <ChevronLeft className="w-4 h-4 shrink-0" aria-hidden="true" />
                  </button>
                  <CalendarDays
                    className="w-3.5 h-3.5 shrink-0 text-[var(--text-tertiary)]"
                    aria-hidden="true"
                  />
                  <span
                    className="tabular text-xs font-bold text-[var(--text-primary)] whitespace-nowrap min-w-[14ch] text-center"
                    aria-live="polite"
                  >
                    <span className="sr-only">Settlement month: </span>
                    {formatMonth(month)}
                  </span>
                  <button
                    type="button"
                    onClick={() => setMonth(shiftMonth(month, 1))}
                    className="p-0.5 rounded-md text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer"
                    aria-label="Next month"
                  >
                    <ChevronRight className="w-4 h-4 shrink-0" aria-hidden="true" />
                  </button>
                </div>
              </div>
              {showRecord && (
                <button
                  type="button"
                  onClick={() => setRecording(true)}
                  className="bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-semibold px-4 py-2.5 rounded-xl transition-all shadow-xs flex items-center gap-2 cursor-pointer active:scale-[0.98] shrink-0"
                >
                  <Plus className="w-4 h-4 shrink-0" aria-hidden="true" />
                  Record Transaction
                </button>
              )}
            </div>
          )}

          {setupIncomplete && !onWizard && (
            <div
              className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--accent-glow)] px-4 py-3 text-sm text-[var(--text-primary)]"
              role="status"
            >
              <span className="min-w-0 flex-1 basis-64">
                <strong className="font-semibold">Setup is half finished.</strong>{' '}
                {setup.data?.next_step === 'residents' && 'Next: who lives here.'}
                {setup.data?.next_step === 'categories' && 'Next: what the household pays for.'}
                {setup.data?.next_step === 'finish' && 'Nearly there — just confirm.'}{' '}
                Everything you entered is saved.
              </span>
              <Link
                className="shrink-0 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] px-3 py-1.5 text-xs font-semibold text-white no-underline transition-colors"
                to="/welcome"
              >
                Pick up where you left off
              </Link>
            </div>
          )}

          <Fragment key={revision}>{children}</Fragment>
        </main>
      </div>

      {recording && (
        <ExpenseFormDialog
          defaultSettlementPeriod={month}
          onClose={() => setRecording(false)}
          onSaved={(message) => {
            setRecording(false)
            setRevision((current) => current + 1)
            toast.success(message)
          }}
        />
      )}
    </AppContext.Provider>
  )
}

export function PageHeader({
  title,
  subtitle,
  actions,
  breadcrumbs,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  breadcrumbs?: Array<{ label: string; to?: string }>
}) {
  return (
    <header className="page-header">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav className="breadcrumbs" aria-label="Breadcrumb">
          <ol>
            {breadcrumbs.map((crumb) => (
              <li key={`${crumb.label}-${crumb.to ?? ''}`}>
                {crumb.to ? <Link to={crumb.to}>{crumb.label}</Link> : <span>{crumb.label}</span>}
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="page-title-row">
        <div>
          <h1>{title}</h1>
          {subtitle && <p className="page-subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
    </header>
  )
}

export function Page({
  children,
  ...header
}: {
  children: ReactNode
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  breadcrumbs?: Array<{ label: string; to?: string }>
}) {
  return (
    <div className="page">
      <PageHeader {...header} />
      <div className="stack">{children}</div>
    </div>
  )
}
