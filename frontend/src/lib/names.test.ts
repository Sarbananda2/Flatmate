import { describe, expect, it } from 'vitest'
import { nameParts, nameToEdit, splitFullName } from './names'

describe('splitFullName', () => {
  it.each([
    ['Rajesh Kumar Sharma', { first: 'Rajesh', middle: 'Kumar', last: 'Sharma' }],
    ['Kumar Yadav', { first: 'Kumar', middle: '', last: 'Yadav' }],
    ['Mohan', { first: 'Mohan', middle: '', last: '' }],
    ['  Venkata  Sai\tKrishna   Rao ', { first: 'Venkata', middle: 'Sai Krishna', last: 'Rao' }],
    ['', { first: '', middle: '', last: '' }],
  ])('splits %j as the backend does', (whole, parts) => {
    expect(splitFullName(whole)).toEqual(parts)
  })
})

describe('nameToEdit', () => {
  it('uses the stored parts, and splits the whole when there are none', () => {
    expect(nameToEdit({ first: 'Rajesh', middle: null, last: 'Sharma' }, 'ignored')).toEqual({
      first: 'Rajesh',
      middle: '',
      last: 'Sharma',
    })
    expect(nameToEdit({ first: null }, 'Kumar S. Yadav')).toEqual({ first: 'Kumar', middle: 'S.', last: 'Yadav' })
    expect(nameToEdit({}, undefined)).toEqual({ first: '', middle: '', last: '' })
  })
})

describe('nameParts', () => {
  it('trims each part and sends an empty middle or last name as null', () => {
    expect(nameParts({ first: ' Mohan ', middle: '  ', last: '' })).toEqual({ first: 'Mohan', middle: null, last: null })
  })
})
