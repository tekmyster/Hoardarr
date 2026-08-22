from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretStoreError(RuntimeError):
    pass


class SecretBox:
    """Encrypt small credentials with record-bound authenticated encryption."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise SecretStoreError("the Hoardarr secret key must contain exactly 32 bytes")
        self._key = key
        self.key_id = hashlib.sha256(key).hexdigest()[:16]
        self._cipher = AESGCM(key)

    @classmethod
    def from_file(cls, path: Path, *, create: bool) -> SecretBox:
        if not path.exists():
            if not create:
                raise SecretStoreError(f"secret key does not exist: {path}")
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            encoded = base64.urlsafe_b64encode(key) + b"\n"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(path, flags, 0o600)
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise SecretStoreError(f"secret key must be a regular file, not a symlink: {path}")
        if os.name == "posix" and file_stat.st_uid != os.geteuid():
            raise SecretStoreError(f"secret key must be owned by the service user: {path}")
        try:
            encoded_key = path.read_bytes().strip()
            key = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
        except (OSError, ValueError, TypeError) as exc:
            raise SecretStoreError(f"could not read a valid secret key from {path}") from exc
        if os.name == "posix" and file_stat.st_mode & 0o077:
            raise SecretStoreError(f"secret key permissions must be 0600: {path}")
        return cls(key)

    @staticmethod
    def _aad(record_type: str, record_id: str) -> bytes:
        return f"hoardarr:v1:{record_type}:{record_id}".encode()

    def encrypt(self, record_type: str, record_id: str, plaintext: str) -> bytes:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext.encode(),
            self._aad(record_type, record_id),
        )
        envelope: dict[str, Any] = {
            "version": 1,
            "key_id": self.key_id,
            "nonce": base64.urlsafe_b64encode(nonce).decode(),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode(),
        }
        return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()

    def fingerprint(self, purpose: str, plaintext: str) -> str:
        """Return a non-reversible, key-bound identifier for idempotency checks."""

        return hmac.new(
            self._key,
            f"hoardarr:v1:{purpose}:".encode() + plaintext.encode(),
            hashlib.sha256,
        ).hexdigest()

    def decrypt(self, record_type: str, record_id: str, envelope_bytes: bytes) -> str:
        try:
            envelope = json.loads(envelope_bytes)
            if envelope.get("version") != 1 or envelope.get("key_id") != self.key_id:
                raise SecretStoreError("credential uses an unsupported encryption key")
            nonce = base64.urlsafe_b64decode(envelope["nonce"])
            ciphertext = base64.urlsafe_b64decode(envelope["ciphertext"])
            plaintext = self._cipher.decrypt(
                nonce,
                ciphertext,
                self._aad(record_type, record_id),
            )
            return plaintext.decode()
        except SecretStoreError:
            raise
        except (InvalidTag, KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise SecretStoreError("credential could not be authenticated or decrypted") from exc
