# B02-W019 C2PA compatibility PoC (independent, not wired into any production code).
# Uses c2pa-python (binding of c2pa-rs) to:
#   1. generate a test-only P-256 key + self-signed X.509 cert (in memory, never written to disk/git)
#   2. embed a C2PA manifest into a JPEG (ES256, no TSA = offline signing)
#   3. read back and inspect validation state (no trust anchors => expected self-signed status)
#   4. tamper one byte and re-read (hard binding check)
#   5. measure size overhead and signing/reading wall time
#   6. optional TSA pass (online RFC 3161 timestamp) - failure is recorded honestly
#   7. derivative test: treat the signed JPEG as a parent ingredient of an edited copy
# Run: tools/c2pa-poc/.venv-poc/Scripts/python.exe poc_c2pa.py

import datetime
import io
import json
import os
import shutil
import struct
import sys
import time

from PIL import Image
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from cryptography.x509.oid import NameOID

import c2pa

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
os.makedirs(OUT, exist_ok=True)

RESULTS = {
    "sdk": c2pa.sdk_version(),
    "python_binding": "c2pa-python 0.39.0",
    "python": sys.version,
    "steps": [],
}


def step(name, data):
    RESULTS["steps"].append({"step": name, **data})
    print(f"[step] {name}: {json.dumps(data, ensure_ascii=True)[:400]}", flush=True)


def make_test_jpeg(path, w=320, h=240):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 255) // w, (y * 255) // h, 128)
    img.save(path, "JPEG", quality=90)
    return os.path.getsize(path)


def make_test_ca_and_leaf():
    """Test-only mini PKI: a self-signed test CA plus a leaf signing cert.
    Nothing is written to disk. The chain (leaf + CA PEM) is what the C2PA
    signer expects; the CA PEM doubles as the PoC trust anchor."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WorkCam Tech Lab (PoC only)"),
            x509.NameAttribute(NameOID.COMMON_NAME, "WorkCam C2PA PoC Test CA"),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WorkCam Tech Lab (PoC only)"),
            x509.NameAttribute(NameOID.COMMON_NAME, "WorkCam C2PA PoC Leaf Signing Cert"),
        ]
    )
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.3.6.1.5.5.7.3.36")]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    chain_pem = leaf_cert.public_bytes(Encoding.PEM).decode() + ca_cert.public_bytes(Encoding.PEM).decode()
    leaf_key_pem = leaf_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    ca_pem = ca_cert.public_bytes(Encoding.PEM).decode()
    return chain_pem, leaf_key_pem, ca_pem


def manifest_definition(record_id, photo_hash, device_id, capture_ts, location):
    return {
        "claim_generator_info": [
            {"name": "workcam-c2pa-poc", "version": "0.1.0", "documentation": "B02-W019 PoC"}
        ],
        "format": "image/jpeg",
        "title": "WorkCam B02-W019 PoC JPEG",
        "ingredients": [],
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture",
                            "when": capture_ts,
                        }
                    ]
                },
            },
            {
                "label": "org.workcam.poc.evidence",
                "data": {
                    "record_id": record_id,
                    "device_id": device_id,
                    "photo_hash_sha256": photo_hash,
                    "capture_timestamp": capture_ts,
                    "location": location,
                    "hash_alg": "sha256",
                    "note": "WorkCam evidence fields mapped as a custom assertion (PoC mapping experiment)",
                },
            },
        ],
    }


def make_trust_settings(ca_pem):
    """Settings that install the PoC test CA as a trust anchor so the SDK's
    signing-time certificate-chain checks accept the leaf (test only)."""
    return c2pa.Settings.from_dict(
        {
            "verify": {"verify_trust": True},
            "trust": {
                "anchors": [
                    {
                        "trust_uri": None,
                        "trust_kind": "manifest",
                        "trust_anchors": ca_pem,
                        "trust_config": None,
                        "allowed_list": None,
                        "trusted_ica_issuers": None,
                    }
                ]
            },
        }
    )


def sign_jpeg(src, dst, cert_pem, key_pem, manifest, tsa_url=None, settings=None):
    with c2pa.Context(settings) as context:
        info = c2pa.C2paSignerInfo(
            alg=c2pa.C2paSigningAlg.ES256,
            sign_cert=cert_pem.encode("utf-8"),
            private_key=key_pem.encode("utf-8"),
            ta_url=tsa_url,
        )
        with c2pa.Signer.from_info(info) as signer:
            with c2pa.Builder(manifest, context) as builder:
                builder.sign_file(src, dst, signer)


def read_jpeg(path, trust_settings=None):
    t0 = time.perf_counter()
    if trust_settings is not None:
        with c2pa.Context(trust_settings) as context:
            with c2pa.Reader(path, context=context) as reader:
                js = reader.json()
                detailed = reader.detailed_json()
    else:
        with c2pa.Context() as context:
            with c2pa.Reader(path, context=context) as reader:
                js = reader.json()
                detailed = reader.detailed_json()
    dt = (time.perf_counter() - t0) * 1000
    return js, detailed, dt


def main():
    print(f"SDK: c2pa {c2pa.sdk_version()}, binding c2pa-python 0.39.0")
    print(f"Python: {sys.version.split()[0]}")

    # --- 1. test jpeg ---
    plain = os.path.join(OUT, "poc_plain.jpg")
    plain_size = make_test_jpeg(plain)
    with open(plain, "rb") as f:
        photo_hash = __import__("hashlib").sha256(f.read()).hexdigest()
    step("generate_test_jpeg", {"path": plain, "bytes": plain_size, "sha256": photo_hash[:16] + "..."})

    # --- 2. test mini-PKI (memory only) ---
    chain_pem, leaf_key_pem, ca_pem = make_test_ca_and_leaf()
    step("generate_test_key", {
        "curve": "secp256r1 (P-256)",
        "structure": "test CA (self-signed) + leaf signing cert issued by CA",
        "leaf_eku": "id-kp-documentSigning (1.3.6.1.5.5.7.3.36)",
        "written_to_disk": False,
        "ca_subject": "O=WorkCam Tech Lab (PoC only), CN=WorkCam C2PA PoC Test CA",
        "leaf_subject": "O=WorkCam Tech Lab (PoC only), CN=WorkCam C2PA PoC Leaf Signing Cert",
    })

    capture_ts = "2026-11-25T10:00:00Z"
    record_id = "b02w019-poc-record-0001"
    device_id = "test-device-001"
    mf = manifest_definition(record_id, photo_hash, device_id, capture_ts, "none")
    with open(os.path.join(OUT, "manifest_definition.json"), "w", encoding="utf-8") as f:
        json.dump(mf, f, indent=2, ensure_ascii=False)

    # --- 2b. signing with default settings MUST fail for a self-signed leaf (honest record) ---
    signed = os.path.join(OUT, "poc_signed.jpg")
    try:
        sign_jpeg(plain, signed + ".tmp", chain_pem, leaf_key_pem, mf, tsa_url=None)
        step("sign_no_trust_settings", {"rejected": False, "unexpected": True})
    except c2pa.C2paError as e:
        step("sign_no_trust_settings", {"rejected": True, "error": str(e)[:200]})

    # --- 3. sign (offline: no TSA) with the PoC test CA installed as trust anchor ---
    trust_settings = make_trust_settings(ca_pem)
    t0 = time.perf_counter()
    sign_jpeg(plain, signed, chain_pem, leaf_key_pem, mf, tsa_url=None, settings=trust_settings)
    sign_ms = (time.perf_counter() - t0) * 1000
    signed_size = os.path.getsize(signed)
    step("sign_offline", {
        "src_bytes": plain_size,
        "dst_bytes": signed_size,
        "overhead_bytes": signed_size - plain_size,
        "overhead_pct": round((signed_size - plain_size) / plain_size * 100, 2),
        "wall_ms": round(sign_ms, 1),
        "tsa": "none (offline)",
    })

    # --- 4. read back (no trust anchors) ---
    js, detailed, read_ms = read_jpeg(signed)
    with open(os.path.join(OUT, "manifest_read.json"), "w", encoding="utf-8") as f:
        f.write(js)
    with open(os.path.join(OUT, "manifest_read_detailed.json"), "w", encoding="utf-8") as f:
        f.write(detailed)
    parsed = json.loads(js)
    validation_state = None
    try:
        validation_state = parsed["validation_state"]
    except Exception:
        pass
    if validation_state is None:
        # try nested locations
        def find_key(d, key):
            if isinstance(d, dict):
                if key in d:
                    return d[key]
                for v in d.values():
                    r = find_key(v, key)
                    if r is not None:
                        return r
            elif isinstance(d, list):
                for v in d:
                    r = find_key(v, key)
                    if r is not None:
                        return r
            return None
        validation_state = find_key(parsed, "validation_state")
    step("read_back_no_trust", {
        "validation_state": validation_state,
        "read_wall_ms": round(read_ms, 1),
        "active_manifest_found": "manifest" in parsed or "active_manifest" in parsed,
    })

    # --- 4b. read back with the PoC cert as trust anchor ---
    js_t, detailed_t, read_ms_t = read_jpeg(signed, trust_settings)
    with open(os.path.join(OUT, "manifest_read_trusted.json"), "w", encoding="utf-8") as f:
        f.write(js_t)
    parsed_t = json.loads(js_t)
    val_t = None
    for _js in (parsed_t, parsed):
        if isinstance(_js, dict) and "validation_state" in _js:
            val_t = _js["validation_state"]
            break
    step("read_back_with_poc_trust", {
        "validation_state": val_t,
        "read_wall_ms": round(read_ms_t, 1),
    })

    # --- 5. tamper one byte in the middle of pixel data ---
    tampered = os.path.join(OUT, "poc_tampered.jpg")
    shutil.copyfile(signed, tampered)
    with open(tampered, "r+b") as f:
        f.seek(2000)
        b = f.read(1)
        f.seek(2000)
        f.write(bytes([b[0] ^ 0x01]))
    tamper_result = {"path": tampered}
    try:
        js2, detailed2, read_ms2 = read_jpeg(tampered)
        with open(os.path.join(OUT, "tampered_read.json"), "w", encoding="utf-8") as f:
            f.write(js2)
        p2 = json.loads(js2)

        def find_key(d, key):
            if isinstance(d, dict):
                if key in d:
                    return d[key]
                for v in d.values():
                    r = find_key(v, key)
                    if r is not None:
                        return r
            elif isinstance(d, list):
                for v in d:
                    r = find_key(v, key)
                    if r is not None:
                        return r
            return None
        tamper_result["reader_raised"] = False
        tamper_result["validation_state_after_tamper"] = find_key(p2, "validation_state")
    except c2pa.C2paError as e:
        tamper_result["reader_raised"] = True
        tamper_result["error"] = str(e)[:300]
    except Exception as e:
        tamper_result["reader_raised"] = True
        tamper_result["error_type"] = type(e).__name__
        tamper_result["error"] = str(e)[:300]
    step("tamper_one_byte", tamper_result)

    # --- 6. online TSA attempt (real network, honest record) ---
    tsa_signed = os.path.join(OUT, "poc_signed_tsa.jpg")
    tsa_result = {}
    try:
        t0 = time.perf_counter()
        sign_jpeg(plain, tsa_signed, chain_pem, leaf_key_pem, mf,
                  tsa_url="https://timestamp.digicert.com", settings=trust_settings)
        tsa_result = {
            "ok": True,
            "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
            "bytes": os.path.getsize(tsa_signed),
        }
    except Exception as e:
        tsa_result = {"ok": False, "error_type": type(e).__name__, "error": str(e)[:300]}
    step("online_tsa_attempt", tsa_result)

    # --- 7. derivative test: signed jpeg as parent ingredient ---
    deriv_result = {}
    try:
        edited_plain = os.path.join(OUT, "poc_edited_plain.jpg")
        img = Image.open(plain)
        img = img.rotate(90)
        img.save(edited_plain, "JPEG", quality=90)
        mf_deriv = {
            "claim_generator_info": [{"name": "workcam-c2pa-poc", "version": "0.1.0"}],
            "format": "image/jpeg",
            "title": "WorkCam B02-W019 PoC derivative (edited copy)",
            "assertions": [
                {
                    "label": "c2pa.actions",
                    "data": {
                        "actions": [
                            {"action": "c2pa.edited"},
                        ]
                    },
                }
            ],
        }
        with c2pa.Context(trust_settings) as context:
            info = c2pa.C2paSignerInfo(
                alg=c2pa.C2paSigningAlg.ES256,
                sign_cert=chain_pem.encode("utf-8"),
                private_key=leaf_key_pem.encode("utf-8"),
                ta_url=None,
            )
            with c2pa.Signer.from_info(info) as signer:
                with c2pa.Builder(mf_deriv, context) as builder:
                    ing_def = {"title": "WorkCam B02-W019 PoC JPEG", "relationship": "parentOf"}
                    with open(signed, "rb") as srcf:
                        builder.add_ingredient(ing_def, "image/jpeg", srcf)
                    builder.set_intent(c2pa.C2paBuilderIntent.EDIT)
                    dst = os.path.join(OUT, "poc_derivative.jpg")
                    builder.sign_file(edited_plain, dst, signer)
        js3, detailed3, _ = read_jpeg(dst, trust_settings)
        with open(os.path.join(OUT, "derivative_read.json"), "w", encoding="utf-8") as f:
            f.write(js3)
        p3 = json.loads(js3)

        def find_key2(d, key):
            if isinstance(d, dict):
                if key in d:
                    return d[key]
                for v in d.values():
                    r = find_key2(v, key)
                    if r is not None:
                        return r
            elif isinstance(d, list):
                for v in d:
                    r = find_key2(v, key)
                    if r is not None:
                        return r
            return None
        deriv_result = {
            "ok": True,
            "validation_state": find_key2(p3, "validation_state"),
            "ingredient_chain_depth": json.dumps(p3).count("parentOf"),
        }
    except Exception as e:
        deriv_result = {"ok": False, "error_type": type(e).__name__, "error": str(e)[:300]}
    step("derivative_ingredient_test", deriv_result)

    # --- 8. large image (12MP-ish) to measure overhead ratio on realistic sizes ---
    big_plain = os.path.join(OUT, "poc_big_plain.jpg")
    big_size = make_test_jpeg(big_plain, w=4000, h=3000)
    big_signed = os.path.join(OUT, "poc_big_signed.jpg")
    t0 = time.perf_counter()
    sign_jpeg(big_plain, big_signed, chain_pem, leaf_key_pem, mf, tsa_url=None, settings=trust_settings)
    big_sign_ms = (time.perf_counter() - t0) * 1000
    big_signed_size = os.path.getsize(big_signed)
    t0 = time.perf_counter()
    read_jpeg(big_signed, trust_settings)
    big_read_ms = (time.perf_counter() - t0) * 1000
    step("large_image_perf", {
        "plain_bytes": big_size,
        "signed_bytes": big_signed_size,
        "overhead_bytes": big_signed_size - big_size,
        "overhead_pct": round((big_signed_size - big_size) / big_size * 100, 3),
        "sign_wall_ms": round(big_sign_ms, 1),
        "read_wall_ms": round(big_read_ms, 1),
    })

    # --- 8b. HEIC round-trip (container support experiment) ---
    heic_result = {}
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        heic_plain = os.path.join(OUT, "poc_plain.heic")
        img = Image.open(plain)
        img.save(heic_plain, format="HEIF", quality=90)
        heic_plain_size = os.path.getsize(heic_plain)
        heic_signed = os.path.join(OUT, "poc_signed.heic")
        t0 = time.perf_counter()
        sign_jpeg(heic_plain, heic_signed, chain_pem, leaf_key_pem, mf,
                  tsa_url=None, settings=trust_settings)
        heic_sign_ms = (time.perf_counter() - t0) * 1000
        js_h, detailed_h, heic_read_ms = read_jpeg(heic_signed, trust_settings)
        with open(os.path.join(OUT, "heic_read.json"), "w", encoding="utf-8") as f:
            f.write(js_h)
        ph = json.loads(js_h)
        vh = None
        if isinstance(ph, dict):
            vh = ph.get("validation_state")
        heic_result = {
            "ok": True,
            "plain_bytes": heic_plain_size,
            "signed_bytes": os.path.getsize(heic_signed),
            "sign_wall_ms": round(heic_sign_ms, 1),
            "read_wall_ms": round(heic_read_ms, 1),
            "validation_state": vh,
        }
    except Exception as e:
        heic_result = {"ok": False, "error_type": type(e).__name__, "error": str(e)[:300]}
    step("heic_roundtrip", heic_result)

    # --- 9. size/perf summary ---
    step("size_perf_summary", {
        "plain_bytes": plain_size,
        "signed_bytes": signed_size,
        "overhead_bytes": signed_size - plain_size,
        "sign_wall_ms_offline": round(sign_ms, 1),
        "read_wall_ms": round(read_ms, 1),
    })

    with open(os.path.join(OUT, "poc_results.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2, ensure_ascii=False)
    print("\nAll steps done. Results -> output/poc_results.json")


if __name__ == "__main__":
    main()
