/**
 * Housing: everything about the flat that lasts beyond a month.
 *
 * The lease, the security deposit, the rooms, the people the household deals
 * with, and the papers -- one page, five tabs. Each tab is the same section the
 * app already uses, so its forms, validation and tables behave exactly as they
 * do elsewhere, and each card on it is edited on its own. The open tab is
 * kept in the address (`?tab=deposits`), so a link or a reload lands on it.
 *
 * Nothing here follows the settlement month: this is the flat's standing
 * state, so the month picker is hidden on this page.
 */
import { useSearchParams } from 'react-router-dom'
import { BedDouble, FileSignature, FileText, ShieldCheck, Users } from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import type { Apartment } from '../../api/types'
import { LeaseContractSection } from '../services/RentConfig'
import { SecurityDepositSection } from '../services/SecurityDeposits'
import { RoomsInventory } from '../services/PropertyRooms'
import { ContactsSection } from '../services/PropertyContacts'
import { DocumentVaultSection } from '../services/DocumentVault'

export const HOUSING_TABS = [
  { value: 'lease', label: 'Tenancy & Master Lease', icon: FileSignature },
  { value: 'deposits', label: 'Security Deposit', icon: ShieldCheck },
  { value: 'rooms', label: 'Rooms Inventory', icon: BedDouble },
  { value: 'contacts', label: 'Stakeholders & Contacts', icon: Users },
  { value: 'documents', label: 'Document Vault', icon: FileText },
] as const

export type HousingTab = (typeof HOUSING_TABS)[number]['value']

export default function HousingHubPage() {
  const { apartment, reloadApartment } = useApp()
  return <HousingHub apartment={apartment} onApartmentChanged={reloadApartment} />
}

export function HousingHub({
  apartment,
  onApartmentChanged,
}: {
  apartment: Apartment | null
  onApartmentChanged: () => void
}) {
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
        {tab === 'lease' && (
          <LeaseContractSection apartment={apartment} onApartmentChanged={onApartmentChanged} />
        )}
        {tab === 'deposits' && <SecurityDepositSection />}
        {tab === 'rooms' && <RoomsInventory />}
        {tab === 'contacts' && <ContactsSection />}
        {tab === 'documents' && <DocumentVaultSection />}
      </div>
    </Page>
  )
}
