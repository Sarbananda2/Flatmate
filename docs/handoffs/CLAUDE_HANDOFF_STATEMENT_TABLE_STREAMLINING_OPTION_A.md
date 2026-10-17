# Architectural Specification: Statement Table Streamlining (Option A - Quick Peek)

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved by User for Implementation  
**Governing Standards:** Rule U1 (Unified Presentation), Scannability, and Quick-Peek Information Hierarchy.  
**Files Affected:** `frontend/src/pages/Payments.tsx` and `frontend/src/pages/Payments.test.tsx`.

---

## 1. Context & User Mandate

The user reviewed the House Kitty and Landlord statements and requested:
> *"we don't need to show every details in the row. just the essentials for quick peek - option A"*

### Key Issues in Current Table Rows:
1. **Double-Height Row Bloat:** Description currently stacks a bulky badge (`<Badge>{item.category}</Badge>`) over a secondary sentence (`{item.description}`), doubling row height.
2. **Text Redundancy:** For example, row 1 had badge *"Rent Collection"*, subtext *"Exit clearance for Anangarag Mahanta"*, and party column *"Anangarag Mahanta"*.
3. **50% Empty Dash Columns:** Having separate `Money In (+)` and `Money Out (−)` columns means every row had an empty `—` cell, stretching the table needlessly.
4. **Clickable Deep Dives:** Clicking any row already opens the full `StatementEntryDialog` with all metadata, categories, references, and notes. The table row should be a **lean, single-line quick peek**.

---

## 2. Streamlined 5-Column Table Architecture (Option A)

The table is streamlined to 5 essential columns with **one clean horizontal line per row**:

```
┌─────────────┬──────────────────────────────┬────────────────────┬──────────────┬─────────────┐
│ DATE        │ DESCRIPTION                  │ PAID BY / TO       │ AMOUNT       │ BALANCE     │
├─────────────┼──────────────────────────────┼────────────────────┼──────────────┼─────────────┤
│ 12 Oct 2026 │ Exit clearance               │ Anangarag Mahanta  │ +₹9,500.00   │ ₹19,000.00  │
│ 3 Oct 2026  │ Rent for October 2026        │ Sarbananda Taw     │ +₹9,500.00   │ ₹9,500.00   │
├─────────────┴──────────────────────────────┴────────────────────┼──────────────┼─────────────┤
│ Totals and closing balance                                      │ +₹19,000.00  │ ₹19,000.00  │
└─────────────────────────────────────────────────────────────────┴──────────────┴─────────────┘
```

---

## 3. Frontend Implementation Details (`frontend/src/pages/Payments.tsx`)

### 3.1. Update `StatementWording`
```ts
interface StatementWording {
  counterparty: string
  balance: string
}

const KITTY_WORDING: StatementWording = {
  counterparty: 'Paid By / To',
  balance: 'Kitty Balance',
}

const LANDLORD_WORDING: StatementWording = {
  counterparty: 'Paid By',
  balance: 'Total Disbursed',
}
```

### 3.2. Update Table Header in `StatementPanel`
Replace the 6 column headers with the 5 streamlined headers:
```tsx
<thead>
  <tr>
    <th scope="col">Date</th>
    <th scope="col">Description</th>
    <th scope="col">{wording.counterparty}</th>
    <th scope="col" className="num">Amount</th>
    <th scope="col" className="num">{wording.balance}</th>
  </tr>
</thead>
```

### 3.3. Update Table Body Rows (Single-Line Content)
Remove the stacked badge container (`<span className="flex flex-col items-start gap-1 ...">`). Render the clean description directly on one line:

```tsx
<tbody>
  {rows.map((item) => (
    <tr key={item.entry_id} className="cursor-pointer" onClick={() => onOpen(item)}>
      <td className="nowrap">
        <button
          type="button"
          aria-haspopup="dialog"
          aria-label={`Entry details: ${item.category}, ${formatDate(item.date)}, ${formatInr(item.amount_paise)}`}
          className="rounded tabular cursor-pointer"
        >
          {formatDate(item.date)}
        </button>
      </td>
      <td>
        <span className="truncate block font-medium text-[var(--text-primary)]" title={item.description}>
          {item.description || item.category}
        </span>
      </td>
      <td className="nowrap">
        <span className="block truncate max-w-[12rem]" title={item.counterparty_name ?? undefined}>
          {item.counterparty_name ?? '—'}
        </span>
      </td>
      <td className="num">
        <Money
          paise={item.direction === 'in' ? item.amount_paise : -item.amount_paise}
          tone="auto"
          sign="always"
        />
      </td>
      <td className="num font-bold">
        <Money paise={item.balance_paise} />
      </td>
    </tr>
  ))}
</tbody>
```

### 3.4. Update Table Footer (`tfoot`)
```tsx
<tfoot>
  <tr>
    <td colSpan={3}>Totals and closing balance</td>
    <td className="num">
      <Money
        paise={statement.total_in_paise - statement.total_out_paise}
        tone="auto"
        sign="always"
      />
    </td>
    <td className="num font-bold">
      <Money paise={statement.closing_balance_paise} />
    </td>
  </tr>
</tfoot>
```

---

## 4. Test Updates (`frontend/src/pages/Payments.test.tsx`)

Update assertions in `Payments.test.tsx`:
1. **Column Headers:**
   Assert that `getAllByRole('columnheader')` returns 5 headers:
   `['Date', 'Description', 'Paid By / To', 'Amount', 'Kitty Balance']` for House Kitty, and
   `['Date', 'Description', 'Paid By', 'Amount', 'Total Disbursed']` for Landlord.
2. **Body Rows:**
   Update row text expectations to reflect single-line description and unified signed amount:
   ```ts
   expect(bodyRows(table)).toEqual([
     ['5 Nov 2026', 'Broadband', 'ACT Fibernet', '−₹1,200.00', '−₹19,200.00'],
     ['4 Nov 2026', 'Rent payout · November 2026', 'Kumar Yadav', '−₹28,500.00', '−₹18,000.00'],
     ['3 Nov 2026', 'Rent for November 2026', 'Alice', '+₹9,500.00', '₹10,500.00'],
     ['1 Nov 2026', 'Payment into House Kitty', 'Bob', '+₹1,000.00', '₹1,000.00'],
   ])
   ```
3. **Footer:**
   Update footer text content expectation to match the 5-column layout.

---

## 5. Verification Checklist

1. Run frontend tests:
   ```bash
   npm --prefix frontend test -- --run
   ```
2. Verify in browser at `http://localhost:8787/payments`:
   - Both House Kitty and Landlord statements display a compact 5-column layout.
   - Rows are single-line (no multi-line badge stacking).
   - Inflows are green (`+₹...`), outflows are amber/red (`−₹...`).
   - Clicking a row still opens the full detail dialog.

