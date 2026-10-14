/**
 * Services & Utilities.
 *
 * One card per recurring monthly bill. Rent and electricity are live and link
 * to their own screens; the rest are placeholders for services that are
 * planned but not built. The lease, deposit, rooms and documents are not here:
 * they are the home itself, under Housing. Every figure comes from
 * `GET /api/services`.
 */
import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Building2,
  Droplets,
  Flame,
  HandPlatter,
  type LucideIcon,
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
import { formatMonth, formatMonthShort } from '../../lib/format'
import type { LeaseLifecycle, ServicesOverview } from '../../api/types'

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

export function ServicesHub({ month }: { month: string }) {
  const overview = useResource<ServicesOverview>('/api/services', { month })
  return (
    <Page
      title="Services & Utilities"
      subtitle="The household's monthly bills: rent collection and payout, utility meters, and recurring services."
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <RentCard month={month} overview={overview} />
        <ElectricityCard month={month} overview={overview} />
        {PLANNED_SERVICES.map((service) => (
          <ServiceCard
            key={service.title}
            title={service.title}
            icon={service.icon}
            badge={<Pill>Planned</Pill>}
            planned
          >
            <p className="m-0 text-xs text-[var(--text-secondary)]">{service.body}</p>
          </ServiceCard>
        ))}
      </div>
    </Page>
  )
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

function RentCard({ month, overview }: CardProps) {
  const config = overview.data?.rent.config ?? null
  const cycle = overview.data?.rent.cycle ?? null
  const lifecycle = config?.lifecycle ?? null
  const noticeServed = config?.lease_status === 'notice_served'

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
          <Pill>Not generated</Pill>
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
            <Fact label="Due day">
              {config ? `Day ${config.due_day_of_month}` : NOT_CONFIGURED}
            </Fact>
            <Fact label={formatMonthShort(month)}>
              {!config
                ? NOT_CONFIGURED
                : cycle
                  ? `${collectedPercent}% collected`
                  : 'Cycle not generated'}
            </Fact>
            {lifecycle && <TermProgress lifecycle={lifecycle} noticeServed={noticeServed} />}
          </dl>
        )}
      </AsyncSection>
    </ServiceCard>
  )
}

function TermProgress({
  lifecycle,
  noticeServed,
}: {
  lifecycle: LeaseLifecycle
  noticeServed: boolean
}) {
  const days = lifecycle.days_until_expiry
  const urgent = lifecycle.is_notice_window_active || lifecycle.is_expired
  const label = `${lifecycle.duration_months}-Month Term (${lifecycle.elapsed_percent}% elapsed)`
  return (
    <div className="pt-1 space-y-1.5">
      <div className="flex items-center justify-between gap-2 text-xs min-w-0">
        <dt className="text-[var(--text-secondary)] truncate">{label}</dt>
        <dd className="m-0 shrink-0">
          <Pill tone={urgent ? 'warning' : 'neutral'}>
            {noticeServed
              ? 'Notice served'
              : lifecycle.is_expired
                ? `Expired ${-days}d ago`
                : `Expires in ${days}d`}
          </Pill>
        </dd>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={lifecycle.elapsed_percent}
        className="h-1.5 rounded-full bg-[var(--bg-elevated)] overflow-hidden"
      >
        <div
          className={
            urgent ? 'h-full rounded-full bg-amber-500' : 'h-full rounded-full bg-[var(--accent)]'
          }
          style={{ width: `${lifecycle.elapsed_percent}%` }}
        />
      </div>
    </div>
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
