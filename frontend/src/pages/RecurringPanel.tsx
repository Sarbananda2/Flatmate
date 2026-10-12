/**
 * Recurring expense generation: preview, adjust amounts, then confirm.
 *
 * Nothing is created until the operator presses the confirm button, and a
 * definition that already has an expense for the month is shown as such rather
 * than being generated again. The backend also refuses a duplicate outright, so
 * pressing the button twice cannot double-charge the household.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Money,
} from '../components/Primitives'
import { useToast } from '../components/Toast'
import { api } from '../api/client'
import { useMutation, useResource } from '../lib/useApi'
import {
  PERIOD_RULE_LABELS,
  formatDate,
  formatInr,
  formatMonth,
  paiseToInput,
  parseRupees,
} from '../lib/format'
import type { RecurringPreview } from '../api/types'

interface Draft {
  include: boolean
  raw: string
  paise: number | null
  error: string | null
}

export function RecurringPanel({ onGenerated }: { onGenerated?: () => void }) {
  const { month } = useApp()
  const toast = useToast()
  const preview = useResource<RecurringPreview>('/api/recurring/preview', {
    settlement_period: month,
  })
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    const lines = preview.data?.lines ?? []
    const next: Record<string, Draft> = {}
    for (const line of lines) {
      if (line.status === 'already_exists' || line.status === 'not_recurring') continue
      const raw = line.suggested_amount_paise != null ? paiseToInput(line.suggested_amount_paise) : ''
      next[line.expense_definition_id] = {
        include: line.status === 'eligible',
        raw,
        paise: line.suggested_amount_paise ?? null,
        error: null,
      }
    }
    setDrafts(next)
  }, [preview.data])

  const generate = useMutation(
    (body: unknown) =>
      api.post<{ created_count: number; skipped_count: number; created_total_paise: number }>(
        '/api/recurring/generate',
        body,
      ),
    {
      onSuccess: (result) => {
        setConfirming(false)
        preview.reload()
        onGenerated?.()
        toast.success(
          result.created_count
            ? `Generated ${result.created_count} expense${result.created_count === 1 ? '' : 's'}`
            : 'Nothing new was generated',
          result.skipped_count
            ? `${result.skipped_count} already existed and were skipped.`
            : `Total ${formatInr(result.created_total_paise)}.`,
        )
      },
    },
  )

  const selected = useMemo(
    () =>
      Object.entries(drafts).filter(
        ([, draft]) => draft.include && draft.paise !== null && !draft.error,
      ),
    [drafts],
  )
  const selectedTotal = selected.reduce((sum, [, draft]) => sum + (draft.paise ?? 0), 0)

  const update = (id: string, patch: Partial<Draft>) =>
    setDrafts((current) => ({ ...current, [id]: { ...current[id], ...patch } }))

  return (
    <Card
      title={`Recurring expenses for ${formatMonth(month)}`}
      hint="A preview of what would be created. Amounts are suggestions you can change every month; nothing is recorded until you confirm."
      flush
      actions={
        <button type="button" className="btn btn--sm" onClick={preview.reload}>
          Refresh preview
        </button>
      }
      footer={
        <>
          <span>
            {selected.length} selected · total <Money paise={selectedTotal} />
          </span>
          <span className="topbar-spacer" />
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={selected.length === 0}
            onClick={() => setConfirming(true)}
          >
            Generate {selected.length || ''} expense{selected.length === 1 ? '' : 's'}…
          </button>
        </>
      }
    >
      <AsyncSection resource={preview}>
        {(data) => (
          <div className="stack">
            {data.statement_status && data.statement_status !== 'draft' && (
              <div style={{ padding: 'var(--space-4) var(--space-5) 0' }}>
                <Callout tone="warn" title="This month's statement is not a draft">
                  Generated expenses cannot be attached to a{' '}
                  {data.statement_status === 'closed' ? 'closed' : 'finalized'} statement. They will
                  be created unattached, and you can move them to the right statement afterwards.
                </Callout>
              </div>
            )}
            {data.lines.length === 0 ? (
              <EmptyState
                title="No categories to generate"
                body="Mark a category as recurring monthly and give it a default amount."
                action={
                  <Link className="btn" to="/definitions">
                    Open expense categories
                  </Link>
                }
              />
            ) : (
              <div className="table-scroll">
                <table className="data">
                  <caption className="sr-only">Recurring generation preview</caption>
                  <thead>
                    <tr>
                      <th scope="col">Include</th>
                      <th scope="col">Category</th>
                      <th scope="col">Period rule</th>
                      <th scope="col">Service · settlement</th>
                      <th scope="col">Transaction date</th>
                      <th scope="col" className="num">
                        Amount this month
                      </th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lines.map((line) => {
                      const draft = drafts[line.expense_definition_id]
                      const generatable =
                        line.status === 'eligible' || line.status === 'needs_amount'
                      return (
                        <tr key={line.expense_definition_id}>
                          <td>
                            {generatable ? (
                              <>
                                <input
                                  type="checkbox"
                                  id={`include-${line.expense_definition_id}`}
                                  checked={draft?.include ?? false}
                                  onChange={(event) =>
                                    update(line.expense_definition_id, {
                                      include: event.target.checked,
                                    })
                                  }
                                  style={{ width: 'auto' }}
                                />
                                <label
                                  className="sr-only"
                                  htmlFor={`include-${line.expense_definition_id}`}
                                >
                                  Include {line.name}
                                </label>
                              </>
                            ) : (
                              <span aria-hidden="true" className="subtle">
                                —
                              </span>
                            )}
                          </td>
                          <th scope="row">
                            {line.name}
                            <div className="subtle">{line.category}</div>
                          </th>
                          <td>
                            <Badge tone="accent">
                              {PERIOD_RULE_LABELS[line.period_rule] ?? line.period_rule}
                            </Badge>
                          </td>
                          <td className="nowrap">
                            {formatMonth(line.service_period)}
                            <div className="subtle">→ {formatMonth(line.settlement_period)}</div>
                          </td>
                          <td className="nowrap">{formatDate(line.transaction_date)}</td>
                          <td className="num">
                            {generatable ? (
                              <>
                                <label
                                  className="sr-only"
                                  htmlFor={`amount-${line.expense_definition_id}`}
                                >
                                  Amount for {line.name} in rupees
                                </label>
                                <input
                                  id={`amount-${line.expense_definition_id}`}
                                  type="text"
                                  inputMode="decimal"
                                  className="amount-input"
                                  style={{ width: 120 }}
                                  value={draft?.raw ?? ''}
                                  aria-invalid={draft?.error ? true : undefined}
                                  placeholder="0.00"
                                  onChange={(event) => {
                                    const parsed = parseRupees(event.target.value, {
                                      allowEmpty: true,
                                    })
                                    update(line.expense_definition_id, {
                                      raw: event.target.value,
                                      paise: event.target.value.trim()
                                        ? parsed.ok
                                          ? parsed.paise
                                          : null
                                        : null,
                                      error: event.target.value.trim() && !parsed.ok
                                        ? parsed.error ?? 'Invalid amount'
                                        : null,
                                    })
                                  }}
                                />
                                {draft?.error && <div className="field-error">{draft.error}</div>}
                              </>
                            ) : line.existing_expense_id ? (
                              <span className="subtle">already recorded</span>
                            ) : (
                              <span className="subtle">—</span>
                            )}
                          </td>
                          <td>
                            {line.status === 'eligible' && <Badge tone="ok">Ready</Badge>}
                            {line.status === 'needs_amount' && (
                              <Badge tone="warn" title={line.reason ?? undefined}>
                                Needs an amount
                              </Badge>
                            )}
                            {line.status === 'already_exists' && (
                              <Badge tone="neutral" title={line.reason ?? undefined}>
                                Already recorded
                              </Badge>
                            )}
                            {line.status === 'not_recurring' && (
                              <Badge tone="neutral">One-off</Badge>
                            )}
                            {line.warnings.map((warning) => (
                              <div className="subtle" key={warning}>
                                {warning}
                              </div>
                            ))}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </AsyncSection>

      {confirming && (
        <ConfirmDialog
          title={`Generate ${selected.length} expense${selected.length === 1 ? '' : 's'}?`}
          confirmLabel="Generate expenses"
          pending={generate.pending}
          error={generate.error}
          onCancel={() => setConfirming(false)}
          onConfirm={() =>
            void generate.run({
              settlement_period: month,
              confirm: true,
              statement_id: preview.data?.statement_status === 'draft'
                ? preview.data?.statement_id
                : null,
              attach_to_statement: preview.data?.statement_status === 'draft',
              lines: selected.map(([definitionId, draft]) => ({
                expense_definition_id: definitionId,
                amount_paise: draft.paise,
              })),
            })
          }
          body={
            <div className="stack">
              <p style={{ margin: 0 }}>
                This creates {selected.length} expense
                {selected.length === 1 ? '' : 's'} totalling <Money paise={selectedTotal} /> in the{' '}
                {formatMonth(month)} settlement period. They stay fully editable while the
                statement is a draft.
              </p>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {selected.map(([definitionId, draft]) => {
                  const line = preview.data?.lines.find(
                    (item) => item.expense_definition_id === definitionId,
                  )
                  return (
                    <li key={definitionId}>
                      {line?.name ?? definitionId} — <Money paise={draft.paise ?? 0} />
                    </li>
                  )
                })}
              </ul>
            </div>
          }
        />
      )}
      <ErrorNotice error={null} />
    </Card>
  )
}
