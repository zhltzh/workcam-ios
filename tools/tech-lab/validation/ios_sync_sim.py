#!/usr/bin/env python3
"""WC-W008 - simulated iOS offline-sync client (DESKTOP_SIMULATION).

Walks the full offline-first chain against a LIVE backend:

  (offline) queue ops with LOCAL ids -> (network restore) batch sync
  -> id map -> upload original bytes -> evidence link -> server verify
  -> pull server state via cursor -> REPLAY (network flapped twice)
  -> verify no duplicates + bytes identical.

Mac: replace the desktop stand-in bytes with a real iPhone JPEG and the
TEST key with SE-exported material. DESKTOP_SIMULATION - this script
never claims device verification.

Usage:
    python3 tools/tech-lab/validation/ios_sync_sim.py <backend-url> <device-token>
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request

PASS = FAIL = 0


def step(name, ok, detail=""):
    global PASS, FAIL
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {mark} {name}" + (f" - {detail}" if detail else ""))


def jcall(method, url, body=None, headers=None, raw_body=None):
    hdrs = dict(headers or {})
    data = None
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}"), {}
        except json.JSONDecodeError:
            return e.code, {}, {}


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    base = argv[1].rstrip("/")
    token = argv[2]
    H = {"X-Device-Token": token}

    print("WorkCam W008 模拟 iOS 离线同步客户端 [DESKTOP_SIMULATION]\n")

    data = b"sim iPhone JPEG stand-in for W008"
    sha = hashlib.sha256(data).hexdigest()

    # ---- (offline) queue with LOCAL ids only -------------------------
    queue_project = [{
        "op": "project_create", "operation_id": "sim-w008-pc",
        "payload": {"operation_id": "sim-w008-pc",
                    "local_id": "loc-offline-proj",
                    "name": "Offline Sync Site"}}]

    # ---- (network restored) batch sync -------------------------------
    code, r1, _ = jcall("POST", f"{base}/api/v1/sync/batch",
                        {"operations": queue_project}, H)
    step("1. sync project_create", code == 200 and
         r1["receipts"][0]["status"] == "applied",
         f"status={r1.get('receipts',[{}])[0].get('status')}")
    pid = r1["id_map"].get("loc-offline-proj", "")

    # ---- photo metadata (references the SERVER project id now) -------
    queue_photo = [{
        "op": "photo_register", "operation_id": "sim-w008-pr",
        "payload": {"operation_id": "sim-w008-pr",
                    "local_id": "loc-offline-photo",
                    "server_project_id": pid,
                    "idempotency_key": "sim-w008-ik-1",
                    "original_sha256": sha,
                    "original_filename": "IMG_offline.jpg",
                    "mime_type": "image/jpeg",
                    "byte_size": len(data),
                    "captured_at": "2026-11-24T08:00:00Z",
                    "device_id": "iPhone15,3"}}]
    code, r2, _ = jcall("POST", f"{base}/api/v1/sync/batch",
                        {"operations": queue_photo}, H)
    step("2. sync photo_register", code == 200 and
         r2["receipts"][0]["status"] == "applied",
         f"status={r2.get('receipts',[{}])[0].get('status')}")
    photo_id = r2["id_map"].get("loc-offline-photo", "")

    # ---- upload original bytes (W007 endpoint) ------------------------
    req = urllib.request.Request(
        f"{base}/api/v1/photos/{photo_id}/original", data=data,
        method="PUT", headers={**H, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        up = json.loads(resp.read())
    step("3. upload original", up.get("stored") is True,
         f"idempotent={up.get('idempotent_replay')}")

    # ---- evidence link + server verify (W006 endpoints) ---------------
    # For the pure-HTTP sim we link by reference and verify the server's
    # projection; the full signature-material verify is covered by the
    # W006 chain_joint_debug.py script.
    code, integ, _ = jcall(
        "GET", f"{base}/api/v1/photos/{photo_id}/original/integrity",
        None, H)
    step("4. integrity view", code == 200 and
         integ.get("original", {}).get("file_hash_valid") is True,
         f"evidence={integ.get('evidence', {}).get('highest_status')}")

    # ---- pull server state via cursor ----------------------------------
    code, events, _ = jcall("GET", f"{base}/api/v1/sync/events?limit=200",
                            None, H)
    step("5. pull events (cursor)", code == 200 and
         len(events.get("items", [])) >= 2,
         f"cursor={events.get('cursor')} items={len(events.get('items', []))}")
    cursor = events.get("cursor", 0)

    # ---- REPLAY the entire offline queue (network flapped twice) ------
    code, rb, _ = jcall("POST", f"{base}/api/v1/sync/batch",
                        {"operations": queue_project}, H)
    dup1 = rb["receipts"][0]["status"]
    code, rb2, _ = jcall("POST", f"{base}/api/v1/sync/batch",
                         {"operations": queue_photo}, H)
    dup2 = rb2["receipts"][0]["status"]
    step("6. replay (both batches)", dup1 == "duplicate" and dup2 == "duplicate",
         f"project={dup1} photo={dup2}")
    # id maps still resolve
    step("7. id map stable", rb["id_map"].get("loc-offline-proj") == pid and
         rb2["id_map"].get("loc-offline-photo") == photo_id)

    # ---- download and compare bytes -------------------------------------
    req = urllib.request.Request(
        f"{base}/api/v1/photos/{photo_id}/original", headers=H)
    with urllib.request.urlopen(req, timeout=60) as resp:
        dl = resp.read()
        dl_sha = hashlib.sha256(dl).hexdigest()
    step("8. download + sha match", dl_sha == sha and dl == data,
         f"{dl_sha[:12]}… == {sha[:12]}…")

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    print("[DESKTOP_SIMULATION] 全程桌面模拟数据；Mac 真机替换照片字节与密钥材料即可。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
