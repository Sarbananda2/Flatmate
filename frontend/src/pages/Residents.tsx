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
  Card,
  EmptyState,
  ErrorNotice,
  Modal,
  Money,
} from '../components/Primitives'
import { CheckboxField, DateField, TextAreaField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { Avatar, Pill } from '../components/ui'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import type { Reconciliation, Resident } from '../api/types'
import { ExitClearanceModal } from './ExitClearanceModal'
import { whatsappHref } from './ResidentProfile'

export default function ResidentsPage() {
  const toast = useToast()
  const residents = useResource<{ items: Resident[]; total: number }>('/api/residents')
  const settlement = useResource<Reconciliation>('/api/reconciliation')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Resident | null>(null)
  const [deactivating, setDeactivating] = useState<Resident | null>(null)

  const balances = new Map(
    (settlement.data?.accounts ?? [])
      .filter((account) => account.account_type === 'resident')
      .map((account) => [account.account_id, account]),
  )

  const refresh = () => {
    residents.reload()
    settlement.reload()
  }

  return (
    <Page
      title="Residents"
      subtitle="The people sharing this apartment. Deactivating somebody records that they moved out; it never removes their financial history."
      actions={
        <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
          Add resident
        </button>
      }
    >
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
            <table className="data residents-table">
              <caption className="sr-only">Residents, how to reach them, and their balance</caption>
              <thead>
                <tr>
                  <th scope="col">Resident</th>
                  <th scope="col">Status</th>
                  <th scope="col">Contact &amp; UPI</th>
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
                  const net = account?.net_position_paise ?? 0
                  const whatsapp = resident.phone ? whatsappHref(resident.phone) : null
                  // Phone first, then whichever of UPI ID and email there is.
                  const second = resident.upi_id ?? resident.email
                  return (
                    <tr key={resident.resident_id}>
                      <th scope="row">
                        <span className="flex items-center gap-2.5 min-w-0">
                          <Avatar name={resident.display_name} tone={active ? 'accent' : 'neutral'} />
                          <Link
                            className="min-w-0 truncate font-semibold"
                            to={`/residents/${resident.resident_id}`}
                          >
                            {resident.display_name}
                          </Link>
                        </span>
                      </th>
                      <td>
                        {active ? (
                          <Pill tone="success">Active</Pill>
                        ) : (
                          <Pill>Moved out</Pill>
                        )}
                      </td>
                      <td>
                        {resident.phone || second ? (
                          <div className="min-w-0 space-y-0.5">
                            {resident.phone && (
                              <div className="flex items-center gap-2 min-w-0">
                                <span className="tabular truncate">{resident.phone}</span>
                                {whatsapp && (
                                  <a
                                    className="shrink-0 text-xs font-semibold"
                                    href={whatsapp}
                                    target="_blank"
                                    rel="noreferrer"
                                    aria-label={`WhatsApp ${resident.display_name}`}
                                  >
                                    WhatsApp
                                  </a>
                                )}
                              </div>
                            )}
                            {second && (
                              <div className="tabular truncate text-xs text-[var(--text-secondary)]">
                                {second}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="subtle">—</span>
                        )}
                      </td>
                      <td className="num">
                        {net === 0 ? (
                          <span className="subtle">Settled</span>
                        ) : (
                          <Money paise={net} tone="auto" sign="always" />
                        )}
                      </td>
                      <td>
                        <div className="flex items-center justify-end gap-2 whitespace-nowrap flex-nowrap">
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => setEditing(resident)}
                            aria-label={`Edit ${resident.display_name}`}
                          >
                            Edit
                          </button>
                          {active ? (
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => setDeactivating(resident)}
                              aria-label={`Record that ${resident.display_name} moved out`}
                            >
                              Moved out
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="btn btn--sm"
                              aria-label={`Reactivate ${resident.display_name}`}
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
        <ExitClearanceModal
          residentId={deactivating.resident_id}
          residentName={deactivating.display_name}
          onClose={() => setDeactivating(null)}
          onCleared={(clearance) => {
            // The slip stays on screen; the list behind it is brought up to date.
            refresh()
            toast.success(
              `${clearance.resident_display_name} has moved out`,
              'The clearance is recorded and their slip is on their page.',
            )
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
  const [contactName, setContactName] = useState(resident?.emergency_contact_name ?? '')
  const [contactPhone, setContactPhone] = useState(resident?.emergency_contact_phone ?? '')
  const [contactRelation, setContactRelation] = useState(
    resident?.emergency_contact_relation ?? '',
  )
  const emergencyContact = {
    emergency_contact_name: contactName.trim() || null,
    emergency_contact_phone: contactPhone.trim() || null,
    emergency_contact_relation: contactRelation.trim() || null,
  }
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
            ...emergencyContact,
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
            ...emergencyContact,
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
        <fieldset className="m-0 border-0 p-0 space-y-3">
          <legend className="p-0 text-sm font-bold text-[var(--text-primary)]">
            Emergency contact (optional)
          </legend>
          <div className="form-grid">
            <TextField
              label="Emergency contact name"
              value={contactName}
              onChange={setContactName}
              maxLength={200}
            />
            <TextField
              label="Emergency contact phone"
              type="tel"
              value={contactPhone}
              onChange={setContactPhone}
              maxLength={50}
            />
            <TextField
              label="Relationship"
              value={contactRelation}
              onChange={setContactRelation}
              maxLength={100}
              placeholder="e.g. Mother"
            />
          </div>
        </fieldset>
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
