/**
 * A service bill's GST and total, for a month still open: the rate the bill
 * adds on its subtotal and, where the provider's invoice rounds its GST
 * another way, the total it actually charges. The total follows the rate until
 * it is set by hand. Any bill priced before GST can use it; the caller saves.
 */
import { useState, type FormEvent } from 'react'
import { MoneyInput, TextField } from './Form'
import { ErrorNotice, Modal } from './Primitives'
import { BUTTON_PRIMARY, BUTTON_QUIET, BUTTON_SECONDARY, DetailFact } from './ui'
import { useMutation } from '../lib/useApi'
import { formatInr, formatMonth, paiseToInput } from '../lib/format'

/** GST on a bill, unless it or its service has a rate of its own. */
export const DEFAULT_GST_RATE = 18

/** The slider's range: India's GST slabs run up to 28%; a rate above it is typed. */
export const GST_SLIDER_MAX = 28

/**
 * GST at `ratePercent` on rents quoted before GST, in whole paise, rounded half
 * up -- as the backend works it out. The rate is taken in hundredths of a
 * percent so the sum stays in whole numbers.
 */
export function gstOn(paise: number, ratePercent: number): number {
  return Math.floor((paise * Math.round(ratePercent * 100) + 5000) / 10000)
}

/** "18", "12.5": a GST rate as it is written. */
export function rateText(ratePercent: number): string {
  return String(Number(ratePercent.toFixed(2)))
}

/** A GST rate as typed: from 0 to 100 percent, to two decimal places at most. */
export function parseGstRate(raw: string): { ok: boolean; percent: number; error?: string } {
  const text = raw.trim()
  const percent = Number(text)
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(text) || percent > 100) {
    return { ok: false, percent: 0, error: 'Enter a rate from 0 to 100, to two decimal places at most.' }
  }
  return { ok: true, percent }
}

export interface BillGstModalProps {
  title?: string
  description?: string
  month: string
  /** What the bill comes to before GST. */
  subtotalPaise: number
  /** The bill's rate; null or left out for the service's default. */
  gstRatePercent?: number | null
  /** The service's rate when the bill has none: 18 unless given; 0 for one exempt from GST, like rent. */
  defaultGstRatePercent?: number
  /** The invoice's own total, if one was set. */
  chargedTotalOverridePaise?: number | null
  /** True once the bill is recorded: only then can its total be set to the invoice's. */
  isBilled: boolean
  onSave: (values: { gst_rate_percent: number; charged_total_paise: number | null }) => Promise<unknown>
  onClose: () => void
}

export function BillGstModal({
  title = 'Configure GST & Total',
  description,
  month,
  subtotalPaise,
  gstRatePercent,
  defaultGstRatePercent = DEFAULT_GST_RATE,
  chargedTotalOverridePaise,
  isBilled,
  onSave,
  onClose,
}: BillGstModalProps) {
  const subtotal = subtotalPaise
  const [rateRaw, setRateRaw] = useState(rateText(gstRatePercent ?? defaultGstRatePercent))
  const rate = parseGstRate(rateRaw)
  const calculatedGst = rate.ok ? gstOn(subtotal, rate.percent) : 0
  const calculated = subtotal + calculatedGst
  const [overridden, setOverridden] = useState(chargedTotalOverridePaise != null)
  const startingTotal = chargedTotalOverridePaise ?? calculated
  const [charged, setCharged] = useState(paiseToInput(startingTotal))
  const [chargedPaise, setChargedPaise] = useState(startingTotal)
  const [chargedOk, setChargedOk] = useState(true)
  const follow = (total: number) => {
    setCharged(paiseToInput(total))
    setChargedPaise(total)
    setChargedOk(true)
  }
  const changeRate = (raw: string) => {
    setRateRaw(raw)
    const next = parseGstRate(raw)
    if (!overridden && next.ok) follow(subtotal + gstOn(subtotal, next.percent))
  }
  const chargedError =
    !isBilled || !overridden || !chargedOk
      ? null
      : chargedPaise < subtotal
        ? `Less than the items' ${formatInr(subtotal)} before GST.`
        : chargedPaise > 2 * subtotal
          ? `More GST than the items' ${formatInr(subtotal)} before it.`
          : null
  // A month not billed yet takes only the rate: there is no invoice to match.
  const valid = rate.ok && (!isBilled || !overridden || (chargedOk && chargedError === null))
  const save = useMutation(() =>
    onSave({
      gst_rate_percent: rate.percent,
      charged_total_paise: isBilled && overridden && chargedPaise !== calculated ? chargedPaise : null,
    }),
  )
  // At 0% the GST is nothing and the total is the subtotal.
  const sliderValue = rate.ok ? Math.min(Math.round(rate.percent), GST_SLIDER_MAX) : 0
  const formId = `bill-gst-${month}`

  return (
    <Modal
      title={title}
      description={description ?? `${formatMonth(month)} bill: GST on its subtotal, and the total the invoice charges.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form={formId} className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
            {save.pending ? 'Saving…' : 'Save'}
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
        <div className="grid grid-cols-1 sm:grid-cols-[1fr_7rem] gap-4 items-start">
          <div className="space-y-1 pt-1">
            <input
              type="range"
              min={0}
              max={GST_SLIDER_MAX}
              step={1}
              value={sliderValue}
              onChange={(event) => changeRate(event.target.value)}
              aria-label="GST rate"
              aria-valuetext={`${sliderValue}%`}
              className="w-full accent-[var(--accent)]"
            />
            <div className="flex justify-between text-[10px] font-medium text-[var(--text-tertiary)] tabular" aria-hidden="true">
              <span>0%</span>
              <span>{GST_SLIDER_MAX}%</span>
            </div>
          </div>
          <TextField
            label="GST (%)"
            value={rateRaw}
            onChange={changeRate}
            inputMode="decimal"
            maxLength={6}
            error={rateRaw.trim() && !rate.ok ? rate.error : null}
          />
        </div>
        <dl className="m-0 grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm" aria-label="Calculated bill">
          <DetailFact label="Items Subtotal">
            <span className="tabular">{formatInr(subtotal)}</span>
          </DetailFact>
          <DetailFact label={rate.ok ? `Calculated GST (${rateText(rate.percent)}%)` : 'Calculated GST'}>
            <span className="tabular">{rate.ok ? formatInr(calculatedGst) : '—'}</span>
          </DetailFact>
          <DetailFact label="Calculated Total">
            <span className="tabular font-semibold">{rate.ok ? formatInr(calculated) : '—'}</span>
          </DetailFact>
        </dl>
        {isBilled ? (
          <div className="stack">
            <MoneyInput
              label="Actual Charged Total"
              required
              value={charged}
              onChange={(raw, parsed) => {
                setCharged(raw)
                setChargedPaise(parsed.paise)
                setChargedOk(parsed.ok)
                setOverridden(true)
              }}
              error={chargedError}
              hint={
                overridden
                  ? 'As the invoice charges it, GST included. Shares follow it.'
                  : 'Follows the rate. Change it to match the invoice to the paisa.'
              }
            />
            {overridden && (
              <button
                type="button"
                className={`${BUTTON_QUIET} self-start`}
                onClick={() => {
                  setOverridden(false)
                  if (rate.ok) follow(calculated)
                }}
              >
                Reset to calculated
              </button>
            )}
          </div>
        ) : (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            The total can be matched to the invoice once the month is billed.
          </p>
        )}
      </form>
    </Modal>
  )
}
