"""
Proof that a request comes from the paired phone itself.

At pairing, the phone creates a signing key inside Android's Keystore (in
the phone's secure hardware where it has it); the private half can't be
read or copied off the phone. It sends AURORA the public half. From then
on every request carries, besides the device key, a signature over

    METHOD \n PATH \n TIME \n NONCE \n SHA-256(BODY)

made with that key (ECDSA P-256, SHA-256). AURORA checks the signature,
that TIME is within two minutes of the PC's clock and that the NONCE
hasn't been seen before, so a copied device key or a recorded request is
useless on any other device.
"""

import base64
import binascii
import hashlib
import re
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

MAX_SKEW_SECONDS = 120
NONCE_SECONDS = 2 * MAX_SKEW_SECONDS + 10


class SignatureError(Exception):
    pass


def load_public_key(text: str) -> ec.EllipticCurvePublicKey:
    """A phone's public key (base64 of X.509 SubjectPublicKeyInfo DER). Only P-256 is accepted."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(text, validate=True))
    except (ValueError, TypeError, binascii.Error):
        raise SignatureError("That isn't a valid phone key.")
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise SignatureError("The phone's key must be an ECDSA P-256 key.")
    return key


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes,
              body_hash: str | None = None) -> bytes:
    """``body_hash`` (hex SHA-256) instead of ``body`` for a file that's checked as it arrives (files.py)."""
    digest = body_hash.lower() if body_hash is not None else hashlib.sha256(body).hexdigest()
    return "\n".join((method.upper(), path, timestamp, nonce, digest)).encode("utf-8")


class NonceCache:
    """Remembers recent nonces so a recorded request can't be sent again."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def use(self, nonce: str) -> bool:
        now = self.clock()
        with self._lock:
            if len(self._seen) > 1000:
                self._seen = {n: t for n, t in self._seen.items() if now - t < NONCE_SECONDS}
            if nonce in self._seen and now - self._seen[nonce] < NONCE_SECONDS:
                return False
            self._seen[nonce] = now
            return True


def verify(public_key: str, method: str, path: str, timestamp: str | None, nonce: str | None,
           signature: str | None, body: bytes, nonces: NonceCache, clock=time.time,
           body_hash: str | None = None) -> None:
    """Raises SignatureError unless the request was signed just now by this phone's key."""
    if not (timestamp and nonce and signature):
        raise SignatureError("The request isn't signed.")
    if not (8 <= len(nonce) <= 64) or not nonce.replace("-", "").replace("_", "").isalnum():
        raise SignatureError("The request isn't signed properly.")
    try:
        sent = int(timestamp)
    except ValueError:
        raise SignatureError("The request isn't signed properly.")
    if body_hash is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", body_hash):
        raise SignatureError("The request isn't signed properly.")
    if abs(clock() - sent) > MAX_SKEW_SECONDS:
        raise SignatureError("The phone's clock and the PC's clock disagree by more than two minutes.")
    try:
        raw = base64.b64decode(signature, validate=True)
        load_public_key(public_key).verify(raw, canonical(method, path, timestamp, nonce, body, body_hash),
                                           ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, binascii.Error):
        raise SignatureError("The request wasn't signed by the paired phone.")
    if not nonces.use(nonce):
        raise SignatureError("That request was already used.")
