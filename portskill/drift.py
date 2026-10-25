"""Read-only drift detection: saved registry state vs what is running now.

A range drifts when the registry says it is ``active`` but the machine
disagrees: the recorded process is gone, or nothing listens on any of its
ports. Nothing here mutates a range; status reports drift, and only ``sweep``
with an explicit opt-in acts on it.

How listening is checked depends on whether the tracked process is alive:

- not alive (including ranges activated externally, with no pid): a loopback
  connect first, then, only when nothing answers, a bind test over every port
  of the range on every local address. Stale (and so release) depends on the
  bind test, and a loopback connect alone would miss a service bound to a LAN
  address, so a stale verdict could free a port that is still in use. An
  answer already proves the port is held, so a healthy external service is
  never bound.
- alive: a loopback connect only. Binding a live service's ports, on every
  status call and UI poll, could make a lazily-binding dev server fail with
  EADDRINUSE while it starts. The cost: a live service listening only on a
  LAN address shows ``port_unbound``, a hint that never leads to a release.

Callers holding the registry lock (``sweep``) pass ``connect=False``: no
loopback connect runs, a live range is not probed at all (it can never be
released, so it reports no drift there), and a not-alive range gets the bind
test only.
"""
from __future__ import annotations

import errno
import socket
from typing import Any

from .lifecycle import range_has_live_process

PID_DEAD = "pid_dead"
NO_PID = "no_pid"
PORT_UNBOUND = "port_unbound"
UNTRACKED_LISTENER = "untracked_listener"

_IPV6_UNAVAILABLE = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}


def port_held(port: int) -> bool:
    """True when something holds ``port`` on any local address.

    Bind ``0.0.0.0:port`` then ``[::]:port`` (IPv6-only), without
    SO_REUSEADDR, so a socket bound to any specific address (loopback, LAN,
    Tailscale) collides. Any error other than a missing IPv6 stack counts as
    held: when the port cannot be proven free, it is not stale.
    """
    for family, address in ((socket.AF_INET, ("0.0.0.0", port)), (socket.AF_INET6, ("::", port))):
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            if family == socket.AF_INET6:
                continue
            return True
        with sock:
            try:
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind(address)
            except OSError as exc:
                if family == socket.AF_INET6 and exc.errno in _IPV6_UNAVAILABLE:
                    continue
                return True
    return False


def range_ports(item: dict) -> list[int]:
    start, end = item.get("start"), item.get("end")
    if not isinstance(start, int) or not 0 < start < 65536:
        return []
    if not isinstance(end, int) or end < start:
        end = start
    return list(range(start, min(end, 65535) + 1))


def range_ports_held(item: dict) -> bool:
    """True when any port of the range is held (or none can be checked)."""
    ports = range_ports(item)
    return not ports or any(port_held(port) for port in ports)


_CONNECT_TIMEOUT = 0.15


def port_answers(port: int) -> bool:
    """True when something accepts a loopback connection on ``port``.

    Side-effect free for the listener (no bind), but blind to services bound
    to a non-loopback address only.
    """
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
                return True
        except OSError:
            pass
    return False


def range_ports_answer(item: dict) -> bool:
    """True when any port of the range answers on loopback (or none can be checked)."""
    ports = range_ports(item)
    return not ports or any(port_answers(port) for port in ports)


def range_ports_listening(item: dict) -> bool:
    """Connect-first held check for a range with no live tracked process.

    Every port gets a loopback connect before any port is bound: an answer
    proves the port is held, so the bind test (the stale decider) only runs
    when nothing answers anywhere in the range.
    """
    return range_ports_answer(item) or range_ports_held(item)


def _ports_label(item: dict) -> str:
    ports = range_ports(item)
    if len(ports) > 1:
        return f"ports {ports[0]}-{ports[-1]}"
    return f"port {ports[0]}" if ports else "its ports"


def range_drift(item: dict, *, connect: bool = True, live: bool | None = None) -> dict[str, Any] | None:
    """Return a drift record for a saved-active LOCAL range, or None.

    Callers must skip remote ranges: their process cannot be observed here.

    ``stale`` is true only when no tracked process is alive AND no port of
    the range is held on any local address: the one case where releasing
    cannot pull a port out from under something that still listens on it.
    The bind test runs only in that not-alive case, after a loopback connect
    found nothing; a live range is checked with a loopback connect (see the
    module docstring). ``connect=False`` runs no connect: a live range returns
    None unprobed and a not-alive range gets the bind test only. ``live``
    overrides the tracked-process check when the caller already decided it.
    """
    if (item.get("state") or "reserved") != "active":
        return None
    life = item.get("lifecycle") or {}
    tracked = bool(life.get("pid") or life.get("pgid"))
    if live is None:
        live = range_has_live_process(item)
    if live and not connect:
        return None
    if live:
        held = range_ports_answer(item)
    else:
        held = range_ports_listening(item) if connect else range_ports_held(item)
    ports = _ports_label(item)
    reasons: list[str] = []
    if tracked and not live:
        reasons.append(PID_DEAD)
    if not held:
        if not tracked:
            reasons.append(NO_PID)
        reasons.append(PORT_UNBOUND)
    if not reasons:
        return None
    stale = not live and not held
    if stale:
        hint = (
            f"Recorded as active, but no tracked process is alive and nothing listens on {ports}. "
            "Start it again, or run `portskill sweep` to review and `portskill sweep --release` to free it."
        )
    elif not live:
        reasons.append(UNTRACKED_LISTENER)
        hint = (
            f"The recorded process exited, but something still listens on {ports} (an untracked process). "
            "Stop that process or restart the service so Portskill tracks it; sweep will not release this range."
        )
    else:
        hint = (
            f"The tracked process is alive, but nothing answers on loopback on {ports}. It may still be "
            "starting, or listen only on another address: check its log, or restart it."
        )
    return {"reasons": reasons, "stale": stale, "hint": hint}


def drift_summary(ranges: list) -> dict[str, Any]:
    """Count drifted and stale ranges among enriched status ranges."""
    drifted = [r for r in ranges if isinstance(r, dict) and r.get("drift")]
    return {
        "count": len(drifted),
        "stale_count": sum(1 for r in drifted if r["drift"].get("stale")),
        "range_ids": [r.get("id") for r in drifted],
    }
