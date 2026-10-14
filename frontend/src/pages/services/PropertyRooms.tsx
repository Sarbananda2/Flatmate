/**
 * The Rooms tab of Household & Property: the physical inventory of the flat.
 *
 * Each room, whether it has its own washroom or a balcony, and who lives in it
 * now. A room holds at most one resident and a resident holds at most one room;
 * the backend enforces both, and these forms check them first so the operator
 * is told before anything is sent. Moving out through the clearance wizard
 * frees the leaver's room on its own.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BedDouble, DoorOpen, Pencil, Plus, Trash2, UserPlus } from 'lucide-react'
import { AsyncSection, ErrorNotice, Modal } from '../../components/Primitives'
import { CheckboxField, DateField, SelectField, TextField } from '../../components/Form'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  EmptyBlock,
  Panel,
  Pill,
} from '../../components/ui'
import { useToast } from '../../components/Toast'
import { api } from '../../api/client'
import { type Resource, useMutation, useResource } from '../../lib/useApi'
import { formatDate, todayIso } from '../../lib/format'
import type { Resident, Room, RoomList } from '../../api/types'

export const ROOMS = '/api/property/rooms'
const EARLIEST_DATE = '2000-01-01'
const MAX_ROOM_NUMBER = 50

const TH = 'pb-3 px-2 font-bold text-center'
const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold'

export function useRooms(): Resource<RoomList> {
  return useResource<RoomList>(ROOMS)
}

function dateError(value: string): string | null {
  if (!value) return 'Enter the date they moved in.'
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Enter a valid date.'
  if (value > todayIso()) return 'The date cannot be in the future.'
  if (value < EARLIEST_DATE) return 'That date is too far in the past.'
  return null
}

/** "Yes" in green, "No" in a quiet grey. */
function YesNo({ value }: { value: boolean }) {
  return value ? <Pill tone="success">Yes</Pill> : <Pill>No</Pill>
}

/** The amenity pills for a room, or a plain note when it has none. */
export function RoomAmenities({ room }: { room: Room }) {
  if (!room.has_attached_washroom && !room.has_balcony) {
    return <span className="text-xs text-[var(--text-tertiary)]">No private amenities</span>
  }
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {room.has_attached_washroom && <Pill tone="accent">Attached Washroom</Pill>}
      {room.has_balcony && <Pill tone="accent">Private Balcony</Pill>}
    </span>
  )
}

/* --------------------------------------------------------------- the page -- */

type Dialog =
  | { kind: 'add' }
  | { kind: 'edit'; room: Room }
  | { kind: 'assign'; room: Room }
  | { kind: 'vacate'; room: Room }
  | { kind: 'delete'; room: Room }

/** The Rooms tab of the Household & Property hub. */
export function RoomsInventory() {
  const rooms = useRooms()
  const residents = useResource<{ items: Resident[] }>('/api/residents', { active_only: true })
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const toast = useToast()

  const done = (message: string) => {
    setDialog(null)
    rooms.reload()
    toast.success(message)
  }

  return (
    <>
      <Panel
        title="Rooms Directory"
        icon={<BedDouble className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint={
          rooms.data
            ? `${rooms.data.summary.total_rooms} rooms · ${rooms.data.summary.occupied_rooms} occupied · ${rooms.data.summary.vacant_rooms} vacant`
            : undefined
        }
     
        actions={
          <button type="button" className={BUTTON_PRIMARY} onClick={() => setDialog({ kind: 'add' })}>
            <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Add Room
          </button>
        }
      >
        <AsyncSection
          resource={rooms}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyBlock
              title="No rooms yet"
              body="Add each room in the flat, with its washroom and balcony, and who lives in it."
              action={
                <button
                  type="button"
                  className={BUTTON_PRIMARY}
                  onClick={() => setDialog({ kind: 'add' })}
                >
                  Add the first room
                </button>
              }
            />
          }
        >
          {(data) => (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-xs min-w-[760px] border-collapse">
                <caption className="sr-only">Rooms</caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className={TH}>Room Number</th>
                    <th scope="col" className={TH}>Attached Washroom</th>
                    <th scope="col" className={TH}>Balcony</th>
                    <th scope="col" className={TH}>Current Occupant</th>
                    <th scope="col" className={TH}>Occupied Since</th>
                    <th scope="col" className={TH}>Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {data.items.map((room) => (
                    <tr key={room.room_id} className="hover:bg-[var(--bg-elevated)] transition-colors">
                      <th scope="row" className="py-3 px-2 text-left font-bold whitespace-nowrap">
                        {room.room_number}
                      </th>
                      <td className="py-3 px-2 text-center">
                        <YesNo value={room.has_attached_washroom} />
                      </td>
                      <td className="py-3 px-2 text-center">
                        <YesNo value={room.has_balcony} />
                      </td>
                      <td className="py-3 px-2">
                        {room.assigned_resident_id ? (
                          <Link
                            to={`/residents/${encodeURIComponent(room.assigned_resident_id)}`}
                            className="flex items-center gap-2 min-w-0 font-semibold"
                          >
                            <Avatar name={room.assigned_display_name ?? room.assigned_resident_id} />
                            <span className="truncate">{room.assigned_display_name}</span>
                          </Link>
                        ) : (
                          <div className="text-center">
                            <Pill tone="warning">Vacant</Pill>
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-2 text-center whitespace-nowrap">
                        {room.assigned_since ? formatDate(room.assigned_since) : '—'}
                      </td>
                      <td className="py-3 px-2">
                        <div className="flex items-center justify-center gap-1.5 whitespace-nowrap">
                          <button
                            type="button"
                            className={BUTTON_SECONDARY}
                            aria-label={`Assign occupant to ${room.room_number}`}
                            onClick={() => setDialog({ kind: 'assign', room })}
                          >
                            <UserPlus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                            Assign
                          </button>
                          <button
                            type="button"
                            className={BUTTON_SECONDARY}
                            aria-label={`Edit ${room.room_number}`}
                            onClick={() => setDialog({ kind: 'edit', room })}
                          >
                            <Pencil className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                            Edit
                          </button>
                          {room.assigned_resident_id ? (
                            <button
                              type="button"
                              className={BUTTON_SECONDARY}
                              aria-label={`Vacate ${room.room_number}`}
                              onClick={() => setDialog({ kind: 'vacate', room })}
                            >
                              <DoorOpen className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                              Vacate
                            </button>
                          ) : (
                            <button
                              type="button"
                              className={BUTTON_QUIET}
                              aria-label={`Remove ${room.room_number}`}
                              title="Remove room"
                              onClick={() => setDialog({ kind: 'delete', room })}
                            >
                              <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </Panel>

      {dialog && (dialog.kind === 'add' || dialog.kind === 'edit') && (
        <RoomModal
          room={dialog.kind === 'edit' ? dialog.room : null}
          rooms={rooms.data?.items ?? []}
          residents={residents.data?.items ?? []}
          onClose={() => setDialog(null)}
          onDone={(room) =>
            done(dialog.kind === 'add' ? `${room.room_number} added` : `${room.room_number} saved`)
          }
        />
      )}
      {dialog?.kind === 'assign' && (
        <AssignModal
          room={dialog.room}
          rooms={rooms.data?.items ?? []}
          residents={residents.data?.items ?? []}
          onClose={() => setDialog(null)}
          onDone={(room) => done(`${room.assigned_display_name} moved into ${room.room_number}`)}
        />
      )}
      {dialog?.kind === 'vacate' && (
        <ConfirmModal
          title="Vacate Room"
          body={`${dialog.room.assigned_display_name} will no longer occupy ${dialog.room.room_number}. They stay a resident; only the room is freed.`}
          confirm="Vacate Room"
          pendingLabel="Vacating…"
          action={() => api.post<Room>(`${ROOMS}/${encodeURIComponent(dialog.room.room_id)}/vacate`)}
          onClose={() => setDialog(null)}
          onDone={() => done(`${dialog.room.room_number} is vacant`)}
        />
      )}
      {dialog?.kind === 'delete' && (
        <ConfirmModal
          title="Remove Room"
          body={`${dialog.room.room_number} will be removed from the inventory. This cannot be undone.`}
          confirm="Remove Room"
          pendingLabel="Removing…"
          action={() => api.delete(`${ROOMS}/${encodeURIComponent(dialog.room.room_id)}`)}
          onClose={() => setDialog(null)}
          onDone={() => done(`${dialog.room.room_number} removed`)}
        />
      )}
    </>
  )
}

/* ----------------------------------------------------------------- modals -- */

/** Active residents with no room, plus anyone already in `room` itself. */
function candidates(residents: Resident[], rooms: Room[], room: Room | null) {
  const housed = new Set(
    rooms
      .filter((other) => other.room_id !== room?.room_id && other.assigned_resident_id)
      .map((other) => other.assigned_resident_id),
  )
  return residents
    .filter((resident) => !housed.has(resident.resident_id))
    .map((resident) => ({ value: resident.resident_id, label: resident.display_name }))
}

function Footer({
  form,
  pending,
  label,
  pendingLabel,
  onClose,
}: {
  form: string
  pending: boolean
  label: string
  pendingLabel: string
  onClose: () => void
}) {
  return (
    <>
      <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={pending}>
        Cancel
      </button>
      <button type="submit" form={form} className={BUTTON_PRIMARY} disabled={pending}>
        {pending ? pendingLabel : label}
      </button>
    </>
  )
}

function RoomModal({
  room,
  rooms,
  residents,
  onClose,
  onDone,
}: {
  room: Room | null
  rooms: Room[]
  residents: Resident[]
  onClose: () => void
  onDone: (room: Room) => void
}) {
  const [number, setNumber] = useState(room?.room_number ?? '')
  const [washroom, setWashroom] = useState(room?.has_attached_washroom ?? false)
  const [balcony, setBalcony] = useState(room?.has_balcony ?? false)
  const [occupant, setOccupant] = useState(room?.assigned_resident_id ?? '')
  const [since, setSince] = useState(todayIso())
  const [submitted, setSubmitted] = useState(false)

  const trimmed = number.trim()
  const clash = rooms.find(
    (other) =>
      other.room_id !== room?.room_id &&
      other.room_number.trim().toLowerCase() === trimmed.toLowerCase(),
  )
  const occupantChanged = occupant !== (room?.assigned_resident_id ?? '')
  const errors = {
    number: !trimmed
      ? 'Enter the room number.'
      : trimmed.length > MAX_ROOM_NUMBER
        ? `Keep it to ${MAX_ROOM_NUMBER} characters.`
        : clash
          ? `${clash.room_number} already exists.`
          : null,
    since: occupant && occupantChanged ? dateError(since) : null,
  }
  const valid = !errors.number && !errors.since
  const formId = room ? 'room-edit-form' : 'room-add-form'

  const save = useMutation(
    () =>
      room
        ? api.patch<Room>(`${ROOMS}/${encodeURIComponent(room.room_id)}`, {
            room_number: trimmed,
            has_attached_washroom: washroom,
            has_balcony: balcony,
            ...(occupantChanged
              ? { assigned_resident_id: occupant || null, effective_date: occupant ? since : null }
              : {}),
          })
        : api.post<Room>(ROOMS, {
            room_number: trimmed,
            has_attached_washroom: washroom,
            has_balcony: balcony,
            assigned_resident_id: occupant || null,
            effective_date: occupant ? since : null,
          }),
    { onSuccess: onDone },
  )

  return (
    <Modal
      title={room ? `Edit ${room.room_number}` : 'Add Room'}
      description="Room numbers are unique in the flat. A room holds one resident at most."
      onClose={onClose}
      footer={
        <Footer
          form={formId}
          pending={save.pending}
          label={room ? 'Save Room' : 'Add Room'}
          pendingLabel="Saving…"
          onClose={onClose}
        />
      }
    >
      <form
        id={formId}
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setSubmitted(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <TextField
          label="Room Number"
          value={number}
          onChange={setNumber}
          placeholder="Room 1"
          required
          maxLength={MAX_ROOM_NUMBER + 10}
          error={submitted || clash ? errors.number : null}
        />
        <CheckboxField label="Attached Washroom" checked={washroom} onChange={setWashroom} />
        <CheckboxField label="Balcony" checked={balcony} onChange={setBalcony} />
        <SelectField
          label="Occupant"
          value={occupant}
          onChange={setOccupant}
          placeholder="Vacant"
          options={candidates(residents, rooms, room)}
          hint="Optional. Only active residents without a room are listed."
        />
        {occupant && occupantChanged && (
          <DateField
            label="Moved in on"
            value={since}
            onChange={setSince}
            required
            max={todayIso()}
            error={submitted ? errors.since : null}
          />
        )}
      </form>
    </Modal>
  )
}

function AssignModal({
  room,
  rooms,
  residents,
  onClose,
  onDone,
}: {
  room: Room
  rooms: Room[]
  residents: Resident[]
  onClose: () => void
  onDone: (room: Room) => void
}) {
  const [resident, setResident] = useState('')
  const [since, setSince] = useState(todayIso())
  const [submitted, setSubmitted] = useState(false)
  const options = candidates(residents, rooms, null)
  const errors = {
    resident: !resident ? 'Choose who is moving in.' : null,
    since: dateError(since),
  }
  const valid = !errors.resident && !errors.since

  const save = useMutation(
    () =>
      api.post<Room>(`${ROOMS}/${encodeURIComponent(room.room_id)}/assign`, {
        resident_id: resident,
        effective_date: since,
      }),
    { onSuccess: onDone },
  )

  return (
    <Modal
      title={room.assigned_resident_id ? 'Swap Occupant' : 'Assign Occupant'}
      description={
        room.assigned_resident_id
          ? `${room.assigned_display_name} occupies ${room.room_number} now and will move out of it.`
          : `${room.room_number} is vacant.`
      }
      onClose={onClose}
      footer={
        <Footer
          form="room-assign-form"
          pending={save.pending}
          label="Assign Occupant"
          pendingLabel="Assigning…"
          onClose={onClose}
        />
      }
    >
      <form
        id="room-assign-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setSubmitted(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        {options.length === 0 ? (
          <p className="m-0 text-xs text-[var(--text-secondary)]" role="status">
            Every active resident already has a room. Vacate one first, or add a resident.
          </p>
        ) : (
          <SelectField
            label="Resident"
            value={resident}
            onChange={setResident}
            placeholder="Choose a resident"
            options={options}
            required
            error={submitted ? errors.resident : null}
          />
        )}
        <DateField
          label="Effective date"
          value={since}
          onChange={setSince}
          required
          max={todayIso()}
          error={submitted ? errors.since : null}
        />
      </form>
    </Modal>
  )
}

function ConfirmModal({
  title,
  body,
  confirm,
  pendingLabel,
  action,
  onClose,
  onDone,
}: {
  title: string
  body: string
  confirm: string
  pendingLabel: string
  action: () => Promise<unknown>
  onClose: () => void
  onDone: () => void
}) {
  const run = useMutation(action, { onSuccess: onDone })
  return (
    <Modal
      title={title}
      description={body}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={run.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={run.pending}
            onClick={() => void run.run(undefined)}
          >
            {run.pending ? pendingLabel : confirm}
          </button>
        </>
      }
    >
      {run.error ? <ErrorNotice error={run.error} /> : null}
    </Modal>
  )
}

/* -------------------------------------------------- the resident's room -- */

/** The resident page's "Assigned Space / Room" card. */
export function AssignedRoomCard({ residentId }: { residentId: string }) {
  const rooms = useRooms()
  return (
    <Panel
      title="Assigned Space / Room"
      icon={<BedDouble className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      actions={
        <Link to="/services/property" className={BUTTON_SECONDARY}>
          Manage Rooms
        </Link>
      }
    >
      <AsyncSection resource={rooms} loadingLabel="Loading rooms…">
        {(data) => {
          const room = data.items.find((item) => item.assigned_resident_id === residentId)
          return room ? (
            <div className="space-y-2">
              <div className="text-sm font-bold text-[var(--text-primary)]">
                Occupying: {room.room_number}
              </div>
              <RoomAmenities room={room} />
              <div className="text-xs text-[var(--text-secondary)]">
                {`Assigned since ${formatDate(room.assigned_since)}`}
              </div>
            </div>
          ) : (
            <p className="m-0 text-xs text-[var(--text-secondary)]">
              No room assigned.{' '}
              {data.summary.vacant_rooms > 0
                ? `${data.summary.vacant_rooms} room${data.summary.vacant_rooms === 1 ? ' is' : 's are'} vacant.`
                : data.items.length === 0
                  ? 'No rooms have been added yet.'
                  : 'Every room is occupied.'}
            </p>
          )
        }}
      </AsyncSection>
    </Panel>
  )
}
