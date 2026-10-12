/**
 * Presentational guarantees that matter for a financial table.
 *
 * The important ones: an amount never relies on colour alone, a net balance
 * says in words which way the money goes, and every async view has a real
 * loading, error, and empty state.
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError } from '../api/client'
import {
  AsyncSection,
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  Money,
  NetBalance,
  Pagination,
  StatementStatusBadge,
} from './Primitives'

describe('Money', () => {
  it('shows a minus sign for a negative amount, not only a colour class', () => {
    render(<Money paise={-12345} tone="auto" />)
    expect(screen.getByText('−₹123.45')).toBeInTheDocument()
  })

  it('shows an explicit plus when asked, so a balance direction is readable', () => {
    render(<Money paise={12345} tone="auto" sign="always" />)
    expect(screen.getByText('+₹123.45')).toBeInTheDocument()
  })

  it('renders a missing amount as a dash', () => {
    render(<Money paise={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('marks the direction with a class as well as the sign', () => {
    const { container } = render(<Money paise={-1} tone="auto" />)
    expect(container.querySelector('.amount--owed-by')).not.toBeNull()
  })
})

describe('NetBalance', () => {
  it('says in words that the household owes a resident', () => {
    render(<NetBalance paise={5000} name="Alice" />)
    expect(screen.getByText(/the household owes Alice/)).toBeInTheDocument()
  })

  it('says in words that the resident owes the household', () => {
    render(<NetBalance paise={-5000} name="Bob" />)
    expect(screen.getByText(/Bob owes the household/)).toBeInTheDocument()
  })

  it('says settled at zero', () => {
    render(<NetBalance paise={0} name="Chris" />)
    expect(screen.getByText('settled')).toBeInTheDocument()
  })
})

describe('Badge', () => {
  it('carries a glyph so status is not conveyed by colour alone', () => {
    const { container } = render(<Badge tone="ok">Active</Badge>)
    expect(container.querySelector('.badge')?.getAttribute('data-glyph')).toBe('✓')
  })

  it('labels every statement status in words', () => {
    const { rerender } = render(<StatementStatusBadge status="draft" />)
    expect(screen.getByText('Draft')).toBeInTheDocument()
    rerender(<StatementStatusBadge status="finalized_correction_window" />)
    expect(screen.getByText('Finalized · correctable')).toBeInTheDocument()
    rerender(<StatementStatusBadge status="closed" />)
    expect(screen.getByText('Closed')).toBeInTheDocument()
  })
})

describe('AsyncSection', () => {
  const base = { data: null, error: null, initialLoading: false, reload: () => {} }

  it('shows a loading state while loading for the first time', () => {
    render(
      <AsyncSection resource={{ ...base, initialLoading: true }} loadingLabel="Loading expenses…">
        {() => <div>content</div>}
      </AsyncSection>,
    )
    expect(screen.getByText('Loading expenses…')).toBeInTheDocument()
  })

  it('shows the server message and a retry on error', async () => {
    const reload = vi.fn()
    render(
      <AsyncSection
        resource={{
          ...base,
          reload,
          error: new ApiError(409, {
            code: 'closed_statement',
            message: 'That statement is closed.',
            details: {},
          }),
        }}
      >
        {() => <div>content</div>}
      </AsyncSection>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('That statement is closed.')
    expect(screen.getByText('closed_statement')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('shows the empty state when the data is empty', () => {
    render(
      <AsyncSection
        resource={{ ...base, data: { items: [] } }}
        isEmpty={(data) => data.items.length === 0}
        empty={<EmptyState title="No expenses yet" />}
      >
        {() => <div>content</div>}
      </AsyncSection>,
    )
    expect(screen.getByText('No expenses yet')).toBeInTheDocument()
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('shows a welcome instead of an error when nothing is set up yet', () => {
    render(
      <AsyncSection
        resource={{
          ...base,
          error: new ApiError(404, {
            code: 'no_apartment',
            message: 'No apartment has been set up yet. Create one first.',
            details: {},
          }),
        }}
      >
        {() => <div>content</div>}
      </AsyncSection>,
    )
    expect(screen.getByText(/let's set up the household/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to setup' })).toHaveAttribute('href', '/setup')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders the content when there is data', () => {
    render(
      <AsyncSection resource={{ ...base, data: { items: [1] } }}>
        {(data) => <div>{data.items.length} row</div>}
      </AsyncSection>,
    )
    expect(screen.getByText('1 row')).toBeInTheDocument()
  })
})

describe('Pagination', () => {
  it('reports the row count without controls when everything fits', () => {
    render(<Pagination total={5} limit={25} offset={0} onChange={() => {}} noun="expenses" />)
    expect(screen.getByText('5 expenses')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Next/ })).not.toBeInTheDocument()
  })

  it('pages forward and back, disabling the edges', async () => {
    const onChange = vi.fn()
    render(<Pagination total={120} limit={50} offset={50} onChange={onChange} />)
    expect(screen.getByText('51–100 of 120 rows')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Next/ }))
    expect(onChange).toHaveBeenCalledWith(100)
    await userEvent.click(screen.getByRole('button', { name: /Previous/ }))
    expect(onChange).toHaveBeenCalledWith(0)
  })

  it('disables Previous on the first page', () => {
    render(<Pagination total={120} limit={50} offset={0} onChange={() => {}} />)
    expect(screen.getByRole('button', { name: /Previous/ })).toBeDisabled()
  })
})

describe('LoadingState and ErrorState', () => {
  it('announces loading to assistive technology', () => {
    render(<LoadingState />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('announces an error as an alert', () => {
    render(<ErrorState error={new Error('boom')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('boom')
  })
})

describe('Modal', () => {
  it('stops the page behind it scrolling, and gives the scroll back on close', () => {
    document.body.style.overflow = 'auto'
    const { unmount } = render(
      <Modal title="Record Payment" onClose={() => {}}>
        <p>Body</p>
      </Modal>,
    )
    expect(screen.getByRole('dialog', { name: 'Record Payment' })).toBeInTheDocument()
    expect(document.body.style.overflow).toBe('hidden')
    unmount()
    // Whatever the page had before, not a blanket reset.
    expect(document.body.style.overflow).toBe('auto')
    document.body.style.overflow = ''
  })

  it('keeps the page locked while a second dialog is still open', () => {
    const first = render(
      <Modal title="First" onClose={() => {}}>
        <p>One</p>
      </Modal>,
    )
    const second = render(
      <Modal title="Second" onClose={() => {}}>
        <p>Two</p>
      </Modal>,
    )
    second.unmount()
    expect(document.body.style.overflow).toBe('hidden')
    first.unmount()
    expect(document.body.style.overflow).toBe('')
  })
})
