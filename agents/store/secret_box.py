"""Encrypts destination secrets for storage (AES-256-GCM), so a database leak reveals nothing.

The key is 3lay's own, never given to clients: in production it's a Key Vault
secret (read through App Configuration, like the backend's other secrets);
locally it's SECRETS_ENCRYPTION_KEY in agents/.env. Only the pipeline (to sign
deliveries) and the backend (to show or rotate a secret) can use it.

Stored form:  v1:<key version>:<base64 of nonce + ciphertext + tag>

- The key version says which key encrypted it, so the key can be rotated:
  add the new key, re-encrypt gradually, keep the old one until nothing uses it.
- Each encryption uses a fresh random nonce, so the same secret never encrypts
  to the same text twice.
- The destination ID is bound in as "associated data": a ciphertext only
  decrypts for the row it was made for, so someone with database access can't
  move one client's secret into another client's row.
"""

import base64
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "v1"
KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # the size AES-GCM is designed for


class SecretBoxError(Exception):
    """A stored secret can't be decrypted: wrong key, wrong row, or tampered with."""


def generate_key():
    """A new encryption key, base64-encoded, for Key Vault (or agents/.env locally)."""
    return base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")


def decode_key(key_b64):
    key = base64.b64decode(key_b64)
    if len(key) != KEY_BYTES:
        raise ValueError(f"An encryption key is {KEY_BYTES} bytes, base64-encoded")
    return key


def encrypt(plaintext, key_b64, key_version, associated):
    """The stored form of plaintext, bound to `associated` (e.g. the destination ID)."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(decode_key(key_b64)).encrypt(
        nonce, plaintext.encode("utf-8"), associated.encode("utf-8"))
    return f"{FORMAT}:{key_version}:{base64.b64encode(nonce + sealed).decode('ascii')}"


def decrypt(stored, keys, associated):
    """The plaintext from its stored form. keys is {key version: base64 key}."""
    try:
        version_tag, key_version, payload = stored.split(":", 2)
        key_version = int(key_version)
    except ValueError as exc:
        raise SecretBoxError("Not a stored secret") from exc
    if version_tag != FORMAT:
        raise SecretBoxError(f"Unknown format {version_tag!r}")
    if key_version not in keys:
        raise SecretBoxError(f"No key for version {key_version}")
    raw = base64.b64decode(payload)
    nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    try:
        plaintext = AESGCM(decode_key(keys[key_version])).decrypt(
            nonce, sealed, associated.encode("utf-8"))
    except InvalidTag as exc:
        raise SecretBoxError("Can't decrypt: wrong key, wrong row, or tampered with") from exc
    return plaintext.decode("utf-8")


def keys_from_environment():
    """(current version, {version: key}) from the environment.

    SECRETS_ENCRYPTION_KEY and SECRETS_ENCRYPTION_KEY_VERSION (default 1) are
    the current key; during a key rotation, SECRETS_ENCRYPTION_KEY_PREVIOUS and
    SECRETS_ENCRYPTION_KEY_PREVIOUS_VERSION keep older secrets readable.
    """
    current = os.environ.get("SECRETS_ENCRYPTION_KEY")
    if not current:
        raise RuntimeError("SECRETS_ENCRYPTION_KEY isn't set: make one with "
                           "scripts/save_configuration.py --generate-key")
    version = int(os.environ.get("SECRETS_ENCRYPTION_KEY_VERSION", "1"))
    keys = {version: current}
    previous = os.environ.get("SECRETS_ENCRYPTION_KEY_PREVIOUS")
    if previous:
        keys[int(os.environ["SECRETS_ENCRYPTION_KEY_PREVIOUS_VERSION"])] = previous
    return version, keys
