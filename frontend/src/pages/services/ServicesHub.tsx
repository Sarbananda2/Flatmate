/**
 * Services & Utilities.
 *
 * One card per recurring monthly bill, each a module of its own: rent,
 * electricity and furniture rental are live and link to their own screens; the
 * rest are placeholders for services that are planned but not built. Each card
 * loads and fails on its own, so one service that cannot be shown never takes
 * the others with it. The lease, deposit, rooms and documents are not here:
 * they are the home itself, under Housing.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Building2,
  Droplets,
  Flame,
  HandPlatter,
  type LucideIcon,
  Sofa,
  Wifi,
  Zap,
} from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection } from '../../components/Primitives'
import {
  BUTTON_PRIMARY,
  Pill,
  Rupees,
  StatementPill,
} from '../../components/ui'
import { type Resource, useResource } from '../../lib/useApi'
import { formatDate, formatInr, formatMonth, formatMonthShort } from '../../lib/format'
import type { FurnitureRentalOverview, ServicesOverview } from '../../api/types'

const PLANNED_SERVICES: Array<{ title: string; icon: LucideIcon; body: string }> = [
  {
    title: 'Water & Tankers',
    icon: Droplets,
    body: 'Tracking private water tankers, municipal water bills, and capacity logs.',
  },
  {
    title: 'Cooking Gas (LPG)',
    icon: Flame,
    body: 'Piped natural gas meter readings or shared commercial LPG cylinder bookings.',
  },
  {
    title: 'Broadband & WiFi',
    icon: Wifi,
    body: 'Monthly ISP fiber plans and due date tracking.',
  },
  {
    title: 'House Help & Cook',
    icon: HandPlatter,
    body: 'Maid and cook fixed monthly salaries and leave tracking.',
  },
]

export default function ServicesHubPage() {
  const { month } = useApp()
  return <ServicesHub key={month} month={month} />
}

/** The live service modules, in the order the hub shows them. */
const SERVICE_MODULES: Array<{
  key: string
  title: string
  Card: (props: CardProps) => ReactNode
}> = [
  { key: 'rent', title: 'Rent', Card: RentCard },
  { key: 'electricity', title: 'Electricity', Card: ElectricityCard },
  { key: 'furniture', title: 'Furniture Rental', Card: FurnitureRentalCard },
]

export function ServicesHub({ month }: { month: string }) {
  const overview = useResource<ServicesOverview>('/api/services', { month })
  return (
    <Page
      title="Services & Utilities"
      subtitle="The household's monthly bills: rent collection and payout, utility meters, and recurring services."
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {SERVICE_MODULES.map(({ key, title, Card }) => (
          <ServiceCardErrorBoundary key={key} title={title}>
            <Card month={month} overview={overview} />
          </ServiceCardErrorBoundary>
        ))}
        {PLANNED_SERVICES.map((service) => (
          <ServiceCardErrorBoundary key={service.title} title={service.title}>
            <ServiceCard title={service.title} icon={service.icon} badge={<Pill>Planned</Pill>} planned>
              <p className="m-0 text-xs text-[var(--text-secondary)]">{service.body}</p>
            </ServiceCard>
          </ServiceCardErrorBoundary>
        ))}
      </div>
    </Page>
  )
}

/**
 * Keeps one service card's failure to itself: if a card throws while it
 * renders, it is replaced by a short notice and the other cards carry on.
 */
export class ServiceCardErrorBoundary extends Component<
  { title: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`The ${this.props.title} card failed to render`, error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <section
        aria-label={this.props.title}
        className="flex flex-col gap-2 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 sm:p-5 min-w-0"
      >
        <h2 className="m-0 font-bold text-base text-[var(--text-primary)]">{this.props.title}</h2>
        <p className="m-0 text-xs text-[var(--danger)]" role="alert">
          This service could not be shown. Reload the page to try again.
        </p>
      </section>
    )
  }
}

function ServiceCard({
  title,
  icon: Icon,
  badge,
  actions,
  planned,
  children,
}: {
  title: string
  icon: LucideIcon
  badge?: ReactNode
  actions?: ReactNode
  planned?: boolean
  children: ReactNode
}) {
  return (
    <section
      aria-label={title}
      className={
        planned
          ? 'flex flex-col gap-4 rounded-2xl border border-dashed border-[var(--border-medium)] p-4 sm:p-5 min-w-0'
          : 'flex flex-col gap-4 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 sm:p-5 shadow-xs min-w-0'
      }
    >
      <div className="flex items-center justify-between gap-2 min-w-0">
        <h2 className="m-0 font-bold text-base text-[var(--text-primary)] tracking-tight flex items-center gap-2 min-w-0">
          <Icon
            className={
              planned
                ? 'w-4 h-4 shrink-0 text-[var(--text-tertiary)]'
                : 'w-4 h-4 shrink-0 text-[var(--accent)]'
            }
            aria-hidden="true"
          />
          <span className="truncate">{title}</span>
        </h2>
        {badge}
      </div>
      <div className="flex-1 space-y-3 min-w-0">{children}</div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </section>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs min-w-0">
      <dt className="text-[var(--text-secondary)] shrink-0">{label}</dt>
      <dd className="m-0 font-semibold text-[var(--text-primary)] truncate">{children}</dd>
    </div>
  )
}

/* ------------------------------------------------------------------- rent -- */

const NOT_CONFIGURED = (
  <span className="font-normal text-[var(--text-tertiary)]">Not configured</span>
)

interface CardProps {
  month: string
  overview: Resource<ServicesOverview>
}

function RentCard({ overview }: CardProps) {
  const config = overview.data?.rent.config ?? null
  const cycle = overview.data?.rent.cycle ?? null

  // Whole percent, rounded down so an incomplete collection never reads as 100%.
  const collectedPercent =
    cycle && cycle.total_rent_paise > 0
      ? Math.min(100, Math.floor((cycle.collected_paise * 100) / cycle.total_rent_paise))
      : 0

  return (
    <ServiceCard
      title="Rent"
      icon={Building2}
      badge={
        cycle ? (
          <Pill tone={cycle.disbursement_status === 'disbursed' ? 'success' : 'warning'}>
            {cycle.disbursement_status === 'disbursed' ? 'Disbursed' : 'Pending'}
          </Pill>
        ) : config ? (
          // The open month is charged by itself; this is a month that is not.
          <Pill>Not billed</Pill>
        ) : overview.data ? (
          <Pill tone="warning">No lease</Pill>
        ) : undefined
      }
      actions={
        <Link to="/services/rent" className={BUTTON_PRIMARY}>
          Open Rent Hub
        </Link>
      }
    >
      <AsyncSection resource={overview} loadingLabel="Loading lease…">
        {() => (
          <dl className="m-0 space-y-2">
            <Fact label="Contract">
              {config ? (
                <>
                  <Rupees paise={config.monthly_rent_paise} />
                  <span className="font-normal text-[var(--text-secondary)]">/mo</span>
                </>
              ) : (
                NOT_CONFIGURED
              )}
            </Fact>
            <Fact label="Landlord">{config ? config.landlord_name : NOT_CONFIGURED}</Fact>
            {/* The month's own due date once it is charged; until then, the lease's day. */}
            <Fact label="Due date">
              {cycle?.due_date
                ? formatDate(cycle.due_date)
                : config
                  ? `Day ${config.due_day_of_month}`
                  : NOT_CONFIGURED}
            </Fact>
            {/* What residents have paid into the house kitty towards the month's rent. */}
            <Fact label="Kitty collection">
              {!cycle ? (
                config ? (
                  <span className="font-normal text-[var(--text-tertiary)]">Not billed</span>
                ) : (
                  NOT_CONFIGURED
                )
              ) : collectedPercent === 100 ? (
                '100% collected'
              ) : (
                `${formatInr(cycle.collected_paise)} of ${formatInr(cycle.total_rent_paise)} (${collectedPercent}%)`
              )}
            </Fact>
          </dl>
        )}
      </AsyncSection>
    </ServiceCard>
  )
}

/* ------------------------------------------------------------ electricity -- */

function ElectricityCard({ month, overview }: CardProps) {
  const forMonth = overview.data?.electricity.bill ?? null
  // With nothing settling this month, fall back to the most recent bill on file.
  const bill = forMonth ?? overview.data?.electricity.latest_bill ?? null

  return (
    <ServiceCard
      title="Electricity"
      icon={Zap}
      badge={
        overview.data ? (
          forMonth ? (
            <StatementPill status={forMonth.statement_status} />
          ) : (
            <Pill tone="warning">No bill for {formatMonthShort(month)}</Pill>
          )
        ) : undefined
      }
      actions={
        <Link to="/services/electricity" className={BUTTON_PRIMARY}>
          Open Power Hub
        </Link>
      }
    >
      <AsyncSection resource={overview} loadingLabel="Loading bills…">
        {() => (
          <dl className="m-0 space-y-2">
            <Fact label="Provider">{bill?.provider_name ?? '—'}</Fact>
            <Fact label="Usage split">Tapo smart plugs</Fact>
            {bill ? (
              <>
                <Fact label="Latest bill">
                  <Rupees paise={bill.amount_paise} />
                  <span className="font-normal text-[var(--text-secondary)]">
                    {' '}
                    · {bill.total_kwh} kWh
                  </span>
                </Fact>
                <Fact label="Settles in">
                  {bill.settlement_period ? formatMonth(bill.settlement_period) : '—'}
                </Fact>
              </>
            ) : (
              <Fact label="Latest bill">None entered yet</Fact>
            )}
          </dl>
        )}
      </AsyncSection>
    </ServiceCard>
  )
}

/* -------------------------------------------------------- furniture rental -- */

function FurnitureRentalCard({ month }: CardProps) {
  const furniture = useResource<FurnitureRentalOverview>('/api/services/furniture', { month })
  const data = furniture.data
  const live = Boolean(data?.service)
  const bill = data?.month ?? null

  return (
    <ServiceCard
      title="Furniture Rental"
      icon={Sofa}
      badge={
        data ? live ? <Pill tone="success">Active</Pill> : <Pill>Not set up</Pill> : undefined
      }
      actions={
        <Link to="/services/furniture" className={BUTTON_PRIMARY}>
          {live || !data ? 'Open Furniture Hub' : 'Set up furniture rental'}
        </Link>
      }
    >
      <AsyncSection resource={furniture} loadingLabel="Loading furniture…">
        {(overview) =>
          overview.service && overview.config ? (
            <dl className="m-0 space-y-2">
              {overview.services && overview.services.length > 1 ? (
                <Fact label="Providers">
                  {overview.services.map((entry) => entry.config.provider_name).join(', ')}
                </Fact>
              ) : (
                <Fact label="Provider">{overview.config.provider_name}</Fact>
              )}
              <Fact label="In the flat">
                {overview.active_item_count} item{overview.active_item_count === 1 ? '' : 's'}
              </Fact>
              <Fact label="Monthly total">
                <Rupees paise={overview.monthly_total_paise} />
                <span className="font-normal text-[var(--text-secondary)]">/mo</span>
              </Fact>
              <Fact label={formatMonthShort(month)}>
                {bill?.expense ? (
                  <>
                    Billed <Rupees paise={bill.expense.amount_paise} />
                  </>
                ) : (
                  <span className="font-normal text-[var(--warning)]">Not billed yet</span>
                )}
              </Fact>
            </dl>
          ) : (
            <p className="m-0 text-xs text-[var(--text-secondary)]">
              Track a Furlenco or Rentomojo subscription: each month&apos;s bill split between
              everyone for shared items, and to each person for their own.
            </p>
          )
        }
      </AsyncSection>
    </ServiceCard>
  )
}
