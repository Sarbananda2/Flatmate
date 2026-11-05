/**
 * House help and cooks: the household's maids, cooks and cleaners, each
 * month's attendance and salaries, and paying them through the House Kitty.
 *
 * A worker does one role or several, each on its own monthly salary, leave
 * allowance and split: shared by every flatmate, or only by those who opted
 * in. Saving a month's attendance bills it -- leave beyond the allowance
 * deducted pro rata or waived, a bonus added -- and every saved figure comes
 * from the backend. Flatmates pay their shares into the kitty, and each worker
 * is paid from it, or by a flatmate whom the kitty pays back the rest.
 *
 * Three tabs, as the other services have them: the month's settlement, its
 * attendance and salaries, and the staff and their roles. The open tab is kept
 * in the address (`?tab=attendance`).
 */
import { useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarCheck, HandPlatter, Pencil, Plus, Receipt, Save, Trash2, UserPlus, Users, Wallet } from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection, Callout, ConfirmDialog, ErrorNotice, Modal } from '../../components/Primitives'
import { CheckboxField, DateField, MoneyInput, SelectField, SwitchField, TextAreaField, TextField } from '../../components/Form'
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
  ProviderPayoutStatementModal,
  ProviderPayoutTable,
  type PayoutTranche,
} from '../../components/ProviderPayoutTable'
import { ServiceDisbursementDialog } from '../../components/ServiceDisbursementDialog'
import { PAYMENT_MODES, ServicePaymentDialog } from '../../components/ServicePaymentDialog'
import { DeleteTransactionDialog, type PendingDeletion } from '../../components/DeleteTransactionDialog'
import { useToast } from '../../components/Toast'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  EmptyBlock,
  MiniStat,
  Panel,
  Pill,
  Rupees,
  TransferDestination,
} from '../../components/ui'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { formatDate, formatInr, formatMonth, paiseToInput } from '../../lib/format'
import type {
  HouseHelpLeaveAction,
  HouseHelpMonthOverview,
  HouseHelpMonthlyRecord,
  HouseHelpPayout,
  HouseHelpRole,
  HouseHelpRoleMonth,
  HouseHelpShare,
  HouseHelpSplitMethod,
  HouseHelpStaff,
  HouseHelpWorker,
  HouseHelpWorkerPayout,
  KittyReimbursement,
  Resident,
} from '../../api/types'

const BASE = '/api/services/househelp'

/** The page's tabs, as the other services have them: the open one is kept in the address (`?tab=staff`). */
export const HOUSEHELP_TABS = [
  { value: 'settlement', label: 'Monthly Settlement', icon: Receipt },
  { value: 'attendance', label: 'Attendance & Salaries', icon: CalendarCheck },
  { value: 'staff', label: 'Staff & Roles', icon: Users },
] as const

export type HouseHelpTab = (typeof HOUSEHELP_TABS)[number]['value']

type Dialog =
  | { kind: 'pay_share'; share: HouseHelpShare }
  | { kind: 'share_statement'; residentId: string }
  | { kind: 'pay_worker'; workerId: string }
  | { kind: 'worker_statement'; workerId: string }
  | { kind: 'reimburse'; reimbursement: KittyReimbursement }
  | { kind: 'reimbursement_statement'; residentId: string }
  | { kind: 'delete'; deletion: PendingDeletion; message: string }
  | { kind: 'worker'; worker: HouseHelpWorker | null }
  | { kind: 'role'; role: HouseHelpRole | null }
  | { kind: 'delete_role'; role: HouseHelpRole }

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]'

const DASH = <span className="text-[var(--text-tertiary)]">—</span>

export default function HouseHelpPage() {
  const { month } = useApp()
  return <HouseHelp key={month} month={month} />
}

export function HouseHelp({ month }: { month: string }) {
  const overview = useResource<HouseHelpMonthOverview>(`${BASE}/month/${month}`)
  const staff = useResource<HouseHelpStaff>(`${BASE}/workers`)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: HouseHelpTab = HOUSEHELP_TABS.some((entry) => entry.value === requested)
    ? (requested as HouseHelpTab)
    : 'settlement'
  const openTab = (value: HouseHelpTab) => setParams(value === 'settlement' ? {} : { tab: value }, { replace: true })
  const toast = useToast()
  // The month as the last payout left it, for what its toast says is still owed.
  const saved = useRef<HouseHelpMonthOverview | null>(null)
  const reload = () => {
    overview.reload()
    staff.reload()
  }
  const done = (message: string, detail?: string) => {
    setDialog(null)
    toast.success(message, detail)
    reload()
  }

  return (
    <AsyncSection resource={overview} loadingLabel="Loading house help…">
      {(data) => (
        <Page
          title="House Help & Cook"
          subtitle="The household's maids and cooks: each month's salaries from attendance, shared by role and paid through the House Kitty."
          breadcrumbs={[{ label: 'Services & Utilities', to: '/services' }, { label: 'House Help & Cook' }]}
        >
          <div className="tabs" role="tablist" aria-label="House Help & Cook">
            {HOUSEHELP_TABS.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                id={`househelp-tab-${value}`}
                type="button"
                role="tab"
                className="tab"
                aria-selected={tab === value}
                aria-controls={`househelp-panel-${value}`}
                onClick={() => openTab(value)}
              >
                <Icon className="w-3.5 h-3.5 shrink-0 inline-block mr-1.5 align-[-2px]" aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`househelp-panel-${tab}`}
            aria-labelledby={`househelp-tab-${tab}`}
            className="space-y-4"
          >
            {tab === 'settlement' &&
              (data.roles.length === 0 ? (
                <NoStaffYet onOpenStaff={() => openTab('staff')} />
              ) : (
                <>
                  <CollectionPanel
                    month={month}
                    data={data}
                    onPay={(share) => setDialog({ kind: 'pay_share', share })}
                    onViewStatement={(share) => setDialog({ kind: 'share_statement', residentId: share.resident_id })}
                  />
                  <WorkerPayoutPanel
                    month={month}
                    data={data}
                    onPay={(worker) => setDialog({ kind: 'pay_worker', workerId: worker.worker_id })}
                    onViewStatement={(worker) => setDialog({ kind: 'worker_statement', workerId: worker.worker_id })}
                  />
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
              ))}
            {tab === 'attendance' && (
              <AsyncSection resource={staff} loadingLabel="Loading roles…">
                {({ items }) => (
                  <AttendancePanel
                    month={month}
                    data={data}
                    workers={items}
                    onOpenStaff={() => openTab('staff')}
                    onSaved={(updated, message) =>
                      done(
                        message,
                        updated.total_paise > 0
                          ? `${formatMonth(month)}'s salaries come to ${formatInr(updated.total_paise)}.`
                          : undefined,
                      )
                    }
                  />
                )}
              </AsyncSection>
            )}
            {tab === 'staff' && (
              <AsyncSection resource={staff} loadingLabel="Loading staff…">
                {({ items, unassigned_roles }) => (
                  <>
                    <WorkersPanel
                      workers={items}
                      onAdd={() => setDialog({ kind: 'worker', worker: null })}
                      onEdit={(worker) => setDialog({ kind: 'worker', worker })}
                    />
                    <RolesPanel
                      workers={items}
                      unassigned={unassigned_roles}
                      onAdd={() => setDialog({ kind: 'role', role: null })}
                      onEdit={(role) => setDialog({ kind: 'role', role })}
                    />
                  </>
                )}
              </AsyncSection>
            )}
          </div>

          {dialog?.kind === 'pay_share' && (
            <ServicePaymentDialog
              title="Record Payment into House Kitty"
              month={month}
              serviceLabel="house help"
              residentName={dialog.share.display_name}
              outstandingPaise={dialog.share.outstanding_paise}
              onSave={(payment) =>
                api.post<HouseHelpMonthOverview>(`${BASE}/record-payment`, {
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
              const share = data.shares.find((row) => row.resident_id === dialog.residentId)
              return (
                share && (
                  <HouseHelpStatementModal
                    month={month}
                    share={share}
                    reimbursement={data.reimbursements.find((row) => row.recipient_resident_id === share.resident_id)}
                    onDeletePayment={
                      data.transactions_open
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
          {dialog?.kind === 'pay_worker' &&
            (() => {
              const worker = data.workers.find((row) => row.worker_id === dialog.workerId)
              return (
                worker && (
                  <ServiceDisbursementDialog
                    title="Pay Worker"
                    month={month}
                    payeeName={worker.name}
                    payeeRole="worker"
                    remainingPaise={worker.still_owed_paise}
                    totalBillPaise={worker.total_due_paise}
                    residents={data.shares.map(({ resident_id, display_name }) => ({ resident_id, display_name }))}
                    destination={(amount) => (
                      <TransferDestination
                        name={worker.name}
                        amountPaise={amount}
                        upiId={worker.upi_id}
                        missing="No UPI ID recorded for them: add it under Staff & Roles."
                      />
                    )}
                    onSave={(payout) =>
                      api
                        .post<HouseHelpMonthOverview>(`${BASE}/disburse-payout`, {
                          settlement_month: month,
                          worker_id: worker.worker_id,
                          ...payout,
                        })
                        .then((updated) => {
                          saved.current = updated
                        })
                    }
                    onClose={() => setDialog(null)}
                    onDone={() => {
                      const left =
                        saved.current?.workers.find((row) => row.worker_id === worker.worker_id)?.still_owed_paise ?? 0
                      done(
                        'Worker payout recorded',
                        left > 0
                          ? `${formatInr(left)} is still owed to ${worker.name}.`
                          : `${worker.name} is paid in full for ${formatMonth(month)}.`,
                      )
                    }}
                  />
                )
              )
            })()}
          {dialog?.kind === 'worker_statement' &&
            (() => {
              const worker = data.workers.find((row) => row.worker_id === dialog.workerId)
              return (
                worker && (
                  <WorkerPayoutStatementModal
                    month={month}
                    worker={worker}
                    onDeletePayout={
                      data.transactions_open
                        ? (payout) =>
                            setDialog({
                              kind: 'delete',
                              message: 'Payout deleted',
                              deletion: {
                                title: 'Delete this payout?',
                                what:
                                  payout.paid_by === 'kitty'
                                    ? `The House Kitty's ${formatInr(payout.amount_paise)} payout to ${worker.name} of ${formatDate(payout.paid_on)}.`
                                    : `${payout.resident_name}'s ${formatInr(payout.amount_paise)} payout to ${worker.name} of ${formatDate(payout.paid_on)}, with their share paid through the House Kitty.`,
                                url: `${BASE}/month/${month}/disbursements/${encodeURIComponent(payout.payout_id)}`,
                              },
                            })
                        : undefined
                    }
                    onClose={() => setDialog(null)}
                  />
                )
              )
            })()}
          {dialog?.kind === 'reimburse' && (
            <ReimburseDialog
              month={month}
              reimbursement={dialog.reimbursement}
              submit={({ note, payment_date, ...details }) =>
                api.post<HouseHelpMonthOverview>(`${BASE}/reimburse-from-kitty`, {
                  settlement_month: month,
                  recipient_resident_id: dialog.reimbursement.recipient_resident_id,
                  ...details,
                  disbursed_date: payment_date,
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
              const owed = data.reimbursements.find((row) => row.recipient_resident_id === dialog.residentId)
              return (
                owed && (
                  <KittyReimbursementStatementModal
                    month={month}
                    reimbursement={owed}
                    onDelete={
                      data.transactions_open
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
              onDeleted={() => done(dialog.message)}
            />
          )}
          {dialog?.kind === 'worker' && (
            <WorkerDialog
              worker={dialog.worker}
              onClose={() => setDialog(null)}
              onSaved={(worker) => done(dialog.worker ? 'Helper saved' : 'Helper added', worker.name)}
            />
          )}
          {dialog?.kind === 'role' && staff.data && (
            <RoleDialog
              role={dialog.role}
              workers={staff.data.items}
              onClose={() => setDialog(null)}
              onDelete={dialog.role ? (role) => setDialog({ kind: 'delete_role', role }) : undefined}
              onSaved={(role) =>
                done(dialog.role ? 'Role saved' : 'Role added', roleLabel(role))
              }
            />
          )}
          {dialog?.kind === 'delete_role' && (
            <DeleteRoleDialog
              role={dialog.role}
              onClose={() => setDialog(null)}
              onDeleted={() => done('Role deleted', roleLabel(dialog.role))}
            />
          )}
        </Page>
      )}
    </AsyncSection>
  )
}

function NoStaffYet({ onOpenStaff }: { onOpenStaff: () => void }) {
  return (
    <Panel
      title="Set up house help"
      icon={<HandPlatter className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
    >
      <EmptyBlock
        title="No helper has a role yet."
        body="Add each helper and the roles they do, with each role's salary and who shares it. Every month's salaries are then saved from its attendance."
        action={
          <button type="button" className={BUTTON_PRIMARY} onClick={onOpenStaff}>
            Go to Staff & Roles
          </button>
        }
      />
    </Panel>
  )
}

/* ---------------------------------------------------- 1. the settlement -- */

function collectionStatus(share: HouseHelpShare): CollectionStatus | null {
  if (share.status === 'paid' || share.status === 'funded_payout') return 'paid'
  return share.status
}

function MonthNotice({ data, month }: { data: HouseHelpMonthOverview; month: string }) {
  if (data.blocked_reason) {
    return (
      <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
        {data.blocked_reason}
      </p>
    )
  }
  if (data.bill_state === 'recorded_elsewhere') {
    return (
      <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
        {formatMonth(month)}&apos;s house help is already recorded as an expense entered by hand.
      </p>
    )
  }
  if (!data.saved) {
    return (
      <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
        {data.bill_state === 'upcoming'
          ? `${formatMonth(month)} has not begun: these are its salaries in full, nobody absent.`
          : `Not saved yet: a preview at full salary. Save ${formatMonth(month)}'s attendance to bill it.`}
      </p>
    )
  }
  return null
}

function CollectionPanel({
  month,
  data,
  onPay,
  onViewStatement,
}: {
  month: string
  data: HouseHelpMonthOverview
  onPay: (share: HouseHelpShare) => void
  onViewStatement: (share: HouseHelpShare) => void
}) {
  // Who has paid what means something only once the month is saved.
  const billed = data.expense != null
  const items: ResidentCollectionItem[] = data.shares.map((share) => ({
    id: share.resident_id,
    name: share.display_name,
    href: `/residents/${share.resident_id}`,
    sharePaise: share.total_paise,
    paidIntoKittyPaise: billed ? share.settled_paise : null,
    stillOwedPaise: billed ? share.outstanding_paise : null,
    status: billed ? collectionStatus(share) : null,
    canPay: billed && share.outstanding_paise > 0 && share.status !== 'funded_payout',
    onPay: () => onPay(share),
    onViewStatement: () => onViewStatement(share),
  }))
  return (
    <Panel
      title="Resident Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Each flatmate's share of the month's salaries, by the roles they share, and how much has come into the House Kitty. Select a row for their statement."
    >
      <MonthNotice data={data} month={month} />
      {data.shares.length === 0 ? (
        <EmptyBlock title="Nobody is sharing house help this month." />
      ) : (
        <ResidentCollectionTable caption={`House help shares for ${formatMonth(month)}`} items={items} />
      )}
    </Panel>
  )
}

function WorkerPayoutPanel({
  month,
  data,
  onPay,
  onViewStatement,
}: {
  month: string
  data: HouseHelpMonthOverview
  onPay: (worker: HouseHelpWorkerPayout) => void
  onViewStatement: (worker: HouseHelpWorkerPayout) => void
}) {
  const billed = data.expense != null
  return (
    <Panel
      title="Worker Payouts"
      icon={<Wallet className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each helper is owed for the month, and payouts made to them. Select a row for their payout history."
    >
      {data.workers.length === 0 ? (
        <EmptyBlock title="No helper has a salary this month." />
      ) : (
        <ProviderPayoutTable
          caption={`House help salaries for ${formatMonth(month)}`}
          columns={{ payee: 'Worker', total: 'Salary Due', paid: 'Paid to Worker' }}
          items={data.workers.map((worker) => ({
            id: worker.worker_id,
            name: worker.name,
            totalPaise: worker.total_due_paise,
            // Nothing is paid or owed until the month is saved.
            paidPaise: billed ? worker.paid_to_worker_paise : null,
            stillOwedPaise: billed ? worker.still_owed_paise : null,
            status: worker.payout_status,
            canPay: billed && worker.still_owed_paise > 0,
            onPay: () => onPay(worker),
            onViewStatement: billed ? () => onViewStatement(worker) : undefined,
          }))}
        />
      )}
    </Panel>
  )
}

/** A payout as the payout statement lists it: a flatmate's, however it was split, is one row. */
function asTranche(payout: HouseHelpPayout): PayoutTranche {
  return {
    disbursement_id: payout.payout_id,
    disbursed_at: payout.paid_on,
    amount_paise: payout.amount_paise,
    disbursed_by: payout.paid_by === 'kitty' ? 'pool' : 'resident_advance',
    funding_resident_id: payout.resident_id,
    funding_resident_name: payout.resident_name,
    disbursement_reference: payout.reference,
    note: payout.note,
  }
}

/** A worker's month: their salary for it, what has been paid to them, and every payout (Rule U7). */
function WorkerPayoutStatementModal({
  month,
  worker,
  onDeletePayout,
  onClose,
}: {
  month: string
  worker: HouseHelpWorkerPayout
  /** Deletes a payout recorded by mistake, while the month is open; without it none is offered. */
  onDeletePayout?: (payout: HouseHelpPayout) => void
  onClose: () => void
}) {
  const byId = new Map(worker.payouts.map((payout) => [payout.payout_id, payout]))
  return (
    <ProviderPayoutStatementModal
      title="Worker Payout Statement"
      name={worker.name}
      month={month}
      columns={{ payee: 'Worker', total: 'Salary Due', paid: 'Paid to Worker' }}
      totalPaise={worker.total_due_paise}
      paidPaise={worker.paid_to_worker_paise}
      stillOwedPaise={worker.still_owed_paise}
      status={worker.payout_status ?? 'pending'}
      disbursements={worker.payouts.map(asTranche)}
      caption={`Payouts to ${worker.name}`}
      onDeleteDisbursement={
        onDeletePayout ? (tranche) => onDeletePayout(byId.get(tranche.disbursement_id)!) : undefined
      }
      onClose={onClose}
    />
  )
}

/**
 * A flatmate's house help for the month: their share, role by role, what they
 * have paid into the kitty towards it, and what the kitty owes them back for
 * paying a worker beyond it.
 */
function HouseHelpStatementModal({
  month,
  share,
  reimbursement,
  onDeletePayment,
  onClose,
}: {
  month: string
  share: HouseHelpShare
  reimbursement?: KittyReimbursement
  /** Deletes a payment recorded by mistake, while the month is open; without it none is offered. */
  onDeletePayment?: (payment: HouseHelpShare['payments'][number]) => void
  onClose: () => void
}) {
  // Nothing is owed, or paid, until the month is saved.
  const billed = share.status != null
  const modes = new Map(PAYMENT_MODES.map((mode) => [mode.value, mode.label]))
  const extra = reimbursement?.net_reimbursement_owed_paise ?? 0
  const pending = reimbursement?.pending_reimbursement_paise ?? 0
  return (
    <Modal title="House Help Statement" description={`${share.display_name} · ${formatMonth(month)}`} onClose={onClose}>
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat label="Share" value={<Rupees paise={share.total_paise} />} />
          <MiniStat label="Paid into Kitty" value={billed ? <Rupees paise={share.settled_paise} /> : DASH} />
          <MiniStat label="Still Owed" value={billed ? <Rupees paise={share.outstanding_paise} /> : DASH} />
        </div>

        {reimbursement && extra > 0 && (
          <Callout>
            {share.display_name} paid {formatInr(reimbursement.total_fronted_paise)} towards the house help,{' '}
            {formatInr(extra)} beyond their share of {formatInr(share.total_paise)},{' '}
            {pending === 0
              ? 'which the House Kitty has reimbursed.'
              : pending < extra
                ? `which will be reimbursed via the House Kitty: ${formatInr(pending)} still to come.`
                : 'which will be reimbursed via the House Kitty.'}
          </Callout>
        )}

        {share.breakdown.length > 0 && (
          <section aria-label="Share breakdown" className="space-y-2">
            <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Share Breakdown</h3>
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs border-collapse">
                <caption className="sr-only">Role breakdown of house help share</caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className="pb-2.5 pr-3 font-bold text-left">
                      Role
                    </th>
                    <th scope="col" className="pb-2.5 pr-3 font-bold text-center">
                      Helper
                    </th>
                    <th scope="col" className="pb-2.5 font-bold text-right">
                      Your Share
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {share.breakdown.map((part) => (
                    <tr key={part.role_id} className="align-middle">
                      <td className="py-2.5 pr-3 text-left font-semibold text-[var(--text-primary)]">{part.role_name}</td>
                      <td className="py-2.5 pr-3 text-center text-[var(--text-secondary)]">{part.worker_name}</td>
                      <td className="py-2.5 text-right tabular font-bold text-[var(--text-primary)]">
                        <Rupees paise={part.share_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-[var(--border-subtle)] font-bold text-[var(--text-primary)]">
                    <th scope="row" colSpan={2} className="pt-2.5 pr-3 text-left">
                      Total Share
                    </th>
                    <td className="pt-2.5 text-right tabular">
                      <Rupees paise={share.total_paise} />
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </section>
        )}

        <section aria-label="Payment history" className="space-y-3">
          <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Payment History</h3>
          {share.payments.length === 0 ? (
            <EmptyBlock title="No payments recorded yet for this month." />
          ) : (
            <>
              <div className="overflow-x-auto min-w-0">
                <table className="w-full text-left text-xs min-w-[480px] border-collapse">
                  <caption className="sr-only">Payments towards {share.display_name}'s house help share</caption>
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
                          {payment.payout_id
                            ? 'Paid to worker'
                            : payment.payment_mode
                              ? modes.get(payment.payment_mode)
                              : DASH}
                        </td>
                        <td className="py-2.5 pr-3 text-center tabular break-all text-[var(--text-primary)]">
                          {payment.reference || DASH}
                        </td>
                        {onDeletePayment && (
                          <td className="py-2.5 text-center">
                            {/* A share paid by paying a worker goes with that payout. */}
                            {payment.payout_id ? (
                              DASH
                            ) : (
                              <button
                                type="button"
                                className={BUTTON_QUIET}
                                onClick={() => onDeletePayment(payment)}
                                aria-label={`Delete the ${formatInr(payment.amount_paise)} payment of ${formatDate(payment.payment_date)}`}
                              >
                                <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                                Delete
                              </button>
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {onDeletePayment && share.payments.some((payment) => payment.payout_id) && (
                <p className="m-0 text-xs text-[var(--text-secondary)]">
                  A share paid by paying a worker directly goes with that payout: delete it from the
                  worker&apos;s payout statement.
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </Modal>
  )
}

/* ------------------------------------------- 2. attendance and salaries -- */

/** A helper's month as typed: their days absent, and what is done about leave beyond their allowance. */
interface WorkerDraft {
  days: string
  action: HouseHelpLeaveAction
}

/** A role's month as typed: its bonus or extra. */
interface RoleDraft {
  bonus: string
  bonusPaise: number
  bonusOk: boolean
}

const LEAVE_ACTIONS: Array<{ value: HouseHelpLeaveAction; label: string }> = [
  { value: 'deduct', label: 'Deduct pro-rata' },
  { value: 'waive', label: 'Waive/Forgive' },
]

function roleDraftOf(role: HouseHelpRoleMonth): RoleDraft {
  return {
    bonus: role.bonus_or_extra_paise ? paiseToInput(role.bonus_or_extra_paise) : '',
    bonusPaise: role.bonus_or_extra_paise,
    bonusOk: true,
  }
}

/** Days absent as typed: a whole number of days in the month, or nothing yet. */
function parseDays(raw: string, daysInMonth: number): number | null {
  const text = raw.trim()
  if (!/^\d{1,2}$/.test(text)) return null
  const days = Number(text)
  return days <= daysInMonth ? days : null
}

function allowance(days: number): string {
  return days === 0 ? 'No leave allowed' : `${days} day${days === 1 ? '' : 's'} allowed`
}

function dayCount(days: number): string {
  return `${days} day${days === 1 ? '' : 's'}`
}

/** A role's pay for the days of the month its helper was there (aem/engine/househelp.py). */
function prorated(monthly: number, daysActive: number, daysInMonth: number): number {
  return Math.floor((monthly * daysActive) / daysInMonth)
}

/**
 * A role's salary as the backend will work it out on saving
 * (aem/engine/househelp.py): its pay for the days its helper was there
 * (`earned`), less a day's pay -- its monthly salary over the month's days,
 * floored to the paisa -- for each of their days absent beyond their
 * allowance, unless waived; the bonus goes on top.
 */
function netSalary(
  monthly: number,
  earned: number,
  excess: number,
  action: HouseHelpLeaveAction,
  bonus: number,
  daysInMonth: number,
) {
  const deduction = action === 'waive' ? 0 : Math.floor(monthly / daysInMonth) * excess
  return { deduction, net: earned - deduction + bonus }
}

function AttendancePanel({
  month,
  data,
  workers,
  onOpenStaff,
  onSaved,
}: {
  month: string
  data: HouseHelpMonthOverview
  /** The helpers and their roles as they now stand: a month is saved at each role's salary, and each helper's allowance, now. */
  workers: HouseHelpWorker[]
  onOpenStaff: () => void
  /** After either panel saves: the month as it then stands, and what to say. */
  onSaved: (overview: HouseHelpMonthOverview, message: string) => void
}) {
  const editable = data.can_save
  const days = data.days_in_month
  const salaryNow = new Map(workers.flatMap((worker) => worker.roles).map((role) => [role.role_id, role.monthly_salary_paise]))
  const allowanceNow = new Map(workers.map((worker) => [worker.worker_id, worker.allowed_leaves_per_month]))
  const joinedOn = new Map(workers.map((worker) => [worker.worker_id, worker.joined_date]))
  // The days of the month a role's helper was there, and its pay for them at its salary now.
  const daysThere = (role: HouseHelpRoleMonth) => role.pro_rated_days ?? days
  const monthlyNow = (role: HouseHelpRoleMonth) => salaryNow.get(role.role_id) ?? role.base_salary_paise
  const baseNow = (role: HouseHelpRoleMonth) =>
    salaryNow.has(role.role_id) ? prorated(monthlyNow(role), daysThere(role), days) : role.base_salary_paise
  // Each panel reads as saved until it is edited, and is saved on its own.
  const [editingAttendance, setEditingAttendance] = useState(false)
  const [editingSalaries, setEditingSalaries] = useState(false)
  // What has been changed while editing: a helper's attendance, a role's bonus, a role added to the month.
  const [workerDrafts, setWorkerDrafts] = useState<Record<string, WorkerDraft>>({})
  const [roleDrafts, setRoleDrafts] = useState<Record<string, RoleDraft>>({})
  const [added, setAdded] = useState<string[]>([])

  /** A helper's attendance as saved -- the same on each of their roles -- or none, on a month not saved yet. */
  const savedAttendance = (workerId: string): WorkerDraft => {
    const role = [...data.roles, ...data.roles_not_on_month].find((entry) => entry.worker_id === workerId)
    return { days: String(role?.days_absent ?? 0), action: role?.excess_leave_action ?? 'deduct' }
  }
  const setWorkerDraft = (workerId: string, change: Partial<WorkerDraft>) =>
    setWorkerDrafts((before) => ({ ...before, [workerId]: { ...(before[workerId] ?? savedAttendance(workerId)), ...change } }))
  const setRoleDraft = (role: HouseHelpRoleMonth, change: Partial<RoleDraft>) =>
    setRoleDrafts((before) => ({ ...before, [role.role_id]: { ...(before[role.role_id] ?? roleDraftOf(role)), ...change } }))

  /**
   * The month worked out from the attendance and bonuses as saved, or as
   * drafted. As saved, it reads as it was billed; with anything drafted it is a
   * preview of saving, at each role's salary and helper's allowance now.
   */
  const work = (attendanceDrafted: boolean, bonusesDrafted: boolean) => {
    const preview = editable && (attendanceDrafted || bonusesDrafted)
    const rows: Array<HouseHelpMonthlyRecord | HouseHelpRoleMonth> = [
      ...data.roles,
      ...(bonusesDrafted ? data.roles_not_on_month.filter((role) => added.includes(role.role_id)) : []),
    ]
    // Each helper on the month with their roles, in the order the roles come.
    const helpers: Array<{ workerId: string; name: string; roles: typeof rows }> = []
    for (const role of rows) {
      const helper = helpers.find((entry) => entry.workerId === role.worker_id)
      if (helper) helper.roles.push(role)
      else helpers.push({ workerId: role.worker_id, name: role.worker_name, roles: [role] })
    }
    return helpers.map(({ workerId, name, roles }) => {
      const attendance = (attendanceDrafted && workerDrafts[workerId]) || savedAttendance(workerId)
      const allowed = preview ? (allowanceNow.get(workerId) ?? roles[0].allowed_leaves_per_month) : roles[0].allowed_leaves_per_month
      // A helper who joined during the month cannot have been away for more days than they were there.
      const active = daysThere(roles[0])
      const absent = parseDays(attendance.days, active)
      const excess = absent == null ? null : Math.max(0, absent - allowed)
      const roleFigures = roles.map((role) => {
        const bonus = (bonusesDrafted && roleDrafts[role.role_id]) || roleDraftOf(role)
        const monthly = monthlyNow(role)
        const base = preview ? baseNow(role) : role.base_salary_paise
        // The deduction as saved stands while nothing it was worked out from has changed.
        const asSaved =
          data.saved &&
          data.roles.includes(role as HouseHelpMonthlyRecord) &&
          attendance.days === String(role.days_absent) &&
          attendance.action === role.excess_leave_action &&
          base === role.base_salary_paise &&
          allowed === role.allowed_leaves_per_month
        // A deduction agreed by hand, kept with it.
        const agreed =
          asSaved &&
          role.deduction_paise !== netSalary(monthly, base, role.excess_leaves, role.excess_leave_action, 0, days).deduction
        const worked =
          excess == null || !bonus.bonusOk
            ? null
            : !preview
              ? { excess: role.excess_leaves, deduction: role.deduction_paise, net: role.final_salary_paise }
              : asSaved
                ? { excess: role.excess_leaves, deduction: role.deduction_paise, net: base - role.deduction_paise + bonus.bonusPaise }
                : { excess, ...netSalary(monthly, base, excess, attendance.action, bonus.bonusPaise, days) }
        return { role, bonus, base, worked, agreed }
      })
      return { workerId, name, attendance, allowed, active, absent, roles: roleFigures }
    })
  }
  const records = (helpers: ReturnType<typeof work>) =>
    helpers.flatMap((helper) =>
      helper.roles.map(({ role, bonus, agreed }) => ({
        role_id: role.role_id,
        days_absent: helper.absent,
        excess_leave_action: helper.attendance.action,
        bonus_or_extra_paise: bonus.bonusPaise,
        deduction_paise: agreed ? role.deduction_paise : null,
        extra_notes: role.extra_notes,
      })),
    )

  const figures = work(editingAttendance, editingSalaries)
  const roleFigures = figures.flatMap((helper) => helper.roles)
  const total = roleFigures.reduce((sum, row) => sum + (row.worked?.net ?? 0), 0)
  const deducted = roleFigures.reduce((sum, row) => sum + (row.worked?.deduction ?? 0), 0)
  const bonuses = roleFigures.reduce((sum, row) => sum + (row.bonus.bonusOk ? row.bonus.bonusPaise : 0), 0)
  // A role's salary or a helper's allowance changed since the month was saved: saving it again applies them.
  const stale =
    data.saved &&
    data.roles.some(
      (role) =>
        baseNow(role) !== role.base_salary_paise ||
        (allowanceNow.get(role.worker_id) ?? role.allowed_leaves_per_month) !== role.allowed_leaves_per_month,
    )
  const attendanceValid = figures.every((helper) => helper.absent != null)
  const attendanceChanged =
    stale ||
    figures.some((helper) => {
      const saved = savedAttendance(helper.workerId)
      return helper.attendance.days !== saved.days || helper.attendance.action !== saved.action
    })
  const salariesValid = roleFigures.every((row) => row.bonus.bonusOk)
  const salariesChanged =
    stale || added.length > 0 || roleFigures.some((row) => row.bonus.bonusPaise !== row.role.bonus_or_extra_paise)

  const saveAttendance = useMutation(
    () => api.post<HouseHelpMonthOverview>(`${BASE}/month/${month}/attendance`, { records: records(work(true, false)) }),
    {
      onSuccess: (updated) => {
        setEditingAttendance(false)
        onSaved(updated, 'Attendance saved')
      },
    },
  )
  const saveSalaries = useMutation(
    () => api.post<HouseHelpMonthOverview>(`${BASE}/month/${month}/attendance`, { records: records(work(false, true)) }),
    {
      onSuccess: (updated) => {
        setEditingSalaries(false)
        setAdded([])
        onSaved(updated, 'Salaries & bonuses saved')
      },
    },
  )
  const saving = saveAttendance.pending || saveSalaries.pending
  const editAttendance = () => {
    setWorkerDrafts({})
    saveAttendance.reset()
    setEditingAttendance(true)
  }
  const editSalaries = () => {
    setRoleDrafts({})
    setAdded([])
    saveSalaries.reset()
    setEditingSalaries(true)
  }
  const unsaved = `Not saved yet: saving bills ${formatMonth(month)}.`

  if (data.roles.length === 0 && data.roles_not_on_month.length === 0) {
    return (
      <Panel
        title="Attendance & Salaries"
        icon={<CalendarCheck className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      >
        <EmptyBlock
          title="No roles to pay this month."
          body={
            data.unassigned_roles.length > 0
              ? 'No active helpers are currently assigned. Assign a helper to each role under Staff & Roles to track attendance and salaries.'
              : 'Add a helper and the roles they do under Staff & Roles.'
          }
          action={
            <button type="button" className={BUTTON_SECONDARY} onClick={onOpenStaff}>
              Go to Staff & Roles
            </button>
          }
        />
      </Panel>
    )
  }

  return (
    <div className="space-y-6">
      <Panel
        title="Helper Attendance"
        icon={<CalendarCheck className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint={`Days off each helper took in ${formatMonth(month)} (${data.days_in_month} days). Days beyond allowance are deducted from role salaries unless waived.`}
        menuActions={
          editable && !editingAttendance && data.roles.length > 0
            ? [{ label: 'Edit Attendance', icon: Pencil, onClick: editAttendance }]
            : undefined
        }
      >
        {data.bill_state === 'frozen' ? (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            The {formatMonth(month)} statement has been finalized: its salaries are final.
          </p>
        ) : data.bill_state === 'upcoming' ? (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            {formatMonth(month)} has not begun: its attendance can be saved once it does.
          </p>
        ) : data.bill_state === 'recorded_elsewhere' ? (
          <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
            {formatMonth(month)}&apos;s house help is already recorded as an expense entered by hand.
          </p>
        ) : (
          data.blocked_reason && (
            <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
              {data.blocked_reason}
            </p>
          )
        )}
        <div className="overflow-x-auto min-w-0">
          <table className="w-full table-fixed text-left text-xs min-w-[600px] border-collapse" aria-busy={saving}>
            <caption className="sr-only">Helper attendance for {formatMonth(month)}</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="w-[32%] pb-3 pr-3 font-bold text-left">Helper</th>
                <th scope="col" className="w-[22%] pb-3 pr-3 font-bold text-center">Allowed Leaves</th>
                <th scope="col" className="w-[22%] pb-3 pr-3 font-bold text-center">Days Absent</th>
                <th scope="col" className="w-[24%] pb-3 font-bold text-center">Beyond Allowance</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {figures.map((helper) => (
                <tr key={helper.workerId} className="align-middle">
                  <th scope="row" className="py-3 pr-3 text-left font-semibold text-[var(--text-primary)] truncate">
                    {helper.name}
                  </th>
                  <td className="py-3 pr-3 text-center text-xs text-[var(--text-secondary)] font-medium tabular">
                    {allowance(helper.allowed)}
                  </td>
                  <td className="py-3 pr-3 text-center">
                    {editingAttendance ? (
                      <div className="mx-auto max-w-[5rem]">
                        <TextField
                          label={<span className="sr-only">{helper.name} days absent</span>}
                          type="number"
                          min={0}
                          step={1}
                          inputMode="numeric"
                          value={helper.attendance.days}
                          onChange={(value) => setWorkerDraft(helper.workerId, { days: value })}
                          error={helper.absent == null ? `0 to ${helper.active}` : null}
                        />
                      </div>
                    ) : (
                      <span className="tabular font-semibold">{helper.attendance.days}</span>
                    )}
                  </td>
                  <td className="py-3 text-center">
                    {editingAttendance ? (
                      <div className="mx-auto min-w-[160px] max-w-[12rem]">
                        <SelectField
                          label={<span className="sr-only">{helper.name} leave beyond the allowance</span>}
                          value={helper.attendance.action}
                          options={LEAVE_ACTIONS}
                          onChange={(value) => setWorkerDraft(helper.workerId, { action: value })}
                        />
                      </div>
                    ) : (
                      LEAVE_ACTIONS.find((option) => option.value === helper.attendance.action)?.label
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {editingAttendance && (
          <div className="pt-4 border-t border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-3">
            {/* There while editing, so a screen reader hears it fill. */}
            <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
              {!data.saved
                ? unsaved
                : attendanceChanged
                  ? 'Unsaved changes: the salaries below are a preview until you save.'
                  : ''}
            </p>
            <div className="flex flex-wrap items-center justify-end gap-3 ml-auto">
              <ErrorNotice error={saveAttendance.error} />
              <button
                type="button"
                className={BUTTON_SECONDARY}
                onClick={() => setEditingAttendance(false)}
                disabled={saveAttendance.pending}
              >
                Cancel
              </button>
              <button
                type="button"
                className={BUTTON_PRIMARY}
                disabled={!attendanceValid || (data.saved && !attendanceChanged) || saving}
                onClick={() => void saveAttendance.run(undefined)}
              >
                <Save className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                {saveAttendance.pending ? 'Saving…' : 'Save Attendance'}
              </button>
            </div>
          </div>
        )}
      </Panel>

      <Panel
        title="Monthly Salaries & Bonuses"
        icon={<Wallet className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
        hint={`Base monthly salary for each role, attendance deductions, and optional bonuses. Saving bills ${formatMonth(month)}'s house help.`}
        menuActions={
          editable && !editingSalaries
            ? [{ label: 'Edit Salaries & Bonuses', icon: Pencil, onClick: editSalaries }]
            : undefined
        }
      >
        <div className="overflow-x-auto min-w-0">
          <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse" aria-busy={saving}>
            <caption className="sr-only">Role salaries for {formatMonth(month)}</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="w-[32%] pb-3 pr-3 font-bold text-left">Role & Helper</th>
                <th scope="col" className="w-[17%] pb-3 pr-3 font-bold text-center">Base Salary</th>
                <th scope="col" className="w-[17%] pb-3 pr-3 font-bold text-center">Deduction</th>
                <th scope="col" className="w-[17%] pb-3 pr-3 font-bold text-center">Bonus / Extra</th>
                <th scope="col" className="w-[17%] pb-3 font-bold text-center">Net Salary</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {roleFigures.map(({ role, bonus, base, worked }) => (
                <tr key={role.role_id} className="align-middle">
                  <th scope="row" className="py-3 pr-3 text-left font-normal">
                    <span className="block font-semibold text-[var(--text-primary)] truncate">{role.role_name}</span>
                    <span className="block text-[11px] text-[var(--text-tertiary)] truncate">
                      {role.worker_name}
                      {added.includes(role.role_id) && ' · new this month'}
                    </span>
                    {role.pro_rated_days != null && role.pro_rated_days < days && (
                      <span className="block text-[11px] text-[var(--text-secondary)] truncate">
                        {joinedOn.has(role.worker_id) && `Joined ${formatDate(joinedOn.get(role.worker_id))} · `}
                        {role.pro_rated_days}/{days} days pro-rated
                      </span>
                    )}
                  </th>
                  <td className="py-3 pr-3 text-center tabular">
                    <Rupees paise={base} />
                  </td>
                  <td className="py-3 pr-3 text-center tabular">
                    {worked && worked.deduction > 0 ? (
                      <>
                        −{formatInr(worked.deduction)}
                        <span className="block text-[10px] text-[var(--text-tertiary)]">for {dayCount(worked.excess)}</span>
                      </>
                    ) : (
                      DASH
                    )}
                  </td>
                  <td className="py-3 pr-3 text-center">
                    {editingSalaries ? (
                      <div className="mx-auto max-w-[7.5rem]">
                        <MoneyInput
                          label={
                            <span className="sr-only">
                              {role.role_name} ({role.worker_name}) bonus or extra
                            </span>
                          }
                          value={bonus.bonus}
                          hint=""
                          onChange={(raw, parsed) =>
                            setRoleDraft(role, {
                              bonus: raw,
                              bonusPaise: raw.trim() ? parsed.paise : 0,
                              bonusOk: !raw.trim() || parsed.ok,
                            })
                          }
                        />
                      </div>
                    ) : role.bonus_or_extra_paise > 0 ? (
                      <Rupees paise={role.bonus_or_extra_paise} />
                    ) : (
                      DASH
                    )}
                  </td>
                  <td className="py-3 text-center tabular font-bold text-[var(--text-primary)]">
                    {worked ? <Rupees paise={worked.net} /> : DASH}
                  </td>
                </tr>
              ))}
            </tbody>
            {roleFigures.length > 0 && (
              <tfoot>
                <tr className="border-t border-[var(--border-subtle)] font-bold text-[var(--text-primary)]">
                  <th scope="row" className="pt-3 pr-3 text-left">Total</th>
                  <td className="pt-3 pr-3 text-center tabular">
                    <Rupees paise={roleFigures.reduce((sum, row) => sum + row.base, 0)} />
                  </td>
                  <td className="pt-3 pr-3 text-center tabular">
                    {deducted > 0 ? `−${formatInr(deducted)}` : DASH}
                  </td>
                  <td className="pt-3 pr-3 text-center tabular">{bonuses > 0 ? <Rupees paise={bonuses} /> : DASH}</td>
                  <td className="pt-3 text-center tabular">
                    <Rupees paise={total} />
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>

        {editingSalaries && data.roles_not_on_month.some((role) => !added.includes(role.role_id)) && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
            <span>Not on {formatMonth(month)} yet:</span>
            {data.roles_not_on_month
              .filter((role) => !added.includes(role.role_id))
              .map((role) => (
                <button
                  key={role.role_id}
                  type="button"
                  className={BUTTON_QUIET}
                  onClick={() => setAdded((before) => [...before, role.role_id])}
                >
                  <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  Add {role.role_name} ({role.worker_name})
                </button>
              ))}
          </div>
        )}

        {/* Only when nobody is left to do them: with active helpers, a role waiting for one is the Staff tab's business. */}
        {data.active_worker_count === 0 && data.unassigned_roles.length > 0 && (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            No active helpers are currently assigned. Assign a helper to each role under{' '}
            <button type="button" className="font-semibold text-[var(--accent)] hover:underline" onClick={onOpenStaff}>
              Staff & Roles
            </button>{' '}
            to track attendance and salaries.
          </p>
        )}

        {editingSalaries && (
          <div className="pt-4 border-t border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-3">
            {/* There while editing, so a screen reader hears it fill. */}
            <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
              {!data.saved
                ? unsaved
                : salariesChanged
                  ? 'Unsaved changes: the net salaries above are a preview until you save.'
                  : ''}
            </p>
            <div className="flex flex-wrap items-center justify-end gap-3 ml-auto">
              <ErrorNotice error={saveSalaries.error} />
              <button
                type="button"
                className={BUTTON_SECONDARY}
                onClick={() => {
                  setEditingSalaries(false)
                  setAdded([])
                }}
                disabled={saveSalaries.pending}
              >
                Cancel
              </button>
              <button
                type="button"
                className={BUTTON_PRIMARY}
                disabled={!salariesValid || (data.saved && !salariesChanged) || saving}
                onClick={() => void saveSalaries.run(undefined)}
              >
                <Save className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                {saveSalaries.pending ? 'Saving…' : 'Save Salaries & Bonuses'}
              </button>
            </div>
          </div>
        )}
      </Panel>
    </div>
  )
}

/* ---------------------------------------------------- 3. staff and roles -- */

function WorkersPanel({
  workers,
  onAdd,
  onEdit,
}: {
  workers: HouseHelpWorker[]
  onAdd: () => void
  onEdit: (worker: HouseHelpWorker) => void
}) {
  return (
    <Panel
      title="Helpers"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="The maids, cooks and cleaners the household employs, and the days off each may take a month. Select a row to edit them."
      menuActions={[{ label: 'Add Helper', icon: UserPlus, onClick: onAdd }]}
    >
      {workers.length === 0 ? (
        <EmptyBlock title="No helpers yet." body="Add the household's maid, cook or cleaner to begin." />
      ) : (
        <div className="overflow-x-auto min-w-0">
          <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
            <caption className="sr-only">The household's helpers</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="w-[32%] pb-3 pr-3 font-bold text-left">Helper</th>
                <th scope="col" className="w-[24%] pb-3 pr-3 font-bold text-center">Joined Date</th>
                <th scope="col" className="w-[24%] pb-3 pr-3 font-bold text-center">Allowed Leaves</th>
                <th scope="col" className="w-[20%] pb-3 pr-3 font-bold text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {workers.map((worker) => (
                <tr
                  key={worker.worker_id}
                  className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                  tabIndex={0}
                  title={`Edit ${worker.name}`}
                  onClick={() => onEdit(worker)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onEdit(worker)
                    }
                  }}
                >
                  <th scope="row" className="py-3 pr-3 text-left font-normal">
                    <span className="flex items-center gap-2.5 min-w-0">
                      <Avatar name={worker.name} />
                      <span className="font-semibold text-[var(--text-primary)] truncate">{worker.name}</span>
                    </span>
                  </th>
                  <td className="py-3 pr-3 text-center tabular text-xs text-[var(--text-secondary)] whitespace-nowrap">
                    {formatDate(worker.joined_date)}
                  </td>
                  <td className="py-3 pr-3 text-center whitespace-nowrap">
                    <Pill>{allowance(worker.allowed_leaves_per_month)}</Pill>
                  </td>
                  <td className="py-3 pr-3 text-center whitespace-nowrap">
                    <Pill tone={worker.is_active ? 'success' : undefined}>{worker.is_active ? 'Active' : 'Inactive'}</Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

/** "Cooking (Sunita)", or just "Cooking" while it has no helper. */
function roleLabel(role: HouseHelpRole): string {
  return role.worker_name ? `${role.role_name} (${role.worker_name})` : role.role_name
}

function RolesPanel({
  workers,
  unassigned,
  onAdd,
  onEdit,
}: {
  workers: HouseHelpWorker[]
  /** Roles not yet given a helper. */
  unassigned: HouseHelpRole[]
  onAdd: () => void
  onEdit: (role: HouseHelpRole) => void
}) {
  const roles = [...workers.flatMap((worker) => worker.roles), ...unassigned]
  return (
    <Panel
      title="Roles"
      icon={<HandPlatter className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each helper does and their monthly base salary. Select a row to edit it."
      menuActions={[{ label: 'Add Role', icon: Plus, onClick: onAdd }]}
    >
      {roles.length === 0 ? (
        <EmptyBlock
          title="No roles yet."
          body="Add a role, such as cleaning or cooking. Give it a helper now, or once you have one."
        />
      ) : (
        <div className="overflow-x-auto min-w-0">
          <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
            <caption className="sr-only">The helpers' roles</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="w-[32%] pb-3 pr-3 font-bold text-left">Role</th>
                <th scope="col" className="w-[24%] pb-3 pr-3 font-bold text-center">Worker</th>
                <th scope="col" className="w-[24%] pb-3 pr-3 font-bold text-center">Split</th>
                <th scope="col" className="w-[20%] pb-3 pr-3 font-bold text-center">Base Salary</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {roles.map((role) => (
                <tr
                  key={role.role_id}
                  className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                  tabIndex={0}
                  title={`Edit ${roleLabel(role)}`}
                  onClick={() => onEdit(role)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onEdit(role)
                    }
                  }}
                >
                  <th scope="row" className="py-3 pr-3 text-left font-semibold text-[var(--text-primary)] truncate">
                    {role.role_name}
                  </th>
                  <td className="py-3 pr-3 text-center truncate">
                    {role.worker_name ?? <span className="text-[var(--warning)]">No helper yet</span>}
                  </td>
                  <td className="py-3 pr-3 text-center text-xs text-[var(--text-secondary)] truncate">
                    {role.split_method === 'equal'
                      ? 'All flatmates'
                      : role.participants.length > 0
                        ? `Opt-in (${role.participants.length})`
                        : 'Opt-in (nobody)'}
                  </td>
                  <td className="py-3 pr-3 text-center tabular font-bold text-[var(--text-primary)]">
                    <Rupees paise={role.monthly_salary_paise} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function WorkerDialog({
  worker,
  onClose,
  onSaved,
}: {
  /** Null to add a helper. */
  worker: HouseHelpWorker | null
  onClose: () => void
  onSaved: (worker: HouseHelpWorker) => void
}) {
  const [name, setName] = useState(worker?.name ?? '')
  const [phone, setPhone] = useState(worker?.phone ?? '')
  const [upi, setUpi] = useState(worker?.upi_id ?? '')
  const [notes, setNotes] = useState(worker?.notes ?? '')
  const [active, setActive] = useState(worker?.is_active ?? true)
  const [leaves, setLeaves] = useState(String(worker?.allowed_leaves_per_month ?? 2))
  const [joined, setJoined] = useState(worker?.joined_date ?? '')
  const leavesOk = /^\d{1,2}$/.test(leaves.trim()) && Number(leaves) <= 31
  const valid = name.trim().length > 0 && leavesOk && /^\d{4}-\d{2}-\d{2}$/.test(joined)
  const details = {
    name: name.trim(),
    joined_date: joined,
    phone: phone.trim() || null,
    upi_id: upi.trim() || null,
    notes: notes.trim() || null,
    allowed_leaves_per_month: Number(leaves),
  }
  const save = useMutation(
    () =>
      worker
        ? api.patch<HouseHelpWorker>(`${BASE}/workers/${encodeURIComponent(worker.worker_id)}`, {
            ...details,
            is_active: active,
          })
        : api.post<HouseHelpWorker>(`${BASE}/workers`, details),
    { onSuccess: onSaved },
  )
  const formId = 'househelp-worker'
  return (
    <Modal
      title={worker ? 'Edit Helper' : 'Add Helper'}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form={formId} className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
            {save.pending ? 'Saving…' : worker ? 'Save' : 'Add Helper'}
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
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField label="Name" required value={name} onChange={setName} maxLength={100} />
          <DateField
            label="Date of joining"
            required
            value={joined}
            onChange={setJoined}
            hint="Paid from this day: the month they join in, for its days from it."
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField
            label="Phone"
            type="tel"
            prefix="+91"
            placeholder="98765 43210"
            value={phone.replace(/^(\+91\s*|0)/, '')}
            onChange={(val) => setPhone(val.replace(/^(\+91\s*|0)/, ''))}
            maxLength={15}
          />
          <TextField label="UPI ID" value={upi} onChange={setUpi} maxLength={320} placeholder="name@bank" />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField
            label="Allowed leaves per month"
            type="number"
            min={0}
            max={31}
            step={1}
            inputMode="numeric"
            value={leaves}
            onChange={setLeaves}
            error={leavesOk ? null : 'Enter days from 0 to 31.'}
            hint="Paid days off allowed per month."
          />
          {worker && (
            <SwitchField
              label="Helper status"
              onLabel="Active"
              offLabel="Inactive"
              checked={active}
              onChange={setActive}
              hint="Inactive helpers do not join new monthly calculations."
            />
          )}
        </div>
        <TextAreaField
          label="Notes"
          value={notes}
          onChange={setNotes}
          rows={2}
          placeholder="Optional notes or details..."
        />
      </form>
    </Modal>
  )
}

function RoleDialog({
  role,
  workers,
  onClose,
  onDelete,
  onSaved,
}: {
  /** Null to add a role. */
  role: HouseHelpRole | null
  workers: HouseHelpWorker[]
  onClose: () => void
  onDelete?: (role: HouseHelpRole) => void
  onSaved: (role: HouseHelpRole) => void
}) {
  const people = useResource<{ items: Resident[] }>('/api/residents')
  const flatmates = (people.data?.items ?? []).filter((resident) => resident.is_active)
  const choices = workers.filter((worker) => worker.is_active || worker.worker_id === role?.worker_id)
  const [name, setName] = useState(role?.role_name ?? '')
  const [workerId, setWorkerId] = useState(role?.worker_id ?? (choices.length === 1 ? choices[0].worker_id : ''))
  const [salary, setSalary] = useState(role ? paiseToInput(role.monthly_salary_paise) : '')
  const [salaryPaise, setSalaryPaise] = useState(role?.monthly_salary_paise ?? 0)
  const [salaryOk, setSalaryOk] = useState(Boolean(role))
  const [split, setSplit] = useState<HouseHelpSplitMethod>(role?.split_method ?? 'equal')
  const [opted, setOpted] = useState<string[]>(role?.participant_ids ?? [])
  const valid =
    name.trim().length > 0 &&
    salaryOk &&
    salaryPaise > 0 &&
    (split === 'equal' || opted.length > 0)

  const save = useMutation(
    async () => {
      const fields = {
        role_name: name.trim(),
        monthly_salary_paise: salaryPaise,
        split_method: split,
      }
      if (!role) {
        return api.post<HouseHelpRole>(`${BASE}/roles`, {
          ...(workerId && { worker_id: workerId }),
          ...fields,
          ...(split === 'opt_in' && { participants: opted }),
        })
      }
      const path = `${BASE}/roles/${encodeURIComponent(role.role_id)}`
      let saved = await api.patch<HouseHelpRole>(path, {
        ...fields,
        ...(workerId !== (role.worker_id ?? '') && { worker_id: workerId || null }),
      })
      const before = [...role.participant_ids].sort().join()
      if (split === 'opt_in' && [...opted].sort().join() !== before) {
        saved = await api.put<HouseHelpRole>(`${path}/participants`, { participants: opted })
      }
      return saved
    },
    { onSuccess: onSaved },
  )
  const formId = 'househelp-role'
  return (
    <Modal
      title={role ? 'Edit Role' : 'Add Role'}
      description={role ? [role.role_name, role.worker_name ?? 'No helper yet'].join(' · ') : undefined}
      onClose={onClose}
      footer={
        <>
          {role && onDelete && (
            <button
              type="button"
              className={`${BUTTON_QUIET} mr-auto text-[var(--danger)]`}
              onClick={() => onDelete(role)}
              disabled={save.pending}
            >
              <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Delete Role
            </button>
          )}
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form={formId} className={BUTTON_PRIMARY} disabled={!valid || save.pending}>
            {save.pending ? 'Saving…' : role ? 'Save' : 'Add Role'}
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
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField
            label="Role name"
            required
            value={name}
            onChange={setName}
            maxLength={100}
            placeholder="e.g. Cooking"
          />
          <SelectField
            label="Worker"
            value={workerId}
            placeholder="No helper yet"
            options={choices.map((worker) => ({ value: worker.worker_id, label: worker.name }))}
            onChange={setWorkerId}
            hint={workerId ? undefined : 'On no month’s salaries until it has a helper.'}
          />
          <MoneyInput
            label="Base salary"
            required
            value={salary}
            onChange={(raw, parsed) => {
              setSalary(raw)
              setSalaryPaise(parsed.paise)
              setSalaryOk(parsed.ok)
            }}
            hint="For a full month, before leave and bonuses. Leave is the helper's."
          />
        </div>
        <fieldset role="radiogroup" className="m-0 border-0 p-0 space-y-2">
          <legend className="p-0 pb-1 text-sm font-bold text-[var(--text-primary)]">Split mode</legend>
          {(
            [
              { value: 'equal', title: 'All flatmates equally', body: 'Everyone living in the flat shares it.' },
              { value: 'opt_in', title: 'Opt-in flatmates', body: 'Only the flatmates chosen below share it.' },
            ] as const
          ).map((option) => (
            <label
              key={option.value}
              className={
                split === option.value
                  ? 'flex items-start gap-3 rounded-xl border border-[var(--accent)] bg-[var(--accent-glow)] p-3 cursor-pointer'
                  : 'flex items-start gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 cursor-pointer'
              }
            >
              <input
                type="radio"
                name={`${formId}-split`}
                className="mt-1 shrink-0"
                value={option.value}
                checked={split === option.value}
                onChange={() => setSplit(option.value)}
              />
              <span className="min-w-0">
                <span className="block text-sm font-bold text-[var(--text-primary)]">{option.title}</span>
                <span className="block text-xs text-[var(--text-secondary)]">{option.body}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {split === 'opt_in' && (
          <fieldset className="m-0 border-0 p-0 space-y-1">
            <legend className="p-0 pb-1 text-sm font-bold text-[var(--text-primary)]">Who shares it</legend>
            {people.data == null ? (
              <p className="m-0 text-xs text-[var(--text-secondary)]">Loading flatmates…</p>
            ) : (
              flatmates.map((person) => (
                <CheckboxField
                  key={person.resident_id}
                  label={person.display_name}
                  checked={opted.includes(person.resident_id)}
                  onChange={(checked) =>
                    setOpted((before) =>
                      checked ? [...before, person.resident_id] : before.filter((id) => id !== person.resident_id),
                    )
                  }
                />
              ))
            )}
            {opted.length === 0 && (
              <p className="m-0 text-xs text-[var(--warning)]">Choose at least one flatmate.</p>
            )}
          </fieldset>
        )}
      </form>
    </Modal>
  )
}

function DeleteRoleDialog({ role, onClose, onDeleted }: { role: HouseHelpRole; onClose: () => void; onDeleted: () => void }) {
  const remove = useMutation(() => api.delete<unknown>(`${BASE}/roles/${encodeURIComponent(role.role_id)}`), {
    onSuccess: onDeleted,
  })
  return (
    <ConfirmDialog
      title="Delete this role?"
      body={
        <>
          <p className="m-0">
            {roleLabel(role)}, {formatInr(role.monthly_salary_paise)} a month.
          </p>
          <p className="m-0 mt-2 text-xs text-[var(--text-secondary)]">
            It comes off every open month it was saved in, and those months are billed again without it.
            A month whose statement has been finalized keeps it: make the helper inactive instead.
          </p>
        </>
      }
      confirmLabel="Delete Role"
      tone="danger"
      pending={remove.pending}
      error={remove.error}
      onConfirm={() => void remove.run(undefined)}
      onCancel={onClose}
    />
  )
}
