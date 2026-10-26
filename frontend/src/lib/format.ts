/**
 * Formatting helpers.
 *
 * Money arrives as integer paise and is formatted here. `formatInr` never
 * divides into a float: it splits the integer into rupees and paise so a value
 * can never drift by a rounding error on its way to the screen.
 */

const PAISE_PER_RUPEE = 100

export function splitPaise(paise: number): { negative: boolean; rupees: number; paise: number } {
  const value = Math.trunc(paise)
  const negative = value < 0
  const magnitude = Math.abs(value)
  return {
    negative,
    rupees: Math.floor(magnitude / PAISE_PER_RUPEE),
    paise: magnitude % PAISE_PER_RUPEE,
  }
}

function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits
  const tail = digits.slice(-3)
  let head = digits.slice(0, -3)
  const groups: string[] = []
  while (head.length > 2) {
    groups.unshift(head.slice(-2))
    head = head.slice(0, -2)
  }
  if (head) groups.unshift(head)
  return [...groups, tail].join(',')
}

/** `123456789` -> `₹12,34,567.89`. Set `sign: 'always'` to force a leading +/-. */
export function formatInr(
  paise: number | null | undefined,
  options: { sign?: 'auto' | 'always' | 'none'; symbol?: boolean } = {},
): string {
  if (paise === null || paise === undefined || Number.isNaN(paise)) return '—'
  const { negative, rupees, paise: fraction } = splitPaise(paise)
  const sign =
    options.sign === 'none'
      ? ''
      : negative
        ? '−'
        : options.sign === 'always' && paise !== 0
          ? '+'
          : ''
  const symbol = options.symbol === false ? '' : '₹'
  return `${sign}${symbol}${groupIndian(String(rupees))}.${String(fraction).padStart(2, '0')}`
}

/** Plain decimal rupees for a form field: `123456` -> `1234.56`. */
export function paiseToInput(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return ''
  const { negative, rupees, paise: fraction } = splitPaise(paise)
  return `${negative ? '-' : ''}${rupees}.${String(fraction).padStart(2, '0')}`
}

export interface ParsedMoney {
  ok: boolean
  paise: number
  error?: string
}

/**
 * Parse an operator-typed rupee amount into integer paise.
 *
 * Rejects more than two decimal places rather than rounding, so the operator is
 * told about the extra precision instead of losing it silently.
 */
export function parseRupees(
  raw: string,
  options: { allowNegative?: boolean; allowEmpty?: boolean } = {},
): ParsedMoney {
  const text = raw.trim().replace(/[₹,\s]/g, '')
  if (!text) {
    return options.allowEmpty
      ? { ok: true, paise: 0 }
      : { ok: false, paise: 0, error: 'Enter an amount.' }
  }
  if (!/^-?\d*(\.\d{0,})?$/.test(text) || text === '-' || text === '.') {
    return { ok: false, paise: 0, error: 'Enter a number, for example 1250.75.' }
  }
  const negative = text.startsWith('-')
  if (negative && !options.allowNegative) {
    return { ok: false, paise: 0, error: 'The amount cannot be negative.' }
  }
  const [whole = '0', fraction = ''] = text.replace('-', '').split('.')
  if (fraction.length > 2) {
    return { ok: false, paise: 0, error: 'At most two decimal places (paise).' }
  }
  const rupees = Number(whole || '0')
  const paise = Number((fraction + '00').slice(0, 2))
  if (!Number.isFinite(rupees) || !Number.isSafeInteger(rupees)) {
    return { ok: false, paise: 0, error: 'That amount is too large.' }
  }
  const total = rupees * PAISE_PER_RUPEE + paise
  return { ok: true, paise: negative ? -total : total }
}

/** Parse a kWh reading into whole watt-hours (three decimal places). */
export function parseKwh(raw: string): { ok: boolean; wh: number; error?: string } {
  const text = raw.trim()
  if (!text) return { ok: false, wh: 0, error: 'Enter a kWh reading.' }
  if (!/^\d*(\.\d{0,})?$/.test(text) || text === '.') {
    return { ok: false, wh: 0, error: 'Enter a number, for example 42.125.' }
  }
  const [whole = '0', fraction = ''] = text.split('.')
  if (fraction.length > 3) {
    return { ok: false, wh: 0, error: 'At most three decimal places (whole watt-hours).' }
  }
  const wh = Number(whole || '0') * 1000 + Number((fraction + '000').slice(0, 3))
  if (!Number.isFinite(wh)) return { ok: false, wh: 0, error: 'That reading is too large.' }
  return { ok: true, wh }
}

export function whToKwh(wh: number | null | undefined): string {
  if (wh === null || wh === undefined) return '—'
  return (Math.trunc(wh) / 1000).toFixed(3)
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** `2026-09` -> `September 2026`. */
export function formatMonth(month: string | null | undefined): string {
  if (!month) return '—'
  const match = /^(\d{4})-(\d{2})$/.exec(month)
  if (!match) return month
  const index = Number(match[2]) - 1
  return `${MONTH_NAMES[index] ?? match[2]} ${match[1]}`
}

/** `2026-09` -> `Sep 2026`. */
export function formatMonthShort(month: string | null | undefined): string {
  const full = formatMonth(month)
  if (full === '—' || full === month) return full
  const [name, year] = full.split(' ')
  return `${name.slice(0, 3)} ${year}`
}

/** `2026-09-08` -> `8 Sep 2026`. */
export function formatDate(date: string | null | undefined): string {
  if (!date) return '—'
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date)
  if (!match) return date
  const monthIndex = Number(match[2]) - 1
  return `${Number(match[3])} ${(MONTH_NAMES[monthIndex] ?? match[2]).slice(0, 3)} ${match[1]}`
}

/** `2003-03-12` -> `12 March 2003`. Empty for no date, so it can sit in a field. */
export function formatDateFull(date: string | null | undefined): string {
  if (!date) return ''
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date)
  if (!match) return date
  const monthIndex = Number(match[2]) - 1
  return `${Number(match[3])} ${MONTH_NAMES[monthIndex] ?? match[2]} ${match[1]}`
}

/** An ISO timestamp as a readable local date and time. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return `${formatDate(value.slice(0, 10))}, ${parsed.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })}`
}

export function todayIso(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** `2027-02-28` plus one day -> `2027-03-01`. Calendar days, no time zone involved. */
export function addDaysIso(date: string, days: number): string {
  const [year, month, day] = date.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10)
}

/**
 * The last day of a lease term, as the backend computes it: the start date
 * plus the duration, less a day. A start on 1 March for 11 months ends on
 * 31 January. Returns null for a date or duration that is not usable.
 */
export function leaseEndIso(start: string, months: number): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(start)
  if (!match || !Number.isInteger(months) || months < 1) return null
  const index = Number(match[1]) * 12 + (Number(match[2]) - 1) + months
  const year = Math.floor(index / 12)
  const month = index % 12
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
  const sameDay = new Date(Date.UTC(year, month, Math.min(Number(match[3]), lastDay)))
  return addDaysIso(sameDay.toISOString().slice(0, 10), -1)
}

/**
 * Rent after a percentage escalation, in integer paise, rounded half up. The
 * percentage is taken to two decimal places so the arithmetic stays in
 * integers. A preview only: the backend computes the figure that is charged.
 */
export function escalatedPaise(paise: number, percent: number): number {
  const hundredths = Math.round(percent * 100)
  return Math.floor((paise * (10000 + hundredths) + 5000) / 10000)
}

export function currentMonth(): string {
  return todayIso().slice(0, 7)
}

export function shiftMonth(month: string, delta: number): string {
  const match = /^(\d{4})-(\d{2})$/.exec(month)
  if (!match) return month
  const index = Number(match[1]) * 12 + (Number(match[2]) - 1) + delta
  const year = Math.floor(index / 12)
  const monthNumber = (index % 12) + 1
  return `${String(year).padStart(4, '0')}-${String(monthNumber).padStart(2, '0')}`
}

/** Hours remaining, phrased for a person. */
export function formatHoursRemaining(hours: number): string {
  if (hours <= 0) return 'expired'
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} minutes left`
  if (hours < 48) return `${Math.round(hours)} hours left`
  // Days alone would turn 71 hours into "2 days"; the leftover hours are kept
  // so a deadline is never made to look further off or nearer than it is.
  const total = Math.round(hours)
  const days = Math.floor(total / 24)
  const rest = total % 24
  return rest === 0
    ? `${days} days left`
    : `${days} days ${rest} hour${rest === 1 ? '' : 's'} left`
}

export const PERIOD_RULE_LABELS: Record<string, string> = {
  previous_month: 'Previous month',
  current_month: 'Current month',
  transaction_date: 'Transaction date',
  custom: 'Custom period',
}

export const PERIOD_RULE_HELP: Record<string, string> = {
  previous_month:
    'The money was for the month before the transaction date, and is charged in the transaction month’s statement. Suits electricity and groceries.',
  current_month:
    'The money is for the transaction month and is charged in that month’s statement. Suits rent, broadband, maintenance, and furniture rental.',
  transaction_date:
    'Charged in the transaction month, with the date itself as the authoritative marker. Suits one-off purchases and repairs.',
  custom: 'You choose both the service period and the settlement month.',
}

export const ALLOCATION_METHOD_LABELS: Record<string, string> = {
  equal: 'Equal shares',
  weighted: 'Weighted shares',
  custom: 'Custom amounts',
  measured: 'Measured usage',
  host_guest: 'Host carries guest',
}

export const PAYMENT_KIND_LABELS: Record<string, string> = {
  to_pool: 'Into House Kitty',
  provider_advance: 'Paid a provider',
  refund: 'From House Kitty',
  adjustment: 'Legacy adjustment',
  kitty_payout: 'Kitty payout',
}

export const STATEMENT_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  finalized_correction_window: 'Finalized — correction window',
  closed: 'Closed',
  not_created: 'Not created',
}
