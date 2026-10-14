/**
 * Household & Property: the flat itself, the people it is rented through and
 * looked after by, and the papers that go with it.
 *
 * Three tabs -- rooms, contacts, documents -- each its own module. The open tab
 * is kept in the address (`?tab=contacts`), so a link or a reload lands on it.
 */
import { useSearchParams } from 'react-router-dom'
import { BedDouble, FileText, Users } from 'lucide-react'
import { Page } from '../../components/Layout'
import { RoomsInventory } from './PropertyRooms'
import { ContactsSection } from './PropertyContacts'
import { DocumentVaultSection } from './DocumentVault'

const TABS = [
  { value: 'rooms', label: 'Rooms Inventory', icon: BedDouble },
  { value: 'contacts', label: 'Stakeholders & Contacts', icon: Users },
  { value: 'documents', label: 'Document Vault', icon: FileText },
] as const

type Tab = (typeof TABS)[number]['value']

export default function PropertyHubPage() {
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: Tab = TABS.some((entry) => entry.value === requested) ? (requested as Tab) : 'rooms'

  return (
    <Page
      title="Household & Property"
      subtitle="The flat's rooms and who occupies them, the owner, broker and caretaker, and the legal documents that go with the tenancy."
      breadcrumbs={[
        { label: 'Housing & Utilities', to: '/services' },
        { label: 'Household & Property' },
      ]}
    >
      <div className="tabs" role="tablist" aria-label="Household & Property">
        {TABS.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            id={`property-tab-${value}`}
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === value}
            aria-controls={`property-panel-${value}`}
            onClick={() => setParams(value === 'rooms' ? {} : { tab: value }, { replace: true })}
          >
            <Icon className="w-3.5 h-3.5 shrink-0 inline-block mr-1.5 align-[-2px]" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`property-panel-${tab}`}
        aria-labelledby={`property-tab-${tab}`}
        className="space-y-4"
      >
        {tab === 'rooms' && <RoomsInventory />}
        {tab === 'contacts' && <ContactsSection />}
        {tab === 'documents' && <DocumentVaultSection />}
      </div>
    </Page>
  )
}
