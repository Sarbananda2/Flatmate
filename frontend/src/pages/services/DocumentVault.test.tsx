/**
 * The Document Vault: the listing (details only, never contents), the upload
 * form's checks on type, size, title and category, the multipart request it
 * sends, and the links that preview and download a file.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DocumentVaultSection, MAX_DOCUMENT_BYTES, fileError, formatBytes } from './DocumentVault'
import { ToastProvider } from '../../components/Toast'
import { installFetchStub, type Recorded } from '../../test/server'
import type { HouseholdDocument } from '../../api/types'

const HASH = 'ab'.repeat(32)

const LEASE: HouseholdDocument = {
  doc_id: 'doc-1',
  apartment_id: 'home',
  title: 'Rental agreement 2026',
  category: 'lease_agreement',
  filename: 'Rental_Agreement.pdf',
  content_type: 'application/pdf',
  size_bytes: 2516582,
  sha256: HASH,
  uploaded_by_resident_id: null,
  notes: 'Signed by all three',
  uploaded_at: '2026-10-12T10:00:00+05:30',
}
const HANDOVER: HouseholdDocument = {
  ...LEASE,
  doc_id: 'doc-2',
  title: 'Inventory handover',
  category: 'inventory_handover',
  filename: 'handover.docx',
  content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  size_bytes: 20480,
  notes: null,
}

let stub: { calls: Recorded[]; restore: () => void } | null = null
afterEach(() => {
  stub?.restore()
  stub = null
})

function open(items: HouseholdDocument[] = [LEASE, HANDOVER], extra = {}) {
  stub = installFetchStub({
    routes: {
      '/api/property/documents': {
        items,
        total: items.length,
        total_bytes: items.reduce((sum, doc) => sum + doc.size_bytes, 0),
        categories_on_file: [],
      },
      ...extra,
    } as never,
  })
  render(
    <ToastProvider>
      <DocumentVaultSection />
    </ToastProvider>,
  )
}

const pdf = (name = 'Rental_Agreement-2026.pdf', size = 1000) =>
  new File([new Uint8Array(size)], name, { type: 'application/pdf' })

describe('fileError', () => {
  it('accepts the vault’s file types up to the limit', () => {
    expect(fileError(pdf())).toBeNull()
    expect(fileError(pdf('scan.JPEG'))).toBeNull()
    expect(fileError(null)).toBe('Choose a file to upload.')
    expect(fileError(pdf('setup.exe'))).toMatch(/Upload a PDF/)
    expect(fileError(pdf('lease', 10))).toMatch(/Upload a PDF/)
    expect(fileError(pdf('empty.pdf', 0))).toBe('The file is empty.')
    expect(fileError(pdf('big.pdf', MAX_DOCUMENT_BYTES + 1))).toBe(
      'The file is larger than the 10 MB limit.',
    )
  })

  it('formats sizes', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(20480)).toBe('20.0 KB')
    expect(formatBytes(2516582)).toBe('2.4 MB')
  })
})

describe('DocumentVaultSection', () => {
  it('lists documents with centred headers, categories and file details', async () => {
    open()
    const table = await screen.findByRole('table', { name: 'Documents' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Document',
      'Category',
      'File',
      'Uploaded',
      'SHA-256',
      'Actions',
    ])
    for (const header of headers) expect(header).toHaveClass('text-center')

    const lease = within(table).getByRole('row', { name: /Rental agreement 2026/ })
    expect(lease).toHaveTextContent('Lease Agreement')
    expect(lease).toHaveTextContent('Rental_Agreement.pdf')
    expect(lease).toHaveTextContent('2.4 MB')
    expect(lease).toHaveTextContent('12 Oct 2026')
    expect(lease).toHaveTextContent(`${HASH.slice(0, 12)}…`)
    expect(within(lease).getByRole('link', { name: 'Download Rental agreement 2026' })).toHaveAttribute(
      'href',
      '/api/property/documents/doc-1/download',
    )
    expect(within(lease).getByRole('link', { name: 'Preview Rental agreement 2026' })).toHaveAttribute(
      'href',
      '/api/property/documents/doc-1/download?inline=true',
    )
    // A Word file is only ever downloaded.
    const handover = within(table).getByRole('row', { name: /Inventory handover/ })
    expect(within(handover).queryByRole('link', { name: /Preview/ })).toBeNull()
  })

  it('refuses an upload until the file, title and category are right', async () => {
    open([])
    await userEvent.click(screen.getAllByRole('button', { name: /Upload/ })[0])
    const dialog = screen.getByRole('dialog')
    const submit = within(dialog).getByRole('button', { name: 'Upload Document' })
    await userEvent.click(submit)
    expect(within(dialog).getByText('Choose a file to upload.')).toBeInTheDocument()
    expect(within(dialog).getByText('Give the document a title.')).toBeInTheDocument()
    expect(within(dialog).getByText('Choose a category.')).toBeInTheDocument()

    await userEvent.upload(within(dialog).getByLabelText('Choose file'), pdf('virus.exe'), {
      applyAccept: false,
    })
    expect(within(dialog).getByText(/Upload a PDF, an image/)).toBeInTheDocument()
    expect(stub!.calls.some((call) => call.method === 'POST')).toBe(false)
  })

  it('uploads a dropped file as a multipart form', async () => {
    open([], { 'POST /api/property/documents': LEASE })
    await userEvent.click(screen.getAllByRole('button', { name: /Upload/ })[0])
    const dialog = screen.getByRole('dialog')
    fireEvent.drop(within(dialog).getByTestId('document-dropzone'), {
      dataTransfer: { files: [pdf()] },
    })
    expect(within(dialog).getByTestId('document-dropzone')).toHaveTextContent(
      'Rental_Agreement-2026.pdf',
    )
    // The title is suggested from the file name.
    expect(within(dialog).getByLabelText(/Title/)).toHaveValue('Rental Agreement 2026')
    await userEvent.selectOptions(within(dialog).getByLabelText(/Category/), 'lease_agreement')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Upload Document' }))

    await waitFor(() =>
      expect(stub!.calls.find((call) => call.method === 'POST')).toBeDefined(),
    )
    expect(stub!.calls.find((call) => call.method === 'POST')!.body).toEqual({
      file: { name: 'Rental_Agreement-2026.pdf', type: 'application/pdf', size: 1000 },
      title: 'Rental Agreement 2026',
      category: 'lease_agreement',
    })
    expect(await screen.findByText('Rental agreement 2026 uploaded')).toBeInTheDocument()
  })

  it('deletes a document after confirmation, showing its hash', async () => {
    open([LEASE], { 'DELETE /api/property/documents/doc-1': {} })
    await userEvent.click(await screen.findByRole('button', { name: 'Delete Rental agreement 2026' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent(HASH)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete Document' }))
    await waitFor(() =>
      expect(stub!.calls.find((call) => call.method === 'DELETE')?.path).toBe(
        '/api/property/documents/doc-1',
      ),
    )
    expect(await screen.findByText('Rental agreement 2026 deleted')).toBeInTheDocument()
  })
})
