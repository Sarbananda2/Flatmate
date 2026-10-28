/**
 * A payout towards a month's bill -- to the landlord, or a service's provider:
 * from the House Kitty, or by a resident from their own account, whose own
 * share then goes through the kitty and whatever they paid beyond it the kitty
 * pays back. Each payout carries its date and bank reference, and is never
 * more than is still owed. The caller saves.
 */
import { useState, type FormEvent, type ReactNode } from 'react'
import { DateField, MoneyInput, SelectField, TextField } from './Form'
import { ErrorNotice, Modal } from './Primitives'
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from './ui'
import { useMutation, useResource } from '../lib/useApi'
import { formatInr, formatMonth, paiseToInput, todayIso } from '../lib/format'
import type { Resident } from '../api/types'

export type DisbursementSource = 'pool' | 'resident_advance'

export interface ServiceDisbursementDialogProps {
  title: string
  month: string
  /** Who is paid: the landlord's or the provider's name. */
  payeeName: string
  /** What they are: "provider" unless given, or "landlord". */
  payeeRole?: string
  /** What is still owed to them; no payout may be more. */
  remainingPaise: number
  /** What they bill for the month, all of it. */
  totalBillPaise: number
  /** What to pre-fill with instead of all that is still owed: what the kitty has collected so far. */
  suggestedPaise?: number
  /** Who may have paid them; left out, the active residents are fetched. */
  residents?: Array<{ resident_id: string; display_name: string }>
  /** Where the money goes, above the form: given the amount entered, null while it is not valid. */
  destination?: (amountPaise: number | null) => ReactNode
  onSave: (payload: {
    disbursement_mode: DisbursementSource
    funding_resident_id: string | null
    reference: string
    disbursed_date: string
    amount_paise: number
    notes: string | null
  }) => Promise<unknown>
  onClose: () => void
  onDone: () => void
}

export function ServiceDisbursementDialog({
  title,
  month,
  payeeName,
  payeeRole = 'provider',
  remainingPaise,
  totalBillPaise,
  suggestedPaise,
  residents,
  destination,
  onSave,
  onClose,
  onDone,
}: ServiceDisbursementDialogProps) {
  const fetched = useResource<{ items: Resident[] }>(residents ? null : '/api/residents')
  const people = residents ?? (fetched.data?.items ?? []).filter((resident) => resident.is_active)
  const loading = !residents && !fetched.data
  const payee = payeeName
  const remaining = remainingPaise
  const suggested = suggestedPaise ?? remaining
  const fromCollection = suggested < remaining
  const [amount, setAmount] = useState(paiseToInput(suggested))
  const [amountPaise, setAmountPaise] = useState(suggested)
  const [amountOk, setAmountOk] = useState(true)
  const [source, setSource] = useState<DisbursementSource>('pool')
  const [funder, setFunder] = useState('')
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const sources: Array<{ value: DisbursementSource; title: string; body: string }> = [
    {
      value: 'pool',
      title: 'House Kitty',
      body: `Paid to ${payee} from the kitty's account, out of what residents have paid in towards the ${formatInr(totalBillPaise)} bill.`,
    },
    {
      value: 'resident_advance',
      title: 'Paid by a resident (reimburse extra via Kitty)',
      body: `A resident paid the ${payeeRole} from their own account. Their allotted share is recorded through the House Kitty, and any extra amount paid beyond their share is reimbursed to them via the House Kitty.`,
    },
  ]

  const record = useMutation(
    () =>
      onSave({
        disbursement_mode: source,
        funding_resident_id: source === 'resident_advance' ? funder : null,
        reference: reference.trim(),
        disbursed_date: date,
        amount_paise: amountPaise,
        notes: notes.trim() || null,
      }),
    { onSuccess: () => onDone() },
  )
  const tooMuch = amountOk && amountPaise > remaining
  const amountValid = amountOk && amountPaise > 0 && amountPaise <= remaining
  const valid = amountValid && Boolean(date) && reference.trim().length > 0 && (source === 'pool' || Boolean(funder))
  const formId = `service-disbursement-${month}`

  return (
    <Modal
      title={title}
      description={`${formatMonth(month)} · ${formatInr(remaining)} still owed to ${payee}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={record.pending}>
            Cancel
          </button>
          <button type="submit" form={formId} className={BUTTON_PRIMARY} disabled={!valid || record.pending}>
            {record.pending ? 'Saving…' : 'Record Payout'}
          </button>
        </>
      }
    >
      <form
        id={formId}
        className="stack"
        noValidate
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          if (valid && !record.pending) void record.run(undefined)
        }}
      >
        <ErrorNotice error={record.error} />
        {destination?.(amountValid ? amountPaise : null)}
        <fieldset role="radiogroup" className="m-0 border-0 p-0 space-y-2">
          <legend className="p-0 pb-1 text-sm font-bold text-[var(--text-primary)]">Paid from</legend>
          {sources.map((option) => (
            <label
              key={option.value}
              className={
                source === option.value
                  ? 'flex items-start gap-3 rounded-xl border border-[var(--accent)] bg-[var(--accent-glow)] p-3 cursor-pointer'
                  : 'flex items-start gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 cursor-pointer'
              }
            >
              <input
                type="radio"
                name={`${formId}-source`}
                className="mt-1 shrink-0"
                value={option.value}
                checked={source === option.value}
                onChange={() => setSource(option.value)}
              />
              <span className="min-w-0">
                <span className="block text-sm font-bold text-[var(--text-primary)]">{option.title}</span>
                <span className="block text-xs text-[var(--text-secondary)]">{option.body}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {source === 'resident_advance' && (
          <SelectField
            label="Paid by"
            required
            value={funder}
            placeholder={loading ? 'Loading…' : 'Choose a resident'}
            options={people.map((resident) => ({ value: resident.resident_id, label: resident.display_name }))}
            onChange={setFunder}
          />
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount"
            required
            value={amount}
            error={tooMuch ? `More than the ${formatInr(remaining)} still owed to the ${payeeRole}.` : null}
            hint={
              fromCollection
                ? `Pre-filled with collected funds. You can pay any amount up to ${formatInr(remaining)}.`
                : `Pre-filled with the balance still owed. You can pay any amount up to ${formatInr(remaining)}.`
            }
            onChange={(raw, parsed) => {
              setAmount(raw)
              setAmountPaise(parsed.paise)
              setAmountOk(parsed.ok)
            }}
          />
          <DateField label="Payment date" required value={date} onChange={setDate} />
        </div>
        <TextField
          label="Reference / UTR"
          required
          value={reference}
          onChange={setReference}
          maxLength={200}
          placeholder="e.g. UPI ref or bank UTR"
        />
        <TextField label="Note" value={notes} onChange={setNotes} maxLength={500} />
      </form>
    </Modal>
  )
}
