/**
 * Electricity: the flat's prepaid meters, UPPCL's and the DG's, recharged as
 * the month goes, and settled through the House Kitty.
 *
 * A meter is topped up whenever it runs low -- by a flatmate from their own
 * account, or from the kitty -- and the month's bills are what was recharged.
 * Each flatmate's own AC comes out of that, and the rest is shared equally;
 * whoever recharged beyond their share is paid back by the kitty.
 *
 * Two tabs, as Rent and Furniture Rental have them: the month's settlement --
 * what each flatmate owes the kitty, and what the kitty owes back -- and its
 * recharges and AC. The open tab is kept in the address (`?tab=allocations`),
 * so a link or a reload lands on it.
 *
 * Only sums are added up here. The split itself, to the paisa, comes from the
 * backend: as the AC is typed, and once it is saved.
 */
import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Calculator, Plus, Receipt, Save, Trash2, Users, Zap } from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection, Callout, ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, MoneyInput, SelectField, TextField } from '../../components/Form'
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
import { DeleteTransactionDialog, type PendingDeletion } from '../../components/DeleteTransactionDialog'
import { ServicePaymentDialog } from '../../components/ServicePaymentDialog'
import { useToast } from '../../components/Toast'
import { BUTTON_PRIMARY, BUTTON_QUIET, BUTTON_SECONDARY, DetailFact, Panel, Rupees } from '../../components/ui'
import { ApiError, api } from '../../api/client'
import { useDebounced, useMutation, useResource } from '../../lib/useApi'
import { formatDate, formatInr, formatMonth, paiseToInput, parseRupees, todayIso } from '../../lib/format'
import type {
  ElectricityMonth,
  ElectricityMonthPreview,
  ElectricityProviderKey,
  ElectricityShare,
  ElectricityTopUp,
  KittyReimbursement,
} from '../../api/types'

const BASE = '/api/electricity'

/** The page's tabs, as Rent's: the open one is kept in the address (`?tab=allocations`). */
export const ELECTRICITY_TABS = [
  { value: 'settlement', label: 'Monthly Settlement', icon: Receipt },
  { value: 'allocations', label: 'Top-ups & AC Allocations', icon: Zap },
] as const

export type ElectricityTab = (typeof ELECTRICITY_TABS)[number]['value']

type Dialog =
  | { kind: 'pay_share'; share: ElectricityShare }
  | { kind: 'top_up' }
  | { kind: 'top_up_detail'; topUp: ElectricityTopUp }
  | { kind: 'remove_top_up'; deletion: PendingDeletion }
  | { kind: 'reimburse'; reimbursement: KittyReimbursement }
  | { kind: 'reimbursement_statement'; residentId: string }

export default function ElectricityPage() {
  const { month } = useApp()
  return <Electricity key={month} month={month} />
}

/** An amount as typed; empty is ₹0. */
function amount(raw: string): { ok: boolean; paise: number } {
  const parsed = parseRupees(raw, { allowEmpty: true })
  return { ok: parsed.ok, paise: parsed.ok ? parsed.paise : 0 }
}

/** An amount ready to edit; nothing for ₹0. */
function editable(paise: number | undefined): string {
  return paise ? paiseToInput(paise) : ''
}

export function Electricity({ month }: { month: string }) {
  const overview = useResource<ElectricityMonth>(`${BASE}/month/${month}`)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: ElectricityTab = ELECTRICITY_TABS.some((entry) => entry.value === requested)
    ? (requested as ElectricityTab)
    : 'settlement'
  const open = (value: ElectricityTab) => setParams(value === 'settlement' ? {} : { tab: value }, { replace: true })
  const toast = useToast()
  // Removing a recharge asks first, from its row or its details.
  const removal = (topUp: ElectricityTopUp): PendingDeletion => ({
    title: 'Remove this recharge?',
    what: `${topUp.provider_name} recharge of ${formatInr(topUp.amount_paise)} on ${formatDate(topUp.recharged_on)}, paid by ${topUp.resident_name ?? 'the House Kitty'}.`,
    url: `${BASE}/month/${month}/top-ups/${encodeURIComponent(topUp.top_up_id)}`,
  })
  const done = (message: string, detail?: string) => {
    setDialog(null)
    toast.success(message, detail)
    overview.reload()
  }

  return (
    <AsyncSection resource={overview} loadingLabel="Loading electricity…">
      {(data) => (
        <Page
          title="Electricity"
          subtitle="The flat's prepaid UPPCL and DG meters: every flatmate pays their own AC and an equal share of what was recharged, through the House Kitty."
          breadcrumbs={[{ label: 'Services & Utilities', to: '/services' }, { label: 'Electricity' }]}
        >
          {data.bill_state === 'recorded_elsewhere' && (
            <Callout tone="warn" title="Recorded with smart-plug readings">
              {formatMonth(month)}'s electricity was entered as a bill split by smart-plug readings, so it is not
              split by AC here.
            </Callout>
          )}
          {data.bill_state === 'frozen' && (
            <Callout tone="info" title="Statement finalized">
              {formatMonth(month)}'s statement is finalized, so its recharges and AC stay as they were entered.
            </Callout>
          )}
          <div className="tabs" role="tablist" aria-label="Electricity">
            {ELECTRICITY_TABS.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                id={`electricity-tab-${value}`}
                type="button"
                role="tab"
                className="tab"
                aria-selected={tab === value}
                aria-controls={`electricity-panel-${value}`}
                onClick={() => open(value)}
              >
                <Icon className="w-3.5 h-3.5 shrink-0 inline-block mr-1.5 align-[-2px]" aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`electricity-panel-${tab}`}
            aria-labelledby={`electricity-tab-${tab}`}
            className="space-y-4"
          >
            {tab === 'settlement' && (
              <>
                <ResidentCollectionPanel
                  month={month}
                  data={data}
                  onPay={(share) => setDialog({ kind: 'pay_share', share })}
                />
                {/* What the kitty owes back to whoever recharged a meter beyond their share. */}
                {data.reimbursements.length > 0 && (
                  <KittyReimbursementPanel
                    reimbursements={data.reimbursements}
                    onReimburse={(owed) => setDialog({ kind: 'reimburse', reimbursement: owed })}
                    onViewStatement={(owed) =>
                      setDialog({ kind: 'reimbursement_statement', residentId: owed.recipient_resident_id })
                    }
                  />
                )}
              </>
            )}
            {tab === 'allocations' && (
              <>
                <TopUpsPanel
                  month={month}
                  data={data}
                  onAdd={() => setDialog({ kind: 'top_up' })}
                  onSelect={(topUp) => setDialog({ kind: 'top_up_detail', topUp })}
                  onRemove={(topUp) => setDialog({ kind: 'remove_top_up', deletion: removal(topUp) })}
                />
                <MonthBill
                  key={data.expense?.expense_id ?? 'new'}
                  month={month}
                  data={data}
                  onSaved={() => {
                    // Saved, the month is collected and paid back on its settlement.
                    open('settlement')
                    done('AC allocations saved', `${formatMonth(month)}'s shares follow them.`)
                  }}
                />
              </>
            )}
          </div>

          {dialog?.kind === 'top_up' && (
            <TopUpDialog
              month={month}
              data={data}
              onClose={() => setDialog(null)}
              onDone={(provider, paid) => done('Recharge logged', `${formatInr(paid)} to ${provider}.`)}
            />
          )}
          {dialog?.kind === 'top_up_detail' && (
            <TopUpDetailModal
              topUp={dialog.topUp}
              removable={data.can_record && data.transactions_open}
              onClose={() => setDialog(null)}
              onRemove={() => setDialog({ kind: 'remove_top_up', deletion: removal(dialog.topUp) })}
            />
          )}
          {dialog?.kind === 'remove_top_up' && (
            <DeleteTransactionDialog
              deletion={dialog.deletion}
              onClose={() => setDialog(null)}
              onDeleted={() => done('Recharge removed')}
            />
          )}
          {dialog?.kind === 'pay_share' && (
            <ServicePaymentDialog
              title="Record Payment into House Kitty"
              month={month}
              serviceLabel="electricity"
              residentName={dialog.share.display_name}
              outstandingPaise={dialog.share.outstanding_paise}
              onSave={(payment) =>
                api.post<ElectricityMonth>(`${BASE}/record-payment`, {
                  settlement_month: month,
                  resident_id: dialog.share.resident_id,
                  ...payment,
                })
              }
              onClose={() => setDialog(null)}
              onDone={(paid) =>
                done('Payment recorded', `${formatInr(paid)} from ${dialog.share.display_name} into the House Kitty`)
              }
            />
          )}
          {dialog?.kind === 'reimburse' && (
            <ReimburseDialog
              month={month}
              reimbursement={dialog.reimbursement}
              submit={({ note, ...details }) =>
                api.post<ElectricityMonth>(`${BASE}/reimburse-from-kitty`, {
                  settlement_month: month,
                  recipient_resident_id: dialog.reimbursement.recipient_resident_id,
                  ...details,
                  notes: note,
                })
              }
              onClose={() => setDialog(null)}
              onDone={(paid) =>
                done('Reimbursement recorded', `${formatInr(paid)} from the kitty to ${dialog.reimbursement.recipient_name}`)
              }
            />
          )}
          {dialog?.kind === 'reimbursement_statement' &&
            (() => {
              // Read from the month as it now stands.
              const owed = data.reimbursements.find((row) => row.recipient_resident_id === dialog.residentId)
              return (
                owed && <KittyReimbursementStatementModal month={month} reimbursement={owed} onClose={() => setDialog(null)} />
              )
            })()}
        </Page>
      )}
    </AsyncSection>
  )
}

/* ------------------------------------------------ 1. the meters' recharges -- */

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]'

function TopUpsPanel({
  month,
  data,
  onAdd,
  onSelect,
  onRemove,
}: {
  month: string
  data: ElectricityMonth
  onAdd: () => void
  onSelect: (topUp: ElectricityTopUp) => void
  onRemove: (topUp: ElectricityTopUp) => void
}) {
  const removable = data.can_record && data.transactions_open
  return (
    <Panel
      title="Meter Top-ups / Recharges"
      icon={<Zap className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Each recharge of the prepaid meters this month, by a flatmate or from the House Kitty. The month's bills are what was recharged. Select a recharge for its details."
      menuActions={data.can_record ? [{ label: 'Add Meter Recharge', icon: Plus, onClick: onAdd }] : undefined}
    >
      {data.top_ups.length === 0 ? (
        <p className="m-0 text-xs text-[var(--text-secondary)]">No recharge logged for {formatMonth(month)} yet.</p>
      ) : (
        <div className="overflow-x-auto min-w-0">
          <table className="w-full table-fixed text-xs min-w-[480px] border-collapse">
            <caption className="sr-only">Meter recharges for {formatMonth(month)}</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="w-[20%] pb-3 pr-4 text-left">
                  Date
                </th>
                <th scope="col" className="w-[30%] pb-3 pr-4 text-left">
                  Meter / Provider
                </th>
                <th scope="col" className="w-[25%] pb-3 pr-4 text-left">
                  Paid By
                </th>
                <th scope="col" className="w-[18%] pb-3 pl-2 text-right">
                  Amount
                </th>
                <th scope="col" className="w-[7%] pb-3 text-right">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {data.top_ups.map((topUp) => (
                // The whole row opens the recharge (Rule U7); its button removes it.
                <tr
                  key={topUp.top_up_id}
                  className="cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                  tabIndex={0}
                  title={`View ${topUp.provider_name} recharge details`}
                  onClick={() => onSelect(topUp)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onSelect(topUp)
                    }
                  }}
                >
                  <td className="py-2.5 pr-4 tabular whitespace-nowrap">{formatDate(topUp.recharged_on)}</td>
                  <th scope="row" className="py-2.5 pr-4 text-left font-semibold text-[var(--text-primary)] truncate">
                    {topUp.provider_name}
                  </th>
                  <td className="py-2.5 pr-4 truncate">{topUp.resident_name ?? 'House Kitty'}</td>
                  <td className="py-2.5 pl-2 text-right tabular font-bold text-[var(--text-primary)]">
                    {formatInr(topUp.amount_paise)}
                  </td>
                  <td className="py-2.5 text-right">
                    {removable && (
                      <button
                        type="button"
                        className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--danger)] hover:bg-[var(--danger-glow)] transition-colors inline-flex items-center justify-center"
                        aria-label={`Remove ${topUp.provider_name} recharge of ${formatInr(topUp.amount_paise)}`}
                        title="Remove this recharge"
                        onClick={(event) => {
                          // Removing it does not also open it.
                          event.stopPropagation()
                          onRemove(topUp)
                        }}
                      >
                        <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <dl
        className="m-0 pt-3 border-t border-[var(--border-subtle)] grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm"
        aria-label="Recharge subtotals"
      >
        {data.components.map((bill) => (
          <div key={bill.id}>
            <dt className="text-xs text-[var(--text-secondary)]">{bill.id === 'uppcl' ? 'UPPCL Recharges' : 'DG Recharges'}</dt>
            <dd className="m-0 font-semibold tabular">{formatInr(bill.amount_paise)}</dd>
          </div>
        ))}
        <div>
          <dt className="text-xs text-[var(--text-secondary)]">Total Prepaid Electricity</dt>
          <dd className="m-0 font-bold tabular text-[var(--text-primary)]">{formatInr(data.total_paise)}</dd>
        </div>
      </dl>
    </Panel>
  )
}

/** One recharge: what was paid, to which meter, by whom, and its reference. */
function TopUpDetailModal({
  topUp,
  removable,
  onClose,
  onRemove,
}: {
  topUp: ElectricityTopUp
  removable: boolean
  onClose: () => void
  onRemove: () => void
}) {
  const byFlatmate = topUp.paid_by === 'flatmate'
  return (
    <Modal
      title="Meter Recharge"
      description={`${topUp.provider_name} · ${formatDate(topUp.recharged_on)}`}
      onClose={onClose}
      footer={
        <>
          {removable && (
            <button type="button" className={`${BUTTON_QUIET} mr-auto text-[var(--danger)]`} onClick={onRemove}>
              <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Remove Recharge
            </button>
          )}
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm" aria-label="Recharge details">
        <DetailFact label="Amount">
          <span className="tabular font-bold text-[var(--text-primary)]">{formatInr(topUp.amount_paise)}</span>
        </DetailFact>
        <DetailFact label="Meter / Provider">{topUp.provider_name}</DetailFact>
        <DetailFact label="Recharged On">
          <span className="tabular">{formatDate(topUp.recharged_on)}</span>
        </DetailFact>
        <DetailFact label="Paid By">
          <span className="block">{topUp.resident_name ?? 'House Kitty'}</span>
          <span className="block text-xs text-[var(--text-secondary)]">
            {byFlatmate
              ? 'From their own account: their share goes through the House Kitty, and anything beyond it the kitty pays back.'
              : "From the House Kitty, out of what flatmates paid in."}
          </span>
        </DetailFact>
        <DetailFact label="Reference / UTR">
          <span className="font-mono tabular break-all">{topUp.reference}</span>
        </DetailFact>
        {topUp.note && <DetailFact label="Note">{topUp.note}</DetailFact>}
      </dl>
    </Modal>
  )
}

/** Log a recharge of one of the meters: what was paid, by whom, and its reference. */
function TopUpDialog({
  month,
  data,
  onClose,
  onDone,
}: {
  month: string
  data: ElectricityMonth
  onClose: () => void
  onDone: (provider: string, paid: number) => void
}) {
  const [provider, setProvider] = useState<ElectricityProviderKey>('uppcl')
  // 'kitty', or the flatmate who paid from their own account.
  const [payer, setPayer] = useState('')
  const [raw, setRaw] = useState('')
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const parsed = parseRupees(raw)
  const valid = parsed.ok && parsed.paise > 0 && Boolean(payer) && Boolean(date) && reference.trim().length > 0
  const name = data.components.find((bill) => bill.id === provider)?.name ?? provider
  const record = useMutation(
    () =>
      api.post<ElectricityMonth>(`${BASE}/disburse-payout`, {
        settlement_month: month,
        provider,
        disbursement_mode: payer === 'kitty' ? 'pool' : 'resident_advance',
        ...(payer === 'kitty' ? {} : { funding_resident_id: payer }),
        amount_paise: parsed.paise,
        disbursed_date: date,
        reference: reference.trim(),
        notes: note.trim() || null,
      }),
    { onSuccess: () => onDone(name, parsed.paise) },
  )
  const formId = `electricity-top-up-${month}`
  return (
    <Modal
      title="Add Meter Recharge"
      description={`${formatMonth(month)} · each recharge raises that meter's bill for the month by as much.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={record.pending}>
            Cancel
          </button>
          <button type="submit" form={formId} className={BUTTON_PRIMARY} disabled={!valid || record.pending}>
            {record.pending ? 'Saving…' : 'Log Recharge'}
          </button>
        </>
      }
    >
      <form
        id={formId}
        className="stack"
        noValidate
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          if (valid && !record.pending) void record.run(undefined)
        }}
      >
        <ErrorNotice error={record.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField<ElectricityProviderKey>
            label="Meter"
            required
            value={provider}
            options={data.components.map((bill) => ({ value: bill.id, label: bill.name }))}
            onChange={setProvider}
          />
          <SelectField
            label="Paid by"
            required
            value={payer}
            placeholder="Choose who paid"
            options={[
              { value: 'kitty', label: 'House Kitty' },
              ...data.participants.map((person) => ({ value: person.resident_id, label: person.display_name })),
            ]}
            onChange={setPayer}
            hint={
              payer && payer !== 'kitty'
                ? 'What they paid beyond their own share, the kitty pays back to them.'
                : 'From the kitty, or a flatmate’s own account.'
            }
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput label="Amount" required value={raw} onChange={(value) => setRaw(value)} hint="What was recharged." />
          <DateField label="Recharged on" required value={date} onChange={setDate} />
        </div>
        <TextField
          label="Reference / UTR"
          required
          value={reference}
          onChange={setReference}
          maxLength={200}
          placeholder="e.g. UPI ref or bank UTR"
        />
        <TextField label="Note" value={note} onChange={setNote} maxLength={500} />
      </form>
    </Modal>
  )
}

/* ------------------------------------------ 2. the AC and the responsibility -- */

/** A flatmate's part of the month: their share of what's left, and in all. */
interface Share {
  common_share_paise: number
  responsibility_paise: number
}

/**
 * Each flatmate's AC, as it is being entered, beside what it leaves them
 * responsible for out of what was recharged. Saved, it is the month's.
 */
function MonthBill({
  month,
  data,
  onSaved,
}: {
  month: string
  data: ElectricityMonth
  onSaved: (updated: ElectricityMonth) => void
}) {
  // Everyone's AC, ready to edit: left empty, it is ₹0.
  const [ac, setAc] = useState<Record<string, string>>(() =>
    Object.fromEntries(data.participants.map(({ resident_id }) => [resident_id, editable(data.ac_allocations[resident_id])])),
  )
  const recharged = data.expense != null
  const editing = data.can_record && recharged

  const parsedAc = data.participants.map((person) => ({ ...person, parsed: amount(ac[person.resident_id] ?? '') }))
  const total = data.total_paise
  const totalAc = parsedAc.reduce((sum, person) => sum + person.parsed.paise, 0)
  const remainder = total - totalAc
  const valid = recharged && parsedAc.every((person) => person.parsed.ok) && remainder >= 0
  const body = {
    ac_allocations: Object.fromEntries(parsedAc.map(({ resident_id, parsed }) => [resident_id, parsed.paise])),
  }
  const unchanged = parsedAc.every((person) => person.parsed.paise === (data.ac_allocations[person.resident_id] ?? 0))

  // The split as typed, worked out by the backend once typing pauses.
  const key = valid && editing && !unchanged ? JSON.stringify(body) : null
  const settledKey = useDebounced(key, 300)
  const [preview, setPreview] = useState<{ key: string; split: ElectricityMonthPreview | null; error: string | null }>()
  useEffect(() => {
    if (!settledKey) return
    let current = true
    api
      .post<ElectricityMonthPreview>(`${BASE}/month/${month}/preview`, JSON.parse(settledKey))
      .then((split) => current && setPreview({ key: settledKey, split, error: null }))
      .catch((cause) => current && setPreview({ key: settledKey, split: null, error: cause instanceof ApiError ? cause.message : String(cause) }))
    return () => {
      current = false
    }
  }, [settledKey, month])

  const typed = key && preview?.key === key ? preview : null
  const working = key != null && typed == null
  // Each flatmate's share, by flatmate: of the AC as typed, or as saved.
  const shares: Map<string, Share> | null = typed?.split
    ? new Map(typed.split.residents.map((row) => [row.resident_id, row]))
    : recharged && (unchanged || !editing)
      ? new Map(
          data.shares.map((share) => [
            share.resident_id,
            { common_share_paise: share.common_share_paise, responsibility_paise: share.total_paise },
          ]),
        )
      : null
  // Until there is one: still being worked out, or not for this AC.
  const missing = (
    <span className="text-[var(--text-tertiary)]">
      <span aria-hidden="true">{working ? '…' : '—'}</span>
      <span className="sr-only">{working ? 'Working out' : 'Not worked out'}</span>
    </span>
  )

  const save = useMutation(
    () => api.post<ElectricityMonth>(`${BASE}/bills`, { settlement_month: month, ...body }),
    { onSuccess: onSaved },
  )

  return (
    <Panel
      title="AC Allocations & Responsibility"
      icon={<Calculator className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Each flatmate pays their own AC, out of what was recharged, and an equal share of whatever is left."
    >
      {!recharged ? (
        <p className="m-0 text-xs text-[var(--text-secondary)]">
          Log a meter recharge first: the AC is taken out of what was recharged.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto min-w-0">
            <table className="w-full table-fixed text-xs min-w-[480px] border-collapse" aria-busy={working || undefined}>
              <caption className="sr-only">AC and responsibility for {formatMonth(month)}</caption>
              <thead>
                <tr className={TABLE_HEAD}>
                  <th scope="col" className="w-[30%] pb-3 pr-3 text-left">
                    Flatmate
                  </th>
                  <th scope="col" className="w-[26%] pb-3 pr-3 text-left">
                    Individual AC Cost
                  </th>
                  <th scope="col" className="w-[22%] pb-3 pr-3 text-right">
                    Shared Remainder
                  </th>
                  <th scope="col" className="w-[22%] pb-3 text-right">
                    Net Responsibility
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)] font-medium tabular">
                {parsedAc.map((person) => {
                  const share = shares?.get(person.resident_id)
                  return (
                    <tr key={person.resident_id}>
                      <th scope="row" className="py-2 pr-3 text-left font-semibold text-[var(--text-primary)] truncate">
                        {person.display_name}
                      </th>
                      <td className="py-2 pr-3 text-left">
                        {editing ? (
                          <MoneyInput
                            label={<span className="sr-only">{person.display_name}'s AC</span>}
                            value={ac[person.resident_id] ?? ''}
                            placeholder="0.00"
                            hint=""
                            onChange={(raw) => setAc((before) => ({ ...before, [person.resident_id]: raw }))}
                          />
                        ) : (
                          formatInr(person.parsed.paise)
                        )}
                      </td>
                      <td className="py-2 pr-3 text-right">{share ? formatInr(share.common_share_paise) : missing}</td>
                      <td className="py-2 text-right font-bold text-[var(--text-primary)]">
                        {share ? <Rupees paise={share.responsibility_paise} /> : missing}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                {/* The AC and what's left of it come to what was recharged. */}
                <tr className="border-t border-[var(--border-subtle)] font-bold tabular">
                  <th scope="row" className="pt-2.5 pr-3 text-left">
                    Total
                  </th>
                  <td className="pt-2.5 pr-3 text-left">{formatInr(totalAc)}</td>
                  <td className={`pt-2.5 pr-3 text-right ${remainder < 0 ? 'text-[var(--danger)]' : ''}`}>
                    {formatInr(remainder)}
                  </td>
                  <td className="pt-2.5 text-right">{formatInr(total)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          {typed?.error && (
            <p className="m-0 text-xs font-medium text-[var(--danger)]" role="alert">
              {typed.error}
            </p>
          )}
          <dl className="m-0 grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm" aria-label="AC and the shared remainder">
            <div>
              <dt className="text-xs text-[var(--text-secondary)]">Total Recharged</dt>
              <dd className="m-0 font-semibold tabular">{formatInr(total)}</dd>
            </div>
            <div>
              <dt className="text-xs text-[var(--text-secondary)]">Sum of ACs</dt>
              <dd className="m-0 font-semibold tabular">{formatInr(totalAc)}</dd>
            </div>
            <div>
              <dt className="text-xs text-[var(--text-secondary)]">Shared Remainder</dt>
              <dd className={`m-0 font-semibold tabular ${remainder < 0 ? 'text-[var(--danger)]' : ''}`}>
                {formatInr(remainder)}
              </dd>
            </div>
          </dl>
          {remainder < 0 && (
            <p className="m-0 text-xs font-medium text-[var(--danger)]" role="alert">
              The AC sum, {formatInr(totalAc)}, exceeds the {formatInr(total)} recharged.
            </p>
          )}
        </>
      )}
      {editing && (
        <div className="pt-4 border-t border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-3">
          <p
            className={`m-0 text-xs ${unchanged ? 'text-[var(--text-secondary)]' : 'font-medium text-[var(--warning)]'}`}
            role="status"
          >
            {unchanged
              ? 'No unsaved changes.'
              : working
                ? "Unsaved changes: working out each flatmate's share…"
                : 'Unsaved changes: the shares above are a preview until you save.'}
          </p>
          <div className="flex flex-wrap items-center justify-end gap-3 ml-auto">
            <ErrorNotice error={save.error} />
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={!valid || unchanged || save.pending}
              onClick={() => void save.run(undefined)}
            >
              <Save className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              {save.pending ? 'Saving…' : 'Save AC Allocations'}
            </button>
          </div>
        </div>
      )}
    </Panel>
  )
}

/* ------------------------------------------------------- 3. the settlement -- */

function collectionStatus(share: ElectricityShare): CollectionStatus | null {
  if (share.status === 'paid' || share.status === 'funded_payout') return 'paid'
  return share.status
}

/** Where a flatmate stands with the kitty: what it owes them, or they owe it. */
function netBalance(share: ElectricityShare): { text: string; tone?: 'warning' | 'muted' } {
  if (share.net_balance_paise > 0) return { text: `Receives ${formatInr(share.net_balance_paise)}` }
  if (share.net_balance_paise < 0) return { text: `Owes ${formatInr(-share.net_balance_paise)}`, tone: 'warning' }
  return { text: 'Settled', tone: 'muted' }
}

function ResidentCollectionPanel({
  month,
  data,
  onPay,
}: {
  month: string
  data: ElectricityMonth
  onPay: (share: ElectricityShare) => void
}) {
  // Who has paid what means something only once the month is recorded.
  const billed = data.expense != null
  const items: ResidentCollectionItem[] = data.shares.map((share) => {
    const balance = billed ? netBalance(share) : null
    return {
      id: share.resident_id,
      name: share.display_name,
      href: `/residents/${share.resident_id}`,
      subtitle: balance?.text,
      subtitleTone: balance?.tone,
      sharePaise: share.total_paise,
      paidIntoKittyPaise: billed ? share.settled_paise : null,
      stillOwedPaise: billed ? share.outstanding_paise : null,
      status: billed ? collectionStatus(share) : null,
      canPay: billed && share.outstanding_paise > 0 && share.status !== 'funded_payout',
      onPay: () => onPay(share),
    }
  })
  return (
    <Panel
      title="Resident Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each flatmate owes for this month's electricity -- their common share and their AC -- and how much has been collected into the House Kitty."
    >
      <ResidentCollectionTable caption={`Electricity owed for ${formatMonth(month)}`} items={items} />
    </Panel>
  )
}
