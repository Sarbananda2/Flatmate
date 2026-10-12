# Handoff: Enforce Universal Table Header Center Alignment

**Rule:** Every table rendered across the entire application must have all of its column headers (`<th>`) center-aligned. This applies across all existing tables and any newly created table/page going forward.

---

## 1. CSS Rule Updates in `frontend/src/index.css`

Update `table.data thead th` in [`frontend/src/index.css`](../frontend/src/index.css):

```css
table.data thead th,
table.data thead th.num {
  background: var(--surface-sunken);
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-subtle);
  font-weight: 700;
  white-space: nowrap;
  text-align: center;
  position: sticky; top: 0; z-index: 1;
}
```

Ensure `.num` on `th` elements does not force `text-align: right` on the header cell (`td.num` continues to be right-aligned for tabular numbers).

Also in `table.data.residents-table`:
```css
table.data.residents-table th:nth-child(1) { width: 28%; }
table.data.residents-table th:nth-child(2) { width: 12%; }
table.data.residents-table th:nth-child(3) { width: 26%; }
table.data.residents-table th:nth-child(4) { width: 14%; }
table.data.residents-table td:nth-child(4) { text-align: center; }
table.data.residents-table th:nth-child(5) { width: 20%; }
```
(Notice `th:nth-child(4)` no longer needs individual `text-align: center` since all `th` elements are centered by default).

---

## 2. Page Template Verification

When creating new pages or auditing existing tables (e.g. `Transactions.tsx`, `ResidentDetail.tsx`, `Definitions.tsx`, `Reconciliation.tsx`, `Setup.tsx`):
- Do **not** apply `text-left` or `text-right` to `<th>` elements.
- Header cells should rely on `table.data thead th` center-alignment or explicit `text-center`.
- `<td>` data cells retain their semantic alignment:
  - Text, names, categories: Left-aligned
  - Numbers, amounts, currency: Right-aligned (`.num`, `tabular-nums`)
  - Status badges, pills, action buttons: Center-aligned

---

## 3. Verification Commands

Run the test suite and production build:
```bash
cd frontend && npm test
cd frontend && npm run build
```
Ensure 134/134 frontend tests pass cleanly with zero build errors.

