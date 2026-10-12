/**
 * The lease's contract term on the Rent screen: where it stands, the prompt to
 * decide when the notice window opens, and the two ways out of it -- renew for
 * another term, or serve notice to vacate.
 *
 * None of this gates rent. A term that has run out keeps generating rent at
 * the current rate; the banner is a reminder, not a lock.
 */
import { useState } from 'react'
import { DoorOpen, RefreshCw } from 'lucide-react'
import { ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, MoneyInput, SelectField, TextAreaField } from '../../components/Form'
import { BUTTON_PRIMARY, BUTTON_SECONDARY, Pill, Rupees } from '../../components/ui'
import { api } from '../../api/client'
import { useMutation } from '../../lib/useApi'
import { leaseDurationOptions } from '../../lib/lease'
import {
  addDaysIso,
  formatDate,
  formatInr,
  leaseEndIso,
  paiseToInput,
  todayIso,
} from '../../lib/format'
import type { RentLeaseConfig } from '../../api/types'

/** The escalation the renew button promises, e.g. `+5% Escalation`. */
function escalationLabel(config: RentLeaseConfig): string {
  if (config.escalation_type === 'none') return 'No Escalation'
  if (config.escalation_type === 'fixed') {
    return formatInr(config.lifecycle?.projected_renewal_rent_paise ?? config.monthly_rent_paise)
  }
  return `+${config.escalation_percent}% Escalation`
}

export function LeaseTermPill({ config }: { config: RentLeaseConfig }) {
  const lifecycle = config.lifecycle
  if (!lifecycle) return null
  const urgent = lifecycle.is_notice_window_active || lifecycle.is_expired
  return (
    <Pill tone={urgent ? 'warning' : 'neutral'}>
      Term: Month {lifecycle.current_month_index} of {lifecycle.duration_months} (
      {lifecycle.is_expired ? 'Expired' : 'Expires'} {formatDate(config.lease_end_date)})
    </Pill>
  )
}

export function LeaseNoticeBanner({
  config,
  participantCount,
  onChanged,
}: {
  config: RentLeaseConfig
  /** Who shares the deposit refund; 0 when the month has no participants yet. */
  participantCount: number
  onChanged: (message: string, detail?: string) => void
}) {
  const [dialog, setDialog] = useState<'renew' | 'vacate' | null>(null)
  const lifecycle = config.lifecycle
  if (!lifecycle) return null

  const noticeServed = config.lease_status === 'notice_served'
  const deciding = lifecycle.is_notice_window_active || lifecycle.is_expired
  if (!noticeServed && !deciding) return null

  const ends = formatDate(config.lease_end_date)
  return (
    <>
      <section
        aria-label="Lease term"
        className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 sm:p-5 flex flex-wrap items-center justify-between gap-4"
      >
        <div className="min-w-0 flex-1 basis-72">
          <h2 className="m-0 text-sm font-bold text-[var(--text-primary)]">
            {noticeServed
              ? `Notice Served: Handover Planned for ${formatDate(config.target_handover_date)}`
              : lifecycle.is_expired
                ? `Lease Term Ended on ${ends}`
                : `Notice Window Open: Master Lease Ends on ${ends}`}
          </h2>
          <p className="m-0 mt-1 text-xs text-[var(--text-secondary)]">
            {noticeServed
              ? `Notice was served on ${formatDate(config.notice_served_date)}. Rent for the remaining months is unaffected. Renewing instead withdraws the notice.`
              : lifecycle.is_expired
                ? 'Rent keeps generating at the current rate. Renew the agreement or serve notice when the household has decided.'
                : `${lifecycle.days_until_expiry} day${lifecycle.days_until_expiry === 1 ? '' : 's'} left, and the landlord needs ${config.notice_period_days} days' notice. Decide whether the household is staying.`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!noticeServed && (
            <button type="button" className={BUTTON_SECONDARY} onClick={() => setDialog('vacate')}>
              <DoorOpen className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Plan to Vacate (Serve Notice)
            </button>
          )}
          <button type="button" className={BUTTON_PRIMARY} onClick={() => setDialog('renew')}>
            <RefreshCw className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Renew Tenure ({escalationLabel(config)})
          </button>
        </div>
      </section>

      {dialog === 'renew' && (
        <RenewLeaseModal
          config={config}
          onClose={() => setDialog(null)}
          onRenewed={(renewed) => {
            setDialog(null)
            onChanged(
              `Lease renewed for term ${renewed.term_number}`,
              `${formatInr(renewed.monthly_rent_paise)} a month until ${formatDate(renewed.lease_end_date)}.`,
            )
          }}
        />
      )}
      {dialog === 'vacate' && (
        <VacateLeaseModal
          config={config}
          participantCount={participantCount}
          onClose={() => setDialog(null)}
          onServed={(served) => {
            setDialog(null)
            onChanged(
              'Notice to vacate recorded',
              `Handover planned for ${formatDate(served.target_handover_date)}.`,
            )
          }}
        />
      )}
    </>
  )
}

function TermColumn({
  heading,
  period,
  rentPaise,
  highlight,
}: {
  heading: string
  period: string
  rentPaise: number | null
  highlight?: boolean
}) {
  return (
    <div
      className={
        highlight
          ? 'rounded-xl border border-[var(--accent)] bg-[var(--accent-glow)] p-3.5 min-w-0'
          : 'rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 min-w-0'
      }
    >
      <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {heading}
      </div>
      <div className="tabular mt-1 text-xs text-[var(--text-secondary)]">{period}</div>
      <Rupees
        paise={rentPaise}
        className="block mt-1 text-base font-bold text-[var(--text-primary)]"
      />
      <div className="text-[11px] text-[var(--text-secondary)]">a month</div>
    </div>
  )
}

function RenewLeaseModal({
  config,
  onClose,
  onRenewed,
}: {
  config: RentLeaseConfig
  onClose: () => void
  onRenewed: (config: RentLeaseConfig) => void
}) {
  const projected = config.lifecycle?.projected_renewal_rent_paise ?? config.monthly_rent_paise
  const earliest = config.lease_end_date ? addDaysIso(config.lease_end_date, 1) : todayIso()
  const [start, setStart] = useState(earliest)
  const [duration, setDuration] = useState(String(config.duration_months))
  const [rent, setRent] = useState(paiseToInput(projected))
  const [rentPaise, setRentPaise] = useState(projected)
  const [rentOk, setRentOk] = useState(true)

  const months = Number(duration)
  const end = start ? leaseEndIso(start, months) : null
  const startError =
    start && start < earliest
      ? `The new term must start after the current one ends on ${formatDate(config.lease_end_date)}.`
      : null
  const valid = Boolean(start) && !startError && rentOk && rentPaise > 0

  const renew = useMutation(
    () =>
      api.post<RentLeaseConfig>('/api/rent/lease/renew', {
        new_start_date: start,
        duration_months: months,
        new_monthly_rent_paise: rentPaise,
        escalation_type: config.escalation_type,
        escalation_percent: config.escalation_percent,
      }),
    { onSuccess: onRenewed },
  )

  const durationOptions = leaseDurationOptions(config.duration_months)

  return (
    <Modal
      title="Renew Lease"
      description={`Term ${config.term_number} is archived and term ${config.term_number + 1} begins. Months already generated keep the rent they were charged at.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={renew.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={!valid || renew.pending}
            onClick={() => void renew.run(undefined)}
          >
            {renew.pending ? 'Renewing…' : 'Confirm Renewal'}
          </button>
        </>
      }
    >
      <div className="stack">
        {renew.error && <ErrorNotice error={renew.error} />}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <TermColumn
            heading={`Expiring Term ${config.term_number}`}
            period={`${formatDate(config.lease_start_date)} – ${formatDate(config.lease_end_date)}`}
            rentPaise={config.monthly_rent_paise}
          />
          <TermColumn
            heading={`New Term ${config.term_number + 1}`}
            period={`${formatDate(start || null)} – ${formatDate(end)}`}
            rentPaise={rentOk && rentPaise > 0 ? rentPaise : null}
            highlight
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <DateField
            label="New term starts"
            required
            value={start}
            onChange={setStart}
            error={startError}
          />
          <SelectField
            label="Duration"
            value={duration}
            options={durationOptions}
            onChange={setDuration}
          />
        </div>
        <MoneyInput
          label="New monthly rent"
          required
          value={rent}
          hint={`The lease's escalation clause projects ${formatInr(projected)}. Change it if a different rent was agreed.`}
          onChange={(raw, parsed) => {
            setRent(raw)
            setRentPaise(parsed.paise)
            setRentOk(parsed.ok)
          }}
        />
      </div>
    </Modal>
  )
}

function VacateLeaseModal({
  config,
  participantCount,
  onClose,
  onServed,
}: {
  config: RentLeaseConfig
  participantCount: number
  onClose: () => void
  onServed: (config: RentLeaseConfig) => void
}) {
  const [served, setServed] = useState(todayIso())
  const [handover, setHandover] = useState(config.lease_end_date ?? '')
  const [deductions, setDeductions] = useState('')
  const [deductionsPaise, setDeductionsPaise] = useState(0)
  const [deductionsOk, setDeductionsOk] = useState(true)
  const [notes, setNotes] = useState('')

  const depositPaise = config.security_deposit_paise
  const handoverError =
    served && handover && handover < served ? 'The handover cannot be before the notice date.' : null
  const deductionsError =
    depositPaise != null && deductionsOk && deductionsPaise > depositPaise
      ? 'Deductions cannot be more than the deposit.'
      : null
  const valid = Boolean(served) && Boolean(handover) && !handoverError && deductionsOk && !deductionsError
  const netRefundPaise = depositPaise != null ? depositPaise - deductionsPaise : null
  const noticeGiven =
    served && handover
      ? Math.round((Date.parse(handover) - Date.parse(served)) / 86_400_000)
      : null

  const serve = useMutation(
    () =>
      api.post<RentLeaseConfig>('/api/rent/lease/vacate', {
        notice_served_date: served,
        target_handover_date: handover,
        // The deductions are an estimate, not a ledger entry, so they are kept
        // with the notes in the audit trail.
        notes:
          [
            notes.trim(),
            deductionsPaise > 0 ? `Expected deposit deductions: ${formatInr(deductionsPaise)}.` : '',
          ]
            .filter(Boolean)
            .join(' ') || null,
      }),
    { onSuccess: onServed },
  )

  return (
    <Modal
      title="Plan to Vacate"
      description="Records that notice has been given to the landlord. It changes nothing in the ledger, and rent for the remaining months is generated as usual."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={serve.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={!valid || serve.pending}
            onClick={() => void serve.run(undefined)}
          >
            {serve.pending ? 'Recording…' : 'Confirm Notice Served'}
          </button>
        </>
      }
    >
      <div className="stack">
        {serve.error && <ErrorNotice error={serve.error} />}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <DateField
            label="Notice served on"
            required
            value={served}
            onChange={setServed}
          />
          <DateField
            label="Target handover date"
            required
            value={handover}
            onChange={setHandover}
            error={handoverError}
            hint={
              noticeGiven !== null && noticeGiven >= 0
                ? `${noticeGiven} days' notice; the lease asks for ${config.notice_period_days}.`
                : undefined
            }
          />
        </div>
        {noticeGiven !== null && noticeGiven >= 0 && noticeGiven < config.notice_period_days && (
          <p className="m-0 text-xs font-semibold text-[var(--warning)]">
            This is shorter than the {config.notice_period_days}-day notice period on the lease.
          </p>
        )}
        {config.lifecycle?.is_within_lock_in && (
          <p className="m-0 text-xs font-semibold text-[var(--warning)]">
            The lease is inside its lock-in period, which runs until{' '}
            {formatDate(config.lifecycle.lock_in_end_date)}.
          </p>
        )}

        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-3">
          <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
            Security Deposit Recovery
          </div>
          {depositPaise == null ? (
            <p className="m-0 text-xs text-[var(--text-secondary)]">
              No security deposit is recorded on the lease.
            </p>
          ) : (
            <>
              <MoneyInput
                label="Expected deductions"
                value={deductions}
                error={deductionsError}
                hint="Painting, repairs, or unpaid dues the landlord will keep. Leave empty if none."
                onChange={(raw, parsed) => {
                  setDeductions(raw)
                  setDeductionsPaise(parsed.paise)
                  setDeductionsOk(parsed.ok)
                }}
              />
              <dl className="m-0 space-y-1.5 text-xs">
                <div className="flex justify-between gap-3">
                  <dt className="text-[var(--text-secondary)]">Deposit held</dt>
                  <dd className="m-0 font-semibold">
                    <Rupees paise={depositPaise} />
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-[var(--text-secondary)]">Less deductions</dt>
                  <dd className="m-0 font-semibold">
                    <Rupees paise={deductionsOk ? deductionsPaise : null} />
                  </dd>
                </div>
                <div className="flex justify-between gap-3 border-t border-[var(--border-subtle)] pt-1.5 text-sm">
                  <dt className="font-bold text-[var(--text-primary)]">Net Refund</dt>
                  <dd className="m-0 font-bold">
                    <Rupees
                      paise={deductionsOk && !deductionsError ? netRefundPaise : null}
                      tone="accent"
                    />
                  </dd>
                </div>
              </dl>
              {participantCount > 0 && netRefundPaise != null && deductionsOk && !deductionsError && (
                <p className="tabular m-0 text-xs text-[var(--text-secondary)]">
                  ~{formatInr(Math.floor(netRefundPaise / participantCount))} each between{' '}
                  {participantCount} residents. An estimate; no refund is recorded here.
                </p>
              )}
            </>
          )}
        </div>

        <TextAreaField
          label="Notes"
          value={notes}
          onChange={setNotes}
          rows={2}
          placeholder="e.g. 30-day notice provided by email"
        />
      </div>
    </Modal>
  )
}
