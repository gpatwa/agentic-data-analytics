"""Connection-target controls (threat T6): a customer-supplied hostname must be a real vendor warehouse host.

A tenant types a hostname; before anything connects it must be a vendor domain, not an IP, not a private address, and
exactly the account the admin said it was. The resolved addresses are recorded so a later change can be caught.

What this does not do: drivers resolve names themselves, so these checks do not pin the connection. Real pinning needs
egress controls at the network layer; `verify_still_pinned` only detects a changed answer at the moments we call it.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass

VENDOR_SUFFIXES: dict[str, tuple[str, ...]] = {
    "snowflake": (".snowflakecomputing.com",),
    "databricks": (".cloud.databricks.com", ".azuredatabricks.net", ".gcp.databricks.com"),
}
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_FORBIDDEN_CHARS = set("/@:?#\\ \t\r\n%;,'\"`<>[]{}()|&$!*")

Resolver = Callable[[str], Iterable[str]]


class TargetRejected(ValueError):
    """The hostname is not an acceptable warehouse target. `code` is stable and safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ValidatedTarget:
    engine: str
    host: str
    account: str  # the host with the vendor suffix removed
    pinned: frozenset[str]


def system_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def _public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    # An IPv4-mapped IPv6 address must be judged as the IPv4 address it carries.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not (ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved)


def validate_target(
    engine: str, host: str, *, expected_account: str, resolver: Resolver = system_resolver
) -> ValidatedTarget:
    suffixes = VENDOR_SUFFIXES.get(engine)
    if suffixes is None:
        raise TargetRejected("unsupported_engine")
    if not isinstance(host, str) or not host or host != host.strip():
        raise TargetRejected("invalid_host")
    name = host.lower()
    if any(ch in _FORBIDDEN_CHARS for ch in name) or not name.isascii():
        raise TargetRejected("invalid_host")
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise TargetRejected("ip_address_not_allowed")
    if name.endswith("."):
        raise TargetRejected("invalid_host")
    labels = name.split(".")
    if any(not _LABEL.match(label) for label in labels) or any(label.startswith("xn--") for label in labels):
        raise TargetRejected("invalid_host")
    suffix = next((s for s in suffixes if name.endswith(s)), None)
    if suffix is None:
        raise TargetRejected("not_a_vendor_domain")
    account = name[: -len(suffix)]
    if not account:
        raise TargetRejected("not_a_vendor_domain")
    if account != str(expected_account).strip().lower():
        raise TargetRejected("account_mismatch")
    try:
        addresses = frozenset(str(a) for a in resolver(name))
    except Exception as exc:  # noqa: BLE001 - a failed lookup is a rejection, never a pass.
        raise TargetRejected("dns_failed") from exc
    if not addresses:
        raise TargetRejected("dns_failed")
    if not all(_public(a) for a in addresses):
        raise TargetRejected("non_public_address")
    return ValidatedTarget(engine, name, account, addresses)


def verify_still_pinned(target: ValidatedTarget, *, resolver: Resolver = system_resolver) -> None:
    """Raise if the name now resolves to anything that is not public or was not seen at validation time."""
    try:
        now = frozenset(str(a) for a in resolver(target.host))
    except Exception as exc:  # noqa: BLE001
        raise TargetRejected("dns_failed") from exc
    if not now or not all(_public(a) for a in now):
        raise TargetRejected("non_public_address")
    if not now <= target.pinned:
        raise TargetRejected("address_changed")
