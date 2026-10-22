/**
 * Furniture rental (Furlenco, Rentomojo): the subscription, the items rented,
 * and what each month bills.
 *
 * Every figure comes from the backend. Shared items are split equally between
 * the residents taking part and a resident's own items are charged to them;
 * nothing is prorated, so an item in the flat at any point in a month counts in
 * full. Recording a month's bill writes one ordinary expense.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Archive, Pencil, Plus, Receipt, Sofa, Undo2 } from 'lucide-react'
import { Page, useApp } from '../../components/Layout'
import { AsyncSection, ErrorNotice, Modal } from '../../components/Primitives'
import { DateField, MoneyInput, SelectField, TextField } from '../../components/Form'
import { useToast } from '../../components/Toast'
import {
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
  FurnitureDepositStatus,
  FurnitureItem,
  FurnitureItemStatus,
  FurnitureMonthBill,
  FurnitureRentalOverview,
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

type Dialog =
  | { kind: 'setup' }
  | { kind: 'retire' }
  | { kind: 'add' }
  | { kind: 'edit'; item: FurnitureItem }
  | { kind: 'return'; item: FurnitureItem }
  | { kind: 'remove'; item: FurnitureItem }
  | { kind: 'generate'; bill: FurnitureMonthBill }

export default function FurnitureRentalPage() {
  const { month } = useApp()
  return <FurnitureRental key={month} month={month} />
}

export function FurnitureRental({ month }: { month: string }) {
  const overview = useResource<FurnitureRentalOverview>(BASE, { month })
  const [dialog, setDialog] = useState<Dialog | null>(null)
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
                  <button type="button" className={BUTTON_PRIMARY} onClick={() => setDialog({ kind: 'setup' })}>
                    <Plus className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                    Set up furniture rental
                  </button>
                }
              />
            </Panel>
          ) : (
            <>
              <SubscriptionPanel data={data} onEdit={() => setDialog({ kind: 'setup' })} onRetire={() => setDialog({ kind: 'retire' })} />
              <ItemsPanel
                items={data.items}
                onAdd={() => setDialog({ kind: 'add' })}
                onEdit={(item) => setDialog({ kind: 'edit', item })}
                onReturn={(item) => setDialog({ kind: 'return', item })}
              />
              {data.month && (
                <BillPanel
                  month={month}
                  bill={data.month}
                  onGenerate={(bill) => setDialog({ kind: 'generate', bill })}
                />
              )}
            </>
          )}

          {dialog?.kind === 'setup' && (
            <SetupDialog
              data={data}
              month={month}
              onClose={() => setDialog(null)}
              onSaved={() => done(data.service ? 'Subscription saved' : 'Furniture rental set up')}
            />
          )}
          {dialog?.kind === 'retire' && (
            <RetireDialog
              month={month}
              onClose={() => setDialog(null)}
              onDone={() => done('Furniture rental ended')}
            />
          )}
          {(dialog?.kind === 'add' || dialog?.kind === 'edit') && (
            <ItemDialog
              item={dialog.kind === 'edit' ? dialog.item : null}
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
        </Page>
      )}
    </AsyncSection>
  )
}

/* ----------------------------------------------------- 1. the subscription -- */

function SubscriptionPanel({
  data,
  onEdit,
  onRetire,
}: {
  data: FurnitureRentalOverview
  onEdit: () => void
  onRetire: () => void
}) {
  const service = data.service!
  const config = data.config!
  const reference = [config.customer_id && `Customer ${config.customer_id}`, config.subscription_reference]
    .filter(Boolean)
    .join(' · ')
  return (
    <Panel
      title={service.display_name}
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
          value={<Rupees paise={data.monthly_total_paise} />}
          note={`${data.active_item_count} item${data.active_item_count === 1 ? '' : 's'} in the flat`}
        />
        <MiniStat
          label="Next billing"
          value={<span className="tabular">{data.next_billing_date ? formatDate(data.next_billing_date) : '—'}</span>}
          note={service.billing_day_of_month ? `Day ${service.billing_day_of_month} each month` : 'No billing day set'}
        />
        <MiniStat
          label="Deposit held"
          value={<Rupees paise={data.deposit_held_paise} />}
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
          <table className="w-full text-xs min-w-[720px] border-collapse">
            <caption className="sr-only">Rented items</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Item</th>
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

function BillPanel({
  month,
  bill,
  onGenerate,
}: {
  month: string
  bill: FurnitureMonthBill
  onGenerate: (bill: FurnitureMonthBill) => void
}) {
  const finalized = bill.statement_status != null && bill.statement_status !== 'draft'
  return (
    <Panel
      title={`Monthly Bill & Shares · ${formatMonth(month)}`}
      icon={<Receipt className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint="Shared items split equally between the residents taking part; each person's own items to them. Nothing is prorated: an item in the flat at any point in the month counts in full."
    >
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
        <MiniStat label="Own items" value={<Rupees paise={bill.private_total_paise} />} note="Charged to their owner" />
        <MiniStat label="Month total" value={<Rupees paise={bill.total_paise} />} note={`${bill.billable_items.length} items billed`} />
      </div>

      {bill.blocked_reason && !bill.expense && (
        <p className="m-0 text-xs font-medium text-[var(--warning)]" role="status">
          {bill.blocked_reason}
        </p>
      )}

      {bill.shares.length > 0 && (
        <div className="overflow-x-auto min-w-0">
          <table className="w-full text-xs min-w-[560px] border-collapse">
            <caption className="sr-only">Shares for {formatMonth(month)}</caption>
            <thead>
              <tr className={TABLE_HEAD}>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Resident</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Shared items</th>
                <th scope="col" className="pb-3 pr-3 font-bold text-center">Own items</th>
                <th scope="col" className="pb-3 font-bold text-center">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
              {bill.shares.map((share) => (
                <tr key={share.resident_id} className="align-middle">
                  <th scope="row" className="py-3 pr-3 text-left font-semibold text-[var(--text-primary)]">
                    {share.display_name}
                    {!share.taking_part && (
                      <span className="block text-[11px] font-normal text-[var(--warning)]">Not taking part</span>
                    )}
                  </th>
                  <td className="py-3 pr-3 text-center">
                    <Rupees paise={share.common_share_paise} />
                  </td>
                  <td className="py-3 pr-3 text-center">
                    <Rupees paise={share.private_paise} />
                    {share.private_items.length > 0 && (
                      <span className="block text-[11px] text-[var(--text-tertiary)]">{share.private_items.join(', ')}</span>
                    )}
                  </td>
                  <td className="py-3 text-center font-bold text-[var(--text-primary)]">
                    <Rupees paise={share.total_paise} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        {bill.expense ? (
          <>
            <span className="text-xs text-[var(--text-secondary)]">
              <Pill tone="success">Billed</Pill>{' '}
              {formatInr(bill.expense.amount_paise)} recorded as an expense for {formatMonth(month)}.
            </span>
            <Link to="/expenses" className={BUTTON_SECONDARY}>
              View in Expenses
            </Link>
          </>
        ) : finalized ? (
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
            >
              Record {formatMonth(month)} bill
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
  data,
  month,
  onClose,
  onSaved,
}: {
  data: FurnitureRentalOverview
  month: string
  onClose: () => void
  onSaved: () => void
}) {
  const config = data.config
  const known = config && PROVIDERS.includes(config.provider_name)
  const [provider, setProvider] = useState(config ? (known ? config.provider_name : 'Other') : 'Furlenco')
  const [otherProvider, setOtherProvider] = useState(config && !known ? config.provider_name : '')
  const [customerId, setCustomerId] = useState(config?.customer_id ?? '')
  const [reference, setReference] = useState(config?.subscription_reference ?? '')
  const [billingDay, setBillingDay] = useState(
    data.service?.billing_day_of_month != null ? String(data.service.billing_day_of_month) : '',
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
        },
        { month },
      ),
    { onSuccess: onSaved },
  )
  return (
    <Modal
      title={data.service ? 'Edit subscription' : 'Set up furniture rental'}
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

function RetireDialog({ month, onClose, onDone }: { month: string; onClose: () => void; onDone: () => void }) {
  const retire = useMutation(() => api.post(`${BASE}/retire`, undefined, { month }), { onSuccess: onDone })
  return (
    <Modal
      title="End furniture rental?"
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
  onClose,
  onRemove,
  onSaved,
}: {
  item: FurnitureItem | null
  onClose: () => void
  onRemove: (item: FurnitureItem) => void
  onSaved: (saved: FurnitureItem) => void
}) {
  const residents = useResource<{ items: Resident[] }>('/api/residents')
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
  const valid = name.trim().length > 0 && rentOk && rentPaise > 0
  const body = {
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
