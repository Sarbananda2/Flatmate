/**
 * Tailwind configuration for the Apartment Expense Manager.
 *
 * The palette lives in `src/index.css` as CSS custom properties so a single
 * `data-theme` attribute on <html> repaints the whole app without a rebuild.
 * The names below are thin aliases onto those variables, which keeps the
 * markup readable (`bg-surface`) while leaving the variables authoritative.
 *
 * Because the tokens are authored as hex and rgba rather than bare channel
 * triples, the opacity modifier (`bg-surface/50`) is not available on them.
 * Use the built-in palette (`border-emerald-500/30`) where a tint is needed.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],

  // The theme is carried by <html data-theme="dark|light">, not by a class, so
  // any `dark:` utility must follow that attribute.
  darkMode: ['selector', '[data-theme="dark"]'],

  theme: {
    extend: {
      colors: {
        // Not `base`: that would make `text-base` set a colour as well as a size.
        canvas: 'var(--bg-base)',
        surface: 'var(--bg-surface)',
        elevated: 'var(--bg-elevated)',
        muted: 'var(--bg-muted)',

        'border-subtle': 'var(--border-subtle)',
        'border-medium': 'var(--border-medium)',

        primary: 'var(--text-primary)',
        secondary: 'var(--text-secondary)',
        tertiary: 'var(--text-tertiary)',

        accent: {
          DEFAULT: 'var(--accent)',
          hover: 'var(--accent-hover)',
          glow: 'var(--accent-glow)',
        },
        success: {
          DEFAULT: 'var(--success)',
          glow: 'var(--success-glow)',
        },
        danger: {
          DEFAULT: 'var(--danger)',
          glow: 'var(--danger-glow)',
        },
        warning: 'var(--warning)',
      },

      borderColor: {
        subtle: 'var(--border-subtle)',
        medium: 'var(--border-medium)',
      },

      fontFamily: {
        // Lora is the single UI typeface: headers, body copy, and controls.
        sans: ['Lora', 'Georgia', '"Times New Roman"', 'serif'],
        serif: ['Lora', 'Georgia', '"Times New Roman"', 'serif'],
        // Reserved for monetary figures, hashes, kWh, and dates via `.tabular`.
        mono: ['ui-monospace', '"SF Mono"', '"JetBrains Mono"', 'Menlo', 'Consolas', 'monospace'],
      },

      boxShadow: {
        // The design language leans on a single hairline shadow. `xs` is a
        // Tailwind v4 name the specification uses throughout, so it is
        // declared here rather than rewritten at every call site.
        xs: '0 1px 2px rgba(8, 12, 24, 0.08)',
      },
    },
  },

  plugins: [],
}
