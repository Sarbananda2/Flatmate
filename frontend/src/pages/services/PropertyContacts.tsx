/**
 * The Stakeholders & Contacts tab of Household & Property.
 *
 * The people the flat deals with, grouped the way the household thinks of
 * them: the owner, the broker, and the caretaker and society. Each card has
 * one-click Call, WhatsApp and Mail, the same shortcuts as a resident's card.
 * The form asks only for the fields that belong to the chosen role and checks
 * each one as the backend will, so a mistake is shown before anything is sent.
 */
import { useState } from 'react'
import {
  Briefcase,
  Building,
  Mail,
  MessageCircle,
  Pencil,
  Phone,
  Plus,
  Trash2,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { AsyncSection, ErrorNotice, Modal } from '../../components/Primitives'
import { MoneyInput, SelectField, TextAreaField, TextField } from '../../components/Form'
import {
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  Panel,
  Pill,
} from '../../components/ui'
import { useToast } from '../../components/Toast'
import { api } from '../../api/client'
import { type Resource, useMutation, useResource } from '../../lib/useApi'
import { paiseToInput, parseRupees } from '../../lib/format'
import type { ContactList, ContactRole, HouseholdContact } from '../../api/types'
import { telHref, whatsappHref } from '../ResidentProfile'

export const CONTACTS = '/api/property/contacts'

export const ROLE_LABELS: Record<ContactRole, string> = {
  owner: 'Property Owner',
  broker: 'Broker',
  caretaker: 'Caretaker',
  society_manager: 'Society Manager',
  emergency_utility: 'Emergency / Utility',
}

const GROUPS: Array<{ title: string; icon: LucideIcon; roles: ContactRole[]; empty: string }> = [
  {
    title: 'Property Owner (Landlord)',
    icon: Building,
    roles: ['owner'],
    empty: 'Add the owner, with their PAN for HRA proof and bank details for payouts.',
  },
  {
    title: 'Broker',
    icon: Briefcase,
    roles: ['broker'],
    empty: 'Add the broker who found the flat, with their agency and brokerage fee.',
  },
  {
    title: 'Caretaker & Society',
    icon: Wrench,
    roles: ['caretaker', 'society_manager', 'emergency_utility'],
    empty: 'Add whoever handles plumbing, electrical, gate passes and NOCs.',
  },
]

// The same rules the backend applies.
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const PHONE_PATTERN = /^\+?[0-9 ()-]+$/
const PAN_PATTERN = /^[A-Z]{5}[0-9]{4}[A-Z]$/
const IFSC_PATTERN = /^[A-Z]{4}0[A-Z0-9]{6}$/
const ACCOUNT_PATTERN = /^[0-9]{9,18}$/
const UPI_PATTERN = /^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$/

/** Which fields each role uses, beyond name, phone, email and notes. */
const ROLE_FIELDS: Record<ContactRole, Array<'owner' | 'broker' | 'upi' | 'duties'>> = {
  owner: ['owner', 'upi'],
  broker: ['broker', 'upi'],
  caretaker: ['duties', 'upi'],
  society_manager: ['duties', 'upi'],
  emergency_utility: ['duties'],
}

export function useContacts(): Resource<ContactList> {
  return useResource<ContactList>(CONTACTS)
}

function phoneError(value: string): string | null {
  const text = value.trim()
  if (!text) return null
  const digits = text.replace(/\D/g, '')
  return PHONE_PATTERN.test(text) && digits.length >= 7 && digits.length <= 15
    ? null
    : 'Enter a phone number of 7 to 15 digits.'
}

function patternError(value: string, pattern: RegExp, message: string): string | null {
  const text = value.trim()
  return text && !pattern.test(text) ? message : null
}

/* ---------------------------------------------------------------- the tab -- */

type Dialog = { kind: 'edit'; contact: HouseholdContact | null } | { kind: 'delete'; contact: HouseholdContact }

export function ContactsSection() {
  const contacts = useContacts()
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const toast = useToast()
  const done = (message: string) => {
    setDialog(null)
    contacts.reload()
    toast.success(message)
  }

  return (
    <>
      <div className="flex justify-end">
        <button
          type="button"
          className={BUTTON_PRIMARY}
          onClick={() => setDialog({ kind: 'edit', contact: null })}
        >
          <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          Add Contact
        </button>
      </div>
      <AsyncSection resource={contacts} loadingLabel="Loading contacts…">
        {(data) => (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {GROUPS.map((group) => {
              const members = data.items.filter((contact) => group.roles.includes(contact.role))
              const Icon = group.icon
              return (
                <Panel
                  key={group.title}
                  title={group.title}
                  icon={<Icon className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
                >
                  <div role="list" aria-label={group.title} className="space-y-3">
                    {members.length === 0 ? (
                      <p className="m-0 text-xs text-[var(--text-secondary)]">{group.empty}</p>
                    ) : (
                      members.map((contact) => (
                        <ContactCard
                          key={contact.contact_id}
                          contact={contact}
                          onEdit={() => setDialog({ kind: 'edit', contact })}
                          onDelete={() => setDialog({ kind: 'delete', contact })}
                        />
                      ))
                    )}
                  </div>
                </Panel>
              )
            })}
          </div>
        )}
      </AsyncSection>

      {dialog?.kind === 'edit' && (
        <ContactModal
          contact={dialog.contact}
          onClose={() => setDialog(null)}
          onDone={(contact) =>
            done(dialog.contact ? `${contact.name} saved` : `${contact.name} added`)
          }
        />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteContactModal
          contact={dialog.contact}
          onClose={() => setDialog(null)}
          onDone={() => done(`${dialog.contact.name} removed`)}
        />
      )}
    </>
  )
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs min-w-0">
      <dt className="text-[var(--text-secondary)] shrink-0">{label}</dt>
      <dd className="m-0 font-semibold text-[var(--text-primary)] tabular truncate">{children}</dd>
    </div>
  )
}

function ContactCard({
  contact,
  onEdit,
  onDelete,
}: {
  contact: HouseholdContact
  onEdit: () => void
  onDelete: () => void
}) {
  const whatsapp = contact.phone ? whatsappHref(contact.phone) : null
  return (
    <article
      role="listitem"
      aria-label={contact.name}
      className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-3 min-w-0"
    >
      <header className="flex items-start justify-between gap-2 min-w-0">
        <div className="min-w-0">
          <div className="text-sm font-bold text-[var(--text-primary)] truncate">{contact.name}</div>
          <div className="text-[11px] text-[var(--text-secondary)] truncate">
            {ROLE_LABELS[contact.role]}
            {contact.agency_name && ` · ${contact.agency_name}`}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            className={BUTTON_QUIET}
            aria-label={`Edit ${contact.name}`}
            title="Edit"
            onClick={onEdit}
          >
            <Pencil className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
          <button
            type="button"
            className={BUTTON_QUIET}
            aria-label={`Remove ${contact.name}`}
            title="Remove"
            onClick={onDelete}
          >
            <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
        </div>
      </header>

      <dl className="m-0 space-y-1.5">
        <Detail label="Phone">{contact.phone ?? '—'}</Detail>
        <Detail label="Email">{contact.email ?? '—'}</Detail>
        {contact.role === 'owner' && (
          <>
            <Detail label="PAN">
              {contact.pan ? <Pill tone="accent">PAN {contact.pan}</Pill> : '—'}
            </Detail>
            <Detail label="Bank">
              {contact.bank_account_no
                ? `${contact.bank_account_no}${contact.bank_ifsc ? ` · ${contact.bank_ifsc}` : ''}`
                : '—'}
            </Detail>
          </>
        )}
        {contact.upi_id && <Detail label="UPI">{contact.upi_id}</Detail>}
        {contact.role === 'broker' && contact.fee_display && (
          <Detail label="Brokerage">
            <span className="inline-flex items-center gap-1.5">
              {contact.fee_display}
              <Pill tone={contact.fee_status === 'paid' ? 'success' : 'warning'}>
                {contact.fee_status === 'paid'
                  ? 'Paid'
                  : contact.fee_status === 'na'
                    ? 'Not applicable'
                    : 'Pending'}
              </Pill>
            </span>
          </Detail>
        )}
        {contact.responsibilities && (
          <Detail label="Looks after">{contact.responsibilities}</Detail>
        )}
      </dl>
      {contact.notes && (
        <p className="m-0 text-[11px] text-[var(--text-secondary)]">{contact.notes}</p>
      )}

      {(contact.phone || contact.email) && (
        <div className="flex flex-wrap items-center gap-2">
          {contact.phone && (
            <a
              className={BUTTON_SECONDARY}
              href={telHref(contact.phone)}
              aria-label={`Call ${contact.name}`}
            >
              <Phone className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Call
            </a>
          )}
          {whatsapp && (
            <a
              className={BUTTON_SECONDARY}
              href={whatsapp}
              target="_blank"
              rel="noreferrer"
              aria-label={`WhatsApp ${contact.name}`}
            >
              <MessageCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              WhatsApp
            </a>
          )}
          {contact.email && (
            <a
              className={BUTTON_SECONDARY}
              href={`mailto:${contact.email}`}
              aria-label={`Mail ${contact.name}`}
            >
              <Mail className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Mail
            </a>
          )}
        </div>
      )}
    </article>
  )
}

/* ----------------------------------------------------------------- modals -- */

export function ContactModal({
  contact,
  onClose,
  onDone,
}: {
  contact: HouseholdContact | null
  onClose: () => void
  onDone: (contact: HouseholdContact) => void
}) {
  const [role, setRole] = useState<ContactRole | ''>(contact?.role ?? '')
  const [name, setName] = useState(contact?.name ?? '')
  const [phone, setPhone] = useState(contact?.phone ?? '')
  const [email, setEmail] = useState(contact?.email ?? '')
  const [pan, setPan] = useState(contact?.pan ?? '')
  const [account, setAccount] = useState(contact?.bank_account_no ?? '')
  const [ifsc, setIfsc] = useState(contact?.bank_ifsc ?? '')
  const [upi, setUpi] = useState(contact?.upi_id ?? '')
  const [agency, setAgency] = useState(contact?.agency_name ?? '')
  const [fee, setFee] = useState(paiseToInput(contact?.fee_amount_paise))
  const [feeStatus, setFeeStatus] = useState<'paid' | 'pending' | 'na' | ''>(
    contact?.fee_status ?? '',
  )
  const [duties, setDuties] = useState(contact?.responsibilities ?? '')
  const [notes, setNotes] = useState(contact?.notes ?? '')
  const [submitted, setSubmitted] = useState(false)

  const uses = role ? ROLE_FIELDS[role] : []
  const parsedFee = parseRupees(fee, { allowEmpty: true })
  const errors = {
    role: role ? null : 'Choose what this contact is to the household.',
    name: name.trim() ? (name.trim().length > 200 ? 'Keep it to 200 characters.' : null) : 'Enter their name.',
    phone: phoneError(phone),
    email: patternError(email, EMAIL_PATTERN, 'Enter a valid email address.'),
    pan: uses.includes('owner')
      ? patternError(pan.toUpperCase(), PAN_PATTERN, 'A PAN looks like ABCDE1234F.')
      : null,
    account: uses.includes('owner')
      ? patternError(account, ACCOUNT_PATTERN, 'A bank account number is 9 to 18 digits.')
      : null,
    ifsc: uses.includes('owner')
      ? patternError(ifsc.toUpperCase(), IFSC_PATTERN, 'An IFSC looks like HDFC0001234.')
      : null,
    upi: uses.includes('upi') ? patternError(upi, UPI_PATTERN, 'A UPI ID looks like name@bank.') : null,
    fee: uses.includes('broker') && fee.trim() && !parsedFee.ok ? (parsedFee.error ?? 'Enter an amount.') : null,
  }
  const valid = Object.values(errors).every((error) => error === null)
  const shown = (error: string | null) => (submitted ? error : null)

  const body = () => {
    const text = (value: string) => value.trim() || null
    return {
      role,
      name: name.trim(),
      phone: text(phone),
      email: text(email),
      notes: text(notes),
      pan: uses.includes('owner') ? text(pan.toUpperCase()) : null,
      bank_account_no: uses.includes('owner') ? text(account) : null,
      bank_ifsc: uses.includes('owner') ? text(ifsc.toUpperCase()) : null,
      upi_id: uses.includes('upi') ? text(upi) : null,
      agency_name: uses.includes('broker') ? text(agency) : null,
      fee_amount_paise: uses.includes('broker') && fee.trim() ? parsedFee.paise : null,
      fee_status: uses.includes('broker') && fee.trim() ? feeStatus || 'pending' : null,
      responsibilities: uses.includes('duties') ? text(duties) : null,
    }
  }

  const save = useMutation(
    () =>
      contact
        ? api.patch<HouseholdContact>(`${CONTACTS}/${encodeURIComponent(contact.contact_id)}`, body())
        : api.post<HouseholdContact>(CONTACTS, body()),
    { onSuccess: onDone },
  )

  return (
    <Modal
      title={contact ? `Edit ${contact.name}` : 'Add Contact'}
      description="Only the details that belong to the chosen role are asked for."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form="contact-form" className={BUTTON_PRIMARY} disabled={save.pending}>
            {save.pending ? 'Saving…' : contact ? 'Save Contact' : 'Add Contact'}
          </button>
        </>
      }
    >
      <form
        id="contact-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setSubmitted(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <SelectField
          label="Role"
          value={role}
          onChange={setRole}
          placeholder="Choose a role"
          required
          options={(Object.keys(ROLE_LABELS) as ContactRole[]).map((value) => ({
            value,
            label: ROLE_LABELS[value],
          }))}
          error={shown(errors.role)}
        />
        <TextField label="Name" value={name} onChange={setName} required error={shown(errors.name)} />
        <TextField
          label="Phone"
          type="tel"
          value={phone}
          onChange={setPhone}
          placeholder="+91 98765 43210"
          error={shown(errors.phone)}
        />
        <TextField label="Email" type="email" value={email} onChange={setEmail} error={shown(errors.email)} />
        {uses.includes('owner') && (
          <>
            <TextField
              label="PAN"
              value={pan}
              onChange={setPan}
              placeholder="ABCDE1234F"
              hint="For HRA proof on rent receipts."
              error={shown(errors.pan)}
            />
            <TextField
              label="Bank account number"
              value={account}
              onChange={setAccount}
              error={shown(errors.account)}
            />
            <TextField label="IFSC" value={ifsc} onChange={setIfsc} placeholder="HDFC0001234" error={shown(errors.ifsc)} />
          </>
        )}
        {uses.includes('broker') && (
          <>
            <TextField label="Agency" value={agency} onChange={setAgency} />
            <MoneyInput
              label="Brokerage fee"
              value={fee}
              onChange={(raw) => setFee(raw)}
              error={shown(errors.fee)}
            />
            {fee.trim() && (
              <SelectField
                label="Fee status"
                value={feeStatus || 'pending'}
                onChange={setFeeStatus}
                options={[
                  { value: 'pending', label: 'Pending' },
                  { value: 'paid', label: 'Paid' },
                  { value: 'na', label: 'Not applicable' },
                ]}
              />
            )}
          </>
        )}
        {uses.includes('upi') && (
          <TextField label="UPI ID" value={upi} onChange={setUpi} placeholder="name@bank" error={shown(errors.upi)} />
        )}
        {uses.includes('duties') && (
          <TextField
            label="Responsibilities"
            value={duties}
            onChange={setDuties}
            placeholder="Plumbing, electrical, gate passes, NOC"
          />
        )}
        <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
      </form>
    </Modal>
  )
}

function DeleteContactModal({
  contact,
  onClose,
  onDone,
}: {
  contact: HouseholdContact
  onClose: () => void
  onDone: () => void
}) {
  const remove = useMutation(
    () => api.delete(`${CONTACTS}/${encodeURIComponent(contact.contact_id)}`),
    { onSuccess: onDone },
  )
  return (
    <Modal
      title="Remove Contact"
      description={`${contact.name} (${ROLE_LABELS[contact.role]}) will be removed. The audit trail keeps a record.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={remove.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={remove.pending}
            onClick={() => void remove.run(undefined)}
          >
            {remove.pending ? 'Removing…' : 'Remove Contact'}
          </button>
        </>
      }
    >
      {remove.error ? <ErrorNotice error={remove.error} /> : null}
    </Modal>
  )
}

