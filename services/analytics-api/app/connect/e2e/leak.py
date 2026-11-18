"""A scanner that fails the live run if a secret's text shows up where it must not: a log line, a response, the audit chain.

Secrets are registered as they come into existence (the sign-in token, the authorization code, the PKCE verifier, the service
key); the scanner remembers them only to look for them and never repeats one: a finding names the secret's label and where it
was seen, nothing else.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|PRIVATE KEY", re.I)
MIN_SECRET_CHARS = 8


@dataclass(frozen=True)
class Finding:
    secret: str  # the label, never the value
    where: str


@dataclass
class LeakScanner:
    _secrets: dict[str, str] = field(default_factory=dict, repr=False)
    findings: list[Finding] = field(default_factory=list)

    def watch(self, label: str, value: str | bytes | None) -> None:
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        if value and len(value) >= MIN_SECRET_CHARS:
            self._secrets[label] = value

    def scan(self, where: str, text: str, *, expected: tuple[str, ...] = ()) -> None:
        """`expected` names secrets that may legitimately appear in this place (the sign-in code arrives in the callback's address)."""
        hits = [label for label, value in self._secrets.items() if value in text and label not in expected] + (["pem_private_key"] if _PEM.search(text) else [])
        for label in hits:
            finding = Finding(label, where)
            if finding not in self.findings:  # one record can reach the scanner through two handlers
                self.findings.append(finding)

    def labels_in(self, text: str) -> list[str]:
        """Which watched secrets appear in `text`, by label. Records nothing."""
        found = [label for label, value in self._secrets.items() if value in text]
        return found + (['pem_private_key'] if _PEM.search(text) else [])

    @property
    def clean(self) -> bool:
        return not self.findings

    def summary(self) -> dict[str, object]:
        return {"clean": self.clean, "watched": sorted(self._secrets), "findings": [{"secret": f.secret, "where": f.where} for f in self.findings]}

    def __repr__(self) -> str:
        return f"LeakScanner(watched={sorted(self._secrets)}, findings={len(self.findings)})"


class ScanningLogHandler(logging.Handler):
    """Feeds every log record, formatted, to the scanner."""

    def __init__(self, scanner: LeakScanner) -> None:
        super().__init__(level=logging.DEBUG)
        self.scanner = scanner

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("httpx", "httpcore")):  # a test client's own request log, not the app's
            return
        try:
            self.scanner.scan(f"log:{record.name}", self.format(record) + " " + " ".join(str(a) for a in (record.args or ())))
        except Exception:  # noqa: BLE001 - a scanner must never break the thing it watches
            pass
