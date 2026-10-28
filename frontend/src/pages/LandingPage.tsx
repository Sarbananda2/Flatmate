/**
 * flatmate.dev's front door: what the app does and why a shared flat needs
 * it, for anyone who is not signed in -- and at /landing for anyone who is.
 *
 * It is drawn in the app's own theme tokens, so it follows the light or dark
 * theme the visitor last chose. Nothing here reads the household: the figures
 * in the example month are made up for the page.
 */
import { Link } from 'react-router-dom'
import { useOptionalAuth } from '../context/AuthContext'

/** Where each link in the navbar goes on the page. */
const NAV_LINKS = [
  { href: '#features', label: 'Features' },
  { href: '#kitty', label: 'The House Kitty' },
  { href: '#rent', label: 'Rent Hub' },
  { href: '#compare', label: 'Compare' },
]

const PRIMARY_CTA =
  'inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--accent)] px-5 py-3 text-sm font-bold text-white shadow-[var(--shadow)] transition-colors hover:bg-[var(--accent-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]'
const SECONDARY_CTA =
  'inline-flex items-center justify-center gap-2 rounded-xl border border-[var(--border-medium)] bg-[var(--bg-surface)] px-5 py-3 text-sm font-bold text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-elevated)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]'
const SECTION = 'mx-auto max-w-6xl px-4 sm:px-6'
const EYEBROW = 'text-xs font-bold uppercase tracking-[0.18em] text-[var(--accent)]'

/** The flatmates in the example month, each paid up. */
const EXAMPLE_FLATMATES = ['Sarbananda Taw', 'Arnab Deka', 'Anagarag Mahanta']

/** Who the example month paid out to. */
const EXAMPLE_PAYOUTS = [
  { payee: 'Rajesh Sharma (Landlord)', amount: '₹45,000.00' },
  { payee: 'Furlenco', amount: '₹2,637.05' },
]

const SPLITWISE_WAY = [
  'Six one-to-one debts between four flatmates, each settled on its own',
  'One person out ₹45,000 for the rent, waiting weeks to be paid back',
  '18% GST rounded per person, so the shares never add up to the invoice',
  'Awkward weekend WhatsApp reminders to whoever has not paid',
]

const FLATMATE_WAY = [
  'One balance each, with the House Kitty: pay in once, settle everything',
  'The landlord and every provider paid from the kitty, in one or several payouts',
  'Whoever fronts a bill is reimbursed from the kitty automatically',
  'Exact integer paise: every share adds up to the bill, to the last paisa',
]

const MODULES = [
  {
    id: 'rent',
    icon: '🏠',
    title: 'Master Lease & Rent Hub',
    body: 'Split the rent equally or by room-tiered base rents, keep the landlord’s PAN on the lease, and hand out HRA rent receipts.',
    points: ['Equal or room-tiered rent', 'Landlord PAN on the lease', 'HRA rent receipts'],
  },
  {
    id: 'furniture',
    icon: '🛋️',
    title: 'Furniture & Appliance Subscriptions',
    body: 'Furlenco and Rentomojo subscriptions with several of an item at once, GST on the bill by slider, and the invoice’s own round-off.',
    points: ['Furlenco & Rentomojo', 'Multi-quantity items', '18% GST slider, invoice round-off'],
  },
  {
    id: 'electricity',
    icon: '⚡',
    title: 'Electricity & Sub-meters',
    body: 'The main meter’s tariff and each bedroom AC’s sub-meter, split the way the units were used, without a spreadsheet nightmare.',
    points: ['Main meter tariffs', 'Bedroom AC sub-meters', 'Shared and own units apart'],
  },
  {
    id: 'passbook',
    icon: '📖',
    title: 'Bank-Grade House Kitty Passbook',
    body: 'Every rupee in and out of the kitty, in a ledger anyone in the flat can read. Mistakes are reversed, never erased.',
    points: ['Reversible audit trail', 'Integer paise math', 'Transparent ledger'],
  },
]

/** What each tool does for a shared flat: true where it is built in. */
const COMPARISON: Array<{ feature: string; flatmate: true; splitwise: string | false; sheets: string | false }> = [
  { feature: 'One pooled House Kitty balance per flatmate', flatmate: true, splitwise: 'Person-to-person', sheets: 'By hand' },
  { feature: 'Landlord and provider payouts from the pool', flatmate: true, splitwise: false, sheets: 'By hand' },
  { feature: 'Reimburses whoever fronted a bill', flatmate: true, splitwise: 'Settle up by hand', sheets: 'By hand' },
  { feature: 'Room-tiered rent and HRA rent receipts', flatmate: true, splitwise: false, sheets: 'Templates' },
  { feature: 'GST on a bill, with invoice round-off', flatmate: true, splitwise: false, sheets: 'Formulas' },
  { feature: 'Electricity sub-meters split by units used', flatmate: true, splitwise: false, sheets: 'Formulas' },
  { feature: 'Shares add up to the bill in integer paise', flatmate: true, splitwise: false, sheets: 'Floating-point' },
  { feature: 'Audit trail that reverses, never deletes', flatmate: true, splitwise: 'Activity feed', sheets: 'Version history' },
]

const HIGHLIGHTS = ['100% integer paise arithmetic', 'PostgreSQL backed', 'Local-first ready', 'Zero ads & tracking']

export default function LandingPage() {
  const auth = useOptionalAuth()
  // Signed in, the workspace is the household's Month Hub.
  const workspace = auth?.user ? '/' : '/signup'
  return (
    <div className="min-h-screen bg-[var(--bg-base)] text-[var(--text-primary)] font-sans">
      <Navbar workspace={workspace} />
      <main>
        <Hero />
        <KittySection />
        <ModulesSection />
        <ComparisonSection />
        <PrivacySection />
        <FinalCta workspace={workspace} />
      </main>
      <Footer />
    </div>
  )
}

function Wordmark() {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-lg font-bold tracking-tight text-[var(--text-primary)]">flatmate</span>
      <span className="rounded-md bg-[var(--accent-glow)] px-2 py-0.5 font-mono text-xs font-bold text-[var(--accent)]">
        .dev
      </span>
    </span>
  )
}

function Navbar({ workspace }: { workspace: string }) {
  return (
    <header className="sticky top-0 z-30 border-b border-[var(--border-subtle)] bg-[color-mix(in_srgb,var(--bg-base)_82%,transparent)] backdrop-blur">
      <nav aria-label="flatmate.dev" className={`${SECTION} flex items-center justify-between gap-4 py-3`}>
        <Link to="/landing" aria-label="flatmate.dev home" className="shrink-0">
          <Wordmark />
        </Link>
        <ul className="m-0 hidden list-none items-center gap-6 p-0 md:flex">
          {NAV_LINKS.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                className="text-sm font-semibold text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
              >
                {link.label}
              </a>
            </li>
          ))}
        </ul>
        <div className="flex shrink-0 items-center gap-2">
          <Link
            to="/login"
            className="rounded-lg px-3 py-2 text-sm font-semibold text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
          >
            Sign In
          </Link>
          <Link to={workspace} className={`${PRIMARY_CTA} px-4 py-2`}>
            Launch Workspace
          </Link>
        </div>
      </nav>
    </header>
  )
}

function Hero() {
  return (
    <section aria-labelledby="landing-hero" className="relative overflow-hidden">
      {/* A soft glow behind the headline and the example month. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 left-1/2 h-[480px] w-[880px] -translate-x-1/2 rounded-full bg-[var(--accent-glow)] blur-3xl"
      />
      <div className={`${SECTION} relative grid items-center gap-12 py-16 sm:py-24 lg:grid-cols-[1.05fr_1fr]`}>
        <div className="space-y-6">
          <p className="m-0 inline-flex max-w-full items-center rounded-full border border-[var(--border-medium)] bg-[var(--bg-surface)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
            ✨ Built for flatmates in Bangalore, Mumbai, NCR & tech hubs · flatmate.dev
          </p>
          <h1
            id="landing-hero"
            className="m-0 text-4xl font-bold leading-[1.1] tracking-tight text-[var(--text-primary)] sm:text-5xl lg:text-6xl"
          >
            The financial OS for modern flatmates.
          </h1>
          <p className="m-0 max-w-xl text-base leading-relaxed text-[var(--text-secondary)] sm:text-lg">
            Stop managing ₹50,000 apartment rents and Furlenco subscriptions on Splitwise. Flatmate pools rent,
            utilities, and furniture into a single transparent House Kitty with bank-grade audit trails.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <Link to="/login" className={PRIMARY_CTA}>
              Open Live Demo <span aria-hidden="true">→</span>
            </Link>
            <Link to="/signup" className={SECONDARY_CTA}>
              Set Up Your Flat (Free)
            </Link>
          </div>
        </div>
        <ExampleMonth />
      </div>
    </section>
  )
}

/** A month in flatmate.dev, as an example: the kitty, who has paid in, and who it has paid. */
function ExampleMonth() {
  return (
    <figure
      aria-label="An example month in flatmate.dev"
      className="m-0 rounded-3xl border border-[var(--border-medium)] bg-[color-mix(in_srgb,var(--bg-surface)_78%,transparent)] p-5 shadow-[var(--shadow-lg)] backdrop-blur-xl sm:p-6"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">Month</div>
          <div className="text-sm font-bold text-[var(--text-primary)]">October 2026</div>
        </div>
        <span className="rounded-full bg-[var(--success-glow)] px-2.5 py-1 text-[11px] font-bold text-[var(--success)]">
          Settled
        </span>
      </div>

      <div className="mt-5 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
        <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
          House Kitty Balance
        </div>
        <div className="mt-1 font-mono text-3xl font-bold tabular-nums text-[var(--text-primary)]">₹48,250.00</div>
      </div>

      <div className="mt-5 space-y-2">
        <div className="flex items-center justify-between gap-3 text-xs">
          <span className="font-bold text-[var(--text-primary)]">Resident Collection</span>
          <span className="font-semibold text-[var(--text-secondary)]">3 of 3 flatmates covered · 100%</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-[var(--bg-muted)]" aria-hidden="true">
          <div className="h-full w-full rounded-full bg-[var(--success)]" />
        </div>
        <ul className="m-0 flex list-none flex-wrap gap-2 p-0 pt-1">
          {EXAMPLE_FLATMATES.map((name) => (
            <li
              key={name}
              className="rounded-full border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-2.5 py-1 text-[11px] font-semibold text-[var(--text-primary)]"
            >
              {name} <span className="text-[var(--success)]">(Paid)</span>
            </li>
          ))}
        </ul>
      </div>

      <ul className="m-0 mt-5 list-none space-y-2 border-t border-[var(--border-subtle)] p-0 pt-4">
        {EXAMPLE_PAYOUTS.map((payout) => (
          <li key={payout.payee} className="flex items-center justify-between gap-3 text-xs">
            <span className="min-w-0 truncate font-semibold text-[var(--text-primary)]">{payout.payee}</span>
            <span className="flex shrink-0 items-center gap-2">
              <span className="font-mono font-bold tabular-nums text-[var(--text-primary)]">{payout.amount}</span>
              <span className="rounded-full bg-[var(--success-glow)] px-2 py-0.5 text-[10px] font-bold text-[var(--success)]">
                Disbursed
              </span>
            </span>
          </li>
        ))}
      </ul>
    </figure>
  )
}

function SectionHeading({ id, eyebrow, title, body }: { id: string; eyebrow: string; title: string; body?: string }) {
  return (
    <div className="mx-auto max-w-2xl space-y-3 text-center">
      <p className={`m-0 ${EYEBROW}`}>{eyebrow}</p>
      <h2 id={id} className="m-0 text-3xl font-bold tracking-tight text-[var(--text-primary)] sm:text-4xl">
        {title}
      </h2>
      {body && <p className="m-0 text-base leading-relaxed text-[var(--text-secondary)]">{body}</p>}
    </div>
  )
}

function KittySection() {
  return (
    <section id="kitty" aria-labelledby="landing-kitty" className={`${SECTION} scroll-mt-20 py-16 sm:py-20`}>
      <SectionHeading
        id="landing-kitty"
        eyebrow="The House Kitty"
        title="Why Splitwise breaks for apartments"
        body="A flat is not a dinner bill. Rent, deposits and subscriptions go to one landlord and a few providers, every month, from everyone at once."
      />
      <div className="mt-10 grid gap-4 md:grid-cols-2">
        <WayCard title="The Splitwise Way" mark="❌" tone="danger" points={SPLITWISE_WAY} />
        <WayCard title="The flatmate.dev Way" mark="✅" tone="success" points={FLATMATE_WAY} />
      </div>
    </section>
  )
}

function WayCard({
  title,
  mark,
  tone,
  points,
}: {
  title: string
  mark: string
  tone: 'danger' | 'success'
  points: string[]
}) {
  const border = tone === 'success' ? 'border-[var(--success)]' : 'border-[var(--border-medium)]'
  const bullet = tone === 'success' ? 'bg-[var(--success)]' : 'bg-[var(--danger)]'
  return (
    <article className={`rounded-2xl border ${border} bg-[var(--bg-surface)] p-6 shadow-[var(--shadow-sm)]`}>
      <h3 className="m-0 text-lg font-bold text-[var(--text-primary)]">
        {title} <span aria-hidden="true">{mark}</span>
      </h3>
      <ul className="m-0 mt-4 list-none space-y-3 p-0">
        {points.map((point) => (
          <li key={point} className="flex items-start gap-3 text-sm leading-relaxed text-[var(--text-secondary)]">
            <span aria-hidden="true" className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${bullet}`} />
            {point}
          </li>
        ))}
      </ul>
    </article>
  )
}

function ModulesSection() {
  return (
    <section
      id="features"
      aria-labelledby="landing-features"
      className="scroll-mt-20 border-y border-[var(--border-subtle)] bg-[var(--bg-surface)] py-16 sm:py-20"
    >
      <div className={SECTION}>
        <SectionHeading
          id="landing-features"
          eyebrow="Features"
          title="Four modules, one month-end"
          body="Every bill a flat pays, kept in one place and settled through the kitty."
        />
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {MODULES.map((module) => (
            <article
              key={module.id}
              id={module.id}
              className="scroll-mt-20 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-base)] p-6"
            >
              <div className="flex items-center gap-3">
                <span
                  aria-hidden="true"
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--accent-glow)] text-xl"
                >
                  {module.icon}
                </span>
                <h3 className="m-0 text-lg font-bold text-[var(--text-primary)]">{module.title}</h3>
              </div>
              <p className="m-0 mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">{module.body}</p>
              <ul className="m-0 mt-4 flex list-none flex-wrap gap-2 p-0">
                {module.points.map((point) => (
                  <li
                    key={point}
                    className="rounded-full border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-2.5 py-1 text-[11px] font-semibold text-[var(--text-secondary)]"
                  >
                    {point}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}

function Cell({ value }: { value: true | string | false }) {
  if (value === true) {
    return (
      <span className="font-bold text-[var(--success)]">
        <span aria-hidden="true">✓</span>
        <span className="sr-only">Yes</span>
      </span>
    )
  }
  if (value === false) {
    return (
      <span className="text-[var(--text-tertiary)]">
        <span aria-hidden="true">—</span>
        <span className="sr-only">Not built in</span>
      </span>
    )
  }
  return <span className="text-[var(--text-secondary)]">{value}</span>
}

function ComparisonSection() {
  return (
    <section id="compare" aria-labelledby="landing-compare" className={`${SECTION} scroll-mt-20 py-16 sm:py-20`}>
      <SectionHeading
        id="landing-compare"
        eyebrow="Compare"
        title="Built for the flat, not the group trip"
        body="What a shared apartment needs month after month, against the tools most flats make do with."
      />
      <div className="mt-10 overflow-x-auto rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <caption className="sr-only">flatmate.dev compared with Splitwise and Google Sheets</caption>
          <thead>
            <tr className="border-b border-[var(--border-subtle)] text-left text-[11px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
              <th scope="col" className="px-5 py-4">Feature</th>
              <th scope="col" className="px-5 py-4 text-center text-[var(--accent)]">flatmate.dev</th>
              <th scope="col" className="px-5 py-4 text-center">Splitwise</th>
              <th scope="col" className="px-5 py-4 text-center">Google Sheets</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)]">
            {COMPARISON.map((row) => (
              <tr key={row.feature}>
                <th scope="row" className="px-5 py-3.5 text-left font-semibold text-[var(--text-primary)]">
                  {row.feature}
                </th>
                <td className="bg-[var(--accent-glow)] px-5 py-3.5 text-center">
                  <Cell value={row.flatmate} />
                </td>
                <td className="px-5 py-3.5 text-center">
                  <Cell value={row.splitwise} />
                </td>
                <td className="px-5 py-3.5 text-center">
                  <Cell value={row.sheets} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function PrivacySection() {
  return (
    <section id="privacy" aria-labelledby="landing-privacy" className={`${SECTION} scroll-mt-20 pb-16 sm:pb-20`}>
      <div className="rounded-2xl border border-[var(--border-medium)] bg-[var(--bg-elevated)] p-6 sm:p-8">
        <p className={`m-0 ${EYEBROW}`}>For developers, and anyone who cares about privacy</p>
        <h2 id="landing-privacy" className="m-0 mt-2 text-xl font-bold text-[var(--text-primary)] sm:text-2xl">
          Money math you can trust, and nobody watching.
        </h2>
        <ul className="m-0 mt-4 flex list-none flex-wrap items-center gap-x-3 gap-y-2 p-0 font-mono text-xs font-semibold text-[var(--text-secondary)] sm:text-sm">
          {HIGHLIGHTS.map((highlight, index) => (
            <li key={highlight} className="flex items-center gap-3">
              {index > 0 && <span aria-hidden="true" className="text-[var(--text-tertiary)]">·</span>}
              {highlight}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function FinalCta({ workspace }: { workspace: string }) {
  return (
    <section aria-labelledby="landing-cta" className={`${SECTION} pb-16 sm:pb-24`}>
      <div className="relative overflow-hidden rounded-3xl bg-[var(--accent)] px-6 py-12 text-center text-white sm:px-12">
        <div aria-hidden="true" className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-white/10 blur-2xl" />
        <h2 id="landing-cta" className="relative m-0 text-2xl font-bold tracking-tight sm:text-3xl">
          Ready to end month-end calculation drama?
        </h2>
        <p className="relative m-0 mx-auto mt-3 max-w-xl text-sm text-white/80 sm:text-base">
          Set up the flat once. Every month after that settles itself through the House Kitty.
        </p>
        <div className="relative mt-6 flex flex-col justify-center gap-3 sm:flex-row">
          <Link
            to={workspace}
            className="inline-flex items-center justify-center rounded-xl bg-white px-5 py-3 text-sm font-bold text-[var(--accent)] transition-opacity hover:opacity-90"
          >
            Launch Workspace
          </Link>
          <Link
            to="/login"
            className="inline-flex items-center justify-center rounded-xl border border-white/40 px-5 py-3 text-sm font-bold text-white transition-colors hover:bg-white/10"
          >
            Sign In
          </Link>
        </div>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="border-t border-[var(--border-subtle)]">
      <div
        className={`${SECTION} flex flex-col items-center justify-between gap-4 py-8 text-sm text-[var(--text-tertiary)] sm:flex-row`}
      >
        <div className="flex items-center gap-3">
          <Wordmark />
          <span>© {new Date().getFullYear()} flatmate.dev</span>
        </div>
        <nav aria-label="Footer" className="flex items-center gap-5">
          <a href="#privacy" className="transition-colors hover:text-[var(--text-primary)]">
            Privacy
          </a>
          <a href="#features" className="transition-colors hover:text-[var(--text-primary)]">
            Documentation
          </a>
        </nav>
      </div>
    </footer>
  )
}
