"""Two small helpers for the Connect-mode setup worker: run one admin statement, and make a key pair.

They are deliberately separate from the sandbox admin tool (`app/execution`, the live-validation provisioner): a test keeps
the runtime API from importing that tool, because it signs in as an admin and creates and drops fixed-name sandboxes. The
setup worker needs only these generic pieces, so it carries its own copy rather than reaching into that tool.
"""

from __future__ import annotations

import base64
from typing import Any


class AdminSqlError(Exception):
    """A statement failed. `code` is the step and the exception type only: a driver message can carry SQL or a token."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def run_statement(conn: Any, statement: str, code: str) -> list[dict[str, Any]]:
    """Run one statement on an admin connection and return its rows as dicts keyed by lower-case column name."""
    cursor = conn.cursor()
    try:
        cursor.execute(statement)
        if getattr(cursor, "description", None):
            names = [str(d[0]).lower() for d in cursor.description]
            return [dict(zip(names, row, strict=False)) for row in cursor.fetchall()]
        return []
    except Exception as exc:  # noqa: BLE001 - the type and step only
        raise AdminSqlError(f"{code}:{type(exc).__name__}"[:60]) from None
    finally:
        cursor.close()


def new_key_pair() -> tuple[bytes, str]:
    """A fresh 2048-bit RSA key: the private key as PKCS8 DER and the public key body for Snowflake."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(public).decode("ascii")
