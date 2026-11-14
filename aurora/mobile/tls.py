"""
The certificate for the home Wi-Fi connection.

AURORA makes its own certificate once (ECDSA P-256, valid 10 years) and
keeps it next to the phone database. No certificate authority is involved:
at pairing (over the USB cable) the phone is given the certificate's
SHA-256 fingerprint and from then on accepts only that exact certificate,
so no other computer on the network can pretend to be this PC.
"""

import hashlib
import logging
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)


def paths(database: Path) -> tuple[Path, Path]:
    return database.with_name("aurora_mobile_tls.pem"), database.with_name("aurora_mobile_tls.key")


def ensure_certificate(cert_path: Path, key_path: Path) -> None:
    """Creates the certificate and key if they don't exist yet (never replaces them: phones pin it)."""
    if cert_path.is_file() and key_path.is_file():
        return
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AURORA PC")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder()
                   .subject_name(name).issuer_name(name)
                   .public_key(key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1))
                   .not_valid_after(now + timedelta(days=3650))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .sign(key, hashes.SHA256()))
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    logger.info("Mobile: Wi-Fi certificate created")


def fingerprint(cert_path: Path) -> str:
    """SHA-256 of the certificate (DER), lower-case hex: what the phone pins."""
    der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context
