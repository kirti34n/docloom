"""Encryption at rest for stored secrets (provider API keys, etc.).

Secret settings are Fernet-encrypted before they touch the DB, so a leaked
studio.db never exposes a usable key. The Fernet key comes from, in order:

  1. DOCLOOM_SECRET_KEY env var (a urlsafe-base64 32-byte Fernet key) — use
     this in production/containers so the key lives outside the data volume.
  2. data_dir()/secret.key — auto-generated once, chmod 600. Fine for a
     single-node/desktop install.

Values are stored with an ``enc:`` prefix so we can tell ciphertext from any
legacy plaintext and decrypt only what we wrote — the migration is lazy and
transparent (a plaintext secret is re-encrypted next time it's saved).
"""

from __future__ import annotations

import os

from .settings import data_dir

_PREFIX = "enc:"
_fernet = None
_loaded = False


def _load():
    global _fernet, _loaded
    if _loaded:
        return _fernet
    _loaded = True
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover - cryptography is a core dep
        _fernet = None
        return None

    key = os.environ.get("DOCLOOM_SECRET_KEY", "").strip()
    if key:
        _fernet = Fernet(key.encode())
        return _fernet

    key_path = data_dir() / "secret.key"
    if key_path.is_file():
        _fernet = Fernet(key_path.read_bytes())
    else:
        new_key = Fernet.generate_key()
        key_path.write_bytes(new_key)
        try:
            key_path.chmod(0o600)
        except OSError:  # pragma: no cover - non-POSIX filesystems
            pass
        _fernet = Fernet(new_key)
    return _fernet


def available() -> bool:
    return _load() is not None


def encrypt(plaintext: str) -> str:
    """Return an ``enc:``-tagged ciphertext, or the plaintext unchanged if it's
    empty or no key backend is available."""
    if not plaintext:
        return plaintext
    f = _load()
    if f is None:
        return plaintext
    return _PREFIX + f.encrypt(plaintext.encode()).decode("ascii")


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def decrypt(value: str) -> str:
    """Reverse encrypt(). Non-``enc:`` values (legacy plaintext) pass through;
    an undecryptable ciphertext returns '' rather than raising."""
    if not is_encrypted(value):
        return value
    f = _load()
    if f is None:
        return ""
    try:
        return f.decrypt(value[len(_PREFIX):].encode()).decode()
    except Exception:
        return ""
