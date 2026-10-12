/** The append-only audit trail. */
import { useState } from 'react'
import { Page } from '../components/Layout'
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  EmptyState,
  Pagination,
} from '../components/Primitives'
import { SelectField, TextField } from '../components/Form'
import { downloadCsv } from '../api/client'
import { useDebounced, useResource } from '../lib/useApi'
import { formatDateTime } from '../lib/format'
import type { AuditRow, Paged } from '../api/types'

const PAGE_SIZE = 50

const ENTITY_TYPES = [
  'apartment',
  'resident',
  'expense_definition',
  'expense',
  'payment',
  'ledger_adjustment',
  'statement',
  'app_setting',
]

export default function AuditPage() {
  const [entityType, setEntityType] = useState('')
  const [entityId, setEntityId] = useState('')
  const [offset, setOffset] = useState(0)
  const debouncedId = useDebounced(entityId)

  const audit = useResource<Paged<AuditRow>>('/api/audit', {
    entity_type: entityType || undefined,
    entity_id: debouncedId || undefined,
    limit: PAGE_SIZE,
    offset,
  })

  return (
    <Page
      title="Audit trail"
      subtitle="Every change, with its old and new value, who made it, when, and why where a reason was required."
      breadcrumbs={[{ label: 'Review' }, { label: 'Audit trail' }]}
      actions={
        <button type="button" className="btn" onClick={() => downloadCsv('/api/audit/export/csv')}>
          Export CSV
        </button>
      }
    >
      <Callout tone="info" title="Append-only">
        The audit log cannot be modified or deleted — database triggers refuse both, so the trail
        stays intact even against a direct SQL mistake. Statement revisions and ledger adjustments
        are protected the same way.
      </Callout>

      <Card title="Filters">
        <div className="filters">
          <SelectField
            label="Record type"
            value={entityType}
            options={ENTITY_TYPES.map((value) => ({
              value,
              label: value.replace(/_/g, ' '),
            }))}
            onChange={(value) => {
              setEntityType(value)
              setOffset(0)
            }}
            placeholder="All record types"
          />
          <TextField
            label="Record ID"
            type="search"
            value={entityId}
            onChange={(value) => {
              setEntityId(value)
              setOffset(0)
            }}
            placeholder="Paste an expense or payment ID"
          />
        </div>
      </Card>

      <Card
        title="Change history"
        hint={audit.data ? `${audit.data.total} entries` : undefined}
        flush
        footer={
          audit.data ? (
            <Pagination
              total={audit.data.total}
              limit={PAGE_SIZE}
              offset={offset}
              onChange={setOffset}
              noun="entries"
            />
          ) : undefined
        }
      >
        <AsyncSection
          resource={audit}
          isEmpty={(data) => data.items.length === 0}
          empty={<EmptyState title="No audit entries match" />}
        >
          {(data) => (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Audit trail</caption>
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Actor</th>
                    <th scope="col">Record</th>
                    <th scope="col">Action</th>
                    <th scope="col">Field</th>
                    <th scope="col">Old value</th>
                    <th scope="col">New value</th>
                    <th scope="col">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr key={row.audit_id}>
                      <td className="nowrap">{formatDateTime(row.created_at)}</td>
                      <td>{row.actor}</td>
                      <td>
                        <Badge tone="neutral">{row.entity_type.replace(/_/g, ' ')}</Badge>
                        <div>
                          <span className="id-chip">{row.entity_id}</span>
                        </div>
                      </td>
                      <td>
                        <Badge
                          tone={
                            row.action === 'create'
                              ? 'ok'
                              : row.action === 'delete'
                                ? 'danger'
                                : row.action === 'finalize' || row.action === 'close'
                                  ? 'accent'
                                  : 'neutral'
                          }
                        >
                          {row.action.replace(/_/g, ' ')}
                        </Badge>
                      </td>
                      <td className="mono">{row.field_name ?? '—'}</td>
                      <td className="mono" style={{ maxWidth: 240, wordBreak: 'break-word' }}>
                        {row.old_value ?? '—'}
                      </td>
                      <td className="mono" style={{ maxWidth: 240, wordBreak: 'break-word' }}>
                        {row.new_value ?? '—'}
                      </td>
                      <td style={{ maxWidth: 220 }}>{row.reason ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </Card>
    </Page>
  )
}
