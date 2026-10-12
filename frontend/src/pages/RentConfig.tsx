/**
 * The master lease: what the rent is, who it goes to, and whether the landlord
 * issues receipts. One lease per apartment; each month's rent is generated from
 * it on the Rent screen.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUpRight,
  Building2,
  CalendarClock,
  CreditCard,
  FileText,
  Layers,
  ReceiptText,
  TrendingUp,
} from 'lucide-react'
import { clsx } from 'clsx'
import { Page } from '../components/Layout'
import { AsyncSection, ErrorNotice } from '../components/Primitives'
import { DateField, Field, MoneyInput, SelectField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { BUTTON_PRIMARY, BUTTON_SECONDARY, Panel, Pill } from '../components/ui'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import {
  escalatedPaise,
  formatDate,
  formatDateTime,
  formatInr,
  leaseEndIso,
  paiseToInput,
} from '../lib/format'
import type { EscalationType, RentLeaseConfig, Resident } from '../api/types'

const DUE_DAYS = Array.from({ length: 28 }, (_, index) => {
  const day = String(index + 1)
  return { value: day, label: day }
})

// The same shapes the backend enforces, checked here so a typo is caught
// before saving rather than after.
// `items-start` keeps every input level with its label: without it a field
// stretches to the tallest one in its row and its input sinks.
const GRID_2 = 'grid grid-cols-1 md:grid-cols-2 gap-4 items-start'

// How a household can contract and divide rent. Only the first is built; the
// rest are shown so the direction is visible, and cannot be chosen.
const RENTAL_MODELS = [
  {
    id: 'equal_split',
    title: 'Single Master Lease (Equal Split)',
    description:
      'One joint lease to the landlord, divided equally among participating flatmates with zero-paisa drift.',
    status: 'active',
  },
  {
    id: 'room_tiered',
    title: 'Room-Tiered Fixed Pricing',
    description:
      'Unequal fixed rent per bedroom based on room size, attached washroom, or balcony amenities.',
    status: 'under_development',
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
] as const

const DURATIONS = [6, 11, 12, 24, 36]

const ESCALATION_CHOICES: Array<{ value: EscalationType; label: string }> = [
  { value: 'percent', label: 'Percentage Rate' },
  { value: 'fixed', label: 'Fixed Amount' },
  { value: 'none', label: 'No Escalation' },
]

/** A whole number within a range, or null when the text is not one. */
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

const PAN_PATTERN = /^[A-Z]{5}[0-9]{4}[A-Z]$/
const IFSC_PATTERN = /^[A-Z]{4}0[A-Z0-9]{6}$/
const PHONE_PATTERN = /^[+]?[0-9\s-]{10,15}$/
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const UPI_PATTERN = /^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,63}$/

export default function RentConfigPage() {
  const lease = useResource<{ config: RentLeaseConfig | null }>('/api/rent/config')

  return (
    <Page
      title="Lease Configuration"
      subtitle="Landlord profile, contract rent, due dates, PAN, and receipt preferences for the master lease."
      breadcrumbs={[
        { label: 'Housing & Utilities', to: '/services' },
        { label: 'Rent', to: '/services/rent' },
        { label: 'Lease Configuration' },
      ]}
      actions={
        <Link to="/services/rent" className={BUTTON_SECONDARY}>
          <ArrowUpRight className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          Open monthly rent
        </Link>
      }
    >
      <AsyncSection resource={lease} loadingLabel="Loading lease…">
        {(data) => (
          <LeaseForm
            // Remount on save so the form always starts from what is stored.
            key={data.config?.updated_at ?? 'new'}
            config={data.config}
            onSaved={lease.reload}
          />
        )}
      </AsyncSection>
    </Page>
  )
}

function LeaseForm({
  config,
  onSaved,
}: {
  config: RentLeaseConfig | null
  onSaved: () => void
}) {
  const toast = useToast()
  const [rent, setRent] = useState(config ? paiseToInput(config.monthly_rent_paise) : '')
  const [rentPaise, setRentPaise] = useState(config?.monthly_rent_paise ?? 0)
  const [rentOk, setRentOk] = useState(Boolean(config))
  const [dueDay, setDueDay] = useState(String(config?.due_day_of_month ?? 5))
  const [name, setName] = useState(config?.landlord_name ?? '')
  const [phone, setPhone] = useState(config?.landlord_phone ?? '')
  const [email, setEmail] = useState(config?.landlord_email ?? '')
  const [pan, setPan] = useState(config?.landlord_pan ?? '')
  const [account, setAccount] = useState(config?.landlord_bank_account ?? '')
  const [ifsc, setIfsc] = useState(config?.landlord_bank_ifsc ?? '')
  const [upi, setUpi] = useState(config?.landlord_upi_id ?? '')
  const [receipts, setReceipts] = useState(config?.requires_landlord_receipt ?? false)
  const [touched, setTouched] = useState({ rent: false, name: false, renewal: false })
  const touch = (field: keyof typeof touched) =>
    setTouched((current) => (current[field] ? current : { ...current, [field]: true }))
  const [startDate, setStartDate] = useState(config?.lease_start_date ?? '')
  const [duration, setDuration] = useState(String(config?.duration_months ?? 11))
  const [noticeDays, setNoticeDays] = useState(String(config?.notice_period_days ?? 30))
  const [lockIn, setLockIn] = useState(String(config?.lock_in_months ?? 6))
  const [deposit, setDeposit] = useState(
    config?.security_deposit_paise != null ? paiseToInput(config.security_deposit_paise) : '',
  )
  const [depositPaise, setDepositPaise] = useState(config?.security_deposit_paise ?? 0)
  const [depositOk, setDepositOk] = useState(true)
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
  const durationOptions = [...new Set([...DURATIONS, durationMonths])]
    .sort((a, b) => a - b)
    .map((months) => ({ value: String(months), label: `${months} months` }))

  const panError =
    pan.trim() && !PAN_PATTERN.test(pan.trim())
      ? 'A PAN is ten characters: five letters, four digits, one letter.'
      : null
  const ifscError =
    ifsc.trim() && !IFSC_PATTERN.test(ifsc.trim())
      ? 'An IFSC is eleven characters: four letters, a zero, then six letters or digits.'
      : null
  const accountError =
    account.trim() && !/^[0-9]{6,18}$/.test(account.trim())
      ? 'An account number is 6 to 18 digits.'
      : null

  const save = useMutation(
    () =>
      api.post<RentLeaseConfig>('/api/rent/config', {
        landlord_name: name.trim(),
        landlord_phone: phone.trim() || null,
        landlord_email: email.trim() || null,
        landlord_pan: pan.trim() || null,
        landlord_bank_account: account.trim() || null,
        landlord_bank_ifsc: ifsc.trim() || null,
        landlord_upi_id: upi.trim() || null,
        monthly_rent_paise: rentPaise,
        due_day_of_month: Number(dueDay),
        requires_landlord_receipt: receipts,
        // The end date is left to the backend, which computes it from these two.
        lease_start_date: startDate || null,
        duration_months: durationMonths,
        notice_period_days: noticeValue,
        lock_in_months: lockInValue,
        security_deposit_paise: deposit.trim() ? depositPaise : null,
        escalation_type: escalation,
        escalation_percent: percentValue ?? config?.escalation_percent ?? 5,
        renewal_rent_paise: escalation === 'fixed' ? renewalPaise : null,
        // Not chosen here: the backend links or creates the Rent category under
        // Housing, and an existing lease keeps the one it has.
        expense_definition_id: null,
      }),
    {
      onSuccess: () => {
        toast.success(config ? 'Lease configuration saved' : 'Lease set up')
        onSaved()
      },
    },
  )
  const nameError = name.trim().length === 0 ? 'Landlord name is required.' : null
  const rentError = !rentOk || rentPaise <= 0 ? 'Monthly rent must be greater than zero.' : null
  const phoneError =
    phone.trim() && !PHONE_PATTERN.test(phone.trim())
      ? 'Enter a valid 10 to 15 digit phone number.'
      : null
  const emailError =
    email.trim() && !EMAIL_PATTERN.test(email.trim()) ? 'Enter a valid email address.' : null
  const upiError =
    upi.trim() && !UPI_PATTERN.test(upi.trim()) ? 'Enter a valid UPI ID (e.g. name@bank).' : null
  const depositError =
    deposit.trim() && !depositOk ? 'Enter a valid deposit amount, or leave empty.' : null
  const renewalError =
    escalation === 'fixed' && (!renewalOk || renewalPaise <= 0)
      ? 'Enter the agreed renewed rent.'
      : null

  const valid =
    !rentError &&
    !nameError &&
    !panError &&
    !ifscError &&
    !accountError &&
    !phoneError &&
    !emailError &&
    !upiError &&
    !depositError &&
    (escalation !== 'percent' || percentValue !== null) &&
    (escalation !== 'fixed' || !renewalError) &&
    noticeValue !== null &&
    lockInValue !== null

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
      className="space-y-6"
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        if (valid && !save.pending) void save.run(undefined)
      }}
    >
      {save.error && <ErrorNotice error={save.error} />}

      <Panel
        title="Rental Agreement Structure"
        icon={<Layers className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="Choose how your household contracts and divides rent. Currently, Model 1 (Equal Split) is live."
      >
        <div
          role="radiogroup"
          aria-label="Rental agreement structure"
          className="grid grid-cols-1 md:grid-cols-2 gap-3"
        >
          {RENTAL_MODELS.map((model) => {
            const active = model.status === 'active'
            return (
              <div
                key={model.id}
                role="radio"
                aria-checked={active}
                aria-disabled={!active || undefined}
                aria-label={model.title}
                tabIndex={active ? 0 : undefined}
                className={clsx(
                  'flex items-start gap-3 rounded-xl border p-3.5 min-w-0',
                  active
                    ? 'border-[var(--accent)] bg-[var(--accent-glow)]'
                    : 'border-[var(--border-subtle)] bg-[var(--bg-elevated)] opacity-70 cursor-not-allowed',
                )}
              >
                <span
                  className={clsx(
                    'mt-0.5 flex w-4 h-4 shrink-0 items-center justify-center rounded-full border-2',
                    active ? 'border-[var(--accent)]' : 'border-[var(--border-medium)]',
                  )}
                  aria-hidden="true"
                >
                  {active && <span className="w-2 h-2 rounded-full bg-[var(--accent)]" />}
                </span>
                <span className="min-w-0 flex-1 space-y-1.5">
                  <span className="block text-sm font-bold text-[var(--text-primary)]">
                    {model.title}
                  </span>
                  <span className="block text-xs text-[var(--text-secondary)]">
                    {model.description}
                  </span>
                  <Pill tone={active ? 'success' : 'neutral'}>
                    {active ? 'Active Model' : 'Under Development'}
                  </Pill>
                </span>
              </div>
            )
          })}
        </div>
      </Panel>

      <Panel
        title="Master Contract"
        icon={<FileText className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="One lease, one monthly rent, split equally between the residents taking part each month."
      >
        <div className={GRID_2}>
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
      </Panel>

      <Panel
        title="Contract Term & Exit Protections"
        icon={<CalendarClock className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="How long the agreement runs and what leaving early costs. The notice window opens this many days before the term ends."
      >
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
        <div className="max-w-sm">
          <MoneyInput
            label="Security Deposit Held by Landlord"
            prefix="₹"
            value={deposit}
            placeholder="e.g. 1,35,000"
            hint="The refundable deposit. Leave empty if none is held."
            onChange={(raw, parsed) => {
              setDeposit(raw)
              setDepositPaise(parsed.paise)
              setDepositOk(parsed.ok)
            }}
          />
        </div>
      </Panel>

      <Panel
        title="Renewal & Escalation Clause"
        icon={<TrendingUp className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="How the rent changes when the agreement is renewed for another term."
      >
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
              prefix="₹"
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
      </Panel>

      <Panel
        title="Landlord Profile & Tax"
        icon={<Building2 className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="Who the rent is paid to. The name and PAN are printed on each resident's HRA receipt."
      >
        <div className={GRID_2}>
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
          />
          <TextField
            label="PAN"
            value={pan}
            onChange={(value) => setPan(value.toUpperCase().replace(/\s/g, ''))}
            maxLength={10}
            placeholder="ABCPS1234K"
            error={panError}
            hint="Employers ask for the landlord's PAN when rent is above ₹1,00,000 a year."
          />
          <TextField
            label="Phone"
            type="tel"
            value={phone}
            onChange={setPhone}
            maxLength={40}
            error={phoneError}
          />
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            maxLength={254}
            error={emailError}
          />
        </div>
      </Panel>

      <Panel
        title="Payout Account Details"
        icon={<CreditCard className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="Where the rent is sent. All optional; shown with the landlord on the Rent page."
      >
        <div className={GRID_2}>
          <TextField
            label="Account number"
            value={account}
            onChange={setAccount}
            maxLength={18}
            error={accountError}
          />
          <TextField
            label="IFSC code"
            value={ifsc}
            onChange={(value) => setIfsc(value.toUpperCase().replace(/\s/g, ''))}
            maxLength={11}
            placeholder="HDFC0000128"
            error={ifscError}
          />
          <TextField
            label="UPI ID"
            value={upi}
            onChange={setUpi}
            placeholder="name@bank"
            error={upiError}
          />
        </div>
      </Panel>

      <Panel
        title="Receipt Preferences"
        icon={<ReceiptText className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint="The landlord's own receipt is separate from the HRA slips this app generates for residents, which are always available."
      >
        <div className="flex items-start justify-between gap-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
          <div className="min-w-0">
            <span id="landlord-receipts-label" className="block text-sm font-bold text-[var(--text-primary)]">
              Do you receive receipts from the landlord?
            </span>
            <p
              id="landlord-receipts-hint"
              className="m-0 mt-1 text-xs text-[var(--text-secondary)]"
              aria-live="polite"
            >
              {receipts
                ? "Receipts enabled — A paid month displays a 'Receipt Pending' badge until attached. Payouts are never blocked."
                : 'Reference only — Payouts require only date, funder, and UTR/reference. No landlord receipt is requested.'}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={receipts}
            aria-labelledby="landlord-receipts-label"
            aria-describedby="landlord-receipts-hint"
            onClick={() => setReceipts((value) => !value)}
            className={clsx(
              'relative w-10 h-6 rounded-full shrink-0 transition-colors cursor-pointer border border-[var(--border-medium)]',
              receipts ? 'bg-[var(--accent)]' : 'bg-[var(--bg-surface)]',
            )}
          >
            <span
              className={clsx(
                'absolute top-0.5 left-0.5 w-[18px] h-[18px] rounded-full transition-transform',
                receipts ? 'translate-x-4 bg-white' : 'bg-[var(--text-tertiary)]',
              )}
              aria-hidden="true"
            />
          </button>
        </div>
      </Panel>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-[var(--text-secondary)]">
          {config && `Last saved ${formatDateTime(config.updated_at)}`}
        </span>
        <button type="submit" className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
          {save.pending ? 'Saving…' : config ? 'Save Configuration' : 'Set Up Lease'}
        </button>
      </div>
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
