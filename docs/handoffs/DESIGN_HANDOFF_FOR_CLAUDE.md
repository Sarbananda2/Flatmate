# Apartment Expense Manager (AEM) — UI/UX Design Specification & Technical Handoff
**Role:** Master Designer & UI Architect  
**Audience:** Claude (Lead Frontend Implementer)  
**Status:** Approved & Ready for Execution  
**Interactive Prototype:** See `wireframe_prototype.html` in the conversation artifact directory or inspect the HTML markup directly.

---

## 1. Executive Summary & Design Philosophy

The Apartment Expense Manager (AEM) is transitioning from a utilitarian developer tool to a **Linear/Stripe-tier accounting interface**. Flat finances are an immutable, mathematically verified ledger — the UI must project institutional trust, calm precision, and effortless readability.

### Core Architecture Decisions (Signed Off by User)
1. **Pure Topbar Navigation:** No sidebar. No layout switcher. The application uses a single, sticky topbar ribbon with a clean, full-width canvas.
2. **Typography System:** **Lora** (serif) across all headers, body copy, and UI controls. **Monospace Tabular** (`font-variant-numeric: tabular-nums`) strictly for monetary figures, hashes, kWh, and mathematical breakdowns.
3. **Master Topbar Ribbon Hierarchy:**
   * **Left:** Flat Emblem & Apartment Name (Single line, vertically centered. Subtitles like *"Accounting Vault · Local"* have been **permanently eliminated**).
   * **Center (Strictly Midpoint-Anchored):** Centered navigation tabs (`Month Hub | Activity Ledger | Statements & Power`) anchored at `left: 50%; transform: translateX(-50%)`. Immune to left or right content shifts.
   * **Right (Utilities):** `[ 🌙 / ☀️ ]` Theme Toggle, followed by `[ ⚙ ]` Settings button (**right-most element**).
4. **Canvas Autonomy:** No demo controls or external toggles. All action bars, settlement period controls (`< October 2026 >`), and the primary CTA (`+ Record Transaction`) live inside the canvas body below the ribbon.

---

## 2. Design Tokens & Styling System

Claude must configure Tailwind CSS (`tailwind.config.js`) and CSS variables (`frontend/src/index.css`) matching these exact specifications:

### 2.1 CSS Variables & Palette

```css
:root, [data-theme="dark"] {
  color-scheme: dark;
  --bg-base: #0B0F19;           /* Canvas background */
  --bg-surface: #111726;        /* Cards & topbar surface */
  --bg-elevated: #182238;       /* Inputs, pills, active containers */
  --bg-muted: #1F2C47;          /* Subdued backgrounds & borders */
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-medium: rgba(255, 255, 255, 0.16);
  --text-primary: #F8FAFC;
  --text-secondary: #94A3B8;
  --text-tertiary: #64748B;
  --accent: #3B82F6;            /* Primary Blue-500 */
  --accent-hover: #2563EB;
  --accent-glow: rgba(59, 130, 246, 0.18);
  --success: #10B981;           /* Emerald-500 */
  --success-glow: rgba(16, 185, 129, 0.14);
  --danger: #F43F5E;            /* Rose-500 */
  --danger-glow: rgba(244, 63, 94, 0.14);
  --warning: #F59E0B;           /* Amber-500 */
}

[data-theme="light"] {
  color-scheme: light;
  --bg-base: #F8FAFC;
  --bg-surface: #FFFFFF;
  --bg-elevated: #F1F5F9;
  --bg-muted: #E2E8F0;
  --border-subtle: #E2E8F0;
  --border-medium: #CBD5E1;
  --text-primary: #0F172A;
  --text-secondary: #475569;
  --text-tertiary: #64748B;
  --accent: #2563EB;
  --accent-hover: #1D4ED8;
  --accent-glow: rgba(37, 99, 235, 0.12);
  --success: #059669;
  --success-glow: rgba(5, 150, 105, 0.12);
  --danger: #E11D48;
  --danger-glow: rgba(225, 29, 72, 0.12);
  --warning: #D97706;
}
```

### 2.2 Typography Rules
* **Font Family:** Google Fonts `Lora` (serif) loaded via `@import` or `<link>`:
  ```html
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400..700;1,400..700&display=swap" rel="stylesheet">
  ```
* **Global Base:**
  ```css
  body, button, input, select, textarea {
    font-family: 'Lora', Georgia, 'Times New Roman', serif;
    letter-spacing: -0.005em;
  }
  ```
* **Monospace Tabular Class (`.tabular`):**
  ```css
  .tabular {
    font-variant-numeric: tabular-nums;
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  }
  ```
  *Use strictly on:* Currency (e.g., `₹48,500`), SHA-256 hashes (`#a8f3...e91`), dates, kilowatt units (`148 kWh`), and percentages.

---

## 3. Structural Layout & Master Topbar Ribbon Blueprint

The application shell consists of the **Master Topbar Ribbon** and the **Application Canvas**:

```
+-------------------------------------------------------------------------------------------------------+
| [Emblem] Flat 402, Lakeview Residency        [ Month Hub | Ledger | Statements ]        [🌙/☀️] [⚙]  |  <- Master Ribbon
+-------------------------------------------------------------------------------------------------------+
|                                                                                                       |
| Settlement Period: < October 2026 >                                            [+ Record Transaction] |  <- Canvas Toolbar
|                                                                                                       |
| +---------------------------------------------------------------------------------------------------+ |
| | 1. Costs Logged (₹)  |  2. Tapo Metering (⚡)  |  3. Hashed Statement [ACTIVE]  |  4. Close (🔒)  | |  <- 4 Stages Grid
| +---------------------------------------------------------------------------------------------------+ |
|                                                                                                       |
| +------------------------------------------------------------+  +-----------------------------------+ |
| | Suggested Peer-to-Peer Transfers (Greedy UPI Simplification)|  | Resident Balances (Net Checksum ₹0)| |
| |   * Bob ➔ Alice: ₹4,120     [Copy UPI Note] [Mark Settle]  |  |   * Alice: +₹6,880                | |
| |   * Chris ➔ Alice: ₹2,760   [Copy UPI Note] [Mark Settle]  |  |   * Bob:   -₹4,120                | |
| +------------------------------------------------------------+  |   * Chris: -₹2,760                | |
|                                                                 +-----------------------------------+ |
+-------------------------------------------------------------------------------------------------------+
```

### 3.1 Master Topbar Ribbon Markup Reference

```tsx
<header className="relative bg-[var(--bg-surface)] border-b border-[var(--border-subtle)] px-4 sm:px-6 py-3 flex items-center justify-between sticky top-0 z-40 backdrop-blur-md min-h-[57px]">
  
  {/* LEFT: Flat Emblem & Name */}
  <div className="flex items-center gap-3 shrink-0 min-w-0">
    <div className="flex w-8 h-8 rounded-lg bg-gradient-to-tr from-blue-700 to-indigo-500 text-white items-center justify-center shrink-0 shadow-xs">
      <BuildingIcon className="w-4 h-4" />
    </div>
    <span className="font-bold text-sm text-[var(--text-primary)] truncate">
      {apartment?.name || 'Flat 402, Lakeview Residency'}
    </span>
  </div>

  {/* CENTER: Navigation Menu (Strictly Centered to the Ribbon Midpoint) */}
  <nav className="absolute left-1/2 -translate-x-1/2 flex items-center justify-center gap-1 bg-[var(--bg-elevated)] p-1 rounded-xl border border-[var(--border-subtle)] text-xs font-medium shadow-xs shrink-0 pointer-events-auto" aria-label="Main Navigation">
    <NavLink to="/" className={({ isActive }) => `px-4 py-1.5 rounded-lg transition-colors whitespace-nowrap cursor-pointer ${isActive ? 'bg-[var(--accent)] text-white font-semibold shadow-xs' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
      Month Hub
    </NavLink>
    <NavLink to="/transactions" className={({ isActive }) => `px-4 py-1.5 rounded-lg transition-colors whitespace-nowrap cursor-pointer ${isActive ? 'bg-[var(--accent)] text-white font-semibold shadow-xs' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
      Activity Ledger
    </NavLink>
    <NavLink to="/statements" className={({ isActive }) => `px-4 py-1.5 rounded-lg transition-colors whitespace-nowrap cursor-pointer ${isActive ? 'bg-[var(--accent)] text-white font-semibold shadow-xs' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
      Statements & Power
    </NavLink>
  </nav>

  {/* RIGHT: Utility Icons (Setting right-most, then Light/Dark) */}
  <div className="flex items-center gap-1.5 shrink-0">
    {/* Theme Toggle Button */}
    <button onClick={toggleTheme} className="p-2 rounded-xl text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer active:scale-95" title="Toggle Theme" aria-label="Toggle Theme">
      {isDark ? <MoonIcon className="w-4 h-4 text-amber-400" /> : <SunIcon className="w-4 h-4 text-amber-500" />}
    </button>

    {/* Settings Button (Right-most!) */}
    <NavLink to="/setup" className="p-2 rounded-xl text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer active:scale-95" title="Settings & Setup" aria-label="Settings">
      <SettingsIcon className="w-4 h-4 shrink-0" />
    </NavLink>
  </div>
</header>
```

---

## 4. Canvas Body & Primary Screen Specifications

### 4.1 Canvas Action Bar (Settlement Context & Primary CTA)
Lives inside `<main className="p-4 sm:p-6 md:p-8 space-y-6 max-w-6xl w-full mx-auto min-w-0">`:
* **Left:** Period Selector:
  * Pill container: `flex items-center bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl px-2.5 py-1.5 gap-2 shadow-xs`.
  * Previous/Next month chevrons: `shiftMonth(month, -1)` / `shiftMonth(month, 1)`.
  * Calendar icon + month text: `tabular text-xs font-bold`.
* **Right:** Primary CTA:
  * `+ Record Transaction` button: `bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-semibold px-4 py-2.5 rounded-xl transition-all shadow-xs flex items-center gap-2 cursor-pointer active:scale-[0.98]`.

### 4.2 Month Hub (Screen 1)
1. **Lifecycle Progression Banner:**
   * 4-Stage Workflow Grid: `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3`.
   * **Stage 1 (Logging Costs):** Emerald glow (`border-emerald-500/30 bg-[var(--success-glow)]`), checkmark icon, summary total spend.
   * **Stage 2 (Utility Metering):** Emerald glow, electricity bill summary (kWh & ₹).
   * **Stage 3 (Hashed Statement):** Active stage! `border-2 border-blue-500 bg-[var(--accent-glow)] shadow-xs`.
     * **Crucial Responsive Guard:** The `[ACTIVE]` badge must have `shrink-0 text-[9px] uppercase tracking-wider font-extrabold px-1.5 py-0.5 bg-blue-500 text-white rounded`. The title must have `min-w-0 truncate` to prevent wrapping or overflowing on smaller viewports.
   * **Stage 4 (Permanent Close):** Subdued lock state (`border-[var(--border-subtle)] bg-[var(--bg-elevated)] opacity-70`).
2. **Settlements & Balances Grid (`grid grid-cols-1 lg:grid-cols-3 gap-6`):**
   * **Suggested Peer-to-Peer Transfers (`lg:col-span-2`):**
     * Greedy debt-simplification rows (`B ➔ A`, `C ➔ A`).
     * Resident avatar circles with initials.
     * Buttons: `Copy UPI Note` (with live copied tooltip) and `Mark Settle`.
     * WhatsApp Share Summary modal button (`bg-emerald-600 hover:bg-emerald-700`).
   * **Resident Net Balances Card (`lg:col-span-1`):**
     * Net position pills (`+₹6,880` in green, `-₹4,120` in red).
     * Checksum verification footer: `Net Checksum: ₹0.00` with verification badge.

### 4.3 Activity Ledger (Screen 2)
1. **Summary Stat Cards:** Total Spent, Split Expenses, Utilities, Direct Credits.
2. **Table Container:**
   * Table must be wrapped in `<div className="overflow-x-auto min-w-0">` with `min-w-[620px]` on the table itself.
   * Columns: `Date`, `Description`, `Category`, `Paid By`, `Split Ratio`, `Amount`, `Receipt/Audit`.
   * Amounts formatted with `.tabular`.

### 4.4 Statements & Power (Screen 3)
1. **Tapo Smart Meter Allocation Card:**
   * Formula breakdown: Fixed Charges (`₹300`), Dynamic Consumption (`148 kWh @ ₹9.5/unit`).
   * Per-room card breakdown with active load status.
2. **Cryptographic SHA-256 Vault Card:**
   * Sealed snapshot with copyable hash (`tabular text-xs`).
   * "Download Immutable PDF/CSV Statement" action.

### 4.5 Record Transaction Slide-Over Drawer
* Triggered by `+ Record Transaction`.
* Drawer from right side (`fixed inset-y-0 right-0 w-full max-w-md bg-[var(--bg-surface)] shadow-2xl z-50`).
* Inputs: Description, Category (Groceries, Wi-Fi, Electricity, Maid, Maintenance), Amount, Paid By, Split Mode (Equal, Custom Shares).
* Participant Share preview cards.
* Client-side validation: Custom shares must exactly equal the total amount.

---

## 5. Responsive Behavior & Collision Defense Checklist

Claude must strictly observe these layout guards:
* [x] **No Text Overflow:** All flex headers and titles must include `min-w-0 truncate`.
* [x] **No Icon Shrinking:** All SVG icons and status pills must have `shrink-0`.
* [x] **Midpoint Anchor:** Never replace `absolute left-1/2 -translate-x-1/2` with flex spacers or margins. Only absolute centering guarantees true ribbon centering regardless of text length on the left or buttons on the right.
* [x] **Canvas Autonomy:** No elements outside the canvas or header.
* [x] **Theme Persistence:** Store dark/light preference in `localStorage.getItem('aem.theme')` and apply to `<html data-theme="...">`.
* [x] **Universal Table Header Center Alignment:** In all tables created across all pages, every column header (`<th>`) must be center-aligned (`text-align: center;`). No exceptions for text, amounts, or actions.
* [x] **Mandatory Field-Level Form Validation:** Every modal, drawer, or inline form must validate all fields client-side (non-empty required fields, email, phone, UPI VPA syntax, non-negative amounts, date ranges) with inline error feedback and visual cues before submission.


---

## 6. Execution Plan for Claude

1. **Step 1: Install Dependencies (`frontend/`)**
   ```bash
   npm install -D tailwindcss postcss autoprefixer
   npm install lucide-react clsx tailwind-merge
   npx tailwindcss init -p
   ```
2. **Step 2: Configure Tailwind & Google Fonts**
   * Configure `tailwind.config.js` to scan `./src/**/*.{js,ts,jsx,tsx}` and `./index.html`.
   * Add Google Fonts `Lora` to `index.html`.
   * Update `frontend/src/index.css` with the CSS tokens and base styles listed in Section 2.
3. **Step 3: Refactor `Layout.tsx`**
   * Replace the legacy sidebar with the approved **Master Topbar Ribbon**.
   * Replace old topbar with the centered tabs and right-hand utilities (`[🌙/☀️]`, `[⚙]`).
   * Render the Canvas Action Bar with the month picker and `+ Record Transaction` CTA.
4. **Step 4: Align Core Views (`Dashboard.tsx`, `Transactions.tsx`, `Statements.tsx`)**
   * Upgrade components to adopt the modern cards, tabular figures, and responsive grids.
5. **Step 5: Automated Verification**
   * Run Vitest: `npm test` -> Ensure all **68/68 tests pass**.
   * Run Pytest: `.venv/bin/pytest` -> Ensure all **289/289 tests pass**.

