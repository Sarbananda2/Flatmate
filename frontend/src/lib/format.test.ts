/**
 * Money and period formatting.
 *
 * These are the only places integer paise meet operator-typed text, so they are
 * tested for the things that would quietly corrupt a balance: a float creeping
 * in, a third decimal place being rounded away, a negative sign being lost.
 */
import { describe, expect, it } from 'vitest'
import {
  addDaysIso,
  escalatedPaise,
  formatDate,
  formatDateFull,
  leaseEndIso,
  formatHoursRemaining,
  formatInr,
  formatMonth,
  formatMonthShort,
  paiseToInput,
  parseKwh,
  parseRupees,
  shiftMonth,
  splitPaise,
  whToKwh,
} from './format'

describe('formatInr', () => {
  it('renders paise with Indian digit grouping and two decimals', () => {
    expect(formatInr(0)).toBe('₹0.00')
    expect(formatInr(1)).toBe('₹0.01')
    expect(formatInr(100)).toBe('₹1.00')
    expect(formatInr(123456)).toBe('₹1,234.56')
    expect(formatInr(123456789)).toBe('₹12,34,567.89')
  })

  it('marks a negative amount with a minus sign, not only a colour', () => {
    expect(formatInr(-5000)).toBe('−₹50.00')
    expect(formatInr(-1)).toBe('−₹0.01')
  })

  it('can force a leading sign so a balance reads unambiguously', () => {
    expect(formatInr(5000, { sign: 'always' })).toBe('+₹50.00')
    expect(formatInr(-5000, { sign: 'always' })).toBe('−₹50.00')
    expect(formatInr(0, { sign: 'always' })).toBe('₹0.00')
  })

  it('renders a missing amount as an em dash rather than zero', () => {
    expect(formatInr(null)).toBe('—')
    expect(formatInr(undefined)).toBe('—')
  })

  it('never loses a paisa to floating point', () => {
    // 0.1 + 0.2 territory: these all round-trip exactly because the value is an integer.
    for (const paise of [1, 7, 33, 99, 101, 999999999]) {
      const { rupees, paise: fraction } = splitPaise(paise)
      expect(rupees * 100 + fraction).toBe(paise)
    }
  })
})

describe('parseRupees', () => {
  it('converts operator-typed rupees to integer paise', () => {
    expect(parseRupees('1250.75')).toEqual({ ok: true, paise: 125075 })
    expect(parseRupees('1250')).toEqual({ ok: true, paise: 125000 })
    expect(parseRupees('0.01')).toEqual({ ok: true, paise: 1 })
    expect(parseRupees('1250.7')).toEqual({ ok: true, paise: 125070 })
  })

  it('accepts the symbols and separators people actually type', () => {
    expect(parseRupees('₹1,234.56')).toEqual({ ok: true, paise: 123456 })
    expect(parseRupees('  45.50 ')).toEqual({ ok: true, paise: 4550 })
  })

  it('rejects a third decimal place instead of rounding it away', () => {
    const result = parseRupees('10.005')
    expect(result.ok).toBe(false)
    expect(result.error).toMatch(/two decimal places/)
  })

  it('rejects anything that is not a number', () => {
    expect(parseRupees('abc').ok).toBe(false)
    expect(parseRupees('1.2.3').ok).toBe(false)
    expect(parseRupees('-').ok).toBe(false)
  })

  it('rejects a negative amount unless one is allowed', () => {
    expect(parseRupees('-50').ok).toBe(false)
    expect(parseRupees('-50', { allowNegative: true })).toEqual({ ok: true, paise: -5000 })
  })

  it('treats an empty value as an error unless emptiness is allowed', () => {
    expect(parseRupees('').ok).toBe(false)
    expect(parseRupees('', { allowEmpty: true })).toEqual({ ok: true, paise: 0 })
  })

  it('round-trips through paiseToInput', () => {
    for (const paise of [0, 1, 99, 100, 123456, 100000000]) {
      expect(parseRupees(paiseToInput(paise))).toEqual({ ok: true, paise })
    }
  })
})

describe('parseKwh', () => {
  it('converts kWh to whole watt-hours at three decimal places', () => {
    expect(parseKwh('42.5')).toEqual({ ok: true, wh: 42500 })
    expect(parseKwh('42.125')).toEqual({ ok: true, wh: 42125 })
    expect(parseKwh('31')).toEqual({ ok: true, wh: 31000 })
    expect(parseKwh('0.001')).toEqual({ ok: true, wh: 1 })
  })

  it('rejects a fourth decimal place rather than rounding it', () => {
    const result = parseKwh('1.0001')
    expect(result.ok).toBe(false)
    expect(result.error).toMatch(/three decimal places/)
  })

  it('rejects a negative reading', () => {
    expect(parseKwh('-5').ok).toBe(false)
  })

  it('round-trips through whToKwh', () => {
    expect(whToKwh(42125)).toBe('42.125')
    expect(whToKwh(0)).toBe('0.000')
    expect(parseKwh(whToKwh(60000))).toEqual({ ok: true, wh: 60000 })
  })
})

describe('period helpers', () => {
  it('renders a month readably', () => {
    expect(formatMonth('2026-09')).toBe('September 2026')
    expect(formatMonthShort('2026-09')).toBe('Sep 2026')
    expect(formatMonth(null)).toBe('—')
  })

  it('renders a date readably', () => {
    expect(formatDate('2026-09-08')).toBe('8 Sep 2026')
    expect(formatDate('2026-12-31')).toBe('31 Dec 2026')
    expect(formatDate(null)).toBe('—')
  })

  it('shifts months across a year boundary', () => {
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2026-09', -1)).toBe('2026-08')
    expect(shiftMonth('2026-09', 0)).toBe('2026-09')
  })
})

describe('formatHoursRemaining', () => {
  it('never rounds a deadline down to fewer days than remain', () => {
    expect(formatHoursRemaining(71)).toBe('2 days 23 hours left')
    expect(formatHoursRemaining(71.9)).toBe('3 days left')
    expect(formatHoursRemaining(72)).toBe('3 days left')
    expect(formatHoursRemaining(49)).toBe('2 days 1 hour left')
  })

  it('keeps short windows in hours and minutes', () => {
    expect(formatHoursRemaining(47)).toBe('47 hours left')
    expect(formatHoursRemaining(0.5)).toBe('30 minutes left')
    expect(formatHoursRemaining(0)).toBe('expired')
  })
})

describe('formatDateFull', () => {
  it('spells the month out and leaves no date blank', () => {
    expect(formatDateFull('2003-03-12')).toBe('12 March 2003')
    expect(formatDateFull('2026-12-01')).toBe('1 December 2026')
    expect(formatDateFull('')).toBe('')
    expect(formatDateFull(null)).toBe('')
  })
})

describe('lease term dates', () => {
  it('ends a term the day before the same date, months later', () => {
    expect(leaseEndIso('2026-03-01', 11)).toBe('2027-01-31')
    expect(leaseEndIso('2026-04-01', 11)).toBe('2027-02-28')
    expect(leaseEndIso('2027-03-01', 12)).toBe('2028-02-29')
    // A start on the 31st is pulled back to the last day the end month has.
    expect(leaseEndIso('2026-03-31', 11)).toBe('2027-02-27')
    expect(leaseEndIso('', 11)).toBeNull()
    expect(leaseEndIso('2026-03-01', 0)).toBeNull()
  })

  it('adds days across month and year ends', () => {
    expect(addDaysIso('2027-02-28', 1)).toBe('2027-03-01')
    expect(addDaysIso('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDaysIso('2027-03-01', -1)).toBe('2027-02-28')
  })
})

describe('escalatedPaise', () => {
  it('stays in integer paise and rounds half up', () => {
    expect(escalatedPaise(2850000, 5)).toBe(2992500)
    expect(escalatedPaise(2850000, 7.5)).toBe(3063750)
    expect(escalatedPaise(2850000, 0)).toBe(2850000)
    expect(escalatedPaise(1000010, 2.5)).toBe(1025010)
    expect(escalatedPaise(1000030, 2.5)).toBe(1025031)
    expect(escalatedPaise(20, 2.5)).toBe(21)
    expect(Number.isInteger(escalatedPaise(3333333, 3.33))).toBe(true)
  })
})
