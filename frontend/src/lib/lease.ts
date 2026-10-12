/**
 * Lease term choices shared by the lease form and the renewal dialog.
 */

/** The agreement lengths offered, in months. Eleven is the usual one. */
export const LEASE_DURATION_MONTHS = [6, 11, 12, 24, 36]

/**
 * Options for a duration select. A lease saved with some other length keeps it
 * as a choice, so opening the form never silently changes the term.
 */
export function leaseDurationOptions(current: number): Array<{ value: string; label: string }> {
  return [...new Set([...LEASE_DURATION_MONTHS, current])]
    .filter((months) => Number.isInteger(months) && months > 0)
    .sort((a, b) => a - b)
    .map((months) => ({ value: String(months), label: `${months} months` }))
}
