"""The one place a sign-in redirect address is judged.

Production: a plain `https` URL with a hostname (not an IP), the default port, a path, and no query, fragment or credentials.
**Sandbox only:** with `allow_local_dev=True` an `http://localhost:<port>/<path>` or `http://127.0.0.1:<port>/<path>` address is
also accepted, so a developer can run the sign-in on their own machine against a throwaway integration (the live end-to-end
check does this). The switch defaults to off, is not reachable from any request or environment variable, and every caller that
turns it on says so in its name.
"""

from __future__ import annotations

import ipaddress
import urllib.parse

LOCAL_DEV_HOSTS = frozenset({"localhost", "127.0.0.1"})
_BAD_CHARS = " '\"`\\\n\r\t;"


def validate_redirect_uri(uri: str, *, allow_local_dev: bool = False) -> str:
    """Return `uri` unchanged or raise `ValueError` saying why not."""
    if not isinstance(uri, str) or not uri or len(uri) > 200 or uri != uri.strip() or any(c in uri for c in _BAD_CHARS):
        raise ValueError("invalid redirect")
    parsed = urllib.parse.urlparse(uri)
    host = parsed.hostname or ""
    plain_path = parsed.path.startswith("/") and ".." not in parsed.path
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not plain_path:
        raise ValueError("the redirect must have a plain path and no query, fragment or credentials")
    if allow_local_dev and parsed.scheme == "http":
        if host not in LOCAL_DEV_HOSTS or parsed.port is None or not 1024 <= parsed.port <= 65535:
            raise ValueError("a development redirect must be http://localhost or http://127.0.0.1 with a port from 1024 to 65535")
        return uri
    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_ip = False
    else:
        is_ip = True
    if is_ip:
        raise ValueError("the redirect must use a hostname, not an IP address")
    if parsed.scheme != "https" or "." not in host or parsed.port is not None:
        raise ValueError("the redirect must be a plain https URL with a hostname and a path, and no port")
    return uri


def is_local_dev_redirect(uri: str) -> bool:
    return urllib.parse.urlparse(uri).scheme == "http"
