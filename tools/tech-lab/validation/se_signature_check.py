#!/usr/bin/env python3
"""WC-W005-R1 P0 - Secure Enclave signature verification (receive-only).

Mac 侧导出的 SE 证据独立验签模式。Windows 只接收并验证以下材料：

  - iPhone 原始 JPEG 文件
  - 原图 SHA-256
  - Secure Enclave 公钥（X9.63 uncompressed 65 字节，base64）
  - 原始签名载荷（EXACT bytes the device signed，hex）
  - P-256 ECDSA 签名（DER，base64）
  - Key ID 及必要元数据

本工具验证且仅验证：字节一致性（照片==SHA-256）与签名数学（公钥+载荷+DER）。
它**不要求、不接收任何私钥**；对 Secure Enclave 的来源仅记录调用方声明，
**不做硬件验证**。签名数学有效 ≠ 密钥在 Secure Enclave 中。

输入 JSON（evidence 文件，Mac 打包一个文件最方便）：

    {
      "mode": "se_direct",                  // 或 "software_test_key"
      "photo_path": "IMG_0001.jpg",
      "photo_sha256": "<64 lowercase hex>",
      "public_key_b64": "<base64 X9.63 65B>",
      "signature_b64": "<base64 DER>",
      "payload_hex": "<hex of the exact signed bytes>",
      "key_id": "com.workcam.se.v1",
      "device_id": "iPhone15,3-XXXX",
      "claims": {"se_origin": true}        // 可选，仅记录
    }

禁止字段：任何含私钥的字段（private_key / private_key_pem / priv /
key_pem / secret）都会导致立即拒绝——本工具的输入面不允许私钥。

Usage:
    python3 tools/tech-lab/validation/se_signature_check.py evidence.json \
        [--photo <override-path>]
Exit codes: 0 = 全部通过; 1 = 验证失败; 2 = 输入错误。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "backend"))

FORBIDDEN_KEY_FIELDS = {
    "private_key", "private_key_pem", "private_key_b64", "priv",
    "key_pem", "secret", "secret_key",
}

PASS = FAIL = 0


def step(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {mark} {name}" + (f" - {detail}" if detail else ""))


def fail_input(msg: str) -> int:
    print(f"INPUT ERROR: {msg}")
    return 2


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Secure Enclave evidence verification (receive-only; "
                    "never accepts private keys)")
    ap.add_argument("evidence", help="evidence JSON file")
    ap.add_argument("--photo", default=None,
                    help="override photo_path from the evidence file")
    args = ap.parse_args(argv[1:])

    path = Path(args.evidence)
    if not path.exists():
        return fail_input(f"evidence file not found: {path}")
    try:
        ev = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return fail_input(f"evidence JSON unreadable: {e}")

    # ---- hard refusal: no private keys may enter this tool ------------
    found = sorted(FORBIDDEN_KEY_FIELDS & set(ev.keys()))
    if found:
        return fail_input(
            f"evidence file contains FORBIDDEN field(s) {found}: this "
            f"verification mode is receive-only and never accepts "
            f"private keys")

    # ---- required fields ------------------------------------------------
    required = ["photo_path", "photo_sha256", "public_key_b64",
                "signature_b64", "payload_hex"]
    missing = [k for k in required if not ev.get(k)]
    if missing:
        return fail_input(f"missing required field(s): {missing}")

    mode = ev.get("mode", "se_direct")
    if mode not in ("se_direct", "software_test_key"):
        return fail_input(f"unknown mode '{mode}' (se_direct | "
                          f"software_test_key)")

    print("WorkCam Secure Enclave 证据独立验证（receive-only）")
    if mode == "software_test_key":
        print("MODE: SOFTWARE_TEST_KEY —— 软件测试密钥，与 Secure Enclave 无关")
    else:
        print("MODE: se_direct —— 调用方声明密钥来自 Secure Enclave"
              "（本工具不做硬件验证，仅记录声明）")
    print(f"key_id: {ev.get('key_id', '<not provided>')}")
    print(f"device_id: {ev.get('device_id', '<not provided>')}")
    print()

    # ---- 1. photo bytes vs sha256 ----------------------------------------
    photo_path = Path(args.photo or ev["photo_path"])
    if not photo_path.exists():
        return fail_input(f"photo file not found: {photo_path}")
    photo_bytes = photo_path.read_bytes()
    actual_hash = hashlib.sha256(photo_bytes).hexdigest()
    declared = ev["photo_sha256"].strip().lower()
    step("photo sha256 match",
         actual_hash == declared,
         f"declared {declared[:12]}... vs actual {actual_hash[:12]}... "
         f"({len(photo_bytes)} B)")

    # ---- 2. payload bytes -------------------------------------------------
    try:
        payload = bytes.fromhex(ev["payload_hex"])
    except ValueError:
        return fail_input("payload_hex is not valid hex")
    step("payload decoded", len(payload) > 0, f"{len(payload)} bytes")

    # ---- 3. DER structure --------------------------------------------------
    from app.services.signature import _validate_der_structure
    try:
        sig = base64.b64decode(ev["signature_b64"], validate=True)
        _validate_der_structure(sig)
        step("signature DER structure", True, f"{len(sig)} bytes")
    except Exception as e:
        step("signature DER structure", False, str(e)[:100])
        print(f"\nRESULT: {PASS} passed, {FAIL} failed")
        return 1

    # ---- 4. public key + ECDSA math -----------------------------------------
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.exceptions import InvalidSignature
    try:
        pub_raw = base64.b64decode(ev["public_key_b64"], validate=True)
        if len(pub_raw) != 65 or pub_raw[0] != 0x04:
            raise ValueError(
                f"public key must be 65-byte X9.63 uncompressed point, "
                f"got {len(pub_raw)} bytes")
        pub = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), pub_raw)
    except Exception as e:
        step("public key parse", False, str(e)[:100])
        print(f"\nRESULT: {PASS} passed, {FAIL} failed")
        return 1
    else:
        step("public key parse", True, "P-256 X9.63 uncompressed")

    try:
        pub.verify(sig, payload, ec.ECDSA(SHA256()))
        step("ECDSA-SHA256 signature over payload", True)
    except InvalidSignature:
        step("ECDSA-SHA256 signature over payload", False,
             "signature does not verify over the provided payload")

    # ---- summary -------------------------------------------------------------
    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    if mode == "software_test_key":
        print("SOFTWARE_TEST_KEY —— 软件密钥验证，不涉及 Secure Enclave。")
    else:
        print("本工具验证了字节一致性与签名数学；密钥是否真在 Secure "
              "Enclave 中未被（也无法被）本工具验证。签名数学有效 ≠ "
              "Secure Enclave 真机验收。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
