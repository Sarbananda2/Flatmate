/**
 * The House Kitty paying back a resident who paid the landlord or a provider
 * more than their own share of a month's bill -- rent, furniture, any service.
 *
 * Their own share went through the kitty like everyone else's; the extra is
 * owed back to them, out of what the other flatmates pay in. The panel is a
 * table laid out column for column as the Resident Collection and Provider
 * Payout tables: who is owed, the extra they paid, what the kitty has paid
 * back, what is still owed, the status, and Pay Now. A row opens the
 * resident's reimbursement statement (Rule U7); the dialog records one payment
 * back. Each screen says where the payment goes, since rent and services have
 * their own endpoints.
 */
import { type FormEvent, useState } from 'react'
import { Check, Clock, CreditCard, HandCoins, Trash2 } from 'lucide-react'
import { DateField, TextField } from './Form'
import { ErrorNotice, Modal } from './Primitives'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  EmptyBlock,
  MiniStat,
  Panel,
  Pill,
  Rupees,
} from './ui'
import type { KittyReimbursement } from '../api/types'
import { formatDate, formatInr, formatMonth, todayIso } from '../lib/format'
import { useMutation } from '../lib/useApi'

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]'

/** One payment back from the kitty, as recorded. */
export type KittyRefund = KittyReimbursement['reimbursements'][number]

/** Paid back in full, in part, or not yet: worded as the collection table words a share. */
function ReimbursementStatus({ owed }: { owed: KittyReimbursement }) {
  if (owed.pending_reimbursement_paise === 0) {
    return (
      <Pill tone="success">
        <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
        Paid
      </Pill>
    )
  }
  return (
    <Pill tone="warning">
      <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
      {owed.reimbursed_so_far_paise > 0 ? 'Part paid' : 'Unpaid'}
    </Pill>
  )
}

export function KittyReimbursementPanel({
  reimbursements,
  billLabel,
  onReimburse,
  onViewStatement,
}: {
  /** Everyone the kitty owes for the month: one row each. */
  reimbursements: KittyReimbursement[]
  /** What was paid for, as a sentence ends: "the November 2026 bill", "November 2026 rent". */
  billLabel: string
  onReimburse: (reimbursement: KittyReimbursement) => void
  onViewStatement: (reimbursement: KittyReimbursement) => void
}) {
  const single = reimbursements.length === 1 ? reimbursements[0].recipient_name : null
  return (
    <Panel
      title={single ? `Reimbursement · ${single}` : 'Reimbursement'}
      icon={<HandCoins className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={
        single
          ? `${single} paid extra beyond their allotted share for ${billLabel}. The House Kitty reimburses the extra amount out of what the other flatmates pay in. Select the row for reimbursement history.`
          : `What the House Kitty owes back to residents who paid extra beyond their allotted share for ${billLabel}. Select a row for reimbursement history.`
      }
    >
      {/* Laid out as the collection and payout tables: what is owed, what has
          been paid back, what is still owed. */}
      <div className="overflow-x-auto min-w-0">
        <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
          <caption className="sr-only">Reimbursements from the House Kitty</caption>
          <thead>
            <tr className={TABLE_HEAD}>
              <th scope="col" className="w-[28%] pb-3 pr-3 font-bold text-left">Resident</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Extra Paid</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Paid from Kitty</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Still Owed</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Status</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {reimbursements.map((owed) => {
              const name = owed.recipient_name
              return (
                // The row opens the resident's reimbursement statement; Pay Now does its own thing.
                <tr
                  key={owed.recipient_resident_id}
                  className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                  tabIndex={0}
                  title={`View ${name}'s reimbursement statement`}
                  onClick={() => onViewStatement(owed)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onViewStatement(owed)
                    }
                  }}
                >
                  <th scope="row" className="py-3 pr-3 text-left font-normal">
                    <span className="flex items-center gap-2.5 min-w-0">
                      <Avatar name={name} />
                      <span className="font-semibold text-[var(--text-primary)] truncate">{name}</span>
                    </span>
                  </th>
                  <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                    <Rupees paise={owed.net_reimbursement_owed_paise} />
                  </td>
                  <td className="py-3 pr-3 text-center">
                    <Rupees paise={owed.reimbursed_so_far_paise} />
                  </td>
                  <td className="py-3 pr-3 text-center">
                    <Rupees paise={owed.pending_reimbursement_paise} />
                  </td>
                  <td className="py-3 pr-3 text-center whitespace-nowrap">
                    <ReimbursementStatus owed={owed} />
                  </td>
                  <td className="py-3 text-center whitespace-nowrap">
                    {/* Always there, as on each resident's row; off until the kitty
                        holds something to pay back, and once it is all paid back. */}
                    <button
                      type="button"
                      className={BUTTON_SECONDARY}
                      disabled={!owed.can_reimburse}
                      onClick={(event) => {
                        event.stopPropagation()
                        onReimburse(owed)
                      }}
                      aria-label={`Reimburse from Kitty to ${name}`}
                    >
                      <CreditCard className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                      Pay Now
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

/** One resident's reimbursement for the month: what they paid, what is owed back, and each payment back (Rule U7). */
export function KittyReimbursementStatementModal({
  month,
  reimbursement,
  onDelete,
  onClose,
}: {
  month: string
  reimbursement: KittyReimbursement
  /** Deletes a payment back recorded by mistake, while the month is open; without it none is offered. */
  onDelete?: (refund: KittyRefund) => void
  onClose: () => void
}) {
  const owed = reimbursement
  const name = owed.recipient_name
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  return (
    <Modal title="Reimbursement Statement" description={`${name} · ${formatMonth(month)}`} onClose={onClose}>
      <div className="stack">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MiniStat label="Total Paid" value={<Rupees paise={owed.total_fronted_paise} />} />
          <MiniStat label="Extra Paid" value={<Rupees paise={owed.net_reimbursement_owed_paise} />} />
          <MiniStat label="Still Owed" value={<Rupees paise={owed.pending_reimbursement_paise} />} />
          <MiniStat label="Kitty Cash Available" value={<Rupees paise={owed.kitty_cash_available_paise} />} />
        </div>

        <section aria-label="Reimbursement history" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Reimbursement History</h3>
            <ReimbursementStatus owed={owed} />
          </div>
          {owed.reimbursements.length === 0 ? (
            <EmptyBlock title="No reimbursements recorded yet for this month." />
          ) : (
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
                      <td className="py-2.5 pr-3 text-center tabular break-all">{refund.reference ?? dash}</td>
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
        </section>
      </div>
    </Modal>
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
