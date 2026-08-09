"""TradingView server-side data source — direct WebSocket to prodata.tradingview.com.

WARNING: This data source connects to TradingView's backend servers
directly using an unofficial protocol. TradingView's Terms of Service
explicitly restrict:

  - Performing automated trading or algorithmic decision-making
    using extracted data
  - Scraping or non-display usage of their platform and data
  - Connecting to their servers or infrastructure via this method
    (the only sanctioned access is via the locally running Desktop app,
    which is what the `pinchtab` data source uses)

Account-ban risk: TradingView can detect this access pattern and
ban the user's account. The auth-token mechanism they use is rotated
periodically to break unofficial clients.

Maintenance burden: this implementation may break at any time when
TradingView updates their protocol. The community has rebuilt this
client several times.

The user has explicitly accepted these costs. This module exists
because the user asked for it, not because it's a good idea.

How it works:
  1. User logs into TradingView in a browser, extracts the
     `sessionid` cookie value from the browser's DevTools
  2. User passes the sessionid to mavam via config or env var
  3. mavam opens a WebSocket to `wss://prodata.tradingview.com/socket.io/1/`
  4. mavam sends a `chart_create_session` message with the sessionid
  5. mavam requests symbol + resolution data
  6. TradingView sends bar data as socket.io frames

The protocol is socket.io v1 long-polling fallback over WebSocket.
Each message is framed: `~m~<length>~m~<json>`.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.request
import urllib.parse
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..core.profile import Bar


DEFAULT_STALE_AFTER_SECONDS = 300

# TradingView's chart-data WebSocket. The cluster (`hk` here) can be
# different — TradingView rotates clusters. This is a real maintenance
# cost: when they rotate, this URL stops working.
_TV_WS_URL = "wss://prodata.tradingview.com/socket.io/1/?cluster=hk"

# How to extract the sessionid:
#   1. Open tradingview.com in Chrome
#   2. Log in
#   3. DevTools → Application → Cookies → tradingview.com
#   4. Find `sessionid` (or `session_id` in some versions)
#   5. Copy its value
_TV_SESSION_ENV = "TRADINGVIEW_SESSION_ID"


@dataclass(frozen=True)
class TradingViewServerConfig:
    """A server-side TradingView data source (unofficial, ToS-restricted)."""

    ticker: str
    exchange: str = "NASDAQ"
    interval: str = "5"     # resolution in minutes (1, 5, 15, 60, 1D, 1W, 1M)
    session_id: str = ""    # TradingView sessionid cookie; "" = unauthenticated
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS


def _interval_to_tv_resolution(interval: str) -> tuple[str, int]:
    """Convert our interval string to TradingView's (resolution, timeframe).

    TradingView uses 1|5|15|30|60|120|240|1D|1W|1M for resolution.
    timeframe is 1 for intraday, 7 for daily, 30 for weekly, 1 for monthly.
    """
    s = str(interval).strip().upper()
    if s in ("1D", "D", "DAY"):
        return "1D", 1
    if s in ("1W", "W", "WEEK"):
        return "1W", 1
    if s in ("1M", "M", "MONTH"):
        return "1M", 1
    # Intraday
    try:
        minutes = int(s)
        return str(minutes), 1
    except (TypeError, ValueError):
        return "5", 1  # default to 5min


def _read_socket_io_frame(sock) -> Optional[str]:
    """Read one socket.io v1 frame from a connected socket.

    Frame format: ~m~<length>~m~<json>  OR  ~h~<ping>  OR  <json>
    """
    # Read until we have the full frame header
    header = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            return None
        header += ch
        # After ~m~ we need length
        if header.startswith(b"~m~"):
            # Find the next ~m~
            try:
                idx = header.index(b"~m~", 4)
            except ValueError:
                continue
            length_str = header[3:idx].decode("ascii")
            length = int(length_str)
            # Read the rest
            body = header[idx + 3:]
            while len(body) < length:
                chunk = sock.recv(length - len(body))
                if not chunk:
                    return None
                body += chunk
            return body.decode("utf-8", errors="replace")
        elif header.startswith(b"~h~"):
            # heartbeat: ~h~4~h~<4 bytes> — ignore
            return None
        else:
            # Plain JSON frame (no ~m~ framing) — read until newline
            while b"\n" not in header:
                ch = sock.recv(1)
                if not ch:
                    return None
                header += ch
            line, _, rest = header.partition(b"\n")
            return line.decode("utf-8", errors="replace") + (b"" if not rest else b"")
    return None


def _send_socket_io_frame(sock, payload: str) -> None:
    """Send a socket.io v1 message with the ~m~ framing."""
    body = payload.encode("utf-8")
    frame = f"~m~{len(body)}~m~".encode("ascii") + body
    sock.sendall(frame)


def fetch_tradingview_bars(
    config: TradingViewServerConfig,
    *,
    as_of_unix: Optional[int] = None,
) -> Optional[List[Bar]]:
    """Fetch OHLCV bars from TradingView's server-side WebSocket.

    Returns None on any failure (network, auth, unknown symbol, etc.).
    The caller treats None as a hard error.

    NOTE: This function uses TradingView's sessionid if provided.
    Without one, TradingView allows limited unauthenticated access
    (rate-limited, fewer symbols, less history). For full access,
    a valid paid-account sessionid is required.
    """
    as_of = as_of_unix if as_of_unix is not None else int(time.time())
    apply_staleness = config.stale_after_seconds > 0
    cutoff = as_of - config.stale_after_seconds if apply_staleness else None

    # Allow sessionid from config OR env var
    session_id = config.session_id or os.environ.get(_TV_SESSION_ENV, "")

    resolution, _ = _interval_to_tv_resolution(config.interval)
    symbol = f"{config.exchange}:{config.ticker}"

    # Open raw TLS WebSocket via stdlib. We avoid the `websockets` library
    # here to keep the dependency tree minimal for this opt-in path.
    raw_sock = None
    try:
        # Parse the wss URL
        u = urllib.parse.urlparse(_TV_WS_URL)
        host = u.hostname
        port = u.port or 443
        # TCP
        raw_sock = socket.create_connection((host, port), timeout=15)
        # TLS
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(raw_sock, server_hostname=host)
        # WebSocket handshake
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {u.path}?{u.query} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
        )
        if session_id:
            handshake += f"Cookie: sessionid={session_id}\r\n"
        handshake += "\r\n"
        sock.sendall(handshake.encode("ascii"))
        # Read response headers
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n", 1)[0]:
            return None
        # WebSocket frame helpers
        def ws_send_text(payload: str) -> None:
            data = payload.encode("utf-8")
            header = bytes([0x81])  # FIN + text
            mask_bit = 0x80
            length = len(data)
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if length < 126:
                sock.sendall(bytes([header[0] | mask_bit, length | 0x80]) + mask + masked)
            elif length < 65536:
                sock.sendall(bytes([header[0] | mask_bit, 126 | 0x80]) + length.to_bytes(2, "big") + mask + masked)
            else:
                sock.sendall(bytes([header[0] | mask_bit, 127 | 0x80]) + length.to_bytes(8, "big") + mask + masked)

        def ws_recv_text() -> Optional[str]:
            # Read 2-byte header
            hdr = sock.recv(2)
            if len(hdr) < 2:
                return None
            fin = hdr[0] & 0x80
            op = hdr[0] & 0x0F
            masked = hdr[1] & 0x80
            length = hdr[1] & 0x7F
            if length == 126:
                length = int.from_bytes(sock.recv(2), "big")
            elif length == 127:
                length = int.from_bytes(sock.recv(8), "big")
            mask = sock.recv(4) if masked else b""
            data = b""
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    return None
                data += chunk
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x1:  # text
                return data.decode("utf-8", errors="replace")
            elif op == 0x8:  # close
                return None
            elif op == 0x9:  # ping
                # Send pong
                pong = bytes([0x8A]) + bytes([length | 0x80]) + os.urandom(4) + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                sock.sendall(pong)
                return ws_recv_text()
            # else ignore
            return ws_recv_text()

        # Socket.io handshake: connect to namespace
        ws_send_text("0")
        # Read until we get a "0" message back (handshake ack)
        deadline = time.time() + 5
        while time.time() < deadline:
            msg = ws_recv_text()
            if msg is None:
                return None
            if msg == "0":
                break

        # Send connect packet (socket.io v1 = 40 for connect)
        ws_send_text('40{"session":"mavam-cli"}')

        # Now build a chart session and request data
        # The protocol uses "qs" (quote session) and "cs" (chart session)
        chart_session = "cs_" + os.urandom(4).hex()
        # Send create_chart_session
        create_msg = (
            '42["chart_create_session","' + chart_session + '","",'
            '{"symbol":"' + symbol + '","resolution":"' + resolution + '",'
            '"use_session_id_for_requester":true}]'
        )
        ws_send_text(create_msg)

        # Wait for the session-created response
        chart_session_ready = False
        deadline = time.time() + 10
        bars_data: List[Bar] = []
        while time.time() < deadline and not bars_data:
            msg = ws_recv_text()
            if msg is None:
                return None
            if msg.startswith("42"):
                # JSON payload
                try:
                    payload = json.loads(msg[2:])
                    if isinstance(payload, list) and len(payload) >= 2:
                        ev = payload[1]
                        # Could be ["series_completed", [data...]]
                        if ev == "series_completed" and len(payload) >= 3:
                            series_data = payload[2]
                            # Format: [meta, {s: "ok", v: [...]}, ...]
                            for item in series_data:
                                if isinstance(item, dict) and "v" in item:
                                    for bar in item["v"]:
                                        # bar: {"i": 0, "v": [...]}
                                        if isinstance(bar, dict) and "v" in bar:
                                            v = bar["v"]
                                            if len(v) >= 5:
                                                # [time, open, high, low, close, volume]
                                                ts = v[0]
                                                # TV uses seconds
                                                if ts < 1_000_000_000:
                                                    ts = ts * 1  # already seconds
                                                if cutoff is not None and ts < cutoff:
                                                    continue
                                                raw_vol = v[5] if len(v) > 5 else 0
                                                bars_data.append(Bar(
                                                    timestamp_unix=int(ts),
                                                    open=float(v[1]),
                                                    high=float(v[2]),
                                                    low=float(v[3]),
                                                    close=float(v[4]),
                                                    volume=float(raw_vol or 0.0),
                                                ))
                        elif ev == "chart_session_ready":
                            chart_session_ready = True
                except (json.JSONDecodeError, TypeError, ValueError, IndexError):
                    pass

        if not bars_data:
            return None

        bars_data.sort(key=lambda b: b.timestamp_unix)
        return bars_data

    except Exception:
        return None
    finally:
        if raw_sock is not None:
            try:
                raw_sock.close()
            except Exception:
                pass


__all__ = [
    "TradingViewServerConfig",
    "DEFAULT_STALE_AFTER_SECONDS",
    "fetch_tradingview_bars",
    "_TV_SESSION_ENV",
]
