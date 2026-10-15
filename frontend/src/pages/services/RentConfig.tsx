/**
 * The master lease: what the rent is, who it goes to, and whether the landlord
 * issues receipts. One lease per apartment; each month's rent is generated from
 * it on the Rent screen.
 */
import { Fragment, useId, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  FileText,
  History,
  Home,
} from 'lucide-react'
import { clsx } from 'clsx'
import { AsyncSection, ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, Field, MoneyInput, SelectField, TextField } from '../../components/Form'
import { useToast } from '../../components/Toast'
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  CopyButton,
  EditButton,
  Panel,
  Pill,
  Rupees,
  StepPills,
} from '../../components/ui'
import { ContactModal, useContacts } from './PropertyContacts'
import { LeaseNoticeBanner } from './LeaseLifecycle'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { leaseDurationOptions } from '../../lib/lease'
import {
  escalatedPaise,
  formatDate,
  formatDateTime,
  formatInr,
  leaseEndIso,
  paiseToInput,
} from '../../lib/format'
import type { Apartment, EscalationType, RentLeaseConfig, Resident } from '../../api/types'

const DUE_DAYS = Array.from({ length: 28 }, (_, index) => {
  const day = String(index + 1)
  return { value: day, label: day }
})

// The same shapes the backend enforces, checked here so a typo is caught
// before saving rather than after.
// `items-start` keeps every input level with its label: without it a field
// stretches to the tallest one in its row and its input sinks.
const GRID_2 = 'grid grid-cols-1 md:grid-cols-2 gap-4 items-start'

const ESCALATION_CHOICES: Array<{ value: EscalationType; label: string }> = [
  { value: 'percent', label: 'Percentage Rate' },
  { value: 'fixed', label: 'Fixed Amount' },
  { value: 'none', label: 'No Escalation' },
]

/** A whole number within a range, or null when the text is not one. */
/** The lease editor shows one section at a time, in this order. */
type TenancyStep = 'contract' | 'term' | 'exit' | 'escalation'

// The four parts of the tenancy terms. The read-only card and the editor's
// steps both take their names from here, so the two always match.
const TENANCY_SECTIONS: Array<{ id: TenancyStep; title: string; hint: string }> = [
  {
    id: 'contract',
    title: 'Monthly Rent',
    hint: 'One lease and one monthly rent. How it is divided between flatmates is set on the Rent screen.',
  },
  {
    id: 'term',
    title: 'Contract Term',
    hint: 'When the agreement starts and how long it runs. The end date is worked out from these two.',
  },
  {
    id: 'exit',
    title: 'Exit Protections',
    hint: 'What leaving costs. The notice window opens this many days before the term ends.',
  },
  {
    id: 'escalation',
    title: 'Escalation Clause',
    hint: 'How the rent changes when the agreement is renewed for another term.',
  },
]

const sectionTitle = (id: TenancyStep) =>
  TENANCY_SECTIONS.find((section) => section.id === id)!.title

/** The editor's steps: the same sections, numbered, each pointing to the next. */
const TENANCY_STEPS = TENANCY_SECTIONS.map((section, index) => ({
  ...section,
  label: `${index + 1}. ${section.title}`,
  next: TENANCY_SECTIONS[index + 1]?.title ?? '',
}))

function wholeNumber(raw: string, low: number, high: number): number | null {
  if (!/^\d{1,4}$/.test(raw.trim())) return null
  const value = Number(raw.trim())
  return value >= low && value <= high ? value : null
}

/** A percentage from 0 to 100 with at most two decimal places, or null. */
function percentage(raw: string): number | null {
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(raw.trim())) return null
  const value = Number(raw.trim())
  return value <= 100 ? value : null
}


/** The Tenancy & Master Lease tab of Housing: the master lease and its landlord. */
export function LeaseContractSection({
  apartment,
  onApartmentChanged,
}: {
  /** The apartment the lease is for; its card is left out when not given. */
  apartment?: Apartment | null
  onApartmentChanged?: () => void
} = {}) {
  const lease = useResource<{ config: RentLeaseConfig | null }>('/api/rent/config')
  const toast = useToast()
  // A saved lease is shown, not edited, until the operator asks to edit it.
  const [editing, setEditing] = useState(false)
  // Who would share a deposit refund if the household served notice.
  const residents = useResource<{ items: Resident[] }>('/api/residents', { active_only: true })
  const activeResidents = residents.data?.items.length ?? 0

  return (
    <AsyncSection resource={lease} loadingLabel="Loading lease…">
      {(data) => (
        <div className="space-y-6">
          {data.config && (
            <>
              <LeaseNoticeBanner
                config={data.config}
                participantCount={activeResidents}
                onChanged={(message, detail) => {
                  toast.success(message, detail)
                  lease.reload()
                }}
              />
            </>
          )}
          {apartment && (
            <ApartmentProfileCard apartment={apartment} onSaved={onApartmentChanged ?? (() => {})} />
          )}
          {data.config && !editing ? (
            <TenancyTermsCard config={data.config} onEdit={() => setEditing(true)} />
          ) : (
            <LeaseForm
              // Remount on save so the form always starts from what is stored.
              key={data.config?.updated_at ?? 'new'}
              config={data.config}
              onSaved={() => {
                setEditing(false)
                lease.reload()
              }}
              onCancel={data.config ? () => setEditing(false) : undefined}
            />
          )}
          {data.config && <LandlordProfileCard config={data.config} onSaved={lease.reload} />}
          {data.config && <CompletedTermsPanel />}
        </div>
      )}
    </AsyncSection>
  )
}

function LeaseForm({
  config,
  onSaved,
  onCancel,
}: {
  config: RentLeaseConfig | null
  onSaved: () => void
  /** Leave the editor without saving; absent while the lease is first set up. */
  onCancel?: () => void
}) {
  const toast = useToast()
  const [rent, setRent] = useState(config ? paiseToInput(config.monthly_rent_paise) : '')
  const [rentPaise, setRentPaise] = useState(config?.monthly_rent_paise ?? 0)
  const [rentOk, setRentOk] = useState(Boolean(config))
  const [dueDay, setDueDay] = useState(String(config?.due_day_of_month ?? 5))
  // Only asked for when the lease is first set up; afterwards the landlord's
  // details are edited on the owner contact, which keeps the lease in step.
  const [name, setName] = useState('')
  const [touched, setTouched] = useState({ rent: false, name: false, renewal: false })
  const touch = (field: keyof typeof touched) =>
    setTouched((current) => (current[field] ? current : { ...current, [field]: true }))
  const [startDate, setStartDate] = useState(config?.lease_start_date ?? '')
  const [duration, setDuration] = useState(String(config?.duration_months ?? 11))
  const [noticeDays, setNoticeDays] = useState(String(config?.notice_period_days ?? 30))
  const [lockIn, setLockIn] = useState(String(config?.lock_in_months ?? 6))
  const [escalation, setEscalation] = useState<EscalationType>(
    config?.escalation_type ?? 'percent',
  )
  const [percent, setPercent] = useState(String(config?.escalation_percent ?? 5))
  const [renewalRent, setRenewalRent] = useState(
    config?.renewal_rent_paise != null ? paiseToInput(config.renewal_rent_paise) : '',
  )
  const [renewalPaise, setRenewalPaise] = useState(config?.renewal_rent_paise ?? 0)
  const [renewalOk, setRenewalOk] = useState(config?.renewal_rent_paise != null)
  // Only for the per-person line of the projection; the form works without it.
  const residents = useResource<{ items: Resident[] }>('/api/residents')
  const residentCount = (residents.data?.items ?? []).filter((r) => r.is_active).length

  const durationMonths = Number(duration)
  const endDate = startDate ? leaseEndIso(startDate, durationMonths) : null
  const noticeValue = wholeNumber(noticeDays, 0, 365)
  const lockInValue = wholeNumber(lockIn, 0, 60)
  const percentValue = percentage(percent)
  const durationOptions = leaseDurationOptions(durationMonths)

  const save = useMutation(
    () =>
      // Only the tenancy terms are sent. The backend keeps everything left
      // out -- the landlord's details, the deposit, the receipt preference --
      // as the lease already has it.
      api.post<RentLeaseConfig>('/api/rent/config', {
        ...(config ? {} : { landlord_name: name.trim() }),
        monthly_rent_paise: rentPaise,
        due_day_of_month: Number(dueDay),
        // The end date is left to the backend, which computes it from these two.
        lease_start_date: startDate || null,
        duration_months: durationMonths,
        notice_period_days: noticeValue,
        lock_in_months: lockInValue,
        escalation_type: escalation,
        escalation_percent: percentValue ?? config?.escalation_percent ?? 5,
        renewal_rent_paise: escalation === 'fixed' ? renewalPaise : null,
        // Not chosen here: the backend links or creates the Rent category under
        // Housing, and an existing lease keeps the one it has.
        expense_definition_id: null,
      }),
    {
      onSuccess: () => {
        toast.success(config ? 'Tenancy terms saved' : 'Lease set up')
        onSaved()
      },
    },
  )
  const nameError = !config && name.trim().length === 0 ? 'Landlord name is required.' : null
  const rentError = !rentOk || rentPaise <= 0 ? 'Monthly rent must be greater than zero.' : null
  const renewalError =
    escalation === 'fixed' && (!renewalOk || renewalPaise <= 0)
      ? 'Enter the agreed renewed rent.'
      : null

  // Which steps hold a value that stops the save, so their pill can say so.
  const invalidSteps: Record<TenancyStep, boolean> = {
    contract: Boolean(rentError || nameError),
    term: false,
    exit: noticeValue === null || lockInValue === null,
    escalation:
      (escalation === 'percent' && percentValue === null) ||
      (escalation === 'fixed' && Boolean(renewalError)),
  }
  const valid = !Object.values(invalidSteps).some(Boolean)
  const [step, setStep] = useState<TenancyStep>('contract')
  const stepIndex = TENANCY_STEPS.findIndex((entry) => entry.id === step)
  const current = TENANCY_STEPS[stepIndex]
  const ids = useId()
  const tabId = (id: TenancyStep) => `${ids}-step-${id}`
  const saveButton = (
    <button type="submit" className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
      {save.pending ? 'Saving…' : config ? 'Save Changes' : 'Set Up Lease'}
    </button>
  )

  // A required field is only called out once it has been edited, so a new
  // lease does not open covered in errors. While an amount is half typed and
  // does not parse, the money input's own message says exactly what is wrong.
  const amountMissing = (raw: string, ok: boolean, paise: number) =>
    !raw.trim() || (ok && paise <= 0)
  const shownRentError =
    touched.rent && rentError && amountMissing(rent, rentOk, rentPaise) ? rentError : null
  const shownNameError = touched.name ? nameError : null
  const shownRenewalError =
    touched.renewal && renewalError && amountMissing(renewalRent, renewalOk, renewalPaise)
      ? renewalError
      : null

  const projectedPaise =
    escalation === 'fixed'
      ? renewalOk && renewalPaise > 0
        ? renewalPaise
        : null
      : escalation === 'none'
        ? rentPaise
        : percentValue !== null
          ? escalatedPaise(rentPaise, percentValue)
          : null
  const showProjection = rentOk && rentPaise > 0 && projectedPaise !== null

  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        if (valid && !save.pending) void save.run(undefined)
      }}
    >
      <Panel
        // The card keeps its name while it is being edited.
        title="Master Tenancy Terms"
        icon={<FileText className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint={config ? `Last saved ${formatDateTime(config.updated_at)}` : undefined}
        actions={
          <>
            {onCancel && (
              <button type="button" className={BUTTON_SECONDARY} onClick={onCancel} disabled={save.pending}>
                Cancel
              </button>
            )}
            {saveButton}
          </>
        }
      >
        <StepPills
          steps={TENANCY_STEPS}
          current={step}
          flagged={invalidSteps}
          onSelect={setStep}
          label="Edit steps"
          tabId={tabId}
          panelId={`${ids}-step-panel`}
          className="pb-3 border-b border-[var(--border-subtle)]"
        />

        <div
          id={`${ids}-step-panel`}
          role="tabpanel"
          aria-labelledby={tabId(step)}
          className="space-y-4"
        >
          {save.error && <ErrorNotice error={save.error} />}
          <p className="m-0 text-xs text-[var(--text-secondary)]">{current.hint}</p>
          {step === 'contract' && (
            <div className={GRID_2}>
              {!config && (
                <TextField
                  label="Landlord name"
                  required
                  value={name}
                  onChange={(value) => {
                    touch('name')
                    setName(value)
                  }}
                  maxLength={200}
                  error={shownNameError}
                  hint="Their phone, PAN and bank details are added to the owner under Stakeholders & Contacts."
                />
              )}
              <MoneyInput
                label="Monthly rent"
                error={shownRentError}
                required
                value={rent}
                placeholder="e.g. 28,500"
                hint="The whole rent on the lease, in rupees. A month already generated keeps the amount it was charged at."
                onChange={(raw, parsed) => {
                  touch('rent')
                  setRent(raw)
                  setRentPaise(parsed.paise)
                  setRentOk(parsed.ok)
                }}
              />
              <SelectField
                label="Due day of month"
                value={dueDay}
                options={DUE_DAYS}
                onChange={setDueDay}
                hint="Rent is paid in advance, on or before this day."
              />
            </div>
          )}
          {step === 'term' && (
            <div className={GRID_2}>
              <DateField
                label="Lease Start Date"
                value={startDate}
                onChange={setStartDate}
                hint="Leave empty if you do not want the term tracked."
              />
              <SelectField
                label="Agreement Duration"
                value={duration}
                options={durationOptions}
                onChange={setDuration}
                hint={
                  endDate ? `Ends ${formatDate(endDate)}.` : 'The end date is worked out from the start date.'
                }
              />
            </div>
          )}
          {step === 'exit' && (
            <div className={GRID_2}>
              <NumberField
                label="Notice Period (Days)"
                value={noticeDays}
                onChange={setNoticeDays}
                error={noticeValue === null ? 'Enter a whole number of days, 0 to 365.' : null}
              />
              <NumberField
                label="Lock-in Period (Months)"
                value={lockIn}
                onChange={setLockIn}
                error={lockInValue === null ? 'Enter a whole number of months, 0 to 60.' : null}
              />
            </div>
          )}
          {step === 'escalation' && (
            <>
              <div role="radiogroup" aria-label="Escalation Method" className="flex flex-wrap gap-2">
                {ESCALATION_CHOICES.map((choice) => {
                  const selected = escalation === choice.value
                  return (
                    <button
                      key={choice.value}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setEscalation(choice.value)}
                      className={clsx(
                        'rounded-full border px-3.5 py-1.5 text-xs font-semibold transition-colors cursor-pointer',
                        selected
                          ? 'bg-[var(--accent)] border-[var(--accent)] text-white'
                          : 'bg-[var(--bg-surface)] border-[var(--border-medium)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                      )}
                    >
                      {choice.label}
                    </button>
                  )
                })}
              </div>
              {escalation === 'percent' && (
                <div className="max-w-sm">
                  <NumberField
                    label="Renewal Escalation Rate (%)"
                    value={percent}
                    onChange={setPercent}
                    suffix="%"
                    decimal
                    error={percentValue === null ? 'Enter a rate from 0 to 100, up to two decimals.' : null}
                  />
                </div>
              )}
              {escalation === 'fixed' && (
                <div className="max-w-sm">
                  <MoneyInput
                    label="Agreed Renewed Rent"
                    error={shownRenewalError}
                    required
                    value={renewalRent}
                    placeholder="e.g. 30,000"
                    hint="The whole monthly rent for the next term."
                    onChange={(raw, parsed) => {
                      touch('renewal')
                      setRenewalRent(raw)
                      setRenewalPaise(parsed.paise)
                      setRenewalOk(parsed.ok)
                    }}
                  />
                </div>
              )}
              {showProjection && (
                <div
                  className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-2.5"
                  role="status"
                  aria-label="Projected renewal rent"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                      Projected Renewal Rent
                    </span>
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-[var(--accent-glow)] text-[var(--accent)] border border-blue-500/20">
                      {escalation === 'percent'
                        ? `+${Number.isInteger(percentValue) ? percentValue!.toFixed(1) : percentValue}% escalation`
                        : escalation === 'fixed'
                          ? 'Fixed renewal rent'
                          : 'No escalation'}
                    </span>
                  </div>

                  <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 pt-2.5 border-t border-[var(--border-subtle)]">
                    <div>
                      <div className="tabular text-lg font-bold text-[var(--text-primary)]">
                        {formatInr(projectedPaise)}{' '}
                        <span className="text-xs font-normal text-[var(--text-secondary)]">/ month</span>
                      </div>
                      <div className="tabular text-xs text-[var(--text-secondary)]">
                        Current {formatInr(rentPaise)} (
                        {formatInr(projectedPaise - rentPaise, { sign: 'always' })} total)
                      </div>
                    </div>

                    {residentCount > 0 && (
                      <div className="sm:text-right">
                        <div className="tabular text-sm font-bold text-[var(--text-primary)]">
                          ~{formatInr(Math.round(projectedPaise / residentCount))}{' '}
                          <span className="text-xs font-normal text-[var(--text-secondary)]">
                            / resident
                          </span>
                        </div>
                        <div className="tabular text-xs text-[var(--text-secondary)]">
                          {formatInr(Math.round((projectedPaise - rentPaise) / residentCount), {
                            sign: 'always',
                          })}{' '}
                          per person across {residentCount} residents
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 pt-3 border-t border-[var(--border-subtle)]">
          {stepIndex > 0 ? (
            <button
              type="button"
              className={BUTTON_SECONDARY}
              onClick={() => setStep(TENANCY_STEPS[stepIndex - 1].id)}
            >
              <ArrowLeft className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Back
            </button>
          ) : (
            <span />
          )}
          {/* Separate keys, so reaching the last step mounts a new Save button
              rather than turning the Next button being clicked into a submit
              button mid-click -- which would save the form instead of moving on. */}
          {stepIndex < TENANCY_STEPS.length - 1 ? (
            <button
              key="next"
              type="button"
              className={BUTTON_SECONDARY}
              onClick={() => setStep(TENANCY_STEPS[stepIndex + 1].id)}
            >
              Next: {current.next}
              <ArrowRight className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            </button>
          ) : (
            <Fragment key="save">{saveButton}</Fragment>
          )}
        </div>
      </Panel>
    </form>
  )
}

/** A short numeric text input, optionally with a unit shown after it. */
function NumberField({
  label,
  value,
  onChange,
  error,
  suffix,
  decimal,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  error?: string | null
  suffix?: string
  decimal?: boolean
}) {
  return (
    <Field label={label} error={error}>
      {({ id, describedBy, invalid }) => (
        <div className="flex items-center gap-2">
          <input
            id={id}
            type="text"
            inputMode={decimal ? 'decimal' : 'numeric'}
            value={value}
            maxLength={6}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            onChange={(event) => onChange(event.target.value)}
          />
          {suffix && (
            <span className="shrink-0 text-sm font-semibold text-[var(--text-secondary)]" aria-hidden="true">
              {suffix}
            </span>
          )}
        </div>
      )}
    </Field>
  )
}

function ProfileFact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <dt className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {label}
      </dt>
      <dd className="m-0 text-sm text-[var(--text-primary)] break-words">{children}</dd>
    </div>
  )
}

// Each card's name, shared with the dialog that edits it, so a section keeps
// its name whether it is being read or edited.
const APARTMENT_TITLE = 'Apartment & Property Details'
const LANDLORD_TITLE = 'Landlord Profile & Payout'

const NOT_RECORDED = <span className="text-[var(--text-tertiary)]">Not recorded</span>

/** A PIN code: six digits, the first never 0. The backend checks the same. */
const PINCODE_PATTERN = /^[1-9][0-9]{5}$/

/** The address parts, in postal order, with what each is called and may hold. */
const ADDRESS_PARTS = [
  { field: 'flat_number', label: 'Flat Number', maxLength: 50, placeholder: '1805' },
  { field: 'tower_block', label: 'Tower / Wing / Block', maxLength: 50, placeholder: 'CG6' },
  { field: 'society_name', label: 'Society / Complex Name', maxLength: 150, placeholder: 'Supertech Capetown' },
  { field: 'locality_street', label: 'Locality / Sector / Street', maxLength: 200, placeholder: 'Sector 74' },
  { field: 'city', label: 'City', maxLength: 100, placeholder: 'Noida' },
  { field: 'state', label: 'State', maxLength: 100, placeholder: 'Uttar Pradesh' },
] as const

type AddressField = (typeof ADDRESS_PARTS)[number]['field'] | 'pincode'

/**
 * The flat itself, by its postal address. The parts are printed, in order, as
 * the premises on HRA rent receipts.
 */
export function ApartmentProfileCard({
  apartment,
  onSaved,
}: {
  apartment: Apartment
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const cityState = [apartment.city, apartment.state].filter(Boolean).join(', ')
  const fact = (value: string | null | undefined) => value || NOT_RECORDED
  return (
    <Panel
      title={APARTMENT_TITLE}
      icon={<Home className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      actions={<EditButton label="Edit apartment" onClick={() => setEditing(true)} />}
    >
      <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4" aria-label="Apartment profile">
        <ProfileFact label="Flat / Unit">
          {apartment.flat_number ? <span className="font-semibold">{apartment.flat_number}</span> : NOT_RECORDED}
        </ProfileFact>
        <ProfileFact label="Tower / Block">{fact(apartment.tower_block)}</ProfileFact>
        <ProfileFact label="Society / Complex">{fact(apartment.society_name)}</ProfileFact>
        <ProfileFact label="Locality / Sector">{fact(apartment.locality_street)}</ProfileFact>
        <ProfileFact label="City & State">{fact(cityState)}</ProfileFact>
        <ProfileFact label="PIN Code">
          {apartment.pincode ? <span className="tabular">{apartment.pincode}</span> : NOT_RECORDED}
        </ProfileFact>
      </dl>
      {editing && (
        <EditApartmentModal
          apartment={apartment}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            onSaved()
          }}
        />
      )}
    </Panel>
  )
}

function EditApartmentModal({
  apartment,
  onClose,
  onSaved,
}: {
  apartment: Apartment
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const [values, setValues] = useState<Record<AddressField, string>>(() => ({
    flat_number: apartment.flat_number ?? '',
    tower_block: apartment.tower_block ?? '',
    society_name: apartment.society_name ?? '',
    locality_street: apartment.locality_street ?? '',
    city: apartment.city ?? '',
    state: apartment.state ?? '',
    pincode: apartment.pincode ?? '',
  }))
  const [submitted, setSubmitted] = useState(false)
  const set = (field: AddressField) => (value: string) => setValues((now) => ({ ...now, [field]: value }))
  const errors: Partial<Record<AddressField, string>> = {}
  for (const { field, maxLength } of ADDRESS_PARTS) {
    if (values[field].trim().length > maxLength) errors[field] = `Keep it to ${maxLength} characters.`
  }
  const pincode = values.pincode.trim()
  // Checked as it is typed, unlike the rest, so a mistyped PIN shows at once.
  const pincodeError =
    pincode && !PINCODE_PATTERN.test(pincode) ? 'A PIN code is 6 digits and does not start with 0.' : null
  const valid = Object.keys(errors).length === 0 && !pincodeError
  const save = useMutation(
    () =>
      api.patch<Apartment>(
        `/api/apartments/${encodeURIComponent(apartment.apartment_id)}`,
        Object.fromEntries(
          (Object.keys(values) as AddressField[]).map((field) => [field, values[field].trim() || null]),
        ),
      ),
    {
      onSuccess: () => {
        toast.success('Apartment saved')
        onSaved()
      },
    },
  )
  return (
    <Modal
      title={APARTMENT_TITLE}
      description="The address is printed, part by part, as the premises on every HRA rent receipt."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form="edit-apartment" className={BUTTON_PRIMARY} disabled={save.pending}>
            {save.pending ? 'Saving…' : 'Save Changes'}
          </button>
        </>
      }
    >
      <form
        id="edit-apartment"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setSubmitted(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <div className={GRID_2}>
          {ADDRESS_PARTS.map(({ field, label, maxLength, placeholder }) => (
            <TextField
              key={field}
              label={label}
              value={values[field]}
              onChange={set(field)}
              maxLength={maxLength + 10}
              placeholder={placeholder}
              error={submitted ? (errors[field] ?? null) : null}
            />
          ))}
          <TextField
            label="PIN Code"
            value={values.pincode}
            onChange={set('pincode')}
            inputMode="numeric"
            maxLength={6}
            placeholder="201301"
            hint="6 digits"
            error={pincodeError}
          />
        </div>
      </form>
    </Modal>
  )
}

/**
 * Who the rent goes to, in full. The details belong to the owner contact,
 * which keeps the lease in step, so Edit opens that contact.
 */
export function LandlordProfileCard({
  config,
  onSaved,
}: {
  config: RentLeaseConfig
  onSaved: () => void
}) {
  const toast = useToast()
  const contacts = useContacts()
  const [editing, setEditing] = useState(false)
  const owner = contacts.data?.items.find((contact) => contact.role === 'owner') ?? null
  return (
    <Panel
      title={LANDLORD_TITLE}
      icon={<Building2 className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="The owner's details are kept on their contact under Stakeholders & Contacts, and in step with the lease."
      actions={
        contacts.data ? <EditButton label="Edit landlord" onClick={() => setEditing(true)} /> : undefined
      }
    >
      <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4" aria-label="Landlord profile">
        <ProfileFact label="Name">
          <span className="font-semibold">{config.landlord_name}</span>
        </ProfileFact>
        <ProfileFact label="Phone">
          {config.landlord_phone ? <span className="tabular">{config.landlord_phone}</span> : NOT_RECORDED}
        </ProfileFact>
        <ProfileFact label="Email">{config.landlord_email ?? NOT_RECORDED}</ProfileFact>
        <ProfileFact label="PAN">
          {config.landlord_pan ? <span className="tabular">{config.landlord_pan}</span> : NOT_RECORDED}
        </ProfileFact>
        <ProfileFact label="Bank Account">
          {config.landlord_bank_account ? (
            <span className="tabular">{config.landlord_bank_account}</span>
          ) : (
            NOT_RECORDED
          )}
        </ProfileFact>
        <ProfileFact label="IFSC">
          {config.landlord_bank_ifsc ? (
            <span className="tabular">{config.landlord_bank_ifsc}</span>
          ) : (
            NOT_RECORDED
          )}
        </ProfileFact>
        <div className="sm:col-span-2">
          <ProfileFact label="UPI ID">
            {config.landlord_upi_id ? (
              <span className="inline-flex items-center gap-1.5 min-w-0">
                <span className="tabular break-all">{config.landlord_upi_id}</span>
                <CopyButton text={config.landlord_upi_id} label="Copy landlord UPI ID" iconOnly />
              </span>
            ) : (
              NOT_RECORDED
            )}
          </ProfileFact>
        </div>
      </dl>
      {editing && (
        <ContactModal
          contact={owner}
          title={LANDLORD_TITLE}
          onClose={() => setEditing(false)}
          onDone={(saved) => {
            setEditing(false)
            contacts.reload()
            onSaved()
            toast.success(`${saved.name} saved`)
          }}
        />
      )}
    </Panel>
  )
}

interface LeaseTermRecord {
  term_id: string
  term_number: number
  start_date: string
  end_date: string
  monthly_rent_paise: number
  escalation_applied_percent: number
  archived_at: string
}

/** Every earlier term of the lease, as it was when it was renewed. */
export function CompletedTermsPanel() {
  const terms = useResource<{ items: LeaseTermRecord[] }>('/api/rent/lease/terms')
  // Nothing is shown until there is a completed term to show.
  if (!terms.data || terms.data.items.length === 0) return null
  return (
    <Panel
      title="Completed Terms"
      icon={<History className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Each earlier term of the lease, kept when it was renewed."
    >
      <div className="overflow-x-auto min-w-0">
        <table className="w-full text-xs min-w-[520px] border-collapse">
          <caption className="sr-only">Completed lease terms</caption>
          <thead>
            <tr className="border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold">
              <th scope="col" className="pb-3 px-2 font-bold text-center">Term</th>
              <th scope="col" className="pb-3 px-2 font-bold text-center">From</th>
              <th scope="col" className="pb-3 px-2 font-bold text-center">To</th>
              <th scope="col" className="pb-3 px-2 font-bold text-center">Monthly Rent</th>
              <th scope="col" className="pb-3 px-2 font-bold text-center">Escalation</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {terms.data.items.map((term) => (
              <tr key={term.term_id}>
                <th scope="row" className="py-2.5 px-2 text-center font-semibold">
                  {term.term_number}
                </th>
                <td className="py-2.5 px-2 text-center">{formatDate(term.start_date)}</td>
                <td className="py-2.5 px-2 text-center">{formatDate(term.end_date)}</td>
                <td className="py-2.5 px-2 text-center">
                  <Rupees paise={term.monthly_rent_paise} />
                </td>
                <td className="py-2.5 px-2 text-center">{`${term.escalation_applied_percent}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

/** `5` -> `5th`, `22` -> `22nd`. */
function ordinal(day: number): string {
  const teen = day % 100 >= 11 && day % 100 <= 13
  const suffix = teen ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[day % 10] ?? 'th'
  return `${day}${suffix}`
}

function TermFact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {label}
      </dt>
      <dd className="m-0 text-xs text-[var(--text-secondary)] space-y-1">{children}</dd>
    </div>
  )
}

/** The saved tenancy terms, read-only, with the way into the editor. */
export function TenancyTermsCard({
  config,
  onEdit,
}: {
  config: RentLeaseConfig
  onEdit: () => void
}) {
  const lifecycle = config.lifecycle
  // The backend's projection when the term is tracked; the same rule otherwise.
  const projected =
    lifecycle?.projected_renewal_rent_paise ??
    (config.escalation_type === 'fixed'
      ? (config.renewal_rent_paise ?? config.monthly_rent_paise)
      : config.escalation_type === 'none'
        ? config.monthly_rent_paise
        : escalatedPaise(config.monthly_rent_paise, config.escalation_percent))
  const escalation =
    config.escalation_type === 'fixed'
      ? 'Fixed renewed rent'
      : config.escalation_type === 'none'
        ? 'No escalation'
        : `${config.escalation_percent}% increment on renewal`
  const progress = !lifecycle
    ? null
    : lifecycle.is_expired
      ? { tone: 'danger' as const, label: 'Term ended' }
      : lifecycle.current_month_index === 0
        ? { tone: 'neutral' as const, label: 'Not started' }
        : {
            tone: lifecycle.is_notice_window_active ? ('warning' as const) : ('accent' as const),
            label: `Month ${Math.min(lifecycle.current_month_index, lifecycle.duration_months)} of ${lifecycle.duration_months}`,
          }

  return (
    <Panel
      title="Master Tenancy Terms"
      icon={<FileText className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={`Term ${config.term_number} of the master lease. How the rent is divided between flatmates is set on the Rent screen.`}
      actions={
        <EditButton label="Edit tenancy terms" onClick={onEdit} />
      }
    >
      <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4" aria-label="Tenancy terms">
        <TermFact label={sectionTitle('contract')}>
          <div className="text-base font-bold text-[var(--text-primary)]">
            <Rupees paise={config.monthly_rent_paise} />
          </div>
          <div>{`Due on the ${ordinal(config.due_day_of_month)} of every month`}</div>
        </TermFact>
        <TermFact label={sectionTitle('term')}>
          {config.lease_start_date && config.lease_end_date ? (
            <div className="font-semibold text-[var(--text-primary)]">
              {`${formatDate(config.lease_start_date)} to ${formatDate(config.lease_end_date)}`}
            </div>
          ) : (
            <div className="text-[var(--text-tertiary)]">Start date not entered</div>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            {`${config.duration_months} Months`}
            {progress && <Pill tone={progress.tone}>{progress.label}</Pill>}
            {config.lease_status === 'notice_served' && <Pill tone="warning">Notice served</Pill>}
          </div>
        </TermFact>
        <TermFact label={sectionTitle('exit')}>
          <div className="font-semibold text-[var(--text-primary)]">
            {`Notice Period: ${config.notice_period_days} Days`}
          </div>
          <div>
            {`Lock-in: ${config.lock_in_months} Months`}
            {lifecycle?.lock_in_end_date && ` (until ${formatDate(lifecycle.lock_in_end_date)})`}
          </div>
        </TermFact>
        <TermFact label={sectionTitle('escalation')}>
          <div className="font-semibold text-[var(--text-primary)]">{escalation}</div>
          <div>
            Projected renewal: <Rupees paise={projected} />
          </div>
        </TermFact>
      </dl>
    </Panel>
  )
}
