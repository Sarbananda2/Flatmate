/**
 * Broadband & WiFi: the flat's internet connection on a fixed monthly plan.
 *
 * Every figure comes from the backend. Each month's fixed rate is one ordinary
 * expense, split equally between the residents taking part, and paid in two
 * steps as the other services are: flatmates pay their shares into the House
 * Kitty, and the ISP is paid from it -- or by a resident from their own
 * account, whom the kitty pays back for what they paid beyond their share. A
 * payment or payout recorded by mistake can be deleted from its statement
 * while the month is open.
 *
 * Three tabs: the month's settlement, the plan and the WiFi network, and the
 * connection's settings. The open tab is kept in the address (`?tab=plan`).
 * No WiFi password is kept anywhere.
 */
import { useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Gauge, Receipt, Router, Settings, Trash2, Users, Wifi } from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection, Callout, ErrorNotice, Modal } from '../../components/Primitives'
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
import {
  PAYOUT_STATUS,
  ProviderPayoutStatementModal,
  ProviderPayoutTable,
} from '../../components/ProviderPayoutTable'
import { DeleteTransactionDialog, type PendingDeletion } from '../../components/DeleteTransactionDialog'
import { ServiceDisbursementDialog } from '../../components/ServiceDisbursementDialog'
import { PAYMENT_MODES, ServicePaymentDialog } from '../../components/ServicePaymentDialog'
import { useToast } from '../../components/Toast'
import { BUTTON_PRIMARY, BUTTON_QUIET, DetailFact, EmptyBlock, MiniStat, Panel, Pill, Rupees } from '../../components/ui'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { formatDate, formatInr, formatMonth, paiseToInput } from '../../lib/format'
import type {
  BroadbandConfig,
  BroadbandMonthBill,
  BroadbandOverview,
  BroadbandShare,
  KittyReimbursement,
} from '../../api/types'

const BASE = '/api/services/broadband'

/** The ISP is paid the bill: the payout table's and statement's column headings. */
const ISP_COLUMNS = { payee: 'ISP', total: 'Total Bill', paid: 'Paid to ISP' }

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]'

const DASH = <span className="text-[var(--text-tertiary)]">—</span>

/** The page's tabs: the open one is kept in the address (`?tab=plan`). */
export const BROADBAND_TABS = [
  { value: 'settlement', label: 'Monthly Settlement', icon: Receipt },
  { value: 'plan', label: 'Plan & WiFi', icon: Wifi },
  { value: 'config', label: 'Connection & Settings', icon: Settings },
] as const

export type BroadbandTab = (typeof BROADBAND_TABS)[number]['value']

type Dialog =
  | { kind: 'pay_share'; share: BroadbandShare }
  | { kind: 'share_statement'; residentId: string }
  | { kind: 'payout' }
  | { kind: 'payout_statement' }
  | { kind: 'reimburse'; reimbursement: KittyReimbursement }
  | { kind: 'reimbursement_statement'; residentId: string }
  | { kind: 'delete'; deletion: PendingDeletion; message: string }

export default function BroadbandPage() {
  const { month } = useApp()
  return <Broadband key={month} month={month} />
}

export function Broadband({ month }: { month: string }) {
  const overview = useResource<BroadbandOverview>(BASE, { month })
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: BroadbandTab = BROADBAND_TABS.some((entry) => entry.value === requested)
    ? (requested as BroadbandTab)
    : 'settlement'
  const toast = useToast()
  // The month as the last payout left it, for what its toast says is still owed.
  const saved = useRef<BroadbandMonthBill | null>(null)
  const done = (message: string, detail?: string) => {
    setDialog(null)
    toast.success(message, detail)
    overview.reload()
  }

  return (
    <AsyncSection resource={overview} loadingLabel="Loading broadband…">
      {(data) => (
        <Page
          title="Broadband & WiFi"
          subtitle="The flat's internet connection: a fixed monthly plan, shared equally and paid through the House Kitty."
          breadcrumbs={[{ label: 'Services & Utilities', to: '/services' }, { label: 'Broadband & WiFi' }]}
        >
          {!data.service || !data.config || !data.month ? (
            <Panel
              title="Set up broadband"
              icon={<Wifi className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
              hint="Enter the plan's fixed monthly rate once. Each month is then billed by itself and split equally."
            >
              <PlanForm config={null} month={month} onSaved={() => done('Broadband set up')} />
            </Panel>
          ) : (
            <>
              <div className="tabs" role="tablist" aria-label="Broadband & WiFi">
                {BROADBAND_TABS.map(({ value, label, icon: Icon }) => (
                  <button
                    key={value}
                    id={`broadband-tab-${value}`}
                    type="button"
                    role="tab"
                    className="tab"
                    aria-selected={tab === value}
                    aria-controls={`broadband-panel-${value}`}
                    onClick={() => setParams(value === 'settlement' ? {} : { tab: value }, { replace: true })}
                  >
                    <Icon className="w-3.5 h-3.5 shrink-0 inline-block mr-1.5 align-[-2px]" aria-hidden="true" />
                    {label}
                  </button>
                ))}
              </div>
              <div
                role="tabpanel"
                id={`broadband-panel-${tab}`}
                aria-labelledby={`broadband-tab-${tab}`}
                className="space-y-4"
              >
                {tab === 'settlement' && (
                  <>
                    <SettlementSummary bill={data.month} provider={data.config.provider_name} />
                    <CollectionPanel
                      month={month}
                      bill={data.month}
                      onPay={(share) => setDialog({ kind: 'pay_share', share })}
                      onViewStatement={(share) => setDialog({ kind: 'share_statement', residentId: share.resident_id })}
                    />
                    <PayoutPanel
                      bill={data.month}
                      config={data.config}
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
                {tab === 'plan' && (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
                    <ConnectionPanel config={data.config} bill={data.month} nextBillingDate={data.next_billing_date} />
                    <WifiPanel config={data.config} />
                  </div>
                )}
                {tab === 'config' && (
                  <Panel
                    title="Connection & Settings"
                    icon={<Settings className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
                    hint="The plan each month is billed from. A new rate moves this month's bill with it; a month whose statement is finalized keeps its own."
                  >
                    <PlanForm config={data.config} month={month} onSaved={() => done('Plan saved')} />
                  </Panel>
                )}
              </div>
            </>
          )}

          {dialog?.kind === 'pay_share' && (
            <ServicePaymentDialog
              title="Record Payment into House Kitty"
              month={month}
              serviceLabel="broadband"
              residentName={dialog.share.display_name}
              outstandingPaise={dialog.share.outstanding_paise}
              onSave={(payment) =>
                api.post<BroadbandOverview>(`${BASE}/record-payment`, {
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
          {dialog?.kind === 'share_statement' &&
            (() => {
              // The share as it now stands, not as it was when opened.
              const share = data.month?.shares.find((row) => row.resident_id === dialog.residentId)
              return (
                share &&
                data.month &&
                data.config && (
                  <BroadbandStatementModal
                    month={month}
                    share={share}
                    bill={data.month}
                    provider={data.config.provider_name}
                    onDeletePayment={
                      data.month.transactions_open
                        ? (payment) =>
                            setDialog({
                              kind: 'delete',
                              message: 'Payment deleted',
                              deletion: {
                                title: 'Delete this payment?',
                                what: `${share.display_name}'s ${formatInr(payment.amount_paise)} payment into the House Kitty of ${formatDate(payment.payment_date)}.`,
                                url: `${BASE}/payments/${encodeURIComponent(payment.payment_id)}`,
                              },
                            })
                        : undefined
                    }
                    onClose={() => setDialog(null)}
                  />
                )
              )
            })()}
          {dialog?.kind === 'payout' && data.month && data.config && (
            <ServiceDisbursementDialog
              title="Record ISP Payout"
              month={month}
              payeeName={data.config.provider_name}
              payeeRole="ISP"
              remainingPaise={data.month.remaining_disbursement_paise}
              totalBillPaise={data.month.total_paise}
              suggestedPaise={collectedForPayout(data.month)}
              residents={data.month.shares.map(({ resident_id, display_name }) => ({ resident_id, display_name }))}
              onSave={(payout) =>
                api
                  .post<BroadbandOverview>(`${BASE}/disburse-payout`, { settlement_month: month, ...payout })
                  .then((updated) => {
                    saved.current = updated.month
                  })
              }
              onClose={() => setDialog(null)}
              onDone={() => {
                const left = saved.current?.remaining_disbursement_paise ?? 0
                done(
                  'ISP payout recorded',
                  left > 0 ? `${formatInr(left)} is still owed to ${data.config?.provider_name}.` : undefined,
                )
              }}
            />
          )}
          {dialog?.kind === 'payout_statement' && data.month && data.config && (
            <ProviderPayoutStatementModal
              title="ISP Payout Statement"
              name={data.config.provider_name}
              month={month}
              columns={ISP_COLUMNS}
              totalPaise={data.month.total_paise}
              paidPaise={data.month.disbursed_paise}
              stillOwedPaise={data.month.remaining_disbursement_paise}
              status={data.month.payout_status}
              disbursements={data.month.disbursements}
              onDeleteDisbursement={
                data.month.transactions_open
                  ? (tranche) =>
                      setDialog({
                        kind: 'delete',
                        message: 'Payout deleted',
                        deletion: {
                          title: 'Delete this payout?',
                          what:
                            tranche.disbursed_by === 'pool'
                              ? `The House Kitty's ${formatInr(tranche.amount_paise)} payout to ${data.config?.provider_name} of ${formatDate(tranche.disbursed_at)}.`
                              : `${tranche.funding_resident_name ?? 'A resident'}'s ${formatInr(tranche.amount_paise)} payout to ${data.config?.provider_name} of ${formatDate(tranche.disbursed_at)}, with their share paid through the House Kitty.`,
                          url: `${BASE}/disbursements/${encodeURIComponent(tranche.disbursement_id)}`,
                        },
                      })
                  : undefined
              }
              onClose={() => setDialog(null)}
            />
          )}
          {dialog?.kind === 'reimburse' && (
            <ReimburseDialog
              month={month}
              reimbursement={dialog.reimbursement}
              submit={({ note, ...details }) =>
                api.post<BroadbandOverview>(`${BASE}/reimburse-from-kitty`, {
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
              // Read from the month as it now stands, so a deleted reimbursement drops out at once.
              const owed = data.month?.reimbursements.find((row) => row.recipient_resident_id === dialog.residentId)
              return (
                owed && (
                  <KittyReimbursementStatementModal
                    month={month}
                    reimbursement={owed}
                    onDelete={
                      data.month?.transactions_open
                        ? (refund) =>
                            setDialog({
                              kind: 'delete',
                              message: 'Reimbursement deleted',
                              deletion: {
                                title: 'Delete this reimbursement?',
                                what: `The House Kitty's ${formatInr(refund.amount_paise)} reimbursement to ${owed.recipient_name} of ${formatDate(refund.payment_date)}.`,
                                url: `${BASE}/payments/${encodeURIComponent(refund.payment_id)}`,
                              },
                            })
                        : undefined
                    }
                    onClose={() => setDialog(null)}
                  />
                )
              )
            })()}
          {dialog?.kind === 'delete' && (
            <DeleteTransactionDialog
              deletion={dialog.deletion}
              onClose={() => setDialog(null)}
              onDeleted={() => done(dialog.message, 'The month owes what it owed before.')}
            />
          )}
        </Page>
      )}
    </AsyncSection>
  )
}

/**
 * The ISP is due on a fixed day and flatmates pay when they can, so a first
 * payout offers what the kitty has collected so far; after that, the rest.
 */
function collectedForPayout(bill: BroadbandMonthBill): number | undefined {
  return bill.disbursements.length === 0 && bill.collected_paise > 0 && bill.collected_paise < bill.total_paise
    ? bill.collected_paise
    : undefined
}

/* ---------------------------------------------------- 1. the settlement -- */

/** The month at a glance: what it bills, what has come into the kitty, and the ISP's side. */
function SettlementSummary({ bill, provider }: { bill: BroadbandMonthBill; provider: string }) {
  const billed = bill.expense != null
  const sharing = bill.shares.filter((share) => share.total_paise > 0).length
  const status = PAYOUT_STATUS[bill.payout_status]
  const each = bill.participant_count > 0 ? `${formatInr(bill.base_share_paise)} each` : null
  return (
    <section aria-label="Month summary" className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      <MiniStat
        label="Total Bill"
        value={<Rupees paise={bill.total_paise} />}
        note={[each, bill.due_date && `due ${formatDate(bill.due_date)}`].filter(Boolean).join(' · ') || undefined}
      />
      <MiniStat
        label="Paid into Kitty"
        value={billed ? <Rupees paise={bill.collected_paise} /> : DASH}
        note={billed ? `${bill.collected_count} of ${sharing} flatmates paid` : 'Not billed yet'}
      />
      <MiniStat
        label="Payout Status"
        value={billed ? <Pill tone={status.tone}>{status.label}</Pill> : DASH}
        note={
          !billed
            ? undefined
            : bill.remaining_disbursement_paise > 0
              ? `${formatInr(bill.remaining_disbursement_paise)} still owed to ${provider}`
              : `Paid to ${provider} in full`
        }
      />
    </section>
  )
}

function collectionStatus(share: BroadbandShare): CollectionStatus | null {
  if (share.status === 'paid' || share.status === 'funded_payout') return 'paid'
  return share.status
}

function CollectionPanel({
  month,
  bill,
  onPay,
  onViewStatement,
}: {
  month: string
  bill: BroadbandMonthBill
  onPay: (share: BroadbandShare) => void
  onViewStatement: (share: BroadbandShare) => void
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
    onViewStatement: billed ? () => onViewStatement(share) : undefined,
  }))
  return (
    <Panel
      title="Resident Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Each flatmate's equal share of this month's broadband, and how much has been collected into the House Kitty. Select a row for its payments."
    >
      {bill.blocked_reason && !billed && (
        <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
          {bill.blocked_reason}
        </p>
      )}
      {bill.shares.length === 0 ? (
        <EmptyBlock title="Nobody is sharing the broadband this month." />
      ) : (
        <ResidentCollectionTable caption={`Broadband shares for ${formatMonth(month)}`} items={items} />
      )}
    </Panel>
  )
}

function PayoutPanel({
  bill,
  config,
  onPayout,
  onViewStatement,
}: {
  bill: BroadbandMonthBill
  config: BroadbandConfig
  onPayout: () => void
  onViewStatement: () => void
}) {
  const billed = bill.expense != null
  return (
    <Panel
      title="ISP Payout"
      icon={<Router className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What the ISP is owed for the month, and payouts made to it. Select the row for its disbursement history."
    >
      <ProviderPayoutTable
        caption={`The broadband bill owed to ${config.provider_name}`}
        columns={ISP_COLUMNS}
        items={[
          {
            id: 'isp',
            name: config.provider_name,
            subtitle: config.account_number ? `Account ${config.account_number}` : undefined,
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

/** One flatmate's side of the month: their share, what they paid, and each payment. */
function BroadbandStatementModal({
  month,
  share,
  bill,
  provider,
  onDeletePayment,
  onClose,
}: {
  month: string
  share: BroadbandShare
  bill: BroadbandMonthBill
  provider: string
  /** Deletes a payment recorded by mistake, while the month is open; without it none is offered. */
  onDeletePayment?: (payment: BroadbandShare['payments'][number]) => void
  onClose: () => void
}) {
  // What they paid the ISP beyond their own share, which the kitty pays back.
  const extra = bill.disbursements
    .filter((tranche) => tranche.funding_resident_id === share.resident_id)
    .reduce((total, tranche) => total + tranche.amount_paise, 0)
  const modes = new Map(PAYMENT_MODES.map((mode) => [mode.value, mode.label]))
  return (
    <Modal title="Broadband Statement" description={`${share.display_name} · ${formatMonth(month)}`} onClose={onClose}>
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat label="Share" value={<Rupees paise={share.total_paise} />} />
          <MiniStat label="Paid into Kitty" value={<Rupees paise={share.settled_paise} />} />
          <MiniStat label="Still Owed" value={<Rupees paise={share.outstanding_paise} />} />
        </div>

        {extra > 0 && (
          <Callout>
            {share.display_name} paid {provider} an extra {formatInr(extra)} beyond their allotted share of{' '}
            {formatInr(share.total_paise)}, which will be reimbursed via the House Kitty.
          </Callout>
        )}

        <section aria-label="Payment history" className="space-y-3">
          <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Payment History</h3>
          {share.payments.length === 0 ? (
            <EmptyBlock title="No payments recorded yet for this month." />
          ) : (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[480px] border-collapse">
                <caption className="sr-only">Payments towards {share.display_name}'s broadband share</caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Date</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Amount</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Mode</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Reference / UTR</th>
                    {onDeletePayment && <th scope="col" className="pb-3 font-bold text-center">Actions</th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {share.payments.map((payment) => (
                    <tr key={payment.payment_id} className="align-middle">
                      <td className="py-2.5 pr-3 text-center tabular">{formatDate(payment.payment_date)}</td>
                      <td className="py-2.5 pr-3 text-center font-bold text-[var(--text-primary)]">
                        <Rupees paise={payment.amount_paise} />
                      </td>
                      <td className="py-2.5 pr-3 text-center">
                        {payment.payment_mode ? modes.get(payment.payment_mode) : DASH}
                      </td>
                      <td className="py-2.5 pr-3 text-center tabular break-all text-[var(--text-primary)]">
                        {payment.reference || DASH}
                      </td>
                      {onDeletePayment && (
                        <td className="py-2.5 text-center">
                          <button
                            type="button"
                            className={BUTTON_QUIET}
                            onClick={() => onDeletePayment(payment)}
                            aria-label={`Delete the ${formatInr(payment.amount_paise)} payment of ${formatDate(payment.payment_date)}`}
                          >
                            <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                            Delete
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </Modal>
  )
}

/* ------------------------------------------------- 2. the plan and WiFi -- */

const notSet = <span className="text-[var(--text-tertiary)]">Not set</span>

function ConnectionPanel({
  config,
  bill,
  nextBillingDate,
}: {
  config: BroadbandConfig
  bill: BroadbandMonthBill
  nextBillingDate: string | null
}) {
  return (
    <Panel
      title="ISP Connection"
      icon={<Gauge className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Who the flat's internet comes from, and what the plan costs."
    >
      <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm" aria-label="ISP connection">
        <DetailFact label="Provider">{config.provider_name}</DetailFact>
        <DetailFact label="Account Number">
          {config.account_number ? <span className="tabular break-all">{config.account_number}</span> : notSet}
        </DetailFact>
        <DetailFact label="Speed">{config.speed_mbps ? `${config.speed_mbps} Mbps` : notSet}</DetailFact>
        <DetailFact label="Due Date">
          {config.billing_day_of_month
            ? `Day ${config.billing_day_of_month} of each month${nextBillingDate ? ` · next ${formatDate(nextBillingDate)}` : ''}`
            : 'No billing day set'}
        </DetailFact>
        <DetailFact label="Monthly Rate">
          <span className="tabular">
            {formatInr(config.monthly_rate_paise)}
            {bill.participant_count > 0 && ` · ${formatInr(bill.base_share_paise)} each`}
          </span>
        </DetailFact>
        <DetailFact label="Split Rule">Equal share among all active flatmates</DetailFact>
      </dl>
    </Panel>
  )
}

function WifiPanel({ config }: { config: BroadbandConfig }) {
  return (
    <Panel
      title="WiFi Network"
      icon={<Wifi className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What flatmates and guests need to get online."
    >
      <dl className="m-0 grid grid-cols-1 gap-4 text-sm" aria-label="WiFi network">
        <DetailFact label="Network Name (SSID)">
          {config.wifi_ssid ? <span className="font-semibold break-all">{config.wifi_ssid}</span> : notSet}
        </DetailFact>
        <DetailFact label="Router & Notes">
          {config.notes ? <span className="whitespace-pre-line">{config.notes}</span> : notSet}
        </DetailFact>
      </dl>
      <p className="m-0 mt-4 text-xs text-[var(--text-secondary)]">
        The WiFi password is not stored here. Share it with flatmates in person.
      </p>
    </Panel>
  )
}

/* ------------------------------------------------------ 3. the settings -- */

/** A whole number as typed, from `low` to `high`, or empty for none. */
function parseWhole(raw: string, low: number, high: number): { ok: boolean; value: number | null } {
  const text = raw.trim()
  if (!text) return { ok: true, value: null }
  const value = Number(text)
  return /^\d+$/.test(text) && value >= low && value <= high ? { ok: true, value } : { ok: false, value: null }
}

function PlanForm({ config, month, onSaved }: { config: BroadbandConfig | null; month: string; onSaved: () => void }) {
  const [provider, setProvider] = useState(config?.provider_name ?? 'Airtel Xstream Fiber')
  const [account, setAccount] = useState(config?.account_number ?? '')
  const [rate, setRate] = useState(config ? paiseToInput(config.monthly_rate_paise) : '')
  const [ratePaise, setRatePaise] = useState(config?.monthly_rate_paise ?? 0)
  const [rateOk, setRateOk] = useState(Boolean(config))
  const [speedRaw, setSpeedRaw] = useState(config?.speed_mbps ? String(config.speed_mbps) : '')
  const speed = parseWhole(speedRaw, 1, 100000)
  const [dayRaw, setDayRaw] = useState(config?.billing_day_of_month ? String(config.billing_day_of_month) : '')
  const day = parseWhole(dayRaw, 1, 31)
  const [ssid, setSsid] = useState(config?.wifi_ssid ?? '')
  const [notes, setNotes] = useState(config?.notes ?? '')
  const valid = provider.trim().length > 0 && rateOk && speed.ok && day.ok
  const save = useMutation(
    () =>
      api.post<BroadbandOverview>(`${BASE}/setup?month=${encodeURIComponent(month)}`, {
        provider_name: provider.trim(),
        account_number: account.trim() || null,
        monthly_rate_paise: ratePaise,
        speed_mbps: speed.value,
        billing_day_of_month: day.value,
        wifi_ssid: ssid.trim() || null,
        notes: notes.trim() || null,
      }),
    { onSuccess: onSaved },
  )
  return (
    <form
      className="stack"
      noValidate
      aria-label="Broadband plan"
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        if (valid && !save.pending) void save.run(undefined)
      }}
    >
      <ErrorNotice error={save.error} />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <TextField label="Provider name" required value={provider} onChange={setProvider} maxLength={100} />
        <TextField
          label="Account number"
          value={account}
          onChange={setAccount}
          maxLength={100}
          hint="The ISP's account or customer ID, as on its bill."
        />
        <MoneyInput
          label="Monthly rate"
          required
          value={rate}
          onChange={(raw, parsed) => {
            setRate(raw)
            setRatePaise(parsed.paise)
            setRateOk(parsed.ok)
          }}
          hint="What the plan costs a month, taxes included. Split equally."
        />
        <TextField
          label="Speed (Mbps)"
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          value={speedRaw}
          onChange={setSpeedRaw}
          error={speed.ok ? null : 'Enter a speed in whole Mbps.'}
        />
        <TextField
          label="Due day"
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          value={dayRaw}
          onChange={setDayRaw}
          error={day.ok ? null : 'Enter a day from 1 to 31.'}
          hint="The day of the month the ISP's bill is due."
        />
        <TextField
          label="Wi-Fi SSID"
          value={ssid}
          onChange={setSsid}
          maxLength={32}
          hint="The network's name. Its password is never stored."
        />
      </div>
      <TextAreaField
        label="Notes"
        value={notes}
        onChange={setNotes}
        rows={3}
        hint="Where the router is, OTT subscriptions bundled with the plan, the ISP's helpline."
      />
      <div className="flex justify-end">
        <button type="submit" className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
          {save.pending ? 'Saving…' : config ? 'Save plan' : 'Set up broadband'}
        </button>
      </div>
    </form>
  )
}
