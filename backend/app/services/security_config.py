"""WC-W009 P1 - startup security configuration checker.

Dangerous configurations are detected at STARTUP and the app refuses to
boot (or marks itself degraded, depending on severity). This closes the
'forgot to set a variable in prod' class of accidents at boot time
instead of relying on operator discipline.

Also provides the PROXY TRUST policy: behind a reverse proxy the peer IP
is the PROXY; the app must NOT parse X-Forwarded-For for access
decisions unless explicitly told to (WORKCAM_TRUST_PROXY_HEADERS=1),
because a client can FORGE those headers. Default: NOT trusted.
"""

from __future__ import annotations

import os
import sys


class SecurityConfigError(Exception):
    pass


def check_startup_security(fail_fast: bool = True) -> list[str]:
    """Run all configuration guards. Returns the list of FATAL errors;
    when fail_fast is True the process exits before serving traffic.
    Warnings (non-fatal) are printed to stderr."""
    errors: list[str] = []
    warnings: list[str] = []

    production = os.environ.get("WORKCAM_ENV") == "production"
    experimental = os.environ.get("WORKCAM_UPLOAD_EXPERIMENTAL") == "1"
    auth = os.environ.get("WORKCAM_UPLOAD_AUTH") == "1"
    proxy_trust = os.environ.get("WORKCAM_TRUST_PROXY_HEADERS") == "1"

    # G1: production must never have the experimental upload endpoints
    if production and experimental:
        errors.append(
            "FATAL: WORKCAM_ENV=production together with "
            "WORKCAM_UPLOAD_EXPERIMENTAL=1 - experimental upload "
            "endpoints are forbidden in production")

    # G2: production with a non-loopback bind and no business auth
    # awareness - the /api/v1 surface ALWAYS requires a token (by
    # design), but production should also have an enrollment code set
    # so enrollment is not accidentally CLOSED for real devices
    # (that would be an availability bug, not a security one).
    if production and not _enrollment_configured():
        warnings.append(
            "WARN: production is running without an enrollment code "
            "(WORKCAM_ENROLLMENT_CODE[_FILE]) - no new device can be "
            "enrolled; intentional only for a fully pre-enrolled fleet")

    # G3: proxy-header trust explicitly enabled - warn loudly because
    # misconfigured trust lets clients forge their peer IP
    if proxy_trust:
        warnings.append(
            "WARN: WORKCAM_TRUST_PROXY_HEADERS=1 - the app will honor "
            "X-Forwarded-For for the loopback gate. ONLY set this when "
            "a trusted reverse proxy strips client-supplied copies of "
            "the header; otherwise clients can forge their peer IP")

    # G4: production without TLS termination knowledge - informational
    if production and not proxy_trust:
        warnings.append(
            "WARN: production without proxy-header trust - if the app "
            "sits behind a TLS reverse proxy, set "
            "WORKCAM_TRUST_PROXY_HEADERS=1 and configure the proxy to "
            "strip inbound X-Forwarded-For")

    for w in warnings:
        print(w, file=sys.stderr)
    if errors and fail_fast:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    return errors


def _enrollment_configured() -> bool:
    if os.environ.get("WORKCAM_ENROLLMENT_CODE"):
        return True
    path = os.environ.get("WORKCAM_ENROLLMENT_CODE_FILE",
                          "data/business/enrollment-code.txt")
    from pathlib import Path
    return Path(path).exists() and Path(path).read_text(
        encoding="utf-8").strip() != ""


def effective_peer_host(request) -> str:
    """The peer address for access decisions.

    Default: the DIRECT socket peer (request.client.host) - X-Forwarded-
    For is NEVER consulted unless WORKCAM_TRUST_PROXY_HEADERS=1, and
    even then only the LAST (rightmost) proxy-appended value is used,
    which a client cannot inject when the proxy strips the header."""
    host = ""
    client = getattr(request, "client", None)
    if client:
        host = client.host or ""
    if os.environ.get("WORKCAM_TRUST_PROXY_HEADERS") == "1":
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            # rightmost entry is the hop added by OUR trusted proxy;
            # client-supplied entries sit to the LEFT and are ignored
            host = xff.split(",")[-1].strip() or host
    return host
