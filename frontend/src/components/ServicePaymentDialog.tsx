/**
 * A resident paying their share of a service's month into the House Kitty:
 * the amount (pre-filled with what is still owed), the date, how it was paid
 * and its transaction reference -- which a bank or UPI payment carries and
 * cash does not. Rent, furniture rental and any other service billed through
 * the kitty use it; the caller saves.
 */
import { useState, type FormEvent, type ReactNode } from 'react'
import { DateField, MoneyInput, SelectField, TextField } from './Form'
import { ErrorNotice, Modal } from './Primitives'
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from './ui'
import { useMutation } from '../lib/useApi'
import { formatInr, formatMonth, paiseToInput, todayIso } from '../lib/format'

export type PaymentMode = 'upi' | 'bank_transfer' | 'cash' | 'other'

export const PAYMENT_MODES: Array<{ value: PaymentMode; label: string }> = [
  { value: 'upi', label: 'UPI' },
  { value: 'bank_transfer', label: 'Bank transfer' },
  { value: 'cash', label: 'Cash' },
  { value: 'other', label: 'Other' },
]

export interface ServicePaymentDialogProps {
  title: string
  /** Left out: who is paying, for what, and what they still owe. */
  description?: string
  month: string
  /** What the share is of, e.g. "furniture rental", "broadband", "rent". */
  serviceLabel?: string
  residentName: string
  outstandingPaise: number
  /** The payment's date to start from; today unless given. */
  initialDate?: string
  /** Where the money goes, above the form: given the amount entered, null while it is not valid. */
  destination?: (amountPaise: number | null) => ReactNode
  onSave: (payload: {
    amount_paise: number
    payment_date: string
    payment_mode: PaymentMode
    reference: string | null
    note: string | null
  }) => Promise<unknown>
  onClose: () => void
  onDone: (amountPaise: number) => void
}

export function ServicePaymentDialog({
  title,
  description,
  month,
  serviceLabel,
  residentName,
  outstandingPaise,
  initialDate,
  destination,
  onSave,
  onClose,
  onDone,
}: ServicePaymentDialogProps) {
  const owed = outstandingPaise
  const what = serviceLabel ? `${formatMonth(month)} ${serviceLabel}` : formatMonth(month)
  const [amount, setAmount] = useState(paiseToInput(owed))
  const [amountPaise, setAmountPaise] = useState(owed)
  const [amountOk, setAmountOk] = useState(true)
  const [date, setDate] = useState(initialDate ?? todayIso())
  const [mode, setMode] = useState<PaymentMode>('upi')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')

  const save = useMutation(
    () =>
      onSave({
        amount_paise: amountPaise,
        payment_date: date,
        payment_mode: mode,
        reference: reference.trim() || null,
        note: note.trim() || null,
      }),
    { onSuccess: () => onDone(amountPaise) },
  )
  // A bank or UPI payment carries its transaction ID; cash has none.
  const needsReference = mode !== 'cash'
  const valid = amountOk && amountPaise > 0 && Boolean(date) && (!needsReference || Boolean(reference.trim()))
  const formId = `service-payment-${month}`

  return (
    <Modal
      title={title}
      description={description ?? `${residentName} · ${what} share · ${formatInr(owed)} outstanding`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form={formId} className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
            {save.pending ? 'Saving…' : 'Record Payment'}
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
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        {destination?.(amountOk ? amountPaise : null)}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount"
            required
            value={amount}
            hint={
              amountOk && amountPaise > owed
                ? `${formatInr(amountPaise - owed)} more than this month's share. The extra goes to ${residentName}'s other charges, oldest first.`
                : amountOk && amountPaise > 0 && amountPaise < owed
                  ? `Part payment: ${formatInr(owed - amountPaise)} of this month's share will still be owed.`
                  : `Pre-filled with the outstanding share. It is applied to the ${what} bill first.`
            }
            onChange={(raw, parsed) => {
              setAmount(raw)
              setAmountPaise(parsed.paise)
              setAmountOk(parsed.ok)
            }}
          />
          <DateField label="Payment date" required value={date} onChange={setDate} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField<PaymentMode> label="Payment mode" value={mode} options={PAYMENT_MODES} onChange={setMode} />
          <TextField
            label="Reference / UTR"
            required={needsReference}
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="e.g. UPI ref or bank UTR"
          />
        </div>
        <TextField
          label="Note"
          value={note}
          onChange={setNote}
          maxLength={500}
          placeholder="Optional remark (e.g. GPay transfer)"
        />
      </form>
    </Modal>
  )
}
