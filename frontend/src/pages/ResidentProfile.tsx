/**
 * A resident's contact details: how to reach them, who to call in an
 * emergency, and a QR code that pays them by UPI.
 *
 * None of this is accounting. The QR code carries only who to pay -- no
 * amount -- so it never states a figure the ledger did not produce.
 */
import { useState } from 'react'
import { LifeBuoy, Mail, MessageCircle, Pencil, Phone } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { Card, ErrorNotice, Modal } from '../components/Primitives'
import { TextField } from '../components/Form'
import { BUTTON_PRIMARY, BUTTON_QUIET, BUTTON_SECONDARY, CopyButton } from '../components/ui'
import { api } from '../api/client'
import { useMutation } from '../lib/useApi'
import type { Resident } from '../api/types'

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

export function ContactCard({ resident, onEdit }: { resident: Resident; onEdit: () => void }) {
  const whatsapp = resident.phone ? whatsappHref(resident.phone) : null
  const hasEmergency = Boolean(
    resident.emergency_contact_name ||
      resident.emergency_contact_phone ||
      resident.emergency_contact_relation,
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
          <div className="flex items-center gap-2 text-xs font-bold text-[var(--text-primary)]">
            <LifeBuoy className="w-4 h-4 shrink-0 text-[var(--danger)]" aria-hidden="true" />
            Emergency contact
          </div>
          {hasEmergency ? (
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 min-w-0">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-[var(--text-primary)] truncate">
                  {resident.emergency_contact_name ?? 'Name not recorded'}
                  {resident.emergency_contact_relation && (
                    <span className="font-normal text-[var(--text-secondary)]">
                      {' '}
                      · {resident.emergency_contact_relation}
                    </span>
                  )}
                </div>
                <div className="tabular text-xs text-[var(--text-secondary)]">
                  {resident.emergency_contact_phone ?? 'Phone not recorded'}
                </div>
              </div>
              {resident.emergency_contact_phone && (
                <a
                  className={BUTTON_QUIET}
                  href={telHref(resident.emergency_contact_phone)}
                  aria-label={`Call emergency contact${
                    resident.emergency_contact_name ? ` ${resident.emergency_contact_name}` : ''
                  }`}
                >
                  <Phone className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Call
                </a>
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
          <TextField
            label="Emergency phone"
            type="tel"
            value={contactPhone}
            onChange={setContactPhone}
            maxLength={50}
          />
        </fieldset>
      </form>
    </Modal>
  )
}
