"""Make a self-signed TEST certificate and key for signing synthetic samples.

Never use an agency or personal certificate for test files. This makes a
throwaway identity, "Stamper Test (not a real identity)", valid for 30 days.

Run it in the throwaway pyHanko venv, not the project venv: it needs the
``cryptography`` package, which pyHanko installs. It doesn't import the
project.

Usage (PowerShell):
    & $env:TEMP\\pyhanko-venv\\Scripts\\python.exe tools\\make_test_cert.py $env:TEMP\\stamper-test-cert
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def make(folder: Path) -> tuple[Path, Path]:
    """Write test.key (unencrypted PEM) and test.crt (PEM) into ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Stamper Test (not a real identity)")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = folder / "test.key", folder / "test.crt"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def main(argv: list[str] | None = None) -> int:
    """Command line: write the key and certificate into the folder given."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", type=Path, help="folder for test.key and test.crt (keep it outside the repo)")
    key, cert = make(p.parse_args(argv).folder)
    print(f"Wrote {key} and {cert} (self-signed test identity, 30 days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
