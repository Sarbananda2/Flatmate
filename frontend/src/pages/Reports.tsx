/**
 * Reports and exports.
 *
 * Each report reads the same records every other screen reads, so a figure in a
 * CSV always matches the figure on screen. A statement export is built from the
 * statement's stored snapshot when one exists, so an exported finalized
 * statement reproduces what was finalized rather than today's ledger.
 */
import { useState } from 'react'
import { Page, useApp } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  EmptyState,
  Money,
} from '../components/Primitives'
import { CheckboxField, SelectField } from '../components/Form'
import { csvUrl, downloadCsv } from '../api/client'
import { useResource } from '../lib/useApi'
import { formatMonth } from '../lib/format'
import type { Report, ReportDescriptor, StatementRow } from '../api/types'

export default function ReportsPage() {
  const { month } = useApp()
  const descriptors = useResource<{ items: ReportDescriptor[] }>('/api/reports')
  const statements = useResource<{ items: StatementRow[] }>('/api/statements', { limit: 100 })

  const [kind, setKind] = useState('monthly_household_expenses')
  const [usePeriod, setUsePeriod] = useState(false)
  const [statementId, setStatementId] = useState('')

  const descriptor = descriptors.data?.items.find((item) => item.kind === kind)

  const query = {
    settlement_period: descriptor?.supports_period && usePeriod ? month : undefined,
    statement_id: descriptor?.needs_statement ? statementId || undefined : undefined,
  }
  const ready = !descriptor?.needs_statement || Boolean(statementId)
  const report = useResource<Report>(ready ? `/api/reports/${kind}` : null, query)

  return (
    <Page
      title="Reports & exports"
      subtitle="Every report is built from the same records the rest of the application reads, so the figures always agree."
      breadcrumbs={[{ label: 'Review' }, { label: 'Reports & exports' }]}
      actions={
        <button
          type="button"
          className="btn btn--primary"
          disabled={!ready}
          onClick={() => downloadCsv(`/api/reports/${kind}/csv`, query)}
        >
          Download CSV
        </button>
      }
    >
      <Card title="Choose a report">
        <div className="filters">
          <SelectField
            label="Report"
            value={kind}
            options={(descriptors.data?.items ?? []).map((item) => ({
              value: item.kind,
              label: item.label,
            }))}
            onChange={(value) => {
              setKind(value)
              setStatementId('')
            }}
          />
          {descriptor?.supports_period && (
            <CheckboxField
              label={`Limit to ${formatMonth(month)}`}
              checked={usePeriod}
              onChange={setUsePeriod}
              hint="Uses the settlement month from the header."
            />
          )}
          {descriptor?.needs_statement && (
            <SelectField
              label="Statement"
              value={statementId}
              options={(statements.data?.items ?? []).map((statement) => ({
                value: statement.statement_id,
                label: `${formatMonth(statement.settlement_month)} · ${statement.status.replace(/_/g, ' ')}`,
              }))}
              onChange={setStatementId}
              placeholder="Choose a statement"
              required
              hint="A finalized statement is exported from its stored snapshot."
            />
          )}
        </div>
      </Card>

      <Callout tone="info" title="About these exports">
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          <li>
            Each CSV carries every money column twice: the authoritative integer paise, and a plain
            rupee column for spreadsheets. No currency symbol or digit grouping is written into the
            file, so the values parse cleanly.
          </li>
          <li>
            A statement export comes from that statement's stored snapshot when it has one, so a
            finalized statement always exports as it was finalized.
          </li>
          <li>
            For a printable statement, open the statement and use Print — the layout drops the
            navigation and controls.
          </li>
        </ul>
      </Callout>

      {!ready ? (
        <Card title="Report">
          <EmptyState
            title="Choose a statement"
            body="This report covers one statement, so pick which one to export."
          />
        </Card>
      ) : (
        <AsyncSection resource={report}>
          {(data) => <ReportTable report={data} csvHref={csvUrl(`/api/reports/${kind}/csv`, query)} />}
        </AsyncSection>
      )}
    </Page>
  )
}

function ReportTable({ report, csvHref }: { report: Report; csvHref: string }) {
  if (report.rows.length === 0) {
    return (
      <Card title={report.title}>
        <EmptyState
          title="This report has no rows yet"
          body="Record the underlying expenses, payments, or statements first."
        />
      </Card>
    )
  }

  const columns = Array.from(
    report.rows.reduce<Set<string>>((set, row) => {
      for (const key of Object.keys(row)) set.add(key)
      return set
    }, new Set<string>()),
  )
  const moneyColumns = new Set(columns.filter((column) => column.endsWith('_paise')))
  const totals: Record<string, number> = {}
  for (const column of moneyColumns) {
    totals[column] = report.rows.reduce((sum, row) => {
      const value = row[column]
      return sum + (typeof value === 'number' ? value : 0)
    }, 0)
  }

  const header = (column: string) =>
    column
      .replace(/_paise$/, '')
      .replace(/_/g, ' ')
      .replace(/^./, (character) => character.toUpperCase())

  return (
    <Card
      title={report.title}
      hint={`${report.row_count} row${report.row_count === 1 ? '' : 's'}`}
      flush
      actions={
        <a className="btn btn--sm" href={csvHref} target="_blank" rel="noreferrer">
          Download CSV
        </a>
      }
      footer={<span>Generated {report.generated_at}</span>}
    >
      <div className="table-scroll">
        <table className="data">
          <caption className="sr-only">{report.title}</caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  scope="col"
                  key={column}
                  className={moneyColumns.has(column) ? 'num' : undefined}
                >
                  {header(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {report.rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => {
                  const value = row[column]
                  if (moneyColumns.has(column)) {
                    return (
                      <td className="num" key={column}>
                        <Money
                          paise={typeof value === 'number' ? value : null}
                          tone={
                            column.includes('net_position') || column.includes('adjust')
                              ? 'auto'
                              : 'plain'
                          }
                          sign={
                            column.includes('net_position') || column.includes('adjust')
                              ? 'always'
                              : 'auto'
                          }
                        />
                      </td>
                    )
                  }
                  if (typeof value === 'boolean') {
                    return (
                      <td key={column}>
                        <Badge tone={value ? 'ok' : 'neutral'}>{value ? 'yes' : 'no'}</Badge>
                      </td>
                    )
                  }
                  if (value === null || value === undefined || value === '') {
                    return (
                      <td key={column} className="subtle">
                        —
                      </td>
                    )
                  }
                  return <td key={column}>{String(value)}</td>
                })}
              </tr>
            ))}
          </tbody>
          {moneyColumns.size > 0 && (
            <tfoot>
              <tr>
                {columns.map((column, index) => {
                  if (moneyColumns.has(column)) {
                    return (
                      <td className="num" key={column}>
                        <Money paise={totals[column]} />
                      </td>
                    )
                  }
                  return <td key={column}>{index === 0 ? 'Total' : ''}</td>
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </Card>
  )
}
