/**
 * A resident's dossier: how to reach them, who to call in an emergency, a QR
 * code that pays them by UPI, and who they are beyond that -- their work, the
 * terms they moved in on, their vehicle and building access, and identity
 * verification. Each card edits only its own fields (Rule U5).
 *
 * None of this is accounting. The QR code carries only who to pay -- no
 * amount -- so it never states a figure the ledger did not produce. An
 * identity number is only ever shown by its last four digits.
 */
import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, LifeBuoy, Mail, MessageCircle, Pencil, Phone } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { Badge, Card, ErrorNotice, Modal } from '../components/Primitives'
import { DateField, SelectField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import {
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  CopyButton,
  EditButton,
  Pill,
  type Tone,
} from '../components/ui'
import { api } from '../api/client'
import { useMutation } from '../lib/useApi'
import { formatDate } from '../lib/format'
import type {
  BloodGroup,
  IdDocumentType,
  PoliceVerificationStatus,
  Resident,
  SocietyNocStatus,
  VehicleType,
  WorkMode,
} from '../api/types'

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
// The same shape the backend accepts for a resident's UPI ID.
const UPI_PATTERN = /^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$/

/** `+91 98765 43210` -> `tel:+919876543210`. */
export function telHref(phone: string): string {
  return `tel:${phone.replace(/[^\d+]/g, '')}`
}

/**
 * A wa.me link, which needs the number in full international form without the
 * plus. A bare ten-digit number is taken to be Indian. Null when the number is
 * too short to be one.
 */
export function whatsappHref(phone: string): string | null {
  let digits = phone.replace(/\D/g, '').replace(/^0+/, '')
  if (digits.length === 10) digits = `91${digits}`
  return digits.length >= 11 && digits.length <= 15 ? `https://wa.me/${digits}` : null
}

/** The UPI link a payment app opens: who to pay, with no amount set. */
export function upiPayUri(upiId: string, payeeName: string): string {
  return `upi://pay?pa=${upiId}&pn=${encodeURIComponent(payeeName)}&cu=INR`
}

function ContactRow({
  label,
  value,
  children,
}: {
  label: string
  value: string | null
  children?: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 min-w-0">
      <div className="min-w-0">
        <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
          {label}
        </div>
        {value ? (
          <div className="tabular text-sm font-semibold text-[var(--text-primary)] truncate">
            {value}
          </div>
        ) : (
          <div className="text-sm text-[var(--text-tertiary)]">Not recorded</div>
        )}
      </div>
      {value && children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}

/** One emergency contact: name, relationship, number, and a way to call. */
function EmergencyPerson({
  name,
  relation,
  phone,
  label,
}: {
  name: string | null
  relation: string | null
  phone: string | null
  label?: string
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 min-w-0">
      <div className="min-w-0">
        {label && (
          <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
            {label}
          </div>
        )}
        <div className="text-sm font-semibold text-[var(--text-primary)] truncate">
          {name ?? 'Name not recorded'}
          {relation && (
            <span className="font-normal text-[var(--text-secondary)]">
              {' '}
              · {relation}
            </span>
          )}
        </div>
        <div className="tabular text-xs text-[var(--text-secondary)]">
          {phone ?? 'Phone not recorded'}
        </div>
      </div>
      {phone && (
        <a
          className={BUTTON_QUIET}
          href={telHref(phone)}
          aria-label={`Call ${label ? label.toLowerCase() : 'emergency contact'}${name ? ` ${name}` : ''}`}
        >
          <Phone className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          Call
        </a>
      )}
    </div>
  )
}

export function ContactCard({ resident, onEdit }: { resident: Resident; onEdit: () => void }) {
  const whatsapp = resident.phone ? whatsappHref(resident.phone) : null
  const hasEmergency = Boolean(
    resident.emergency_contact_name ||
      resident.emergency_contact_phone ||
      resident.emergency_contact_relation,
  )
  const hasSecond = Boolean(
    resident.secondary_emergency_name ||
      resident.secondary_emergency_phone ||
      resident.secondary_emergency_relation,
  )
  return (
    <Card
      id="resident-contact"
      title="Contact & Emergency"
      hint="How to reach them, and who to call if something goes wrong."
      actions={
        <button type="button" className={BUTTON_SECONDARY} onClick={onEdit}>
          <Pencil className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          Edit Profile
        </button>
      }
    >
      <div className="space-y-4">
        <ContactRow label="Phone" value={resident.phone}>
          <a className={BUTTON_QUIET} href={telHref(resident.phone ?? '')}>
            <Phone className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Call
          </a>
          {whatsapp && (
            <a className={BUTTON_QUIET} href={whatsapp} target="_blank" rel="noreferrer">
              <MessageCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              WhatsApp
            </a>
          )}
        </ContactRow>
        <ContactRow label="Email" value={resident.email}>
          <a className={BUTTON_QUIET} href={`mailto:${resident.email ?? ''}`}>
            <Mail className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Mail
          </a>
        </ContactRow>

        <section
          aria-label="Emergency contact"
          className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-2.5"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs font-bold text-[var(--text-primary)]">
              <LifeBuoy className="w-4 h-4 shrink-0 text-[var(--danger)]" aria-hidden="true" />
              Emergency contact
            </div>
            {/* What a hospital asks first, so it sits with who to call. */}
            {resident.blood_group && <Pill tone="danger">Blood: {resident.blood_group}</Pill>}
          </div>
          {hasEmergency || hasSecond ? (
            <div className="space-y-3">
              {hasEmergency && (
                <EmergencyPerson
                  name={resident.emergency_contact_name}
                  relation={resident.emergency_contact_relation}
                  phone={resident.emergency_contact_phone}
                />
              )}
              {hasSecond && (
                <EmergencyPerson
                  label="Second contact"
                  name={resident.secondary_emergency_name}
                  relation={resident.secondary_emergency_relation}
                  phone={resident.secondary_emergency_phone}
                />
              )}
            </div>
          ) : (
            <p className="m-0 text-xs text-[var(--text-secondary)]">
              No emergency contact recorded. Add a next of kin from Edit Profile.
            </p>
          )}
        </section>
      </div>
    </Card>
  )
}

export function UpiCard({ resident }: { resident: Resident }) {
  if (!resident.upi_id) return null
  const uri = upiPayUri(resident.upi_id, resident.display_name)
  return (
    <Card
      id="resident-upi"
      title="Scan to Pay / UPI Settlement"
      hint={`Scan with any UPI app to pay ${resident.display_name}. No amount is set; enter it in the app.`}
    >
      <div className="flex flex-wrap items-center gap-5">
        {/* A QR code is read dark-on-light, so it keeps a white tile in both themes. */}
        <div className="rounded-xl bg-white p-3 shrink-0 border border-[var(--border-subtle)]">
          <QRCodeSVG
            value={uri}
            size={168}
            level="M"
            role="img"
            aria-label={`UPI payment QR code for ${resident.display_name}`}
          />
        </div>
        <div className="min-w-0 space-y-2">
          <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
            UPI ID
          </div>
          <div className="flex items-center gap-2 min-w-0">
            <span className="tabular text-sm font-semibold text-[var(--text-primary)] truncate select-all">
              {resident.upi_id}
            </span>
            <CopyButton text={resident.upi_id} label="Copy UPI ID" iconOnly />
          </div>
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            Payments made this way are not recorded automatically. Record them under Payments.
          </p>
        </div>
      </div>
    </Card>
  )
}

export function EditProfileDialog({
  resident,
  onClose,
  onSaved,
}: {
  resident: Resident
  onClose: () => void
  onSaved: () => void
}) {
  const [phone, setPhone] = useState(resident.phone ?? '')
  const [email, setEmail] = useState(resident.email ?? '')
  const [upiId, setUpiId] = useState(resident.upi_id ?? '')
  const [contactName, setContactName] = useState(resident.emergency_contact_name ?? '')
  const [contactRelation, setContactRelation] = useState(resident.emergency_contact_relation ?? '')
  const [contactPhone, setContactPhone] = useState(resident.emergency_contact_phone ?? '')
  const [bloodGroup, setBloodGroup] = useState<BloodGroup | ''>(resident.blood_group ?? '')
  const [secondName, setSecondName] = useState(resident.secondary_emergency_name ?? '')
  const [secondRelation, setSecondRelation] = useState(resident.secondary_emergency_relation ?? '')
  const [secondPhone, setSecondPhone] = useState(resident.secondary_emergency_phone ?? '')

  const emailError =
    email.trim() && !EMAIL_PATTERN.test(email.trim()) ? 'Enter a valid email address.' : null
  const upiError =
    upiId.trim() && !UPI_PATTERN.test(upiId.trim()) ? 'A UPI ID looks like name@bank.' : null

  const save = useMutation(
    () =>
      api.patch<Resident>(`/api/residents/${encodeURIComponent(resident.resident_id)}`, {
        phone: phone.trim() || null,
        email: email.trim() || null,
        upi_id: upiId.trim() || null,
        emergency_contact_name: contactName.trim() || null,
        emergency_contact_relation: contactRelation.trim() || null,
        emergency_contact_phone: contactPhone.trim() || null,
        blood_group: bloodGroup || null,
        secondary_emergency_name: secondName.trim() || null,
        secondary_emergency_relation: secondRelation.trim() || null,
        secondary_emergency_phone: secondPhone.trim() || null,
      }),
    { onSuccess: onSaved },
  )
  const valid = !emailError && !upiError

  return (
    <Modal
      title={`Edit ${resident.display_name}'s profile`}
      description="Contact details only. Nothing here affects any charge, payment or balance."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button
            type="submit"
            form="resident-profile-form"
            className={BUTTON_PRIMARY}
            disabled={!valid || save.pending}
          >
            {save.pending ? 'Saving…' : 'Save Profile'}
          </button>
        </>
      }
    >
      <form
        id="resident-profile-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField label="Phone" type="tel" value={phone} onChange={setPhone} maxLength={50} />
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            maxLength={200}
            error={emailError}
          />
        </div>
        <TextField
          label="UPI ID"
          value={upiId}
          onChange={setUpiId}
          maxLength={320}
          placeholder="name@bank"
          error={upiError}
          hint="Shows a scan-to-pay QR code on this page and a payable link in settlement summaries."
        />
        <fieldset className="m-0 border-0 p-0 space-y-3">
          <legend className="p-0 text-sm font-bold text-[var(--text-primary)]">
            Emergency contact
          </legend>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <TextField
              label="Next-of-kin name"
              value={contactName}
              onChange={setContactName}
              maxLength={200}
            />
            <TextField
              label="Relationship"
              value={contactRelation}
              onChange={setContactRelation}
              maxLength={100}
              placeholder="e.g. Mother"
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <TextField
              label="Emergency phone"
              type="tel"
              value={contactPhone}
              onChange={setContactPhone}
              maxLength={50}
            />
            <SelectField<BloodGroup | ''>
              label="Blood group"
              value={bloodGroup}
              options={[
                { value: '', label: 'Not recorded' },
                ...BLOOD_GROUPS.map((group) => ({ value: group, label: group })),
              ]}
              onChange={setBloodGroup}
            />
          </div>
        </fieldset>
        <fieldset className="m-0 border-0 p-0 space-y-3">
          <legend className="p-0 text-sm font-bold text-[var(--text-primary)]">
            Second emergency contact
          </legend>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <TextField
              label="Second contact name"
              value={secondName}
              onChange={setSecondName}
              maxLength={200}
            />
            <TextField
              label="Second contact relationship"
              value={secondRelation}
              onChange={setSecondRelation}
              maxLength={100}
              placeholder="e.g. Brother"
            />
          </div>
          <TextField
            label="Second contact phone"
            type="tel"
            value={secondPhone}
            onChange={setSecondPhone}
            maxLength={50}
          />
        </fieldset>
      </form>
    </Modal>
  )
}

/* -------------------------------------------------------------- dossier -- */

const BLOOD_GROUPS: BloodGroup[] = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']

const WORK_MODES: Array<{ value: WorkMode; label: string }> = [
  { value: 'wfh', label: 'WFH' },
  { value: 'hybrid', label: 'Hybrid' },
  { value: 'onsite', label: 'On-site' },
]

const VEHICLES: Array<{ value: VehicleType; label: string }> = [
  { value: 'none', label: 'No vehicle' },
  { value: 'two_wheeler', label: '2-Wheeler' },
  { value: 'four_wheeler', label: '4-Wheeler' },
  { value: 'both', label: '2- & 4-Wheeler' },
]

const ID_DOCUMENTS: Array<{ value: IdDocumentType; label: string }> = [
  { value: 'aadhaar', label: 'Aadhaar' },
  { value: 'passport', label: 'Passport' },
  { value: 'voter_id', label: 'Voter ID' },
  { value: 'driving_license', label: 'Driving Licence' },
  { value: 'other', label: 'Other ID' },
]

const POLICE_STATUS: Record<PoliceVerificationStatus, { label: string; tone: Tone }> = {
  verified: { label: 'Verified', tone: 'success' },
  submitted: { label: 'Submitted', tone: 'accent' },
  pending: { label: 'Pending', tone: 'warning' },
  exempt: { label: 'Exempt', tone: 'neutral' },
}

const NOC_STATUS: Record<SocietyNocStatus, { label: string; tone: Tone }> = {
  approved: { label: 'Approved', tone: 'success' },
  pending: { label: 'Pending', tone: 'warning' },
  not_applicable: { label: 'Not applicable', tone: 'neutral' },
}

const AADHAAR_PATTERN = /^\d{12}$/
const ID_NUMBER_PATTERN = /^[A-Za-z0-9 -]{4,30}$/

function labelOf<T extends string>(options: Array<{ value: T; label: string }>, value: T | null) {
  return options.find((option) => option.value === value)?.label ?? null
}

/** An identity number as the screen shows it: its last four digits, the rest hidden. */
export function maskedIdNumber(type: IdDocumentType | null, number: string | null): string | null {
  if (!number) return null
  const tail = number.replace(/[\s-]/g, '').slice(-4)
  return type === 'aadhaar' ? `•••• •••• ${tail}` : `•••• ${tail}`
}

/** One labelled fact on a dossier card, or "Not recorded" when there is none. */
function DossierRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 min-w-0">
      <dt className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
        {label}
      </dt>
      <dd className="m-0 min-w-0 text-sm font-semibold text-[var(--text-primary)] break-words text-right">
        {children || (
          <span className="font-normal text-[var(--text-tertiary)]">Not recorded</span>
        )}
      </dd>
    </div>
  )
}

/** Closes a card's dialog, reloads the resident, and says what was saved. */
function useSaved(close: () => void, onReload: () => void, message: string) {
  const toast = useToast()
  return () => {
    close()
    onReload()
    toast.success(message)
  }
}

/**
 * The dialog every dossier card edits through. It sends only the card's own
 * fields, so saving one card never touches another's.
 */
function DossierDialog({
  resident,
  title,
  description,
  payload,
  valid = true,
  onClose,
  onSaved,
  children,
}: {
  resident: Resident
  title: string
  description: string
  payload: () => Record<string, unknown>
  valid?: boolean
  onClose: () => void
  onSaved: () => void
  children: React.ReactNode
}) {
  const formId = useId()
  const save = useMutation(
    () => api.patch<Resident>(`/api/residents/${encodeURIComponent(resident.resident_id)}`, payload()),
    { onSuccess: onSaved },
  )
  return (
    <Modal
      title={title}
      description={description}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button
            type="submit"
            form={formId}
            className={BUTTON_PRIMARY}
            disabled={!valid || save.pending}
          >
            {save.pending ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      <form
        id={formId}
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        {children}
      </form>
    </Modal>
  )
}

/* ------------------------------------------------------- work & profession -- */

export function ProfessionalCard({ resident, onReload }: { resident: Resident; onReload: () => void }) {
  const [editing, setEditing] = useState(false)
  const saved = useSaved(() => setEditing(false), onReload, 'Work details saved')
  const mode = labelOf(WORK_MODES, resident.work_mode)
  return (
    <Card
      id="resident-work"
      title="Work & Profession"
      hint="What they do and where, and whether they are usually home in the day."
      actions={<EditButton label="Edit work and profession" onClick={() => setEditing(true)} />}
    >
      <dl className="m-0 space-y-3">
        <DossierRow label="Job title / Profession">{resident.profession}</DossierRow>
        <DossierRow label="Employer / University">{resident.employer}</DossierRow>
        <DossierRow label="Work mode">{mode && <Pill tone="accent">{mode}</Pill>}</DossierRow>
      </dl>
      {editing && (
        <EditProfessionalDialog resident={resident} onClose={() => setEditing(false)} onSaved={saved} />
      )}
    </Card>
  )
}

export function EditProfessionalDialog({
  resident,
  onClose,
  onSaved,
}: {
  resident: Resident
  onClose: () => void
  onSaved: () => void
}) {
  const [profession, setProfession] = useState(resident.profession ?? '')
  const [employer, setEmployer] = useState(resident.employer ?? '')
  const [workMode, setWorkMode] = useState<WorkMode | ''>(resident.work_mode ?? '')
  return (
    <DossierDialog
      resident={resident}
      title="Work & Profession"
      description={`What ${resident.display_name} does, and where.`}
      payload={() => ({
        profession: profession.trim() || null,
        employer: employer.trim() || null,
        work_mode: workMode || null,
      })}
      onClose={onClose}
      onSaved={onSaved}
    >
      <TextField
        label="Job title / Profession"
        value={profession}
        onChange={setProfession}
        maxLength={200}
        placeholder="e.g. Software engineer"
      />
      <TextField
        label="Employer / University"
        value={employer}
        onChange={setEmployer}
        maxLength={200}
      />
      <SelectField<WorkMode | ''>
        label="Work mode"
        value={workMode}
        options={[{ value: '', label: 'Not recorded' }, ...WORK_MODES]}
        onChange={setWorkMode}
      />
    </DossierDialog>
  )
}

/* --------------------------------------------------------- tenancy terms -- */

export function TenancyTermsCard({ resident, onReload }: { resident: Resident; onReload: () => void }) {
  const [editing, setEditing] = useState(false)
  const saved = useSaved(() => setEditing(false), onReload, 'Tenancy terms saved')
  const days = resident.notice_period_days
  return (
    <Card
      id="resident-tenancy"
      title="Tenancy Terms"
      hint="What they agreed to when they moved in."
      actions={<EditButton label="Edit tenancy terms" onClick={() => setEditing(true)} />}
    >
      <dl className="m-0 space-y-3">
        <DossierRow label="Master lease">
          {resident.is_lease_signatory ? (
            <Pill tone="accent">Primary Lease Signatory</Pill>
          ) : (
            <Pill>Co-tenant</Pill>
          )}
        </DossierRow>
        <DossierRow label="Notice period">
          {days != null && `${days} day${days === 1 ? '' : 's'} notice required`}
        </DossierRow>
        <DossierRow label="Lock-in">
          {resident.lock_in_until && `Lock-in until ${formatDate(resident.lock_in_until)}`}
        </DossierRow>
        <DossierRow label="Keys & handover">{resident.keys_issued_notes}</DossierRow>
      </dl>
      {editing && (
        <EditTenancyTermsDialog resident={resident} onClose={() => setEditing(false)} onSaved={saved} />
      )}
    </Card>
  )
}

export function EditTenancyTermsDialog({
  resident,
  onClose,
  onSaved,
}: {
  resident: Resident
  onClose: () => void
  onSaved: () => void
}) {
  const [signatory, setSignatory] = useState(Boolean(resident.is_lease_signatory))
  const [noticeDays, setNoticeDays] = useState(
    resident.notice_period_days != null ? String(resident.notice_period_days) : '',
  )
  const [lockIn, setLockIn] = useState(resident.lock_in_until ?? '')
  const [keys, setKeys] = useState(resident.keys_issued_notes ?? '')
  const noticeError =
    noticeDays.trim() && !/^\d{1,4}$/.test(noticeDays.trim())
      ? 'Enter a whole number of days, such as 30.'
      : null
  return (
    <DossierDialog
      resident={resident}
      title="Tenancy Terms"
      description={`The terms ${resident.display_name} moved in on. Nothing here changes any charge.`}
      payload={() => ({
        is_lease_signatory: signatory,
        notice_period_days: noticeDays.trim() ? Number(noticeDays.trim()) : null,
        lock_in_until: lockIn || null,
        keys_issued_notes: keys.trim() || null,
      })}
      valid={!noticeError}
      onClose={onClose}
      onSaved={onSaved}
    >
      <label className="flex items-start gap-2.5 cursor-pointer">
        <input
          type="checkbox"
          className="w-auto mt-0.5"
          checked={signatory}
          onChange={(event) => setSignatory(event.target.checked)}
        />
        <span className="text-sm">
          <span className="font-semibold text-[var(--text-primary)]">Signs the master lease</span>
          <span className="block text-xs text-[var(--text-secondary)]">
            The landlord&apos;s agreement names them as a tenant.
          </span>
        </span>
      </label>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <TextField
          label="Notice period (days)"
          value={noticeDays}
          onChange={setNoticeDays}
          inputMode="numeric"
          maxLength={4}
          placeholder="e.g. 30"
          error={noticeError}
        />
        <div className="space-y-1.5">
          <DateField label="Lock-in until" value={lockIn} onChange={setLockIn} />
          {lockIn && (
            <button type="button" className={BUTTON_QUIET} onClick={() => setLockIn('')}>
              No lock-in
            </button>
          )}
        </div>
      </div>
      <TextField
        label="Keys & handover"
        value={keys}
        onChange={setKeys}
        maxLength={500}
        placeholder="e.g. 2 keys & AC remote"
      />
    </DossierDialog>
  )
}

/* ------------------------------------------------ vehicle & building access -- */

/** `4-Wheeler (DL 3C AB 1234)`, `No vehicle`, or null when nothing is recorded. */
export function vehicleSummary(resident: Pick<Resident, 'vehicle_type' | 'vehicle_reg_no'>): string | null {
  const type = labelOf(VEHICLES, resident.vehicle_type)
  if (resident.vehicle_type === 'none') return type
  if (type && resident.vehicle_reg_no) return `${type} (${resident.vehicle_reg_no})`
  return type ?? resident.vehicle_reg_no
}

export function BuildingLogisticsCard({
  resident,
  onReload,
}: {
  resident: Resident
  onReload: () => void
}) {
  const [editing, setEditing] = useState(false)
  const saved = useSaved(() => setEditing(false), onReload, 'Vehicle and access saved')
  return (
    <Card
      id="resident-logistics"
      title="Vehicle & Building Access"
      hint="Their vehicle, parking and the society's access tag."
      actions={<EditButton label="Edit vehicle and building access" onClick={() => setEditing(true)} />}
    >
      <dl className="m-0 space-y-3">
        <DossierRow label="Vehicle">{vehicleSummary(resident)}</DossierRow>
        <DossierRow label="Parking slot">{resident.parking_slot}</DossierRow>
        <DossierRow label="Society RFID tag">
          {resident.society_rfid_tag && <span className="tabular">{resident.society_rfid_tag}</span>}
        </DossierRow>
      </dl>
      {editing && (
        <EditLogisticsDialog resident={resident} onClose={() => setEditing(false)} onSaved={saved} />
      )}
    </Card>
  )
}

export function EditLogisticsDialog({
  resident,
  onClose,
  onSaved,
}: {
  resident: Resident
  onClose: () => void
  onSaved: () => void
}) {
  const [vehicle, setVehicle] = useState<VehicleType | ''>(resident.vehicle_type ?? '')
  const [registration, setRegistration] = useState(resident.vehicle_reg_no ?? '')
  const [parking, setParking] = useState(resident.parking_slot ?? '')
  const [rfid, setRfid] = useState(resident.society_rfid_tag ?? '')
  return (
    <DossierDialog
      resident={resident}
      title="Vehicle & Building Access"
      description={`${resident.display_name}'s vehicle, parking and society access.`}
      payload={() => ({
        vehicle_type: vehicle || null,
        // Nobody registers a vehicle they do not have.
        vehicle_reg_no: vehicle === 'none' ? null : registration.trim().toUpperCase() || null,
        parking_slot: parking.trim() || null,
        society_rfid_tag: rfid.trim() || null,
      })}
      onClose={onClose}
      onSaved={onSaved}
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <SelectField<VehicleType | ''>
          label="Vehicle"
          value={vehicle}
          options={[{ value: '', label: 'Not recorded' }, ...VEHICLES]}
          onChange={setVehicle}
        />
        <TextField
          label="Registration number"
          value={vehicle === 'none' ? '' : registration}
          onChange={setRegistration}
          maxLength={50}
          placeholder="e.g. DL 3C AB 1234"
          disabled={vehicle === 'none'}
        />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <TextField
          label="Parking slot"
          value={parking}
          onChange={setParking}
          maxLength={100}
          placeholder="e.g. B-12 (Stilt)"
        />
        <TextField
          label="Society RFID tag"
          value={rfid}
          onChange={setRfid}
          maxLength={100}
          placeholder="e.g. #RF-8821"
        />
      </div>
    </DossierDialog>
  )
}

/* ------------------------------------------------------- KYC & compliance -- */

export function KycComplianceCard({ resident, onReload }: { resident: Resident; onReload: () => void }) {
  const [editing, setEditing] = useState(false)
  const saved = useSaved(() => setEditing(false), onReload, 'KYC details saved')
  const idType = labelOf(ID_DOCUMENTS, resident.id_document_type)
  const masked = maskedIdNumber(resident.id_document_type, resident.id_document_number)
  const hometown = [resident.home_city, resident.home_state].filter(Boolean).join(', ')
  const police = POLICE_STATUS[resident.police_verification_status]
  const noc = NOC_STATUS[resident.society_noc_status]
  return (
    <Card
      id="resident-kyc"
      title="KYC & Verification"
      hint="Identity and local verification, for police and society records."
      actions={<EditButton label="Edit KYC and verification" onClick={() => setEditing(true)} />}
    >
      <dl className="m-0 space-y-3">
        <DossierRow label="Government ID">
          {idType && masked ? (
            <>
              {idType} <span className="tabular">({masked})</span>
            </>
          ) : (
            idType ?? (masked && <span className="tabular">{masked}</span>)
          )}
        </DossierRow>
        <DossierRow label="Permanent hometown">{hometown}</DossierRow>
        <DossierRow label="Police verification">
          {police && <Pill tone={police.tone}>{police.label}</Pill>}
        </DossierRow>
        <DossierRow label="Society move-in NOC">
          {noc && <Pill tone={noc.tone}>{noc.label}</Pill>}
        </DossierRow>
      </dl>
      <Link to="/housing?tab=documents" className={BUTTON_QUIET}>
        View in Document Vault
        <ArrowRight className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
      </Link>
      {editing && <EditKycDialog resident={resident} onClose={() => setEditing(false)} onSaved={saved} />}
    </Card>
  )
}

export function EditKycDialog({
  resident,
  onClose,
  onSaved,
}: {
  resident: Resident
  onClose: () => void
  onSaved: () => void
}) {
  const onFile = maskedIdNumber(resident.id_document_type, resident.id_document_number)
  const [idType, setIdType] = useState<IdDocumentType | ''>(resident.id_document_type ?? '')
  // The number on file is never put back on screen: a new one replaces it.
  const [newNumber, setNewNumber] = useState('')
  const [removeNumber, setRemoveNumber] = useState(false)
  const [city, setCity] = useState(resident.home_city ?? '')
  const [state, setState] = useState(resident.home_state ?? '')
  const [police, setPolice] = useState<PoliceVerificationStatus>(resident.police_verification_status)
  const [noc, setNoc] = useState<SocietyNocStatus>(resident.society_noc_status)

  const typed = newNumber.trim()
  const numberError = !typed
    ? null
    : idType === 'aadhaar'
      ? AADHAAR_PATTERN.test(typed.replace(/\s/g, ''))
        ? null
        : 'An Aadhaar number is 12 digits.'
      : ID_NUMBER_PATTERN.test(typed)
        ? null
        : 'Use letters, digits, spaces or hyphens, 4 to 30 characters.'

  return (
    <DossierDialog
      resident={resident}
      title="KYC & Verification"
      description={`${resident.display_name}'s identity and local verification.`}
      payload={() => ({
        id_document_type: idType || null,
        ...(typed
          ? { id_document_number: typed }
          : removeNumber
            ? { id_document_number: null }
            : {}),
        home_city: city.trim() || null,
        home_state: state.trim() || null,
        police_verification_status: police,
        society_noc_status: noc,
      })}
      valid={!numberError}
      onClose={onClose}
      onSaved={onSaved}
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <SelectField<IdDocumentType | ''>
          label="Government ID"
          value={idType}
          options={[{ value: '', label: 'Not recorded' }, ...ID_DOCUMENTS]}
          onChange={setIdType}
        />
        <TextField
          label={onFile ? 'New ID number' : 'ID number'}
          value={newNumber}
          onChange={setNewNumber}
          maxLength={100}
          autoComplete="off"
          disabled={removeNumber}
          placeholder={onFile ? `${onFile} on file` : 'e.g. 1234 5678 9012'}
          hint={
            onFile
              ? 'Leave blank to keep the number on file. It is only ever shown by its last four digits.'
              : 'Only ever shown by its last four digits.'
          }
          error={numberError}
        />
      </div>
      {onFile && (
        <label className="flex items-center gap-2.5 cursor-pointer text-sm text-[var(--text-secondary)]">
          <input
            type="checkbox"
            className="w-auto"
            checked={removeNumber}
            onChange={(event) => {
              setRemoveNumber(event.target.checked)
              if (event.target.checked) setNewNumber('')
            }}
          />
          Remove the ID number on file
        </label>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <TextField label="Home city" value={city} onChange={setCity} maxLength={100} placeholder="e.g. Kolkata" />
        <TextField
          label="Home state"
          value={state}
          onChange={setState}
          maxLength={100}
          placeholder="e.g. West Bengal"
        />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <SelectField<PoliceVerificationStatus>
          label="Police verification"
          value={police}
          options={(Object.keys(POLICE_STATUS) as PoliceVerificationStatus[]).map((value) => ({
            value,
            label: POLICE_STATUS[value].label,
          }))}
          onChange={setPolice}
        />
        <SelectField<SocietyNocStatus>
          label="Society move-in NOC"
          value={noc}
          options={(Object.keys(NOC_STATUS) as SocietyNocStatus[]).map((value) => ({
            value,
            label: NOC_STATUS[value].label,
          }))}
          onChange={setNoc}
        />
      </div>
    </DossierDialog>
  )
}

/* ---------------------------------------------------------- status & notes -- */

export function StatusNotesCard({ resident }: { resident: Resident }) {
  return (
    <Card title="Status & Notes">
      <div className="stack stack--tight">
        <div className="row">
          {resident.is_active ? <Badge tone="ok">Living here</Badge> : <Badge tone="neutral">Moved out</Badge>}
          <span className="subtle">
            {resident.active_from ? `From ${formatDate(resident.active_from)}` : 'Move-in date not recorded'}
            {resident.active_until ? ` until ${formatDate(resident.active_until)}` : ''}
          </span>
        </div>
        {resident.notes && <p className="muted" style={{ margin: 0 }}>{resident.notes}</p>}
      </div>
    </Card>
  )
}
