/**
 * Property & Rooms.
 *
 * The backend enforces the occupancy rules; these tests pin what the screen
 * adds on top: the table's shape and centred headers, the checks made before
 * anything is sent (a room number, no duplicates, a real move-in date that is
 * not in the future), who is offered as an occupant, and the exact requests.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AssignedRoomCard, RoomsInventory } from './PropertyRooms'
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
    assigned_resident_id: null,
    assigned_display_name: null,
    assigned_since: null,
    status: 'vacant',
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

function open(extra: Record<string, unknown> = {}) {
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
        <RoomsInventory />
      </ToastProvider>
    </MemoryRouter>,
  )
}

const sent = (method: string, path: string) =>
  stub!.calls.find((call) => call.method === method && call.path === path)

describe('RoomsInventory', () => {
  it('lists the rooms with centred headers, amenities and occupants', async () => {
    open()
    const table = await screen.findByRole('table', { name: 'Rooms' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Room Number',
      'Attached Washroom',
      'Balcony',
      'Current Occupant',
      'Occupied Since',
      'Actions',
    ])
    for (const header of headers) expect(header).toHaveClass('text-center')

    const first = within(table).getByRole('row', { name: /Room 1/ })
    expect(within(first).getAllByText('Yes')).toHaveLength(2)
    expect(within(first).getByRole('link', { name: /Alice/ })).toHaveAttribute(
      'href',
      '/residents/alice',
    )
    expect(first).toHaveTextContent('1 Aug 2026')
    expect(within(first).getByRole('button', { name: 'Vacate Room 1' })).toBeInTheDocument()

    const second = within(table).getByRole('row', { name: /Room 2/ })
    expect(within(second).getByText('Vacant')).toBeInTheDocument()
    expect(within(second).getByText('No')).toBeInTheDocument()
    expect(second).toHaveTextContent('—')
    expect(within(second).getByRole('button', { name: 'Remove Room 2' })).toBeInTheDocument()
    expect(screen.getByText('2 rooms · 1 occupied · 1 vacant')).toBeInTheDocument()
  })

  it('adds a room after checking its number', async () => {
    open({ 'POST /api/property/rooms': room({ room_number: 'Room 3' }) })
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Add Room' }))
    const dialog = screen.getByRole('dialog')
    const submit = within(dialog).getByRole('button', { name: 'Add Room' })

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
    await userEvent.click(submit)
    await waitFor(() => expect(sent('POST', '/api/property/rooms')).toBeDefined())
    expect(sent('POST', '/api/property/rooms')!.body).toEqual({
      room_number: 'Room 3',
      has_attached_washroom: false,
      has_balcony: true,
      assigned_resident_id: null,
      effective_date: null,
    })
    expect(await screen.findByText('Room 3 added')).toBeInTheDocument()
  })

  it('offers only active residents who have no room', async () => {
    open()
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Assign occupant to Room 2' }))
    const select = within(screen.getByRole('dialog')).getByLabelText(/Resident/)
    const names = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent)
    expect(names).toEqual(['Choose a resident', 'Bob', 'Chris'])
  })

  it('assigns an occupant with a required, past-or-today move-in date', async () => {
    open({
      'POST /api/property/rooms/room-2/assign': {
        ...ROOM_2,
        assigned_resident_id: 'bob',
        assigned_display_name: 'Bob',
        assigned_since: TODAY,
        status: 'occupied',
      },
    })
    await screen.findByRole('table', { name: 'Rooms' })
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
    open()
    await screen.findByRole('table', { name: 'Rooms' })
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

  it('edits a room and sends only an occupant change it was given', async () => {
    open({ 'PATCH /api/property/rooms/room-1': { ...ROOM_1, has_balcony: false } })
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Edit Room 1' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Room Number/)).toHaveValue('Room 1')
    expect(within(dialog).getByLabelText('Attached Washroom')).toBeChecked()
    expect(within(dialog).getByLabelText(/Occupant/)).toHaveValue('alice')
    await userEvent.click(within(dialog).getByLabelText('Balcony'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save Room' }))
    await waitFor(() => expect(sent('PATCH', '/api/property/rooms/room-1')).toBeDefined())
    expect(sent('PATCH', '/api/property/rooms/room-1')!.body).toEqual({
      room_number: 'Room 1',
      has_attached_washroom: true,
      has_balcony: false,
    })
  })

  it('vacates a room after confirmation', async () => {
    open({ 'POST /api/property/rooms/room-1/vacate': { ...ROOM_1, status: 'vacant' } })
    await screen.findByRole('table', { name: 'Rooms' })
    await userEvent.click(screen.getByRole('button', { name: 'Vacate Room 1' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Alice will no longer occupy Room 1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Vacate Room' }))
    await waitFor(() => expect(sent('POST', '/api/property/rooms/room-1/vacate')).toBeDefined())
    expect(await screen.findByText('Room 1 is vacant')).toBeInTheDocument()
  })

  it('shows what the backend refused', async () => {
    stub = installFetchStub({
      routes: { '/api/property/rooms': listing, '/api/residents': residents } as never,
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
})
