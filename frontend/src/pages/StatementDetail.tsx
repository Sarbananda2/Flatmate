/**
 * One statement: review, correct, finalize, and inspect.
 *
 * A draft shows live figures plus the expenses still eligible to be attached.
 * Once finalized, the stored snapshot is what is shown and exported, with its
 * hash verified, so an exported finalized statement reproduces exactly what was
 * finalized rather than what the ledger looks like today.
 */
import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Page } from "../components/Layout";
import {
  AsyncSection,
  Badge,
  Callout,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Modal,
  Money,
  Stat,
  StatementStatusBadge,
} from "../components/Primitives";
import type { BadgeTone } from "../components/Primitives";
import { CheckboxField, TextAreaField, TextField } from "../components/Form";
import { useToast } from "../components/Toast";
import { api, downloadCsv } from "../api/client";
import { useMutation, useResource } from "../lib/useApi";
import {
  PAYMENT_KIND_LABELS,
  PERIOD_RULE_LABELS,
  formatDate,
  formatDateTime,
  formatHoursRemaining,
  formatInr,
  formatMonth,
} from "../lib/format";
import type { StatementPreview, StatementRow } from "../api/types";

export default function StatementDetailPage() {
  const { statementId = "" } = useParams();
  const toast = useToast();
  const statement = useResource<StatementRow & { revisions: unknown[] }>(
    statementId ? `/api/statements/${encodeURIComponent(statementId)}` : null,
  );
  const preview = useResource<StatementPreview>(
    statementId
      ? `/api/statements/${encodeURIComponent(statementId)}/preview`
      : null,
  );
  const [finalizing, setFinalizing] = useState(false);
  const [revising, setRevising] = useState(false);
  const [closing, setClosing] = useState(false);

  const refresh = () => {
    statement.reload();
    preview.reload();
  };

  const attach = useMutation(
    () =>
      api.post<{ attached_count: number }>(
        `/api/statements/${encodeURIComponent(statementId)}/attach-expenses`,
        {},
      ),
    {
      onSuccess: (result) => {
        refresh();
        toast.success(
          result.attached_count
            ? `Attached ${result.attached_count} expense${result.attached_count === 1 ? "" : "s"}`
            : "Nothing eligible to attach",
        );
      },
    },
  );

  const status = statement.data?.status;
  const month = statement.data?.settlement_month;

  return (
    <Page
      title={month ? `Statement — ${formatMonth(month)}` : "Statement"}
      subtitle="Everything charged in this settlement month, reconciled against the household's full history."
      breadcrumbs={[
        { label: "Settle" },
        { label: "Statements", to: "/statements" },
        { label: month ? formatMonth(month) : statementId },
      ]}
      actions={
        <>
          <button type="button" className="btn" onClick={() => window.print()}>
            Print
          </button>
          <button
            type="button"
            className="btn"
            onClick={() =>
              downloadCsv(
                `/api/statements/${encodeURIComponent(statementId)}/export/csv`,
              )
            }
          >
            Export CSV
          </button>
          {status === "draft" && (
            <>
              <button
                type="button"
                className="btn"
                disabled={attach.pending}
                onClick={() => void attach.run(undefined)}
              >
                {attach.pending ? "Attaching…" : "Attach this month’s expenses"}
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => setFinalizing(true)}
              >
                Finalize…
              </button>
            </>
          )}
          {status === "finalized_correction_window" && (
            <>
              <button
                type="button"
                className="btn"
                onClick={() => setRevising(true)}
              >
                Record a correction…
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => setClosing(true)}
              >
                Close…
              </button>
            </>
          )}
        </>
      }
    >
      <ErrorNotice error={attach.error} />
      <AsyncSection resource={preview}>
        {(data) => <Body data={data} />}
      </AsyncSection>

      {finalizing && (
        <FinalizeDialog
          preview={preview.data}
          statementId={statementId}
          onClose={() => setFinalizing(false)}
          onFinalized={() => {
            setFinalizing(false);
            refresh();
            toast.success(
              "Statement finalized",
              "The 72-hour correction window has started.",
            );
          }}
        />
      )}
      {revising && (
        <ReviseDialog
          statementId={statementId}
          onClose={() => setRevising(false)}
          onRevised={() => {
            setRevising(false);
            refresh();
            toast.success("Correction recorded as a revision");
          }}
        />
      )}
      {closing && (
        <CloseDialog
          statement={statement.data}
          statementId={statementId}
          onClose={() => setClosing(false)}
          onClosed={() => {
            setClosing(false);
            refresh();
            toast.success("Statement closed", "The snapshot is now immutable.");
          }}
        />
      )}
    </Page>
  );
}

function Body({ data }: { data: StatementPreview }) {
  const [showGuideModal, setShowGuideModal] = useState(false);
  const residents = data.accounts.filter(
    (account) => account.account_type === "resident",
  );
  const charges = data.statement_charges_paise_by_resident;
  const residentNames = new Map(
    data.residents.map((r) => [r.resident_id, r.display_name]),
  );

  return (
    <>
      <div className="row">
        <StatementStatusBadge status={data.status} />
        {data.status === "finalized_correction_window" &&
          data.correction_deadline && (
            <span className="subtle">
              Correction window closes{" "}
              {formatDateTime(data.correction_deadline)}
            </span>
          )}
        {data.status === "closed" && (
          <span className="subtle">
            Closed {formatDateTime(data.closed_at)} — immutable
          </span>
        )}
        {data.snapshot_hash && (
          <span className="id-chip" title="SHA-256 of the snapshot">
            {data.snapshot_hash.slice(0, 16)}…
          </span>
        )}
      </div>

      {data.blocking_issues.length > 0 && (
        <Callout tone="danger" title="These must be resolved before finalizing">
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {data.blocking_issues.map((issue) => (
              <li key={`${issue.code}-${issue.entity_id}`}>
                {issue.message}
                {issue.suggested_action && (
                  <>
                    {" "}
                    <span className="muted">{issue.suggested_action}</span>
                  </>
                )}
              </li>
            ))}
          </ul>
        </Callout>
      )}

      {data.validation.warning_count > 0 && (
        <Callout tone="warn" title="Worth checking before you finalize">
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {data.validation.issues
              .filter((issue) => issue.severity === "warning")
              .map((issue) => (
                <li key={`${issue.code}-${issue.entity_id}`}>
                  {issue.message}
                  {issue.suggested_action && (
                    <>
                      {" "}
                      <span className="muted">{issue.suggested_action}</span>
                    </>
                  )}
                </li>
              ))}
          </ul>
        </Callout>
      )}

      {data.status === "closed" && (
        <Callout tone="info" title="This statement is closed">
          The snapshot cannot be changed, in the application or by direct SQL. A
          correction now belongs in a signed ledger adjustment, which is
          recorded as its own event and leaves this statement exactly as it was
          finalized.
        </Callout>
      )}

      <div className="grid grid--stats">
        <Stat
          label="Statement total"
          value={formatInr(data.statement_total_paise)}
          note={`${data.expense_count} expense${data.expense_count === 1 ? "" : "s"} in this month`}
        />
        <Stat
          label="Household expenses, all time"
          value={formatInr(data.totals.household_expenses_paise)}
          note="Across every month on record"
        />
        <Stat
          label="Outstanding charges"
          value={formatInr(data.totals.outstanding_charges_paise)}
          note="Carried forward until settled"
        />
        <Stat
          label="Unapplied credits"
          value={formatInr(data.totals.unapplied_credits_paise)}
          note="Still held for residents"
        />
      </div>

      {data.candidate_expenses.length > 0 && data.status === "draft" && (
        <Card
          title={`${data.candidate_expenses.length} expense(s) settle in this month but are not attached`}
          hint="Collected by each expense's period rule, not by its transaction date."
          flush
        >
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Candidate expenses</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Description</th>
                  <th scope="col">Service period</th>
                  <th scope="col" className="num">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.candidate_expenses.map((candidate) => (
                  <tr key={candidate.expense_id}>
                    <td className="nowrap">
                      {formatDate(candidate.expense_date)}
                    </td>
                    <th scope="row">{candidate.description}</th>
                    <td className="nowrap">
                      {formatMonth(candidate.service_period)}
                    </td>
                    <td className="num">
                      <Money paise={candidate.amount_paise} />
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>Total not yet attached</td>
                  <td className="num">
                    <Money paise={data.candidate_expense_total_paise} />
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>
      )}

      <Card
        title="Expenses in this statement"
        hint="Funding is shown separately from the shares, because they are different facts."
        flush
      >
        {data.expenses.length === 0 ? (
          <EmptyState
            title="No expenses attached yet"
            body="Attach the expenses that settle in this month, or record new ones."
            action={
              <Link className="btn btn--primary" to="/expenses">
                Record an expense
              </Link>
            }
          />
        ) : (
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Expenses in this statement</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Description</th>
                  <th scope="col">Service → settlement</th>
                  <th scope="col">Funded by</th>
                  {data.residents.map((resident) => (
                    <th scope="col" className="num" key={resident.resident_id}>
                      {resident.display_name}
                    </th>
                  ))}
                  <th scope="col" className="num">
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.expenses.map((expense) => {
                  const byResident = new Map(
                    expense.allocations.map((allocation) => [
                      allocation.resident_id,
                      allocation.amount_paise,
                    ]),
                  );
                  return (
                    <tr key={expense.expense_id}>
                      <td className="nowrap">
                        {formatDate(expense.expense_date)}
                      </td>
                      <th scope="row">
                        {expense.description}
                        <div className="subtle">
                          {expense.definition_name ?? "No category"}
                          {expense.expense_kind === "electricity" && (
                            <>
                              {" "}
                              <Badge tone="accent" glyph="⚡">
                                Electricity
                              </Badge>
                            </>
                          )}
                          {expense.generated_from_recurrence && (
                            <>
                              {" "}
                              <Badge tone="neutral">recurring</Badge>
                            </>
                          )}
                        </div>
                      </th>
                      <td className="nowrap">
                        {formatMonth(expense.service_period)}
                        <div className="subtle">
                          → {formatMonth(expense.settlement_period)}
                          {expense.period_rule && (
                            <>
                              {" · "}
                              {PERIOD_RULE_LABELS[expense.period_rule] ??
                                expense.period_rule}
                            </>
                          )}
                        </div>
                      </td>
                      <td>
                        {expense.funding_source ===
                        "linked_provider_advance" ? (
                          <>
                            <Badge tone="ok">linked advance</Badge>
                            <div className="subtle">
                              {expense.linked_provider_advances
                                .map(
                                  (advance) =>
                                    `${residentNames.get(advance.payer_resident_id) ?? advance.payer_resident_id} ${formatInr(advance.amount_paise)}`,
                                )
                                .join(", ")}
                            </div>
                          </>
                        ) : expense.funding_source === "payer_resident" ? (
                          (expense.payer_display_name ??
                          expense.payer_resident_id)
                        ) : (
                          <span className="subtle">household pool</span>
                        )}
                      </td>
                      {data.residents.map((resident) => (
                        <td className="num" key={resident.resident_id}>
                          <Money
                            paise={byResident.get(resident.resident_id) ?? 0}
                          />
                        </td>
                      ))}
                      <td className="num">
                        <Money paise={expense.amount_paise} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={4}>Charged this month</td>
                  {data.residents.map((resident) => (
                    <td className="num" key={resident.resident_id}>
                      <Money paise={charges[resident.resident_id] ?? 0} />
                    </td>
                  ))}
                  <td className="num">
                    <Money paise={data.statement_total_paise} />
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>

      {data.carry_forward_breakdown &&
        data.carry_forward_breakdown.length > 0 && (
          <Card
            title="Monthly carry forward progression"
            hint="Previous Balance − Month Share − Unattached + Payments + Advances ± Adjustments = Carried Forward"
            actions={
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setShowGuideModal(true)}
              >
                ⓘ How this math works
              </button>
            }
            flush
          >
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">
                  Monthly carry forward progression
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Resident</th>
                    <th scope="col" className="num" style={{ whiteSpace: 'normal' }}>
                      Brought forward
                    </th>
                    <th scope="col" className="num" style={{ whiteSpace: 'normal' }}>
                      This month's share
                    </th>
                    <th scope="col" className="num">
                      Unattached
                    </th>
                    <th scope="col" className="num" style={{ whiteSpace: 'normal' }}>
                      Payments applied
                    </th>
                    <th scope="col" className="num" style={{ whiteSpace: 'normal' }}>
                      Advances / Funded
                    </th>
                    <th scope="col" className="num">
                      Adjustments
                    </th>
                    <th scope="col" className="num" style={{ whiteSpace: 'normal' }}>
                      Carried forward
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.carry_forward_breakdown.map((row) => (
                    <Fragment key={row.resident_id}>
                      <tr className={row.formula_explanation ? 'row--joined' : undefined}>
                        <th scope="row">
                          <Link to={`/residents/${row.resident_id}`}>
                            {row.display_name}
                          </Link>
                        </th>
                        <td className="num">
                          <Money
                            paise={row.brought_forward_paise}
                            tone="auto"
                            sign="always"
                          />
                        </td>
                        <td className="num">
                          <Money paise={row.statement_charges_paise} />
                        </td>
                        <td className="num">
                          {row.unattached_charges_paise ? (
                            <Money paise={row.unattached_charges_paise} />
                          ) : (
                            <span className="subtle">—</span>
                          )}
                        </td>
                        <td className="num">
                          <Money paise={row.payments_applied_paise} />
                        </td>
                        <td className="num">
                          <Money paise={row.provider_advances_paise} />
                        </td>
                        <td className="num">
                          <Money
                            paise={row.adjustments_paise}
                            tone="auto"
                            sign="always"
                          />
                        </td>
                        <td className="num">
                          <Money
                            paise={row.carried_forward_paise}
                            tone="auto"
                            sign="always"
                          />
                        </td>
                      </tr>
                      {row.formula_explanation && (
                        <tr style={{ background: "transparent" }}>
                          <td
                            colSpan={8}
                            className="subtle"
                            style={{
                              fontSize: "0.75rem",
                              paddingLeft: "1.5rem",
                              paddingTop: "0.2rem",
                              paddingBottom: "0.6rem",
                              borderTop: "none",
                            }}
                          >
                            ↳ {row.formula_explanation}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
                {data.carry_forward_totals && (
                  <tfoot>
                    <tr>
                      <th scope="row">
                        Household Totals
                        {/* Residents alone balance only when the pool holds nothing. */}
                        <div
                          className="subtle"
                          style={{ fontWeight: "normal", fontSize: "0.75rem" }}
                        >
                          {(data.carry_forward_totals.carried_forward_paise ??
                            0) !== 0
                            ? "offset by household pool"
                            : "(Balanced)"}
                        </div>
                      </th>
                      <td className="num">
                        <Money
                          paise={
                            data.carry_forward_totals.brought_forward_paise
                          }
                          tone="auto"
                          sign="always"
                        />
                      </td>
                      <td className="num">
                        <Money
                          paise={
                            data.carry_forward_totals.statement_charges_paise
                          }
                        />
                      </td>
                      <td className="num">
                        <Money
                          paise={
                            data.carry_forward_totals.unattached_charges_paise
                          }
                        />
                      </td>
                      <td className="num">
                        <Money
                          paise={
                            data.carry_forward_totals.payments_applied_paise
                          }
                        />
                      </td>
                      <td className="num">
                        <Money
                          paise={
                            data.carry_forward_totals.provider_advances_paise
                          }
                        />
                      </td>
                      <td className="num">
                        <Money
                          paise={data.carry_forward_totals.adjustments_paise}
                          tone="auto"
                          sign="always"
                        />
                      </td>
                      <td className="num">
                        <Money
                          paise={
                            data.carry_forward_totals.carried_forward_paise ?? 0
                          }
                          tone="auto"
                          sign="always"
                        />
                      </td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </Card>
        )}

      {showGuideModal && (
        <CarryForwardGuide onClose={() => setShowGuideModal(false)} />
      )}

      <Card
        title="Reconciled balances across all months"
        hint="This is the full household ledger, not just this month: outstanding charges carried forward, credits, funding, and adjustments."
        flush
        footer={
          <span>
            All accounts sum to <Money paise={data.net_positions_sum_paise} /> —
            the pool and every resident together always balance to zero.
          </span>
        }
      >
        <div className="table-scroll">
          <table className="data">
            <caption className="sr-only">Reconciled balances</caption>
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col" className="num">
                  Charged
                </th>
                <th scope="col" className="num">
                  Outstanding
                </th>
                <th scope="col" className="num">
                  Paid in
                </th>
                <th scope="col" className="num">
                  Funded
                </th>
                <th scope="col" className="num">
                  Credit
                </th>
                <th scope="col" className="num">
                  Adjustments
                </th>
                <th scope="col" className="num">
                  Net balance
                </th>
              </tr>
            </thead>
            <tbody>
              {residents.map((account) => (
                <tr key={account.account_id}>
                  <th scope="row">
                    <Link to={`/residents/${account.account_id}`}>
                      {account.display_name}
                    </Link>
                  </th>
                  <td className="num">
                    <Money paise={account.allocated_charges_paise ?? 0} />
                  </td>
                  <td className="num">
                    <Money
                      paise={account.outstanding_charges_paise ?? 0}
                      tone={
                        account.outstanding_charges_paise ? "owed-by" : "plain"
                      }
                    />
                  </td>
                  <td className="num">
                    <Money paise={account.pool_payments_paise ?? 0} />
                  </td>
                  <td className="num">
                    <Money paise={account.funding_credits_paise ?? 0} />
                  </td>
                  <td className="num">
                    <Money
                      paise={account.unapplied_credit_paise ?? 0}
                      tone={account.unapplied_credit_paise ? "credit" : "plain"}
                    />
                  </td>
                  <td className="num">
                    <Money
                      paise={account.adjustments_paise ?? 0}
                      tone="auto"
                      sign="always"
                    />
                  </td>
                  <td className="num">
                    <Money
                      paise={account.net_position_paise}
                      tone="auto"
                      sign="always"
                    />
                  </td>
                </tr>
              ))}
              {data.accounts
                .filter((account) => account.account_type === "pool")
                .map((pool) => (
                  <tr key={pool.account_id}>
                    <th scope="row">
                      {pool.display_name}
                      <div className="subtle">
                        cash held by or owed to the pool
                      </div>
                    </th>
                    <td className="num" colSpan={6}>
                      <span className="subtle">
                        expenses incurred{" "}
                        <Money paise={pool.expenses_incurred_paise ?? 0} /> ·
                        funded by residents{" "}
                        <Money paise={pool.funded_by_residents_paise ?? 0} />
                      </span>
                    </td>
                    <td className="num">
                      <Money
                        paise={pool.net_position_paise}
                        tone="auto"
                        sign="always"
                      />
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid--halves">
        <Card
          title="Suggested transfers"
          hint="Advice only. Nothing here changes a record; recording a transfer is a separate action."
          flush
        >
          {data.transfers.length === 0 ? (
            <EmptyState icon="✓" title="Nothing to settle" />
          ) : (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Suggested transfers</caption>
                <thead>
                  <tr>
                    <th scope="col">From</th>
                    <th scope="col">To</th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.transfers.map((transfer) => (
                    <tr
                      key={`${transfer.from_account}-${transfer.to_account}-${transfer.amount_paise}`}
                    >
                      <td>{transfer.from_display_name}</td>
                      <td>{transfer.to_display_name}</td>
                      <td className="num">
                        <Money paise={transfer.amount_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Payments recorded in this month" flush>
          {data.payments_in_month.length === 0 ? (
            <EmptyState title="No payments this month" />
          ) : (
            <div className="table-scroll">
              <table className="data">
                <caption className="sr-only">Payments in this month</caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Resident</th>
                    <th scope="col">Kind</th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.payments_in_month.map((payment) => (
                    <tr key={payment.payment_id}>
                      <td className="nowrap">
                        {formatDate(payment.payment_date)}
                      </td>
                      <td>
                        {payment.payer_display_name ??
                          payment.payer_resident_id}
                      </td>
                      <td>
                        {PAYMENT_KIND_LABELS[payment.payment_kind] ??
                          payment.payment_kind}
                      </td>
                      <td className="num">
                        <Money paise={payment.amount_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {data.adjustments.length > 0 && (
        <Card
          title="Adjustments linked to this statement"
          hint="Signed corrections recorded against this month without rewriting it."
          flush
        >
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Adjustments</caption>
              <thead>
                <tr>
                  <th scope="col">Effective</th>
                  <th scope="col">Resident</th>
                  <th scope="col">Reason</th>
                  <th scope="col" className="num">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.adjustments.map((adjustment) => (
                  <tr key={adjustment.adjustment_id}>
                    <td className="nowrap">
                      {formatDate(
                        adjustment.effective_date ?? adjustment.created_at,
                      )}
                    </td>
                    <td>{adjustment.display_name ?? adjustment.resident_id}</td>
                    <td>{adjustment.reason}</td>
                    <td className="num">
                      <Money
                        paise={adjustment.amount_paise}
                        tone="auto"
                        sign="always"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {data.revisions.length > 0 && (
        <Card
          title="Revision history"
          hint="Numbered and immutable. Each one records why."
          flush
        >
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Revisions</caption>
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">When</th>
                  <th scope="col">By</th>
                  <th scope="col">Reason</th>
                </tr>
              </thead>
              <tbody>
                {data.revisions.map((revision) => (
                  <tr key={revision.revision_id}>
                    <th scope="row">{revision.revision_number}</th>
                    <td className="nowrap">
                      {formatDateTime(revision.created_at)}
                    </td>
                    <td>{revision.created_by}</td>
                    <td>{revision.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}

function FinalizeDialog({
  preview,
  statementId,
  onClose,
  onFinalized,
}: {
  preview: StatementPreview | null;
  statementId: string;
  onClose: () => void;
  onFinalized: () => void;
}) {
  const [finalizedBy, setFinalizedBy] = useState("operator");
  const [requireClean, setRequireClean] = useState(true);
  const [touched, setTouched] = useState(false);

  const finalize = useMutation(
    () =>
      api.post(`/api/statements/${encodeURIComponent(statementId)}/finalize`, {
        finalized_by: finalizedBy.trim(),
        confirm: true,
        require_clean: requireClean,
      }),
    { onSuccess: onFinalized },
  );

  const blocked = (preview?.blocking_issues.length ?? 0) > 0;
  const nameError =
    touched && !finalizedBy.trim() ? "Enter who is finalizing this." : null;

  return (
    <ConfirmDialog
      title={`Finalize ${preview ? formatMonth(preview.settlement_month) : "this statement"}?`}
      confirmLabel="Finalize statement"
      pending={finalize.pending}
      error={finalize.error}
      confirmDisabled={!finalizedBy.trim() || (blocked && requireClean)}
      onCancel={onClose}
      onConfirm={() => {
        setTouched(true);
        if (finalizedBy.trim()) void finalize.run(undefined);
      }}
      body={
        <div className="stack">
          <p style={{ margin: 0 }}>
            This stores a hashed snapshot of the statement and starts a{" "}
            {preview?.correction_window_hours ?? 72}-hour correction window. It
            is not reversible: after the window the statement closes and becomes
            immutable.
          </p>
          {preview && (
            <dl className="kv">
              <dt>Expenses</dt>
              <dd>{preview.expense_count}</dd>
              <dt>Statement total</dt>
              <dd>
                <Money paise={preview.statement_total_paise} />
              </dd>
              <dt>Not yet attached</dt>
              <dd>
                {preview.candidate_expenses.length} expense
                {preview.candidate_expenses.length === 1 ? "" : "s"} (
                <Money paise={preview.candidate_expense_total_paise} />)
              </dd>
              <dt>Accounting errors</dt>
              <dd>{preview.validation.error_count}</dd>
              <dt>Warnings</dt>
              <dd>{preview.validation.warning_count}</dd>
            </dl>
          )}
          {preview && preview.candidate_expenses.length > 0 && (
            <Callout
              tone="warn"
              title="Some expenses for this month are not attached"
            >
              {preview.candidate_expenses.length} expense
              {preview.candidate_expenses.length === 1 ? "" : "s"} settle in
              this month but are not in the statement. Attach them first if they
              belong here — a closed statement cannot take them later.
            </Callout>
          )}
          {blocked && (
            <Callout tone="danger" title="This statement has accounting errors">
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {preview?.blocking_issues.map((issue) => (
                  <li key={`${issue.code}-${issue.entity_id}`}>
                    {issue.message}
                  </li>
                ))}
              </ul>
            </Callout>
          )}
        </div>
      }
    >
      <div className="stack">
        <TextField
          label="Finalized by"
          value={finalizedBy}
          onChange={setFinalizedBy}
          required
          error={nameError}
          hint="Recorded on the snapshot and in the audit trail."
          maxLength={120}
        />
        <CheckboxField
          label="Refuse to finalize while accounting errors remain"
          checked={requireClean}
          onChange={setRequireClean}
          hint="Leave this on unless you have a deliberate reason to finalize over a known error."
        />
      </div>
    </ConfirmDialog>
  );
}

function ReviseDialog({
  statementId,
  onClose,
  onRevised,
}: {
  statementId: string;
  onClose: () => void;
  onRevised: () => void;
}) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const revise = useMutation(
    () =>
      api.post(`/api/statements/${encodeURIComponent(statementId)}/revise`, {
        reason: reason.trim(),
        change_note: note.trim() || null,
      }),
    { onSuccess: onRevised },
  );

  return (
    <ConfirmDialog
      title="Record a correction"
      confirmLabel="Record revision"
      pending={revise.pending}
      error={revise.error}
      confirmDisabled={!reason.trim()}
      onCancel={onClose}
      onConfirm={() => void revise.run(undefined)}
      body={
        <>
          This appends a numbered, immutable revision and re-takes the snapshot
          from the current data. Use it after editing an expense inside the
          correction window, or to record a correction you made elsewhere. It is
          refused once the window has expired — then the correction belongs in a
          ledger adjustment.
        </>
      }
    >
      <div className="stack">
        <TextField
          label="Reason"
          value={reason}
          onChange={setReason}
          required
          maxLength={500}
          placeholder="Electricity reading for Bob was corrected"
        />
        <TextAreaField
          label="Change note (optional)"
          value={note}
          onChange={setNote}
          rows={2}
        />
      </div>
    </ConfirmDialog>
  );
}

function CloseDialog({
  statement,
  statementId,
  onClose,
  onClosed,
}: {
  statement: StatementRow | null;
  statementId: string;
  onClose: () => void;
  onClosed: () => void;
}) {
  const windowOpen = Boolean(statement?.correction_window_open);
  const [force, setForce] = useState(false);
  const close = useMutation(
    () =>
      api.post(`/api/statements/${encodeURIComponent(statementId)}/close`, {
        confirm: true,
        force,
      }),
    { onSuccess: onClosed },
  );

  return (
    <ConfirmDialog
      title="Close this statement?"
      confirmLabel="Close statement"
      tone="danger"
      pending={close.pending}
      error={close.error}
      confirmDisabled={windowOpen && !force}
      onCancel={onClose}
      onConfirm={() => void close.run(undefined)}
      body={
        <div className="stack">
          <p style={{ margin: 0 }}>
            After closing, the snapshot is immutable — enforced in the
            application and by database triggers. Any later correction must be a
            signed ledger adjustment, which is recorded as its own event and
            leaves this statement exactly as finalized.
          </p>
          {windOpenNotice(statement)}
        </div>
      }
    >
      {windowOpen && (
        <CheckboxField
          label="Close early, before the correction window has elapsed"
          checked={force}
          onChange={setForce}
          hint="Only do this if you are certain no further corrections are needed."
        />
      )}
    </ConfirmDialog>
  );
}

function windOpenNotice(statement: StatementRow | null) {
  if (!statement?.correction_window_open) {
    return (
      <Callout tone="info">
        The correction window has already elapsed, so closing this statement is
        the normal next step.
      </Callout>
    );
  }
  return (
    <Callout tone="warn" title="The correction window is still open">
      {formatHoursRemaining(statement.correction_hours_remaining)}. Closing now
      gives up the ability to correct this statement in place.
    </Callout>
  );
}

/* ------------------------------------------------------- carry forward guide -- */

const GUIDE_PANEL = {
  padding: "0.75rem",
  borderRadius: "6px",
  background: "var(--bg-surface, #111726)",
  border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))",
} as const;

const GUIDE_HEADING = {
  fontSize: "0.75rem",
  textTransform: "uppercase",
  fontWeight: 600,
  color: "var(--text-tertiary, #64748b)",
  marginBottom: "0.5rem",
} as const;

const GUIDE_COLUMNS: Array<{
  name: string;
  tone: BadgeTone;
  badge: string;
  body: string;
}> = [
  {
    name: "1. Brought Forward",
    tone: "accent",
    badge: "Start",
    body: "Net position from prior month or pre-app opening balance. (-) means debt, (+) means credit.",
  },
  {
    name: "2. Month's Share",
    tone: "danger",
    badge: "Expense",
    body: "Allocated share of the expenses on this month's statement (Rent, Maid, Cook, Electricity, etc.).",
  },
  {
    name: "3. Unattached",
    tone: "warn",
    badge: "Off statement",
    body: "Share of charges that settle in this month but were never attached to this statement. Usually zero.",
  },
  {
    name: "4. Paid In (Pool / Kitty)",
    tone: "ok",
    badge: "Deposit",
    body: "Actual funds transferred by resident into the shared apartment bank account or UPI flat pool, less any refund paid back.",
  },
  {
    name: "5. Advances / Funded",
    tone: "ok",
    badge: "Direct Pay",
    body: "Out-of-pocket payments made directly to a vendor or landlord on the household's behalf.",
  },
  {
    name: "6. Adjustments",
    tone: "neutral",
    badge: "Correction",
    body: "Signed ledger adjustments effective this month. (+) credits the resident, (-) debits them.",
  },
  {
    name: "7. Carried Forward",
    tone: "credit",
    badge: "Closing",
    body: "Net closing balance after applying all month entries. Becomes next month's Brought Forward.",
  },
];

function CarryForwardGuide({ onClose }: { onClose: () => void }) {
  return (
    <Modal
      title="Monthly Carry Forward Guide & Formula Explanation"
      description="How resident dues progress across monthly statements and reconcile to zero."
      wide
      onClose={onClose}
      footer={
        <button type="button" className="btn btn--primary" onClick={onClose}>
          Got it, close guide
        </button>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        <div
          style={{
            background: "var(--bg-elevated, #182238)",
            padding: "1rem",
            borderRadius: "8px",
            border: "1px solid var(--border-subtle, rgba(255,255,255,0.1))",
          }}
        >
          <div
            style={{
              fontSize: "0.75rem",
              textTransform: "uppercase",
              color: "var(--accent, #3b82f6)",
              fontWeight: 600,
              marginBottom: "0.25rem",
            }}
          >
            The Core Progression Formula
          </div>
          <code
            style={{
              display: "block",
              padding: "0.75rem",
              background: "rgba(0,0,0,0.3)",
              borderRadius: "6px",
              fontSize: "0.85rem",
            }}
          >
            Carried Forward = Brought Forward − Month's Share − Unattached +
            Paid In + Funded/Adv ± Adjustments
          </code>
          <p
            className="subtle"
            style={{ fontSize: "0.8rem", marginTop: "0.5rem", marginBottom: 0 }}
          >
            Every month connects directly to the next. The household ledger is
            zero-sum: the residents' balances and the household pool's balance
            always add up to exactly ₹0.
          </p>
        </div>

        <div>
          <div style={GUIDE_HEADING}>Column Reference</div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "0.75rem",
            }}
          >
            {GUIDE_COLUMNS.map((column) => (
              <div key={column.name} style={GUIDE_PANEL}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "0.25rem",
                  }}
                >
                  <strong>{column.name}</strong>
                  <Badge tone={column.tone}>{column.badge}</Badge>
                </div>
                <div className="subtle" style={{ fontSize: "0.75rem" }}>
                  {column.body}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div style={GUIDE_PANEL}>
          <div style={GUIDE_HEADING}>Where This Fits In Your Monthly Cycle</div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.5rem",
              fontSize: "0.75rem",
            }}
          >
            <div>
              <strong>1st – 25th: Day-to-Day</strong>
              <div className="subtle">
                Log bills and pool deposits. Balances accumulate in real-time.
              </div>
            </div>
            <div>
              <strong>26th – 31st: Month-End Review</strong>
              <div className="subtle">
                Admin drafts statement. Inspect this table to verify who owes or
                is owed money.
              </div>
            </div>
            <div>
              <strong>1st – 5th: Settlement</strong>
              <div className="subtle">
                Roommates pay "Carried Forward" dues to square up their account
                to ₹0.
              </div>
            </div>
          </div>
        </div>

        <div
          style={{
            padding: "0.75rem",
            borderRadius: "6px",
            background: "rgba(59, 130, 246, 0.05)",
            border: "1px solid rgba(59, 130, 246, 0.2)",
          }}
        >
          <div style={{ ...GUIDE_HEADING, color: "var(--accent, #3b82f6)" }}>
            Practical Flat Walkthrough (Flat 402)
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "0.4rem",
              fontSize: "0.75rem",
            }}
          >
            <div>
              <strong>Alice (-₹2,250):</strong> Started with -₹750 prior debt.
              October share was ₹5,500. Transferred ₹4,000 to pool.{" "}
              <code>-₹750 − ₹5,500 + ₹4,000 = -₹2,250</code> owed.
            </div>
            <div>
              <strong>Bob (+₹700):</strong> Started with +₹1,200 credit. October
              share was ₹5,500. Transferred ₹5,000 to pool.{" "}
              <code>+₹1,200 − ₹5,500 + ₹5,000 = +₹700</code> surplus credit.
            </div>
            <div>
              <strong>Chris (+₹1,550):</strong> Started at ₹0. October share
              ₹5,500. Unattached gas ₹350. But Chris paid ₹7,400 rent directly
              to landlord. <code>₹0 − ₹5,500 − ₹350 + ₹7,400 = +₹1,550</code>{" "}
              owed back to Chris.
            </div>
            <div
              style={{
                marginTop: "0.25rem",
                paddingTop: "0.25rem",
                borderTop: "1px dashed rgba(255,255,255,0.1)",
                color: "var(--success, #10b981)",
                fontWeight: 600,
              }}
            >
              Sum Check: Alice (-₹2,250) + Bob (+₹700) + Chris (+₹1,550) =
              ₹0.00, so the pool holds nothing (Balanced ✓)
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
