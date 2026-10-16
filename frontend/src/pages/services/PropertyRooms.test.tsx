/**
 * Rooms Inventory and Room Allotment.
 *
 * The backend enforces the occupancy rules; these tests pin what the screen
 * adds on top: each table's shape and centred headers, that the inventory only
 * defines rooms and the allotment only says who lives in them, the checks made
 * before anything is sent (a room number, no duplicates, a real move-in date
 * that is not in the future), who is offered as an occupant, and the exact
 * requests.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AssignedRoomCard, RoomAllotmentSection, RoomsInventory } from './PropertyRooms'
import { ToastProvider } from '../../components/Toast'
import { installFetchStub, type Recorded } from '../../test/server'
import { todayIso } from '../../lib/format'
import type { Room, RoomList } from '../../api/types'

const TODAY = todayIso()

function room(overrides: Partial<Room>): Room {
  return {
    room_id: 'room-x',
    apartment_id: 'home',
    room_number: 'Room X',
    has_attached_washroom: false,
    has_balcony: false,
    base_rent_paise: null,
    assigned_resident_id: null,
    assigned_display_name: null,
    assigned_since: null,
    status: 'vacant',
    linked_deposit_slot: null,
    created_at: '2026-10-01T10:00:00+05:30',
    updated_at: '2026-10-01T10:00:00+05:30',
    ...overrides,
  }
}

const ROOM_1 = room({
  room_id: 'room-1',
  room_number: 'Room 1',
  has_attached_washroom: true,
  has_balcony: true,
  assigned_resident_id: 'alice',
  assigned_display_name: 'Alice',
  assigned_since: '2026-08-01',
  status: 'occupied',
  base_rent_paise: 1800000,
  linked_deposit_slot: {
    slot_id: 'slot-1',
    slot_number: 1,
    slot_label: 'Room 1',
    target_deposit_paise: 5000000,
    funded_amount_paise: 5000000,
    status: 'occupied_funded',
  },
})
const ROOM_2 = room({ room_id: 'room-2', room_number: 'Room 2', has_attached_washroom: true })

const listing: RoomList = {
  items: [ROOM_1, ROOM_2],
  total: 2,
  summary: {
    total_rooms: 2,
    occupied_rooms: 1,
    vacant_rooms: 1,
    with_attached_washroom: 2,
    with_balcony: 1,
    rooms_with_base_rent: 1,
    total_base_rent_paise: 1800000,
    linked_slots: 1,
  },
}

const residents = {
  items: [
    { resident_id: 'alice', display_name: 'Alice', is_active: 1 },
    { resident_id: 'bob', display_name: 'Bob', is_active: 1 },
    { resident_id: 'chris', display_name: 'Chris', is_active: 1 },
  ],
  total: 3,
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function open(extra: Record<string, unknown> = {}, Section = RoomsInventory) {
  stub = installFetchStub({
    routes: {
      '/api/property/rooms': listing,
      '/api/residents': residents,
      ...extra,
    } as never,
  })
  render(
    <MemoryRouter>
      <ToastProvider>
        <Section />
      </ToastProvider>
    </MemoryRouter>,
  )
}

/** The Room Allotment tab, with the same rooms and residents. */
const openAllotment = (extra: Record<string, unknown> = {}) => open(extra, RoomAllotmentSection)

/** A read-only details dialog's facts, read back as "label: value". */
const factsIn = (dialog: HTMLElement) =>
  within(dialog)
    .getAllByRole('term')
    .map((term) => `${term.textContent}: ${term.nextElementSibling!.textContent}`)

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path === path)

describe('RoomsInventory', () => {
  it('defines the rooms only: number, amenities, base rent and actions', async () => {
    open()
    const table = await screen.findByRole('table', { name: 'Rooms' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Room',
      'Attached Washroom',
      'Balcony',
      'Base Rent',
      'Actions',
    ])
    for (const header of headers) expect(header).toHaveClass('text-center')

    const first = within(table).getByRole('row', { name: /Room 1/ })
    expect(within(first).getByRole('rowheader')).toHaveTextContent(/^Room 1$/)
    expect(within(first).getAllByText('Yes')).toHaveLength(2)
    expect(first).toHaveTextContent('₹18,000.00')
    // Occupied: it can be edited, but is vacated under Room Allotment before it can go.
    expect(within(first).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      'Edit Room 1',
    ])

    const second = within(table).getByRole('row', { name: /Room 2/ })
    expect(within(second).getByText('No')).toBeInTheDocument()
    expect(second).toHaveTextContent('—')
    expect(within(second).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      'Edit Room 2',
      'Remove Room 2',
    ])

    // Who lives where, and the deposit, are other tabs' business.
    for (const elsewhere of ['Alice', 'Vacant', 'Since', 'Slot', 'Assign', 'Vacate', 'Unlinked']) {
      expect(table).not.toHaveTextContent(elsewhere)
    }
    expect(screen.getByRole('heading', { name: 'Rooms Inventory' })).toBeInTheDocument()
    expect(screen.getByText('2 rooms defined')).toBeInTheDocument()
    // Neither residents nor the deposit are asked for.
    expect(stub!.calls.some((call) => call.path.startsWith('/api/residents'))).toBe(false)
    expect(stub!.calls.some((call) => call.path.startsWith('/api/rent/deposits'))).toBe(false)
  })

  it('adds a room after checking its number, sending only the room itself', async () => {
    open({ 'POST /api/property/rooms': room({ room_number: 'Room 3' }) })
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Add Room' }))
    const dialog = screen.getByRole('dialog')
    const submit = within(dialog).getByRole('button', { name: 'Add Room' })
    // Only what defines a room.
    expect(within(dialog).queryByLabelText(/Occupant/)).toBeNull()
    expect(within(dialog).queryByLabelText(/Deposit Slot/)).toBeNull()
    expect(within(dialog).queryByLabelText(/Moved in on/)).toBeNull()

    await userEvent.click(submit)
    expect(within(dialog).getByText('Enter the room number.')).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Room Number/)).toHaveAttribute('aria-invalid', 'true')

    await userEvent.type(within(dialog).getByLabelText(/Room Number/), ' room 2 ')
    expect(within(dialog).getByText('Room 2 already exists.')).toBeInTheDocument()
    await userEvent.click(submit)
    expect(sent('POST', '/api/property/rooms')).toBeUndefined()

    await userEvent.clear(within(dialog).getByLabelText(/Room Number/))
    await userEvent.type(within(dialog).getByLabelText(/Room Number/), ' Room 3 ')
    await userEvent.click(within(dialog).getByLabelText('Balcony'))
    await userEvent.type(within(dialog).getByLabelText(/Base Rent/), '12500.50')
    await userEvent.click(submit)
    await waitFor(() => expect(sent('POST', '/api/property/rooms')).toBeDefined())
    expect(sent('POST', '/api/property/rooms')!.body).toEqual({
      room_number: 'Room 3',
      has_attached_washroom: false,
      has_balcony: true,
      base_rent_paise: 1250050,
    })
    expect(await screen.findByText('Room 3 added')).toBeInTheDocument()
  })

  it('refuses a base rent that is not an amount', async () => {
    open()
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Add Room' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/Room Number/), 'Room 3')
    await userEvent.type(within(dialog).getByLabelText(/Base Rent/), '12.345')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add Room' }))
    expect(within(dialog).getByLabelText(/Base Rent/)).toHaveAttribute('aria-invalid', 'true')
    expect(sent('POST', '/api/property/rooms')).toBeUndefined()
  })

  it('edits a room, sending only its own fields and leaving its occupant alone', async () => {
    open({ 'PATCH /api/property/rooms/room-1': { ...ROOM_1, has_balcony: false } })
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Edit Room 1' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Room Number/)).toHaveValue('Room 1')
    expect(within(dialog).getByLabelText('Attached Washroom')).toBeChecked()
    expect(within(dialog).getByLabelText(/Base Rent/)).toHaveValue('18000.00')
    expect(within(dialog).queryByLabelText(/Occupant/)).toBeNull()
    await userEvent.click(within(dialog).getByLabelText('Balcony'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Room' }))
    await waitFor(() => expect(sent('PATCH', '/api/property/rooms/room-1')).toBeDefined())
    expect(sent('PATCH', '/api/property/rooms/room-1')!.body).toEqual({
      room_number: 'Room 1',
      has_attached_washroom: true,
      has_balcony: false,
      base_rent_paise: 1800000,
    })
  })

  it('shows what the backend refused', async () => {
    stub = installFetchStub({
      routes: { '/api/property/rooms': listing } as never,
      errors: {
        'DELETE /api/property/rooms/room-2': {
          status: 409,
          code: 'room_occupied',
          message: 'Room 2 is occupied by Bob. Vacate it before removing it.',
        },
      },
    })
    render(
      <MemoryRouter>
        <ToastProvider>
          <RoomsInventory />
        </ToastProvider>
      </MemoryRouter>,
    )
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Remove Room 2' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove Room' }))
    expect(
      await within(dialog).findByText(/Room 2 is occupied by Bob/),
    ).toBeInTheDocument()
  })
})

describe('RoomAllotmentSection', () => {
  it('lists who lives in each room under four centred headers', async () => {
    openAllotment()
    const table = await screen.findByRole('table', { name: 'Room allotment' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Room',
      'Resident',
      'Occupied Since',
      'Actions',
    ])
    for (const header of headers) expect(header).toHaveClass('text-center')
    expect(screen.getByText('1 of 2 rooms allotted · 1 vacant')).toBeInTheDocument()

    const first = within(table).getByRole('row', { name: /Room 1/ })
    const roomButton = within(within(first).getByRole('rowheader')).getByRole('button')
    expect(roomButton).toHaveAccessibleName('Room 1')
    expect(roomButton).toHaveAttribute('aria-haspopup', 'dialog')
    const [resident, since, actions] = within(first).getAllByRole('cell')
    expect(resident).toHaveTextContent(/Alice$/)
    expect(since).toHaveTextContent(/^1 Aug 2026$/)
    expect(within(actions).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Reassign',
      'Vacate',
    ])
    expect(within(actions).getByRole('button', { name: 'Reassign occupant of Room 1' })).toBeInTheDocument()

    const second = within(table).getByRole('row', { name: /Room 2/ })
    const cells = within(second).getAllByRole('cell')
    expect(within(cells[0]).getByText('Vacant')).toBeInTheDocument()
    expect(cells[1]).toHaveTextContent(/^—$/)
    expect(within(cells[2]).getAllByRole('button').map((button) => button.textContent)).toEqual(['Assign'])
    // The room's definition stays in Rooms Inventory.
    expect(table).not.toHaveTextContent('₹18,000.00')
    expect(within(table).queryByRole('button', { name: /Edit|Remove/ })).toBeNull()
  })

  it("opens a room's allotment details from its row, read-only", async () => {
    openAllotment()
    await screen.findByRole('table', { name: 'Room allotment' })
    // A click anywhere on the row opens it.
    await userEvent.click(within(screen.getByRole('row', { name: /Room 1/ })).getAllByRole('cell')[1])
    let dialog = screen.getByRole('dialog', { name: 'Room Allotment Details' })
    expect(within(dialog).getByText('Occupied')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Base rent ₹18,000.00')
    expect(factsIn(dialog)).toEqual([
      'Room: Room 1',
      'Private Amenities: Attached Washroom · Balcony',
      'Resident: Alice',
      'Occupied Since: 1 Aug 2026',
    ])
    expect(
      within(dialog)
        .getAllByRole('button')
        .map((button) => button.getAttribute('aria-label') ?? button.textContent),
    ).toEqual(['Close dialog', 'Close'])
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    // From the keyboard, a vacant room with no base rent.
    const roomTwo = screen.getByRole('button', { name: 'Room 2' })
    roomTwo.focus()
    await userEvent.keyboard('{Enter}')
    dialog = screen.getByRole('dialog', { name: 'Room Allotment Details' })
    // The status pill, and the Resident fact.
    expect(within(dialog).getAllByText('Vacant')).toHaveLength(2)
    expect(dialog).toHaveTextContent('Base rent not set')
    expect(factsIn(dialog)).toEqual([
      'Room: Room 2',
      'Private Amenities: Attached Washroom',
      'Resident: Vacant',
      'Occupied Since: —',
    ])
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(roomTwo).toHaveFocus()
  })

  it("opens a row's action straight away, not the room's details", async () => {
    openAllotment()
    await screen.findByRole('table', { name: 'Room allotment' })
    await userEvent.click(screen.getByRole('button', { name: 'Vacate Room 1' }))
    const [dialog, ...others] = screen.getAllByRole('dialog')
    expect(others).toHaveLength(0)
    expect(dialog).toHaveAccessibleName('Vacate Room')
  })

  it('offers only active residents who have no room', async () => {
    openAllotment()
    await screen.findByRole('table', { name: 'Room allotment' })
    await userEvent.click(screen.getByRole('button', { name: 'Assign occupant to Room 2' }))
    const select = within(screen.getByRole('dialog')).getByLabelText(/Resident/)
    const names = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent)
    expect(names).toEqual(['Choose a resident', 'Bob', 'Chris'])
  })

  it('names the dialog after the button: Reassign opens Reassign Occupant', async () => {
    openAllotment()
    await screen.findByRole('table', { name: 'Room allotment' })
    await userEvent.click(screen.getByRole('button', { name: 'Reassign occupant of Room 1' }))
    const dialog = screen.getByRole('dialog', { name: 'Reassign Occupant' })
    expect(dialog).toHaveTextContent('Alice occupies Room 1 now and will move out of it.')
  })

  it('assigns an occupant with a required, past-or-today move-in date', async () => {
    openAllotment({
      'POST /api/property/rooms/room-2/assign': {
        ...ROOM_2,
        assigned_resident_id: 'bob',
        assigned_display_name: 'Bob',
        assigned_since: TODAY,
        status: 'occupied',
      },
    })
    await screen.findByRole('table', { name: 'Room allotment' })
    await userEvent.click(screen.getByRole('button', { name: 'Assign occupant to Room 2' }))
    const dialog = screen.getByRole('dialog')
    const submit = within(dialog).getByRole('button', { name: 'Assign Occupant' })

    await userEvent.click(submit)
    expect(within(dialog).getByText('Choose who is moving in.')).toBeInTheDocument()
    expect(sent('POST', '/api/property/rooms/room-2/assign')).toBeUndefined()

    await userEvent.selectOptions(within(dialog).getByLabelText(/Resident/), 'bob')
    await userEvent.click(submit)
    await waitFor(() => expect(sent('POST', '/api/property/rooms/room-2/assign')).toBeDefined())
    expect(sent('POST', '/api/property/rooms/room-2/assign')!.body).toEqual({
      resident_id: 'bob',
      effective_date: TODAY,
    })
    expect(await screen.findByText('Bob moved into Room 2')).toBeInTheDocument()
  })

  it('refuses a move-in date in the future', async () => {
    openAllotment()
    await screen.findByRole('table', { name: 'Room allotment' })
    await userEvent.click(screen.getByRole('button', { name: 'Assign occupant to Room 2' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByLabelText(/Resident/), 'chris')
    const date = within(dialog).getByLabelText(/Effective date/)
    await userEvent.clear(date)
    await userEvent.type(date, '2999-01-01')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Assign Occupant' }))
    expect(within(dialog).getByText('The date cannot be in the future.')).toBeInTheDocument()
    expect(sent('POST', '/api/property/rooms/room-2/assign')).toBeUndefined()
  })

  it('vacates a room after confirmation', async () => {
    openAllotment({ 'POST /api/property/rooms/room-1/vacate': { ...ROOM_1, status: 'vacant' } })
    await screen.findByRole('table', { name: 'Room allotment' })
    await userEvent.click(screen.getByRole('button', { name: 'Vacate Room 1' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Alice will no longer occupy Room 1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Vacate Room' }))
    await waitFor(() => expect(sent('POST', '/api/property/rooms/room-1/vacate')).toBeDefined())
    expect(await screen.findByText('Room 1 is vacant')).toBeInTheDocument()
  })

  it('points to Rooms Inventory until there are rooms to allot', async () => {
    openAllotment({ '/api/property/rooms': { ...listing, items: [], total: 0 } })
    expect(await screen.findByText('No rooms defined yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Rooms Inventory' })).toHaveAttribute(
      'href',
      '/housing?tab=rooms',
    )
    expect(screen.queryByRole('table')).toBeNull()
  })
})

describe('AssignedRoomCard', () => {
  function show(residentId: string) {
    stub = installFetchStub({ routes: { '/api/property/rooms': listing } as never })
    render(
      <MemoryRouter>
        <AssignedRoomCard residentId={residentId} />
      </MemoryRouter>,
    )
  }

  it('shows the room, its amenities and since when', async () => {
    show('alice')
    expect(await screen.findByText('Occupying: Room 1')).toBeInTheDocument()
    expect(screen.getByText('Attached Washroom')).toBeInTheDocument()
    expect(screen.getByText('Private Balcony')).toBeInTheDocument()
    expect(screen.getByText('Assigned since 1 Aug 2026')).toBeInTheDocument()
  })

  it('says when the resident has no room', async () => {
    show('bob')
    expect(await screen.findByText(/No room assigned\. 1 room is vacant\./)).toBeInTheDocument()
  })

  it('links to Room Allotment, where rooms are given out', async () => {
    show('alice')
    expect(await screen.findByRole('link', { name: 'Manage Allotment' })).toHaveAttribute(
      'href',
      '/housing?tab=allotment',
    )
  })
})
