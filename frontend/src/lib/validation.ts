/**
 * The formats of Indian payment details, as the backend checks them.
 *
 * A form checks them first so the operator is told before anything is sent;
 * the server and the database refuse the same things.
 */

export const IFSC_PATTERN = /^[A-Z]{4}0[A-Z0-9]{6}$/
export const ACCOUNT_PATTERN = /^[0-9]{9,18}$/
export const UPI_PATTERN = /^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$/
/** A PIN code: six digits, the first never 0. */
export const PINCODE_PATTERN = /^[1-9][0-9]{5}$/
/** A payout beneficiary: a person or a business, and nothing that reads as code. */
export const BENEFICIARY_PATTERN = /^(?!.*--)[A-Za-z0-9 .,'&-]{1,200}$/

/** The message when a filled-in value does not match; an empty value is fine. */
export function patternError(value: string, pattern: RegExp, message: string): string | null {
  const text = value.trim()
  return text && !pattern.test(text) ? message : null
}

export const ACCOUNT_MESSAGE = 'A bank account number is 9 to 18 digits.'
export const IFSC_MESSAGE = 'An IFSC looks like HDFC0001234.'
export const UPI_MESSAGE = 'A UPI ID looks like name@bank.'
export const PINCODE_MESSAGE = 'A PIN code is 6 digits and does not start with 0.'
export const BENEFICIARY_MESSAGE = 'Beneficiary name contains invalid characters.'
