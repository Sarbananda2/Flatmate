/**
 * Furniture rental (Furlenco, Rentomojo): the subscription, the items rented,
 * and what each month bills.
 *
 * Every figure comes from the backend. Shared items are split equally between
 * the residents taking part and a resident's own items are charged to them;
 * nothing is prorated, so an item in the flat at any point in a month counts in
 * full. Recording a month's bill writes one ordinary expense.
 *
 * Once it is recorded, the month is paid in the same two steps as rent:
 * flatmates pay their shares into the House Kitty, and the provider is paid the
 * bill, in one payout or several, from the kitty or by a resident from their own
 * account. A resident who paid the provider is paid back by the kitty out of
 * what the others paid in.
 *
 * Three tabs, as Housing has them: the month's settlement, the items, and the
 * subscription with its deposit. The open tab is kept in the address
 * (`?tab=items`), so a link or a reload lands on it.
 */
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Archive,
  Check,
  Clock,
  CreditCard,
  HandCoins,
  Pencil,
  Plus,
  Receipt,
  ShieldCheck,
  Sofa,
  Store,
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
  type Tone,
} from '../../components/ui'
import { api } from '../../api/client'
import { useMutation, useResource } from '../../lib/useApi'
import { formatDate, formatInr, formatMonth, paiseToInput, todayIso } from '../../lib/format'
import type {
  FurnitureCategory,
  FurnitureConfig,
  FurnitureDepositStatus,
  FurnitureItem,
  FurnitureItemStatus,
  FurnitureMonthBill,
  FurniturePayoutStatus,
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

export const CATEGORY_LABELS: Record<FurnitureCategory, string> = {
  living_room: 'Living room',
  bedroom: 'Bedroom',
  appliance: 'Appliance',
  dining: 'Dining',
  workstation: 'Workstation',
  other: 'Other',
}

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

type PaymentMode = 'upi' | 'bank_transfer' | 'cash' | 'other'

const PAYMENT_MODES: Array<{ value: PaymentMode; label: string }> = [
  { value: 'upi', label: 'UPI' },
  { value: 'bank_transfer', label: 'Bank transfer' },
  { value: 'cash', label: 'Cash' },
  { value: 'other', label: 'Other' },
]

/** Nothing left to pay on the share: paid into the kitty, or covered by paying the provider. */
function isSettled(share: FurnitureShare): boolean {
  return share.status === 'paid' || share.status === 'funded_payout' || share.outstanding_paise <= 0
}

/** The provider's side of the month, worded as the collection table words a resident's. */
const PAYOUT: Record<FurniturePayoutStatus, { tone: Tone; label: string }> = {
  pending: { tone: 'warning', label: 'Unpaid' },
  partially_disbursed: { tone: 'warning', label: 'Part paid' },
  disbursed: { tone: 'success', label: 'Paid' },
}

/** A share of the whole to one decimal place, for display only. */
function percent(part: number, whole: number): string {
  if (whole <= 0) return '0.0%'
  const tenths = Math.round((part * 1000) / whole)
  // Rounding must never make an incomplete collection read as complete.
  return `${((part < whole ? Math.min(tenths, 999) : tenths) / 10).toFixed(1)}%`
}

/** The provider's billing day in `month`, pulled back to the month's last day. */
function billingDate(month: string, day: number): string {
  const [year, mon] = month.split('-').map(Number)
  const last = new Date(Date.UTC(year, mon, 0)).getUTCDate()
  return `${month}-${String(Math.min(day, last)).padStart(2, '0')}`
}

/** Every live provider: from the backend, or, for an older response, the one there was. */
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
  | { kind: 'add' }
  | { kind: 'edit'; item: FurnitureItem }
  | { kind: 'return'; item: FurnitureItem }
  | { kind: 'remove'; item: FurnitureItem }
  | { kind: 'generate'; bill: FurnitureMonthBill }
  | { kind: 'provider_payout'; bill: FurnitureMonthBill; payout: FurnitureProviderPayout }
  | { kind: 'payout_statement'; payout: FurnitureProviderPayout }
  | { kind: 'reimburse'; reimbursement: FurnitureReimbursement }
  | { kind: 'share_detail'; share: FurnitureShare; bill: FurnitureMonthBill }
  | { kind: 'pay_share'; share: FurnitureShare }

export default function FurnitureRentalPage() {
  const { month } = useApp()
  return <FurnitureRental key={month} month={month} />
}

export function FurnitureRental({ month }: { month: string }) {
  const overview = useResource<FurnitureRentalOverview>(BASE, { month })
  const [dialog, setDialog] = useState<Dialog | null>(null)
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
                    <Metrics bill={data.month} billingDay={data.service.billing_day_of_month} />
                    {/* The month's two steps, in order: collect from residents, then pay the provider. */}
                    <ResidentCollectionPanel
                      month={month}
                      bill={data.month}
                      onGenerate={(bill) => setDialog({ kind: 'generate', bill })}
                      onViewShare={(share) => setDialog({ kind: 'share_detail', share, bill: data.month! })}
                      onPayShare={(share) => setDialog({ kind: 'pay_share', share })}
                    />
                    <ProviderPayoutPanel
                      bill={data.month}
                      payouts={payoutsOf(data.month, data.service, data.config)}
                      onPayout={(payout) => setDialog({ kind: 'provider_payout', bill: data.month!, payout })}
                      onViewStatement={(payout) => setDialog({ kind: 'payout_statement', payout })}
                    />
                    {(data.month.reimbursements ?? (data.month.reimbursement ? [data.month.reimbursement] : [])).map(
                      (reimbursement) => (
                        <ReimbursementPanel
                          key={reimbursement.recipient_resident_id}
                          month={month}
                          reimbursement={reimbursement}
                          provider={servicesOf(data)
                            .map((entry) => entry.config.provider_name)
                            .join(', ')}
                          onReimburse={(owed) => setDialog({ kind: 'reimburse', reimbursement: owed })}
                        />
                      ),
                    )}
                  </>
                )}
                {tab === 'items' && (
                  <ItemsPanel
                    items={data.items}
                    onAdd={() => setDialog({ kind: 'add' })}
                    onEdit={(item) => setDialog({ kind: 'edit', item })}
                    onReturn={(item) => setDialog({ kind: 'return', item })}
                  />
                )}
                {tab === 'subscription' && (
                  <>
                    <div className="flex justify-end">
                      <button
                        type="button"
                        className={BUTTON_SECONDARY}
                        onClick={() => setDialog({ kind: 'setup', entry: null, adding: true })}
                      >
                        <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                        Add rental provider
                      </button>
                    </div>
                    {servicesOf(data).map((entry, _, all) => (
                      <SubscriptionPanel
                        key={entry.service.service_id}
                        entry={entry}
                        several={all.length > 1}
                        onEdit={() => setDialog({ kind: 'setup', entry })}
                        onRetire={() => setDialog({ kind: 'retire', entry })}
                      />
                    ))}
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
          {dialog?.kind === 'generate' && (
            <GenerateDialog
              month={month}
              bill={dialog.bill}
              onClose={() => setDialog(null)}
              onDone={() =>
                done(`${formatMonth(month)} furniture bill recorded`, formatInr(dialog.bill.total_paise))
              }
            />
          )}
          {dialog?.kind === 'provider_payout' && (
            <ProviderPayoutModal
              month={month}
              bill={dialog.bill}
              payout={dialog.payout}
              onClose={() => setDialog(null)}
              onDone={(updated) => {
                const payout = dialog.payout
                const now = updated.provider_payouts?.find((row) => row.service_id === payout.service_id)
                const left = now ? now.remaining_disbursement_paise : updated.remaining_disbursement_paise
                const part = now ? now.payout_status === 'partially_disbursed' : updated.payout_status === 'partially_disbursed'
                done(
                  'Provider payout recorded',
                  part ? `${formatInr(left)} is still owed to ${payout.provider_name}.` : undefined,
                )
              }}
            />
          )}
          {dialog?.kind === 'payout_statement' && (
            <ProviderPayoutStatementModal month={month} payout={dialog.payout} onClose={() => setDialog(null)} />
          )}
          {dialog?.kind === 'share_detail' && data.config && (
            <FurnitureStatementModal
              month={month}
              share={dialog.share}
              bill={dialog.bill}
              provider={data.config.provider_name}
              onClose={() => setDialog(null)}
            />
          )}
          {dialog?.kind === 'pay_share' && (
            <RecordFurniturePaymentDialog
              month={month}
              share={dialog.share}
              onClose={() => setDialog(null)}
              onDone={(amount) =>
                done('Payment recorded', `${formatInr(amount)} from ${dialog.share.display_name} into the House Kitty`)
              }
            />
          )}
          {dialog?.kind === 'reimburse' && (
            <ReimburseDialog
              month={month}
              reimbursement={dialog.reimbursement}
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

function SubscriptionPanel({
  entry,
  several,
  onEdit,
  onRetire,
}: {
  entry: FurnitureServiceEntry
  several: boolean
  onEdit: () => void
  onRetire: () => void
}) {
  const { service, config } = entry
  const reference = [config.customer_id && `Customer ${config.customer_id}`, config.subscription_reference]
    .filter(Boolean)
    .join(' · ')
  return (
    <Panel
      title={several ? `${service.display_name} · ${config.provider_name}` : service.display_name}
      icon={<Sofa className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="The subscription, what it costs a month, and the deposit the provider holds."
      menuActions={[
        { label: 'Edit subscription', icon: Pencil, onClick: onEdit },
        { label: 'End subscription', icon: Archive, onClick: onRetire, tone: 'danger' },
      ]}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
        <MiniStat label="Provider" value={config.provider_name} note={reference || 'No customer ID on file'} />
        <MiniStat
          label="Monthly total"
          value={<Rupees paise={entry.monthly_total_paise} />}
          note={`${entry.active_item_count} item${entry.active_item_count === 1 ? '' : 's'} in the flat`}
        />
        <MiniStat
          label="Next billing"
          value={<span className="tabular">{entry.next_billing_date ? formatDate(entry.next_billing_date) : '—'}</span>}
          note={service.billing_day_of_month ? `Day ${service.billing_day_of_month} each month` : 'No billing day set'}
        />
        <MiniStat
          label="Deposit held"
          value={<Rupees paise={entry.deposit_held_paise} />}
          note={`${DEPOSIT_STATUS[config.deposit_status]} · ${formatInr(config.deposit_paid_paise)} paid`}
        />
      </div>
    </Panel>
  )
}

/* ---------------------------------------------------------- 2. the items -- */

function ItemsPanel({
  items,
  onAdd,
  onEdit,
  onReturn,
}: {
  items: FurnitureItem[]
  onAdd: () => void
  onEdit: (item: FurnitureItem) => void
  onReturn: (item: FurnitureItem) => void
}) {
  return (
    <Panel
      title="Rented Items"
      hint="Shared items are split between everyone; a person's own items are theirs alone."
      menuActions={[{ label: 'Add item', icon: Plus, onClick: onAdd }]}
    >
      {items.length === 0 ? (
        <EmptyBlock title="No items yet" body="Add each item on the subscription, with what it costs a month." />
      ) : (
        <div className="overflow-x-auto min-w-0">
          <table className="w-full text-xs min-w-[800px] border-collapse">
            <caption className="sr-only">Rented items</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Item</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Provider</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Category</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Paid by</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Monthly rent</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Status</th>
                <th scope="col" className="pb-3 font-bold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {items.map((item) => {
                const status = ITEM_STATUS[item.status]
                return (
                  <tr key={item.item_id} className="align-middle">
                    <th scope="row" className="py-3 pr-3 text-left font-semibold text-[var(--text-primary)]">
                      {item.name}
                    </th>
                    <td className="py-3 pr-3 text-center">{item.provider_name ?? '—'}</td>
                    <td className="py-3 pr-3 text-center">{CATEGORY_LABELS[item.category]}</td>
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
                      <span className="block mt-1 text-[11px] text-[var(--text-tertiary)] tabular">
                        {item.status === 'returned'
                          ? `On ${formatDate(item.return_date)}`
                          : item.delivery_date
                            ? `${item.status === 'pending_delivery' ? 'Due' : 'Since'} ${formatDate(item.delivery_date)}`
                            : ''}
                      </span>
                    </td>
                    <td className="py-3 text-center whitespace-nowrap">
                      <span className="inline-flex items-center justify-center gap-2">
                        <button
                          type="button"
                          className={BUTTON_QUIET}
                          onClick={() => onEdit(item)}
                          aria-label={`Edit ${item.name}`}
                        >
                          <Pencil className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                          Edit
                        </button>
                        {item.status !== 'returned' && (
                          <button
                            type="button"
                            className={BUTTON_QUIET}
                            onClick={() => onReturn(item)}
                            aria-label={`Return ${item.name}`}
                          >
                            <Undo2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                            Return
                          </button>
                        )}
                      </span>
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

/* ------------------------------------------------- 3. the month's bill -- */

/** The month at a glance: the split, what the kitty has collected, and whether the provider is paid. */
function Metrics({ bill, billingDay }: { bill: FurnitureMonthBill; billingDay: number | null }) {
  const billed = bill.expense != null
  const charged = bill.shares.filter((share) => share.total_paise > 0).length
  const lastPaid = bill.disbursements.length ? bill.disbursements[bill.disbursements.length - 1].disbursed_at : null
  const due = billingDay ? billingDate(bill.settlement_month, billingDay) : null
  const payout = PAYOUT[bill.payout_status]
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
      <MiniStat label="Shared items" value={<Rupees paise={bill.common_total_paise} />} note="Split equally" />
      <MiniStat
        label="Each person's part"
        value={<Rupees paise={bill.participant_count ? bill.base_share_paise : null} />}
        note={
          bill.participant_count
            ? `${bill.participant_count} taking part${bill.remainder_paise ? ` · ${bill.remainder_paise} paise over` : ''}`
            : 'Nobody taking part'
        }
      />
      <MiniStat
        label="Internal Collection"
        value={<Rupees paise={billed ? bill.collected_paise : null} />}
        note={
          billed
            ? `${bill.collected_count} of ${charged} covered · ${percent(bill.collected_paise, bill.expense!.amount_paise)}`
            : 'Starts once the bill is recorded'
        }
      />
      <MiniStat
        label="Provider Payout"
        value={
          billed ? (
            <Pill tone={payout.tone}>
              {bill.payout_status === 'disbursed' ? (
                <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
              ) : (
                <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
              )}
              {payout.label}
            </Pill>
          ) : (
            <span className="text-[var(--text-tertiary)]">—</span>
          )
        }
        note={
          bill.payout_status === 'disbursed' && lastPaid
            ? `Paid ${formatDate(lastPaid)}`
            : bill.payout_status === 'partially_disbursed'
              ? `${formatInr(bill.remaining_disbursement_paise)} still to pay`
              : due
                ? `Due ${formatDate(due)}`
                : 'No billing day set'
        }
      />
    </div>
  )
}

function ResidentCollectionPanel({
  month,
  bill,
  onGenerate,
  onViewShare,
  onPayShare,
}: {
  month: string
  bill: FurnitureMonthBill
  onGenerate: (bill: FurnitureMonthBill) => void
  onViewShare: (share: FurnitureShare) => void
  onPayShare: (share: FurnitureShare) => void
}) {
  const finalized = bill.statement_status != null && bill.statement_status !== 'draft'
  // Who has paid what means something only once the bill is recorded.
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
      shareNote:
        share.private_paise > 0 ? (
          <>
            incl. <Rupees paise={share.private_paise} /> own
          </>
        ) : undefined,
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
      title={`Resident Collection · ${formatMonth(month)}`}
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

      {!billed && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          {finalized ? (
            <span className="text-xs text-[var(--text-secondary)]">
              The {formatMonth(month)} statement has been finalized, so this month can no longer be billed.
            </span>
          ) : (
            <>
              <span className="text-xs text-[var(--text-secondary)]">Not billed yet.</span>
              <button
                type="button"
                className={BUTTON_PRIMARY}
                disabled={!bill.can_generate}
                onClick={() => onGenerate(bill)}
                aria-label={`Record ${formatMonth(month)} bill`}
              >
                Record {formatMonth(month)} bill
              </button>
            </>
          )}
        </div>
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
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  const single = payouts.length === 1 ? payouts[0].provider_name : null
  return (
    <Panel
      title={single ? `Provider Payout · ${single}` : 'Provider Payout'}
      icon={<Store className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={
        single
          ? `What is owed to ${single}, and payouts made to them. Select the row for disbursement history.`
          : 'What each provider is owed for the month, and payouts made to them. Select a row for its disbursement history.'
      }
    >
      {/* Laid out as the collection table above: what was due, what has been paid, what is still owed. */}
      <div className="overflow-x-auto min-w-0">
        <table className="w-full table-fixed text-left text-xs min-w-[640px] border-collapse">
          <caption className="sr-only">
            {single ? `The furniture bill owed to ${single}` : 'The furniture bill owed to each provider'}
          </caption>
          <thead>
            <tr className={TABLE_HEAD}>
              <th scope="col" className="w-[28%] pb-3 pr-3 font-bold text-left">Provider / Subscription</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Total Bill</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Paid to Provider</th>
              <th scope="col" className="w-[14%] pb-3 pr-3 font-bold text-center">Still Owed</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Status</th>
              <th scope="col" className="w-[15%] pb-3 pr-3 font-bold text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
            {payouts.map((payout) => {
              const provider = payout.provider_name
              const status = PAYOUT[payout.payout_status]
              // Open until the provider has been paid in full: the bill can go in tranches.
              const owing = billed && payout.payout_status !== 'disbursed' && payout.total_paise > 0
              const subscription = [
                payout.customer_id && `Customer ${payout.customer_id}`,
                payout.subscription_reference,
                `${payout.item_count} item${payout.item_count === 1 ? '' : 's'}`,
              ]
                .filter(Boolean)
                .join(' · ')
              return (
                // Once the bill is recorded, the row opens the provider's payout
                // statement; the button in it does its own thing instead.
                <tr
                  key={payout.service_id}
                  className={
                    billed
                      ? 'align-middle cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors'
                      : 'align-middle'
                  }
                  {...(billed && {
                    tabIndex: 0,
                    title: `View the ${provider} payout statement`,
                    onClick: () => onViewStatement(payout),
                    onKeyDown: (event: React.KeyboardEvent<HTMLTableRowElement>) => {
                      if (event.target !== event.currentTarget) return
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        onViewStatement(payout)
                      }
                    },
                  })}
                >
                  <th scope="row" className="py-3 pr-3 text-left font-normal">
                    <span className="flex items-center gap-2.5 min-w-0">
                      <Avatar name={provider} />
                      <span className="min-w-0">
                        <span className="block font-semibold text-[var(--text-primary)] truncate">{provider}</span>
                        <span className="block text-[11px] text-[var(--text-tertiary)] tabular truncate" title={subscription}>
                          {subscription}
                        </span>
                      </span>
                    </span>
                  </th>
                  <td className="py-3 pr-3 text-center font-bold text-[var(--text-primary)]">
                    <Rupees paise={payout.total_paise} />
                  </td>
                  <td className="py-3 pr-3 text-center">{billed ? <Rupees paise={payout.disbursed_paise} /> : dash}</td>
                  <td className="py-3 pr-3 text-center">
                    {billed ? <Rupees paise={payout.remaining_disbursement_paise} /> : dash}
                  </td>
                  <td className="py-3 pr-3 text-center whitespace-nowrap">
                    {billed ? (
                      <Pill tone={status.tone}>
                        {payout.payout_status === 'disbursed' ? (
                          <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
                        ) : (
                          <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
                        )}
                        {status.label}
                      </Pill>
                    ) : (
                      <Pill>Not billed</Pill>
                    )}
                  </td>
                  <td className="py-3 text-center whitespace-nowrap">
                    {/* Always there, as on each resident's row; off until the bill is
                        recorded and once the provider is paid in full. */}
                    <button
                      type="button"
                      className={BUTTON_SECONDARY}
                      disabled={!owing}
                      onClick={(event) => {
                        event.stopPropagation()
                        onPayout(payout)
                      }}
                      aria-label={`Pay now to ${provider}`}
                    >
                      <CreditCard className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                      Pay Now
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
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

function ReimbursementPanel({
  month,
  reimbursement,
  provider,
  onReimburse,
}: {
  month: string
  reimbursement: FurnitureReimbursement
  provider: string
  onReimburse: (reimbursement: FurnitureReimbursement) => void
}) {
  const owed = reimbursement
  const name = owed.recipient_name
  const settled = owed.pending_reimbursement_paise === 0
  // What of the total they paid was their own share, paid through the kitty.
  const viaKitty = owed.total_fronted_paise - owed.net_reimbursement_owed_paise
  return (
    <Panel
      title={`Reimbursement · ${name}`}
      icon={<HandCoins className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={`${name} paid extra beyond their allotted share for the ${formatMonth(month)} bill. The House Kitty reimburses the extra amount out of what the other flatmates pay in.`}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
        <MiniStat
          label="Total paid"
          value={<Rupees paise={owed.total_fronted_paise} />}
          note={
            viaKitty > 0 ? `Includes ${formatInr(viaKitty)} allotted share` : `All of it beyond their share, to ${provider}`
          }
        />
        <MiniStat
          label="Extra (reimbursable)"
          value={<Rupees paise={owed.net_reimbursement_owed_paise} />}
          note="To be reimbursed via House Kitty"
        />
        <MiniStat
          label="Reimbursed so far"
          value={<Rupees paise={owed.reimbursed_so_far_paise} />}
          note={settled ? 'Nothing still owed' : `${formatInr(owed.pending_reimbursement_paise)} still owed`}
        />
        <MiniStat
          label="Kitty cash available"
          value={<Rupees paise={owed.kitty_cash_available_paise} />}
          note="Collected from other flatmates"
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        {settled ? (
          <span className="text-xs text-[var(--text-secondary)]">
            <Pill tone="success">
              <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
              Reimbursed
            </Pill>{' '}
            {name} has been paid back in full.
          </span>
        ) : (
          <>
            <span className="text-xs text-[var(--text-secondary)]">
              {owed.can_reimburse
                ? `The kitty can pay back ${formatInr(owed.reimbursable_paise)} now.`
                : 'Nothing paid into the kitty for this bill is waiting to go back yet.'}
            </span>
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={!owed.can_reimburse}
              onClick={() => onReimburse(owed)}
            >
              {owed.can_reimburse
                ? `Reimburse ${formatInr(owed.reimbursable_paise)} from Kitty to ${name}`
                : `Reimburse from Kitty to ${name}`}
            </button>
          </>
        )}
      </div>
    </Panel>
  )
}

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
  const [category, setCategory] = useState<FurnitureCategory>(item?.category ?? 'living_room')
  const [rent, setRent] = useState(item ? paiseToInput(item.monthly_rent_paise) : '')
  const [rentPaise, setRentPaise] = useState(item?.monthly_rent_paise ?? 0)
  const [rentOk, setRentOk] = useState(Boolean(item))
  // Who pays: '' for everyone, else the resident the item is for.
  const [payer, setPayer] = useState(item?.assigned_resident_id ?? '')
  const [status, setStatus] = useState<'active' | 'pending_delivery'>(
    item?.status === 'pending_delivery' ? 'pending_delivery' : 'active',
  )
  const [delivery, setDelivery] = useState(item?.delivery_date ?? '')

  const people = (residents.data?.items ?? []).filter(
    (resident) => resident.is_active || resident.resident_id === item?.assigned_resident_id,
  )
  const valid = Boolean(serviceId) && name.trim().length > 0 && rentOk && rentPaise > 0
  const body = {
    service_id: serviceId || null,
    name: name.trim(),
    category,
    monthly_rent_paise: rentPaise,
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
        <SelectField
          label="Rental provider"
          required
          value={serviceId}
          options={services.map((entry) => ({ value: entry.service.service_id, label: entry.config.provider_name }))}
          onChange={setServiceId}
          hint="The subscription it is rented on."
        />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <TextField label="Item" required value={name} onChange={setName} maxLength={200} placeholder="e.g. 3-seater sofa" />
          <SelectField<FurnitureCategory>
            label="Category"
            value={category}
            options={(Object.keys(CATEGORY_LABELS) as FurnitureCategory[]).map((value) => ({
              value,
              label: CATEGORY_LABELS[value],
            }))}
            onChange={setCategory}
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Monthly rent"
            required
            value={rent}
            onChange={(raw, parsed) => {
              setRent(raw)
              setRentPaise(parsed.paise)
              setRentOk(parsed.ok)
            }}
          />
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
            <DateField label={status === 'pending_delivery' ? 'Expected delivery' : 'Delivered on'} value={delivery} onChange={setDelivery} />
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
          Bills already recorded keep the items behind them in their notes.
        </p>
      </form>
    </Modal>
  )
}

function GenerateDialog({
  month,
  bill,
  onClose,
  onDone,
}: {
  month: string
  bill: FurnitureMonthBill
  onClose: () => void
  onDone: () => void
}) {
  const generate = useMutation(() => api.post(`${BASE}/generate-cycle`, { settlement_month: month }), {
    onSuccess: onDone,
  })
  return (
    <Modal
      title={`Record the ${formatMonth(month)} bill`}
      description={`${formatInr(bill.total_paise)} as one expense for ${formatMonth(month)}, charged as shown. A month is billed once.`}
      onClose={onClose}
      footer={
        <DialogFooter onClose={onClose} pending={generate.pending} label="Record bill" form="furniture-generate" />
      }
    >
      <form id="furniture-generate" className="stack" onSubmit={submitWith(() => void generate.run(undefined))}>
        <ErrorNotice error={generate.error} />
        <ul className="m-0 p-0 list-none space-y-1.5 text-sm">
          {bill.shares.map((share) => (
            <li key={share.resident_id} className="flex justify-between gap-3">
              <span>{share.display_name}</span>
              <Rupees paise={share.total_paise} />
            </li>
          ))}
        </ul>
      </form>
    </Modal>
  )
}

type PayoutSource = 'pool' | 'resident_advance'

function ProviderPayoutModal({
  month,
  bill,
  payout,
  onClose,
  onDone,
}: {
  month: string
  bill: FurnitureMonthBill
  payout: FurnitureProviderPayout
  onClose: () => void
  onDone: (bill: FurnitureMonthBill) => void
}) {
  const residents = useResource<{ items: Resident[] }>('/api/residents')
  const provider = payout.provider_name
  const total = bill.expense!.amount_paise
  const remaining = payout.remaining_disbursement_paise
  // The provider bills on a fixed day and flatmates pay when they can, so a
  // first payout offers what the kitty has collected so far; after that, the
  // rest. With several providers, what was collected is not any one's.
  const single = (bill.provider_payouts?.length ?? 1) <= 1
  const fromCollection =
    single && bill.disbursements.length === 0 && bill.collected_paise > 0 && bill.collected_paise < total
  const suggested = fromCollection ? bill.collected_paise : remaining
  const [amount, setAmount] = useState(paiseToInput(suggested))
  const [amountPaise, setAmountPaise] = useState(suggested)
  const [amountOk, setAmountOk] = useState(true)
  const [source, setSource] = useState<PayoutSource>('pool')
  const [funder, setFunder] = useState('')
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const people = (residents.data?.items ?? []).filter(
    (resident) => resident.is_active || bill.shares.some((share) => share.resident_id === resident.resident_id),
  )
  const sources: Array<{ value: PayoutSource; title: string; body: string }> = [
    {
      value: 'pool',
      title: 'House Kitty',
      body: `Paid to ${provider} from the kitty's account, out of what residents have paid in.`,
    },
    {
      value: 'resident_advance',
      title: 'Paid by a resident (reimburse extra via Kitty)',
      body: 'Their allotted share is recorded through the House Kitty, and any extra amount paid beyond their share is reimbursed to them via the House Kitty.',
    },
  ]

  const record = useMutation(
    () =>
      api.post<FurnitureRentalOverview>(`${BASE}/disburse-payout`, {
        settlement_month: month,
        service_id: payout.service_id,
        disbursement_mode: source,
        funding_resident_id: source === 'resident_advance' ? funder : null,
        reference: reference.trim(),
        disbursed_date: date,
        amount_paise: amountPaise,
        notes: notes.trim() || null,
      }),
    { onSuccess: (updated) => onDone(updated.month!) },
  )
  const tooMuch = amountOk && amountPaise > remaining
  const amountValid = amountOk && amountPaise > 0 && amountPaise <= remaining
  const valid =
    amountValid && Boolean(date) && reference.trim().length > 0 && (source === 'pool' || Boolean(funder))

  return (
    <Modal
      title="Record Provider Payout"
      description={`${formatMonth(month)} · ${formatInr(remaining)} still owed to ${provider}`}
      onClose={onClose}
      footer={
        <DialogFooter onClose={onClose} pending={record.pending} disabled={!valid} label="Record Payout" form="furniture-provider-payout" />
      }
    >
      <form id="furniture-provider-payout" className="stack" noValidate onSubmit={submitWith(() => valid && void record.run(undefined))}>
        <ErrorNotice error={record.error} />
        <fieldset role="radiogroup" className="m-0 border-0 p-0 space-y-2">
          <legend className="p-0 pb-1 text-sm font-bold text-[var(--text-primary)]">Paid from</legend>
          {sources.map((option) => (
            <label
              key={option.value}
              className={
                source === option.value
                  ? 'flex items-start gap-3 rounded-xl border border-[var(--accent)] bg-[var(--accent-glow)] p-3 cursor-pointer'
                  : 'flex items-start gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 cursor-pointer'
              }
            >
              <input
                type="radio"
                name="furniture-payout-source"
                className="mt-1 shrink-0"
                value={option.value}
                checked={source === option.value}
                onChange={() => setSource(option.value)}
              />
              <span className="min-w-0">
                <span className="block text-sm font-bold text-[var(--text-primary)]">{option.title}</span>
                <span className="block text-xs text-[var(--text-secondary)]">{option.body}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {source === 'resident_advance' && (
          <SelectField
            label="Paid by"
            required
            value={funder}
            placeholder={residents.data ? 'Choose a resident' : 'Loading…'}
            options={people.map((resident) => ({ value: resident.resident_id, label: resident.display_name }))}
            onChange={setFunder}
          />
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount"
            required
            value={amount}
            error={tooMuch ? `More than the ${formatInr(remaining)} still owed to ${provider}.` : null}
            hint={
              fromCollection
                ? `Pre-filled with what the kitty has collected. You can pay any amount up to ${formatInr(remaining)}.`
                : `Pre-filled with the balance still owed. You can pay any amount up to ${formatInr(remaining)}.`
            }
            onChange={(raw, parsed) => {
              setAmount(raw)
              setAmountPaise(parsed.paise)
              setAmountOk(parsed.ok)
            }}
          />
          <DateField label="Payment date" required value={date} onChange={setDate} />
        </div>
        <TextField
          label="Reference / UTR"
          required
          value={reference}
          onChange={setReference}
          maxLength={200}
          placeholder="e.g. UPI ref or bank UTR"
        />
        <TextField label="Note" value={notes} onChange={setNotes} maxLength={500} />
      </form>
    </Modal>
  )
}

/** The provider's side of the month: what is due to them, and every payment made (Rule U7). */
function ProviderPayoutStatementModal({
  month,
  payout,
  onClose,
}: {
  month: string
  payout: FurnitureProviderPayout
  onClose: () => void
}) {
  const provider = payout.provider_name
  const status = PAYOUT[payout.payout_status]
  const dash = <span className="text-[var(--text-tertiary)]">—</span>
  return (
    <Modal title="Provider Payout Statement" description={`${provider} · ${formatMonth(month)}`} onClose={onClose}>
      <div className="stack">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <MiniStat label="Total Bill" value={<Rupees paise={payout.total_paise} />} />
          <MiniStat label="Paid to Provider" value={<Rupees paise={payout.disbursed_paise} />} />
          <MiniStat label="Still Owed" value={<Rupees paise={payout.remaining_disbursement_paise} />} />
        </div>

        <section aria-label="Disbursement history" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-bold text-[var(--text-primary)]">Disbursement History</h3>
            <Pill tone={status.tone}>
              {payout.payout_status === 'disbursed' ? (
                <Check className="w-3 h-3 shrink-0" aria-hidden="true" />
              ) : (
                <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />
              )}
              {status.label}
            </Pill>
          </div>
          {payout.disbursements.length === 0 ? (
            <EmptyBlock title="No payouts recorded yet for this month." />
          ) : (
            <div className="overflow-x-auto min-w-0">
              <table className="w-full text-left text-xs min-w-[560px] border-collapse">
                <caption className="sr-only">Payments to {provider}</caption>
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Date</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Amount</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Funded From</th>
                    <th scope="col" className="pb-3 pr-3 font-bold text-center">Reference / UTR</th>
                    <th scope="col" className="pb-3 font-bold text-center">Note</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                  {payout.disbursements.map((tranche) => (
                    <tr key={tranche.disbursement_id} className="align-middle">
                      <td className="py-2.5 pr-3 text-center tabular">{formatDate(tranche.disbursed_at)}</td>
                      <td className="py-2.5 pr-3 text-center font-bold text-[var(--text-primary)]">
                        <Rupees paise={tranche.amount_paise} />
                      </td>
                      <td className="py-2.5 pr-3 text-center">
                        {tranche.disbursed_by === 'pool'
                          ? 'House Kitty'
                          : `${tranche.funding_resident_name ?? tranche.funding_resident_id} (advance)`}
                      </td>
                      <td className="py-2.5 pr-3 text-center tabular break-all">{tranche.disbursement_reference}</td>
                      <td className="py-2.5 text-center text-[var(--text-secondary)]">{tranche.note ?? dash}</td>
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

function ReimburseDialog({
  month,
  reimbursement,
  onClose,
  onDone,
}: {
  month: string
  reimbursement: FurnitureReimbursement
  onClose: () => void
  onDone: (amount: number) => void
}) {
  const [date, setDate] = useState(todayIso())
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const name = reimbursement.recipient_name
  // What the kitty holds for the bill now; the rest waits for the others to pay in.
  const amount = reimbursement.reimbursable_paise
  const later = reimbursement.pending_reimbursement_paise - amount
  const save = useMutation(
    () =>
      api.post<FurnitureRentalOverview>(`${BASE}/reimburse-from-kitty`, {
        settlement_month: month,
        recipient_resident_id: reimbursement.recipient_resident_id,
        amount_paise: amount,
        payment_date: date,
        reference: reference.trim() || null,
        notes: notes.trim() || null,
      }),
    { onSuccess: () => onDone(amount) },
  )
  return (
    <Modal
      title={`Reimburse ${name}`}
      description={`${formatInr(amount)} from the kitty to ${name}, out of what flatmates paid in for the ${formatMonth(month)} bill.${later > 0 ? ` ${formatInr(later)} more is owed once the rest have paid.` : ''}`}
      onClose={onClose}
      footer={
        <DialogFooter
          onClose={onClose}
          pending={save.pending}
          disabled={!date}
          label="Record reimbursement"
          form="furniture-reimburse"
        />
      }
    >
      <form id="furniture-reimburse" className="stack" noValidate onSubmit={submitWith(() => Boolean(date) && void save.run(undefined))}>
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <DateField label="Paid on" required value={date} onChange={setDate} />
          <TextField
            label="Reference / UTR"
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="e.g. UPI ref or bank UTR"
          />
        </div>
        <TextField label="Note" value={notes} onChange={setNotes} maxLength={500} />
      </form>
    </Modal>
  )
}

/** One resident's share of the month: what they owe, and every payment into the kitty towards it (Rule U7). */
function FurnitureStatementModal({
  month,
  share,
  bill,
  provider,
  onClose,
}: {
  month: string
  share: FurnitureShare
  bill: FurnitureMonthBill
  provider: string
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
                    <th scope="col" className="pb-3 font-bold text-center">Reference / UTR</th>
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
                      <td className="py-2.5 text-center tabular break-all text-[var(--text-primary)]">
                        {payment.reference || dash}
                      </td>
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

function RecordFurniturePaymentDialog({
  month,
  share,
  onClose,
  onDone,
}: {
  month: string
  share: FurnitureShare
  onClose: () => void
  onDone: (amount: number) => void
}) {
  const owed = share.outstanding_paise
  const [amount, setAmount] = useState(paiseToInput(owed))
  const [amountPaise, setAmountPaise] = useState(owed)
  const [amountOk, setAmountOk] = useState(true)
  const [date, setDate] = useState(todayIso())
  const [mode, setMode] = useState<PaymentMode>('upi')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')

  const save = useMutation(
    () =>
      api.post<FurnitureRentalOverview>(`${BASE}/record-payment`, {
        settlement_month: month,
        resident_id: share.resident_id,
        amount_paise: amountPaise,
        payment_date: date,
        payment_mode: mode,
        reference: reference.trim() || null,
        note: note.trim() || null,
      }),
    { onSuccess: () => onDone(amountPaise) },
  )
  // A bank or UPI payment carries its transaction ID; cash has none.
  const needsReference = mode !== 'cash'
  const valid = amountOk && amountPaise > 0 && Boolean(date) && (!needsReference || Boolean(reference.trim()))

  return (
    <Modal
      title="Record Payment into House Kitty"
      description={`${share.display_name} · ${formatMonth(month)} furniture rental share · ${formatInr(owed)} outstanding`}
      onClose={onClose}
      footer={
        <DialogFooter onClose={onClose} pending={save.pending} disabled={!valid} label="Record Payment" form="furniture-pay" />
      }
    >
      <form id="furniture-pay" className="stack" noValidate onSubmit={submitWith(() => valid && void save.run(undefined))}>
        <ErrorNotice error={save.error} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <MoneyInput
            label="Amount"
            required
            value={amount}
            hint={
              amountOk && amountPaise > owed
                ? `${formatInr(amountPaise - owed)} more than this month's share. The extra goes to ${share.display_name}'s other charges, oldest first.`
                : amountOk && amountPaise > 0 && amountPaise < owed
                  ? `Part payment: ${formatInr(owed - amountPaise)} of this month's share will still be owed.`
                  : `Pre-filled with the outstanding share. It is applied to the ${formatMonth(month)} furniture bill first.`
            }
            onChange={(raw, parsed) => {
              setAmount(raw)
              setAmountPaise(parsed.paise)
              setAmountOk(parsed.ok)
            }}
          />
          <DateField label="Payment date" required value={date} onChange={setDate} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
          <SelectField<PaymentMode> label="Payment mode" value={mode} options={PAYMENT_MODES} onChange={setMode} />
          <TextField
            label="Reference / UTR"
            required={needsReference}
            value={reference}
            onChange={setReference}
            maxLength={200}
            placeholder="e.g. UPI ref or bank UTR"
          />
        </div>
        <TextField
          label="Note"
          value={note}
          onChange={setNote}
          maxLength={500}
          placeholder="Optional remark (e.g. GPay transfer)"
        />
      </form>
    </Modal>
  )
}
