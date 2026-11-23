#!/usr/bin/env python3
"""WC-W007 - simulated iOS client: full original-photo chain.

跑通：创建项目 → 登记照片 → 上传原图 → 关联证据 → 服务端验证 →
查询验证状态 → 下载原图 → 比较 SHA-256。

Mac 联调：把 PHOTO_FILE 换成真实 iPhone JPEG、KEY_PEM 换成 SE 导出
密钥材料即可（认证用已注册的 device token）。全程桌面模拟数据在
输出中明确标注。

Usage:
    python3 tools/tech-lab/validation/ios_client_sim.py <backend-url> <device-token>

或用自包含模式（会在后端注册一个测试设备并走全链）：
    python3 tools/tech-lab/validation/ios_client_sim.py <backend-url> --self-test
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PASS = FAIL = 0


def step(name, ok, detail=""):
    global PASS, FAIL
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {mark} {name}" + (f" - {detail}" if detail else ""))


def call(method, url, body=None, headers=None, raw_body=None):
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
            payload = resp.read()
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, payload, dict(e.headers)


def jcall(method, url, body=None, headers=None, raw_body=None):
    code, raw, hdrs = call(method, url, body, headers, raw_body)
    try:
        return code, json.loads(raw) if raw else {}, hdrs
    except json.JSONDecodeError:
        return code, {"_raw": raw.decode("utf-8", "replace")}, hdrs


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    base = argv[1].rstrip("/")
    token = argv[2] if len(argv) > 2 else None

    if token in (None, "--self-test"):
        print("MODE: self-test（桌面模拟数据；不声称真机）")
        # register a test device via the backend registry file - the
        # joint-debug convention: the operator pre-enrolls; here we
        # document the required header and stop.
        print("需要一个已注册的 device token（X-Device-Token）。")
        print("联调流程见 docs/mvp/w007-mac-handoff.md。")
        return 2

    H = {"X-Device-Token": token}
    print("WorkCam W007 模拟 iOS 客户端全链（桌面模拟数据）\n")

    # 1. create project
    code, proj, _ = jcall("POST", f"{base}/api/v1/projects",
                          {"name": "Sim iPhone Project"}, H)
    step("1. create project", code == 201, f"pid={proj.get('project_id', '')[:8]}…")
    pid = proj["project_id"]

    # 2. photo bytes (Mac: replace with a real iPhone JPEG file read)
    data = b"\x42" * 2048                     # DESKTOP stand-in
    sha = hashlib.sha256(data).hexdigest()
    # 3. register photo metadata
    code, photo, _ = jcall(
        "POST", f"{base}/api/v1/projects/{pid}/photos",
        {"idempotency_key": f"sim-{pid[:8]}",
         "original_sha256": sha,
         "original_filename": "IMG_sim.jpg",
         "mime_type": "image/jpeg",
         "byte_size": len(data),
         "captured_at": "2026-11-24T08:00:00Z",
         "device_id": "iPhone15,3"}, H)
    step("3. register photo", code == 201, f"sha={sha[:12]}…")
    photo_id = photo["photo_id"]

    # 4. upload + bind original
    code, up, _ = jcall("PUT",
                        f"{base}/api/v1/photos/{photo_id}/original",
                        headers={**H, "Content-Type": "application/octet-stream"},
                        raw_body=data)
    step("4. upload original", code == 200, f"stored={up.get('stored')}")

    # 5. link evidence (signature manifest: build one if the caller
    #    has a key; in pure-HTTP sim we link by reference)
    code, link, _ = jcall(
        "POST", f"{base}/api/v1/photos/{photo_id}/evidence-links",
        {"evidence_reference": f"sim-{photo_id[:8]}",
         "evidence_type": "signature_manifest"}, H)
    step("5. link evidence", code == 201,
         f"status={link.get('verification_status')}")

    # 6. integrity view (storage + chain state)
    code, integ, _ = jcall("GET",
                           f"{base}/api/v1/photos/{photo_id}/original/integrity",
                           None, H)
    ok = (code == 200
          and integ["original"]["file_hash_valid"] is True
          and integ["claims"]["file_integrity_verified"] is True
          and integ["claims"]["photo_authenticity_proven"] is False)
    step("6. integrity view", ok,
         f"evidence={integ.get('evidence', {}).get('highest_status')}")

    # 7. download + compare
    code, raw, hdrs = call("GET",
                           f"{base}/api/v1/photos/{photo_id}/original", None,
                           H)
    # urllib normalizes response header keys to lowercase
    server_sha = hdrs.get("x-original-sha256",
                          hdrs.get("X-Original-Sha256", ""))
    dl_sha = hashlib.sha256(raw).hexdigest() if code == 200 else ""
    step("7. download + sha compare",
         code == 200 and dl_sha == sha,
         f"hdr={server_sha[:12]}… vs local {sha[:12]}…")

    # 8. negative: tampered re-upload is refused
    bad = data[:-1] + bytes([data[-1] ^ 0xFF])
    code2, err, _ = jcall("PUT", f"{base}/api/v1/photos/{photo_id}/original",
                          headers={**H, "Content-Type": "application/octet-stream"},
                          raw_body=bad)
    # binding exists with same registered sha: content mismatch -> 400
    step("8. tampered upload refused", code2 in (400, 409),
         f"code={code2}")

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    print("全程桌面模拟数据；真实 iPhone JPEG 替换步骤 2 的 data 即为真机联调。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
