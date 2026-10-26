/**
 * Deleting a transaction recorded by mistake -- a resident's payment into the
 * House Kitty, the kitty's reimbursement to a resident, or a payout to the
 * landlord or a provider -- while the month's statement is still open.
 *
 * The backend deletes whatever was recorded together with it (a resident's
 * payout takes their share paid through the kitty and their advance), refuses
 * while a reimbursement depends on it, and refuses once the month's statement
 * is finalized. This only asks first, and shows any refusal.
 */
import { ConfirmDialog } from './Primitives'
import { api } from '../api/client'
import { useMutation } from '../lib/useApi'

export interface PendingDeletion {
  /** The dialog's title: "Delete this payment?". */
  title: string
  /** What it is, as a sentence: "Bob's ₹15,000.00 payment into the kitty of 5 Nov 2026." */
  what: string
  /** The DELETE endpoint. */
  url: string
}

export function DeleteTransactionDialog({
  deletion,
  onClose,
  onDeleted,
}: {
  deletion: PendingDeletion
  onClose: () => void
  onDeleted: () => void
}) {
  const remove = useMutation(() => api.delete<unknown>(deletion.url), { onSuccess: onDeleted })
  return (
    <ConfirmDialog
      title={deletion.title}
      body={
        <>
          <p className="m-0">{deletion.what}</p>
          <p className="m-0 mt-2 text-xs text-[var(--text-secondary)]">
            It is deleted outright, with anything recorded together with it, and the month owes
            what it owed before. This is only possible while the month&apos;s statement is open.
          </p>
        </>
      }
      confirmLabel="Delete"
      tone="danger"
      pending={remove.pending}
      error={remove.error}
      onConfirm={() => void remove.run(undefined)}
      onCancel={onClose}
    />
  )
}
