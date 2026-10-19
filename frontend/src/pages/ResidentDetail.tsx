import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { LogOut, Receipt } from 'lucide-react'
import { Page } from '../components/Layout'
import { AsyncSection } from '../components/Primitives'
import { useToast } from '../components/Toast'
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from '../components/ui'
import { useResource } from '../lib/useApi'
import type { ResidentDetail } from '../api/types'
import { ClearanceSlipPanel, ExitClearanceModal } from './ExitClearanceModal'
import {
  BuildingLogisticsCard,
  ContactCard,
  EditProfileDialog,
  KycComplianceCard,
  ProfessionalCard,
  StatusNotesCard,
  TenancyTermsCard,
  UpiCard,
} from './ResidentProfile'
import { AssignedRoomCard } from './services/PropertyRooms'
import { ResidentDepositCard } from './services/SecurityDeposits'

/**
 * Who the resident is: their room, deposit, contact details and tenancy.
 * What they owe and have paid is on their statement page, under Statements.
 */
export default function ResidentDetailPage() {
  const { residentId = '' } = useParams()
  const resource = useResource<ResidentDetail>(
    residentId ? `/api/residents/${encodeURIComponent(residentId)}` : null,
  )

  return (
    <Page
      title={resource.data?.resident.display_name ?? 'Resident'}
      subtitle="Room assignment, security deposit, and contact details."
      breadcrumbs={[
        { label: 'Residents', to: '/residents' },
        { label: resource.data?.resident.display_name ?? residentId },
      ]}
      actions={
        <Link
          to={`/statements/residents/${encodeURIComponent(residentId)}`}
          className={BUTTON_SECONDARY}
        >
          <Receipt className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          View Financial Statements
        </Link>
      }
    >
      <AsyncSection resource={resource}>
        {(data) => <Body data={data} residentId={residentId} onReload={() => resource.reload()} />}
      </AsyncSection>
    </Page>
  )
}

function Body({
  data,
  residentId,
  onReload,
}: {
  data: ResidentDetail
  residentId: string
  onReload: () => void
}) {
  const active = Boolean(data.resident.is_active)
  const toast = useToast()
  const [editingProfile, setEditingProfile] = useState(false)
  const [clearing, setClearing] = useState(false)

  return (
    <>
      {active ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-4 py-3">
          <span className="min-w-0 text-xs text-[var(--text-secondary)]">
            Moving out? One guided clearance settles their deposit, their operating balance and
            the move-out date together.
          </span>
          <button type="button" className={BUTTON_PRIMARY} onClick={() => setClearing(true)}>
            <LogOut className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Start Clearance &amp; Move-Out
          </button>
        </div>
      ) : (
        <ClearanceSlipPanel residentId={residentId} />
      )}

      {clearing && (
        <ExitClearanceModal
          residentId={residentId}
          residentName={data.resident.display_name}
          onClose={() => setClearing(false)}
          onCleared={() => {
            onReload()
            toast.success(`${data.resident.display_name} has moved out`)
          }}
        />
      )}

      {/* Left, where they live and on what terms; right, who they are and how
          to reach them. */}
      <div className="grid grid--halves">
        <div className="stack">
          <AssignedRoomCard residentId={residentId} />
          <TenancyTermsCard resident={data.resident} onReload={onReload} />
          <ResidentDepositCard residentId={residentId} />
          <BuildingLogisticsCard resident={data.resident} onReload={onReload} />
        </div>
        <div className="stack">
          <ContactCard resident={data.resident} onEdit={() => setEditingProfile(true)} />
          <KycComplianceCard resident={data.resident} onReload={onReload} />
          <ProfessionalCard resident={data.resident} onReload={onReload} />
          <UpiCard resident={data.resident} />
        </div>
      </div>

      {editingProfile && (
        <EditProfileDialog
          resident={data.resident}
          onClose={() => setEditingProfile(false)}
          onSaved={() => {
            setEditingProfile(false)
            onReload()
            toast.success('Profile saved')
          }}
        />
      )}

      <StatusNotesCard resident={data.resident} />
    </>
  )
}
