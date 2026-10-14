"""The household's contacts and its document vault.

These tests pin what makes the vault trustworthy: a document comes back
byte for byte with the SHA-256 taken when it went in, a listing never carries
file contents, a file that no longer matches its hash is refused rather than
served, and every upload and deletion is in the audit trail with its hash.
For contacts: each field is validated, and a role's own details are kept to
that role.
"""
from __future__ import annotations

import hashlib

import pytest

from aem.errors import NotFoundError, ReconciliationError, ValidationError
from aem.services import property as household

from .conftest import APARTMENT
from .support import connect

PDF = b"%PDF-1.7\n" + bytes(range(256)) * 40 + b"\n%%EOF\n"
PDF_HASH = hashlib.sha256(PDF).hexdigest()


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _owner(db, **fields):
    return household.create_contact(db, APARTMENT, {
        "role": "owner", "name": "Kumar Yadav", "phone": "+91 98765 43210",
        "email": "kumar@example.com", "pan": "abcde1234f", "bank_account_no": "123456789012",
        "bank_ifsc": "hdfc0001234", "upi_id": "kumar@okhdfc", **fields})


def _upload(db, content=PDF, filename="Rental_Agreement.pdf", **fields):
    return household.upload_document(
        db, APARTMENT, content=content, filename=filename,
        title=fields.pop("title", "Rental agreement 2026"),
        category=fields.pop("category", "lease_agreement"), **fields)


# ---------------------------------------------------------------- contacts --

def test_an_owner_is_recorded_with_their_tax_and_bank_details(apartment):
    owner = _owner(apartment)
    assert (owner["role"], owner["name"], owner["phone"]) == (
        "owner", "Kumar Yadav", "+91 98765 43210")
    # PAN and IFSC are stored upper-case, however they were typed.
    assert (owner["pan"], owner["bank_ifsc"]) == ("ABCDE1234F", "HDFC0001234")
    assert owner["bank_account_no"] == "123456789012" and owner["upi_id"] == "kumar@okhdfc"
    assert household.list_contacts(apartment, APARTMENT)["roles_on_file"] == ["owner"]


def test_a_brokers_fee_defaults_to_pending_and_is_formatted(apartment):
    broker = household.create_contact(apartment, APARTMENT, {
        "role": "broker", "name": "Ravi", "agency_name": "NestAway Realty",
        "fee_amount_paise": 2_850_000})
    assert (broker["fee_status"], broker["fee_display"]) == ("pending", "₹28,500.00")
    paid = household.update_contact(apartment, APARTMENT, broker["contact_id"],
                                    {"fee_status": "paid"})
    assert paid["fee_status"] == "paid" and paid["fee_amount_paise"] == 2_850_000


def test_contacts_are_listed_in_the_order_they_were_added(apartment):
    _owner(apartment)
    household.create_contact(apartment, APARTMENT, {
        "role": "caretaker", "name": "Mohan", "phone": "9876500000",
        "responsibilities": "Plumbing, electrical, gate passes"})
    household.create_contact(apartment, APARTMENT, {"role": "broker", "name": "Ravi"})
    listing = household.list_contacts(apartment, APARTMENT)
    assert [c["name"] for c in listing["items"]] == ["Kumar Yadav", "Mohan", "Ravi"]
    assert listing["roles_on_file"] == ["owner", "broker", "caretaker"]


@pytest.mark.parametrize("fields, field", [
    ({"name": "  "}, "name"),
    ({"role": "landlord"}, "role"),
    ({"phone": "12345"}, "phone"),
    ({"phone": "98765 4321O"}, "phone"),
    ({"phone": "1" * 16}, "phone"),
    ({"email": "kumar@example"}, "email"),
    ({"pan": "ABCD1234F"}, "pan"),
    ({"bank_ifsc": "HDFC1001234"}, "bank_ifsc"),
    ({"bank_account_no": "12-34"}, "bank_account_no"),
    ({"upi_id": "kumar"}, "upi_id"),
    ({"fee_amount_paise": 100}, "fee_amount_paise"),
    ({"responsibilities": "Rent"}, "responsibilities"),
])
def test_an_invalid_contact_is_refused_and_writes_nothing(apartment, fields, field):
    with pytest.raises(ValidationError) as caught:
        _owner(apartment, **fields)
    assert caught.value.details.get("field") == field
    assert household.list_contacts(apartment, APARTMENT)["total"] == 0


def test_a_fee_must_be_whole_paise(apartment):
    for fee in (12.5, True, -1):
        with pytest.raises(ValidationError):
            household.create_contact(apartment, APARTMENT, {
                "role": "broker", "name": "Ravi", "fee_amount_paise": fee})


def test_owner_details_are_refused_for_another_role(apartment):
    with pytest.raises(ValidationError) as caught:
        household.create_contact(apartment, APARTMENT, {
            "role": "caretaker", "name": "Mohan", "pan": "ABCDE1234F"})
    assert caught.value.details["field"] == "pan"


def test_changing_a_role_drops_the_details_it_does_not_use(apartment):
    owner = _owner(apartment)
    changed = household.update_contact(apartment, APARTMENT, owner["contact_id"],
                                       {"role": "caretaker", "responsibilities": "Keys"})
    assert (changed["pan"], changed["bank_account_no"], changed["bank_ifsc"]) == (
        None, None, None)
    assert changed["responsibilities"] == "Keys"
    # The phone, email and UPI ID are kept: a caretaker can have those too.
    assert changed["phone"] == "+91 98765 43210" and changed["upi_id"] == "kumar@okhdfc"


def test_an_update_changes_only_what_was_sent_and_is_audited(apartment):
    owner = _owner(apartment)
    updated = household.update_contact(apartment, APARTMENT, owner["contact_id"],
                                       {"phone": "9123456780", "email": None})
    assert (updated["phone"], updated["email"], updated["pan"]) == (
        "9123456780", None, "ABCDE1234F")
    entry = _rows(apartment, "SELECT * FROM audit_log WHERE entity_type='household_contact' "
                             "AND action='update'")[0]
    assert '"phone"' in entry["new_value"] and '"pan"' not in entry["new_value"]


def test_an_unchanged_update_writes_nothing(apartment):
    owner = _owner(apartment)
    household.update_contact(apartment, APARTMENT, owner["contact_id"], {"name": "Kumar Yadav"})
    assert not _rows(apartment, "SELECT 1 FROM audit_log WHERE action='update' "
                                "AND entity_type='household_contact'")


def test_a_contact_is_deleted_with_an_audit_record(apartment):
    owner = _owner(apartment)
    household.delete_contact(apartment, APARTMENT, owner["contact_id"])
    assert household.list_contacts(apartment, APARTMENT)["total"] == 0
    entry = _rows(apartment, "SELECT * FROM audit_log WHERE entity_id=%s AND action='delete'",
                  (owner["contact_id"],))[0]
    assert "Kumar Yadav" in entry["old_value"]
    with pytest.raises(NotFoundError):
        household.delete_contact(apartment, APARTMENT, owner["contact_id"])


# --------------------------------------------------------------- documents --

def test_a_document_round_trips_byte_for_byte_with_its_hash(apartment):
    meta = _upload(apartment, notes="Signed copy", uploaded_by_resident_id="alice")
    assert (meta["sha256"], meta["size_bytes"], meta["content_type"]) == (
        PDF_HASH, len(PDF), "application/pdf")
    assert (meta["filename"], meta["category"], meta["uploaded_by_resident_id"]) == (
        "Rental_Agreement.pdf", "lease_agreement", "alice")
    document = household.get_document_content(apartment, APARTMENT, meta["doc_id"])
    assert document["content"] == PDF and document["inline_allowed"] is True


def test_the_listing_carries_no_file_contents(apartment):
    _upload(apartment)
    _upload(apartment, content=b"Society NOC", filename="noc.txt", title="Society NOC",
            category="society_noc")
    listing = household.list_documents(apartment, APARTMENT)
    assert [d["title"] for d in listing["items"]] == ["Society NOC", "Rental agreement 2026"]
    assert all("content" not in item for item in listing["items"])
    assert listing["total_bytes"] == len(PDF) + len(b"Society NOC")
    assert listing["categories_on_file"] == ["lease_agreement", "society_noc"]


def test_the_upload_and_its_hash_are_in_the_audit_trail(apartment):
    meta = _upload(apartment)
    entry = _rows(apartment, "SELECT * FROM audit_log WHERE entity_id=%s", (meta["doc_id"],))[0]
    assert entry["action"] == "upload" and PDF_HASH in entry["new_value"]


def test_a_tampered_file_is_refused_rather_than_served(apartment):
    meta = _upload(apartment)
    with connect(apartment) as conn:
        conn.execute("UPDATE household_document SET content = %s WHERE doc_id = %s",
                     (b"X" * len(PDF), meta["doc_id"]))
    with pytest.raises(ReconciliationError) as caught:
        household.get_document_content(apartment, APARTMENT, meta["doc_id"])
    assert caught.value.code == "document_hash_mismatch"


def test_a_path_in_the_filename_is_dropped(apartment):
    assert _upload(apartment, filename="C:\\Users\\me\\..\\lease.PDF")["filename"] == "lease.PDF"
    assert _upload(apartment, filename="../../etc/passwd.txt")["filename"] == "passwd.txt"


@pytest.mark.parametrize("upload, field", [
    ({"content": b""}, "file"),
    ({"filename": "lease.exe"}, "file"),
    ({"filename": "lease"}, "file"),
    ({"filename": "  "}, "file"),
    ({"title": "  "}, "title"),
    ({"category": "rent_receipt"}, "category"),
])
def test_an_invalid_upload_is_refused_and_writes_nothing(apartment, upload, field):
    with pytest.raises(ValidationError) as caught:
        _upload(apartment, **upload)
    assert caught.value.details.get("field") == field
    assert household.list_documents(apartment, APARTMENT)["total"] == 0


def test_a_file_over_the_limit_is_refused(apartment):
    with pytest.raises(ValidationError) as caught:
        _upload(apartment, content=b"x" * (household.MAX_DOCUMENT_BYTES + 1))
    assert caught.value.code == "file_too_large"
    assert _upload(apartment, content=b"x" * household.MAX_DOCUMENT_BYTES,
                   filename="max.txt")["size_bytes"] == household.MAX_DOCUMENT_BYTES


def test_an_uploader_from_another_flat_is_refused(apartment):
    with pytest.raises(NotFoundError):
        _upload(apartment, uploaded_by_resident_id="ghost")


def test_a_deleted_document_is_gone_but_its_hash_stays_in_the_audit_trail(apartment):
    meta = _upload(apartment)
    household.delete_document(apartment, APARTMENT, meta["doc_id"], reason="Superseded")
    assert household.list_documents(apartment, APARTMENT)["total"] == 0
    assert not _rows(apartment, "SELECT 1 FROM household_document")
    entry = _rows(apartment, "SELECT * FROM audit_log WHERE entity_id=%s AND action='delete'",
                  (meta["doc_id"],))[0]
    assert PDF_HASH in entry["old_value"] and entry["reason"] == "Superseded"
    with pytest.raises(NotFoundError):
        household.get_document_content(apartment, APARTMENT, meta["doc_id"])


def test_another_apartments_document_is_not_found(apartment):
    from aem.services import apartments
    meta = _upload(apartment)
    apartments.create_apartment(apartment, apartment_id="other", name="Other flat")
    with pytest.raises(NotFoundError):
        household.get_document_content(apartment, "other", meta["doc_id"])
    with pytest.raises(NotFoundError):
        household.delete_document(apartment, "other", meta["doc_id"])


def test_the_database_keeps_the_size_and_the_bytes_in_step(apartment):
    from .support import DatabaseRefusal
    with pytest.raises(DatabaseRefusal):
        with connect(apartment) as conn:
            conn.execute(
                "INSERT INTO household_document (doc_id, apartment_id, title, category, "
                "filename, content_type, size_bytes, sha256, content, uploaded_at) VALUES "
                "('d', %s, 't', 'other', 'f.txt', 'text/plain', 99, %s, 'abc', 'now')",
                (APARTMENT, "0" * 64))


# ----------------------------------------------------------------------- API --

@pytest.fixture()
def flat_client(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    return client


def test_the_contacts_api(flat_client):
    created = flat_client.post("/api/property/contacts", json={
        "role": "broker", "name": "Ravi", "phone": "9876543210", "fee_amount_paise": 1_000_000})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["fee_display"] == "₹10,000.00" and body["fee_status"] == "pending"
    assert flat_client.post("/api/property/contacts",
                            json={"role": "broker", "name": "X",
                                  "fee_amount_paise": 10.5}).status_code == 422
    assert flat_client.post("/api/property/contacts",
                            json={"role": "nobody", "name": "X"}).status_code == 422
    assert flat_client.post("/api/property/contacts",
                            json={"role": "owner", "name": "X",
                                  "email": "nope"}).status_code == 422
    patched = flat_client.patch(f"/api/property/contacts/{body['contact_id']}",
                                json={"fee_status": "paid"})
    assert patched.status_code == 200 and patched.json()["fee_status"] == "paid"
    assert flat_client.get("/api/property/contacts").json()["total"] == 1
    assert flat_client.delete(
        f"/api/property/contacts/{body['contact_id']}").status_code == 204
    assert flat_client.delete(
        f"/api/property/contacts/{body['contact_id']}").status_code == 404


def test_the_documents_api_uploads_lists_downloads_and_deletes(flat_client):
    uploaded = flat_client.post(
        "/api/property/documents",
        files={"file": ("Rental Agreement ₹.pdf", PDF, "application/octet-stream")},
        data={"title": "Rental agreement", "category": "lease_agreement"})
    assert uploaded.status_code == 201, uploaded.text
    meta = uploaded.json()
    assert meta["sha256"] == PDF_HASH and "content" not in meta
    # The type comes from the extension, not from what the browser claimed.
    assert meta["content_type"] == "application/pdf"

    listing = flat_client.get("/api/property/documents").json()
    assert [item["doc_id"] for item in listing["items"]] == [meta["doc_id"]]

    download = flat_client.get(f"/api/property/documents/{meta['doc_id']}/download")
    assert download.status_code == 200 and download.content == PDF
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["x-content-sha256"] == PDF_HASH
    assert download.headers["x-content-type-options"] == "nosniff"
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''Rental%20Agreement%20%E2%82%B9.pdf" in disposition
    inline = flat_client.get(f"/api/property/documents/{meta['doc_id']}/download?inline=true")
    assert inline.headers["content-disposition"].startswith("inline;")

    assert flat_client.delete(f"/api/property/documents/{meta['doc_id']}").status_code == 204
    assert flat_client.get(
        f"/api/property/documents/{meta['doc_id']}/download").status_code == 404


def test_a_word_document_is_never_shown_inline(flat_client):
    meta = flat_client.post(
        "/api/property/documents", files={"file": ("handover.docx", b"PK\x03\x04docx", "")},
        data={"title": "Inventory handover", "category": "inventory_handover"}).json()
    download = flat_client.get(f"/api/property/documents/{meta['doc_id']}/download?inline=true")
    assert download.headers["content-disposition"].startswith("attachment;")


def test_an_upload_without_a_file_or_title_is_refused(flat_client):
    assert flat_client.post("/api/property/documents",
                            data={"title": "T", "category": "other"}).status_code == 422
    assert flat_client.post("/api/property/documents",
                            files={"file": ("a.pdf", PDF, "application/pdf")},
                            data={"category": "other"}).status_code == 422
    assert flat_client.post("/api/property/documents",
                            files={"file": ("a.exe", PDF, "application/pdf")},
                            data={"title": "T", "category": "other"}).status_code == 422
