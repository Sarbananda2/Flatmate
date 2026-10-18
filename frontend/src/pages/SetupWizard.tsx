/**
 * Guided first-run setup.
 *
 * Two things shape this screen.
 *
 * **Fewest clicks.** Identifiers are derived from the names you type, so you
 * never invent one. Residents go in as a list rather than one dialog at a
 * time. The essentials come pre-ticked with the right period rule already
 * chosen. Amounts are optional. Four steps, one button each.
 *
 * **Closing it costs nothing.** Every step commits to the database when you
 * press Continue, and the step to resume at is read back from the server --
 * from whether an apartment row, resident rows, and definition rows actually
 * exist. Close the tab, clear the browser, move to another machine: reopening
 * lands you exactly where you stopped, with nothing to redo. Browser storage
 * holds only what you have half-typed in the step you are on, and the screen
 * works correctly when it is unavailable.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronLeft, RotateCcw, X } from 'lucide-react'
import { api } from '../api/client'
import { Badge, Callout, ErrorNotice, LoadingState } from '../components/Primitives'
import { Avatar } from '../components/ui'
import { SelectField, TextField } from '../components/Form'
import { useToast } from '../components/Toast'
import { useMutation, useResource } from '../lib/useApi'
import {
  PERIOD_RULE_LABELS,
  formatInr,
  formatMonth,
  parseRupees,
} from '../lib/format'
import { PINCODE_MESSAGE, PINCODE_PATTERN, patternError } from '../lib/validation'
import type {
  Apartment,
  CatalogueEntry,
  PeriodRule,
  Recurrence,
  SetupState,
  SetupStepKey,
} from '../api/types'

const DRAFT_KEY = 'aem.setup-draft'

/** The apartment's postal address, part by part, as step 1 holds it. */
interface AddressDraft {
  flat_number: string
  tower_block: string
  society_name: string
  locality_street: string
  city: string
  state: string
  pincode: string
}

const EMPTY_ADDRESS: AddressDraft = {
  flat_number: '',
  tower_block: '',
  society_name: '',
  locality_street: '',
  city: '',
  state: '',
  pincode: '',
}

const ADDRESS_FIELDS = Object.keys(EMPTY_ADDRESS) as Array<keyof AddressDraft>

/** What an apartment is called when it is not given a name, as the backend names it. */
function apartmentName(societyName: string | null | undefined, flatNumber: string | null | undefined): string {
  return [societyName, flatNumber].map((part) => (part ?? '').trim()).filter(Boolean).join(' ') || 'My Apartment'
}

/** An apartment's address, as step 1 shows it for editing. */
function addressOf(apartment: Apartment): AddressDraft {
  return {
    flat_number: apartment.flat_number ?? '',
    tower_block: apartment.tower_block ?? '',
    society_name: apartment.society_name ?? '',
    locality_street: apartment.locality_street ?? '',
    city: apartment.city ?? '',
    state: apartment.state ?? '',
    pincode: apartment.pincode ?? '',
  }
}

/** Half-typed input for the step in progress. Never the source of truth. */
interface Draft {
  address: AddressDraft
  residentNames: string[]
  picked: Record<string, boolean>
  amounts: Record<string, string>
  custom: Array<{ name: string; period_rule: PeriodRule; recurrence: Recurrence }>
  createStatement: boolean
}

const EMPTY_DRAFT: Draft = {
  address: EMPTY_ADDRESS,
  residentNames: ['', '', ''],
  picked: {},
  amounts: {},
  custom: [],
  createStatement: true,
}

function readDraft(): Draft {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY)
    if (!raw) return EMPTY_DRAFT
    const saved = JSON.parse(raw) as Partial<Draft>
    return { ...EMPTY_DRAFT, ...saved, address: { ...EMPTY_ADDRESS, ...saved.address } }
  } catch {
    // Private window, blocked storage, or stale shape. The wizard works
    // without it; only the half-typed text in the current step is lost.
    return EMPTY_DRAFT
  }
}

function writeDraft(draft: Draft): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
  } catch {
    // Not important enough to interrupt setup over.
  }
}

function clearDraft(): void {
  try {
    window.localStorage.removeItem(DRAFT_KEY)
  } catch {
    /* nothing to do */
  }
}

const STEP_ORDER: SetupStepKey[] = ['apartment', 'residents', 'categories', 'finish']

export default function SetupWizardPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const state = useResource<SetupState>('/api/setup/state')
  const catalogue = useResource<{ items: CatalogueEntry[] }>('/api/setup/catalogue')
  const [draft, setDraft] = useState<Draft>(readDraft)
  const [touched, setTouched] = useState(false)

  const patch = useCallback((changes: Partial<Draft>) => {
    setDraft((current) => {
      const next = { ...current, ...changes }
      writeDraft(next)
      return next
    })
  }, [])

  // The step to show comes from the server, not from anything remembered here.
  // The one exception is the operator going back to look at a finished step,
  // which lasts only until they continue or leave.
  const serverStep = state.data?.next_step ?? 'apartment'
  const [activeStepOverride, setActiveStepOverride] = useState<SetupStepKey | null>(null)
  const step = activeStepOverride ?? serverStep
  const existingResidents = state.data?.residents ?? []
  const activeResidents = existingResidents.filter((resident) => resident.is_active)
  const removedResidents = existingResidents.filter((resident) => !resident.is_active)
  const existingDefinitions = state.data?.definitions ?? []

  /** Open a step that is already done, showing what the server has for it. */
  const goTo = (target: SetupStepKey) => {
    setTouched(false)
    setActiveStepOverride(target === serverStep ? null : target)
    if (target === 'apartment' && state.data?.apartment) {
      patch({ address: addressOf(state.data.apartment) })
    }
    // Saved residents are listed and edited in place, so the boxes below them
    // start empty and are only for adding someone new.
    if (target === 'residents' && existingResidents.length > 0) {
      patch({ residentNames: [''] })
    }
  }

  /** After continuing from a revisited step: the next one, up to where setup stands. */
  const advanceFrom = (from: SetupStepKey) => {
    setTouched(false)
    const next = STEP_ORDER[STEP_ORDER.indexOf(from) + 1]
    const reached = STEP_ORDER.indexOf(serverStep === 'done' ? 'finish' : serverStep)
    setActiveStepOverride(next && STEP_ORDER.indexOf(next) < reached ? next : null)
  }

  // Pre-tick the suggested essentials once the catalogue arrives.
  useEffect(() => {
    const items = catalogue.data?.items
    if (!items || Object.keys(draft.picked).length > 0) return
    const picked: Record<string, boolean> = {}
    const amounts: Record<string, string> = {}
    for (const item of items) {
      picked[item.definition_id] = item.suggested
      amounts[item.definition_id] = ''
    }
    patch({ picked, amounts })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogue.data])

  // Step 1: the flat, its society and its city are needed; a PIN code, if
  // typed, is checked as it is typed.
  const address = draft.address
  const addressErrors = {
    flat_number: address.flat_number.trim() ? null : 'Enter flat/unit number.',
    society_name: address.society_name.trim() ? null : 'Enter society/complex name.',
    city: address.city.trim() ? null : 'Enter city.',
  }
  const pincodeError = patternError(address.pincode, PINCODE_PATTERN, PINCODE_MESSAGE)
  const addressValid = Object.values(addressErrors).every((error) => !error) && !pincodeError
  const saveApartment = useMutation(
    async () => {
      const parts = {
        flat_number: address.flat_number.trim(),
        tower_block: address.tower_block.trim() || null,
        society_name: address.society_name.trim(),
        locality_street: address.locality_street.trim() || null,
        city: address.city.trim(),
        state: address.state.trim() || null,
        pincode: address.pincode.trim() || null,
      }
      const existing = state.data?.apartment
      // Going back to this step changes the address; it never makes a second
      // apartment. A name still the one its address gave it follows the address.
      if (!existing) {
        await api.post<{ state: SetupState }>('/api/setup/apartment', parts)
      } else if (ADDRESS_FIELDS.some((field) => (existing[field] ?? null) !== parts[field])) {
        const named = existing.name === apartmentName(existing.society_name, existing.flat_number)
        await api.patch(`/api/apartments/${encodeURIComponent(existing.apartment_id)}`, {
          ...parts,
          ...(named ? { name: apartmentName(parts.society_name, parts.flat_number) } : {}),
        })
      }
    },
    {
      onSuccess: () => {
        advanceFrom('apartment')
        state.reload()
      },
    },
  )

  const typedResidents = draft.residentNames.map((name) => name.trim()).filter(Boolean)
  const knownResidents = new Set(existingResidents.map((r) => r.display_name.toLowerCase()))
  const newResidents = typedResidents.filter((name) => !knownResidents.has(name.toLowerCase()))
  const saveResidents = useMutation(
    async () => {
      // Names already saved are left alone; only new ones are sent.
      if (existingResidents.length > 0 && newResidents.length === 0) return
      await api.post<{ created_count: number; state: SetupState }>('/api/setup/residents', {
        names: existingResidents.length > 0 ? newResidents : typedResidents,
      })
    },
    {
      onSuccess: () => {
        patch({ residentNames: [''] })
        advanceFrom('residents')
        state.reload()
      },
    },
  )

  const chosen = useMemo(() => {
    const items = catalogue.data?.items ?? []
    const fromCatalogue = items
      .filter((item) => draft.picked[item.definition_id])
      .map((item) => {
        const parsed = parseRupees(draft.amounts[item.definition_id] ?? '', {
          allowEmpty: true,
        })
        const typed = (draft.amounts[item.definition_id] ?? '').trim()
        return {
          definition_id: item.definition_id,
          name: item.name,
          category: item.category,
          period_rule: item.period_rule,
          default_allocation_method: item.default_allocation_method,
          recurrence: item.recurrence,
          sort_order: item.sort_order,
          default_amount_paise: typed && parsed.ok ? parsed.paise : null,
        }
      })
    const custom = draft.custom
      .filter((entry) => entry.name.trim())
      .map((entry, index) => ({
        name: entry.name.trim(),
        category: 'Household',
        period_rule: entry.period_rule,
        default_allocation_method: 'equal' as const,
        recurrence: entry.recurrence,
        sort_order: 500 + index,
        default_amount_paise: null,
      }))
    return [...fromCatalogue, ...custom]
  }, [catalogue.data, draft.picked, draft.amounts, draft.custom])

  const knownCategories = new Set(existingDefinitions.map((d) => d.name.toLowerCase()))
  const newCategories = chosen.filter((item) => !knownCategories.has(item.name.toLowerCase()))
  const saveCategories = useMutation(
    async () => {
      // Categories already saved are left alone; only new ones are sent.
      if (existingDefinitions.length > 0 && newCategories.length === 0) return
      await api.post<{ created_count: number; state: SetupState }>('/api/setup/categories', {
        items: newCategories.map((item) => ({
          ...item,
          default_amount_paise: item.default_amount_paise ?? undefined,
        })),
      })
    },
    {
      onSuccess: () => {
        advanceFrom('categories')
        state.reload()
      },
    },
  )

  const finish = useMutation(
    () => api.post<{ statement_id: string | null }>('/api/setup/finish', {
      create_statement_for: draft.createStatement ? state.data?.current_month : null,
    }),
    {
      onSuccess: () => {
        clearDraft()
        toast.success('Setup complete', 'You can change any of this later.')
        navigate('/')
      },
    },
  )

  if (state.initialLoading) {
    return (
      <div className="page">
        <LoadingState label="Checking what is already set up…" />
      </div>
    )
  }

  const data = state.data
  const removeResident = (removeIndex: number) => {
    patch({
      residentNames: draft.residentNames.filter((_, i) => i !== removeIndex),
    })
  }
  const amountTotal = chosen.reduce((sum, item) => sum + (item.default_amount_paise ?? 0), 0)

  return (
    <div className="page page--narrow">
      <header className="page-header">
        <div className="page-title-row">
          <div>
            <h1>{data?.is_complete ? 'Setup' : "Let's get you set up"}</h1>
            <p className="page-subtitle">
              Four short steps. <strong>Each one saves as you go</strong>, so if you close
              this by accident you will come back to exactly where you stopped — nothing to
              redo.
            </p>
          </div>
          {data?.is_complete && (
            <div className="page-actions">
              <Link className="btn" to="/">
                Back to the dashboard
              </Link>
            </div>
          )}
        </div>
      </header>

      <ol className="stepper" aria-label="Setup progress">
        {(data?.steps ?? []).map((item, index) => {
          const current = item.key === (step === 'done' ? 'finish' : step)
          const contents = (
            <>
              <span className="stepper-mark" aria-hidden="true">
                {item.done && !current ? '✓' : index + 1}
              </span>
              <span className="stepper-text">
                <span className="stepper-label">{item.label}</span>
                {item.summary && <span className="stepper-summary">{item.summary}</span>}
              </span>
            </>
          )
          // A finished step can be reopened; so can the step setup has reached,
          // to get back to it after looking at an earlier one.
          const reachable = !current && (item.done || item.key === serverStep)
          return (
            <li
              key={item.key}
              className={reachable ? 'stepper-item stepper-item--link' : 'stepper-item'}
              data-state={current ? 'current' : item.done ? 'done' : 'todo'}
              aria-current={current ? 'step' : undefined}
            >
              {reachable ? (
                <button
                  type="button"
                  className="stepper-link"
                  onClick={() => goTo(item.key)}
                  aria-label={`Go to step ${index + 1}: ${item.label}`}
                >
                  {contents}
                </button>
              ) : (
                contents
              )}
            </li>
          )
        })}
      </ol>

      {step === 'apartment' && (
        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Step 1 · Where is your apartment?</h2>
              <div className="card-hint">
                Enter your apartment and address details. These will be used on your household dashboard,
                lease terms, and HRA rent receipts.
              </div>
            </div>
          </div>
          <div className="card-body">
            <form
              className="stack"
              // The fields say what is missing themselves, rather than the browser.
              noValidate
              onSubmit={(event) => {
                event.preventDefault()
                setTouched(true)
                if (addressValid) void saveApartment.run(undefined)
              }}
            >
              <ErrorNotice error={saveApartment.error} />
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
                <TextField
                  label="Flat / Unit Number"
                  value={address.flat_number}
                  onChange={(value) => patch({ address: { ...address, flat_number: value } })}
                  required
                  maxLength={50}
                  placeholder="e.g. 1805"
                  error={touched ? addressErrors.flat_number : null}
                />
                <TextField
                  label="Tower / Wing / Block"
                  value={address.tower_block}
                  onChange={(value) => patch({ address: { ...address, tower_block: value } })}
                  maxLength={50}
                  placeholder="e.g. CG6 or Tower B"
                />
                <div className="md:col-span-2">
                  <TextField
                    label="Society / Complex Name"
                    value={address.society_name}
                    onChange={(value) => patch({ address: { ...address, society_name: value } })}
                    required
                    maxLength={150}
                    placeholder="e.g. Supertech Capetown"
                    error={touched ? addressErrors.society_name : null}
                  />
                </div>
                <TextField
                  label="Locality / Sector / Street"
                  value={address.locality_street}
                  onChange={(value) => patch({ address: { ...address, locality_street: value } })}
                  maxLength={200}
                  placeholder="e.g. Sector 74"
                />
                <TextField
                  label="City"
                  value={address.city}
                  onChange={(value) => patch({ address: { ...address, city: value } })}
                  required
                  maxLength={100}
                  placeholder="e.g. Noida"
                  error={touched ? addressErrors.city : null}
                />
                <TextField
                  label="State"
                  value={address.state}
                  onChange={(value) => patch({ address: { ...address, state: value } })}
                  maxLength={100}
                  placeholder="e.g. Uttar Pradesh"
                />
                <TextField
                  label="PIN Code"
                  value={address.pincode}
                  onChange={(value) => patch({ address: { ...address, pincode: value } })}
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="201301"
                  hint="6 digits"
                  error={pincodeError}
                />
              </div>
              <div className="form-actions">
                <button type="submit" className="btn btn--primary" disabled={saveApartment.pending}>
                  {saveApartment.pending ? 'Saving…' : 'Continue'}
                </button>
              </div>
            </form>
          </div>
        </section>
      )}

      {step === 'residents' && (
        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Step 2 · Who lives here?</h2>
              <div className="card-hint">
                Everyone sharing the costs. Add them all at once — leave spare boxes empty.
              </div>
            </div>
          </div>
          <div className="card-body">
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault()
                setTouched(true)
                if (typedResidents.length > 0 || existingResidents.length > 0) {
                  void saveResidents.run(undefined)
                }
              }}
            >
              <ErrorNotice error={saveResidents.error} />
              {existingResidents.length > 0 && (
                <section aria-label="Residents already added" className="space-y-2">
                  <ul className="m-0 p-0 list-none space-y-2">
                    {activeResidents.map((resident) => (
                      <SavedResidentRow
                        // Remount on a saved rename so the box shows what was stored.
                        key={`${resident.resident_id}:${resident.display_name}`}
                        resident={resident}
                        canRemove={activeResidents.length > 1}
                        onChanged={state.reload}
                      />
                    ))}
                  </ul>
                  {activeResidents.length === 1 && (
                    <p className="m-0 text-xs text-[var(--text-secondary)]">
                      At least one resident is required.
                    </p>
                  )}
                  {removedResidents.length > 0 && (
                    <RemovedResidents residents={removedResidents} onChanged={state.reload} />
                  )}
                  <h3 className="m-0 pt-2 text-sm font-bold text-[var(--text-primary)]">
                    Add new flatmates
                  </h3>
                </section>
              )}
              <div className="form-grid">
                {draft.residentNames.map((name, index) => (
                  <div key={index} className="flex items-end gap-1.5 min-w-0">
                    <div className="flex-1 min-w-0">
                      <TextField
                        label={
                          existingResidents.length > 0
                            ? `New flatmate ${index + 1}`
                            : `Resident ${index + 1}`
                        }
                        value={name}
                        onChange={(value) => {
                          const names = [...draft.residentNames]
                          names[index] = value
                          patch({ residentNames: names })
                        }}
                        placeholder={
                          ['e.g. Alice', 'e.g. Bob', 'e.g. Chris'][index] ?? 'e.g. Name'
                        }
                        maxLength={200}
                      />
                    </div>
                    {draft.residentNames.length > 1 && (
                      <button
                        type="button"
                        className="btn btn--ghost p-2 rounded-md shrink-0"
                        onClick={() => removeResident(index)}
                        aria-label={`Remove resident ${index + 1}`}
                        title="Remove this box"
                      >
                        <X className="w-4 h-4" aria-hidden="true" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              {touched && typedResidents.length === 0 && existingResidents.length === 0 && (
                <span className="field-error">Add at least one person.</span>
              )}
              <div className="row">
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => patch({ residentNames: [...draft.residentNames, ''] })}
                >
                  + Another person
                </button>
              </div>
              <ul className="m-0 pl-4 space-y-1 text-xs text-[var(--text-secondary)] list-disc">
                <li>You can add people, or mark someone as moved out, at any time.</li>
                <li>Moving out never deletes anyone; their history is kept.</li>
                <li>Shares are not prorated: a part month counts as a full share.</li>
              </ul>
              <div className="form-actions">
                <button
                  type="button"
                  className="btn btn--ghost flex items-center gap-1.5 cursor-pointer"
                  onClick={() => goTo('apartment')}
                >
                  <ChevronLeft className="w-4 h-4 shrink-0" aria-hidden="true" />
                  Back
                </button>
                <button type="submit" className="btn btn--primary" disabled={saveResidents.pending}>
                  {saveResidents.pending ? 'Saving…' : 'Continue'}
                </button>
              </div>
            </form>
          </div>
        </section>
      )}

      {step === 'categories' && (
        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Step 3 · What does the household pay for?</h2>
              <div className="card-hint">
                The usual ones are already ticked, each with the right accounting month
                chosen. Untick what you do not pay. Amounts are optional — they are only a
                suggestion for next month.
              </div>
            </div>
          </div>
          <div className="card-body">
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault()
                setTouched(true)
                if (chosen.length > 0 || existingDefinitions.length > 0) {
                  void saveCategories.run(undefined)
                }
              }}
            >
              <ErrorNotice error={saveCategories.error} />
              <div className="table-scroll">
                <table className="data">
                  <caption className="sr-only">Essentials to set up</caption>
                  <thead>
                    <tr>
                      <th scope="col">Include</th>
                      <th scope="col">What</th>
                      <th scope="col">Charged in</th>
                      <th scope="col" className="num">
                        Typical amount
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {(catalogue.data?.items ?? []).map((item) => {
                      const on = Boolean(draft.picked[item.definition_id])
                      return (
                        <tr key={item.definition_id}>
                          <td>
                            <input
                              type="checkbox"
                              id={`pick-${item.definition_id}`}
                              checked={on}
                              style={{ width: 'auto' }}
                              onChange={(event) =>
                                patch({
                                  picked: {
                                    ...draft.picked,
                                    [item.definition_id]: event.target.checked,
                                  },
                                })
                              }
                            />
                            <label className="sr-only" htmlFor={`pick-${item.definition_id}`}>
                              Include {item.name}
                            </label>
                          </td>
                          <th scope="row">
                            {item.name}
                            <div className="subtle">{item.hint}</div>
                          </th>
                          <td>
                            <Badge tone="accent">
                              {PERIOD_RULE_LABELS[item.period_rule] ?? item.period_rule}
                            </Badge>
                            {item.recurrence === 'monthly' && (
                              <div className="subtle">repeats monthly</div>
                            )}
                          </td>
                          <td className="num">
                            <label className="sr-only" htmlFor={`amt-${item.definition_id}`}>
                              Typical amount for {item.name}
                            </label>
                            <input
                              id={`amt-${item.definition_id}`}
                              type="text"
                              inputMode="decimal"
                              className="amount-input"
                              style={{ width: 120 }}
                              placeholder="optional"
                              disabled={!on}
                              value={draft.amounts[item.definition_id] ?? ''}
                              onChange={(event) =>
                                patch({
                                  amounts: {
                                    ...draft.amounts,
                                    [item.definition_id]: event.target.value,
                                  },
                                })
                              }
                            />
                          </td>
                        </tr>
                      )
                    })}
                    {draft.custom.map((entry, index) => (
                      <tr key={`custom-${index}`}>
                        <td>
                          <span aria-hidden="true">+</span>
                        </td>
                        <th scope="row">
                          <TextField
                            label={`Your own category ${index + 1}`}
                            value={entry.name}
                            onChange={(value) => {
                              const custom = [...draft.custom]
                              custom[index] = { ...custom[index], name: value }
                              patch({ custom })
                            }}
                            placeholder="Terrace garden, gym, parking…"
                            maxLength={200}
                          />
                        </th>
                        <td>
                          <SelectField
                            label="Charged in"
                            value={entry.period_rule}
                            options={[
                              { value: 'current_month' as PeriodRule, label: 'Current month' },
                              { value: 'previous_month' as PeriodRule, label: 'Previous month' },
                              {
                                value: 'transaction_date' as PeriodRule,
                                label: 'Month it was bought',
                              },
                            ]}
                            onChange={(value) => {
                              const custom = [...draft.custom]
                              custom[index] = { ...custom[index], period_rule: value }
                              patch({ custom })
                            }}
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn--sm btn--ghost"
                            onClick={() =>
                              patch({ custom: draft.custom.filter((_, i) => i !== index) })
                            }
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="row">
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() =>
                    patch({
                      custom: [
                        ...draft.custom,
                        { name: '', period_rule: 'current_month', recurrence: 'monthly' },
                      ],
                    })
                  }
                >
                  + Something else you pay for
                </button>
              </div>

              {touched && chosen.length === 0 && existingDefinitions.length === 0 && (
                <span className="field-error">Tick at least one thing.</span>
              )}

              <Callout tone="info">
                None of these is built into the application — each one becomes an ordinary
                record you can rename, re-rule, or retire. “Charged in” decides which
                month's statement picks it up: September's electricity bill arrives in
                October and is settled in the October statement.
              </Callout>

              {existingDefinitions.length > 0 && (
                <Callout tone="info" title="Already added">
                  {existingDefinitions.map((definition) => definition.name).join(', ')}. Anything
                  else ticked here is added as well. Unticking one does not remove it — retire
                  it from Expense categories once setup is finished.
                </Callout>
              )}
              <div className="form-actions">
                <span className="subtle" style={{ marginRight: 'auto' }}>
                  {chosen.length} selected
                  {amountTotal > 0 && <> · about {formatInr(amountTotal)} a month</>}
                </span>
                <button
                  type="button"
                  className="btn btn--ghost flex items-center gap-1.5 cursor-pointer"
                  onClick={() => goTo('residents')}
                >
                  <ChevronLeft className="w-4 h-4 shrink-0" aria-hidden="true" />
                  Back
                </button>
                <button type="submit" className="btn btn--primary" disabled={saveCategories.pending}>
                  {saveCategories.pending
                    ? 'Saving…'
                    : existingDefinitions.length === 0
                      ? `Add ${chosen.length} and continue`
                      : newCategories.length > 0
                        ? `Add ${newCategories.length} and continue`
                        : 'Continue'}
                </button>
              </div>
            </form>
          </div>
        </section>
      )}

      {(step === 'finish' || step === 'done') && (
        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                {data?.is_complete ? 'All set' : 'Step 4 · That is everything'}
              </h2>
              <div className="card-hint">
                You can change any of this at any time from Settings.
              </div>
            </div>
          </div>
          <div className="card-body">
            <div className="stack">
              <ErrorNotice error={finish.error} />
              <dl className="kv">
                <dt>Apartment</dt>
                <dd>{data?.apartment?.name ?? '—'}</dd>
                <dt>Residents</dt>
                <dd>
                  {data?.residents.map((r) => r.display_name).join(', ') || '—'}
                </dd>
                <dt>Categories</dt>
                <dd>{data?.definitions.map((d) => d.name).join(', ') || '—'}</dd>
              </dl>

              {!data?.is_complete && (
                <>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={draft.createStatement}
                      onChange={(event) => patch({ createStatement: event.target.checked })}
                    />
                    <span>
                      Open the {formatMonth(data?.current_month)} statement now
                      <span className="field-hint" style={{ display: 'block' }}>
                        So you can start recording straight away. You can also do this later
                        from Statements.
                      </span>
                    </span>
                  </label>

                  <Callout tone="info" title="What happens next">
                    Generate this month's recurring expenses in one press from Expense
                    categories, record anything else on the Expenses screen, then review and
                    finalize the statement when the month is done.
                  </Callout>

                  <div className="form-actions">
                    <button
                      type="button"
                      className="btn btn--ghost flex items-center gap-1.5 cursor-pointer"
                      onClick={() => goTo('categories')}
                    >
                      <ChevronLeft className="w-4 h-4 shrink-0" aria-hidden="true" />
                      Back
                    </button>
                    <button
                      type="button"
                      className="btn btn--primary"
                      disabled={finish.pending}
                      onClick={() => void finish.run(undefined)}
                    >
                      {finish.pending ? 'Finishing…' : 'Finish setup'}
                    </button>
                  </div>
                </>
              )}

              {data?.is_complete && (
                <div className="form-actions">
                  <Link className="btn" to="/residents">
                    Add more people
                  </Link>
                  <Link className="btn" to="/definitions">
                    Add more categories
                  </Link>
                  <Link className="btn btn--primary" to="/">
                    Go to the dashboard
                  </Link>
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

type SavedResident = SetupState['residents'][number]

/**
 * A resident the server already has: rename in place, or remove.
 *
 * Removing marks them as moved out. Nobody is deleted, so a name removed here
 * by mistake can be restored from the line underneath the list.
 */
function SavedResidentRow({
  resident,
  canRemove,
  onChanged,
}: {
  resident: SavedResident
  canRemove: boolean
  onChanged: () => void
}) {
  const [name, setName] = useState(resident.display_name)
  const path = `/api/residents/${encodeURIComponent(resident.resident_id)}`
  const rename = useMutation(
    (displayName: string) => api.patch(path, { display_name: displayName }),
    { onSuccess: onChanged },
  )
  const remove = useMutation(() => api.post(`${path}/deactivate`, {}), { onSuccess: onChanged })

  const commit = () => {
    const trimmed = name.trim()
    // An emptied box goes back to the saved name; a resident cannot be nameless.
    if (!trimmed) {
      setName(resident.display_name)
      return
    }
    if (trimmed !== resident.display_name) void rename.run(trimmed)
  }

  const error = rename.error ?? remove.error
  return (
    <li className="space-y-1">
      <div className="flex items-center gap-2.5 min-w-0">
        <Avatar name={name || resident.display_name} />
        <input
          type="text"
          className="min-w-0 flex-1"
          value={name}
          maxLength={200}
          aria-label={`Name of ${resident.display_name}`}
          aria-invalid={error ? true : undefined}
          disabled={rename.pending || remove.pending}
          onChange={(event) => setName(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return
            // Enter saves this name; it must not submit the whole step.
            event.preventDefault()
            event.currentTarget.blur()
          }}
        />
        <button
          type="button"
          className="btn btn--ghost p-2 rounded-md shrink-0"
          disabled={!canRemove || rename.pending || remove.pending}
          onClick={() => void remove.run(undefined)}
          aria-label={`Remove ${resident.display_name}`}
          title={canRemove ? 'Remove' : 'At least one resident is required'}
        >
          <X className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
      {error && <span className="field-error">{error.message}</span>}
    </li>
  )
}

function RemovedResidents({
  residents,
  onChanged,
}: {
  residents: SavedResident[]
  onChanged: () => void
}) {
  const restore = useMutation(
    (residentId: string) =>
      api.post(`/api/residents/${encodeURIComponent(residentId)}/reactivate`, {}),
    { onSuccess: onChanged },
  )
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-secondary)]">
      <span>Removed (kept as moved out):</span>
      {residents.map((resident) => (
        <button
          key={resident.resident_id}
          type="button"
          className="btn btn--ghost btn--sm flex items-center gap-1 cursor-pointer"
          disabled={restore.pending}
          onClick={() => void restore.run(resident.resident_id)}
          aria-label={`Restore ${resident.display_name}`}
        >
          <RotateCcw className="w-3 h-3 shrink-0" aria-hidden="true" />
          {resident.display_name}
        </button>
      ))}
      {restore.error && <span className="field-error">{restore.error.message}</span>}
    </div>
  )
}
