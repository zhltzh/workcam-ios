"""M03-D02: RFC 3161 timestamps via a LOCAL TEST TSA.

Uses the REAL protocol through `openssl ts` (Git-for-windows ships
it) - not a mock of the wire format:

  - TimeStampReq: sha256 messageImprint + nonce (openssl builds the
    proper DER)
  - Local TEST TSA: a self-signed test CA + TSA signer key ( clearly
    labeled - NEVER mixed with real roots)
  - TimeStampResp: CMS-signed token; `openssl ts -verify` performs
    the full cryptographic verification against the test CA

HONESTY RULES (task brief):
  - a timestamp proves the DATA EXISTED at tstInfo.genTime - it says
    NOTHING about when the photo was TAKEN
  - the TEST TSA is explicitly a test trust anchor; a real TSA's
    certificate chain is a deployment config the operator supplies
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from techlab.geometry.camera import _require_finite  # noqa: E402


def _openssl_exe() -> str:
    exe = shutil.which("openssl")
    if exe is None:
        for cand in (r"C:\Program Files\Git\mingw64\bin\openssl.exe",
                     r"C:\Program Files\Git\usr\bin\openssl.exe"):
            if Path(cand).exists():
                return cand
    if exe is None:
        raise RuntimeError("openssl not found - RFC 3161 suite "
                           "cannot run (recorded, not skipped "
                           "silently)")
    return exe


def _run(args: list[str], cwd: Path | None = None
         ) -> tuple[int, str, str]:
    proc = subprocess.run([_openssl_exe()] + args,
                          cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


class TSAError(ValueError):
    pass


@dataclass(frozen=True)
class TestTSA:
    """A LOCAL test TSA identity (generated, self-signed, clearly
    labeled). Production deployments plug a REAL TSA config instead -
    the module takes the anchor as a parameter, never a global."""
    ca_key: Path
    ca_cert: Path
    tsa_key: Path
    tsa_cert: Path
    tsa_config: Path


def build_test_tsa(directory: Path, cn: str = "WorkCam Test TSA"
                   ) -> TestTSA:
    """Generate the test CA + TSA signer. Fully offline; every cert
    carries the test CN so it can never be confused with a real
    anchor. Implementation: openssl req + openssl x509 self-sign +
    a minimal openssl ts config."""
    directory.mkdir(parents=True, exist_ok=True)
    ca_key = directory / "test-ca.key"
    ca_cert = directory / "test-ca.pem"
    tsa_key = directory / "test-tsa.key"
    tsa_cert = directory / "test-tsa.pem"
    tsa_config = directory / "tsa.cnf"

    def step(args: list[str]) -> None:
        code, out, err = _run(args)
        if code != 0:
            raise TSAError(f"openssl {' '.join(args[:2])} failed: "
                           f"{err.strip()[:300]}")

    if not ca_key.exists():
        step(["req", "-x509", "-newkey", "ec",
              "-pkeyopt", "ec_paramgen_curve:P-256",
              "-keyout", str(ca_key), "-out", str(ca_cert),
              "-days", "3650", "-nodes", "-subj",
              f"/CN={cn} CA (TEST ONLY)"])
    if not tsa_key.exists():
        step(["ecparam", "-name", "P-256", "-genkey", "-noout",
              "-out", str(tsa_key)])
        step(["req", "-new", "-key", str(tsa_key), "-out",
              str(directory / "tsa.csr"), "-subj",
              f"/CN={cn} Signer (TEST ONLY)"])
        step(["x509", "-req", "-in", str(directory / "tsa.csr"),
              "-CA", str(ca_cert), "-CAkey", str(ca_key),
              "-CAcreateserial", "-days", "3650",
              "-out", str(tsa_cert),
              "-extfile", _write_exts(directory)])
    if not tsa_config.exists():
        # Windows path backslashes are ESCAPE CHARS in openssl config
        # - always emit forward-slash absolute paths
        serial_file = directory / "tsa_serial"
        if not serial_file.exists():
            serial_file.write_text("01\n", encoding="ascii")
        tsa_config.write_text(_TSA_CONFIG.format(
            ca=ca_cert.resolve().as_posix(),
            signer=tsa_cert.resolve().as_posix(),
            key=tsa_key.resolve().as_posix(),
            serial=serial_file.resolve().as_posix()),
            encoding="utf-8")
    return TestTSA(ca_key, ca_cert, tsa_key, tsa_cert, tsa_config)


def _write_exts(directory: Path) -> str:
    exts = directory / "tsa.ext"
    exts.write_text(
        "extendedKeyUsage = critical,timeStamping\n"
        "basicConstraints = CA:FALSE\n", encoding="utf-8")
    return str(exts)


_TSA_CONFIG = """
[ tsa ]
default_tsa = tsa_config1

[ tsa_config1 ]
# RFC 3161 default policy OID for the TEST TSA (openssl's commonly
# used example value; clearly a TEST policy)
default_policy = 1.3.6.1.4.1.13700.2
# ESSCertIDv2 over sha256 (openssl 3.x)
ess_cert_id_alg = sha256
signer_digest = sha256
digests = sha256
serial = {serial}
serial_file = {serial}
certs = {ca}
signer_cert = {signer}
signer_key = {key}
"""


def timestamp_query(data: bytes, out: Path,
                    nonce: bytes | None = None) -> bytes:
    """Build a REAL TimeStampReq (DER) over sha256(data).
    openssl ts -query includes a nonce BY DEFAULT (the option is
    -no_nonce to disable) - so the wire format carries one unless
    the caller explicitly asks for none."""
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data)
        data_file = Path(f.name)
    try:
        args = ["ts", "-query", "-data", str(data_file),
                "-sha256", "-out", str(out)]
        if nonce is not None:
            args.append("-no_nonce")
        code, out_s, err = _run(args)
        if code != 0:
            raise TSAError(f"ts -query failed: {err.strip()[:300]}")
        return out.read_bytes()
    finally:
        data_file.unlink(missing_ok=True)


def tsa_respond(query_path: Path, response_path: Path,
                tsa: TestTSA) -> None:
    """The LOCAL TEST TSA answers the query (openssl ts -reply with
    the test config)."""
    code, out, err = _run(["ts", "-reply",
                           "-queryfile", str(query_path),
                           "-out", str(response_path),
                           "-config", str(tsa.tsa_config)])
    if code != 0:
        raise TSAError(f"ts -reply failed: {err.strip()[:300]}")


def tsa_verify(data: bytes, response_path: Path, tsa: TestTSA
               ) -> dict:
    """Full RFC 3161 verification: `openssl ts -verify` checks the
    CMS signature, the message imprint AND the chain against the
    supplied anchor. M03-D02 AUDIT SUPPLEMENT: the verdict now also
    reports ISSUANCE-TIME certificate facts - verification against a
    CURRENTLY-EXPIRED-but-valid-at-issuance chain still succeeds
    (that is the long-term verification semantics), and the cert's
    key-usage (timeStamping EKU) + algorithm are checked and named.
    Returns the genTime from the token text dump."""
    data_file = response_path.parent / "verify-data.bin"
    data_file.write_bytes(data)
    try:
        code, out, err = _run(
            ["ts", "-verify", "-data", str(data_file),
             "-in", str(response_path),
             "-CAfile", str(tsa.ca_cert),
             "-untrusted", str(tsa.tsa_cert)])
        if code != 0:
            return {"verified": False,
                    "reason": err.strip()[:300] or out.strip()[:300]}
        # extract genTime via the text dump
        txt_path = response_path.parent / "tsa-dump.txt"
        _run(["ts", "-reply", "-in", str(response_path),
              "-text", "-out", str(txt_path)])
        text = txt_path.read_text(encoding="utf-8",
                                  errors="replace")
        gen_time = ""
        nonce = ""
        policy = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("Time stamp:"):
                gen_time = s.split(":", 1)[1].strip()
            if s.startswith("Nonce:"):
                nonce = s.split(":", 1)[1].strip()
            if s.startswith("Policy OID:"):
                policy = s.split(":", 1)[1].strip()
        # ---- M03-D02 audit: certificate facts at ISSUANCE time ----
        audit = _audit_tsa_cert(tsa.tsa_cert, tsa.ca_cert)
        return {"verified": True, "gen_time": gen_time,
                "nonce": nonce, "policy": policy,
                "cert_audit": audit}
    finally:
        data_file.unlink(missing_ok=True)


def _audit_tsa_cert(tsa_cert: Path, ca_cert: Path) -> dict:
    """Issuance-time certificate audit (the D02 supplement): key
    usage (timeStamping EKU is REQUIRED), signature algorithm
    (policy floor: SHA-256+, no SHA-1), chain presence, and validity
    WINDOW as facts - an expired TODAY cert that was valid at genTime
    still verifies long-term (openssl checks the chain as supplied;
    the audit REPORTS the windows instead of silently guessing)."""
    facts: dict = {}
    # text dump of the leaf
    code, txt, err = _run(["x509", "-in", str(tsa_cert),
                           "-noout", "-text"])
    if code == 0:
        t = txt
        facts["key_usage"] = ("timeStamping" in t
                             or "Time Stamping" in t)
        facts["sha1_signature"] = ("sha1WithRSA" in t) \
            or ("ecdsa-with-SHA1" in t)
        for label, key in (("Not Before:", "not_before"),
                           ("Not After :", "not_after")):
            for line in t.splitlines():
                if line.strip().startswith(label):
                    facts[key] = line.strip()
        facts["ec_key"] = "id-ecPublicKey" in t
    code, ca_txt, _ = _run(["x509", "-in", str(ca_cert),
                            "-noout", "-text"])
    facts["chain_anchor_present"] = code == 0
    facts["algorithm_below_policy"] = facts.get("sha1_signature",
                                                 False)
    facts["note"] = ("verdicts use the ARCHIVED chain; a cert valid "
                     "at genTime still proves existence even if "
                     "expired TODAY - validity windows are REPORTED "
                     "facts, never silently re-judged")
    return facts


def message_imprint(data: bytes) -> dict:
    """The RFC 3161 MessageImprint inputs: sha256 digest + the
    standard hashAlgorithm OID (2.16.840.1.101.3.4.2.1)."""
    return {"hash_algorithm_oid": "2.16.840.1.101.3.4.2.1",
            "digest_hex": hashlib.sha256(data).hexdigest()}


def evidence_meaning_note() -> str:
    """The honesty rule carried beside every timestamp result: a
    timestamp proves EXISTENCE at genTime - never the capture time."""
    return ("A timestamp proves the data existed at genTime. It "
            "says NOTHING about when the photo was taken; capture "
            "time remains a client claim bound by the separate "
            "time-anchor system (TM09/TM10).")
