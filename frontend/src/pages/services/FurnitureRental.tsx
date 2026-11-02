/**
 * Furniture rental (Furlenco, Rentomojo): the subscription, the items rented,
 * and what each month bills.
 *
 * Every figure comes from the backend. Shared items are split equally between
 * the residents taking part and a resident's own items are charged to them;
 * nothing is prorated, so an item in the flat at any point in a month counts in
 * full. Each month's bill is one ordinary expense, which follows the items while
 * the month is open and is kept as it stands once the month closes. An item's
 * rent is before GST: the bill adds GST at its rate on the items together, or is
 * the invoice's own total where that rounds its GST another way.
 *
 * The month is paid in the same two steps as rent:
 * flatmates pay their shares into the House Kitty, and the provider is paid the
 * bill, in one payout or several, from the kitty or by a resident from their own
 * account. A resident who paid the provider is paid back by the kitty out of
 * what the others paid in.
 *
 * Three tabs, as Housing has them: the month's settlement, the items, and the
 * subscription with its deposit. The open tab is kept in the address
 * (`?tab=items`), so a link or a reload lands on it.
 */
import { useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Archive,
  Check,
  Clock,
  Pencil,
  Percent,
  Plus,
  Receipt,
  ShieldCheck,
  Sofa,
  Store,
  Trash2,
  Undo2,
  Users,
} from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection, Callout, ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, MoneyInput, SelectField, TextField } from '../../components/Form'
import {
  ResidentCollectionTable,
  type CollectionStatus,
  type ResidentCollectionItem,
} from '../../components/ResidentCollectionTable'
import { DeleteTransactionDialog, type PendingDeletion } from '../../components/DeleteTransactionDialog'
import { BillGstModal, rateText } from '../../components/BillGstModal'
import { ServiceDisbursementDialog } from '../../components/ServiceDisbursementDialog'
import { PAYMENT_MODES, ServicePaymentDialog } from '../../components/ServicePaymentDialog'
import {
  KittyReimbursementPanel,
  KittyReimbursementStatementModal,
  ReimburseDialog,
} from '../../components/KittyReimbursement'
import {
  ProviderPayoutStatementModal,
  ProviderPayoutTable,
  type ProviderPayoutItem,
} from '../../components/ProviderPayoutTable'
import { useToast } from '../../components/Toast'
import {
  Avatar,
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  CardActions,
  DetailFact,
  EmptyBlock,
  MiniStat,
  Panel,
  Pill,
  Rupees,
  type Tone,
} from '../../components/ui'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { formatDate, formatInr, formatMonth, paiseToInput, todayIso } from '../../lib/format'
import type {
  FurnitureConfig,
  FurnitureDepositStatus,
  FurnitureItem,
  FurnitureItemStatus,
  FurnitureMonthBill,
  FurnitureProviderPayout,
  FurnitureReimbursement,
  FurnitureRentalOverview,
  FurnitureShare,
  FurnitureServiceEntry,
  FurnitureShareStatus,
  HouseholdService,
  Resident,
} from '../../api/types'

const BASE = '/api/services/furniture'

const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold'


const ITEM_STATUS: Record<FurnitureItemStatus, { label: string; tone: Tone }> = {
  active: { label: 'In the flat', tone: 'success' },
  pending_delivery: { label: 'Awaiting delivery', tone: 'warning' },
  returned: { label: 'Returned', tone: 'neutral' },
}

const DEPOSIT_STATUS: Record<FurnitureDepositStatus, string> = {
  held: 'Held by the provider',
  partially_refunded: 'Partly refunded',
  refunded: 'Refunded',
}

const PROVIDERS = ['Furlenco', 'Rentomojo']

/** The page's tabs, as Housing has them: the open one is kept in the address (`?tab=items`). */
export const FURNITURE_TABS = [
  { value: 'settlement', label: 'Monthly Settlement', icon: Receipt },
  { value: 'items', label: 'Rented Items', icon: Sofa },
  { value: 'subscription', label: 'Subscription & Deposit', icon: ShieldCheck },
] as const

export type FurnitureTab = (typeof FURNITURE_TABS)[number]['value']

// A share covered by paying the provider directly reads Paid, as rent's does.
const SHARE_STATUS: Record<FurnitureShareStatus, { label: string; tone: Tone }> = {
  paid: { label: 'Paid', tone: 'success' },
  partial: { label: 'Part paid', tone: 'warning' },
  unpaid: { label: 'Unpaid', tone: 'warning' },
  funded_payout: { label: 'Paid', tone: 'success' },
}

/**
 * Who may have paid a provider for the month: everyone on its bill, and the
 * residents left out of it.
 */
function payersOf(bill: FurnitureMonthBill): Array<{ resident_id: string; display_name: string }> {
  return [...bill.shares, ...bill.excluded_residents]
    .map(({ resident_id, display_name }) => ({ resident_id, display_name }))
    .sort((a, b) => a.display_name.localeCompare(b.display_name))
}

/**
 * The provider bills on a fixed day and flatmates pay when they can, so a first
 * payout offers what the kitty has collected so far; after that, the rest. With
 * several providers, what was collected is not any one's.
 */
function collectedForPayout(bill: FurnitureMonthBill): number | undefined {
  const total = bill.expense?.amount_paise ?? 0
  const single = (bill.provider_payouts?.length ?? 1) <= 1
  return single && bill.disbursements.length === 0 && bill.collected_paise > 0 && bill.collected_paise < total
    ? bill.collected_paise
    : undefined
}

/** A quantity as typed: a whole number, 1 or more; null otherwise. */
function parseQuantity(raw: string): number | null {
  const text = raw.trim()
  return /^\d{1,4}$/.test(text) && Number(text) >= 1 ? Number(text) : null
}

/** Nothing left to pay on the share: paid into the kitty, or covered by paying the provider. */
function isSettled(share: FurnitureShare): boolean {
  return share.status === 'paid' || share.status === 'funded_payout' || share.outstanding_paise <= 0
}

/** Every live provider: from the backend, or, for an older response, the one there was. */
/** Everyone the kitty owes for the month, one entry each. */
function reimbursementsOf(bill: FurnitureMonthBill): FurnitureReimbursement[] {
  return bill.reimbursements ?? (bill.reimbursement ? [bill.reimbursement] : [])
}

function servicesOf(data: FurnitureRentalOverview): FurnitureServiceEntry[] {
  if (data.services) return data.services
  return data.service && data.config
    ? [
        {
          service: data.service,
          config: data.config,
          monthly_total_paise: data.monthly_total_paise,
          active_item_count: data.active_item_count,
          next_billing_date: data.next_billing_date,
          deposit_held_paise: data.deposit_held_paise,
        },
      ]
    : []
}

/** Each provider's part of the month's bill, or, for an older response, the one provider's. */
function payoutsOf(bill: FurnitureMonthBill, service: HouseholdService, config: FurnitureConfig): FurnitureProviderPayout[] {
  return (
    bill.provider_payouts ?? [
      {
        service_id: service.service_id,
        display_name: service.display_name,
        provider_name: config.provider_name,
        customer_id: config.customer_id,
        subscription_reference: config.subscription_reference,
        billing_day_of_month: service.billing_day_of_month,
        item_count: bill.billable_items.length,
        total_paise: bill.expense?.amount_paise ?? bill.total_paise,
        disbursed_paise: bill.disbursed_paise,
        remaining_disbursement_paise: bill.remaining_disbursement_paise,
        payout_status: bill.payout_status,
        disbursements: bill.disbursements,
      },
    ]
  )
}

type Dialog =
  // `entry` is the provider to edit: none to set one up, or to add another.
  | { kind: 'setup'; entry: FurnitureServiceEntry | null; adding?: boolean }
  | { kind: 'retire'; entry: FurnitureServiceEntry }
  | { kind: 'subscription_detail'; entry: FurnitureServiceEntry }
  | { kind: 'add' }
  | { kind: 'edit'; item: FurnitureItem }
  | { kind: 'item_detail'; item: FurnitureItem }
  | { kind: 'return'; item: FurnitureItem }
  | { kind: 'remove'; item: FurnitureItem }
  | { kind: 'provider_payout'; bill: FurnitureMonthBill; payout: FurnitureProviderPayout }
  | { kind: 'payout_statement'; payout: FurnitureProviderPayout }
  | { kind: 'reimburse'; reimbursement: FurnitureReimbursement }
  | { kind: 'reimbursement_statement'; reimbursement: FurnitureReimbursement }
  | { kind: 'share_detail'; share: FurnitureShare; bill: FurnitureMonthBill }
  | { kind: 'pay_share'; share: FurnitureShare }
  | { kind: 'bill_gst'; bill: FurnitureMonthBill }

export default function FurnitureRentalPage() {
  const { month } = useApp()
  return <FurnitureRental key={month} month={month} />
}

export function FurnitureRental({ month }: { month: string }) {
  const overview = useResource<FurnitureRentalOverview>(BASE, { month })
  const [dialog, setDialog] = useState<Dialog | null>(null)
  // A payment or payout recorded by mistake, about to be deleted. It opens over
  // the statement it was chosen from, which then shows the month as it stands.
  const [deleting, setDeleting] = useState<PendingDeletion | null>(null)
  // The month as a payout to a provider left it, for what its toast says is still owed.
  const payoutSaved = useRef<FurnitureMonthBill | null>(null)
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: FurnitureTab = FURNITURE_TABS.some((entry) => entry.value === requested)
    ? (requested as FurnitureTab)
    : 'settlement'
  const toast = useToast()
  const done = (message: string, detail?: string) => {
    setDialog(null)
    toast.success(message, detail)
    overview.reload()
  }

  return (
    <AsyncSection resource={overview} loadingLabel="Loading furniture rental…">
      {(data) => (
        <Page
          title="Furniture Rental"
          subtitle="Rented furniture and appliances: what the flat pays the provider each month, and who pays which part."
          breadcrumbs={[{ label: 'Services & Utilities', to: '/services' }, { label: 'Furniture Rental' }]}
        >
          {!data.service || !data.config ? (
            <Panel
              title="Set up furniture rental"
              icon={<Sofa className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
            >
              <EmptyBlock
                title="No furniture rental yet"
                body="Add the Furlenco or Rentomojo subscription, then the items it covers. Each month's bill is split from them: shared items equally, a person's own items to them."
                action={
                  <button type="button" className={BUTTON_PRIMARY} onClick={() => setDialog({ kind: 'setup', entry: null })}>
                    <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                    Set up furniture rental
                  </button>
                }
              />
            </Panel>
          ) : (
            <>
              <div className="tabs" role="tablist" aria-label="Furniture Rental">
                {FURNITURE_TABS.map(({ value, label, icon: Icon }) => (
                  <button
                    key={value}
                    id={`furniture-tab-${value}`}
                    type="button"
                    role="tab"
                    className="tab"
                    aria-selected={tab === value}
                    aria-controls={`furniture-panel-${value}`}
                    onClick={() => setParams(value === 'settlement' ? {} : { tab: value }, { replace: true })}
                  >
                    <Icon className="w-3.5 h-3.5 shrink-0 inline-block mr-1.5 align-[-2px]" aria-hidden="true" />
                    {label}
                  </button>
                ))}
              </div>
              <div
                role="tabpanel"
                id={`furniture-panel-${tab}`}
                aria-labelledby={`furniture-tab-${tab}`}
                className="space-y-4"
              >
                {tab === 'settlement' && data.month && (
                  <>
                    {/* The month's two steps, in order: collect from residents, then pay the provider. */}
                    <ResidentCollectionPanel
                      month={month}
                      bill={data.month}
                      onViewShare={(share) => setDialog({ kind: 'share_detail', share, bill: data.month! })}
                      onPayShare={(share) => setDialog({ kind: 'pay_share', share })}
                    />
                    <ProviderPayoutPanel
                      bill={data.month}
                      payouts={payoutsOf(data.month, data.service, data.config)}
                      onPayout={(payout) => setDialog({ kind: 'provider_payout', bill: data.month!, payout })}
                      onViewStatement={(payout) => setDialog({ kind: 'payout_statement', payout })}
                    />
                    {/* Whoever paid a provider more than their share is paid back from the kitty. */}
                    {reimbursementsOf(data.month).length > 0 && (
                      <KittyReimbursementPanel
                        reimbursements={reimbursementsOf(data.month)}
                        onReimburse={(owed) => setDialog({ kind: 'reimburse', reimbursement: owed })}
                        onViewStatement={(owed) => setDialog({ kind: 'reimbursement_statement', reimbursement: owed })}
                      />
                    )}
                  </>
                )}
                {tab === 'items' && (
                  <>
                    {data.month && (
                      <>
                        <BillSummaryPanel
                          month={month}
                          bill={data.month}
                          onConfigure={() => setDialog({ kind: 'bill_gst', bill: data.month! })}
                        />
                      </>
                    )}
                    <ItemsPanel
                      items={data.items}
                      onAdd={() => setDialog({ kind: 'add' })}
                      onView={(item) => setDialog({ kind: 'item_detail', item })}
                      onEdit={(item) => setDialog({ kind: 'edit', item })}
                      onReturn={(item) => setDialog({ kind: 'return', item })}
                    />
                  </>
                )}
                {tab === 'subscription' && (
                  <>
                    <SubscriptionsPanel
                      services={servicesOf(data)}
                      onAdd={() => setDialog({ kind: 'setup', entry: null, adding: true })}
                      onView={(entry) => setDialog({ kind: 'subscription_detail', entry })}
                      onEdit={(entry) => setDialog({ kind: 'setup', entry })}
                      onRetire={(entry) => setDialog({ kind: 'retire', entry })}
                    />
                  </>
                )}
              </div>
            </>
          )}

          {dialog?.kind === 'setup' && (
            <SetupDialog
              entry={dialog.entry}
              adding={Boolean(dialog.adding)}
              month={month}
              onClose={() => setDialog(null)}
              onSaved={() =>
                done(
                  dialog.entry ? 'Subscription saved' : dialog.adding ? 'Rental provider added' : 'Furniture rental set up',
                )
              }
            />
          )}
          {dialog?.kind === 'subscription_detail' && (
            <SubscriptionDetailModal
              entry={dialog.entry}
              items={data.items.filter((item) => item.service_id === dialog.entry.service.service_id)}
              onClose={() => setDialog(null)}
              onEdit={() => setDialog({ kind: 'setup', entry: dialog.entry })}
              onRetire={() => setDialog({ kind: 'retire', entry: dialog.entry })}
            />
          )}
          {dialog?.kind === 'retire' && (
            <RetireDialog
              month={month}
              entry={dialog.entry}
              onClose={() => setDialog(null)}
              onDone={() => done(`${dialog.entry.config.provider_name} subscription ended`)}
            />
          )}
          {(dialog?.kind === 'add' || dialog?.kind === 'edit') && (
            <ItemDialog
              item={dialog.kind === 'edit' ? dialog.item : null}
              services={servicesOf(data)}
              onClose={() => setDialog(null)}
              onRemove={(item) => setDialog({ kind: 'remove', item })}
              onSaved={(saved) => done(dialog.kind === 'edit' ? 'Item saved' : 'Item added', saved.name)}
            />
          )}
          {dialog?.kind === 'item_detail' && (
            <ItemDetailModal
              item={dialog.item}
              onClose={() => setDialog(null)}
              onEdit={() => setDialog({ kind: 'edit', item: dialog.item })}
              onReturn={() => setDialog({ kind: 'return', item: dialog.item })}
            />
          )}
          {dialog?.kind === 'return' && (
            <ReturnDialog
              item={dialog.item}
              onClose={() => setDialog(null)}
              onSaved={(saved) => done('Item returned', saved.name)}
            />
          )}
          {dialog?.kind === 'remove' && (
            <RemoveDialog
              item={dialog.item}
              onClose={() => setDialog(null)}
              onRemoved={() => done('Item removed', dialog.item.name)}
            />
          )}
          {dialog?.kind === 'provider_payout' && (
            <ServiceDisbursementDialog
              title="Record Provider Payout"
              month={month}
              payeeName={dialog.payout.provider_name}
              remainingPaise={dialog.payout.remaining_disbursement_paise}
              totalBillPaise={dialog.payout.total_paise}
              suggestedPaise={collectedForPayout(dialog.bill)}
              residents={payersOf(dialog.bill)}
              onSave={(payload) =>
                api
                  .post<FurnitureRentalOverview>(`${BASE}/disburse-payout`, {
                    settlement_month: month,
                    service_id: dialog.payout.service_id,
                    ...payload,
                  })
                  .then((updated) => {
                    payoutSaved.current = updated.month
                  })
              }
              onClose={() => setDialog(null)}
              onDone={() => {
                const payout = dialog.payout
                const updated = payoutSaved.current
                const now = updated?.provider_payouts?.find((row) => row.service_id === payout.service_id)
                const left = now ? now.remaining_disbursement_paise : (updated?.remaining_disbursement_paise ?? 0)
                const part = now
                  ? now.payout_status === 'partially_disbursed'
                  : updated?.payout_status === 'partially_disbursed'
                done(
                  'Provider payout recorded',
                  part ? `${formatInr(left)} is still owed to ${payout.provider_name}.` : undefined,
                )
              }}
            />
          )}
          {dialog?.kind === 'payout_statement' &&
            (() => {
              // The provider's side of the month as it now stands, not as it was when opened.
              const payout =
                (data.month && data.service && data.config
                  ? payoutsOf(data.month, data.service, data.config).find(
                      (row) => row.service_id === dialog.payout.service_id,
                    )
                  : undefined) ?? dialog.payout
              return (
                <ProviderPayoutStatementModal
                  title="Provider Payout Statement"
                  name={payout.provider_name}
                  month={month}
                  totalPaise={payout.total_paise}
                  paidPaise={payout.disbursed_paise}
                  stillOwedPaise={payout.remaining_disbursement_paise}
                  status={payout.payout_status}
                  disbursements={payout.disbursements}
                  onDeleteDisbursement={
                    data.month?.transactions_open
                      ? (tranche) =>
                          setDeleting({
                            title: 'Delete this payout?',
                            what: `${formatInr(tranche.amount_paise)} paid to ${payout.provider_name} on ${formatDate(tranche.disbursed_at)}${tranche.disbursed_by === 'pool' ? ' from the House Kitty' : ` by ${tranche.funding_resident_name ?? 'a resident'}`}.`,
                            url: `${BASE}/disbursements/${encodeURIComponent(tranche.disbursement_id)}`,
                          })
                      : undefined
                  }
                  onClose={() => setDialog(null)}
                />
              )
            })()}
          {dialog?.kind === 'share_detail' && data.config && (
            <FurnitureStatementModal
              month={month}
              // The share as it now stands, not as it was when opened.
              share={data.month?.shares.find((row) => row.resident_id === dialog.share.resident_id) ?? dialog.share}
              bill={data.month ?? dialog.bill}
              provider={data.config.provider_name}
              onDeletePayment={
                data.month?.transactions_open
                  ? (payment) =>
                      setDeleting({
                        title: 'Delete this payment?',
                        what: `${dialog.share.display_name}'s ${formatInr(payment.amount_paise)} payment of ${formatDate(payment.payment_date)}.`,
                        url: `${BASE}/payments/${encodeURIComponent(payment.payment_id)}`,
                      })
                  : undefined
              }
              onClose={() => setDialog(null)}
            />
          )}
          {dialog?.kind === 'reimbursement_statement' &&
            (() => {
              // Read from the month as it now stands, so a deleted reimbursement drops out at once.
              const owed = (data.month ? reimbursementsOf(data.month) : []).find(
                (row) => row.recipient_resident_id === dialog.reimbursement.recipient_resident_id,
              )
              return (
                owed && (
                  <KittyReimbursementStatementModal
                    month={month}
                    reimbursement={owed}
                    onDelete={
                      data.month?.transactions_open
                        ? (refund) =>
                            setDeleting({
                              title: 'Delete this reimbursement?',
                              what: `${formatInr(refund.amount_paise)} paid back from the kitty to ${owed.recipient_name} on ${formatDate(refund.payment_date)}.`,
                              url: `${BASE}/payments/${encodeURIComponent(refund.payment_id)}`,
                            })
                        : undefined
                    }
                    onClose={() => setDialog(null)}
                  />
                )
              )
            })()}
          {deleting && (
            <DeleteTransactionDialog
              deletion={deleting}
              onClose={() => setDeleting(null)}
              onDeleted={() => {
                setDeleting(null)
                toast.success('Deleted', 'The month owes what it owed before.')
                overview.reload()
              }}
            />
          )}
          {dialog?.kind === 'pay_share' && (
            <ServicePaymentDialog
              title="Record Payment into House Kitty"
              month={month}
              serviceLabel="furniture rental"
              residentName={dialog.share.display_name}
              outstandingPaise={dialog.share.outstanding_paise}
              onSave={(payload) =>
                api.post<FurnitureRentalOverview>(`${BASE}/record-payment`, {
                  settlement_month: month,
                  resident_id: dialog.share.resident_id,
                  ...payload,
                })
              }
              onClose={() => setDialog(null)}
              onDone={(amount) =>
                done('Payment recorded', `${formatInr(amount)} from ${dialog.share.display_name} into the House Kitty`)
              }
            />
          )}
          {dialog?.kind === 'bill_gst' && (
            <BillGstModal
              month={month}
              description={`${formatMonth(month)} furniture bill: GST on the items' rents, and the total the invoice charges.`}
              subtotalPaise={dialog.bill.subtotal_base_paise}
              gstRatePercent={dialog.bill.gst_rate_percent}
              chargedTotalOverridePaise={dialog.bill.charged_total_override_paise}
              isBilled={dialog.bill.expense != null}
              onSave={(values) =>
                api
                  .post<FurnitureRentalOverview>(`${BASE}/month/${encodeURIComponent(month)}/billing`, values)
                  .then((updated) =>
                    done(
                      'Bill updated',
                      `${formatMonth(month)} bills ${formatInr(updated.month?.total_paise ?? dialog.bill.total_paise)}, GST included.`,
                    ),
                  )
              }
              onClose={() => setDialog(null)}
            />
          )}
          {dialog?.kind === 'reimburse' && (
            <ReimburseDialog
              month={month}
              reimbursement={dialog.reimbursement}
              submit={(details) =>
                api.post<FurnitureRentalOverview>(`${BASE}/reimburse-from-kitty`, {
                  settlement_month: month,
                  recipient_resident_id: dialog.reimbursement.recipient_resident_id,
                  amount_paise: details.amount_paise,
                  payment_date: details.payment_date,
                  reference: details.reference,
                  notes: details.note,
                })
              }
              onClose={() => setDialog(null)}
              onDone={(amount) =>
                done('Reimbursement recorded', `${formatInr(amount)} from the kitty to ${dialog.reimbursement.recipient_name}`)
              }
            />
          )}
        </Page>
      )}
    </AsyncSection>
  )
}

/* ----------------------------------------------------- 1. the subscription -- */

function subscriptionReference(config: FurnitureConfig): string {
  return [config.customer_id && `Customer ${config.customer_id}`, config.subscription_reference]
    .filter(Boolean)
    .join(' · ')
}

/** Every provider's subscription, one card each; a card opens its details (Rule U7). */
function SubscriptionsPanel({
  services,
  onAdd,
  onView,
  onEdit,
  onRetire,
}: {
  services: FurnitureServiceEntry[]
  onAdd: () => void
  onView: (entry: FurnitureServiceEntry) => void
  onEdit: (entry: FurnitureServiceEntry) => void
  onRetire: (entry: FurnitureServiceEntry) => void
}) {
  const several = services.length > 1
  return (
    <Panel
      title="Rental Subscriptions"
      icon={<Sofa className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Each provider's subscription, monthly commitment, and security deposit. Select a provider card for full details and its rented items."
      menuActions={[{ label: 'Add rental provider', icon: Plus, onClick: onAdd }]}
    >
      <div className={several ? 'grid grid-cols-1 lg:grid-cols-2 gap-4' : 'grid grid-cols-1 gap-4'}>
        {services.map((entry) => (
          <ProviderSubscriptionCard
            key={entry.service.service_id}
            entry={entry}
            several={several}
            onView={() => onView(entry)}
            onEdit={() => onEdit(entry)}
            onRetire={() => onRetire(entry)}
          />
        ))}
      </div>
    </Panel>
  )
}

function ProviderSubscriptionCard({
  entry,
  several,
  onView,
  onEdit,
  onRetire,
}: {
  entry: FurnitureServiceEntry
  several: boolean
  onView: () => void
  onEdit: () => void
  onRetire: () => void
}) {
  const { service, config } = entry
  const reference = subscriptionReference(config)
  const headingId = `furniture-subscription-${service.service_id}`
  return (
    <article
      // A tinted surface, so the card stands off the panel it sits in. (An
      // opacity modifier like `/60` on a hex variable emits nothing in Tailwind 3.)
      className="rounded-xl border border-[var(--border-subtle)] hover:border-[var(--border-medium)] bg-[color-mix(in_srgb,var(--bg-elevated)_60%,transparent)] hover:bg-[var(--bg-elevated)] p-4 sm:p-5 space-y-4 min-w-0 cursor-pointer transition-colors"
      tabIndex={0}
      aria-labelledby={headingId}
      title={`View the ${config.provider_name} subscription`}
      onClick={onView}
      onKeyDown={(event) => {
        // Only the card itself: keys pressed in its menu are the menu's.
        if (event.target !== event.currentTarget) return
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onView()
        }
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <Avatar name={config.provider_name} tone="accent" />
          <div className="min-w-0">
            <h3 id={headingId} className="m-0 font-bold text-sm text-[var(--text-primary)] truncate">
              {several ? `${service.display_name} · ${config.provider_name}` : config.provider_name}
            </h3>
            <div className="text-[11px] text-[var(--text-secondary)] truncate">
              {reference || 'No customer ID on file'}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Whether the subscription is running: the deposit's state is under Deposit held. */}
          <Pill tone={service.status === 'paused' ? 'warning' : 'success'}>
            {service.status === 'paused' ? 'Paused' : 'Active'}
          </Pill>
          {/* The card's own actions: choosing one does not also open the card. */}
          <div onClick={(event) => event.stopPropagation()}>
            <CardActions
              actions={[
                { label: 'Edit subscription', icon: Pencil, onClick: onEdit },
                { label: 'End subscription', icon: Archive, onClick: onRetire, tone: 'danger' },
              ]}
            />
          </div>
        </div>
      </div>
      {/* The provider and its reference are in the header, so the figures start with the money. */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-3.5 border-t border-[var(--border-subtle)]">
        <CardFigure
          label="Monthly total"
          value={<Rupees paise={entry.monthly_total_paise} />}
          note={`${entry.active_item_count} item${entry.active_item_count === 1 ? '' : 's'} in the flat`}
        />
        <CardFigure
          label="Next billing"
          value={<span className="tabular">{entry.next_billing_date ? formatDate(entry.next_billing_date) : '—'}</span>}
          note={service.billing_day_of_month ? `Day ${service.billing_day_of_month} each month` : 'No billing day set'}
        />
        <CardFigure
          label="Deposit held"
          value={<Rupees paise={entry.deposit_held_paise} />}
          note={`${DEPOSIT_STATUS[config.deposit_status]} · ${formatInr(config.deposit_paid_paise)} paid`}
        />
      </div>
    </article>
  )
}

/** One figure on a provider card: a MiniStat's type without its box, since the card is the box. */
function CardFigure({ label, value, note }: { label: string; value: ReactNode; note: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">{label}</div>
      <div className="mt-1 text-base font-bold text-[var(--text-primary)] truncate">{value}</div>
      <div className="mt-0.5 text-[11px] text-[var(--text-secondary)]">{note}</div>
    </div>
  )
}

/** One provider's subscription in full: its deposit, its account, and what is rented from it. */
function SubscriptionDetailModal({
  entry,
  items,
  onClose,
  onEdit,
  onRetire,
}: {
  entry: FurnitureServiceEntry
  /** The items rented from this provider. */
  items: FurnitureItem[]
  onClose: () => void
  onEdit: () => void
  onRetire: () => void
}) {
  const { service, config } = entry
  const reference = subscriptionReference(config)
  return (
    <Modal
      title={`${config.provider_name} Subscription`}
      description={reference || 'Subscription details, security deposit, and rented items'}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onRetire}>
            <Archive className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            End subscription
          </button>
          <button type="button" className={BUTTON_PRIMARY} onClick={onEdit}>
            <Pencil className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Edit subscription
          </button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MiniStat
            label="Monthly Rent"
            value={<Rupees paise={entry.monthly_total_paise} />}
            note={`${entry.active_item_count} active item${entry.active_item_count === 1 ? '' : 's'}`}
          />
          <MiniStat
            label="Next Billing"
            value={<span className="tabular">{entry.next_billing_date ? formatDate(entry.next_billing_date) : '—'}</span>}
            note={service.billing_day_of_month ? `Day ${service.billing_day_of_month} each month` : 'No billing day set'}
          />
          <MiniStat
            label="Deposit Paid"
            value={<Rupees paise={config.deposit_paid_paise} />}
            note={
              config.deposit_refunded_paise > 0
                ? `${formatInr(config.deposit_refunded_paise)} refunded`
                : 'No refund recorded yet'
            }
          />
          <MiniStat
            label="Deposit Held"
            value={<Rupees paise={entry.deposit_held_paise} />}
            note={DEPOSIT_STATUS[config.deposit_status]}
          />
        </div>

        <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm" aria-label="Account and contract details">
          <DetailFact label="Customer ID">{config.customer_id || '—'}</DetailFact>
          <DetailFact label="Subscription Reference">{config.subscription_reference || '—'}</DetailFact>
          <DetailFact label="Deposit Status">{DEPOSIT_STATUS[config.deposit_status]}</DetailFact>
          {config.notes && <DetailFact label="Notes">{config.notes}</DetailFact>}
        </dl>

        <section className="space-y-2" aria-labelledby="furniture-subscription-items">
          <h3 id="furniture-subscription-items" className="m-0 text-sm font-bold text-[var(--text-primary)]">
            Rented Items from {config.provider_name}
          </h3>
          {items.length === 0 ? (
            <EmptyBlock title="Nothing rented from this provider yet" />
          ) : (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-xs min-w-[480px] border-collapse">
                <caption className="sr-only">Items rented from {config.provider_name}</caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className="pb-3 pr-3 font-bold text-left">Item</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Paid by</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Monthly rent</th>
                    <th scope="col" className="pb-3 font-bold text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {items.map((item) => {
                    const status = ITEM_STATUS[item.status]
                    return (
                      <tr key={item.item_id} className="align-middle">
                        <th scope="row" className="py-3 pr-3 text-left font-semibold text-[var(--text-primary)]">
                          {item.name}
                          {item.quantity > 1 && (
                            <span className="block text-[11px] font-normal text-[var(--text-tertiary)]">
                              Qty {item.quantity}
                            </span>
                          )}
                        </th>
                        <td className="py-3 pr-3 text-center">
                          {item.allocation_scope === 'common' ? (
                            <Pill tone="accent">Shared</Pill>
                          ) : (
                            item.assigned_resident_name ?? item.assigned_resident_id
                          )}
                        </td>
                        <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                          <Rupees paise={item.monthly_rent_paise} />
                        </td>
                        <td className="py-3 text-center whitespace-nowrap">
                          <Pill tone={status.tone}>{status.label}</Pill>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </Modal>
  )
}

/* ---------------------------------------------------------- 2. the items -- */

function ItemsPanel({
  items,
  onAdd,
  onView,
  onEdit,
  onReturn,
}: {
  items: FurnitureItem[]
  onAdd: () => void
  onView: (item: FurnitureItem) => void
  onEdit: (item: FurnitureItem) => void
  onReturn: (item: FurnitureItem) => void
}) {
  return (
    <Panel
      title="Rented Items"
      icon={<Sofa className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Shared items are split between everyone; a person's own items are theirs alone. Select an item for full details."
      menuActions={[{ label: 'Add item', icon: Plus, onClick: onAdd }]}
    >
      {items.length === 0 ? (
        <EmptyBlock title="No items yet" body="Add each item on the subscription, with what it costs a month." />
      ) : (
        <div className="overflow-x-auto min-w-0">
          <table className="w-full table-fixed text-xs min-w-[560px] border-collapse">
            <caption className="sr-only">Rented items</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="w-[40%] pb-3 pr-3 font-bold text-left">Item</th>
                <th scope="col" className="w-[16%] pb-3 pr-3 font-bold text-center">Paid by</th>
                <th scope="col" className="w-[16%] pb-3 pr-3 font-bold text-center">Monthly rent</th>
                <th scope="col" className="w-[16%] pb-3 pr-3 font-bold text-center">Status</th>
                <th scope="col" className="w-[12%] pb-3 font-bold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {items.map((item) => {
                const status = ITEM_STATUS[item.status]
                return (
                  // The whole row opens the item (Rule U7); its menu edits or returns it.
                  <tr
                    key={item.item_id}
                    className="align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                    tabIndex={0}
                    title={`View ${item.name} details`}
                    onClick={() => onView(item)}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        onView(item)
                      }
                    }}
                  >
                    <th scope="row" className="py-3 pr-3 text-left font-normal">
                      <span className="block font-semibold text-[var(--text-primary)] truncate">{item.name}</span>
                      <span className="block text-[11px] text-[var(--text-tertiary)] truncate">
                        {item.provider_name}
                        {item.quantity > 1 && ` · Qty ${item.quantity}`}
                      </span>
                    </th>
                    <td className="py-3 pr-3 text-center">
                      {item.allocation_scope === 'common' ? (
                        <Pill tone="accent">Shared</Pill>
                      ) : (
                        item.assigned_resident_name ?? item.assigned_resident_id
                      )}
                    </td>
                    <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                      <Rupees paise={item.monthly_rent_paise} />
                    </td>
                    <td className="py-3 pr-3 text-center whitespace-nowrap">
                      <Pill tone={status.tone}>{status.label}</Pill>
                    </td>
                    <td className="py-3 text-center whitespace-nowrap">
                      {/* The row's own actions: choosing one does not also open the item. */}
                      <div className="inline-flex justify-center" onClick={(event) => event.stopPropagation()}>
                        <CardActions
                          floating
                          actions={[
                            { label: 'Edit item', icon: Pencil, onClick: () => onEdit(item) },
                            ...(item.status !== 'returned'
                              ? [{ label: 'Return item', icon: Undo2, onClick: () => onReturn(item) }]
                              : []),
                          ]}
                        />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

/** One rented item: what it costs, who pays for it, and when it came and went. */
function ItemDetailModal({
  item,
  onClose,
  onEdit,
  onReturn,
}: {
  item: FurnitureItem
  onClose: () => void
  onEdit: () => void
  onReturn: () => void
}) {
  const status = ITEM_STATUS[item.status]
  const shared = item.allocation_scope === 'common'
  return (
    <Modal
      title={item.name}
      description={item.provider_name ?? 'Rented item'}
      onClose={onClose}
      footer={
        <>
          {item.status !== 'returned' && (
            <button type="button" className={BUTTON_SECONDARY} onClick={onReturn}>
              <Undo2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              Return item
            </button>
          )}
          <button type="button" className={BUTTON_PRIMARY} onClick={onEdit}>
            <Pencil className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            Edit item
          </button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat
            label="Monthly Rent"
            value={<Rupees paise={item.monthly_rent_paise} />}
            note={
              item.quantity > 1
                ? `${item.quantity} × ${formatInr(item.unit_monthly_rent_paise)} · Before GST`
                : 'Before GST'
            }
          />
          <MiniStat
            label="Paid By"
            value={shared ? 'Shared equally' : (item.assigned_resident_name ?? item.assigned_resident_id ?? '—')}
            note={shared ? 'All participating flatmates' : 'Personal item'}
          />
          <MiniStat label="Status" value={<Pill tone={status.tone}>{status.label}</Pill>} />
        </div>
        <dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm" aria-label="Item details">
          {item.quantity > 1 && (
            <>
              <DetailFact label="Quantity">
                <span className="tabular">{item.quantity}</span>
              </DetailFact>
              <DetailFact label="Unit Rent">
                <span className="tabular">{formatInr(item.unit_monthly_rent_paise)}</span>
              </DetailFact>
              <DetailFact label="Total Monthly Rent">
                <span className="tabular font-semibold">{formatInr(item.monthly_rent_paise)}</span>
              </DetailFact>
            </>
          )}
          <DetailFact label="Rental Provider">{item.provider_name ?? '—'}</DetailFact>
          <DetailFact label="Delivery Date">
            <span className="tabular">{item.delivery_date ? formatDate(item.delivery_date) : '—'}</span>
          </DetailFact>
          <DetailFact label="Return Date">
            <span className="tabular">
              {item.return_date
                ? formatDate(item.return_date)
                : item.status === 'pending_delivery'
                  ? 'Not delivered yet'
                  : 'Still in the flat'}
            </span>
          </DetailFact>
        </dl>
      </div>
    </Modal>
  )
}

/* ------------------------------------------------- 3. the month's bill -- */

/**
 * Whether the month's GST and total can still change: until its statement is
 * finalized, a month gone by as much as this one or one ahead.
 */
function billIsOpen(bill: FurnitureMonthBill): boolean {
  return bill.bill_state === 'live' || bill.statement_status === 'draft' || bill.statement_status == null
}

/**
 * The month's bill: the items' rents before GST, the GST the bill adds on them,
 * and what it comes to -- the invoice's own total where one was set. While the
 * month is open its GST rate and total can be set.
 */
function BillSummaryPanel({
  month,
  bill,
  onConfigure,
}: {
  month: string
  bill: FurnitureMonthBill
  onConfigure: () => void
}) {
  const overridden = bill.charged_total_override_paise != null
  return (
    <Panel
      title="Monthly Bill"
      icon={<Receipt className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={
        billIsOpen(bill)
          ? "GST is added to the items' rents on the bill, as the provider's invoice adds it."
          : `${formatMonth(month)} is closed: it reads as it was billed.`
      }
      menuActions={
        billIsOpen(bill) ? [{ label: 'Configure GST & Total', icon: Percent, onClick: onConfigure }] : undefined
      }
    >
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <MiniStat label="Items Subtotal" value={<Rupees paise={bill.subtotal_base_paise} />} note="Rents before GST" />
        <MiniStat
          label={bill.gst_rate_percent == null ? 'GST' : `GST (${rateText(bill.gst_rate_percent)}%)`}
          value={<Rupees paise={bill.total_gst_paise} />}
          note={overridden ? 'As invoiced' : bill.gst_rate_percent == null ? 'In the items’ rents' : undefined}
        />
        <MiniStat
          label="Gross Bill Total"
          value={<Rupees paise={bill.total_paise} />}
          note={overridden ? `Calculated: ${formatInr(bill.calculated_total_paise)}` : 'GST included'}
        />
      </div>
    </Panel>
  )
}

function ResidentCollectionPanel({
  month,
  bill,
  onViewShare,
  onPayShare,
}: {
  month: string
  bill: FurnitureMonthBill
  onViewShare: (share: FurnitureShare) => void
  onPayShare: (share: FurnitureShare) => void
}) {
  // Who has paid what means something only once the bill is recorded, which an
  // open month is by itself as soon as it has anything to bill.
  const billed = bill.expense != null
  const items: ResidentCollectionItem[] = bill.shares.map((share) => {
    const funded = share.status === 'funded_payout'
    const status: CollectionStatus | null =
      share.status === 'paid' || funded
        ? 'paid'
        : share.status === 'partial'
          ? 'partial'
          : share.status === 'unpaid'
            ? 'unpaid'
            : null
    return {
      id: share.resident_id,
      name: share.display_name,
      href: `/residents/${share.resident_id}`,
      subtitle: share.taking_part ? undefined : 'Not taking part',
      subtitleTone: share.taking_part ? undefined : 'warning',
      sharePaise: share.total_paise,
      paidIntoKittyPaise: billed ? share.settled_paise : null,
      stillOwedPaise: billed ? share.outstanding_paise : null,
      status,
      canPay: billed && !isSettled(share),
      onPay: () => onPayShare(share),
      onViewStatement: () => onViewShare(share),
    }
  })
  return (
    <Panel
      title="Resident Collection"
      icon={<Users className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each resident owes for this month's furniture rental, and how much has been collected into the House Kitty."
    >
      {bill.blocked_reason && !bill.expense && (
        <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
          {bill.blocked_reason}
        </p>
      )}

      {bill.shares.length === 0 ? (
        <EmptyBlock title="No residents are taking part in furniture rental." />
      ) : (
        <ResidentCollectionTable caption={`Shares for ${formatMonth(month)}`} items={items} />
      )}
    </Panel>
  )
}

/* -------------------------------------------- 4. paying the provider -- */

function ProviderPayoutPanel({
  bill,
  payouts,
  onPayout,
  onViewStatement,
}: {
  bill: FurnitureMonthBill
  payouts: FurnitureProviderPayout[]
  onPayout: (payout: FurnitureProviderPayout) => void
  onViewStatement: (payout: FurnitureProviderPayout) => void
}) {
  const billed = bill.expense != null
  const single = payouts.length === 1 ? payouts[0].provider_name : null
  const items: ProviderPayoutItem[] = payouts.map((payout) => ({
    id: payout.service_id,
    name: payout.provider_name,
    totalPaise: payout.total_paise,
    // Nothing is paid or owed until the bill is recorded.
    paidPaise: billed ? payout.disbursed_paise : null,
    stillOwedPaise: billed ? payout.remaining_disbursement_paise : null,
    status: billed ? payout.payout_status : null,
    // Open until the provider has been paid in full: the bill can go in tranches.
    canPay: billed && payout.payout_status !== 'disbursed' && payout.total_paise > 0,
    onPay: () => onPayout(payout),
    onViewStatement: billed ? () => onViewStatement(payout) : undefined,
  }))
  return (
    <Panel
      title="Provider Payout"
      icon={<Store className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="What each provider is owed for the month, and payouts made to them. Select a row for its disbursement history."
    >
      <ProviderPayoutTable
        caption={single ? `The furniture bill owed to ${single}` : 'The furniture bill owed to each provider'}
        items={items}
      />
    </Panel>
  )
}

function ShareStatus({ status }: { status: FurnitureShareStatus | null; provider?: string }) {
  if (!status) return <span className="text-[var(--text-tertiary)]">—</span>
  const { label, tone } = SHARE_STATUS[status]
  return (
    <Pill tone={tone}>
      {status === 'paid' || status === 'funded_payout' ? (
        <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
      ) : (
        <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
      )}
      {label}
    </Pill>
  )
}

/* -------------------------------------------- 5. paying back who paid it -- */

/* -------------------------------------------------------------- dialogs -- */

function DialogFooter({
  onClose,
  pending,
  disabled,
  label,
  form,
  tone = 'primary',
}: {
  onClose: () => void
  pending: boolean
  disabled?: boolean
  label: string
  form: string
  tone?: 'primary' | 'danger'
}) {
  return (
    <>
      <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={pending}>
        Cancel
      </button>
      <button
        type="submit"
        form={form}
        className={tone === 'danger' ? `${BUTTON_PRIMARY} !bg-[var(--danger)]` : BUTTON_PRIMARY}
        disabled={disabled || pending}
      >
        {pending ? 'Saving…' : label}
      </button>
    </>
  )
}

function submitWith(run: () => void) {
  return (event: React.FormEvent) => {
    event.preventDefault()
    run()
  }
}

function SetupDialog({
  entry,
  adding,
  month,
  onClose,
  onSaved,
}: {
  entry: FurnitureServiceEntry | null
  adding: boolean
  month: string
  onClose: () => void
  onSaved: () => void
}) {
  const config = entry?.config ?? null
  const known = config && PROVIDERS.includes(config.provider_name)
  const [provider, setProvider] = useState(config ? (known ? config.provider_name : 'Other') : 'Furlenco')
  const [otherProvider, setOtherProvider] = useState(config && !known ? config.provider_name : '')
  const [customerId, setCustomerId] = useState(config?.customer_id ?? '')
  const [reference, setReference] = useState(config?.subscription_reference ?? '')
  const [billingDay, setBillingDay] = useState(
    entry?.service.billing_day_of_month != null ? String(entry.service.billing_day_of_month) : '',
  )
  const [paid, setPaid] = useState(paiseToInput(config?.deposit_paid_paise ?? 0))
  const [paidPaise, setPaidPaise] = useState(config?.deposit_paid_paise ?? 0)
  const [paidOk, setPaidOk] = useState(true)
  const [refunded, setRefunded] = useState(paiseToInput(config?.deposit_refunded_paise ?? 0))
  const [refundedPaise, setRefundedPaise] = useState(config?.deposit_refunded_paise ?? 0)
  const [refundedOk, setRefundedOk] = useState(true)
  const [notes, setNotes] = useState(config?.notes ?? '')

  const providerName = provider === 'Other' ? otherProvider.trim() : provider
  const dayError =
    billingDay.trim() && !(/^\d{1,2}$/.test(billingDay.trim()) && +billingDay >= 1 && +billingDay <= 31)
      ? 'Enter a day from 1 to 31.'
      : null
  const refundError = refundedOk && paidOk && refundedPaise > paidPaise ? 'More than the deposit paid.' : null
  const valid = Boolean(providerName) && !dayError && paidOk && refundedOk && !refundError

  const save = useMutation(
    () =>
      api.post<FurnitureRentalOverview>(
        `${BASE}/setup`,
        {
          provider_name: providerName,
          customer_id: customerId.trim() || null,
          subscription_reference: reference.trim() || null,
          billing_day_of_month: billingDay.trim() ? Number(billingDay.trim()) : null,
          deposit_paid_paise: paidPaise,
          deposit_refunded_paise: refundedPaise,
          notes: notes.trim() || null,
          // Which provider: the one being edited, or another one being added.
          ...(entry ? { service_id: entry.service.service_id } : adding ? { create_new: true } : {}),
        },
        { month },
      ),
    { onSuccess: onSaved },
  )
  return (
    <Modal
      title={entry ? 'Edit subscription' : adding ? 'Add rental provider' : 'Set up furniture rental'}
      description="The provider, the subscription's references, its billing day, and the deposit."
      onClose={onClose}
      wide
      footer={
        <DialogFooter onClose={onClose} pending={save.pending} disabled={!valid} label="Save" form="furniture-setup" />
      }
    >
      <form id="furniture-setup" className="stack" noValidate onSubmit={submitWith(() => valid && void save.run(undefined))}>
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField
            label="Provider"
            value={provider}
            options={[...PROVIDERS, 'Other'].map((name) => ({ value: name, label: name }))}
            onChange={setProvider}
          />
          {provider === 'Other' ? (
            <TextField label="Provider name" required value={otherProvider} onChange={setOtherProvider} maxLength={100} />
          ) : (
            <TextField
              label="Billing day"
              value={billingDay}
              onChange={setBillingDay}
              inputMode="numeric"
              maxLength={2}
              placeholder="e.g. 5"
              error={dayError}
              hint="The day each month's bill is due."
            />
          )}
        </div>
        {provider === 'Other' && (
          <TextField
            label="Billing day"
            value={billingDay}
            onChange={setBillingDay}
            inputMode="numeric"
            maxLength={2}
            placeholder="e.g. 5"
            error={dayError}
            hint="The day each month's bill is due."
          />
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField label="Customer ID" value={customerId} onChange={setCustomerId} maxLength={100} />
          <TextField label="Subscription / order reference" value={reference} onChange={setReference} maxLength={100} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Deposit paid"
            value={paid}
            hint="Refundable deposit paid to the provider."
            onChange={(raw, parsed) => {
              setPaid(raw)
              setPaidPaise(parsed.paise)
              setPaidOk(parsed.ok)
            }}
          />
          <MoneyInput
            label="Deposit refunded"
            value={refunded}
            error={refundError}
            hint="What the provider has paid back so far."
            onChange={(raw, parsed) => {
              setRefunded(raw)
              setRefundedPaise(parsed.paise)
              setRefundedOk(parsed.ok)
            }}
          />
        </div>
        <TextField label="Notes" value={notes} onChange={setNotes} maxLength={2000} />
      </form>
    </Modal>
  )
}

function RetireDialog({
  month,
  entry,
  onClose,
  onDone,
}: {
  month: string
  entry: FurnitureServiceEntry
  onClose: () => void
  onDone: () => void
}) {
  const retire = useMutation(
    () => api.post(`${BASE}/retire`, undefined, { month, service_id: entry.service.service_id }),
    { onSuccess: onDone },
  )
  return (
    <Modal
      title={`End the ${entry.config.provider_name} subscription?`}
      description="Every item must have been returned first. The subscription, its items and every bill already recorded stay for the record."
      onClose={onClose}
      footer={
        <DialogFooter onClose={onClose} pending={retire.pending} label="End subscription" form="furniture-retire" tone="danger" />
      }
    >
      <form id="furniture-retire" className="stack" onSubmit={submitWith(() => void retire.run(undefined))}>
        <ErrorNotice error={retire.error} />
        <p className="m-0 text-sm text-[var(--text-secondary)]">
          Collect the deposit refund from the provider before ending it, and record it on the subscription.
        </p>
      </form>
    </Modal>
  )
}

function ItemDialog({
  item,
  services,
  onClose,
  onRemove,
  onSaved,
}: {
  item: FurnitureItem | null
  services: FurnitureServiceEntry[]
  onClose: () => void
  onRemove: (item: FurnitureItem) => void
  onSaved: (saved: FurnitureItem) => void
}) {
  const residents = useResource<{ items: Resident[] }>('/api/residents')
  // The provider it is rented from.
  const [serviceId, setServiceId] = useState(item?.service_id ?? services[0]?.service.service_id ?? '')
  const [name, setName] = useState(item?.name ?? '')
  // How many alike, and the rent for one as quoted, before GST: the month's bill adds GST.
  const [quantityRaw, setQuantityRaw] = useState(String(item?.quantity ?? 1))
  const quantity = parseQuantity(quantityRaw)
  const unitStart = item ? (item.unit_monthly_rent_paise ?? Math.floor(item.monthly_rent_paise / (item.quantity || 1))) : 0
  const [rent, setRent] = useState(item ? paiseToInput(unitStart) : '')
  const [rentPaise, setRentPaise] = useState(unitStart)
  const [rentOk, setRentOk] = useState(Boolean(item))
  // Who pays: '' for everyone, else the resident the item is for.
  const [payer, setPayer] = useState(item?.assigned_resident_id ?? '')
  const [status, setStatus] = useState<'active' | 'pending_delivery'>(
    item?.status === 'pending_delivery' ? 'pending_delivery' : 'active',
  )
  // The day it arrived, from which it is billed: today for a new item, unless changed.
  const [delivery, setDelivery] = useState(item ? (item.delivery_date ?? '') : todayIso())

  const people = (residents.data?.items ?? []).filter(
    (resident) => resident.is_active || resident.resident_id === item?.assigned_resident_id,
  )
  const valid =
    Boolean(serviceId) &&
    name.trim().length > 0 &&
    rentOk &&
    rentPaise > 0 &&
    quantity !== null &&
    (status !== 'active' || Boolean(delivery))
  const body = {
    service_id: serviceId || null,
    name: name.trim(),
    quantity: quantity ?? 1,
    monthly_rent_paise: rentPaise * (quantity ?? 1),
    allocation_scope: payer ? 'resident' : 'common',
    assigned_resident_id: payer || null,
    delivery_date: delivery || null,
    ...(item?.status === 'returned' ? {} : { status }),
  }
  const save = useMutation(
    () =>
      item
        ? api.patch<FurnitureItem>(`${BASE}/items/${encodeURIComponent(item.item_id)}`, body)
        : api.post<FurnitureItem>(`${BASE}/items`, body),
    { onSuccess: onSaved },
  )

  return (
    <Modal
      title={item ? `Edit ${item.name}` : 'Add a rented item'}
      description="What it is, what it costs a month, and who pays for it."
      onClose={onClose}
      wide
      footer={
        <>
          {item && (
            <button type="button" className={`${BUTTON_QUIET} mr-auto`} onClick={() => onRemove(item)}>
              Remove item
            </button>
          )}
          <DialogFooter onClose={onClose} pending={save.pending} disabled={!valid} label={item ? 'Save' : 'Add item'} form="furniture-item" />
        </>
      }
    >
      <form id="furniture-item" className="stack" noValidate onSubmit={submitWith(() => valid && void save.run(undefined))}>
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField
            label="Rental provider"
            required
            value={serviceId}
            options={services.map((entry) => ({ value: entry.service.service_id, label: entry.config.provider_name }))}
            onChange={setServiceId}
            hint="The subscription it is rented on."
          />
          <TextField label="Item" required value={name} onChange={setName} maxLength={200} placeholder="e.g. 3-seater sofa" />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField
            label="Quantity"
            required
            type="number"
            min={1}
            step={1}
            inputMode="numeric"
            value={quantityRaw}
            onChange={setQuantityRaw}
            error={quantityRaw.trim() && quantity === null ? 'Enter a whole number, 1 or more.' : null}
            hint="How many alike, rented together."
          />
          <MoneyInput
            label="Monthly rent per item"
            required
            value={rent}
            onChange={(raw, parsed) => {
              setRent(raw)
              setRentPaise(parsed.paise)
              setRentOk(parsed.ok)
            }}
            hint="For one, as quoted, before GST."
          />
        </div>
        {quantity !== null && quantity > 1 && rentOk && rentPaise > 0 && (
          <p role="status" className="m-0 text-xs font-medium text-[var(--text-secondary)] tabular">
            {quantity} items × {formatInr(rentPaise)} = {formatInr(rentPaise * quantity)}/mo total
          </p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField
            label="Paid by"
            value={payer}
            options={[
              { value: '', label: 'Shared by everyone' },
              ...people.map((resident) => ({ value: resident.resident_id, label: resident.display_name })),
            ]}
            onChange={setPayer}
            hint="Shared items are split equally; a person's own item is charged to them."
          />
        </div>
        {item?.status !== 'returned' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <SelectField<'active' | 'pending_delivery'>
              label="Status"
              value={status}
              options={[
                { value: 'active', label: 'In the flat' },
                { value: 'pending_delivery', label: 'Awaiting delivery' },
              ]}
              onChange={setStatus}
              hint="An item awaiting delivery is not billed."
            />
            {status === 'pending_delivery' ? (
              <DateField label="Expected delivery" value={delivery} onChange={setDelivery} hint="Optional until delivered." />
            ) : (
              <DateField
                label="Delivered on"
                required
                value={delivery}
                onChange={setDelivery}
                error={delivery ? null : 'Enter the day it arrived in the flat.'}
                hint="It is billed from the month it arrived."
              />
            )}
          </div>
        )}
      </form>
    </Modal>
  )
}

function ReturnDialog({
  item,
  onClose,
  onSaved,
}: {
  item: FurnitureItem
  onClose: () => void
  onSaved: (saved: FurnitureItem) => void
}) {
  const [date, setDate] = useState(todayIso())
  const tooEarly = Boolean(item.delivery_date && date && date < item.delivery_date)
  const save = useMutation(
    () =>
      api.patch<FurnitureItem>(`${BASE}/items/${encodeURIComponent(item.item_id)}`, {
        status: 'returned',
        return_date: date,
      }),
    { onSuccess: onSaved },
  )
  return (
    <Modal
      title={`Return ${item.name}`}
      description="Nothing is prorated: the month it goes back is still billed in full; the months after are not."
      onClose={onClose}
      footer={
        <DialogFooter onClose={onClose} pending={save.pending} disabled={!date || tooEarly} label="Record return" form="furniture-return" />
      }
    >
      <form id="furniture-return" className="stack" noValidate onSubmit={submitWith(() => !tooEarly && void save.run(undefined))}>
        <ErrorNotice error={save.error} />
        <DateField
          label="Returned on"
          required
          value={date}
          onChange={setDate}
          error={tooEarly ? 'Before it was delivered.' : null}
        />
      </form>
    </Modal>
  )
}

function RemoveDialog({ item, onClose, onRemoved }: { item: FurnitureItem; onClose: () => void; onRemoved: () => void }) {
  const remove = useMutation(() => api.delete(`${BASE}/items/${encodeURIComponent(item.item_id)}`), {
    onSuccess: onRemoved,
  })
  return (
    <Modal
      title={`Remove ${item.name}?`}
      description="For an item entered by mistake. To stop paying for an item, return it instead, so its history is kept."
      onClose={onClose}
      footer={
        <DialogFooter onClose={onClose} pending={remove.pending} label="Remove item" form="furniture-remove" tone="danger" />
      }
    >
      <form id="furniture-remove" className="stack" onSubmit={submitWith(() => void remove.run(undefined))}>
        <ErrorNotice error={remove.error} />
        <p className="m-0 text-sm text-[var(--text-secondary)]">
          This month's bill drops it. Months already closed keep the items behind them in their notes.
        </p>
      </form>
    </Modal>
  )
}

/** The provider's side of the month: what is due to them, and every payment made (Rule U7). */
/** One resident's share of the month: what they owe, and every payment into the kitty towards it (Rule U7). */
function FurnitureStatementModal({
  month,
  share,
  bill,
  provider,
  onDeletePayment,
  onClose,
}: {
  month: string
  share: FurnitureShare
  bill: FurnitureMonthBill
  provider: string
  /** Deletes a payment recorded by mistake, while the month is open; without it none is offered. */
  onDeletePayment?: (payment: FurnitureShare['payments'][number]) => void
  onClose: () => void
}) {
  const billed = bill.expense != null
  // What they paid the provider beyond their own share, which the kitty pays back.
  const extra = bill.disbursements
    .filter((tranche) => tranche.funding_resident_id === share.resident_id)
    .reduce((total, tranche) => total + tranche.amount_paise, 0)
  const modes = new Map(PAYMENT_MODES.map((mode) => [mode.value, mode.label]))
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  return (
    <Modal
      title="Furniture Statement"
      description={`${share.display_name} · ${formatMonth(month)}`}
      onClose={onClose}
    >
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat
            label="Share"
            value={<Rupees paise={share.total_paise} />}
            note={share.private_paise > 0 ? `incl. ${formatInr(share.private_paise)} own items` : undefined}
          />
          <MiniStat label="Paid into Kitty" value={billed ? <Rupees paise={share.settled_paise} /> : dash} />
          <MiniStat label="Still Owed" value={billed ? <Rupees paise={share.outstanding_paise} /> : dash} />
        </div>

        {extra > 0 && (
          <Callout>
            {share.display_name} paid an extra {formatInr(extra)} beyond their allotted share of{' '}
            {formatInr(share.total_paise)}, which will be reimbursed via the House Kitty.
          </Callout>
        )}

        <section aria-label="Payment history" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Payment History</h3>
            {share.status && <ShareStatus status={share.status} provider={provider} />}
          </div>
          {share.payments.length === 0 ? (
            <EmptyBlock title="No payments recorded yet for this month." />
          ) : (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[480px] border-collapse">
                <caption className="sr-only">Payments towards {share.display_name}'s furniture share</caption>
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
                        {payment.payment_mode ? modes.get(payment.payment_mode) : dash}
                      </td>
                      <td className="py-2.5 pr-3 text-center tabular break-all text-[var(--text-primary)]">
                        {payment.reference || dash}
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

