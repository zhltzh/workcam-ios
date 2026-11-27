"""Measure serial same-key enrollment against an isolated SQLite database."""
import argparse
import base64
import json
import math
import os
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.db import migrate
from app.services.device_identity import enroll_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=25)
    count = parser.parse_args().samples
    if count < 1:
        parser.error("--samples must be positive")
    with tempfile.TemporaryDirectory(prefix="workcam-enrollment-") as root:
        path = Path(root) / "business.db"
        os.environ["WORKCAM_BUSINESS_DB"] = str(path)
        os.environ["WORKCAM_ENROLLMENT_CODE"] = "local-benchmark-only"
        migrate(path)
        pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        public = base64.b64encode(pub).decode()
        start = time.perf_counter()
        token = enroll_device(public, "benchmark", "local-benchmark-only")
        first = (time.perf_counter() - start) * 1000
        samples = []
        for _ in range(count):
            start = time.perf_counter()
            assert enroll_device(public, "benchmark", "local-benchmark-only") == token
            samples.append((time.perf_counter() - start) * 1000)
        ordered = sorted(samples)
        print(json.dumps({"samples": count, "first_ms": first, "replay_ms": samples,
                          **{f"p{p}_ms": ordered[math.ceil(count*p/100)-1] for p in (50, 95, 99)}}))


if __name__ == "__main__":
    main()
