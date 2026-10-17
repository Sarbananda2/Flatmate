/**
 * People's names in parts, as the backend keeps them: a first name, an
 * optional middle name and an optional last name -- optional because many
 * names have no surname. The whole name is composed by the backend; screens
 * that only show a name read that whole.
 */

/** A name's parts as a form holds them, each possibly empty. */
export interface PersonName {
  first: string
  middle: string
  last: string
}

export const EMPTY_NAME: PersonName = { first: '', middle: '', last: '' }

/** The longest one part may be, as the backend allows. */
export const NAME_PART_MAX_LENGTH = 100

/**
 * A whole name split as the backend splits one: the first word, the last word,
 * and the words between. One word is a first name alone.
 */
export function splitFullName(name: string | null | undefined): PersonName {
  const words = (name ?? '').split(/\s+/).filter(Boolean)
  if (words.length === 0) return EMPTY_NAME
  if (words.length === 1) return { first: words[0], middle: '', last: '' }
  return { first: words[0], middle: words.slice(1, -1).join(' '), last: words[words.length - 1] }
}

/** The whole name: the parts that are there, joined by single spaces. */
export function composeFullName(name: PersonName): string {
  return [name.first, name.middle, name.last].map((part) => part.trim()).filter(Boolean).join(' ')
}

/**
 * A record's name in parts, to edit: its stored parts, or its whole name split
 * when it has none.
 */
export function nameToEdit(
  parts: { first?: string | null; middle?: string | null; last?: string | null },
  whole: string | null | undefined,
): PersonName {
  if (!parts.first) return splitFullName(whole)
  return { first: parts.first, middle: parts.middle ?? '', last: parts.last ?? '' }
}

/** The parts to send: trimmed, with an empty middle or last name as null. */
export function nameParts(name: PersonName) {
  return {
    first: name.first.trim(),
    middle: name.middle.trim() || null,
    last: name.last.trim() || null,
  }
}
