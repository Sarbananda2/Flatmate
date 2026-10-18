/**
 * Form controls.
 *
 * Every control gets a real `<label>` tied to its input, a described-by hint,
 * and `aria-invalid` plus a message when it is wrong -- so validation is
 * announced, not just coloured.
 *
 * `MoneyInput` is the one place operator rupee input becomes integer paise. It
 * reports the parse failure instead of rounding, so no precision is ever lost
 * on the way in.
 */
import { type ReactNode, useId } from 'react'
import { Calendar } from 'lucide-react'
import { clsx } from 'clsx'
import { formatDateFull, parseKwh, parseRupees } from '../lib/format'
import { NAME_PART_MAX_LENGTH, type PersonName } from '../lib/names'

export function Field({
  label,
  hint,
  error,
  required,
  children,
  htmlFor,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string | null
  required?: boolean
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode
  htmlFor?: string
}) {
  const generated = useId()
  const id = htmlFor ?? generated
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ')
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
        {required && (
          <span className="field-required" aria-hidden="true">
            *
          </span>
        )}
        {required && <span className="sr-only"> (required)</span>}
      </label>
      {children({ id, describedBy: describedBy || undefined, invalid: Boolean(error) })}
      {hint && (
        <span className="field-hint" id={hintId}>
          {hint}
        </span>
      )}
      {error && (
        <span className="field-error" id={errorId}>
          {error}
        </span>
      )}
    </div>
  )
}

export function TextField({
  label,
  value,
  onChange,
  hint,
  error,
  required,
  placeholder,
  type = 'text',
  maxLength,
  disabled,
  autoComplete,
  inputMode,
}: {
  label: ReactNode
  value: string
  onChange: (value: string) => void
  hint?: ReactNode
  error?: string | null
  required?: boolean
  placeholder?: string
  type?: 'text' | 'search' | 'email' | 'tel' | 'date' | 'month' | 'password'
  maxLength?: number
  disabled?: boolean
  autoComplete?: string
  inputMode?: 'numeric' | 'tel' | 'email'
}) {
  return (
    <Field label={label} hint={hint} error={error} required={required}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          type={type}
          value={value}
          placeholder={placeholder}
          maxLength={maxLength}
          disabled={disabled}
          autoComplete={autoComplete}
          inputMode={inputMode}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  )
}

/**
 * A person's name as three fields side by side -- a required first name, then
 * an optional middle and last name, since many names have no surname -- that
 * stack on a narrow screen. With a `legend` they are grouped under it, so each
 * is announced as part of whose name it is.
 */
export function PersonNameFields({
  value,
  onChange,
  firstNameError,
  legend,
  hint,
}: {
  value: PersonName
  onChange: (value: PersonName) => void
  firstNameError?: string | null
  /** Whose name it is, where the form is about more than this person. */
  legend?: string
  /** Shown under the three fields. */
  hint?: ReactNode
}) {
  const hintId = useId()
  const set = (part: keyof PersonName) => (next: string) => onChange({ ...value, [part]: next })
  const fields = (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-start">
      <TextField
        label="First Name"
        value={value.first}
        onChange={set('first')}
        required
        maxLength={NAME_PART_MAX_LENGTH}
        placeholder="e.g. Rajesh"
        error={firstNameError}
      />
      <TextField
        label="Middle Name"
        value={value.middle}
        onChange={set('middle')}
        maxLength={NAME_PART_MAX_LENGTH}
        placeholder="e.g. Kumar"
      />
      <TextField
        label="Last Name"
        value={value.last}
        onChange={set('last')}
        maxLength={NAME_PART_MAX_LENGTH}
        placeholder="e.g. Sharma"
      />
    </div>
  )
  if (!legend && !hint) return fields
  return (
    <fieldset className="m-0 p-0 border-0 min-w-0" aria-describedby={hint ? hintId : undefined}>
      {legend && <legend className="field-label p-0 mb-1.5">{legend}</legend>}
      {fields}
      {hint && (
        <p className="field-hint m-0 mt-1.5" id={hintId}>
          {hint}
        </p>
      )}
    </fieldset>
  )
}

/**
 * Whose account a landlord's payouts go to: their own, the default, or someone
 * else's -- a spouse, a parent, a family business -- named. An affirmative
 * choice rather than a box to leave blank, so the landlord's own name is never
 * typed in again as a beneficiary. The name box is always there: locked to the
 * landlord's name for their own account, open to edit for someone else's.
 */
export function PayoutBeneficiaryField({
  landlordName,
  custom,
  onCustomChange,
  value,
  onChange,
  error,
}: {
  /** The landlord's whole name, as it stands in the form. */
  landlordName: string
  /** Whether the payouts go to someone other than the landlord. */
  custom: boolean
  onCustomChange: (custom: boolean) => void
  /** That someone's name. */
  value: string
  onChange: (value: string) => void
  error?: string | null
}) {
  const labelId = useId()
  const choice = (selected: boolean) =>
    clsx(
      'px-3 py-1.5 text-xs font-semibold rounded-md text-left transition-colors cursor-pointer',
      selected
        ? 'bg-[var(--accent)] text-white shadow-xs'
        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
    )
  return (
    <div className="field">
      <span className="field-label" id={labelId}>
        Payout Beneficiary
      </span>
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        className="inline-flex flex-wrap justify-self-start max-w-full p-1 gap-1 rounded-lg border border-[var(--border-medium)] bg-[var(--bg-elevated)]"
      >
        <button type="button" role="radio" aria-checked={!custom} onClick={() => onCustomChange(false)} className={choice(!custom)}>
          Same as Landlord ({landlordName})
        </button>
        <button type="button" role="radio" aria-checked={custom} onClick={() => onCustomChange(true)} className={choice(custom)}>
          Custom Beneficiary
        </button>
      </div>
      <div className="mt-1.5">
        <TextField
          label="Beneficiary Name"
          value={custom ? value : landlordName}
          onChange={onChange}
          disabled={!custom}
          required={custom}
          maxLength={200}
          placeholder="e.g. Anita Sharma"
          hint={
            custom
              ? 'Whose bank account or UPI ID receives payouts (e.g., spouse, parent, family business).'
              : "🔒 Locked to the landlord's legal name. Switch to 'Custom Beneficiary' above to edit."
          }
          error={custom ? error : null}
        />
      </div>
    </div>
  )
}

/**
 * A date shown in full ("12 March 2003") instead of in the browser's own
 * numeric format.
 *
 * The native date input is still the real control: it sits invisibly over the
 * formatted text, so the label, keyboard entry, the value read out by a screen
 * reader and the browser's picker all work as they do for any date input.
 * Clicking anywhere on the field opens the picker.
 */
export function DateField({
  label,
  value,
  onChange,
  hint,
  error,
  required,
  placeholder = 'Select date',
  disabled,
  min,
  max,
}: {
  label: ReactNode
  value: string
  onChange: (value: string) => void
  hint?: ReactNode
  error?: string | null
  required?: boolean
  placeholder?: string
  disabled?: boolean
  min?: string
  max?: string
}) {
  const displayValue = formatDateFull(value)
  return (
    <Field label={label} hint={hint} error={error} required={required}>
      {({ id, describedBy, invalid }) => (
        <div
          className={clsx(
            'date-field relative flex items-center justify-between rounded-[10px] border px-[11px] py-2 text-sm transition-colors',
            invalid
              ? 'border-[var(--danger)] bg-[var(--danger-soft)]'
              : 'border-[var(--border-strong)] bg-[var(--surface)] hover:border-[var(--accent)]',
            displayValue ? 'text-[var(--text-primary)] font-medium' : 'text-[var(--text-tertiary)]',
            disabled && 'opacity-60',
          )}
        >
          <span className="truncate" aria-hidden="true">
            {displayValue || placeholder}
          </span>
          <Calendar
            className="w-4 h-4 shrink-0 text-[var(--text-secondary)] ml-2"
            aria-hidden="true"
          />
          <input
            id={id}
            type="date"
            value={value}
            min={min}
            max={max}
            disabled={disabled}
            required={required}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            className={clsx(
              'absolute inset-0 h-full w-full opacity-0',
              disabled ? 'cursor-not-allowed' : 'cursor-pointer',
            )}
            onChange={(event) => onChange(event.target.value)}
            onClick={(event) => {
              // Browsers only open the picker from the small calendar glyph;
              // open it from anywhere on the field. Where that is refused or
              // unsupported, the input still has focus and takes typed dates.
              try {
                event.currentTarget.showPicker?.()
              } catch {
                // Nothing to do: the focused input is the fallback.
              }
            }}
          />
        </div>
      )}
    </Field>
  )
}

export function TextAreaField({
  label,
  value,
  onChange,
  hint,
  error,
  required,
  placeholder,
  rows = 3,
}: {
  label: ReactNode
  value: string
  onChange: (value: string) => void
  hint?: ReactNode
  error?: string | null
  required?: boolean
  placeholder?: string
  rows?: number
}) {
  return (
    <Field label={label} hint={hint} error={error} required={required}>
      {({ id, describedBy, invalid }) => (
        <textarea
          id={id}
          value={value}
          rows={rows}
          placeholder={placeholder}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  )
}

export function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  hint,
  error,
  required,
  disabled,
  placeholder,
}: {
  label: ReactNode
  value: T | ''
  options: Array<{ value: T; label: string; disabled?: boolean }>
  onChange: (value: T) => void
  hint?: ReactNode
  error?: string | null
  required?: boolean
  disabled?: boolean
  placeholder?: string
}) {
  return (
    <Field label={label} hint={hint} error={error} required={required}>
      {({ id, describedBy, invalid }) => (
        <select
          id={id}
          value={value}
          disabled={disabled}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          onChange={(event) => onChange(event.target.value as T)}
        >
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
}

/**
 * Rupee input bound to an integer-paise value.
 *
 * The raw text is held by the caller so a half-typed `12.` is not destroyed;
 * `onChange` reports the parsed paise and whether it parsed, so the submit
 * button can stay disabled until the amount is valid.
 */
export function MoneyInput({
  label,
  value,
  onChange,
  hint,
  error,
  required,
  allowNegative,
  disabled,
  placeholder = '0.00',
  prefix = '₹',
}: {
  label: ReactNode
  value: string
  onChange: (raw: string, parsed: { ok: boolean; paise: number; error?: string }) => void
  hint?: ReactNode
  error?: string | null
  required?: boolean
  allowNegative?: boolean
  disabled?: boolean
  placeholder?: string
  /** The adornment shown before the input. Pass an empty string for none. */
  prefix?: string
}) {
  const parsed = parseRupees(value, { allowNegative, allowEmpty: !required })
  const shown = error ?? (value.trim() && !parsed.ok ? parsed.error ?? null : null)
  return (
    <Field
      label={label}
      hint={hint ?? 'Rupees, up to two decimal places. Stored as integer paise.'}
      error={shown}
      required={required}
    >
      {({ id, describedBy, invalid }) => {
        const input = (
            <input
              id={id}
              type="text"
              inputMode="decimal"
              className="amount-input"
              value={value}
              placeholder={placeholder}
              disabled={disabled}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
              required={required}
              onChange={(event) =>
                onChange(
                  event.target.value,
                  parseRupees(event.target.value, { allowNegative, allowEmpty: !required }),
                )
              }
            />
        )
        return prefix ? (
          <div className="input-addon">
            <span className="input-addon-prefix" aria-hidden="true">
              {prefix}
            </span>
            {input}
          </div>
        ) : (
          input
        )
      }}
    </Field>
  )
}

/** kWh input bound to whole watt-hours. Three decimal places, never rounded. */
export function KwhInput({
  label,
  value,
  onChange,
  hint,
  error,
  disabled,
}: {
  label: ReactNode
  value: string
  onChange: (raw: string, parsed: { ok: boolean; wh: number; error?: string }) => void
  hint?: ReactNode
  error?: string | null
  disabled?: boolean
}) {
  const parsed = parseKwh(value)
  const shown = error ?? (value.trim() && !parsed.ok ? parsed.error ?? null : null)
  return (
    <Field label={label} hint={hint} error={shown}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          type="text"
          inputMode="decimal"
          className="amount-input"
          value={value}
          placeholder="0.000"
          disabled={disabled}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          onChange={(event) => onChange(event.target.value, parseKwh(event.target.value))}
        />
      )}
    </Field>
  )
}

export function CheckboxField({
  label,
  checked,
  onChange,
  hint,
  disabled,
}: {
  label: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  hint?: ReactNode
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="field">
      <label className="check" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          aria-describedby={hint ? `${id}-hint` : undefined}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>{label}</span>
      </label>
      {hint && (
        <span className="field-hint" id={`${id}-hint`}>
          {hint}
        </span>
      )}
    </div>
  )
}

export function MonthPicker({
  label,
  value,
  onChange,
  months,
  hint,
}: {
  label: ReactNode
  value: string
  onChange: (value: string) => void
  months: string[]
  hint?: ReactNode
}) {
  const id = useId()
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="month"
        value={value}
        list={`${id}-known`}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      <datalist id={`${id}-known`}>
        {months.map((month) => (
          <option key={month} value={month} />
        ))}
      </datalist>
      {hint && (
        <span className="field-hint" id={`${id}-hint`}>
          {hint}
        </span>
      )}
    </div>
  )
}
