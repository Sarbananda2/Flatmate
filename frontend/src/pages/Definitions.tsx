/**
 * Expense categories and recurring definitions.
 *
 * Nothing is hardcoded. Rent, electricity, groceries, maintenance, maid,
 * broadband, furniture rental — all of them are rows the operator creates,
 * edits, and deactivates here. Each one carries the period rule that decides
 * which statement month charges an expense entered against it.
 */
import { useMemo, useState } from 'react'
import { Page } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Modal,
  Money,
} from '../components/Primitives'
import {
  CheckboxField,
  MoneyInput,
  SelectField,
  TextAreaField,
  TextField,
} from '../components/Form'
import { useToast } from '../components/Toast'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import {
  ALLOCATION_METHOD_LABELS,
  PERIOD_RULE_HELP,
  PERIOD_RULE_LABELS,
  paiseToInput,
} from '../lib/format'
import type {
  AllocationMethod,
  ExpenseDefinition,
  PeriodRule,
  Recurrence,
  Resident,
} from '../api/types'
import { RecurringPanel } from './RecurringPanel'

const PERIOD_RULE_OPTIONS: Array<{ value: PeriodRule; label: string }> = [
  { value: 'current_month', label: PERIOD_RULE_LABELS.current_month },
  { value: 'previous_month', label: PERIOD_RULE_LABELS.previous_month },
  { value: 'transaction_date', label: PERIOD_RULE_LABELS.transaction_date },
  { value: 'custom', label: PERIOD_RULE_LABELS.custom },
]

const METHOD_OPTIONS: Array<{ value: AllocationMethod; label: string }> = [
  { value: 'equal', label: ALLOCATION_METHOD_LABELS.equal },
  { value: 'weighted', label: ALLOCATION_METHOD_LABELS.weighted },
  { value: 'custom', label: ALLOCATION_METHOD_LABELS.custom },
  { value: 'measured', label: ALLOCATION_METHOD_LABELS.measured },
  { value: 'host_guest', label: ALLOCATION_METHOD_LABELS.host_guest },
]

export default function DefinitionsPage() {
  const toast = useToast()
  const [includeInactive, setIncludeInactive] = useState(false)
  const definitions = useResource<{ items: ExpenseDefinition[]; total: number }>(
    '/api/expense-definitions',
    { include_inactive: includeInactive },
  )
  const residents = useResource<{ items: Resident[] }>('/api/residents')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<ExpenseDefinition | null>(null)
  const [deactivating, setDeactivating] = useState<ExpenseDefinition | null>(null)

  return (
    <Page
      title="Expense categories"
      subtitle="Reusable definitions for the things the household pays for. Each one sets the period rule that decides which statement month charges it."
      breadcrumbs={[{ label: 'Configure' }, { label: 'Expense categories' }]}
      actions={
        <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
          New category
        </button>
      }
    >
      <Callout tone="info" title="Period rules decide which statement charges an expense">
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          <li>
            <strong>{PERIOD_RULE_LABELS.current_month}</strong> — {PERIOD_RULE_HELP.current_month}
          </li>
          <li>
            <strong>{PERIOD_RULE_LABELS.previous_month}</strong> — {PERIOD_RULE_HELP.previous_month}
          </li>
          <li>
            <strong>{PERIOD_RULE_LABELS.transaction_date}</strong> —{' '}
            {PERIOD_RULE_HELP.transaction_date}
          </li>
          <li>
            <strong>{PERIOD_RULE_LABELS.custom}</strong> — {PERIOD_RULE_HELP.custom}
          </li>
        </ul>
      </Callout>

      <Card
        title="Definitions"
        flush
        actions={
          <CheckboxField
            label="Show retired categories"
            checked={includeInactive}
            onChange={setIncludeInactive}
          />
        }
      >
        <AsyncSection
          resource={definitions}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No expense categories yet"
              body="Create the things this household pays for. You can also add a conventional starter set from Setup."
              action={
                <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
                  Create the first category
                </button>
              }
            />
          }
        >
          {(data) => (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Expense definitions</caption>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Category</th>
                    <th scope="col">Period rule</th>
                    <th scope="col">Default split</th>
                    <th scope="col">Recurs</th>
                    <th scope="col" className="num">
                      Default amount
                    </th>
                    <th scope="col">Status</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((definition) => (
                    <tr key={definition.expense_definition_id}>
                      <th scope="row">
                        {definition.name}
                        <div>
                          <span className="id-chip">{definition.expense_definition_id}</span>
                        </div>
                      </th>
                      <td>{definition.category}</td>
                      <td>
                        <Badge tone="accent" title={PERIOD_RULE_HELP[definition.period_rule]}>
                          {PERIOD_RULE_LABELS[definition.period_rule] ?? definition.period_rule}
                        </Badge>
                      </td>
                      <td>
                        {ALLOCATION_METHOD_LABELS[definition.default_allocation_method] ??
                          definition.default_allocation_method}
                      </td>
                      <td>
                        {definition.recurrence === 'monthly' ? (
                          <Badge tone="ok">
                            Monthly
                            {definition.day_of_month ? ` · day ${definition.day_of_month}` : ''}
                          </Badge>
                        ) : (
                          <span className="subtle">One-off</span>
                        )}
                      </td>
                      <td className="num">
                        {definition.default_amount_paise === null ? (
                          <span className="subtle">not set</span>
                        ) : (
                          <Money paise={definition.default_amount_paise} />
                        )}
                      </td>
                      <td>
                        {definition.is_active ? (
                          <Badge tone="ok">Active</Badge>
                        ) : (
                          <Badge tone="neutral">Retired</Badge>
                        )}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 6 }}>
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => setEditing(definition)}
                          >
                            Edit
                          </button>
                          {definition.is_active ? (
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => setDeactivating(definition)}
                            >
                              Retire
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={async () => {
                                await api.patch(
                                  `/api/expense-definitions/${encodeURIComponent(definition.expense_definition_id)}`,
                                  { is_active: true, reason: 'reactivated' },
                                )
                                definitions.reload()
                                toast.success(`${definition.name} is active again`)
                              }}
                            >
                              Reactivate
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
      </Card>

      <RecurringPanel onGenerated={() => definitions.reload()} />

      {(creating || editing) && (
        <DefinitionDialog
          definition={editing ?? undefined}
          residents={residents.data?.items ?? []}
          onClose={() => {
            setCreating(false)
            setEditing(null)
          }}
          onSaved={() => {
            setCreating(false)
            setEditing(null)
            definitions.reload()
            toast.success(editing ? 'Category updated' : 'Category created')
          }}
        />
      )}
      {deactivating && (
        <RetireDialog
          definition={deactivating}
          onClose={() => setDeactivating(null)}
          onSaved={() => {
            setDeactivating(null)
            definitions.reload()
            toast.success('Category retired', 'Existing expenses keep their link to it.')
          }}
        />
      )}
    </Page>
  )
}

function DefinitionDialog({
  definition,
  residents,
  onClose,
  onSaved,
}: {
  definition?: ExpenseDefinition
  residents: Resident[]
  onClose: () => void
  onSaved: () => void
}) {
  const editing = Boolean(definition)
  const [definitionId, setDefinitionId] = useState(definition?.expense_definition_id ?? '')
  const [name, setName] = useState(definition?.name ?? '')
  const [category, setCategory] = useState(definition?.category ?? '')
  const [periodRule, setPeriodRule] = useState<PeriodRule>(
    definition?.period_rule ?? 'current_month',
  )
  const [method, setMethod] = useState<AllocationMethod>(
    definition?.default_allocation_method ?? 'equal',
  )
  const [recurrence, setRecurrence] = useState<Recurrence>(definition?.recurrence ?? 'monthly')
  const [dayOfMonth, setDayOfMonth] = useState(
    definition?.day_of_month ? String(definition.day_of_month) : '',
  )
  const [amountRaw, setAmountRaw] = useState(
    definition?.default_amount_paise != null ? paiseToInput(definition.default_amount_paise) : '',
  )
  const [amountPaise, setAmountPaise] = useState<number | null>(
    definition?.default_amount_paise ?? null,
  )
  const [amountOk, setAmountOk] = useState(true)
  const [provider, setProvider] = useState(definition?.default_provider_name ?? '')
  const [notes, setNotes] = useState(definition?.notes ?? '')
  const [participants, setParticipants] = useState<string[]>(
    definition?.default_participants ?? [],
  )
  const [touched, setTouched] = useState(false)

  const activeResidents = useMemo(
    () => residents.filter((resident) => Boolean(resident.is_active)),
    [residents],
  )

  const dayError =
    dayOfMonth && (Number(dayOfMonth) < 1 || Number(dayOfMonth) > 28)
      ? 'Use 1–28 so every month has that day.'
      : null

  const save = useMutation(
    () => {
      const body: Record<string, unknown> = {
        name: name.trim(),
        category: category.trim(),
        period_rule: periodRule,
        default_allocation_method: method,
        recurrence,
        default_amount_paise: amountRaw.trim() ? amountPaise : null,
        default_provider_name: provider.trim() || null,
        default_participants: participants.length ? participants : null,
        day_of_month: dayOfMonth ? Number(dayOfMonth) : null,
        notes: notes.trim() || null,
      }
      if (editing) {
        body.reason = 'edited by the operator'
        return api.patch<ExpenseDefinition>(
          `/api/expense-definitions/${encodeURIComponent(definition!.expense_definition_id)}`,
          body,
        )
      }
      return api.post<ExpenseDefinition>('/api/expense-definitions', {
        ...body,
        definition_id: definitionId.trim(),
      })
    },
    { onSuccess: onSaved },
  )

  const idError = touched && !editing && !definitionId.trim() ? 'An ID is required.' : null
  const nameError = touched && !name.trim() ? 'A name is required.' : null
  const categoryError = touched && !category.trim() ? 'A category is required.' : null
  const valid =
    Boolean(name.trim() && category.trim() && (editing || definitionId.trim())) &&
    !dayError &&
    (!amountRaw.trim() || amountOk)

  return (
    <Modal
      title={editing ? `Edit ${definition!.name}` : 'New expense category'}
      description="The actual amount can differ every month; the default below is only the suggestion used when generating a recurring expense."
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button
            type="submit"
            form="definition-form"
            className="btn btn--primary"
            disabled={save.pending}
          >
            {save.pending ? 'Saving…' : editing ? 'Save changes' : 'Create category'}
          </button>
        </>
      }
    >
      <form
        id="definition-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (valid) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="form-grid">
          {!editing && (
            <TextField
              label="Definition ID"
              value={definitionId}
              onChange={setDefinitionId}
              required
              error={idError}
              hint="A short identifier. It cannot be changed later."
              maxLength={64}
            />
          )}
          <TextField
            label="Name"
            value={name}
            onChange={setName}
            required
            error={nameError}
            placeholder="Society maintenance"
            maxLength={200}
          />
          <TextField
            label="Category"
            value={category}
            onChange={setCategory}
            required
            error={categoryError}
            hint="Your own grouping, used in reports."
            placeholder="Housing"
            maxLength={120}
          />
        </div>

        <fieldset>
          <legend>Accounting period</legend>
          <div className="stack">
            <SelectField
              label="Period rule"
              value={periodRule}
              options={PERIOD_RULE_OPTIONS}
              onChange={setPeriodRule}
              hint={PERIOD_RULE_HELP[periodRule]}
            />
          </div>
        </fieldset>

        <fieldset>
          <legend>Defaults for new expenses</legend>
          <div className="stack">
            <div className="form-grid">
              <SelectField
                label="Default split"
                value={method}
                options={METHOD_OPTIONS}
                onChange={setMethod}
                hint="Measured usage is for electricity; it is calculated by the electricity module."
              />
              <MoneyInput
                label="Default amount"
                value={amountRaw}
                onChange={(raw, parsed) => {
                  setAmountRaw(raw)
                  setAmountOk(parsed.ok)
                  setAmountPaise(parsed.ok ? parsed.paise : null)
                }}
                hint="Optional. The suggestion when generating this month's recurring expense."
              />
              <TextField
                label="Default provider"
                value={provider}
                onChange={setProvider}
                hint="Who gets paid, for your records."
                maxLength={200}
              />
            </div>
            {activeResidents.length > 0 && (
              <div className="field">
                <span className="field-label">Default participants</span>
                <span className="field-hint">
                  Leave all unchecked to include every active resident. A resident who does not
                  take part gets an explicit zero share rather than being left out.
                </span>
                <div className="row">
                  {activeResidents.map((resident) => (
                    <label className="check" key={resident.resident_id}>
                      <input
                        type="checkbox"
                        checked={participants.includes(resident.resident_id)}
                        onChange={(event) =>
                          setParticipants((current) =>
                            event.target.checked
                              ? [...current, resident.resident_id]
                              : current.filter((id) => id !== resident.resident_id),
                          )
                        }
                      />
                      <span>{resident.display_name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        </fieldset>

        <fieldset>
          <legend>Recurrence</legend>
          <div className="form-grid">
            <SelectField
              label="Repeats"
              value={recurrence}
              options={[
                { value: 'monthly' as Recurrence, label: 'Every month' },
                { value: 'none' as Recurrence, label: 'Not recurring' },
              ]}
              onChange={setRecurrence}
              hint="Recurring categories appear in the monthly generation preview. Generation always needs your confirmation."
            />
            <TextField
              label="Day of month"
              value={dayOfMonth}
              onChange={setDayOfMonth}
              error={dayError}
              hint="1–28. Used as the transaction date of a generated expense."
            />
          </div>
        </fieldset>

        <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
      </form>
    </Modal>
  )
}

function RetireDialog({
  definition,
  onClose,
  onSaved,
}: {
  definition: ExpenseDefinition
  onClose: () => void
  onSaved: () => void
}) {
  const retire = useMutation(
    () =>
      api.post(
        `/api/expense-definitions/${encodeURIComponent(definition.expense_definition_id)}/deactivate`,
      ),
    { onSuccess: onSaved },
  )
  return (
    <ConfirmDialog
      title={`Retire ${definition.name}?`}
      confirmLabel="Retire category"
      pending={retire.pending}
      error={retire.error}
      onCancel={onClose}
      onConfirm={() => void retire.run(undefined)}
      body={
        <>
          The category stops being offered for new expenses and stops appearing in recurring
          generation. It is <strong>not deleted</strong>: expenses already recorded against it keep
          their link, so history stays auditable. You can reactivate it later.
        </>
      }
    />
  )
}
