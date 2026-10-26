/**
 * The House Kitty paying back a resident who paid the landlord or a provider
 * more than their own share of a month's bill -- rent, furniture, any service.
 *
 * Their own share went through the kitty like everyone else's; the extra is
 * owed back to them, out of what the other flatmates pay in. The panel says
 * what they paid, what is owed, what has been paid back and what the kitty
 * holds now; the dialog records one payment back. Each screen says where the
 * payment goes, since rent and services have their own endpoints.
 */
import { type FormEvent, useState } from 'react'
import { Check, HandCoins, Trash2 } from 'lucide-react'
import { DateField, TextField } from './Form'
import { ErrorNotice, Modal } from './Primitives'
import { BUTTON_PRIMARY, BUTTON_QUIET, BUTTON_SECONDARY, MiniStat, Panel, Pill, Rupees } from './ui'
import type { KittyReimbursement } from '../api/types'
import { formatDate, formatInr, formatMonth, todayIso } from '../lib/format'
import { useMutation } from '../lib/useApi'

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]'

/** One payment back from the kitty, as recorded. */
export type KittyRefund = KittyReimbursement['reimbursements'][number]

export function KittyReimbursementPanel({
  reimbursement,
  billLabel,
  paidTo,
  onReimburse,
  onDelete,
}: {
  reimbursement: KittyReimbursement
  /** What was paid for, as a sentence ends: "the November 2026 bill", "November 2026 rent". */
  billLabel: string
  /** Who they paid: "Furlenco", "the landlord". */
  paidTo: string
  onReimburse: (reimbursement: KittyReimbursement) => void
  /** Deletes a payment back recorded by mistake, while the month is open; without it none is offered. */
  onDelete?: (refund: KittyRefund) => void
}) {
  const owed = reimbursement
  const name = owed.recipient_name
  const settled = owed.pending_reimbursement_paise === 0
  // What of the total they paid was their own share, paid through the kitty.
  const viaKitty = owed.total_fronted_paise - owed.net_reimbursement_owed_paise
  return (
    <Panel
      title={`Reimbursement · ${name}`}
      icon={<HandCoins className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={`${name} paid extra beyond their allotted share for ${billLabel}. The House Kitty reimburses the extra amount out of what the other flatmates pay in.`}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
        <MiniStat
          label="Total paid"
          value={<Rupees paise={owed.total_fronted_paise} />}
          note={viaKitty > 0 ? `Includes ${formatInr(viaKitty)} allotted share` : `All of it beyond their share, to ${paidTo}`}
        />
        <MiniStat
          label="Extra (reimbursable)"
          value={<Rupees paise={owed.net_reimbursement_owed_paise} />}
          note="To be reimbursed via House Kitty"
        />
        <MiniStat
          label="Reimbursed so far"
          value={<Rupees paise={owed.reimbursed_so_far_paise} />}
          note={settled ? 'Nothing still owed' : `${formatInr(owed.pending_reimbursement_paise)} still owed`}
        />
        <MiniStat
          label="Kitty cash available"
          value={<Rupees paise={owed.kitty_cash_available_paise} />}
          note="Collected from other flatmates"
        />
      </div>

      {owed.reimbursements.length > 0 && (
        <div className="overflow-x-auto min-w-0">
          <table className="w-full text-left text-xs min-w-[480px] border-collapse">
            <caption className="sr-only">Reimbursements to {name}</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Paid on</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Amount</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Reference / UTR</th>
                {onDelete && <th scope="col" className="pb-3 font-bold text-center">Actions</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {owed.reimbursements.map((refund) => (
                <tr key={refund.payment_id} className="align-middle">
                  <td className="py-2.5 pr-3 text-center tabular">{formatDate(refund.payment_date)}</td>
                  <td className="py-2.5 pr-3 text-center font-bold text-[var(--text-primary)]">
                    <Rupees paise={refund.amount_paise} />
                  </td>
                  <td className="py-2.5 pr-3 text-center tabular break-all">
                    {refund.reference ?? <span className="text-[var(--text-tertiary)]">—</span>}
                  </td>
                  {onDelete && (
                    <td className="py-2.5 text-center">
                      <button
                        type="button"
                        className={BUTTON_QUIET}
                        onClick={() => onDelete(refund)}
                        aria-label={`Delete the ${formatInr(refund.amount_paise)} reimbursement of ${formatDate(refund.payment_date)}`}
                      >
                        <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        {settled ? (
          <span className="text-xs text-[var(--text-secondary)]">
            <Pill tone="success">
              <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
              Reimbursed
            </Pill>{' '}
            {name} has been paid back in full.
          </span>
        ) : (
          <>
            <span className="text-xs text-[var(--text-secondary)]">
              {owed.can_reimburse
                ? `The kitty can pay back ${formatInr(owed.reimbursable_paise)} now.`
                : `Nothing paid into the kitty for ${billLabel} is waiting to go back yet.`}
            </span>
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={!owed.can_reimburse}
              onClick={() => onReimburse(owed)}
            >
              {owed.can_reimburse
                ? `Reimburse ${formatInr(owed.reimbursable_paise)} from Kitty to ${name}`
                : `Reimburse from Kitty to ${name}`}
            </button>
          </>
        )}
      </div>
    </Panel>
  )
}

/** What one payment back records; each screen sends it to its own endpoint. */
export interface KittyRefundDetails {
  amount_paise: number
  payment_date: string
  reference: string | null
  note: string | null
}

export function ReimburseDialog({
  month,
  billLabel,
  reimbursement,
  submit,
  onClose,
  onDone,
}: {
  month: string
  /** As on the panel: "the November 2026 bill", "November 2026 rent". */
  billLabel: string
  reimbursement: KittyReimbursement
  submit: (details: KittyRefundDetails) => Promise<unknown>
  onClose: () => void
  onDone: (amount: number) => void
}) {
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const name = reimbursement.recipient_name
  // What the kitty holds for the bill now; the rest waits for the others to pay in.
  const amount = reimbursement.reimbursable_paise
  const later = reimbursement.pending_reimbursement_paise - amount
  const save = useMutation(
    () =>
      submit({
        amount_paise: amount,
        payment_date: date,
        reference: reference.trim() || null,
        note: note.trim() || null,
      }),
    { onSuccess: () => onDone(amount) },
  )
  const formId = `kitty-reimburse-${month}`
  return (
    <Modal
      title={`Reimburse ${name}`}
      description={`${formatInr(amount)} from the kitty to ${name}, out of what flatmates paid in for ${billLabel}.${later > 0 ? ` ${formatInr(later)} more is owed once the rest have paid.` : ''}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form={formId} className={BUTTON_PRIMARY} disabled={!date || save.pending}>
            {save.pending ? 'Saving…' : 'Record reimbursement'}
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
          if (date && !save.pending) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <DateField label="Paid on" required value={date} onChange={setDate} />
          <TextField
            label="Reference / UTR"
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="e.g. UPI ref or bank UTR"
          />
        </div>
        <TextField label="Note" value={note} onChange={setNote} maxLength={500} />
      </form>
    </Modal>
  )
}

/** "the November 2026 bill" -- how a service's month reads in a sentence. */
export function billFor(month: string): string {
  return `the ${formatMonth(month)} bill`
}
