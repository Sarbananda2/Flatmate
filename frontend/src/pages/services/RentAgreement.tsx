/**
 * The household's rental agreement: how the rent is divided between
 * flatmates.
 *
 * It belongs with the monthly rent, not with the lease: the lease says what
 * the flat costs, the agreement says who pays which part of it. It is shown
 * read-only; "Change Agreement Model" opens a dialog to change it, and saving
 * sends only the split, so the rest of the lease is left as it is.
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import { Layers } from 'lucide-react'
import { ErrorNotice, Modal } from '../../components/Primitives'
import { BUTTON_PRIMARY, BUTTON_SECONDARY, EditButton, Panel, Pill, Rupees } from '../../components/ui'
import { useToast } from '../../components/Toast'
import { api } from '../../api/client'
import { useMutation } from '../../lib/useApi'
import { formatMonth } from '../../lib/format'
import type { RentCycle, RentLeaseConfig, RentPreview, RentSplitMode } from '../../api/types'

type ModelStatus = 'available' | 'under_development'

/** How a household can contract and divide rent. Two are built; the rest show the direction. */
export const RENTAL_MODELS: ReadonlyArray<{
  id: RentSplitMode | 'sqft_ratio' | 'unbundled_maintenance' | 'individual_direct'
  title: string
  description: string
  status: ModelStatus
}> = [
  {
    id: 'equal',
    title: 'Single Master Lease (Equal Split)',
    description:
      'One joint lease to the landlord, divided equally among participating flatmates with zero-paisa drift.',
    status: 'available',
  },
  {
    id: 'room_tiered',
    title: 'Room-Tiered Fixed Pricing',
    description:
      'Unequal fixed rent per bedroom based on room size, attached washroom, or balcony amenities.',
    status: 'available',
  },
  {
    id: 'sqft_ratio',
    title: 'Square Footage / Floor-Area Ratio',
    description:
      'Mathematical allocation proportional to private bedroom carpet area plus shared common spaces.',
    status: 'under_development',
  },
  {
    id: 'unbundled_maintenance',
    title: 'Unbundled (Rent + Society Maintenance)',
    description:
      'Bifurcated monthly cycle: base house rent to the owner (with HRA slips) and society maintenance.',
    status: 'under_development',
  },
  {
    id: 'individual_direct',
    title: 'Individual Direct Contracts (PG / Co-Living)',
    description:
      'Landlord contracts with each resident directly; residents carry no collective pool liability.',
    status: 'under_development',
  },
]

/** The card's name, shared with the dialog that edits it. */
const AGREEMENT_TITLE = 'Rental Agreement Structure'

export const MODEL_TITLES: Record<RentSplitMode, string> = {
  equal: 'Single Master Lease (Equal Split)',
  room_tiered: 'Room-Tiered Fixed Pricing',
}

/** The models as choices. Only the built ones can be picked. */
export function AgreementModelPicker({
  value,
  onChange,
  roomTieredAvailable,
  disabled,
}: {
  value: RentSplitMode
  onChange: (mode: RentSplitMode) => void
  roomTieredAvailable: boolean
  disabled?: boolean
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Rental agreement structure"
      className="grid grid-cols-1 md:grid-cols-2 gap-3"
    >
      {RENTAL_MODELS.map((model) => {
        const selectable = model.status === 'available'
        const checked = model.id === value
        return (
          <button
            key={model.id}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-disabled={!selectable || undefined}
            aria-label={model.title}
            disabled={disabled || !selectable}
            onClick={() => selectable && onChange(model.id as RentSplitMode)}
            className={clsx(
              'flex items-start gap-3 rounded-xl border p-3.5 min-w-0 text-left',
              checked
                ? 'border-[var(--accent)] bg-[var(--accent-glow)]'
                : selectable
                  ? 'border-[var(--border-medium)] bg-[var(--bg-elevated)] cursor-pointer'
                  : 'border-[var(--border-subtle)] bg-[var(--bg-elevated)] opacity-70 cursor-not-allowed',
            )}
          >
            <span
              className={clsx(
                'mt-0.5 flex w-4 h-4 shrink-0 items-center justify-center rounded-full border-2',
                checked ? 'border-[var(--accent)]' : 'border-[var(--border-medium)]',
              )}
              aria-hidden="true"
            >
              {checked && <span className="w-2 h-2 rounded-full bg-[var(--accent)]" />}
            </span>
            <span className="min-w-0 flex-1 space-y-1.5">
              <span className="block text-sm font-bold text-[var(--text-primary)]">
                {model.title}
              </span>
              <span className="block text-xs text-[var(--text-secondary)]">
                {model.description}
              </span>
              {!selectable ? (
                <Pill>Under Development</Pill>
              ) : model.id === 'room_tiered' ? (
                <Pill tone={roomTieredAvailable ? 'accent' : 'warning'}>
                  {roomTieredAvailable ? 'Room Rents Add Up' : 'Needs Room Base Rents'}
                </Pill>
              ) : (
                <Pill tone="success">Always Available</Pill>
              )}
            </span>
          </button>
        )
      })}
    </div>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {label}
      </dt>
      <dd className="m-0 text-xs text-[var(--text-secondary)] space-y-1">{children}</dd>
    </div>
  )
}

/** The agreement as it stands, read-only, with the way to change it. */
export function RentAgreementCard({
  config,
  cycle,
  preview,
  month,
  onSaved,
}: {
  config: RentLeaseConfig
  cycle: RentCycle | null
  preview: RentPreview | null
  month: string
  onSaved: () => void
}) {
  const [changing, setChanging] = useState(false)
  const model = config.rent_split_mode
  const source = cycle ?? preview
  // A generated month keeps the split it was generated with.
  const monthSplit = cycle ? cycle.split_mode : model
  const tieredAvailable = Boolean(preview?.room_tiered.available)

  return (
    <Panel
      title={AGREEMENT_TITLE}
      icon={<Layers className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="How the household divides the rent between flatmates."
      actions={
        <EditButton label="Edit agreement model" onClick={() => setChanging(true)} />
      }
    >
      <dl className="m-0 grid grid-cols-1 md:grid-cols-2 gap-4" aria-label="Rental agreement">
        <Fact label="Active Agreement Model">
          <div className="text-sm font-bold text-[var(--text-primary)]">{MODEL_TITLES[model]}</div>
          {cycle && cycle.split_mode !== model && (
            <div>{`${formatMonth(month)} was generated with ${MODEL_TITLES[cycle.split_mode]}.`}</div>
          )}
        </Fact>
        <Fact label="Current Share Calculation">
          {monthSplit === 'equal' ? (
            source && source.participant_count > 0 ? (
              <div className="font-semibold text-[var(--text-primary)]">
                {`Equal share among ${source.participant_count} participant${source.participant_count === 1 ? '' : 's'} (`}
                <Rupees paise={source.base_share_paise} />
                /resident)
              </div>
            ) : (
              <div>Equal share among the residents taking part.</div>
            )
          ) : (
            <div className="font-semibold text-[var(--text-primary)]">
              Each resident pays their room&apos;s base rent.
            </div>
          )}
          {!cycle && model === 'room_tiered' && preview && !tieredAvailable && (
            <div className="text-[var(--warning)]">{preview.room_tiered.blocked_reason}</div>
          )}
        </Fact>
      </dl>

      {changing && (
        <ChangeAgreementModal
          config={config}
          roomTieredAvailable={tieredAvailable}
          onClose={() => setChanging(false)}
          onSaved={() => {
            setChanging(false)
            onSaved()
          }}
        />
      )}
    </Panel>
  )
}

function ChangeAgreementModal({
  config,
  roomTieredAvailable,
  onClose,
  onSaved,
}: {
  config: RentLeaseConfig
  roomTieredAvailable: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const [model, setModel] = useState<RentSplitMode>(config.rent_split_mode)
  const unchanged = model === config.rent_split_mode

  const save = useMutation(
    () =>
      api.post<RentLeaseConfig>('/api/rent/config', { rent_split_mode: model }),
    {
      onSuccess: (saved) => {
        toast.success('Agreement saved', MODEL_TITLES[saved.rent_split_mode])
        onSaved()
      },
    },
  )

  return (
    <Modal
      title={AGREEMENT_TITLE}
      description="How every month's rent is divided from now on. A month already generated keeps the split it was charged with."
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={unchanged || save.pending}
            onClick={() => void save.run(undefined)}
          >
            {save.pending ? 'Saving…' : 'Save Model'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {save.error && <ErrorNotice error={save.error} />}
        <AgreementModelPicker
          value={model}
          onChange={setModel}
          roomTieredAvailable={roomTieredAvailable}
          disabled={save.pending}
        />
        {model === 'room_tiered' && !roomTieredAvailable && (
          <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
            The room base rents do not add up to the lease rent yet, so rent cannot be generated
            this way until they do. Set them under Housing, in Rooms Inventory.
          </p>
        )}
      </div>
    </Modal>
  )
}
