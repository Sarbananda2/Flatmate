/**
 * First-run setup, operator settings, and health.
 *
 * This is also where the local security assumptions are stated plainly, since
 * the application has no authentication by design.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Page, useApp } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  EmptyState,
  ErrorNotice,
  Modal,
} from '../components/Primitives'
import { SelectField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { EditButton } from '../components/ui'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import { formatDateTime } from '../lib/format'
import type { Apartment, ExpenseDefinition, Health } from '../api/types'

export default function SetupPage() {
  const toast = useToast()
  const { reloadApartment } = useApp()
  const health = useResource<Health>('/health')
  const apartments = useResource<{
    items: Apartment[]
    total: number
    default_apartment_id: string | null
  }>('/api/apartments')
  const [creating, setCreating] = useState(false)

  const hasApartment = (apartments.data?.total ?? 0) > 0

  return (
    <Page
      title="Setup & health"
      subtitle="Create the apartment, pick the default, and check that the database is where you expect it."
      breadcrumbs={[{ label: 'Configure' }, { label: 'Setup & health' }]}
      actions={
        <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
          New apartment
        </button>
      }
    >
      {!hasApartment && !apartments.initialLoading && (
        <Callout tone="info" title="Nothing is set up yet">
          Create an apartment first, then add the residents sharing it, then the expense
          categories you want to track. Nothing about those categories is built in — rent,
          electricity, groceries and the rest are all records you create and can rename or retire.
        </Callout>
      )}

      <Card title="Apartments" flush>
        <AsyncSection
          resource={apartments}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No apartment yet"
              body="An apartment holds the residents, expenses, payments, and statements."
              action={
                <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
                  Create an apartment
                </button>
              }
            />
          }
        >
          {(data) => (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Apartments</caption>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">ID</th>
                    <th scope="col">Currency</th>
                    <th scope="col">Time zone</th>
                    <th scope="col">Created</th>
                    <th scope="col">Default</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((apartment) => (
                    <tr key={apartment.apartment_id}>
                      <th scope="row">{apartment.name}</th>
                      <td>
                        <span className="id-chip">{apartment.apartment_id}</span>
                      </td>
                      <td>{apartment.currency_code}</td>
                      <td>{apartment.timezone}</td>
                      <td className="nowrap">{formatDateTime(apartment.created_at)}</td>
                      <td>
                        {apartment.apartment_id === data.default_apartment_id ? (
                          <Badge tone="ok">Default</Badge>
                        ) : (
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={async () => {
                              await api.put(
                                `/api/settings/default-apartment/${encodeURIComponent(apartment.apartment_id)}`,
                              )
                              apartments.reload()
                              reloadApartment()
                              toast.success('Default apartment changed')
                            }}
                          >
                            Make default
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </Card>

      {hasApartment && apartments.data && (
        <SettingsCard
          apartments={apartments.data.items}
          defaultApartmentId={apartments.data.default_apartment_id}
          onSaved={() => {
            apartments.reload()
            reloadApartment()
          }}
        />
      )}

      {hasApartment && <StarterDefinitions />}

      {hasApartment && (
        <Card
          title="Residents & Flatmates"
          hint="Who lives here: contact details, emergency contacts, UPI IDs, and move-in and move-out dates."
        >
          <div className="row">
            <Link className="btn btn--primary" to="/residents">
              Manage residents
            </Link>
          </div>
        </Card>
      )}

      {hasApartment && (
        <Card
          title="Rent & Lease"
          hint="The master lease and its terms live under Housing; how each month's rent is divided and collected lives on the Rent screen."
        >
          <div className="row">
            <Link className="btn btn--primary" to="/housing?tab=lease">
              Configure rent & lease
            </Link>
            <Link className="btn" to="/services/rent">
              Open monthly rent
            </Link>
          </div>
        </Card>
      )}

      <Card
        title="Health and database"
        hint="The household's PostgreSQL database. Take a backup with scripts/manage.py backup."
      >
        <AsyncSection resource={health}>
          {(data) => (
            <div className="stack">
              <div className="row">
                <Badge tone={data.status === 'ok' ? 'ok' : 'warn'}>
                  {data.status === 'ok' ? 'Healthy' : 'Needs attention'}
                </Badge>
                <span className="subtle">
                  v{data.version} · schema v{data.schema_version}
                </span>
              </div>
              <dl className="kv">
                <dt>Database</dt>
                <dd className="mono">{data.database_path}</dd>
                <dt>Database reachable</dt>
                <dd>{data.database_exists ? 'Yes' : 'No'}</dd>
                <dt>Schema up to date</dt>
                <dd>{String(data.checks.schema_up_to_date ?? '—')}</dd>
                <dt>Integrity check</dt>
                <dd>{String(data.checks.integrity ?? '—')}</dd>
                <dt>Foreign keys enforced</dt>
                <dd>{String(data.checks.foreign_keys ?? '—')}</dd>
                <dt>Apartments on record</dt>
                <dd>{data.apartment_count}</dd>
                <dt>Server address</dt>
                <dd className="mono">
                  {String(data.settings.host)}:{String(data.settings.port)}
                </dd>
                <dt>Bound to localhost only</dt>
                <dd>{data.settings.binds_loopback_only ? 'Yes' : 'No — see the warning below'}</dd>
              </dl>
              {!data.settings.binds_loopback_only && (
                <Callout tone="danger" title="This server is reachable from the network">
                  The application has no authentication. Anyone who can reach this port can read
                  and change the household's accounts. Bind it to 127.0.0.1 unless you have added
                  authentication in front of it.
                </Callout>
              )}
              <Callout tone="info" title="Local security assumptions">
                This is a single-operator, local-first application. There is no login, and none is
                implied. It is meant to run on your own machine, bound to localhost, with ordinary
                file permissions on the database. Do not expose it to the internet.
              </Callout>
            </div>
          )}
        </AsyncSection>
      </Card>

      {creating && (
        <CreateApartmentDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            apartments.reload()
            health.reload()
            reloadApartment()
            toast.success('Apartment created', 'Add the residents next.')
          }}
        />
      )}
    </Page>
  )
}

function StarterDefinitions() {
  const toast = useToast()
  const definitions = useResource<{ items: ExpenseDefinition[]; total: number }>(
    '/api/expense-definitions',
    { include_inactive: true },
  )
  const seed = useMutation(
    (apartmentId: string) =>
      api.post<{ created_count: number }>(
        `/api/apartments/${encodeURIComponent(apartmentId)}/starter-definitions`,
      ),
    {
      onSuccess: (result) => {
        definitions.reload()
        toast.success(
          result.created_count
            ? `Added ${result.created_count} expense categories`
            : 'Everything in the starter set already exists',
        )
      },
    },
  )
  const { apartment } = useApp()
  if (!apartment) return null
  if ((definitions.data?.total ?? 0) > 0) return null

  return (
    <Card
      title="Expense categories"
      hint="No category is built into the application. These are ordinary records you can rename, re-rule, or retire at any time."
    >
      <div className="stack">
        <ErrorNotice error={seed.error} />
        <p className="muted" style={{ margin: 0 }}>
          You have no expense categories yet. Add a conventional starting set — rent, electricity,
          broadband, maintenance, maid, furniture rental, groceries — each with a sensible period
          rule, or create your own from scratch on the Expense categories screen.
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn--primary"
            disabled={seed.pending}
            onClick={() => seed.run(apartment.apartment_id)}
          >
            {seed.pending ? 'Adding…' : 'Add the starter set'}
          </button>
        </div>
      </div>
    </Card>
  )
}

function CreateApartmentDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => void
}) {
  const [apartmentId, setApartmentId] = useState('home')
  const [name, setName] = useState('')
  const [currency, setCurrency] = useState('INR')
  const [timezone, setTimezone] = useState('Asia/Kolkata')
  const [touched, setTouched] = useState(false)

  const create = useMutation(
    () =>
      api.post<Apartment>('/api/apartments', {
        apartment_id: apartmentId.trim(),
        name: name.trim(),
        currency_code: currency.trim().toUpperCase(),
        timezone: timezone.trim(),
      }),
    { onSuccess: onCreated },
  )

  const idError = touched && !apartmentId.trim() ? 'An ID is required.' : null
  const nameError = touched && !name.trim() ? 'A name is required.' : null
  const valid = Boolean(apartmentId.trim() && name.trim() && currency.trim().length === 3)

  return (
    <Modal
      title="Create an apartment"
      description="The apartment is the container for residents, expenses, payments, and statements."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={create.pending}>
            Cancel
          </button>
          <button
            type="submit"
            form="create-apartment"
            className="btn btn--primary"
            disabled={create.pending}
          >
            {create.pending ? 'Creating…' : 'Create apartment'}
          </button>
        </>
      }
    >
      <form
        id="create-apartment"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (valid) void create.run(undefined)
        }}
      >
        <ErrorNotice error={create.error} />
        <div className="form-grid">
          <TextField
            label="Apartment ID"
            value={apartmentId}
            onChange={setApartmentId}
            required
            error={idError}
            hint="A short identifier used in URLs and exports. It cannot be changed later."
            maxLength={64}
          />
          <TextField
            label="Name"
            value={name}
            onChange={setName}
            required
            error={nameError}
            placeholder="Flat 402, Lakeview"
            maxLength={200}
          />
          <TextField
            label="Currency code"
            value={currency}
            onChange={setCurrency}
            hint="Three letters. Amounts are stored as integer minor units."
            maxLength={3}
          />
          <TextField
            label="Time zone"
            value={timezone}
            onChange={setTimezone}
            hint="Used for timestamps on statements and the audit trail."
            maxLength={64}
          />
        </div>
      </form>
    </Modal>
  )
}

/* --------------------------------------------------------------- settings -- */

/** The card's name, shared with the dialog that edits it. */
const SETTINGS_TITLE = 'Settings'

interface OperatorSettings {
  default_apartment_id: string | null
  server: { operator_name?: string }
}

function SettingRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

/** The saved settings, read-only, with an explicit way into editing them. */
export function SettingsCard({
  apartments,
  defaultApartmentId,
  onSaved,
}: {
  apartments: Apartment[]
  defaultApartmentId: string | null
  onSaved: () => void
}) {
  const settings = useResource<OperatorSettings>('/api/settings')
  const [editing, setEditing] = useState(false)
  const current =
    apartments.find((apartment) => apartment.apartment_id === defaultApartmentId) ??
    apartments[0] ??
    null

  return (
    <Card
      title={SETTINGS_TITLE}
      hint="The apartment the app opens on, and who is recorded as making changes."
      actions={
        current && <EditButton label="Edit settings" onClick={() => setEditing(true)} />
      }
    >
      <dl className="kv" aria-label="Settings">
        <SettingRow label="Default apartment">{current ? current.name : '—'}</SettingRow>
        <SettingRow label="Currency">{current?.currency_code ?? '—'}</SettingRow>
        <SettingRow label="Time zone">{current?.timezone ?? '—'}</SettingRow>
        <SettingRow label="Operator">
          {settings.data?.server.operator_name ?? '—'}
          <span className="subtle"> · set on the server by AEM_OPERATOR_NAME</span>
        </SettingRow>
      </dl>
      {editing && current && (
        <EditSettingsDialog
          apartments={apartments}
          current={current}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            onSaved()
          }}
        />
      )}
    </Card>
  )
}

function EditSettingsDialog({
  apartments,
  current,
  onClose,
  onSaved,
}: {
  apartments: Apartment[]
  current: Apartment
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const [defaultId, setDefaultId] = useState(current.apartment_id)
  const chosen = apartments.find((apartment) => apartment.apartment_id === defaultId) ?? current
  const [name, setName] = useState(chosen.name)
  const [timezone, setTimezone] = useState(chosen.timezone)
  const [submitted, setSubmitted] = useState(false)

  const errors = {
    name: !name.trim() ? 'A name is required.' : name.trim().length > 200 ? 'Keep it to 200 characters.' : null,
    timezone: !timezone.trim() ? 'A time zone is required, e.g. Asia/Kolkata.' : null,
  }
  const valid = !errors.name && !errors.timezone

  const save = useMutation(
    async () => {
      if (defaultId !== current.apartment_id) {
        await api.put(`/api/settings/default-apartment/${encodeURIComponent(defaultId)}`)
      }
      const changes: Record<string, string> = {}
      if (name.trim() !== chosen.name) changes.name = name.trim()
      if (timezone.trim() !== chosen.timezone) changes.timezone = timezone.trim()
      if (Object.keys(changes).length > 0) {
        await api.patch(`/api/apartments/${encodeURIComponent(defaultId)}`, changes)
      }
    },
    {
      onSuccess: () => {
        toast.success('Settings saved')
        onSaved()
      },
    },
  )

  return (
    <Modal
      title={SETTINGS_TITLE}
      description="Changes apply as soon as they are saved."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form="edit-settings" className="btn btn--primary" disabled={save.pending}>
            {save.pending ? 'Saving…' : 'Save Changes'}
          </button>
        </>
      }
    >
      <form
        id="edit-settings"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setSubmitted(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        {apartments.length > 1 && (
          <SelectField
            label="Default apartment"
            value={defaultId}
            onChange={(id) => {
              setDefaultId(id)
              const next = apartments.find((apartment) => apartment.apartment_id === id)
              if (next) {
                setName(next.name)
                setTimezone(next.timezone)
              }
            }}
            options={apartments.map((apartment) => ({
              value: apartment.apartment_id,
              label: apartment.name,
            }))}
          />
        )}
        <TextField
          label="Apartment name"
          value={name}
          onChange={setName}
          required
          maxLength={210}
          error={submitted ? errors.name : null}
        />
        <TextField
          label="Time zone"
          value={timezone}
          onChange={setTimezone}
          required
          placeholder="Asia/Kolkata"
          error={submitted ? errors.timezone : null}
        />
      </form>
    </Modal>
  )
}
