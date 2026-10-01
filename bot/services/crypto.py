"""Symmetric encryption helpers (Fernet) for School 21 passwords."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CryptoService:
    """
    Encrypt / decrypt user passwords before they touch SQLite.

    The Fernet key MUST come from ``ENCRYPTION_KEY`` in the environment.
    Plaintext passwords exist only briefly in process memory.
    """

    def __init__(self, encryption_key: str) -> None:
        key = encryption_key.strip().encode("utf-8")
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "ENCRYPTION_KEY is invalid. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        """Return a URL-safe base64 Fernet token (str)."""
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, token: str) -> str:
        """Decrypt a Fernet token back to plaintext password."""
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "Failed to decrypt password. ENCRYPTION_KEY may have changed."
            ) from exc
