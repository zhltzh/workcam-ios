# WorkCam Windows hotfix handoff

Backend parent `2b535a047e5f9e5c9c5091da949a591442b4ab61`. Mac reference [PR #24](https://github.com/zhltzh/workcam-ios/pull/24), code `53440fe`, report `33cc1ca`. Pin the reviewed hotfix code commit from this candidate branch. No schema version bump.

| Mac operation | Actual server contract |
|---|---|
| Enroll and connect | POST `/api/v1/devices/enroll` with X9.63 public key, device ID and enrollment code. HTTP 201 with opaque `device_token`. Identical active key returns the same token. It also appends a replay audit record. Revoked, suspended and retired keys can receive 409 with a typed code. Suspend requires admin resume. |
| Challenge | POST `/api/v1/devices/challenge` with X-Device-Token, then POST `/api/v1/devices/challenge/verify` with token, nonce and base64 DER over the nonce's ASCII bytes. A challenge has a 60-second lifetime. On HTTP 401 clear the invalid credential and reconnect. |
| Publish | POST `/api/v1/sync/batch`, at most 50 operations. The payload repeats `operation_id`. Keep the original body through retries. Apply receipts individually and store the local-to-server ID mapping in the same local commit as acknowledgment. |
| Rename or archive | Use `project_update` or `project_archive` with `server_project_id` and `expected_revision`. Refetch and resolve conflict before issuing a new operation ID. Archived projects reject later renames and registration of new photos. |
| Edit issue | `issue_update` and `issue_transition` use `expected_revision`; closed is terminal. Use `issue_photo_unlink` with the exact current photo and role. For replacement via `issue_photo_link`, supply `expected_previous_photo_id`, otherwise a populated different role returns `photo_role_conflict`. Use fresh IDs for intentional return to a previous photo. |
| Pull | GET `/api/v1/sync/events?cursor=...&limit=...&include_state=true`. Cursor monotonic per owner and filter. Limit counts matching rows. Store a separate filtered cursor. Optional snapshots reflect the entity's current revision at page read time, not the historical event revision. Annotation soft-delete events carry a tombstone. Evidence/original events are pointers. Store applied data and returned cursor atomically. |
| Upload | POST `/api/v1/photos/{id}/original-session` with claimed hash, chunk size, total count and bytes. Upload ID is **32 lowercase hex**, not a hyphenated UUID. Send missing indices with X-Chunk-SHA256, then complete. On restart reopen and reconcile `received_chunks`. Final GET `.../original/integrity` must confirm `original.sha256`, `bound_to_photo`, `claims.file_integrity_verified`. |

HTTP API errors use `detail.code/message/details`. Batch rejection is a per-operation receipt even when HTTP is 200. Repeated rejected operations keep `status=duplicate` for backward compatibility with `result.status=rejected` and the stored error. Historical rejections created before this fix may return `historical_rejection`. Report `revision_conflict`, `project_archived`, `issue_closed`, `photo_role_conflict`, `annotation_deleted` and `op_id_conflict` without dropping queued intent. A reconciled body must use a new ID.

PR #24 already understands nested integrity, hex upload IDs, individual receipts, and rejects a duplicate whose result is marked rejected. Its initial-publication model still needs the pull cursor, rename/archive/edit operations, explicit role compare-and-swap, and reconciling changed intent. Role replacement in this batch contract is guarded. Older direct REST role endpoints retain their prior behavior. Full multi-owner collaboration is outside the current token-scoped model.

## Reproduce

From an isolated checkout/venv. On Windows use the backend's pinned lock and its installed Python. The macOS validation used CPython 3.12.16 and the versions pinned at `53440fe:Tests/Integration02/backend-macos-py312.lock.txt`.

```sh
cd backend
python -m pytest -q
python scripts/b02_w026_export_openapi.py ../docs/api/openapi.json
python scripts/win_hotfix_live.py .
PYTHONPATH=. python scripts/bench_enrollment.py --samples 100
cd ..
xcrun swiftc -module-cache-path /tmp/win-hotfix-swift-cache test-vectors/win-hotfix/SyncContract.swift test-vectors/win-hotfix/main.swift -o /tmp/win-hotfix-swift
/tmp/win-hotfix-swift test-vectors/win-hotfix/vectors.json
```

The HTTP script owns an isolated loopback server and synthetic credentials, stops/restarts it during upload, and cleans its temporary data. The Swift DTO fixtures exercise decoding and acknowledgment rules. They are independent of the iOS live test. No production data or credentials are required.
