#!/usr/bin/env python3
"""WC-W005 P1 - trusted evidence-chain joint-debug script.

Mac 可直接执行的联调脚本（也可在 Windows 桌面跑真实照片）。给定：

  - 一张真实照片文件（Mac 真机拍摄，或桌面任意 JPEG——脚本只对
    磁盘字节做哈希/签名，不问来源）
  - 一个 P-256 私钥 PEM（**联调/测试密钥**；真机版由 Secure Enclave
    产生同格式证据——脚本不区分，也不声称密钥来源）
  - 后端地址

执行全链：注册公钥 → 生成签名清单（v1.1 协议）→ /verify → 打证据包
→ 离线验证 CLI → 时间锚点 → 全失败场景（篡改原图/换公钥/重复提交/
断链）→ 每步打印 PASS/FAIL。

HONESTY: 全程不声称 Secure Enclave 验收——密钥来源由调用方负责，
本脚本输出会明确打印 key-origin unknown（除非 --claim-se 传入，
即使传入也只记录来源声明，不做硬件验证）。

Usage (Mac):
    python3 tools/tech-lab/validation/chain_joint_debug.py \
        <photo.jpg> <key.pem> <backend-url> [--device-id ID]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    Encoding,
    PublicFormat,
)

REPO = Path(__file__).resolve().parents[3]
PASS = FAIL = 0


def step(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))


def _req(method, url, body=None, headers=None, raw=False):
    hdrs = dict(headers or {})
    data = None
    if body is not None:
        if isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
    req = request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return resp.status, payload if raw else json.loads(payload or b"{}")
    except error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": payload.decode("utf-8", "replace")}


def canonical_payload_v11(m: dict) -> bytes:
    lines = [
        "WorkCam-Sig-v1.1",
        f"version={m['version']}",
        f"record_id={m['record_id']}",
        f"sequence_number={m['sequence_number']}",
        f"previous_record_hash={'none' if m['previous_record_hash'] is None else m['previous_record_hash']}",
        f"photo_hash={m['photo_hash']}",
        f"capture_timestamp={m['capture_timestamp']}",
        f"device_reported_time={m['device_reported_time']}",
        f"device_id={m['device_id']}",
        "location=none",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("key_pem")
    ap.add_argument("base_url")
    ap.add_argument("--device-id", default="joint-debug-device")
    ap.add_argument("--claim-se", action="store_true",
                    help="record caller's claim that the key is from "
                         "Secure Enclave (NOT verified by this script)")
    args = ap.parse_args(argv[1:])

    photo = Path(args.photo)
    base = args.base_url.rstrip("/")
    if not photo.exists():
        print(f"FAIL: photo not found: {photo}")
        return 2

    print("WorkCam 可信证据链联调（WC-W005 P1）")
    key_origin = ("caller-claimed Secure Enclave (NOT hardware-verified)"
                  if args.claim_se else "unknown origin - TEST/joint-debug key")
    print(f"密钥来源: {key_origin}")
    print(f"照片: {photo.name} ({photo.stat().st_size} B)\n")

    # ---- 1. inputs ------------------------------------------------------
    photo_bytes = photo.read_bytes()
    photo_hash = hashlib.sha256(photo_bytes).hexdigest()
    priv = load_pem_private_key(Path(args.key_pem).read_bytes(), password=None)
    if not isinstance(priv, ec.EllipticCurvePrivateKey) or \
            not isinstance(priv.curve, ec.SECP256R1):
        print("FAIL: key must be P-256 (secp256r1)")
        return 2
    pub_b64 = base64.b64encode(priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "version": "1.1", "record_id": str(uuid.uuid4()),
        "sequence_number": 0, "previous_record_hash": None,
        "photo_hash": photo_hash, "capture_timestamp": now,
        "device_reported_time": now, "device_id": args.device_id,
        "location": None, "public_key": pub_b64, "signature": "",
    }
    sig = priv.sign(canonical_payload_v11(manifest), ec.ECDSA(hashes.SHA256()))
    manifest["signature"] = base64.b64encode(sig).decode()
    b64photo = base64.b64encode(photo_bytes).decode()
    step("photo sha256 computed", True, f"{photo_hash[:12]}...")

    # ---- 2. enroll the public key (test flow) ---------------------------
    # register-key binds scalar params from the QUERY STRING (W005-2 finding)
    st, r = _req("POST",
                 f"{base}/register-key?public_key_b64={pub_b64}"
                 f"&device_id={args.device_id}&allow_test_registration=true")
    step("register-key", st == 200 and r.get("registration_status") == "registered",
         f"status={st}")

    # ---- 3. /verify happy path -------------------------------------------
    st, r = _req("POST", f"{base}/verify",
                 {"manifest": manifest, "photo_base64": b64photo})
    ok = (st == 200 and r.get("cryptographic_signature_valid") is True
          and r.get("photo_hash_valid") is True
          and r.get("evidence_status") in ("cryptographically_valid", "verified"))
    step("/verify valid evidence", ok, f"evidence_status={r.get('evidence_status')}")

    # ---- 4. tampered photo ----------------------------------------------
    bad = photo_bytes[:-1] + bytes([photo_bytes[-1] ^ 0x55])
    st, r = _req("POST", f"{base}/verify",
                 {"manifest": manifest, "photo_base64": base64.b64encode(bad).decode()})
    step("tampered photo rejected", r.get("photo_hash_valid") is False
         and r.get("evidence_status") == "invalid")

    # ---- 5. swapped public key ------------------------------------------
    other = ec.generate_private_key(ec.SECP256R1())
    other_b64 = base64.b64encode(other.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    m2 = dict(manifest); m2["public_key"] = other_b64
    st, r = _req("POST", f"{base}/verify", {"manifest": m2})
    step("swapped public key rejected", r.get("cryptographic_signature_valid") is False)

    # ---- 6. evidence package + offline verify ----------------------------
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pkg"
        pkg.mkdir()
        (pkg / "photos").mkdir(); (pkg / "manifests").mkdir()
        (pkg / "photos" / f"{manifest['record_id']}.jpg").write_bytes(photo_bytes)
        (pkg / "manifests" / f"{manifest['record_id']}.json").write_text(
            json.dumps(manifest))
        (pkg / "package.json").write_text(json.dumps({
            "package_version": "1", "created_at": now,
            "device_id": args.device_id,
            "records": [{
                "record_id": manifest["record_id"],
                "photo_file": f"photos/{manifest['record_id']}.jpg",
                "manifest_file": f"manifests/{manifest['record_id']}.json",
                "capture_timestamp": now, "location_meta": None,
                "anchor_receipt_file": None, "derivatives": [],
            }],
            "notes": ["joint-debug package"]}))
        # broken derivative back-link
        (pkg / "derivatives").mkdir()
        (pkg / "derivatives" / "orphan.jpg").write_bytes(b"derivative-bytes")
        # re-verify via backend-independent evidence_package
        sys.path.insert(0, str(REPO / "backend"))
        from techlab.evidence_package import verify_package
        result = verify_package(pkg)
        ok = result.all_passed and result.record_results[0].passed
        step("evidence package verify (library)", ok,
             f"record={manifest['record_id'][:8]} passed={result.record_results[0].passed}")

        # offline CLI (subprocess, cross-platform semantics)
        import os
        proc = subprocess.run(
            [sys.executable,
             str(REPO / "tools/tech-lab/validation/evidence_verifier.py"),
             str(pkg), "--json", str(Path(tmp) / "r.json")],
            capture_output=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        step("offline CLI verify", proc.returncode == 0,
             proc.stdout.strip().splitlines()[-1] if proc.stdout.strip()
             else proc.stderr[:120])

    # ---- 7. store-record duplicate ---------------------------------------
    st1, r1 = _req("POST", f"{base}/store-record",
                   {"manifest": manifest, "photo_base64": b64photo})
    st2, r2 = _req("POST", f"{base}/store-record",
                   {"manifest": manifest, "photo_base64": b64photo})
    ok = (r1.get("stored") is True and r2.get("stored") is False
          and "duplicate" in str(r2.get("message", "")).lower())
    step("store-record idempotent duplicate", ok,
         f"first={r1.get('stored')} retry={r2.get('stored')}")

    # ---- 8. time anchor ----------------------------------------------------
    head = hashlib.sha256(b"chain-head").hexdigest()
    st, r = _req("POST", f"{base}/time-anchor/anchor", {
        "batch_id": f"joint-{uuid.uuid4().hex[:8]}",
        "chain_head_hash": head, "chain_tail_hash": head, "record_count": 1,
        "device_reported_start_time": now, "device_reported_end_time": now})
    ok = st == 200 and "server_signature" in r
    step("time anchor issued", ok)
    if ok:
        receipt = dict(r)
        receipt["device_reported_start_time"] = now
        receipt["device_reported_end_time"] = now
        receipt["stored_at"] = r["server_received_at"]
        st2, v = _req("POST", f"{base}/time-anchor/verify", receipt)
        step("anchor receipt verified", st2 == 200 and v.get("valid") is True,
             f"trust={v.get('trust_level')}")

    # ---- summary ------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"RESULT: {PASS} passed, {FAIL} failed  (key origin: {key_origin})")
    print("本脚本不构成 Secure Enclave 真机验收 - 密钥来源未做硬件验证。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
