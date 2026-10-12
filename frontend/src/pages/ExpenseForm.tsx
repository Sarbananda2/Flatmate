/**
 * The expense form.
 *
 * The allocation breakdown is calculated by the backend's own engine and shown
 * before anything is saved, so the preview can never disagree with what lands
 * in the database. The form will not submit unless the shares total the expense
 * amount exactly.
 *
 * It also keeps the two period fields visible and explains them, because
 * "which month does this belong to" is the question that decides which
 * statement charges the expense.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AsyncSection,
  Badge,
  Callout,
  ErrorNotice,
  Modal,
  Money,
} from '../components/Primitives'
import { DateField, MoneyInput, SelectField, TextAreaField, TextField } from '../components/Form'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import {
  ALLOCATION_METHOD_LABELS,
  PERIOD_RULE_HELP,
  PERIOD_RULE_LABELS,
  formatInr,
  formatMonth,
  paiseToInput,
  parseRupees,
  todayIso,
} from '../lib/format'
import type {
  AllocationPreview,
  ExpenseDefinition,
  ExpenseDetail,
  PeriodRule,
  PlannableMethod,
  Resident,
  StatementRow,
} from '../api/types'

const METHOD_OPTIONS: Array<{ value: PlannableMethod; label: string }> = [
  { value: 'equal', label: ALLOCATION_METHOD_LABELS.equal },
  { value: 'weighted', label: ALLOCATION_METHOD_LABELS.weighted },
  { value: 'custom', label: ALLOCATION_METHOD_LABELS.custom },
]

const PERIOD_RULE_OPTIONS: Array<{ value: PeriodRule; label: string }> = [
  { value: 'current_month', label: PERIOD_RULE_LABELS.current_month },
  { value: 'previous_month', label: PERIOD_RULE_LABELS.previous_month },
  { value: 'transaction_date', label: PERIOD_RULE_LABELS.transaction_date },
  { value: 'custom', label: PERIOD_RULE_LABELS.custom },
]

export function ExpenseFormDialog({
  expense,
  defaultSettlementPeriod,
  onClose,
  onSaved,
}: {
  expense?: ExpenseDetail
  defaultSettlementPeriod: string
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const editing = Boolean(expense)
  const residents = useResource<{ items: Resident[] }>('/api/residents')
  const definitions = useResource<{ items: ExpenseDefinition[] }>('/api/expense-definitions')
  const statements = useResource<{ items: StatementRow[] }>('/api/statements', { limit: 100 })

  const [description, setDescription] = useState(expense?.description ?? '')
  const [amountRaw, setAmountRaw] = useState(
    expense ? paiseToInput(expense.amount_paise) : '',
  )
  const [amountPaise, setAmountPaise] = useState(expense?.amount_paise ?? 0)
  const [amountOk, setAmountOk] = useState(Boolean(expense))
  const [expenseDate, setExpenseDate] = useState(expense?.expense_date ?? todayIso())
  const [definitionId, setDefinitionId] = useState(expense?.expense_definition_id ?? '')
  const [periodRule, setPeriodRule] = useState<PeriodRule>(expense?.period_rule ?? 'current_month')
  const [servicePeriod, setServicePeriod] = useState(expense?.service_period ?? '')
  const [settlementPeriod, setSettlementPeriod] = useState(
    expense?.settlement_period ?? defaultSettlementPeriod,
  )
  const [payer, setPayer] = useState(expense?.payer_resident_id ?? '')
  const [provider, setProvider] = useState(expense?.provider_name ?? '')
  const [reference, setReference] = useState(expense?.reference ?? '')
  const [notes, setNotes] = useState(expense?.notes ?? '')
  const [statementId, setStatementId] = useState(expense?.statement_id ?? '')
  const [reason, setReason] = useState('')
  const [touched, setTouched] = useState(false)

  const [method, setMethod] = useState<PlannableMethod>(() => {
    // A new expense defaults to an equal split among everybody, which is what a
    // shared household wants most of the time. An existing one keeps the method
    // it was saved with, so editing it does not silently re-spread the money.
    if (!expense) return 'equal'
    const existing = expense.allocation_method
    return existing === 'equal' || existing === 'weighted' ? existing : 'custom'
  })
  const [participants, setParticipants] = useState<string[]>(() =>
    expense ? expense.allocations.map((allocation) => allocation.resident_id) : [],
  )
  const [weights, setWeights] = useState<Record<string, string>>({})
  const [customShares, setCustomShares] = useState<Record<string, string>>(() => {
    if (!expense) return {}
    const next: Record<string, string> = {}
    for (const allocation of expense.allocations) {
      next[allocation.resident_id] = paiseToInput(allocation.amount_paise)
    }
    return next
  })

  const allResidents = residents.data?.items ?? []
  const activeResidents = useMemo(
    () => allResidents.filter((resident) => Boolean(resident.is_active)),
    [allResidents],
  )

  // Default participants to everyone active, once residents have loaded.
  useEffect(() => {
    if (!editing && participants.length === 0 && activeResidents.length > 0) {
      setParticipants(activeResidents.map((resident) => resident.resident_id))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeResidents.length])

  const chosenDefinition = definitions.data?.items.find(
    (definition) => definition.expense_definition_id === definitionId,
  )

  // Adopt the definition's rule and defaults when one is picked.
  useEffect(() => {
    if (!chosenDefinition || editing) return
    setPeriodRule(chosenDefinition.period_rule)
    if (!description.trim()) setDescription(chosenDefinition.name)
    if (chosenDefinition.default_provider_name && !provider) {
      setProvider(chosenDefinition.default_provider_name)
    }
    if (
      chosenDefinition.default_allocation_method === 'equal' ||
      chosenDefinition.default_allocation_method === 'weighted' ||
      chosenDefinition.default_allocation_method === 'custom'
    ) {
      setMethod(chosenDefinition.default_allocation_method)
    }
    if (chosenDefinition.default_participants?.length) {
      setParticipants(chosenDefinition.default_participants)
    }
    if (chosenDefinition.default_amount_paise != null && !amountRaw.trim()) {
      setAmountRaw(paiseToInput(chosenDefinition.default_amount_paise))
      setAmountPaise(chosenDefinition.default_amount_paise)
      setAmountOk(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [definitionId])

  const customTotalPaise = useMemo(() => {
    let total = 0
    for (const raw of Object.values(customShares)) {
      const parsed = parseRupees(raw, { allowEmpty: true })
      if (parsed.ok) total += parsed.paise
    }
    return total
  }, [customShares])

  const customSharesPaise = useMemo(() => {
    const shares: Record<string, number> = {}
    for (const resident of allResidents) {
      const parsed = parseRupees(customShares[resident.resident_id] ?? '', { allowEmpty: true })
      if (parsed.ok && parsed.paise !== 0) shares[resident.resident_id] = parsed.paise
    }
    return shares
  }, [customShares, allResidents])

  const weightsNumeric = useMemo(() => {
    const parsed: Record<string, number> = {}
    for (const resident of participants) {
      const value = Number(weights[resident] ?? '1')
      parsed[resident] = Number.isInteger(value) && value >= 0 ? value : 0
    }
    return parsed
  }, [weights, participants])

  // Ask the backend for the breakdown. Debounced by the request itself being
  // cheap and local; the key below keeps it from firing on every keystroke of
  // an unrelated field.
  const previewKey = JSON.stringify({
    amountPaise,
    method,
    participants,
    weightsNumeric,
    customSharesPaise,
    payer,
  })
  const [previewData, setPreviewData] = useState<AllocationPreview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewing, setPreviewing] = useState(false)

  useEffect(() => {
    if (!amountOk || amountPaise <= 0) {
      setPreviewData(null)
      setPreviewError(null)
      return
    }
    if (method === 'equal' && participants.length === 0) {
      setPreviewData(null)
      setPreviewError('Choose at least one participant.')
      return
    }
    if (method === 'custom' && Object.keys(customSharesPaise).length === 0) {
      setPreviewData(null)
      setPreviewError('Enter at least one resident share.')
      return
    }
    let cancelled = false
    const timer = window.setTimeout(() => {
      setPreviewing(true)
      api
        .post<AllocationPreview>('/api/expenses/allocation-preview', {
          amount_paise: amountPaise,
          method,
          participants: method === 'equal' ? participants : null,
          weights: method === 'weighted' ? weightsNumeric : null,
          shares_paise: method === 'custom' ? customSharesPaise : null,
          payer_resident_id: payer || null,
        })
        .then((result) => {
          if (cancelled) return
          setPreviewData(result)
          setPreviewError(null)
        })
        .catch((cause) => {
          if (cancelled) return
          setPreviewData(null)
          setPreviewError(cause instanceof Error ? cause.message : String(cause))
        })
        .finally(() => {
          if (!cancelled) setPreviewing(false)
        })
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey, amountOk])

  const draftStatements = (statements.data?.items ?? []).filter(
    (statement) => statement.status === 'draft',
  )
  const matchingStatement = draftStatements.find(
    (statement) => statement.settlement_month === settlementPeriod,
  )

  const needsReason =
    editing && expense?.statement_status === 'finalized_correction_window'

  const save = useMutation(
    () => {
      const body: Record<string, unknown> = {
        description: description.trim(),
        amount_paise: amountPaise,
        expense_date: expenseDate,
        provider_name: provider.trim() || null,
        reference: reference.trim() || null,
        notes: notes.trim() || null,
        payer_resident_id: payer || null,
        allocation_method: method,
        allocations_paise:
          method === 'custom' ? (previewData?.shares_paise ?? customSharesPaise) : null,
        participants: method === 'equal' ? participants : null,
        weights: method === 'weighted' ? weightsNumeric : null,
      }
      if (editing) {
        body.settlement_period = settlementPeriod || null
        body.service_period = servicePeriod || null
        if (needsReason) body.reason = reason.trim()
        if (method !== 'custom') body.allocations_paise = previewData?.shares_paise ?? null
        return api.patch<ExpenseDetail>(
          `/api/expenses/${encodeURIComponent(expense!.expense_id)}`,
          body,
        )
      }
      return api.post<ExpenseDetail>('/api/expenses', {
        ...body,
        expense_definition_id: definitionId || null,
        period_rule: periodRule,
        service_period: servicePeriod || null,
        settlement_period: periodRule === 'custom' ? settlementPeriod : settlementPeriod || null,
        statement_id: statementId || null,
      })
    },
    { onSuccess: () => onSaved(editing ? 'Expense updated' : 'Expense recorded') },
  )

  const descriptionError = touched && !description.trim() ? 'A description is required.' : null
  const amountError = touched && (!amountOk || amountPaise <= 0) ? 'Enter an amount above zero.' : null
  const reasonError = touched && needsReason && !reason.trim()
    ? 'A reason is required while the statement is in its correction window.'
    : null
  const allocationBalanced = previewData !== null && previewData.total_paise === amountPaise
  const valid =
    Boolean(description.trim()) &&
    amountOk &&
    amountPaise > 0 &&
    allocationBalanced &&
    !reasonError &&
    (periodRule !== 'custom' || Boolean(settlementPeriod))

  const residentName = (id: string) =>
    allResidents.find((resident) => resident.resident_id === id)?.display_name ?? id

  return (
    <Modal
      title={editing ? `Edit ${expense!.description}` : 'Record an expense'}
      description="An expense is a cost the household incurred. Who funded it is recorded separately, below — that is not a second expense."
      onClose={onClose}
      wide
      footer={
        <>
          <span className="subtle" style={{ marginRight: 'auto' }}>
            {previewing
              ? 'Calculating shares…'
              : allocationBalanced
                ? `Shares total ${formatInr(previewData!.total_paise)} ✓`
                : 'Shares must total the expense amount'}
          </span>
          <button type="button" className="btn" onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button
            type="submit"
            form="expense-form"
            className="btn btn--primary"
            disabled={save.pending}
          >
            {save.pending ? 'Saving…' : editing ? 'Save changes' : 'Record expense'}
          </button>
        </>
      }
    >
      <form
        id="expense-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (valid) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />

        {needsReason && (
          <Callout tone="warn" title="This statement is inside its correction window">
            The change will be recorded as a numbered statement revision, which needs a reason.
          </Callout>
        )}

        <fieldset>
          <legend>What was bought</legend>
          <div className="form-grid">
            {!editing && (
              <SelectField
                label="Category"
                value={definitionId}
                options={(definitions.data?.items ?? []).map((definition) => ({
                  value: definition.expense_definition_id,
                  label: `${definition.name} · ${definition.category}`,
                }))}
                onChange={setDefinitionId}
                placeholder="No category"
                hint="Picking one adopts its period rule and defaults."
              />
            )}
            <TextField
              label="Description"
              value={description}
              onChange={setDescription}
              required
              error={descriptionError}
              maxLength={500}
            />
            <MoneyInput
              label="Amount"
              value={amountRaw}
              onChange={(raw, parsed) => {
                setAmountRaw(raw)
                setAmountOk(parsed.ok)
                setAmountPaise(parsed.ok ? parsed.paise : 0)
              }}
              required
              error={amountError}
            />
            <DateField
              label="Transaction date"
              value={expenseDate}
              onChange={setExpenseDate}
              required
              hint="When the money actually moved."
            />
            <TextField
              label="Provider"
              value={provider}
              onChange={setProvider}
              hint="Who was paid, for your records."
              maxLength={200}
            />
            <TextField
              label="Reference"
              value={reference}
              onChange={setReference}
              hint="Bill or receipt number, if you have one."
              maxLength={200}
            />
          </div>
        </fieldset>

        <fieldset>
          <legend>Accounting period</legend>
          <div className="stack">
            <div className="form-grid">
              {!editing && (
                <SelectField
                  label="Period rule"
                  value={periodRule}
                  options={PERIOD_RULE_OPTIONS}
                  onChange={setPeriodRule}
                  hint={PERIOD_RULE_HELP[periodRule]}
                />
              )}
              <TextField
                label="Service period"
                type="month"
                value={servicePeriod}
                onChange={setServicePeriod}
                hint="The month the money was for. Leave empty to let the rule decide."
              />
              <TextField
                label="Settlement month"
                type="month"
                value={settlementPeriod}
                onChange={setSettlementPeriod}
                required={periodRule === 'custom'}
                hint="The statement month that charges this expense."
              />
            </div>
            {!editing && (
              <SelectField
                label="Attach to statement"
                value={statementId}
                options={draftStatements.map((statement) => ({
                  value: statement.statement_id,
                  label: `${formatMonth(statement.settlement_month)} (draft)`,
                }))}
                onChange={setStatementId}
                placeholder="Leave unattached for now"
                hint={
                  matchingStatement
                    ? `There is a draft statement for ${formatMonth(settlementPeriod)}. An expense can only be attached to the statement for its own settlement month.`
                    : 'No draft statement exists for this settlement month yet. You can attach it later from the statement screen.'
                }
              />
            )}
          </div>
        </fieldset>

        <fieldset>
          <legend>Who funded it</legend>
          <div className="stack">
            <SelectField
              label="Paid by"
              value={payer}
              options={allResidents.map((resident) => ({
                value: resident.resident_id,
                label: resident.display_name,
              }))}
              onChange={setPayer}
              placeholder="Paid from the household pool"
              hint="Funding is not a second expense. If you also record a provider advance for this bill, link it to this expense so the payer is not credited twice."
            />
            {payer && (
              <Callout tone="info">
                {residentName(payer)} will be credited {formatInr(amountPaise)} for funding this
                expense, and still charged only their own share of it. Unavoidable remainder paise
                in an equal split go to the others first, so the payer is not charged the spare
                paisa.
              </Callout>
            )}
          </div>
        </fieldset>

        <fieldset>
          <legend>How it is split</legend>
          <div className="stack">
            <SelectField
              label="Allocation method"
              value={method}
              options={METHOD_OPTIONS}
              onChange={setMethod}
              hint={
                method === 'equal'
                  ? 'Split equally among the participants you choose.'
                  : method === 'weighted'
                    ? 'Split in proportion to whole-number weights.'
                    : 'Enter the exact amount for each resident. They must total the expense.'
              }
            />

            {method === 'equal' && (
              <div className="field">
                <span className="field-label">Participants</span>
                <span className="field-hint">
                  A resident who does not take part gets an explicit zero share, so the allocation
                  is never silently incomplete.
                </span>
                <div className="row">
                  {allResidents.map((resident) => (
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
                      <span>
                        {resident.display_name}
                        {!resident.is_active && (
                          <>
                            {' '}
                            <Badge tone="neutral">moved out</Badge>
                          </>
                        )}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {method === 'weighted' && (
              <div className="stack">
                <div className="field">
                  <span className="field-label">Participants and weights</span>
                  <span className="field-hint">
                    Whole numbers. Largest-remainder rounding; ties break by resident ID. The payer
                    rounding preference applies to equal splits only.
                  </span>
                </div>
                <div className="form-grid">
                  {allResidents.map((resident) => (
                    <div className="field" key={resident.resident_id}>
                      <label className="field-label" htmlFor={`weight-${resident.resident_id}`}>
                        {resident.display_name}
                      </label>
                      <input
                        id={`weight-${resident.resident_id}`}
                        type="number"
                        min={0}
                        step={1}
                        className="amount-input"
                        value={weights[resident.resident_id] ?? (participants.includes(resident.resident_id) ? '1' : '0')}
                        onChange={(event) => {
                          const value = event.target.value
                          setWeights((current) => ({ ...current, [resident.resident_id]: value }))
                          setParticipants((current) =>
                            Number(value) > 0
                              ? current.includes(resident.resident_id)
                                ? current
                                : [...current, resident.resident_id]
                              : current.filter((id) => id !== resident.resident_id),
                          )
                        }}
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {method === 'custom' && (
              <div className="stack">
                <div className="form-grid">
                  {allResidents.map((resident) => (
                    <MoneyInput
                      key={resident.resident_id}
                      label={resident.display_name}
                      value={customShares[resident.resident_id] ?? ''}
                      hint={undefined}
                      onChange={(raw) =>
                        setCustomShares((current) => ({ ...current, [resident.resident_id]: raw }))
                      }
                    />
                  ))}
                </div>
                <Callout
                  tone={customTotalPaise === amountPaise ? 'ok' : 'warn'}
                  title={
                    customTotalPaise === amountPaise
                      ? 'The shares add up exactly'
                      : 'The shares do not add up yet'
                  }
                >
                  Entered {formatInr(customTotalPaise)} of {formatInr(amountPaise)}.{' '}
                  {customTotalPaise !== amountPaise && (
                    <>
                      Difference: <Money paise={customTotalPaise - amountPaise} sign="always" />.
                    </>
                  )}
                </Callout>
              </div>
            )}

            {previewError && <Callout tone="warn">{previewError}</Callout>}

            {previewData && (
              <div className="card" style={{ borderRadius: 'var(--radius)' }}>
                <div className="card-header">
                  <div>
                    <div className="card-title">Allocation breakdown</div>
                    <div className="card-hint">
                      Calculated by the same engine that will save it. Nothing is written yet.
                    </div>
                  </div>
                  <Badge tone={allocationBalanced ? 'ok' : 'danger'}>
                    {allocationBalanced ? 'Balances exactly' : 'Does not balance'}
                  </Badge>
                </div>
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Calculated resident shares</caption>
                    <thead>
                      <tr>
                        <th scope="col">Resident</th>
                        <th scope="col">Takes part</th>
                        <th scope="col" className="num">
                          Share
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewData.lines.map((line) => (
                        <tr key={line.resident_id}>
                          <th scope="row">
                            {line.display_name}
                            {line.resident_id === payer && (
                              <>
                                {' '}
                                <Badge tone="accent">funded this</Badge>
                              </>
                            )}
                          </th>
                          <td>{line.is_participant ? 'Yes' : 'No — zero share'}</td>
                          <td className="num">
                            <Money paise={line.amount_paise} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={2}>Total allocated</td>
                        <td className="num">
                          <Money
                            paise={previewData.total_paise}
                            tone={allocationBalanced ? 'plain' : 'owed-by'}
                          />
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
                {previewData.warnings.length > 0 && (
                  <div className="card-footer">
                    {previewData.warnings.map((warning) => (
                      <span key={warning}>{warning}</span>
                    ))}
                  </div>
                )}
                {typeof previewData.basis.rounding_rule === 'string' && (
                  <div className="card-footer">{String(previewData.basis.rounding_rule)}</div>
                )}
              </div>
            )}
          </div>
        </fieldset>

        {needsReason && (
          <TextField
            label="Reason for this correction"
            value={reason}
            onChange={setReason}
            required
            error={reasonError}
            hint="Stored on the statement revision and in the audit trail."
            maxLength={500}
          />
        )}

        <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
      </form>
    </Modal>
  )
}

/** Loads one expense, then opens the form for it. */
export function EditExpenseDialog({
  expenseId,
  defaultSettlementPeriod,
  onClose,
  onSaved,
}: {
  expenseId: string
  defaultSettlementPeriod: string
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const resource = useResource<ExpenseDetail>(`/api/expenses/${encodeURIComponent(expenseId)}`)
  return (
    <AsyncSection resource={resource}>
      {(data) => (
        <ExpenseFormDialog
          expense={data}
          defaultSettlementPeriod={defaultSettlementPeriod}
          onClose={onClose}
          onSaved={onSaved}
        />
      )}
    </AsyncSection>
  )
}
