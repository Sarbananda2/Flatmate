/**
 * A flatmate's own monthly statement, and the ways to send it.
 *
 * The household statement says what the flat spent; this says what one person
 * owes, line by line, with the message and the UPI code to settle it. Every
 * figure -- the shares, the credits, the net due, the message text and the
 * payment link -- is worked out by the backend and only displayed here.
 *
 * `StatementSlip` is the statement itself. `MonthlyStatementCard` puts it on a
 * resident's page behind a month switcher, and `FlatmateSharesPanel` lists the
 * whole flat for one month so each message can be copied in turn.
 */
import { useEffect, useState } from 'react'
import { flushSync } from 'react-dom'
import { ChevronLeft, ChevronRight, Copy, Eye, Printer, QrCode } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { AsyncSection, Modal } from '../components/Primitives'
import {
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  EmptyBlock,
  Panel,
  Pill,
  Rupees,
  StatementPill,
} from '../components/ui'
import { useToast } from '../components/Toast'
import { useResource } from '../lib/useApi'
import {
  ALLOCATION_METHOD_LABELS,
  PAYMENT_KIND_LABELS,
  formatDate,
  formatInr,
  formatMonth,
  formatMonthShort,
  shiftMonth,
} from '../lib/format'
import type { FlatmateStatementList, ResidentMonthlyStatement } from '../api/types'

const TH = 'pb-2.5 px-2 font-bold text-center'
const HEAD_ROW =
  'border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold'

const CREDIT_LABELS: Record<string, string> = {
  ...PAYMENT_KIND_LABELS,
  expense_funded: 'Paid a household expense',
  ledger_adjustment: 'Ledger adjustment',
}

export function statementPath(residentId: string, month: string): string {
  return `/api/residents/${encodeURIComponent(residentId)}/statements/${month}`
}

function netDueLabel(statement: ResidentMonthlyStatement): string {
  const net = statement.net_due_paise
  if (net > 0) return `${formatInr(net)} Owed to Pool`
  if (net < 0) return `${formatInr(-net)} Owed to ${statement.display_name}`
  return 'Settled'
}

function netDueTone(statement: ResidentMonthlyStatement): 'danger' | 'success' | 'neutral' {
  return statement.net_due_paise > 0
    ? 'danger'
    : statement.net_due_paise < 0
      ? 'success'
      : 'neutral'
}

/** Copies a flatmate's WhatsApp message and confirms it with a toast. */
export function CopyWhatsAppButton({
  statement,
  label,
  className = BUTTON_SECONDARY,
}: {
  statement: ResidentMonthlyStatement
  label: string
  className?: string
}) {
  const toast = useToast()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(statement.whatsapp_text)
      toast.success(`WhatsApp summary copied for ${statement.display_name}`)
    } catch {
      toast.failure('Could not copy', 'The browser did not allow access to the clipboard.')
    }
  }
  return (
    <button
      type="button"
      className={className}
      aria-label={`${label} for ${statement.display_name}`}
      onClick={() => void copy()}
    >
      <Copy className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
      {label}
    </button>
  )
}

function SumRow({
  label,
  children,
  total,
}: {
  label: string
  children: React.ReactNode
  total?: boolean
}) {
  return (
    <div
      className={
        total
          ? 'flex items-baseline justify-between gap-4 border-t border-[var(--border-medium)] pt-2 text-sm font-bold'
          : 'flex items-baseline justify-between gap-4'
      }
    >
      <dt className={total ? '' : 'text-[var(--text-secondary)]'}>{label}</dt>
      <dd className="m-0 tabular text-right">{children}</dd>
    </div>
  )
}

/** One resident's statement: the lines, the credits, the net due, and how to send it. */
export function StatementSlip({ statement }: { statement: ResidentMonthlyStatement }) {
  const [showQr, setShowQr] = useState(false)
  const [printing, setPrinting] = useState(false)
  const payable = statement.payable_now_paise
  const net = statement.net_due_paise

  useEffect(() => setShowQr(false), [statement.resident_id, statement.settlement_month])

  // Only this sheet goes to paper, even when another printable slip shares the page.
  const print = () => {
    flushSync(() => setPrinting(true))
    try {
      window.print()
    } finally {
      setPrinting(false)
    }
  }

  return (
    <div className="space-y-4">
      <article
        className={`${printing ? 'receipt-sheet print-target ' : ''}rounded-xl border border-[var(--border-medium)] bg-[var(--bg-surface)] p-5 space-y-4 text-[var(--text-primary)]`}
        aria-label={`${statement.display_name} statement for ${formatMonth(statement.settlement_month)}`}
      >
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-0.5 min-w-0">
            <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
              {statement.apartment_name} — {formatMonth(statement.settlement_month)} Statement
            </div>
            <div className="text-base font-bold">
              {statement.display_name}
              {statement.room_label && (
                <span className="font-normal text-[var(--text-secondary)]">
                  {' '}
                  · {statement.room_label}
                </span>
              )}
            </div>
          </div>
          <Pill tone={netDueTone(statement)} className="!text-sm !px-3.5 !py-1.5 !font-bold">
            <span className="tabular" data-testid="net-due-badge">
              {netDueLabel(statement)}
            </span>
          </Pill>
        </header>

        {statement.items.length === 0 ? (
          <p className="m-0 text-xs text-[var(--text-secondary)]">
            No charges settle in {formatMonth(statement.settlement_month)}.
          </p>
        ) : (
          <div className="overflow-x-auto min-w-0">
            <table className="w-full text-xs min-w-[560px] border-collapse">
              <caption className="sr-only">Itemized charges</caption>
              <thead>
                <tr className={HEAD_ROW}>
                  <th scope="col" className={TH}>Charge</th>
                  <th scope="col" className={TH}>Service month</th>
                  <th scope="col" className={TH}>Split</th>
                  <th scope="col" className={TH}>Whole bill</th>
                  <th scope="col" className={TH}>Their share</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)]">
                {statement.items.map((item) => (
                  <tr key={item.expense_id}>
                    <th scope="row" className="py-2.5 px-2 text-left font-semibold align-top">
                      {item.category}
                      {item.description.toLowerCase() !== item.category.toLowerCase() && (
                        <div className="font-normal text-[var(--text-secondary)]">
                          {item.description}
                        </div>
                      )}
                      {item.components.length > 1 && (
                        <div className="font-normal text-[11px] text-[var(--text-tertiary)] tabular">
                          {item.components
                            .map((c) => `${c.name} ${formatInr(c.resident_share_paise)}`)
                            .join(' + ')}
                        </div>
                      )}
                    </th>
                    <td className="py-2.5 px-2 text-center align-top whitespace-nowrap">
                      {formatMonthShort(item.service_period)}
                    </td>
                    <td className="py-2.5 px-2 text-center align-top">
                      {ALLOCATION_METHOD_LABELS[item.allocation_method] ?? item.allocation_method}
                    </td>
                    <td className="py-2.5 px-2 text-center align-top">
                      <Rupees paise={item.total_expense_paise} />
                    </td>
                    <td className="py-2.5 px-2 text-center align-top font-semibold">
                      <Rupees paise={item.resident_share_paise} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {statement.payments.length > 0 && (
          <div className="overflow-x-auto min-w-0">
            <table className="w-full text-xs min-w-[560px] border-collapse">
              <caption className="pb-2 text-left text-[10px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                Payments credited
              </caption>
              <thead>
                <tr className={HEAD_ROW}>
                  <th scope="col" className={TH}>Date</th>
                  <th scope="col" className={TH}>Credit</th>
                  <th scope="col" className={TH}>Reference</th>
                  <th scope="col" className={TH}>Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)]">
                {statement.payments.map((payment) => (
                  <tr key={`${payment.payment_kind}-${payment.payment_id}`}>
                    <td className="py-2.5 px-2 text-center whitespace-nowrap">
                      {formatDate(payment.payment_date)}
                    </td>
                    <th scope="row" className="py-2.5 px-2 text-left font-semibold">
                      {CREDIT_LABELS[payment.payment_kind] ?? payment.description}
                    </th>
                    <td className="py-2.5 px-2 text-center tabular text-[var(--text-secondary)]">
                      {payment.reference ?? '—'}
                    </td>
                    <td className="py-2.5 px-2 text-center font-semibold">
                      <Rupees paise={payment.amount_paise} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <dl className="m-0 space-y-1.5 text-xs" aria-label="Statement figures">
          <SumRow label="Total charges">
            <Rupees paise={statement.total_charges_paise} />
          </SumRow>
          {statement.brought_forward_paise !== 0 && (
            <SumRow
              label={
                statement.brought_forward_paise > 0
                  ? 'Brought forward (due)'
                  : 'Brought forward (credit)'
              }
            >
              <Rupees paise={statement.brought_forward_paise} />
            </SumRow>
          )}
          <SumRow label="Payments credited">
            <Rupees paise={-statement.total_payments_paise || 0} />
          </SumRow>
          <SumRow
            label={
              net > 0
                ? 'Net due to the pool'
                : net < 0
                  ? `Owed to ${statement.display_name}`
                  : 'Net due'
            }
            total
          >
            <Rupees paise={Math.abs(net)} tone="accent" />
          </SumRow>
        </dl>

        {net > 0 && payable !== net && (
          <p className="m-0 text-xs font-semibold text-[var(--text-secondary)]" role="status">
            {payable === 0
              ? 'Paid in full since this statement.'
              : `${formatInr(payable)} is still to pay after later payments.`}
          </p>
        )}
        {(statement.statement_status === 'draft' ||
          statement.statement_status === 'not_created') && (
          <p className="m-0 text-[11px] text-[var(--text-tertiary)]">
            Draft — these figures can still change until the month's statement is finalized.
          </p>
        )}
      </article>

      {showQr &&
        (statement.upi_uri ? (
          <section
            aria-label="Scan to pay"
            className="flex flex-wrap items-center gap-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4"
          >
            {/* A QR code is read dark-on-light, so it keeps a white tile in both themes. */}
            <div className="rounded-xl bg-white p-3 shrink-0 border border-[var(--border-subtle)]">
              <QRCodeSVG
                value={statement.upi_uri}
                size={148}
                level="M"
                role="img"
                aria-label={`UPI QR code to pay ${statement.recipient_name} ${formatInr(payable)}`}
              />
            </div>
            <div className="min-w-0 space-y-1 text-xs text-[var(--text-secondary)]">
              <div className="text-sm font-bold text-[var(--text-primary)]">
                Pay {statement.recipient_name} <Rupees paise={payable} tone="accent" />
              </div>
              <div className="tabular truncate">{statement.recipient_upi_id}</div>
              <p className="m-0">
                Scan with any UPI app. Paying does not record itself here: record the payment
                once it arrives.
              </p>
            </div>
          </section>
        ) : (
          <p className="m-0 text-xs text-[var(--text-secondary)]" role="status">
            No scan-to-pay code:{' '}
            {payable === 0
              ? 'there is nothing for them to pay.'
              : statement.recipient_name
                ? `${statement.recipient_name} has no UPI ID on file.`
                : 'no primary signatory is set to receive payments.'}
          </p>
        ))}

      <div className="flex flex-wrap items-center gap-2 print:hidden">
        <CopyWhatsAppButton statement={statement} label="Copy WhatsApp Breakdown" />
        <button
          type="button"
          className={BUTTON_SECONDARY}
          aria-expanded={showQr}
          onClick={() => setShowQr((open) => !open)}
        >
          <QrCode className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          {showQr ? 'Hide UPI Code' : 'Scan to Pay UPI'}
        </button>
        <button type="button" className={BUTTON_SECONDARY} onClick={print}>
          <Printer className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          Print / Save PDF
        </button>
      </div>
    </div>
  )
}

/** The statement card on a resident's page, with its own month switcher. */
export function MonthlyStatementCard({
  residentId,
  initialMonth,
}: {
  residentId: string
  initialMonth: string
}) {
  const [month, setMonth] = useState(initialMonth)
  const resource = useResource<ResidentMonthlyStatement>(statementPath(residentId, month))

  return (
    <Panel
      title="Monthly Statement & Sharing"
      hint="Their own share for the month, line by line, ready to send."
      actions={
        <div className="flex items-center gap-1" role="group" aria-label="Statement month">
          <button
            type="button"
            className={BUTTON_QUIET}
            aria-label="Previous month"
            onClick={() => setMonth((current) => shiftMonth(current, -1))}
          >
            <ChevronLeft className="w-4 h-4" aria-hidden="true" />
          </button>
          <span
            className="min-w-[8.5rem] text-center text-xs font-bold text-[var(--text-primary)]"
            aria-live="polite"
          >
            {formatMonth(month)}
          </span>
          <button
            type="button"
            className={BUTTON_QUIET}
            aria-label="Next month"
            onClick={() => setMonth((current) => shiftMonth(current, 1))}
          >
            <ChevronRight className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
      }
    >
      <AsyncSection resource={resource} loadingLabel="Working out the statement…">
        {(statement) => <StatementSlip statement={statement} />}
      </AsyncSection>
    </Panel>
  )
}

/** Every flatmate's share for one month, each with its message to send. */
export function FlatmateSharesPanel({ month }: { month: string }) {
  const resource = useResource<FlatmateStatementList>(`/api/statements/${month}/flatmates`)
  const [viewing, setViewing] = useState<ResidentMonthlyStatement | null>(null)

  return (
    <Panel
      title="Flatmate Shares & WhatsApp Dispatch"
      hint={`What each flatmate owes for ${formatMonth(month)}, with a message ready to send them.`}
      actions={
        resource.data ? <StatementPill status={resource.data.statement_status} /> : undefined
      }
    >
      <AsyncSection
        resource={resource}
        isEmpty={(data) => data.items.length === 0}
        empty={
          <EmptyBlock
            title="No flatmates to bill"
            body={`Nobody has a charge, a payment or a balance in ${formatMonth(month)}.`}
          />
        }
      >
        {(data) => (
          <div className="overflow-x-auto min-w-0">
            <table className="w-full text-xs min-w-[760px] border-collapse">
              <caption className="sr-only">Flatmate shares for {formatMonth(month)}</caption>
              <thead>
                <tr className={HEAD_ROW}>
                  <th scope="col" className={TH}>Resident</th>
                  <th scope="col" className={TH}>Month Charges</th>
                  <th scope="col" className={TH}>Paid / Credited</th>
                  <th scope="col" className={TH}>Net Due</th>
                  <th scope="col" className={TH}>Status</th>
                  <th scope="col" className={TH}>Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                {data.items.map((statement) => (
                  <tr
                    key={statement.resident_id}
                    className="hover:bg-[var(--bg-elevated)] transition-colors"
                  >
                    <th scope="row" className="py-3 px-2 text-left font-semibold">
                      {statement.display_name}
                      {statement.room_label && (
                        <span className="font-normal text-[var(--text-secondary)]">
                          {' '}
                          · {statement.room_label}
                        </span>
                      )}
                      {statement.brought_forward_paise !== 0 && (
                        <div className="font-normal text-[11px] text-[var(--text-tertiary)] tabular">
                          {statement.brought_forward_paise > 0
                            ? `${formatInr(statement.brought_forward_paise)} brought forward`
                            : `${formatInr(-statement.brought_forward_paise)} credit brought forward`}
                        </div>
                      )}
                    </th>
                    <td className="py-3 px-2 text-center">
                      <Rupees paise={statement.total_charges_paise} />
                    </td>
                    <td className="py-3 px-2 text-center">
                      <Rupees paise={statement.total_payments_paise} />
                    </td>
                    <td className="py-3 px-2 text-center font-bold">
                      <Rupees paise={Math.abs(statement.net_due_paise)} />
                    </td>
                    <td className="py-3 px-2 text-center">
                      <Pill tone={netDueTone(statement)}>
                        {statement.direction === 'owed_to_pool'
                          ? 'Owes the pool'
                          : statement.direction === 'owed_to_resident'
                            ? 'Owed by the pool'
                            : 'Settled'}
                      </Pill>
                    </td>
                    <td className="py-3 px-2">
                      <div className="flex items-center justify-center gap-2 whitespace-nowrap">
                        <CopyWhatsAppButton statement={statement} label="Copy WhatsApp" />
                        <button
                          type="button"
                          className={BUTTON_SECONDARY}
                          aria-label={`View statement for ${statement.display_name}`}
                          onClick={() => setViewing(statement)}
                        >
                          <Eye className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                          View Statement
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncSection>

      {viewing && (
        <Modal
          title={`${viewing.display_name} — ${formatMonth(viewing.settlement_month)}`}
          description="Their personal statement for the month."
          onClose={() => setViewing(null)}
          wide
          footer={
            <button type="button" className={BUTTON_PRIMARY} onClick={() => setViewing(null)}>
              Done
            </button>
          }
        >
          <StatementSlip statement={viewing} />
        </Modal>
      )}
    </Panel>
  )
}
