/**
 * The guided move-out clearance.
 *
 * Four steps bring together what otherwise has to be squared by hand when a
 * flatmate leaves: the move-out date, their deposit slot, what they owe or are
 * owed on the operating ledger, and the cash that changes hands. Nothing is
 * saved until the last step is confirmed, and then it is saved all together.
 *
 * The figures shown come from the backend's preview. The running total here is
 * a sum of those integer paise amounts; the backend works the settlement out
 * again and its figure is the one recorded and shown on the slip.
 */
import { useState } from 'react'
import { Check, Printer } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { AsyncSection, ErrorNotice, Modal } from '../components/Primitives'
import {
  CheckboxField,
  DateField,
  MoneyInput,
  SelectField,
  TextAreaField,
  TextField,
} from '../components/Form'
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  CopyButton,
  Panel,
  Pill,
  Rupees,
} from '../components/ui'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import { formatDate, formatInr, parseRupees, todayIso } from '../lib/format'
import type { ClearancePath, ExitClearance, ExitClearancePreview } from '../api/types'

const STEPS = ['Handover', 'Deposit', 'Ledger', 'Settlement'] as const
const EARLIEST_DATE = '2000-01-01'

const PATHS: Array<{ value: ClearancePath; title: string; body: string }> = [
  {
    value: 'signatory_refund',
    title: 'Signatory / Pool Refund',
    body: 'Their deposit is handed back, and the slot stands vacant until someone is assigned to it.',
  },
  {
    value: 'direct_replacement_transfer',
    title: 'Direct Replacement Transfer',
    body: 'The incoming resident pays them directly and takes the slot over straight away.',
  },
]

function dateError(value: string): string | null {
  if (!value) return 'Enter a date.'
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Enter a valid date.'
  if (value > todayIso()) return 'The date cannot be in the future.'
  if (value < EARLIEST_DATE) return 'That date is too far in the past.'
  return null
}

function clearancePath(residentId: string): string {
  return `/api/residents/${encodeURIComponent(residentId)}/clearance`
}

export function ExitClearanceModal({
  residentId,
  residentName,
  onClose,
  onCleared,
}: {
  residentId: string
  residentName: string
  onClose: () => void
  /** Called once the clearance is saved, while its slip is still on screen. */
  onCleared: (clearance: ExitClearance) => void
}) {
  const preview = useResource<ExitClearancePreview>(`${clearancePath(residentId)}/preview`)
  const [done, setDone] = useState<ExitClearance | null>(null)

  if (done) {
    return (
      <Modal
        title="Clearance Complete"
        description={`${done.resident_display_name} has moved out and the clearance is recorded.`}
        onClose={onClose}
        wide
        footer={
          <button type="button" className={BUTTON_PRIMARY} onClick={onClose}>
            Done
          </button>
        }
      >
        <ClearanceSlip clearance={done} />
      </Modal>
    )
  }

  if (!preview.data || preview.data.blocking_reason) {
    return (
      <Modal
        title="Move-Out Clearance"
        description={residentName}
        onClose={onClose}
        footer={
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose}>
            Close
          </button>
        }
      >
        <AsyncSection resource={preview} loadingLabel="Working out the clearance…">
          {(data) => (
            <p className="m-0 text-sm font-semibold text-[var(--warning)]" role="alert">
              {data.blocking_reason}
            </p>
          )}
        </AsyncSection>
      </Modal>
    )
  }

  return (
    <Wizard
      preview={preview.data}
      onClose={onClose}
      onDone={(clearance) => {
        setDone(clearance)
        onCleared(clearance)
      }}
    />
  )
}

function Wizard({
  preview,
  onClose,
  onDone,
}: {
  preview: ExitClearancePreview
  onClose: () => void
  onDone: (clearance: ExitClearance) => void
}) {
  const slot = preview.slot
  const funded = slot?.funded_amount_paise ?? 0
  const balance = preview.operating_net_balance_paise

  const [step, setStep] = useState(0)
  // Which steps the operator has tried to leave, so their errors can show.
  const [attempted, setAttempted] = useState<boolean[]>([false, false, false, false])
  const [moveOut, setMoveOut] = useState(preview.default_move_out_date)
  const [notes, setNotes] = useState('')
  const [deductionsRaw, setDeductionsRaw] = useState('')
  const [reason, setReason] = useState('')
  // No path is assumed when there is a deposit to settle; the operator says.
  const [path, setPath] = useState<ClearancePath | ''>(slot ? '' : 'standalone_clearance')
  const [incoming, setIncoming] = useState('')
  const [offset, setOffset] = useState(true)
  const [settledOn, setSettledOn] = useState(todayIso())
  const [reference, setReference] = useState('')

  const parsed = parseRupees(deductionsRaw, { allowEmpty: true })
  const deductions = deductionsRaw.trim() ? parsed.paise : 0
  const direct = path === 'direct_replacement_transfer'
  const depositRefund = funded - deductions
  const ledgerOffset = offset ? balance : 0
  const net = depositRefund + ledgerOffset

  const errors = {
    moveOut: dateError(moveOut),
    deductions:
      deductionsRaw.trim() && parsed.ok && deductions > funded
        ? `Deductions cannot be more than the ${formatInr(funded)} put in.`
        : null,
    reason: deductions > 0 && !reason.trim() ? 'Say what the deductions are for.' : null,
    path: slot && !path ? 'Choose how the deposit is being settled.' : null,
    incoming: direct && !incoming ? 'Choose the resident taking the slot over.' : null,
    handover:
      direct && parsed.ok && deductions <= funded && depositRefund <= 0
        ? 'Nothing would be handed over. Choose the refund path instead.'
        : null,
    settledOn: dateError(settledOn),
  }
  const stepValid = [
    !errors.moveOut,
    parsed.ok && !errors.deductions && !errors.reason && !errors.path && !errors.incoming &&
      !errors.handover,
    true,
    !errors.settledOn,
  ]
  const showErrors = attempted[step]

  const save = useMutation(
    () =>
      api.post<ExitClearance>(clearancePath(preview.resident_id), {
        move_out_date: moveOut,
        deductions_paise: deductions,
        deduction_reason: deductions > 0 ? reason.trim() : null,
        net_ledger_offset: offset,
        settlement_path: path,
        incoming_resident_id: direct ? incoming : null,
        settlement_date: settledOn,
        settlement_reference: reference.trim() || null,
        notes: notes.trim() || null,
      }),
    { onSuccess: onDone },
  )

  const advance = () => {
    setAttempted((current) => current.map((value, index) => value || index === step))
    if (!stepValid[step]) return
    if (step < STEPS.length - 1) setStep(step + 1)
    else if (!save.pending) void save.run(undefined)
  }

  const last = step === STEPS.length - 1
  return (
    <Modal
      title="Move-Out Clearance"
      description={`${preview.display_name} · step ${step + 1} of ${STEPS.length}: ${STEPS[step]}`}
      onClose={onClose}
      wide
      footer={
        <>
          <button
            type="button"
            className={BUTTON_SECONDARY}
            onClick={step === 0 ? onClose : () => setStep(step - 1)}
            disabled={save.pending}
          >
            {step === 0 ? 'Cancel' : 'Back'}
          </button>
          <button type="submit" form="clearance-form" className={BUTTON_PRIMARY} disabled={save.pending}>
            {last
              ? save.pending
                ? 'Settling…'
                : 'Confirm Move-Out & Settle Clearance'
              : 'Next'}
          </button>
        </>
      }
    >
      <form
        id="clearance-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          advance()
        }}
      >
        <ol className="m-0 p-0 list-none flex flex-wrap gap-2" aria-label="Clearance steps">
          {STEPS.map((label, index) => (
            <li key={label} aria-current={index === step ? 'step' : undefined}>
              <Pill tone={index === step ? 'accent' : index < step ? 'success' : 'neutral'}>
                {index < step ? (
                  <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
                ) : (
                  <span aria-hidden="true">{index + 1}</span>
                )}
                {label}
              </Pill>
            </li>
          ))}
        </ol>

        {save.error && <ErrorNotice error={save.error} />}

        {step === 0 && (
          <>
            <div className="max-w-sm">
              <DateField
                label="Move-Out Date"
                required
                value={moveOut}
                min={EARLIEST_DATE}
                max={todayIso()}
                onChange={setMoveOut}
                error={showErrors ? errors.moveOut : null}
                hint="The day they hand the room back. Their shares are not prorated for a part month."
              />
            </div>
            <TextAreaField
              label="Departure reason / notes"
              value={notes}
              onChange={setNotes}
              rows={2}
              placeholder="e.g. Moving to Pune for work"
              hint="Optional. Kept with the clearance and in the audit trail."
            />
          </>
        )}

        {step === 1 &&
          (slot ? (
            <>
              <p className="m-0 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-4 py-3 text-sm font-bold text-[var(--text-primary)]">
                {slot.slot_label} · Funded: <Rupees paise={funded} />
                <span className="block text-xs font-normal text-[var(--text-secondary)]">
                  Slot {slot.slot_number}, target {formatInr(slot.target_deposit_paise)}. The slot
                  itself stays with the household.
                </span>
              </p>
              <div className="max-w-sm">
                <MoneyInput
                  label="Physical Deductions"
                  value={deductionsRaw}
                  error={errors.deductions}
                  hint="Painting, damages or repairs kept back. Leave empty if none."
                  onChange={setDeductionsRaw}
                />
              </div>
              {deductions > 0 && (
                <TextAreaField
                  label="Deduction Reason"
                  required
                  value={reason}
                  onChange={setReason}
                  rows={2}
                  error={showErrors ? errors.reason : null}
                />
              )}
              <fieldset
                role="radiogroup"
                aria-required="true"
                aria-invalid={showErrors && errors.path ? true : undefined}
                aria-describedby={showErrors && errors.path ? 'clearance-path-error' : undefined}
                className="m-0 border-0 p-0 space-y-2"
              >
                <legend className="p-0 pb-1 text-sm font-bold text-[var(--text-primary)]">
                  How is the deposit being settled?
                </legend>
                {PATHS.map((option) => (
                  <label
                    key={option.value}
                    className={
                      path === option.value
                        ? 'flex items-start gap-3 rounded-xl border border-[var(--accent)] bg-[var(--accent-glow)] p-3 cursor-pointer'
                        : showErrors && errors.path
                          ? 'flex items-start gap-3 rounded-xl border border-[var(--danger)] bg-[var(--bg-elevated)] p-3 cursor-pointer'
                          : 'flex items-start gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 cursor-pointer'
                    }
                  >
                    <input
                      type="radio"
                      name="clearance-settlement-path"
                      className="mt-1 shrink-0"
                      value={option.value}
                      checked={path === option.value}
                      onChange={() => setPath(option.value)}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-bold text-[var(--text-primary)]">
                        {option.title}
                      </span>
                      <span className="block text-xs text-[var(--text-secondary)]">
                        {option.body}
                      </span>
                    </span>
                  </label>
                ))}
                {showErrors && errors.path && (
                  <span className="field-error" id="clearance-path-error">
                    {errors.path}
                  </span>
                )}
              </fieldset>
              {direct &&
                (preview.incoming_candidates.length === 0 ? (
                  <p className="m-0 text-xs font-semibold text-[var(--warning)]">
                    Nobody can take this slot over yet. Add the replacement as a resident first;
                    a resident who already occupies a slot cannot take a second one.
                  </p>
                ) : (
                  <SelectField
                    label="Replacement resident"
                    required
                    value={incoming}
                    placeholder="Choose a resident"
                    options={preview.incoming_candidates.map((row) => ({
                      value: row.resident_id,
                      label: row.display_name,
                    }))}
                    onChange={setIncoming}
                    error={showErrors ? errors.incoming : null}
                  />
                ))}
              {errors.handover && (
                <p className="m-0 text-xs font-semibold text-[var(--warning)]">{errors.handover}</p>
              )}
            </>
          ) : (
            <p className="m-0 rounded-xl border border-dashed border-[var(--border-medium)] px-4 py-3 text-xs text-[var(--text-secondary)]">
              {preview.display_name} holds no deposit slot, so there is no deposit to refund or
              deduct from. This is a standalone clearance of their operating balance.
            </p>
          ))}

        {step === 2 && (
          <>
            <dl
              className="m-0 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-1.5 text-xs"
              aria-label="Operating ledger"
            >
              <SumRow label="Unpaid charges">
                <Rupees paise={preview.ledger_outstanding_charges_paise} />
              </SumRow>
              <SumRow label="Unapplied credits">
                <Rupees paise={preview.ledger_unapplied_credits_paise} />
              </SumRow>
              <SumRow label="Operating balance" total>
                {balance === 0 ? (
                  'Settled'
                ) : (
                  <>
                    <Rupees paise={balance < 0 ? -balance : balance} tone="accent" />{' '}
                    <span className="font-normal text-[var(--text-secondary)]">
                      {balance < 0 ? 'owed by them' : 'owed to them'}
                    </span>
                  </>
                )}
              </SumRow>
            </dl>
            {balance === 0 ? (
              <p className="m-0 text-xs text-[var(--text-secondary)]">
                Nothing is owed either way on the operating ledger, so there is nothing to offset.
              </p>
            ) : (
              <CheckboxField
                label="Offset operating dues directly against deposit refund"
                checked={offset}
                onChange={setOffset}
                hint={
                  offset
                    ? 'One ledger payment is recorded that brings their operating balance to exactly zero, and the cash handed over is adjusted by the same amount.'
                    : 'The ledger is left as it is. Their operating balance stays open and is settled separately.'
                }
              />
            )}
            {balance !== 0 &&
              balance !== preview.ledger_unapplied_credits_paise - preview.ledger_outstanding_charges_paise && (
                <p className="m-0 text-[11px] text-[var(--text-secondary)]">
                  The operating balance also counts expenses they paid for the household and any
                  adjustments, so it differs from charges less credits.
                </p>
              )}
          </>
        )}

        {step === 3 && (
          <>
            <dl
              className="m-0 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-1.5 text-xs"
              aria-label="Settlement summary"
            >
              {slot && (
                <>
                  <SumRow label="Contributed Deposit">
                    <Signed paise={funded} />
                  </SumRow>
                  <SumRow label="Room Deductions">
                    <Signed paise={-deductions} />
                  </SumRow>
                </>
              )}
              {offset && balance !== 0 && (
                <SumRow label="Operating Ledger Offset">
                  <Signed paise={ledgerOffset} />
                </SumRow>
              )}
              <SumRow
                label={
                  net > 0
                    ? `Net Payout to ${preview.display_name}`
                    : net < 0
                      ? `Net Payable by ${preview.display_name}`
                      : 'Net Settlement'
                }
                total
              >
                <Rupees paise={net < 0 ? -net : net} tone="accent" />
              </SumRow>
            </dl>
            {!offset && balance !== 0 && (
              <p className="m-0 text-xs text-[var(--text-secondary)]">
                Their operating balance of {formatInr(balance < 0 ? -balance : balance)} is not
                included and stays open on the ledger.
              </p>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
              <DateField
                label="Settlement date"
                required
                value={settledOn}
                min={EARLIEST_DATE}
                max={todayIso()}
                onChange={setSettledOn}
                error={showErrors ? errors.settledOn : null}
              />
              <TextField
                label="Settlement UTR / reference"
                value={reference}
                onChange={setReference}
                maxLength={200}
                placeholder="UPI or bank reference"
              />
            </div>
            <p className="m-0 text-xs font-semibold text-[var(--warning)]">
              Confirming marks {preview.display_name} as moved out on {formatDate(moveOut)} and
              cannot be undone.
            </p>
          </>
        )}
      </form>
    </Modal>
  )
}

function SumRow({
  label,
  total,
  children,
}: {
  label: string
  total?: boolean
  children: React.ReactNode
}) {
  return (
    <div
      className={
        total
          ? 'flex justify-between gap-3 border-t border-[var(--border-subtle)] pt-1.5 text-sm'
          : 'flex justify-between gap-3'
      }
    >
      <dt className={total ? 'font-bold text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'}>
        {label}
      </dt>
      <dd className="m-0 font-semibold text-[var(--text-primary)]">{children}</dd>
    </div>
  )
}

/** A rupee amount with its sign always shown, as a line of a sum. */
function Signed({ paise }: { paise: number }) {
  return <span className="tabular">{formatInr(paise, { sign: 'always' })}</span>
}

/* ------------------------------------------------------------------- slip -- */

/** A recorded clearance: the figures, a scan-to-pay code, and ways to share it. */
export function ClearanceSlip({ clearance }: { clearance: ExitClearance }) {
  const net = clearance.net_settlement_paise
  const name = clearance.resident_display_name
  return (
    <div className="space-y-4">
      <article className="receipt-sheet rounded-xl border border-[var(--border-medium)] bg-[var(--bg-surface)] p-5 space-y-4 text-[var(--text-primary)]">
        <header className="space-y-0.5">
          <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
            {clearance.apartment_name} — Move-Out Clearance Statement
          </div>
          <div className="text-base font-bold">
            {name}
            {clearance.slot_label && (
              <span className="font-normal text-[var(--text-secondary)]">
                {' '}
                · {clearance.slot_label}
              </span>
            )}
          </div>
          <div className="tabular text-xs text-[var(--text-secondary)]">
            Moved out {formatDate(clearance.move_out_date)} · settled{' '}
            {formatDate(clearance.settlement_date)}
          </div>
        </header>

        <dl className="m-0 space-y-1.5 text-xs" aria-label="Clearance figures">
          {clearance.slot_id && (
            <>
              <SumRow label="Contributed Deposit">
                <Signed paise={clearance.deposit_funded_paise} />
              </SumRow>
              <SumRow
                label={
                  clearance.deduction_reason
                    ? `Deductions (${clearance.deduction_reason})`
                    : 'Deductions'
                }
              >
                <Signed paise={-clearance.deposit_deductions_paise} />
              </SumRow>
            </>
          )}
          {clearance.net_ledger_offset_applied ? (
            <SumRow label="Operating Ledger Offset">
              <Signed paise={clearance.ledger_offset_paise} />
            </SumRow>
          ) : (
            (clearance.ledger_outstanding_paise > 0 || clearance.ledger_credit_paise > 0) && (
              <SumRow label="Operating ledger (not included)">
                <Signed paise={clearance.ledger_credit_paise - clearance.ledger_outstanding_paise} />
              </SumRow>
            )
          )}
          <SumRow
            label={
              net > 0 ? `Payable to ${name}` : net < 0 ? `Payable by ${name}` : 'Nothing to pay'
            }
            total
          >
            <Rupees paise={net < 0 ? -net : net} tone="accent" />
          </SumRow>
        </dl>

        <div className="text-xs text-[var(--text-secondary)]">
          Status: Settled
          {clearance.settlement_reference && (
            <>
              {' '}
              · <span className="tabular">UTR {clearance.settlement_reference}</span>
            </>
          )}
          {clearance.counterparty_display_name && (
            <> · settled with {clearance.counterparty_display_name}</>
          )}
        </div>
        {clearance.notes && (
          <p className="m-0 text-xs text-[var(--text-secondary)]">{clearance.notes}</p>
        )}
      </article>

      {net !== 0 &&
        (clearance.upi_uri ? (
          <section
            aria-label="Scan to pay"
            className="flex flex-wrap items-center gap-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4"
          >
            {/* A QR code is read dark-on-light, so it keeps a white tile in both themes. */}
            <div className="rounded-xl bg-white p-3 shrink-0 border border-[var(--border-subtle)]">
              <QRCodeSVG
                value={clearance.upi_uri}
                size={148}
                level="M"
                role="img"
                aria-label={`UPI QR code to pay ${clearance.payee_display_name} ${formatInr(net < 0 ? -net : net)}`}
              />
            </div>
            <div className="min-w-0 space-y-1 text-xs text-[var(--text-secondary)]">
              <div className="text-sm font-bold text-[var(--text-primary)]">
                Pay {clearance.payee_display_name}{' '}
                <Rupees paise={net < 0 ? -net : net} tone="accent" />
              </div>
              <div className="tabular truncate">{clearance.payee_upi_id}</div>
              <p className="m-0">
                Scan with any UPI app. The amount is set to the net settlement.
              </p>
            </div>
          </section>
        ) : (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            No scan-to-pay code:{' '}
            {clearance.payee_display_name
              ? `${clearance.payee_display_name} has no UPI ID on file.`
              : 'there is no resident on the receiving side to pay.'}
          </p>
        ))}

      <div className="flex flex-wrap items-center gap-2 print:hidden">
        <CopyButton
          text={clearance.summary_text}
          label="Copy WhatsApp Summary"
          className={BUTTON_SECONDARY}
        />
        <button type="button" className={BUTTON_SECONDARY} onClick={() => window.print()}>
          <Printer className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          Print / Save as PDF
        </button>
      </div>
    </div>
  )
}

/** The latest clearance slip for a resident who has been cleared; nothing otherwise. */
export function ClearanceSlipPanel({ residentId }: { residentId: string }) {
  const resource = useResource<{ clearance: ExitClearance | null }>(clearancePath(residentId))
  const clearance = resource.data?.clearance
  if (!clearance) return null
  return (
    <Panel
      title="Clearance Slip"
      hint="The record of their move-out settlement, as it was made."
      actions={<Pill tone="success">Settled</Pill>}
    >
      <ClearanceSlip clearance={clearance} />
    </Panel>
  )
}
