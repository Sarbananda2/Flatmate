/**
 * The household's rental agreement: how the rent is divided between
 * flatmates, and whether the landlord issues receipts.
 *
 * It belongs with the monthly rent, not with the lease: the lease says what
 * the flat costs, the agreement says who pays which part of it. It is shown
 * read-only; "Change Agreement Model" opens a dialog to change it, and saving
 * sends only these two settings, so the rest of the lease is left as it is.
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

/** On or off: does the landlord issue a receipt for each payout? */
export function ReceiptSwitch({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
      <div className="min-w-0">
        <span id="landlord-receipts-label" className="block text-sm font-bold text-[var(--text-primary)]">
          Do you receive receipts from the landlord?
        </span>
        <p id="landlord-receipts-hint" className="m-0 mt-1 text-xs text-[var(--text-secondary)]" aria-live="polite">
          {checked
            ? "Receipts enabled — A paid month displays a 'Receipt Pending' badge until attached. Payouts are never blocked."
            : 'Reference only — Payouts require only date, funder, and UTR/reference. No landlord receipt is requested.'}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby="landlord-receipts-label"
        aria-describedby="landlord-receipts-hint"
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative w-10 h-6 rounded-full shrink-0 transition-colors cursor-pointer border border-[var(--border-medium)]',
          checked ? 'bg-[var(--accent)]' : 'bg-[var(--bg-surface)]',
        )}
      >
        <span
          className={clsx(
            'absolute top-0.5 left-0.5 w-[18px] h-[18px] rounded-full transition-transform',
            checked ? 'translate-x-4 bg-white' : 'bg-[var(--text-tertiary)]',
          )}
          aria-hidden="true"
        />
      </button>
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
      title="Rental Agreement Structure"
      icon={<Layers className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="How the household divides the rent between flatmates, and how the landlord confirms a payout."
      actions={
        <EditButton label="Edit agreement model" onClick={() => setChanging(true)} />
      }
    >
      <dl className="m-0 grid grid-cols-1 md:grid-cols-3 gap-4" aria-label="Rental agreement">
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
        <Fact label="Receipt Preferences">
          <div className="font-semibold text-[var(--text-primary)]">
            {`Requires Landlord Receipts: ${config.requires_landlord_receipt ? 'Yes' : 'No'}`}
          </div>
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
  const [receipts, setReceipts] = useState(config.requires_landlord_receipt)
  const unchanged =
    model === config.rent_split_mode && receipts === config.requires_landlord_receipt

  const save = useMutation(
    () =>
      api.post<RentLeaseConfig>('/api/rent/config', {
        rent_split_mode: model,
        requires_landlord_receipt: receipts,
      }),
    {
      onSuccess: (saved) => {
        toast.success('Agreement saved', MODEL_TITLES[saved.rent_split_mode])
        onSaved()
      },
    },
  )

  return (
    <Modal
      title="Change Agreement Model"
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
            this way until they do. Set them on the Rooms tab under Housing.
          </p>
        )}
        <ReceiptSwitch checked={receipts} onChange={setReceipts} disabled={save.pending} />
      </div>
    </Modal>
  )
}
