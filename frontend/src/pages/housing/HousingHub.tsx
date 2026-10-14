/**
 * Housing: everything about the flat that lasts beyond a month.
 *
 * The lease, the security deposit, the rooms, the people the household deals
 * with, and the papers -- one page, five tabs, with a summary of all of them
 * at the top. Each tab is the same section the app already uses, so its forms,
 * validation and tables behave exactly as they do elsewhere. The open tab is
 * kept in the address (`?tab=deposits`), so a link or a reload lands on it.
 *
 * Nothing here follows the settlement month: this is the flat's standing
 * state, so the month picker is hidden on this page.
 */
import { useSearchParams } from 'react-router-dom'
import {
  BedDouble,
  FileSignature,
  FileText,
  Home,
  Phone,
  ShieldCheck,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { BUTTON_QUIET, CopyButton, Pill, Rupees } from '../../components/ui'
import { useResource } from '../../lib/useApi'
import { formatDate } from '../../lib/format'
import type { RentLeaseConfig } from '../../api/types'
import { LeaseContractSection } from '../services/RentConfig'
import { SecurityDepositSection, useDepositSummary } from '../services/SecurityDeposits'
import { RoomsInventory, useRooms } from '../services/PropertyRooms'
import { ContactsSection, useContacts } from '../services/PropertyContacts'
import { DocumentVaultSection } from '../services/DocumentVault'
import { telHref } from '../ResidentProfile'

export const HOUSING_TABS = [
  { value: 'lease', label: 'Lease & Contract', icon: FileSignature },
  { value: 'deposits', label: 'Security Deposit', icon: ShieldCheck },
  { value: 'rooms', label: 'Rooms Inventory', icon: BedDouble },
  { value: 'contacts', label: 'Stakeholders & Contacts', icon: Users },
  { value: 'documents', label: 'Document Vault', icon: FileText },
] as const

export type HousingTab = (typeof HOUSING_TABS)[number]['value']

const FUNDING_LABELS = {
  primary_signatory: 'Primary Signatory',
  direct_co_tenants: 'Co-Tenants',
} as const

export default function HousingHubPage() {
  const { apartment } = useApp()
  return <HousingHub apartmentName={apartment?.name ?? null} />
}

export function HousingHub({ apartmentName }: { apartmentName: string | null }) {
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: HousingTab = HOUSING_TABS.some((entry) => entry.value === requested)
    ? (requested as HousingTab)
    : 'lease'

  return (
    <Page
      title="Housing"
      subtitle="Central configuration for the lease contract, security deposit, physical spaces, stakeholders and documents."
    >
      <HousingSummary apartmentName={apartmentName} />
      <div className="tabs" role="tablist" aria-label="Housing">
        {HOUSING_TABS.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            id={`housing-tab-${value}`}
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === value}
            aria-controls={`housing-panel-${value}`}
            onClick={() => setParams(value === 'lease' ? {} : { tab: value }, { replace: true })}
          >
            <Icon className="w-3.5 h-3.5 shrink-0 inline-block mr-1.5 align-[-2px]" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`housing-panel-${tab}`}
        aria-labelledby={`housing-tab-${tab}`}
        className="space-y-4"
      >
        {tab === 'lease' && <LeaseContractSection />}
        {tab === 'deposits' && <SecurityDepositSection />}
        {tab === 'rooms' && <RoomsInventory />}
        {tab === 'contacts' && <ContactsSection />}
        {tab === 'documents' && <DocumentVaultSection />}
      </div>
    </Page>
  )
}

/* ------------------------------------------------------------ the summary -- */

function Tile({
  label,
  icon: Icon,
  children,
}: {
  label: string
  icon: LucideIcon
  children: React.ReactNode
}) {
  return (
    <section
      aria-label={label}
      className="min-w-0 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3.5 shadow-xs space-y-1"
    >
      <h2 className="m-0 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        <Icon className="w-3.5 h-3.5 shrink-0 text-[var(--accent)]" aria-hidden="true" />
        {label}
      </h2>
      <div className="text-xs text-[var(--text-secondary)] space-y-1 min-w-0">{children}</div>
    </section>
  )
}

const MISSING = <span className="text-[var(--text-tertiary)]">Not set up</span>

/** The flat at a glance: who, where, on what terms, and how the deposit stands. */
export function HousingSummary({ apartmentName }: { apartmentName: string | null }) {
  const rooms = useRooms()
  const lease = useResource<{ config: RentLeaseConfig | null }>('/api/rent/config')
  const deposits = useDepositSummary()
  const contacts = useContacts()

  const counts = rooms.data?.summary
  const config = lease.data?.config ?? null
  const lifecycle = config?.lifecycle ?? null
  const deposit = deposits.data
  const owner = contacts.data?.items.find((contact) => contact.role === 'owner') ?? null

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3" aria-label="Housing summary">
      <Tile label="Apartment" icon={Home}>
        <div className="text-sm font-bold text-[var(--text-primary)] truncate">
          {apartmentName ?? '—'}
        </div>
      </Tile>

      <Tile label="Physical Space" icon={BedDouble}>
        {!counts ? (
          '—'
        ) : counts.total_rooms === 0 ? (
          MISSING
        ) : (
          <div className="font-semibold text-[var(--text-primary)]">
            {`${counts.total_rooms} Room${counts.total_rooms === 1 ? '' : 's'} · ${counts.occupied_rooms} Occupied · ${counts.vacant_rooms} Vacant`}
          </div>
        )}
      </Tile>

      <Tile label="Active Lease" icon={FileSignature}>
        {!lease.data ? (
          '—'
        ) : !config ? (
          MISSING
        ) : (
          <>
            <div className="font-semibold text-[var(--text-primary)]">
              Monthly Rent: <Rupees paise={config.monthly_rent_paise} />
            </div>
            {lifecycle && lifecycle.current_month_index > 0 && (
              <div>{`Term: Month ${Math.min(lifecycle.current_month_index, lifecycle.duration_months)} of ${lifecycle.duration_months}`}</div>
            )}
            {config.lease_end_date && (
              <div className="flex flex-wrap items-center gap-1.5">
                {`Expires: ${formatDate(config.lease_end_date)}`}
                {lifecycle?.is_expired ? (
                  <Pill tone="danger">Expired</Pill>
                ) : lifecycle?.is_notice_window_active ? (
                  <Pill tone="warning">Notice window</Pill>
                ) : null}
              </div>
            )}
          </>
        )}
      </Tile>

      <Tile label="Security Deposit" icon={ShieldCheck}>
        {!deposit ? (
          '—'
        ) : deposit.master_deposit_paise == null ? (
          MISSING
        ) : (
          <>
            <div className="font-semibold text-[var(--text-primary)]">
              <Rupees paise={deposit.master_deposit_paise} /> Escrow
            </div>
            <div>
              {deposit.funding_mode
                ? `Funding Mode: ${FUNDING_LABELS[deposit.funding_mode]}`
                : 'Funding mode not chosen'}
            </div>
            {deposit.total_slots_count > 0 && (
              <Pill tone={deposit.is_balanced ? 'success' : 'warning'}>
                {deposit.is_balanced ? 'Balanced' : 'Unbalanced'}
              </Pill>
            )}
          </>
        )}
      </Tile>

      <Tile label="Landlord / Owner" icon={Users}>
        {!contacts.data ? (
          '—'
        ) : !owner ? (
          MISSING
        ) : (
          <>
            <div className="font-semibold text-[var(--text-primary)] truncate">{owner.name}</div>
            {owner.phone && (
              <a
                className={`${BUTTON_QUIET} !px-0 !py-0 tabular`}
                href={telHref(owner.phone)}
                aria-label={`Call ${owner.name}`}
              >
                <Phone className="w-3 h-3 shrink-0" aria-hidden="true" />
                {owner.phone}
              </a>
            )}
            {owner.upi_id && (
              <div className="flex items-center gap-1 min-w-0">
                <span className="tabular truncate">{owner.upi_id}</span>
                <CopyButton text={owner.upi_id} label={`Copy UPI ID of ${owner.name}`} iconOnly />
              </div>
            )}
          </>
        )}
      </Tile>
    </div>
  )
}
