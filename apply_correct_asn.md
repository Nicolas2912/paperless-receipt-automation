Short answer:
- You’re not actually setting archive_serial_number on the document anywhere. You only embed “AS: n” in the title.
- The correct writable field in the Paperless-ngx documents API is archive_serial_number (integer). Leading zeros aren’t preserved.

What’s happening in your code:
- You compute a next ASN via get_next_archive_serial_number(...) and/or take it from metadata to build the title string.
- You never send archive_serial_number in either the multipart POST to /api/documents/post_document/ or a follow-up PATCH to /api/documents/{id}/.
- Result: Paperless will not have the ASN stored in its dedicated field; it will only appear in the title text.

What to change:
- Option A (preferred): After the upload POST, PATCH the created document with archive_serial_number.
- Option B (if supported by your Paperless version): Include archive_serial_number as a form field in the initial multipart POST. Some versions may ignore it on post_document/, so PATCH is the reliable path.

Minimal diff to add a PATCH step after upload
Assuming the upload endpoint returns the created document object or an ID/task that you can resolve to a document ID, add:

```python
def _api_patch_document(base_url: str, token: str, doc_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/documents/{doc_id}/"
    try:
        r = requests.patch(
            url,
            headers=_auth_headers(token) | {"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        debug(f"ERROR patching document {doc_id}: {e}")
        return None
```

After upload_document(...), extract the new document’s id and set the ASN:

```python
result = upload_document(...)
# result["json"] might be a task envelope or a document. Handle both.
doc_id = None
if isinstance(result.get("json"), dict):
    j = result["json"]
    # Common patterns:
    # 1) Direct document object
    if isinstance(j.get("id"), int):
        doc_id = j["id"]
    # 2) Task-based response with "document" field or similar. Adjust if your instance differs.
    elif isinstance(j.get("document"), dict) and isinstance(j["document"].get("id"), int):
        doc_id = j["document"]["id"]

if doc_id:
    # Choose ASN: prefer metadata "asn" if present, else compute next
    md_asn = None
    if md_json and isinstance(md_json.get("asn"), int):
        md_asn = md_json["asn"]
    try:
        asn_to_set = md_asn if isinstance(md_asn, int) and md_asn > 0 else get_next_archive_serial_number(args.base_url, token)
    except Exception as e2:
        debug(f"WARN: Falling back ASN to 1: {e2}")
        asn_to_set = 1

    patched = _api_patch_document(args.base_url, token, doc_id, {"archive_serial_number": asn_to_set})
    if patched:
        debug(f"Set archive_serial_number={asn_to_set} on document id={doc_id}")
    else:
        debug("WARN: Failed to set archive_serial_number; document kept without ASN.")
else:
    debug("WARN: Could not determine document ID from upload response; cannot set archive_serial_number.")
```

If you want to send it in the initial POST (may or may not be honored by your version), add this where you build data_list in upload_document:

```python
# e.g., pass through from caller
# if archive_serial_number is not None:
#     data_list.append(("archive_serial_number", str(archive_serial_number)))
```

Then change your main flow to compute the ASN once and pass it into upload_document; still be prepared to PATCH if the POST ignores it.

About your metadata JSON
- Your extractor provides "asn": 6 and a title that already contains “AS: 6”.
- That’s fine for display, but not enough. You must write 6 to archive_serial_number to make it the official ASN in Paperless.
- Ensure you don’t accidentally double-assign or collide. If 6 is already taken, the PATCH will fail with a validation error. Handle that by recomputing next or informing the user.

Summary answers to your questions:
- Is the field correct? Use archive_serial_number (integer) on the document resource.
- Do you set it correctly now? No. You only put “AS: n” in the title; you need to POST or PATCH archive_serial_number explicitly.