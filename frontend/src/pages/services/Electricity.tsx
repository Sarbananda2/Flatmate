/**
 * Electricity management.
 *
 * The flow is deliberately two-step: enter the actual utility bill and the
 * readings, **review** the calculated allocation, then commit. The bill total
 * and its itemised charges are authoritative — nothing here reconstructs a
 * tariff from kWh — and the review screen states plainly whether the component
 * totals and the resident shares both reconcile to the bill before anything is
 * saved.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Page, useApp } from '../../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  EmptyState,
  ErrorNotice,
  Modal,
  Money,
  Stat,
  StatementStatusBadge,
} from '../../components/Primitives'
import { DateField, KwhInput, MoneyInput, SelectField, TextAreaField, TextField } from '../../components/Form'
import { useToast } from '../../components/Toast'
import { api, downloadCsv } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import {
  PERIOD_RULE_HELP,
  PERIOD_RULE_LABELS,
  formatDate,
  formatInr,
  formatMonth,
  parseRupees,
  shiftMonth,
  todayIso,
  whToKwh,
} from '../../lib/format'
import type {
  ElectricityBillSummary,
  ElectricityPreview,
  ExpenseDefinition,
  PeriodRule,
  Resident,
  SavedElectricityBill,
  StatementRow,
  TapoSyncResult,
} from '../../api/types'

type EngineMethod = 'usage' | 'equal' | 'custom'

interface ComponentDraft {
  key: string
  id: string
  name: string
  raw: string
  paise: number
  ok: boolean
  method: EngineMethod
  customRaw: Record<string, string>
}

interface ReadingDraft {
  residentId: string
  raw: string
  wh: number
  ok: boolean
  device: string
}

const METHOD_OPTIONS: Array<{ value: EngineMethod; label: string }> = [
  { value: 'usage', label: 'By measured usage (smart-plug kWh)' },
  { value: 'equal', label: 'Equally among residents' },
  { value: 'custom', label: 'Custom amounts per resident' },
]

const PERIOD_RULE_OPTIONS: Array<{ value: PeriodRule; label: string }> = [
  { value: 'previous_month', label: PERIOD_RULE_LABELS.previous_month },
  { value: 'current_month', label: PERIOD_RULE_LABELS.current_month },
  { value: 'transaction_date', label: PERIOD_RULE_LABELS.transaction_date },
  { value: 'custom', label: PERIOD_RULE_LABELS.custom },
]

export default function ElectricityPage() {
  const toast = useToast()
  const { month } = useApp()
  const bills = useResource<{ items: ElectricityBillSummary[]; total: number }>(
    '/api/electricity/bills',
    { limit: 100 },
  )
  const consumption = useResource<{
    items: Array<{
      service_period: string
      resident_id: string
      display_name: string
      energy_wh: number
      kwh: string
      amount_paise: number
    }>
  }>('/api/electricity/consumption')
  const [entering, setEntering] = useState(false)
  const [viewing, setViewing] = useState<string | null>(null)

  return (
    <Page
      title="Electricity"
      subtitle="The actual utility bill and its charges are authoritative. Smart-plug readings are allocation weights you enter; the engine never reconstructs a tariff from kWh."
      breadcrumbs={[{ label: 'Housing & Utilities', to: '/services' }, { label: 'Electricity' }]}
      actions={
        <>
          <button
            type="button"
            className="btn"
            onClick={() => downloadCsv('/api/electricity/consumption/export/csv')}
          >
            Export consumption
          </button>
          <button type="button" className="btn btn--primary" onClick={() => setEntering(true)}>
            Enter a bill
          </button>
        </>
      }
    >
      <Callout tone="info" title="How electricity is handled">
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          <li>
            The bill total and each itemised charge are taken as given. Component amounts must add
            up to the bill exactly, and so must the resident shares.
          </li>
          <li>
            Each component can use a different rule, so energy charges can follow measured usage
            while fixed and common-area charges are split equally or by an agreed exception.
          </li>
          <li>
            <strong>Guests are not billed residents.</strong> Add a guest's kWh to the host's own
            reading; the host carries the cost.
          </li>
          <li>
            Readings take three decimal places of a kWh; money stays integer paise. Remainder paise
            go by largest remainder with the bill payer placed last, so the payer is not the one
            charged the spare paisa.
          </li>
          <li>
            A saved calculation is never changed silently. Correcting one needs a reason, keeps the
            superseded calculation in the audit trail, and is refused outright once the statement
            is closed.
          </li>
        </ul>
      </Callout>

      <Card title="Recorded bills" flush>
        <AsyncSection
          resource={bills}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              icon="⚡"
              title="No electricity bills yet"
              body="Enter the utility bill total, its itemised charges, and each resident's smart-plug reading."
              action={
                <button type="button" className="btn btn--primary" onClick={() => setEntering(true)}>
                  Enter the first bill
                </button>
              }
            />
          }
        >
          {(data) => (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Electricity bills</caption>
                <thead>
                  <tr>
                    <th scope="col">Bill date</th>
                    <th scope="col">Description</th>
                    <th scope="col">Bill period</th>
                    <th scope="col">Settles in</th>
                    <th scope="col">Paid by</th>
                    <th scope="col" className="num">
                      Total kWh
                    </th>
                    <th scope="col" className="num">
                      Bill total
                    </th>
                    <th scope="col">Statement</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((bill) => (
                    <tr key={bill.expense_id}>
                      <td className="nowrap">{formatDate(bill.expense_date)}</td>
                      <th scope="row">
                        {bill.description}
                        {bill.bill_number && (
                          <div>
                            <span className="id-chip">{bill.bill_number}</span>
                          </div>
                        )}
                      </th>
                      <td className="nowrap">
                        {bill.bill_period_start ? (
                          <>
                            {formatDate(bill.bill_period_start)}
                            <div className="subtle">to {formatDate(bill.bill_period_end)}</div>
                          </>
                        ) : (
                          formatMonth(bill.service_period)
                        )}
                      </td>
                      <td className="nowrap">{formatMonth(bill.settlement_period)}</td>
                      <td>{bill.payer_display_name ?? 'household pool'}</td>
                      <td className="num">{bill.total_kwh}</td>
                      <td className="num">
                        <Money paise={bill.amount_paise} />
                      </td>
                      <td>
                        {bill.statement_id ? (
                          <Link to={`/statements/${bill.statement_id}`}>
                            <StatementStatusBadge status={bill.statement_status} />
                          </Link>
                        ) : (
                          <StatementStatusBadge status="unattached" />
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() => setViewing(bill.expense_id)}
                        >
                          Details
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </Card>

      <Card
        title="Consumption and allocation history"
        hint="Per-resident kWh and the money allocated, by the month the electricity was consumed."
        flush
      >
        <AsyncSection
          resource={consumption}
          isEmpty={(data) => data.items.length === 0}
          empty={<EmptyState title="No readings recorded yet" />}
        >
          {(data) => (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Consumption history</caption>
                <thead>
                  <tr>
                    <th scope="col">Service month</th>
                    <th scope="col">Resident</th>
                    <th scope="col" className="num">
                      kWh
                    </th>
                    <th scope="col" className="num">
                      Allocated
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr key={`${row.service_period}-${row.resident_id}`}>
                      <td className="nowrap">{formatMonth(row.service_period)}</td>
                      <th scope="row">{row.display_name}</th>
                      <td className="num">{row.kwh}</td>
                      <td className="num">
                        <Money paise={row.amount_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </Card>

      {entering && (
        <BillWizard
          defaultSettlementPeriod={month}
          onClose={() => setEntering(false)}
          onSaved={() => {
            setEntering(false)
            bills.reload()
            consumption.reload()
            toast.success('Electricity bill saved', 'The allocation was committed as reviewed.')
          }}
        />
      )}
      {viewing && <BillDetail expenseId={viewing} onClose={() => setViewing(null)} />}
    </Page>
  )
}

function newComponent(index: number, residents: Resident[]): ComponentDraft {
  return {
    key: `component-${Date.now()}-${index}`,
    id: index === 0 ? 'energy' : `charge-${index + 1}`,
    name: index === 0 ? 'Energy charges' : '',
    raw: '',
    paise: 0,
    ok: false,
    method: index === 0 ? 'usage' : 'equal',
    customRaw: Object.fromEntries(residents.map((resident) => [resident.resident_id, ''])),
  }
}

function BillWizard({
  defaultSettlementPeriod,
  onClose,
  onSaved,
}: {
  defaultSettlementPeriod: string
  onClose: () => void
  onSaved: () => void
}) {
  const residents = useResource<{ items: Resident[] }>('/api/residents')
  const definitions = useResource<{ items: ExpenseDefinition[] }>('/api/expense-definitions')
  const statements = useResource<{ items: StatementRow[] }>('/api/statements', { limit: 100 })

  const [step, setStep] = useState<'enter' | 'review'>('enter')
  const [description, setDescription] = useState('')
  const [expenseDate, setExpenseDate] = useState(todayIso())
  const [totalRaw, setTotalRaw] = useState('')
  const [totalPaise, setTotalPaise] = useState(0)
  const [totalOk, setTotalOk] = useState(false)
  const [payer, setPayer] = useState('')
  const [provider, setProvider] = useState('Electricity provider')
  const [billNumber, setBillNumber] = useState('')
  const [definitionId, setDefinitionId] = useState('')
  const [periodRule, setPeriodRule] = useState<PeriodRule>('previous_month')
  const [servicePeriod, setServicePeriod] = useState(shiftMonth(defaultSettlementPeriod, -1))
  const [settlementPeriod, setSettlementPeriod] = useState(defaultSettlementPeriod)
  const [billStart, setBillStart] = useState('')
  const [billEnd, setBillEnd] = useState('')
  const [statementId, setStatementId] = useState('')
  const [notes, setNotes] = useState('')
  const [useComponents, setUseComponents] = useState(true)
  const [components, setComponents] = useState<ComponentDraft[]>([])
  const [readings, setReadings] = useState<ReadingDraft[]>([])
  const [touched, setTouched] = useState(false)
  const [preview, setPreview] = useState<ElectricityPreview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const activeResidents = residents.data?.items ?? []

  const { health } = useApp()
  const tapoConfigured = Boolean(health?.settings?.tapo_configured)
  const [tapoResult, setTapoResult] = useState<TapoSyncResult | null>(null)
  const tapoSync = useMutation(
    () =>
      api.post<TapoSyncResult>('/api/electricity/sync-tapo', {
        period_start: billStart,
        period_end: billEnd,
      }),
    {
      onSuccess: (result) => {
        setTapoResult(result)
        // Only the residents a plug answered for are filled in; the rest keep
        // whatever the operator typed.
        setReadings((current) =>
          current.map((item) => {
            const fetched = result.readings.find((row) => row.resident_id === item.residentId)
            return fetched
              ? { ...item, raw: fetched.kwh, wh: fetched.energy_wh, ok: true, device: fetched.device_label }
              : item
          }),
        )
      },
    },
  )

  useEffect(() => {
    if (activeResidents.length === 0) return
    if (readings.length === 0) {
      setReadings(
        activeResidents.map((resident) => ({
          residentId: resident.resident_id,
          raw: '',
          wh: 0,
          ok: false,
          device: 'Smart plug',
        })),
      )
    }
    if (components.length === 0) {
      setComponents([newComponent(0, activeResidents), newComponent(1, activeResidents)])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeResidents.length])

  const electricityDefinition = definitions.data?.items.find(
    (definition) => definition.default_allocation_method === 'measured',
  )
  useEffect(() => {
    if (!definitionId && electricityDefinition) {
      setDefinitionId(electricityDefinition.expense_definition_id)
      setPeriodRule(electricityDefinition.period_rule)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [electricityDefinition?.expense_definition_id])

  const componentTotal = components.reduce(
    (sum, component) => sum + (component.ok ? component.paise : 0),
    0,
  )
  const componentsBalance = !useComponents || componentTotal === totalPaise

  const buildPayload = () => ({
    total_amount_paise: totalPaise,
    payer_resident_id: payer || null,
    expense_date: expenseDate,
    smart_plug_readings: readings
      .filter((reading) => reading.raw.trim() !== '')
      .map((reading) => ({
        resident_id: reading.residentId,
        kwh: (reading.wh / 1000).toFixed(3),
        device_label: reading.device || 'Smart plug',
      })),
    components: useComponents
      ? components.map((component) => ({
          id: component.id.trim(),
          name: component.name.trim() || component.id.trim(),
          amount_paise: component.paise,
          allocation_method: component.method,
          ...(component.method === 'custom'
            ? {
                allocations_paise: Object.fromEntries(
                  activeResidents.map((resident) => {
                    const parsed = parseRupees(component.customRaw[resident.resident_id] ?? '', {
                      allowEmpty: true,
                    })
                    return [resident.resident_id, parsed.ok ? parsed.paise : 0]
                  }),
                ),
              }
            : {}),
        }))
      : null,
  })

  const runPreview = useMutation(
    () => api.post<ElectricityPreview>('/api/electricity/preview', buildPayload()),
    {
      onSuccess: (result) => {
        setPreview(result)
        setPreviewError(null)
        setStep('review')
      },
    },
  )

  const save = useMutation(
    () =>
      api.post<SavedElectricityBill>('/api/electricity/bills', {
        ...buildPayload(),
        description: description.trim(),
        provider_name: provider.trim() || 'Electricity provider',
        bill_number: billNumber.trim() || null,
        expense_definition_id: definitionId || null,
        period_rule: periodRule,
        service_period: servicePeriod || null,
        settlement_period: settlementPeriod || null,
        bill_period_start: billStart || null,
        bill_period_end: billEnd || null,
        statement_id: statementId || null,
        notes: notes.trim() || null,
      }),
    { onSuccess: onSaved },
  )

  const draftStatements = (statements.data?.items ?? []).filter(
    (statement) => statement.status === 'draft' && statement.settlement_month === settlementPeriod,
  )

  const descriptionError = touched && !description.trim() ? 'A description is required.' : null
  const totalError = touched && (!totalOk || totalPaise <= 0) ? 'Enter the bill total.' : null
  const dateError =
    billStart && billEnd && billEnd < billStart
      ? 'The bill period cannot end before it starts.'
      : null
  const readyToReview =
    Boolean(description.trim()) && totalOk && totalPaise > 0 && componentsBalance && !dateError

  if (step === 'review' && preview) {
    return (
      <Modal
        title="Review before committing"
        description="Calculated by the electricity engine from the bill you entered. Nothing is saved yet."
        onClose={onClose}
        wide
        footer={
          <>
            <span className="subtle" style={{ marginRight: 'auto' }}>
              {preview.reconciles
                ? 'Components and resident shares both reconcile to the bill ✓'
                : 'This calculation does not reconcile'}
            </span>
            <button type="button" className="btn" onClick={() => setStep('enter')}>
              ← Back to edit
            </button>
            <button
              type="button"
              className="btn btn--primary"
              disabled={save.pending || !preview.reconciles}
              onClick={() => void save.run(undefined)}
            >
              {save.pending ? 'Saving…' : 'Commit this calculation'}
            </button>
          </>
        }
      >
        <div className="stack">
          <ErrorNotice error={save.error} />
          <Callout tone={preview.reconciles ? 'ok' : 'danger'} title={
            preview.reconciles ? 'The calculation reconciles exactly' : 'The calculation does not reconcile'
          }>
            Bill total {formatInr(preview.total_amount_paise)} · components{' '}
            {formatInr(preview.components.reduce((sum, c) => sum + c.amount_paise, 0))} · resident
            shares {formatInr(preview.allocations.reduce((sum, a) => sum + a.amount_paise, 0))}.
          </Callout>

          <div className="grid grid--stats">
            <Stat label="Bill total" value={formatInr(preview.total_amount_paise)} note="As billed by the provider" />
            <Stat label="Total measured" value={`${preview.total_kwh} kWh`} note="Sum of the readings you entered" />
            <Stat label="Components" value={String(preview.components.length)} note="Each with its own allocation rule" />
          </div>

          <div className="card" style={{ borderRadius: 'var(--radius)' }}>
            <div className="card-header">
              <div className="card-title">Resident allocation</div>
            </div>
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Resident allocation</caption>
                <thead>
                  <tr>
                    <th scope="col">Resident</th>
                    <th scope="col" className="num">
                      kWh
                    </th>
                    <th scope="col" className="num">
                      Share of usage
                    </th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {preview.residents.map((resident) => (
                    <tr key={resident.resident_id}>
                      <th scope="row">
                        {resident.display_name}
                        {resident.is_payer && (
                          <>
                            {' '}
                            <Badge tone="accent">paid the bill</Badge>
                          </>
                        )}
                      </th>
                      <td className="num">{resident.kwh}</td>
                      <td className="num">{resident.usage_share_percent.toFixed(2)}%</td>
                      <td className="num">
                        <Money paise={resident.amount_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Total</td>
                    <td className="num">{preview.total_kwh}</td>
                    <td className="num">100.00%</td>
                    <td className="num">
                      <Money paise={preview.allocations.reduce((sum, a) => sum + a.amount_paise, 0)} />
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
            <div className="card-footer">{preview.rounding_rule}</div>
          </div>

          <div className="card" style={{ borderRadius: 'var(--radius)' }}>
            <div className="card-header">
              <div className="card-title">Component breakdown</div>
            </div>
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Component breakdown</caption>
                <thead>
                  <tr>
                    <th scope="col">Component</th>
                    <th scope="col">Rule</th>
                    {preview.residents.map((resident) => (
                      <th scope="col" className="num" key={resident.resident_id}>
                        {resident.display_name}
                      </th>
                    ))}
                    <th scope="col" className="num">
                      Component total
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {preview.components.map((component) => (
                    <tr key={component.component_id}>
                      <th scope="row">{component.name}</th>
                      <td>
                        {METHOD_OPTIONS.find((option) => option.value === component.allocation_method)
                          ?.label ?? component.allocation_method}
                      </td>
                      {preview.residents.map((resident) => (
                        <td className="num" key={resident.resident_id}>
                          <Money
                            paise={
                              component.allocations.find(
                                (allocation) => allocation.resident_id === resident.resident_id,
                              )?.amount_paise ?? 0
                            }
                          />
                        </td>
                      ))}
                      <td className="num">
                        <Money paise={component.amount_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={2}>Total</td>
                    {preview.residents.map((resident) => (
                      <td className="num" key={resident.resident_id}>
                        <Money paise={resident.amount_paise} />
                      </td>
                    ))}
                    <td className="num">
                      <Money paise={preview.total_amount_paise} />
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          {preview.warnings.length > 0 && (
            <Callout tone="warn" title="Warnings from the engine">
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {preview.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </Callout>
          )}

          <Callout tone="info" title="What will be saved">
            One expense of {formatInr(totalPaise)} for service period{' '}
            {formatMonth(servicePeriod)}, charged in the {formatMonth(settlementPeriod)} statement;
            one component row per bill charge; the resident allocations above; and the readings you
            entered. All of it in a single transaction.
            {payer && (
              <>
                {' '}
                {preview.residents.find((resident) => resident.is_payer)?.display_name} is recorded
                as having funded the bill. If you also record a provider advance for it, link that
                advance to this expense so they are not credited twice.
              </>
            )}
          </Callout>
        </div>
      </Modal>
    )
  }

  return (
    <Modal
      title="Enter an electricity bill"
      description="Enter the bill exactly as the provider issued it. The engine allocates that bill; it never estimates it."
      onClose={onClose}
      wide
      footer={
        <>
          <span className="subtle" style={{ marginRight: 'auto' }}>
            {useComponents
              ? componentsBalance
                ? `Components total ${formatInr(componentTotal)} ✓`
                : `Components total ${formatInr(componentTotal)} of ${formatInr(totalPaise)}`
              : 'The whole bill will be allocated as one usage-based component'}
          </span>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            form="electricity-form"
            className="btn btn--primary"
            disabled={runPreview.pending}
          >
            {runPreview.pending ? 'Calculating…' : 'Review the calculation →'}
          </button>
        </>
      }
    >
      <form
        id="electricity-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          setTouched(true)
          if (readyToReview) void runPreview.run(undefined)
        }}
      >
        <ErrorNotice error={runPreview.error} />
        {previewError && <Callout tone="danger">{previewError}</Callout>}

        {activeResidents.length === 0 && (
          <Callout tone="warn" title="No residents yet">
            Add residents before entering a bill — there is nobody to allocate it to.
          </Callout>
        )}

        <fieldset>
          <legend>The bill</legend>
          <div className="form-grid">
            <TextField
              label="Description"
              value={description}
              onChange={setDescription}
              required
              error={descriptionError}
              placeholder="August electricity bill"
              maxLength={500}
            />
            <MoneyInput
              label="Actual bill total"
              value={totalRaw}
              onChange={(raw, parsed) => {
                setTotalRaw(raw)
                setTotalOk(parsed.ok)
                setTotalPaise(parsed.ok ? parsed.paise : 0)
              }}
              required
              error={totalError}
              hint="Exactly as the provider billed it. This figure is authoritative."
            />
            <DateField
              label="Bill date"
              value={expenseDate}
              onChange={setExpenseDate}
              required
              hint="When the bill was issued or paid."
            />
            <TextField
              label="Provider"
              value={provider}
              onChange={setProvider}
              maxLength={200}
            />
            <TextField
              label="Bill number"
              value={billNumber}
              onChange={setBillNumber}
              hint="From the utility's bill, if you have it."
              maxLength={120}
            />
            <SelectField
              label="Paid by"
              value={payer}
              options={activeResidents.map((resident) => ({
                value: resident.resident_id,
                label: resident.display_name,
              }))}
              onChange={setPayer}
              placeholder="Paid from the household pool"
              hint="The payer is placed last when remainder paise are assigned."
            />
          </div>
        </fieldset>

        <fieldset>
          <legend>Periods</legend>
          <div className="form-grid">
            <SelectField
              label="Period rule"
              value={periodRule}
              options={PERIOD_RULE_OPTIONS}
              onChange={(value) => {
                setPeriodRule(value)
                if (value === 'previous_month') {
                  setServicePeriod(shiftMonth(expenseDate.slice(0, 7), -1))
                  setSettlementPeriod(expenseDate.slice(0, 7))
                }
              }}
              hint={PERIOD_RULE_HELP[periodRule]}
            />
            <TextField
              label="Service period"
              type="month"
              value={servicePeriod}
              onChange={setServicePeriod}
              hint="The month the electricity was consumed."
            />
            <TextField
              label="Settlement month"
              type="month"
              value={settlementPeriod}
              onChange={setSettlementPeriod}
              hint="The statement month that charges this bill."
            />
            <DateField
              label="Bill period start"
              value={billStart}
              onChange={setBillStart}
              hint="The utility's own reading window. Defaults to the service month."
            />
            <DateField
              label="Bill period end"
              value={billEnd}
              onChange={setBillEnd}
              error={dateError}
            />
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
                draftStatements.length
                  ? 'Only a draft statement for this settlement month can be chosen.'
                  : 'No draft statement for this settlement month yet.'
              }
            />
          </div>
        </fieldset>

        <fieldset>
          <legend>Smart-plug readings</legend>
          <div className="stack">
            <p className="field-hint" style={{ margin: 0 }}>
              Three decimal places of a kWh. <strong>Include any guest's usage in the host's
              reading</strong> — a guest is never charged directly. Leave a reading blank if there
              is no meter for that resident; usage-based components then allocate nothing to them.
            </p>
            {tapoConfigured && (
              <div className="row" style={{ flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={tapoSync.pending || !billStart || !billEnd}
                  onClick={() => void tapoSync.run(undefined)}
                >
                  {tapoSync.pending ? 'Reading plugs…' : 'Fetch from Tapo plugs'}
                </button>
                <span className="subtle">
                  {billStart && billEnd
                    ? 'Fills in the readings for the bill period. Review them before saving.'
                    : 'Enter the bill period first.'}
                </span>
              </div>
            )}
            <ErrorNotice error={tapoSync.error} />
            {tapoResult && !tapoResult.complete && (
              <Callout tone="warn" title="Some readings are incomplete">
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                  {tapoResult.errors.map((error) => (
                    <li key={error.ip_address}>
                      {error.device_label} ({error.resident_id}) could not be read: {error.message}
                    </li>
                  ))}
                  {tapoResult.readings
                    .filter((row) => !row.complete)
                    .map((row) => (
                      <li key={row.resident_id}>
                        {row.display_name}: the plug reported {row.days_reported} of{' '}
                        {row.days_expected} days, so {row.kwh} kWh is a minimum.
                      </li>
                    ))}
                </ul>
              </Callout>
            )}
            <div className="form-grid">
              {readings.map((reading, index) => {
                const resident = activeResidents.find(
                  (candidate) => candidate.resident_id === reading.residentId,
                )
                return (
                  <KwhInput
                    key={reading.residentId}
                    label={`${resident?.display_name ?? reading.residentId} (kWh)`}
                    value={reading.raw}
                    onChange={(raw, parsed) =>
                      setReadings((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, raw, wh: parsed.ok ? parsed.wh : 0, ok: parsed.ok }
                            : item,
                        ),
                      )
                    }
                  />
                )
              })}
            </div>
            <div className="subtle">
              Total measured:{' '}
              {whToKwh(readings.reduce((sum, reading) => sum + (reading.ok ? reading.wh : 0), 0))}{' '}
              kWh
            </div>
          </div>
        </fieldset>

        <fieldset>
          <legend>Bill charges</legend>
          <div className="stack">
            <label className="check">
              <input
                type="checkbox"
                checked={useComponents}
                onChange={(event) => setUseComponents(event.target.checked)}
              />
              <span>
                Enter the bill's itemised charges separately
                <span className="field-hint" style={{ display: 'block' }}>
                  Unchecked, the whole bill is allocated as one usage-based component.
                </span>
              </span>
            </label>

            {useComponents && (
              <>
                {components.map((component, index) => (
                  <div className="card" key={component.key} style={{ borderRadius: 'var(--radius)' }}>
                    <div className="card-header">
                      <div className="card-title">Charge {index + 1}</div>
                      {components.length > 1 && (
                        <button
                          type="button"
                          className="btn btn--sm btn--ghost"
                          onClick={() =>
                            setComponents((current) =>
                              current.filter((item) => item.key !== component.key),
                            )
                          }
                        >
                          Remove
                        </button>
                      )}
                    </div>
                    <div className="card-body">
                      <div className="form-grid">
                        <TextField
                          label="Charge ID"
                          value={component.id}
                          onChange={(value) =>
                            setComponents((current) =>
                              current.map((item) =>
                                item.key === component.key ? { ...item, id: value } : item,
                              ),
                            )
                          }
                          hint="Short, unique within this bill."
                          maxLength={64}
                        />
                        <TextField
                          label="Charge name"
                          value={component.name}
                          onChange={(value) =>
                            setComponents((current) =>
                              current.map((item) =>
                                item.key === component.key ? { ...item, name: value } : item,
                              ),
                            )
                          }
                          placeholder="Fixed / common-area charges"
                          maxLength={200}
                        />
                        <MoneyInput
                          label="Amount on the bill"
                          value={component.raw}
                          onChange={(raw, parsed) =>
                            setComponents((current) =>
                              current.map((item) =>
                                item.key === component.key
                                  ? {
                                      ...item,
                                      raw,
                                      paise: parsed.ok ? parsed.paise : 0,
                                      ok: parsed.ok,
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                        <SelectField
                          label="Allocation rule"
                          value={component.method}
                          options={METHOD_OPTIONS}
                          onChange={(value) =>
                            setComponents((current) =>
                              current.map((item) =>
                                item.key === component.key ? { ...item, method: value } : item,
                              ),
                            )
                          }
                          hint="Each charge can use a different rule — that is how agreed exceptions are configured."
                        />
                      </div>
                      {component.method === 'custom' && (
                        <div className="stack" style={{ marginTop: 'var(--space-4)' }}>
                          <span className="field-hint">
                            Exact amount per resident. These must add up to this charge.
                          </span>
                          <div className="form-grid">
                            {activeResidents.map((resident) => (
                              <MoneyInput
                                key={resident.resident_id}
                                label={resident.display_name}
                                hint={undefined}
                                value={component.customRaw[resident.resident_id] ?? ''}
                                onChange={(raw) =>
                                  setComponents((current) =>
                                    current.map((item) =>
                                      item.key === component.key
                                        ? {
                                            ...item,
                                            customRaw: {
                                              ...item.customRaw,
                                              [resident.resident_id]: raw,
                                            },
                                          }
                                        : item,
                                    ),
                                  )
                                }
                              />
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                <div className="row">
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() =>
                      setComponents((current) => [
                        ...current,
                        newComponent(current.length, activeResidents),
                      ])
                    }
                  >
                    + Add another charge
                  </button>
                </div>
                <Callout
                  tone={componentsBalance ? 'ok' : 'warn'}
                  title={
                    componentsBalance
                      ? 'The charges add up to the bill'
                      : 'The charges do not add up to the bill'
                  }
                >
                  Entered {formatInr(componentTotal)} of {formatInr(totalPaise)}.{' '}
                  {!componentsBalance && (
                    <>
                      Difference: <Money paise={componentTotal - totalPaise} sign="always" />. The
                      engine will reject a bill whose charges do not total it exactly.
                    </>
                  )}
                </Callout>
              </>
            )}
          </div>
        </fieldset>

        <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
      </form>
    </Modal>
  )
}

function BillDetail({ expenseId, onClose }: { expenseId: string; onClose: () => void }) {
  const resource = useResource<SavedElectricityBill>(
    `/api/electricity/bills/${encodeURIComponent(expenseId)}`,
  )
  return (
    <Modal
      title="Electricity bill"
      description={<span className="id-chip">{expenseId}</span>}
      onClose={onClose}
      wide
      footer={
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      <AsyncSection resource={resource}>
        {(data) => {
          const residents = Object.keys(data.resident_totals_paise).sort()
          return (
            <div className="stack">
              <Callout tone={data.reconciles ? 'ok' : 'danger'} title={
                data.reconciles
                  ? 'Components and resident shares reconcile to the bill'
                  : 'This saved bill does not reconcile'
              }>
                Bill total {formatInr(Number(data.expense.amount_paise))}; component total{' '}
                {formatInr(data.components.reduce((sum, row) => sum + row.amount_paise, 0))};
                resident shares{' '}
                {formatInr(Object.values(data.resident_totals_paise).reduce((a, b) => a + b, 0))}.
              </Callout>

              {data.bill && (
                <dl className="kv">
                  <dt>Bill period</dt>
                  <dd>
                    {formatDate(data.bill.bill_period_start)} – {formatDate(data.bill.bill_period_end)}
                  </dd>
                  <dt>Settlement month</dt>
                  <dd>{formatMonth(data.bill.settlement_period)}</dd>
                  <dt>Bill number</dt>
                  <dd className="mono">{data.bill.bill_number ?? '—'}</dd>
                  <dt>Provider</dt>
                  <dd>{data.bill.provider_name ?? '—'}</dd>
                </dl>
              )}

              <div className="card" style={{ borderRadius: 'var(--radius)' }}>
                <div className="card-header">
                  <div className="card-title">Resident shares</div>
                </div>
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Resident shares</caption>
                    <thead>
                      <tr>
                        <th scope="col">Resident</th>
                        <th scope="col" className="num">
                          Total share
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {residents.map((residentId) => (
                        <tr key={residentId}>
                          <th scope="row">{residentId}</th>
                          <td className="num">
                            <Money paise={data.resident_totals_paise[residentId]} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td>Total</td>
                        <td className="num">
                          <Money
                            paise={Object.values(data.resident_totals_paise).reduce(
                              (a, b) => a + b,
                              0,
                            )}
                          />
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </div>

              <div className="card" style={{ borderRadius: 'var(--radius)' }}>
                <div className="card-header">
                  <div className="card-title">Charges as billed</div>
                </div>
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Charges</caption>
                    <thead>
                      <tr>
                        <th scope="col">Charge</th>
                        <th scope="col">Rule</th>
                        <th scope="col" className="num">
                          Amount
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.components.map((component) => (
                        <tr key={component.component_id}>
                          <th scope="row">{component.name}</th>
                          <td>{component.allocation_method}</td>
                          <td className="num">
                            <Money paise={component.amount_paise} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="card" style={{ borderRadius: 'var(--radius)' }}>
                <div className="card-header">
                  <div>
                    <div className="card-title">Readings as entered</div>
                    <div className="card-hint">
                      Operator-supplied weights. Guest usage is inside the host's figure.
                    </div>
                  </div>
                </div>
                <div className="table-scroll">
                  <table className="data">
                    <caption className="sr-only">Readings</caption>
                    <thead>
                      <tr>
                        <th scope="col">Resident</th>
                        <th scope="col">Device</th>
                        <th scope="col">Period</th>
                        <th scope="col" className="num">
                          kWh
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.readings.map((reading) => (
                        <tr key={reading.reading_id}>
                          <th scope="row">{reading.resident_id}</th>
                          <td>{reading.device_label}</td>
                          <td className="nowrap">
                            {formatDate(reading.period_start)} – {formatDate(reading.period_end)}
                          </td>
                          <td className="num">{reading.kwh ?? whToKwh(reading.energy_wh)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <Callout tone="info" title="Correcting a saved bill">
                A saved calculation is never changed silently. Corrections go through the recalculate
                endpoint, which requires a reason, keeps the superseded calculation in the audit
                trail, and refuses once the statement is closed — then the correction belongs in a
                ledger adjustment.
              </Callout>
            </div>
          )
        }}
      </AsyncSection>
    </Modal>
  )
}
