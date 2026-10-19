/**
 * Card header actions (Rule U9): one action is a 32×32 icon button named by
 * its tooltip; two or more fold into a "More options" menu that opens on the
 * first action, moves with the arrow keys, and closes on Escape, an outside
 * click, or choosing an action.
 */
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Pencil, Settings, Trash2, Upload } from 'lucide-react'
import { CardActions } from './ui'
import { Card } from './Primitives'

describe('CardActions', () => {
  it('renders nothing without actions', () => {
    const { container } = render(<CardActions actions={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('makes a single action an icon button named by its tooltip', async () => {
    const onClick = vi.fn()
    render(<CardActions actions={[{ label: 'Edit contact', icon: Pencil, onClick }]} />)
    const button = screen.getByRole('button', { name: 'Edit contact' })
    expect(button).toHaveAttribute('title', 'Edit contact')
    expect(button.textContent).toBe('')
    expect(button).toHaveClass('w-8', 'h-8', 'shrink-0')
    await userEvent.click(button)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('makes a single action that goes somewhere a link', () => {
    render(
      <MemoryRouter>
        <CardActions actions={[{ label: 'Manage rooms', icon: Settings, to: '/housing?tab=rooms' }]} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Manage rooms' })).toHaveAttribute('href', '/housing?tab=rooms')
    expect(screen.queryByRole('button')).toBeNull()
  })

  describe('with two or more actions', () => {
    function menu() {
      const edit = vi.fn()
      const remove = vi.fn()
      const upload = vi.fn()
      render(
        <div>
          <p>Outside</p>
          <CardActions
            actions={[
              { label: 'Edit', icon: Pencil, onClick: edit },
              { label: 'Upload', icon: Upload, onClick: upload, disabled: true },
              { label: 'Delete', icon: Trash2, onClick: remove, tone: 'danger' },
            ]}
          />
        </div>,
      )
      return { edit, remove, upload, trigger: screen.getByRole('button', { name: 'More options' }) }
    }

    it('folds them into one "More options" button that opens a menu on its first action', async () => {
      const { trigger } = menu()
      expect(screen.getAllByRole('button')).toHaveLength(1)
      expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
      expect(trigger).toHaveAttribute('aria-expanded', 'false')
      expect(screen.queryByRole('menu')).toBeNull()

      await userEvent.click(trigger)
      expect(trigger).toHaveAttribute('aria-expanded', 'true')
      const list = screen.getByRole('menu', { name: 'More options' })
      expect(within(list).getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
        'Edit',
        'Upload',
        'Delete',
      ])
      expect(within(list).getByRole('menuitem', { name: 'Edit' })).toHaveFocus()
      expect(within(list).getByRole('menuitem', { name: 'Upload' })).toHaveAttribute('aria-disabled', 'true')
    })

    it('moves with the arrow keys, skipping a disabled action, and runs the one chosen', async () => {
      const { remove, upload } = menu()
      await userEvent.click(screen.getByRole('button', { name: 'More options' }))
      await userEvent.keyboard('{ArrowDown}')
      expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()
      await userEvent.keyboard('{ArrowDown}')
      expect(screen.getByRole('menuitem', { name: 'Edit' })).toHaveFocus()
      await userEvent.keyboard('{End}')
      expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()

      await userEvent.click(screen.getByRole('menuitem', { name: 'Upload' }))
      expect(upload).not.toHaveBeenCalled()
      expect(screen.getByRole('menu')).toBeInTheDocument()

      await userEvent.keyboard('{End}{Enter}')
      expect(remove).toHaveBeenCalledOnce()
      expect(screen.queryByRole('menu')).toBeNull()
    })

    it('closes on Escape, handing the keyboard back to the button', async () => {
      const { trigger } = menu()
      await userEvent.click(trigger)
      await userEvent.keyboard('{Escape}')
      expect(screen.queryByRole('menu')).toBeNull()
      expect(trigger).toHaveFocus()
      expect(trigger).toHaveAttribute('aria-expanded', 'false')
    })

    it('closes on a click outside it, running nothing', async () => {
      const { edit, remove } = menu()
      await userEvent.click(screen.getByRole('button', { name: 'More options' }))
      await userEvent.click(screen.getByText('Outside'))
      expect(screen.queryByRole('menu')).toBeNull()
      expect(edit).not.toHaveBeenCalled()
      expect(remove).not.toHaveBeenCalled()
    })
  })
})

describe('Card header actions', () => {
  it('puts the card\'s menu actions in its header, beside the title', () => {
    render(
      <Card id="demo" title="Demo" hint="A long hint" menuActions={[{ label: 'Edit demo', icon: Pencil }]}>
        Body
      </Card>,
    )
    const header = screen.getByRole('heading', { name: 'Demo' }).closest('header')!
    expect(within(header).getByRole('button', { name: 'Edit demo' })).toBeInTheDocument()
    expect(header.querySelector('.card-header-actions')).toContainElement(
      screen.getByRole('button', { name: 'Edit demo' }),
    )
  })
})
