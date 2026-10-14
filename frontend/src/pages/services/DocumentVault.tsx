/**
 * The Document Vault tab of Household & Property.
 *
 * Agreements, NOCs and verifications, kept in the database so a backup holds
 * them too. The list shows only each file's details; a file is fetched when it
 * is opened or downloaded, and the backend checks its SHA-256 every time. The
 * upload form checks the file's type and size before sending it.
 */
import { useRef, useState } from 'react'
import { Download, Eye, FileText, Fingerprint, Trash2, Upload, UploadCloud } from 'lucide-react'
import { AsyncSection, ErrorNotice, Modal } from '../../components/Primitives'
import { SelectField, TextAreaField, TextField } from '../../components/Form'
import {
  BUTTON_PRIMARY,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  CopyButton,
  EmptyBlock,
  Panel,
  Pill,
  type Tone,
} from '../../components/ui'
import { useToast } from '../../components/Toast'
import { api } from '../../api/client'
import { type Resource, useMutation, useResource } from '../../lib/useApi'
import { formatDate } from '../../lib/format'
import type { DocumentCategory, DocumentList, HouseholdDocument } from '../../api/types'

export const DOCUMENTS = '/api/property/documents'
export const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
/** The file types the backend accepts, by extension. */
export const ACCEPTED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.doc', '.docx', '.txt']
const INLINE_TYPES = new Set(['application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'text/plain'])

const TH = 'pb-3 px-2 font-bold text-center'
const TABLE_HEAD =
  'border-b border-[var(--border-subtle)] text-[var(--text-tertiary)] uppercase tracking-wider text-[10px] font-bold'

export const CATEGORY_LABELS: Record<DocumentCategory, string> = {
  lease_agreement: 'Lease Agreement',
  society_noc: 'Society NOC',
  police_verification: 'Police Verification',
  inventory_handover: 'Inventory Handover',
  other: 'Other',
}

const CATEGORY_TONES: Record<DocumentCategory, Tone> = {
  lease_agreement: 'accent',
  society_noc: 'success',
  police_verification: 'warning',
  inventory_handover: 'neutral',
  other: 'neutral',
}

export function useDocuments(): Resource<DocumentList> {
  return useResource<DocumentList>(DOCUMENTS)
}

/** `2516582` -> `2.4 MB`. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function downloadUrl(doc: HouseholdDocument, inline = false): string {
  return `${DOCUMENTS}/${encodeURIComponent(doc.doc_id)}/download${inline ? '?inline=true' : ''}`
}

/** Why a file cannot be uploaded, or null when it can. */
export function fileError(file: File | null): string | null {
  if (!file) return 'Choose a file to upload.'
  const dot = file.name.lastIndexOf('.')
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  if (!ACCEPTED_EXTENSIONS.includes(extension)) {
    return 'Upload a PDF, an image (PNG, JPEG, WebP), a Word document or a text file.'
  }
  if (file.size === 0) return 'The file is empty.'
  if (file.size > MAX_DOCUMENT_BYTES) return 'The file is larger than the 10 MB limit.'
  return null
}

/** A title from a file name: `Rental_Agreement-2026.pdf` -> `Rental Agreement 2026`. */
function titleFrom(filename: string): string {
  return filename.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim()
}

/* ---------------------------------------------------------------- the tab -- */

export function DocumentVaultSection() {
  const documents = useDocuments()
  const [uploading, setUploading] = useState(false)
  const [deleting, setDeleting] = useState<HouseholdDocument | null>(null)
  const toast = useToast()

  return (
    <Panel
      title="Document Vault"
      icon={<FileText className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}
      hint={
        documents.data && documents.data.total > 0
          ? `${documents.data.total} document${documents.data.total === 1 ? '' : 's'} · ${formatBytes(documents.data.total_bytes)}, kept in the database and included in every backup`
          : 'Kept in the database, so every backup includes them.'
      }
      actions={
        <button type="button" className={BUTTON_PRIMARY} onClick={() => setUploading(true)}>
          <Upload className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          Upload Document
        </button>
      }
    >
      <AsyncSection
        resource={documents}
        isEmpty={(data) => data.items.length === 0}
        empty={
          <EmptyBlock
            title="No documents yet"
            body="Upload the rental agreement, the society NOC and police verification so they are kept with the household's records."
            action={
              <button type="button" className={BUTTON_PRIMARY} onClick={() => setUploading(true)}>
                Upload the first document
              </button>
            }
          />
        }
      >
        {(data) => (
          <div className="overflow-x-auto min-w-0">
            <table className="w-full text-xs min-w-[760px] border-collapse">
              <caption className="sr-only">Documents</caption>
              <thead>
                <tr className={TABLE_HEAD}>
                  <th scope="col" className={TH}>Document</th>
                  <th scope="col" className={TH}>Category</th>
                  <th scope="col" className={TH}>File</th>
                  <th scope="col" className={TH}>Uploaded</th>
                  <th scope="col" className={TH}>SHA-256</th>
                  <th scope="col" className={TH}>Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)] font-medium">
                {data.items.map((doc) => (
                  <tr key={doc.doc_id} className="hover:bg-[var(--bg-elevated)] transition-colors">
                    <th scope="row" className="py-3 px-2 text-left font-semibold">
                      {doc.title}
                      {doc.notes && (
                        <div className="font-normal text-[11px] text-[var(--text-secondary)]">
                          {doc.notes}
                        </div>
                      )}
                    </th>
                    <td className="py-3 px-2 text-center">
                      <Pill tone={CATEGORY_TONES[doc.category]}>{CATEGORY_LABELS[doc.category]}</Pill>
                    </td>
                    <td className="py-3 px-2 text-center">
                      <div className="truncate max-w-[14rem] mx-auto">{doc.filename}</div>
                      <div className="text-[11px] text-[var(--text-tertiary)]">
                        {formatBytes(doc.size_bytes)}
                      </div>
                    </td>
                    <td className="py-3 px-2 text-center whitespace-nowrap">
                      {formatDate(doc.uploaded_at.slice(0, 10))}
                    </td>
                    <td className="py-3 px-2">
                      <div className="flex items-center justify-center gap-1">
                        <code className="tabular text-[11px] text-[var(--text-secondary)]" title={doc.sha256}>
                          {doc.sha256.slice(0, 12)}…
                        </code>
                        <CopyButton text={doc.sha256} label={`Copy SHA-256 of ${doc.title}`} iconOnly />
                      </div>
                    </td>
                    <td className="py-3 px-2">
                      <div className="flex items-center justify-center gap-1.5 whitespace-nowrap">
                        {INLINE_TYPES.has(doc.content_type) && (
                          <a
                            className={BUTTON_SECONDARY}
                            href={downloadUrl(doc, true)}
                            target="_blank"
                            rel="noreferrer"
                            aria-label={`Preview ${doc.title}`}
                          >
                            <Eye className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                            Preview
                          </a>
                        )}
                        <a
                          className={BUTTON_SECONDARY}
                          href={downloadUrl(doc)}
                          download={doc.filename}
                          aria-label={`Download ${doc.title}`}
                        >
                          <Download className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                          Download
                        </a>
                        <button
                          type="button"
                          className={BUTTON_QUIET}
                          aria-label={`Delete ${doc.title}`}
                          title="Delete"
                          onClick={() => setDeleting(doc)}
                        >
                          <Trash2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
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

      {uploading && (
        <UploadModal
          onClose={() => setUploading(false)}
          onDone={(doc) => {
            setUploading(false)
            documents.reload()
            toast.success(`${doc.title} uploaded`, `SHA-256 ${doc.sha256.slice(0, 12)}…`)
          }}
        />
      )}
      {deleting && (
        <DeleteDocumentModal
          doc={deleting}
          onClose={() => setDeleting(null)}
          onDone={() => {
            setDeleting(null)
            documents.reload()
            toast.success(`${deleting.title} deleted`)
          }}
        />
      )}
    </Panel>
  )
}

/* ----------------------------------------------------------------- modals -- */

function UploadModal({
  onClose,
  onDone,
}: {
  onClose: () => void
  onDone: (doc: HouseholdDocument) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState<DocumentCategory | ''>('')
  const [notes, setNotes] = useState('')
  const [dragging, setDragging] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const errors = {
    file: fileError(file),
    title: !title.trim()
      ? 'Give the document a title.'
      : title.trim().length > 200
        ? 'Keep it to 200 characters.'
        : null,
    category: category ? null : 'Choose a category.',
  }
  const valid = !errors.file && !errors.title && !errors.category
  // A wrong file is pointed out as soon as it is chosen.
  const fileShown = submitted || file ? errors.file : null

  const choose = (chosen: File | null | undefined) => {
    if (!chosen) return
    setFile(chosen)
    if (!title.trim()) setTitle(titleFrom(chosen.name))
  }

  const save = useMutation(
    () => {
      const form = new FormData()
      form.append('file', file as File)
      form.append('title', title.trim())
      form.append('category', category)
      if (notes.trim()) form.append('notes', notes.trim())
      return api.upload<HouseholdDocument>(DOCUMENTS, form)
    },
    { onSuccess: onDone },
  )

  return (
    <Modal
      title="Upload Document"
      description="PDF, image, Word or text, up to 10 MB. It is hashed with SHA-256 and stored with the household's records."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={save.pending}>
            Cancel
          </button>
          <button type="submit" form="document-upload-form" className={BUTTON_PRIMARY} disabled={save.pending}>
            {save.pending ? 'Uploading…' : 'Upload Document'}
          </button>
        </>
      }
    >
      <form
        id="document-upload-form"
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setSubmitted(true)
          if (valid && !save.pending) void save.run(undefined)
        }}
      >
        {save.error && <ErrorNotice error={save.error} />}
        <div className="field">
          <span className="text-sm font-semibold text-[var(--text-primary)]" id="document-file-label">
            File <span aria-hidden="true">*</span>
          </span>
          <div
            role="button"
            tabIndex={0}
            aria-labelledby="document-file-label"
            aria-describedby={fileShown ? 'document-file-error' : 'document-file-hint'}
            aria-invalid={fileShown ? true : undefined}
            data-testid="document-dropzone"
            className={
              dragging
                ? 'flex flex-col items-center gap-2 rounded-xl border-2 border-dashed border-[var(--accent)] bg-[var(--accent-glow)] p-6 text-center cursor-pointer'
                : fileShown
                  ? 'flex flex-col items-center gap-2 rounded-xl border-2 border-dashed border-[var(--danger)] bg-[var(--bg-elevated)] p-6 text-center cursor-pointer'
                  : 'flex flex-col items-center gap-2 rounded-xl border-2 border-dashed border-[var(--border-medium)] bg-[var(--bg-elevated)] p-6 text-center cursor-pointer'
            }
            onClick={() => input.current?.click()}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                input.current?.click()
              }
            }}
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              choose(event.dataTransfer.files?.[0])
            }}
          >
            <UploadCloud className="w-6 h-6 text-[var(--accent)]" aria-hidden="true" />
            {file ? (
              <span className="text-sm font-semibold text-[var(--text-primary)]">
                {file.name} · {formatBytes(file.size)}
              </span>
            ) : (
              <span className="text-sm text-[var(--text-secondary)]">
                Drag a file here, or click to choose one
              </span>
            )}
          </div>
          <input
            ref={input}
            type="file"
            className="sr-only"
            tabIndex={-1}
            aria-label="Choose file"
            accept={ACCEPTED_EXTENSIONS.join(',')}
            onChange={(event) => choose(event.target.files?.[0])}
          />
          {fileShown ? (
            <span className="field-error" id="document-file-error">
              {fileShown}
            </span>
          ) : (
            <span className="field-hint" id="document-file-hint">
              {ACCEPTED_EXTENSIONS.join(' ')} · 10 MB at most
            </span>
          )}
        </div>
        <TextField
          label="Title"
          value={title}
          onChange={setTitle}
          required
          error={submitted ? errors.title : null}
        />
        <SelectField
          label="Category"
          value={category}
          onChange={setCategory}
          placeholder="Choose a category"
          required
          options={(Object.keys(CATEGORY_LABELS) as DocumentCategory[]).map((value) => ({
            value,
            label: CATEGORY_LABELS[value],
          }))}
          error={submitted ? errors.category : null}
        />
        <TextAreaField label="Notes" value={notes} onChange={setNotes} rows={2} />
      </form>
    </Modal>
  )
}

function DeleteDocumentModal({
  doc,
  onClose,
  onDone,
}: {
  doc: HouseholdDocument
  onClose: () => void
  onDone: () => void
}) {
  const remove = useMutation(() => api.delete(`${DOCUMENTS}/${encodeURIComponent(doc.doc_id)}`), {
    onSuccess: onDone,
  })
  return (
    <Modal
      title="Delete Document"
      description={`${doc.title} (${doc.filename}) will be deleted for good. Its title, size and SHA-256 stay in the audit trail.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON_SECONDARY} onClick={onClose} disabled={remove.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={remove.pending}
            onClick={() => void remove.run(undefined)}
          >
            {remove.pending ? 'Deleting…' : 'Delete Document'}
          </button>
        </>
      }
    >
      <p className="m-0 flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
        <Fingerprint className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
        <code className="tabular break-all">{doc.sha256}</code>
      </p>
      {remove.error ? <ErrorNotice error={remove.error} /> : null}
    </Modal>
  )
}
