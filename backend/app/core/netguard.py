"""Where the server may connect on a user's behalf (webhooks, mailbox IMAP/SMTP).

A user-supplied host is refused when it resolves to this machine, the cluster or cloud internals:
loopback, link-local (including the 169.254.169.254 metadata service), unspecified, multicast, reserved and
private address ranges (RFC 1918, carrier-grade NAT, IPv6 unique-local). Private servers a company really uses
(an on-premises Exchange, an internal integration hub) are allowed by listing them in
``OUTBOUND_ALLOWED_HOSTS`` (host names or CIDR ranges). Mailboxes are also limited to the standard mail ports, so
a mailbox can't be used to probe other services.

The check runs when the address is saved and again right before each connection, so a DNS name that later
points inside the network (rebinding) is caught at use. A name that doesn't resolve is allowed at save time
(the connection will fail on its own).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings

IMAP_PORTS = {993, 143}
SMTP_PORTS = {25, 465, 587, 2525}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BlockedDestination(ValueError):
    pass


def _allowlist() -> tuple[set[str], list]:
    names, nets = set(), []
    for item in (settings.outbound_allowed_hosts or "").split(","):
        item = item.strip().lower()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            names.add(item)
    return names, nets


def _internal(ip: ipaddress._BaseAddress) -> bool:
    return (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_private
            or (ip.version == 4 and ip in _CGNAT))


def check_host(host: str | None, what: str = "That address") -> None:
    host = (host or "").strip().strip("[]").lower().rstrip(".")
    if not host:
        raise BlockedDestination(f"{what} needs a host name")
    names, nets = _allowlist()
    if host in names:
        return
    if host == "localhost" or host.endswith((".localhost", ".internal", ".local")):
        raise BlockedDestination(f"{what} points at this server or an internal network")
    try:
        addrs = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addrs = {ipaddress.ip_address(a[4][0].split("%")[0]) for a in socket.getaddrinfo(host, None)}
        except (socket.gaierror, UnicodeError, ValueError):
            return
    for ip in addrs:
        if _internal(ip) and not any(ip in n for n in nets):
            raise BlockedDestination(f"{what} points at this server or an internal network "
                                     "(an administrator can allow specific internal hosts with OUTBOUND_ALLOWED_HOSTS)")


def check_url(url: str, what: str = "That URL") -> str:
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BlockedDestination(f"{what} must be an http(s) URL")
    if u.username or u.password:
        raise BlockedDestination(f"{what} can't contain a user name or password")
    check_host(u.hostname, what)
    return url.strip()


def check_mail_server(host: str | None, port: int | None, kind: str) -> None:
    allowed = IMAP_PORTS if kind == "imap" else SMTP_PORTS
    if port is not None and port not in allowed:
        raise BlockedDestination(f"{kind.upper()} port must be one of {', '.join(str(p) for p in sorted(allowed))}")
    check_host(host, f"The {kind.upper()} server")
