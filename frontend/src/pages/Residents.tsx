/**
 * Residents.
 *
 * A resident is never deleted. Moving somebody out sets their status and
 * records the date; every charge, payment, credit, and adjustment stays exactly
 * where it was, and they keep appearing in reconciliation while they have an
 * open balance.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Page } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Modal,
  Money,
} from '../components/Primitives'
import { CheckboxField, DateField, TextAreaField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import { formatDate, todayIso } from '../lib/format'
import type { OpeningBalanceItem, Reconciliation, Resident } from '../api/types'

export default function ResidentsPage() {
  const toast = useToast()
  const residents = useResource<{ items: Resident[]; total: number }>('/api/residents')
  const settlement = useResource<Reconciliation>('/api/reconciliation')
  const openingBalances = useResource<{ items: OpeningBalanceItem[] }>('/api/opening-balances')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Resident | null>(null)
  const [deactivating, setDeactivating] = useState<Resident | null>(null)

  const balances = new Map(
    (settlement.data?.accounts ?? [])
      .filter((account) => account.account_type === 'resident')
      .map((account) => [account.account_id, account]),
  )

  const openingMap = new Map(
    (openingBalances.data?.items ?? []).map((item) => [item.resident_id, item]),
  )

  const refresh = () => {
    residents.reload()
    settlement.reload()
    openingBalances.reload()
  }

  return (
    <Page
      title="Residents"
      subtitle="The people sharing this apartment. Deactivating somebody records that they moved out; it never removes their financial history."
      breadcrumbs={[{ label: 'Configure' }, { label: 'Residents' }]}
      actions={
        <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
          Add resident
        </button>
      }
    >
      <Callout tone="info" title="Nothing prorates — this household's rule">
        A partial month does not reduce anybody's share. Somebody who moves in on the 16th and
        takes part in an expense is charged a <strong>full ordinary share</strong> of it, and the
        same goes for somebody moving out. Move-in and move-out dates are recorded here for
        reference and never change an allocation.
        <br />
        To leave somebody out of a particular expense entirely, uncheck them in that expense's
        participants — they then get an explicit zero share. That is a participation choice you
        make and can see, not a calculation happening behind your back.
      </Callout>

      <Card title="All residents" flush>
        <AsyncSection
          resource={residents}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No residents yet"
              body="Add everyone sharing the apartment. Expenses are allocated among them."
              action={
                <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
                  Add the first resident
                </button>
              }
            />
          }
        >
          {(data) => (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Residents and their balances</caption>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Status</th>
                    <th scope="col">Active from</th>
                    <th scope="col">Active until</th>
                    <th scope="col" className="num">
                      Opening
                    </th>
                    <th scope="col" className="num">
                      Outstanding
                    </th>
                    <th scope="col" className="num">
                      Credit
                    </th>
                    <th scope="col" className="num">
                      Net balance
                    </th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((resident) => {
                    const account = balances.get(resident.resident_id)
                    const active = Boolean(resident.is_active)
                    return (
                      <tr key={resident.resident_id}>
                        <th scope="row">
                          <Link to={`/residents/${resident.resident_id}`}>
                            {resident.display_name}
                          </Link>
                          <div>
                            <span className="id-chip">{resident.resident_id}</span>
                          </div>
                        </th>
                        <td>
                          {active ? (
                            <Badge tone="ok">Active</Badge>
                          ) : (
                            <Badge tone="neutral">Moved out</Badge>
                          )}
                        </td>
                        <td className="nowrap">{formatDate(resident.active_from)}</td>
                        <td className="nowrap">{formatDate(resident.active_until)}</td>
                        <td className="num">
                          {(() => {
                            const ob = openingMap.get(resident.resident_id)
                            if (!ob) return <span className="subtle">—</span>
                            return (
                              <Money
                                paise={ob.amount_paise}
                                tone={ob.amount_paise < 0 ? 'owed-by' : 'credit'}
                                sign="always"
                              />
                            )
                          })()}
                        </td>
                        <td className="num">
                          <Money
                            paise={account?.outstanding_charges_paise ?? 0}
                            tone={account?.outstanding_charges_paise ? 'owed-by' : 'plain'}
                          />
                        </td>
                        <td className="num">
                          <Money
                            paise={account?.unapplied_credit_paise ?? 0}
                            tone={account?.unapplied_credit_paise ? 'credit' : 'plain'}
                          />
                        </td>
                        <td className="num">
                          <Money
                            paise={account?.net_position_paise ?? 0}
                            tone="auto"
                            sign="always"
                          />
                        </td>
                        <td>
                          <div className="row" style={{ gap: 6 }}>
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => setEditing(resident)}
                            >
                              Edit
                            </button>
                            {active ? (
                              <button
                                type="button"
                                className="btn btn--sm"
                                onClick={() => setDeactivating(resident)}
                              >
                                Moved out
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="btn btn--sm"
                                onClick={async () => {
                                  await api.post(
                                    `/api/residents/${encodeURIComponent(resident.resident_id)}/reactivate`,
                                  )
                                  refresh()
                                  toast.success(`${resident.display_name} is active again`)
                                }}
                              >
                                Reactivate
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </Card>

      {creating && (
        <ResidentDialog
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false)
            refresh()
            toast.success('Resident added')
          }}
        />
      )}
      {editing && (
        <ResidentDialog
          resident={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            refresh()
            toast.success('Resident updated')
          }}
        />
      )}
      {deactivating && (
        <DeactivateDialog
          resident={deactivating}
          outstanding={balances.get(deactivating.resident_id)?.outstanding_charges_paise ?? 0}
          onClose={() => setDeactivating(null)}
          onSaved={() => {
            setDeactivating(null)
            refresh()
            toast.success('Move-out recorded', 'Their financial history is unchanged.')
          }}
        />
      )}
    </Page>
  )
}

function ResidentDialog({
  resident,
  onClose,
  onSaved,
}: {
  resident?: Resident
  onClose: () => void
  onSaved: () => void
}) {
  const editing = Boolean(resident)
  const [residentId, setResidentId] = useState(resident?.resident_id ?? '')
  const [displayName, setDisplayName] = useState(resident?.display_name ?? '')
  const [activeFrom, setActiveFrom] = useState(resident?.active_from ?? '')
  const [activeUntil, setActiveUntil] = useState(resident?.active_until ?? '')
  const [email, setEmail] = useState(resident?.email ?? '')
  const [phone, setPhone] = useState(resident?.phone ?? '')
  const [upiId, setUpiId] = useState(resident?.upi_id ?? '')
  const [notes, setNotes] = useState(resident?.notes ?? '')
  const [isActive, setIsActive] = useState(resident ? Boolean(resident.is_active) : true)
  const [touched, setTouched] = useState(false)

  const save = useMutation(
    () =>
      editing
        ? api.patch<Resident>(`/api/residents/${encodeURIComponent(resident!.resident_id)}`, {
            display_name: displayName.trim(),
            active_from: activeFrom || null,
            active_until: activeUntil || null,
            email: email || null,
            phone: phone || null,
            upi_id: upiId.trim() || null,
            notes: notes || null,
            is_active: isActive,
          })
        : api.post<Resident>('/api/residents', {
            resident_id: residentId.trim(),
            display_name: displayName.trim(),
            active_from: activeFrom || null,
            active_until: activeUntil || null,
            email: email || null,
            phone: phone || null,
            upi_id: upiId.trim() || null,
            notes: notes || null,
            is_active: isActive,
          }),
    { onSuccess: onSaved },
  )

  const idError = touched && !editing && !residentId.trim() ? 'An ID is required.' : null
  const nameError = touched && !displayName.trim() ? 'A name is required.' : null
  const dateError =
    activeFrom && activeUntil && activeUntil < activeFrom
      ? 'The move-out date cannot be before the move-in date.'
      : null
  const valid = Boolean(displayName.trim() && (editing || residentId.trim()) && !dateError)

  return (
    <Modal
      title={editing ? `Edit ${resident!.display_name}` : 'Add a resident'}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button
            type="submit"
            form="resident-form"
            className="btn btn--primary"
            disabled={save.pending}
          >
            {save.pending ? 'Saving…' : editing ? 'Save changes' : 'Add resident'}
          </button>
        </>
      }
    >
      <form
        id="resident-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (valid) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="form-grid">
          {!editing && (
            <TextField
              label="Resident ID"
              value={residentId}
              onChange={setResidentId}
              required
              error={idError}
              hint="A short identifier used in exports. It cannot be changed later."
              maxLength={64}
            />
          )}
          <TextField
            label="Display name"
            value={displayName}
            onChange={setDisplayName}
            required
            error={nameError}
            maxLength={200}
          />
          <DateField
            label="Active from"
            value={activeFrom}
            onChange={setActiveFrom}
            hint="When they moved in."
          />
          <DateField
            label="Active until"
            value={activeUntil}
            onChange={setActiveUntil}
            error={dateError}
            hint="When they moved out. Leave empty while they still live here."
          />
          <TextField label="Email" type="email" value={email} onChange={setEmail} maxLength={200} />
          <TextField label="Phone" type="tel" value={phone} onChange={setPhone} maxLength={50} />
          <TextField
            label="UPI ID"
            value={upiId}
            onChange={setUpiId}
            maxLength={320}
            placeholder="name@bank"
            hint="Optional. Adds a payable link to shared settlement summaries."
          />
        </div>
        <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
        <CheckboxField
          label="Currently living here"
          checked={isActive}
          onChange={setIsActive}
          hint="Unchecking this keeps every record; it only stops them being suggested for new expenses."
        />
      </form>
    </Modal>
  )
}

function DeactivateDialog({
  resident,
  outstanding,
  onClose,
  onSaved,
}: {
  resident: Resident
  outstanding: number
  onClose: () => void
  onSaved: () => void
}) {
  const [activeUntil, setActiveUntil] = useState(todayIso())
  const [reason, setReason] = useState('')
  const save = useMutation(
    () =>
      api.post(`/api/residents/${encodeURIComponent(resident.resident_id)}/deactivate`, {
        active_until: activeUntil,
        reason: reason || null,
      }),
    { onSuccess: onSaved },
  )

  return (
    <ConfirmDialog
      title={`Record that ${resident.display_name} moved out`}
      confirmLabel="Record move-out"
      pending={save.pending}
      error={save.error}
      onCancel={onClose}
      onConfirm={() => void save.run(undefined)}
      body={
        <div className="stack">
          <p style={{ margin: 0 }}>
            This marks them inactive and records the date. Their charges, payments, credits, and
            adjustments are all kept, and they stay in reconciliation until their balance is
            settled.
            {' '}
            <strong>Their share of this month is not prorated</strong> — if they take part in an
            expense settling now, they are charged a full share of it. Leave them out of that
            expense's participants instead if they should not be in it.
          </p>
          {outstanding !== 0 && (
            <Callout tone="warn" title="They still have an unsettled balance">
              <Money paise={outstanding} tone="owed-by" /> of charges are not yet settled. They will
              keep appearing on the dashboard until the balance is cleared or a ledger adjustment
              documents how it was resolved.
            </Callout>
          )}
        </div>
      }
    >
      <div className="form-grid">
        <DateField
          label="Move-out date"
          value={activeUntil}
          onChange={setActiveUntil}
          required
        />
        <TextField
          label="Reason (optional)"
          value={reason}
          onChange={setReason}
          hint="Recorded in the audit trail."
          maxLength={200}
        />
      </div>
    </ConfirmDialog>
  )
}
