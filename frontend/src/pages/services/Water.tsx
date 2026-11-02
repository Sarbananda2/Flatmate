/**
 * The water supply: a fixed monthly contract, such as a society's pipeline
 * connection, whatever the flat uses.
 *
 * Every figure comes from the backend. Each month's fixed rate is one ordinary
 * expense, split equally between the residents taking part, and paid in two
 * steps as the other services are: flatmates pay their shares into the House
 * Kitty, and the society is paid from it -- or by a resident from their own
 * account, whom the kitty pays back for what they paid beyond their share.
 *
 * Three tabs: the month's settlement, the supply and how it is shared, and the
 * connection's contract. The open tab is kept in the address (`?tab=config`).
 */
import { useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Droplets, Receipt, Settings, Store, Users, Waves } from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection, ErrorNotice } from '../../components/Primitives'
import { MoneyInput, TextAreaField, TextField } from '../../components/Form'
import {
  ResidentCollectionTable,
  type CollectionStatus,
  type ResidentCollectionItem,
} from '../../components/ResidentCollectionTable'
import {
  KittyReimbursementPanel,
  KittyReimbursementStatementModal,
  ReimburseDialog,
} from '../../components/KittyReimbursement'
import { ProviderPayoutStatementModal, ProviderPayoutTable } from '../../components/ProviderPayoutTable'
import { ServiceDisbursementDialog } from '../../components/ServiceDisbursementDialog'
import { ServicePaymentDialog } from '../../components/ServicePaymentDialog'
import { useToast } from '../../components/Toast'
import { BUTTON_PRIMARY, DetailFact, EmptyBlock, Panel } from '../../components/ui'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { formatInr, formatMonth, paiseToInput } from '../../lib/format'
import type { KittyReimbursement, WaterConfig, WaterMonthBill, WaterOverview, WaterShare } from '../../api/types'

const BASE = '/api/services/water'

/** The page's tabs: the open one is kept in the address (`?tab=pipeline`). */
export const WATER_TABS = [
  { value: 'settlement', label: 'Monthly Settlement', icon: Receipt },
  { value: 'pipeline', label: 'Supply & Pipeline', icon: Waves },
  { value: 'config', label: 'Connection & Settings', icon: Settings },
] as const

export type WaterTab = (typeof WATER_TABS)[number]['value']

type Dialog =
  | { kind: 'pay_share'; share: WaterShare }
  | { kind: 'payout' }
  | { kind: 'payout_statement' }
  | { kind: 'reimburse'; reimbursement: KittyReimbursement }
  | { kind: 'reimbursement_statement'; residentId: string }

export default function WaterPage() {
  const { month } = useApp()
  return <Water key={month} month={month} />
}

export function Water({ month }: { month: string }) {
  const overview = useResource<WaterOverview>(BASE, { month })
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: WaterTab = WATER_TABS.some((entry) => entry.value === requested) ? (requested as WaterTab) : 'settlement'
  const toast = useToast()
  // The month as the last payout left it, for what its toast says is still owed.
  const saved = useRef<WaterMonthBill | null>(null)
  const done = (message: string, detail?: string) => {
    setDialog(null)
    toast.success(message, detail)
    overview.reload()
  }

  return (
    <AsyncSection resource={overview} loadingLabel="Loading the water supply…">
      {(data) => (
        <Page
          title="Water Supply"
          subtitle="The flat's piped water: a fixed monthly contract, shared equally and paid through the House Kitty."
          breadcrumbs={[{ label: 'Services & Utilities', to: '/services' }, { label: 'Water Supply' }]}
        >
          {!data.service || !data.config || !data.month ? (
            <Panel
              title="Set up the water supply"
              icon={<Droplets className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
              hint="Enter the connection's fixed monthly rate once. Each month is then billed by itself and split equally."
            >
              <ContractForm config={null} month={month} onSaved={() => done('Water supply set up')} />
            </Panel>
          ) : (
            <>
              <div className="tabs" role="tablist" aria-label="Water Supply">
                {WATER_TABS.map(({ value, label, icon: Icon }) => (
                  <button
                    key={value}
                    id={`water-tab-${value}`}
                    type="button"
                    role="tab"
                    className="tab"
                    aria-selected={tab === value}
                    aria-controls={`water-panel-${value}`}
                    onClick={() => setParams(value === 'settlement' ? {} : { tab: value }, { replace: true })}
                  >
                    <Icon className="w-3.5 h-3.5 shrink-0 inline-block mr-1.5 align-[-2px]" aria-hidden="true" />
                    {label}
                  </button>
                ))}
              </div>
              <div role="tabpanel" id={`water-panel-${tab}`} aria-labelledby={`water-tab-${tab}`} className="space-y-4">
                {tab === 'settlement' && (
                  <>
                    <CollectionPanel
                      month={month}
                      bill={data.month}
                      onPay={(share) => setDialog({ kind: 'pay_share', share })}
                    />
                    <PayoutPanel
                      bill={data.month}
                      provider={data.config.provider_name}
                      onPayout={() => setDialog({ kind: 'payout' })}
                      onViewStatement={() => setDialog({ kind: 'payout_statement' })}
                    />
                    {data.month.reimbursements.length > 0 && (
                      <KittyReimbursementPanel
                        reimbursements={data.month.reimbursements}
                        onReimburse={(owed) => setDialog({ kind: 'reimburse', reimbursement: owed })}
                        onViewStatement={(owed) =>
                          setDialog({ kind: 'reimbursement_statement', residentId: owed.recipient_resident_id })
                        }
                      />
                    )}
                  </>
                )}
                {tab === 'pipeline' && <PipelinePanel config={data.config} bill={data.month} />}
                {tab === 'config' && (
                  <Panel
                    title="Connection & Settings"
                    icon={<Settings className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
                    hint="The contract each month is billed from. A new rate moves this month's bill with it; a month gone by keeps its own."
                  >
                    <ContractForm config={data.config} month={month} onSaved={() => done('Contract saved')} />
                  </Panel>
                )}
              </div>
            </>
          )}

          {dialog?.kind === 'pay_share' && (
            <ServicePaymentDialog
              title="Record Payment into House Kitty"
              month={month}
              serviceLabel="water"
              residentName={dialog.share.display_name}
              outstandingPaise={dialog.share.outstanding_paise}
              onSave={(payment) =>
                api.post<WaterOverview>(`${BASE}/record-payment`, {
                  settlement_month: month,
                  resident_id: dialog.share.resident_id,
                  ...payment,
                })
              }
              onClose={() => setDialog(null)}
              onDone={(amount) =>
                done('Payment recorded', `${formatInr(amount)} from ${dialog.share.display_name} into the House Kitty`)
              }
            />
          )}
          {dialog?.kind === 'payout' && data.month && data.config && (
            <ServiceDisbursementDialog
              title="Record Society Payout"
              month={month}
              payeeName={data.config.provider_name}
              payeeRole="society"
              remainingPaise={data.month.remaining_disbursement_paise}
              totalBillPaise={data.month.total_paise}
              suggestedPaise={collectedForPayout(data.month)}
              residents={data.month.shares.map(({ resident_id, display_name }) => ({ resident_id, display_name }))}
              onSave={(payout) =>
                api
                  .post<WaterOverview>(`${BASE}/disburse-payout`, { settlement_month: month, ...payout })
                  .then((updated) => {
                    saved.current = updated.month
                  })
              }
              onClose={() => setDialog(null)}
              onDone={() => {
                const left = saved.current?.remaining_disbursement_paise ?? 0
                done('Society payout recorded', left > 0 ? `${formatInr(left)} is still owed to the society.` : undefined)
              }}
            />
          )}
          {dialog?.kind === 'payout_statement' && data.month && data.config && (
            <ProviderPayoutStatementModal
              title="Society Payout Statement"
              name={data.config.provider_name}
              month={month}
              columns={{ payee: 'Society', total: 'Total Bill', paid: 'Paid to Society' }}
              totalPaise={data.month.total_paise}
              paidPaise={data.month.disbursed_paise}
              stillOwedPaise={data.month.remaining_disbursement_paise}
              status={data.month.payout_status}
              disbursements={data.month.disbursements}
              onClose={() => setDialog(null)}
            />
          )}
          {dialog?.kind === 'reimburse' && (
            <ReimburseDialog
              month={month}
              reimbursement={dialog.reimbursement}
              submit={({ note, ...details }) =>
                api.post<WaterOverview>(`${BASE}/reimburse-from-kitty`, {
                  settlement_month: month,
                  recipient_resident_id: dialog.reimbursement.recipient_resident_id,
                  ...details,
                  notes: note,
                })
              }
              onClose={() => setDialog(null)}
              onDone={(amount) =>
                done('Reimbursement recorded', `${formatInr(amount)} from the kitty to ${dialog.reimbursement.recipient_name}`)
              }
            />
          )}
          {dialog?.kind === 'reimbursement_statement' &&
            (() => {
              // Read from the month as it now stands.
              const owed = data.month?.reimbursements.find((row) => row.recipient_resident_id === dialog.residentId)
              return (
                owed && (
                  <KittyReimbursementStatementModal month={month} reimbursement={owed} onClose={() => setDialog(null)} />
                )
              )
            })()}
        </Page>
      )}
    </AsyncSection>
  )
}

/**
 * The society is due on a fixed day and flatmates pay when they can, so a first
 * payout offers what the kitty has collected so far; after that, the rest.
 */
function collectedForPayout(bill: WaterMonthBill): number | undefined {
  return bill.disbursements.length === 0 && bill.collected_paise > 0 && bill.collected_paise < bill.total_paise
    ? bill.collected_paise
    : undefined
}

/* ---------------------------------------------------- 1. the settlement -- */

function collectionStatus(share: WaterShare): CollectionStatus | null {
  if (share.status === 'paid' || share.status === 'funded_payout') return 'paid'
  return share.status
}

function CollectionPanel({
  month,
  bill,
  onPay,
}: {
  month: string
  bill: WaterMonthBill
  onPay: (share: WaterShare) => void
}) {
  // Who has paid what means something only once the month is billed, which an
  // open month is by itself.
  const billed = bill.expense != null
  const items: ResidentCollectionItem[] = bill.shares.map((share) => ({
    id: share.resident_id,
    name: share.display_name,
    href: `/residents/${share.resident_id}`,
    sharePaise: share.total_paise,
    paidIntoKittyPaise: billed ? share.settled_paise : null,
    stillOwedPaise: billed ? share.outstanding_paise : null,
    status: billed ? collectionStatus(share) : null,
    canPay: billed && share.outstanding_paise > 0 && share.status !== 'funded_payout',
    onPay: () => onPay(share),
  }))
  return (
    <Panel
      title="Resident Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Each flatmate's equal share of this month's water, and how much has been collected into the House Kitty."
    >
      {bill.blocked_reason && !billed && (
        <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
          {bill.blocked_reason}
        </p>
      )}
      {bill.shares.length === 0 ? (
        <EmptyBlock title="Nobody is sharing the water this month." />
      ) : (
        <ResidentCollectionTable caption={`Water shares for ${formatMonth(month)}`} items={items} />
      )}
    </Panel>
  )
}

function PayoutPanel({
  bill,
  provider,
  onPayout,
  onViewStatement,
}: {
  bill: WaterMonthBill
  provider: string
  onPayout: () => void
  onViewStatement: () => void
}) {
  const billed = bill.expense != null
  return (
    <Panel
      title="Society Payout"
      icon={<Store className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What the society is owed for the month, and payouts made to it. Select the row for its disbursement history."
    >
      <ProviderPayoutTable
        caption={`The water bill owed to ${provider}`}
        columns={{ payee: 'Society', total: 'Total Bill', paid: 'Paid to Society' }}
        items={[
          {
            id: 'society',
            name: provider,
            totalPaise: bill.total_paise,
            // Nothing is paid or owed until the month is billed.
            paidPaise: billed ? bill.disbursed_paise : null,
            stillOwedPaise: billed ? bill.remaining_disbursement_paise : null,
            status: billed ? bill.payout_status : null,
            canPay: billed && bill.payout_status !== 'disbursed' && bill.total_paise > 0,
            onPay: onPayout,
            onViewStatement: billed ? onViewStatement : undefined,
          },
        ]}
      />
    </Panel>
  )
}

/* ------------------------------------------------------ 2. the pipeline -- */

function PipelinePanel({ config, bill }: { config: WaterConfig; bill: WaterMonthBill }) {
  return (
    <Panel
      title="Supply & Pipeline"
      icon={<Waves className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Where the flat's water comes from, and how its bill is shared."
    >
      <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm" aria-label="Supply and pipeline">
        <DetailFact label="Connection Source">Society Main Pipeline Connection</DetailFact>
        <DetailFact label="Distribution">Filtration system line + Bathrooms/utilities line</DetailFact>
        <DetailFact label="Tariff Model">Fixed constant monthly contract (consumption-independent)</DetailFact>
        <DetailFact label="Split Rule">100% Equal share among all active flatmates</DetailFact>
        <DetailFact label="Supplier">{config.provider_name}</DetailFact>
        <DetailFact label="This Month">
          <span className="tabular">
            {formatInr(bill.total_paise)}
            {bill.participant_count > 0 && ` · ${formatInr(bill.base_share_paise)} each`}
          </span>
        </DetailFact>
        {config.pipeline_notes && <DetailFact label="Pipeline Notes">{config.pipeline_notes}</DetailFact>}
      </dl>
    </Panel>
  )
}

/* ----------------------------------------------------- 3. the contract -- */

/** A day of the month as typed: 1 to 31, or empty for none. */
function parseDay(raw: string): { ok: boolean; day: number | null } {
  const text = raw.trim()
  if (!text) return { ok: true, day: null }
  const day = Number(text)
  return /^\d{1,2}$/.test(text) && day >= 1 && day <= 31 ? { ok: true, day } : { ok: false, day: null }
}

function ContractForm({ config, month, onSaved }: { config: WaterConfig | null; month: string; onSaved: () => void }) {
  const [provider, setProvider] = useState(config?.provider_name ?? 'Society Water Supply')
  const [rate, setRate] = useState(config ? paiseToInput(config.monthly_rate_paise) : '')
  const [ratePaise, setRatePaise] = useState(config?.monthly_rate_paise ?? 0)
  const [rateOk, setRateOk] = useState(Boolean(config))
  const [dayRaw, setDayRaw] = useState(config?.billing_day_of_month ? String(config.billing_day_of_month) : '')
  const day = parseDay(dayRaw)
  const [consumer, setConsumer] = useState(config?.consumer_id ?? '')
  const [pipelineNotes, setPipelineNotes] = useState(config?.pipeline_notes ?? '')
  const [notes, setNotes] = useState(config?.notes ?? '')
  const valid = provider.trim().length > 0 && rateOk && day.ok
  const save = useMutation(
    () =>
      api.post<WaterOverview>(`${BASE}/setup?month=${encodeURIComponent(month)}`, {
        provider_name: provider.trim(),
        monthly_rate_paise: ratePaise,
        billing_day_of_month: day.day,
        consumer_id: consumer.trim() || null,
        pipeline_notes: pipelineNotes.trim() || null,
        notes: notes.trim() || null,
      }),
    { onSuccess: onSaved },
  )
  return (
    <form
      className="stack"
      noValidate
      aria-label="Water contract"
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        if (valid && !save.pending) void save.run(undefined)
      }}
    >
      <ErrorNotice error={save.error} />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <TextField label="Provider" required value={provider} onChange={setProvider} maxLength={100} />
        <MoneyInput
          label="Fixed monthly rate"
          required
          value={rate}
          onChange={(raw, parsed) => {
            setRate(raw)
            setRatePaise(parsed.paise)
            setRateOk(parsed.ok)
          }}
          hint="What the flat pays a month, whatever it uses. Split equally."
        />
        <TextField
          label="Billing due day"
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          value={dayRaw}
          onChange={setDayRaw}
          error={day.ok ? null : 'Enter a day from 1 to 31.'}
          hint="The day of the month the society is due."
        />
        <TextField label="Consumer reference" value={consumer} onChange={setConsumer} maxLength={100} />
      </div>
      <TextAreaField label="Pipeline notes" value={pipelineNotes} onChange={setPipelineNotes} rows={2} />
      <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
      <div className="flex justify-end">
        <button type="submit" className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
          {save.pending ? 'Saving…' : config ? 'Save contract' : 'Set up water supply'}
        </button>
      </div>
    </form>
  )
}
