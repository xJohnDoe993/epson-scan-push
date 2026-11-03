#!/usr/bin/env python3
"""Epson "Scan to Computer" (push scan) receiver for the ET-4800 - replaces the Windows Event Manager.

Protocol (reverse-engineered from a capture of the Windows software):
  1. The printer sends a multicast SLPv2 AttrRqst (UDP 239.255.255.253:2968) for
     "service:NetScanMonitor-agent" (scope "PID 1185") asking for receivers.
  2. We answer by unicast (UDP printer:2968) with an SLPv2 AttrRply
     "(ClientName=..),(IPAddress=..),(EventPort=2968)", XID copied from the request. This makes the
     computer appear on the printer under Scan -> Computer.
  3. When a target is selected there, the printer connects to EventPort over TCP and sends
     "POST /PushScan" (SOAP, PushScanIDIn = selected target). We confirm with "200 OK / StatusOut=OK".
  4. Then we scan (SCAN_CMD, e.g. scan2paperless; Windows connects to TCP 1865 on the printer here).
"""
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from socketserver import StreamRequestHandler, ThreadingTCPServer

PRINTER = os.environ.get("EPSON_PRINTER", "10.0.0.119")
PORT = 2968
MCAST = "239.255.255.253"
NAME = os.environ.get("CLIENT_NAME", socket.gethostname().upper())[:15]
SCAN_CMD = os.environ.get("SCAN_CMD", "/usr/local/bin/scan2paperless")
SERVICE = b"NetScanMonitor-agent"

RESPONSE_BODY = (
    '<?xml version="1.0" ?>\r\n'
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">\r\n'
    "  <s:Body>\r\n"
    '    <p:PushScanResponse xmlns:p="http://schema.epson.net/EpsonNet/Scan/2004/pushscan">\r\n'
    "      <StatusOut>OK</StatusOut>\r\n"
    "    </p:PushScanResponse>\r\n"
    "  </s:Body>\r\n"
    "</s:Envelope>\r\n"
)


def log(*a):
    print(*a, flush=True)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((PRINTER, PORT))
        return s.getsockname()[0]
    finally:
        s.close()


def build_attr_reply(xid, name, ip, port):
    """SLPv2 AttrRply (function 7) as sent by the Event Manager."""
    attrs = f"(ClientName={name}),(IPAddress={ip}),(EventPort={port})".encode()
    body = b"\x00\x02en" + b"\x00\x00" + struct.pack(">H", len(attrs)) + attrs + b"\x00"
    total = 12 + len(body)
    return bytes([2, 7]) + total.to_bytes(3, "big") + b"\x00\x00" + b"\x00\x00\x00" + xid + body


def build_http_response(uid):
    body = RESPONSE_BODY.encode()
    head = (
        "HTTP/1.0 200 OK\r\n"
        "Server : Epson Net Scan Monitor/2.0\r\n"
        "Content-Type : application/octet-stream\r\n"
        f"Content-Length : {len(body)}\r\n"
        "x-protocol-name : Epson Network Service Protocol\r\n"
        "x-protocol-version : 2.00\r\n"
        f"x-uid : {uid}\r\n"
        "x-status : 0001\r\n\r\n"
    )
    return head.encode() + body


def tls_tunnel():
    """Plain text 127.0.0.1:1865 -> TLS printer:1865.

    For network scans (ESC/I-2) the ET-4800 expects TLS (self-signed certificate); the SANE
    epsonds backend only speaks plain text. With this tunnel it can still scan:
    SCAN_DEVICE=epsonds:net:127.0.0.1
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    def pump(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def handle(c):
        try:
            # TCP_NODELAY: ESC/I-2 consists of many small request/response packets; without it every
            # round trip is slowed by ~40 ms (Nagle + delayed ACK), and a scan takes over a minute.
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            raw = socket.create_connection((PRINTER, 1865), timeout=10)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            raw.settimeout(None)
            up = ctx.wrap_socket(raw, server_hostname=PRINTER)
        except OSError as e:
            log(f"TLS tunnel: connection to printer failed: {e}")
            c.close()
            return
        threading.Thread(target=pump, args=(c, up), daemon=True).start()
        pump(up, c)
        c.close()
        up.close()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 1865))
    srv.listen(4)
    log("TLS tunnel: 127.0.0.1:1865 -> printer:1865 (TLS)")
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def discovery_loop(my_ip):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("", PORT))
    mreq = socket.inet_aton(MCAST) + socket.inet_aton(my_ip)
    rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    last = {}

    def answer(xid, addr):
        # The Event Manager sends the reply 3 times, ~0.5 s apart (UDP is unreliable)
        pkt = build_attr_reply(xid, NAME, my_ip, PORT)
        for _ in range(3):
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tx.sendto(pkt, (addr[0], PORT))
            tx.close()
            time.sleep(0.5)

    log(f"Discovery: waiting for requests on {MCAST}:{PORT}, announcing as '{NAME}' ({my_ip})")
    while True:
        data, addr = rx.recvfrom(2048)
        if addr[0] != PRINTER or len(data) < 12 or data[0] != 2 or data[1] != 6 or SERVICE not in data:
            continue
        xid = data[10:12]
        now = time.time()
        if now - last.get(xid, 0) < 3:  # the printer repeats the request 3 times
            continue
        last[xid] = now
        log(f"AttrRqst from {addr[0]} (xid {xid.hex()}), replying")
        threading.Thread(target=answer, args=(xid, addr), daemon=True).start()


SCAN_BUSY = threading.Lock()


def adf_loaded():
    """True/False per the printer's eSCL status (paper in the feeder?), None if unknown."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(f"https://{PRINTER}/eSCL/ScannerStatus", timeout=5, context=ctx) as r:
            body = r.read()
    except (OSError, ValueError):
        return None
    if b"ScannerAdfLoaded" in body:
        return True
    if b"ScannerAdfEmpty" in body:
        return False
    return None


def run_scan(push_id):
    # The printer sometimes sends the push twice: ignore it if a scan is already running.
    if not SCAN_BUSY.acquire(blocking=False):
        log(f"Scan already running, ignoring PushScan (ID {push_id})")
        return
    try:
        _run_scan(push_id)
    finally:
        SCAN_BUSY.release()


def _run_scan(push_id):
    env = dict(os.environ)
    # After a push the printer keeps the session open and expects the network connection
    # (TCP 1865, TLS) - a USB scan then fails with an I/O error (SANE status 9). So go through the
    # TLS tunnel (see tls_tunnel).
    env.setdefault("SCAN_DEVICE", "epsonds:net:127.0.0.1")
    # Patched epsonds (see epsonds-jpeg.patch): JPEG instead of RAW from the printer. With RAW (~49 MB
    # per A4 page) the scan clogs the printer's TLS link (the carriage moves in jerks).
    env.setdefault("EPSONDS_JPEG", "1")
    # Pick the source directly: the ADF attempt of scan2* waits ~26 s (3 tries) when the feeder is empty,
    # but the printer does not keep the push session open that long (it aborts).
    loaded = adf_loaded()
    sources = ("flatbed",) if loaded is False else ("adf", "flatbed")
    # Only allow the (slow) retries on "empty" if the feeder is known to be loaded; otherwise fall
    # back to the flatbed immediately (scan2folder reads SCAN_TRIES, scan2paperless ignores it).
    env.setdefault("SCAN_TRIES", "3" if loaded else "1")
    log(f"Feeder loaded: {loaded}")
    for source in sources:
        log(f"Scan starting (target ID {push_id}, source {source}, device {env['SCAN_DEVICE']})")
        r = subprocess.run([SCAN_CMD, source], capture_output=True, text=True, env=env)
        out = (r.stdout.strip() or r.stderr.strip())[-300:]
        log(f"{os.path.basename(SCAN_CMD)} {source}: rc={r.returncode} {out}")
        if r.returncode == 0:
            return
        if r.returncode != 2:  # 2 = no pages (e.g. feeder empty) -> try the flatbed
            return


class PushHandler(StreamRequestHandler):
    def handle(self):
        if self.client_address[0] != PRINTER:
            log(f"TCP from unknown address {self.client_address[0]} ignored")
            return
        self.request.settimeout(10)
        line = self.rfile.readline().decode("latin1").strip()
        headers = {}
        while True:
            h = self.rfile.readline().decode("latin1").strip()
            if not h:
                break
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        if not line.startswith("POST /PushScan"):
            self.wfile.write(b"HTTP/1.0 404 Not Found\r\n\r\n")
            return
        m = re.search(r"<PushScanIDIn>([^<]*)<", body)
        push_id = m.group(1) if m else "?"
        log(f"PushScan from printer: ID={push_id} ({line})")
        self.wfile.write(build_http_response(headers.get("x-uid", "0")))
        self.wfile.flush()
        threading.Thread(target=run_scan, args=(push_id,), daemon=True).start()


class Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    my_ip = local_ip()
    threading.Thread(target=discovery_loop, args=(my_ip,), daemon=True).start()
    threading.Thread(target=tls_tunnel, daemon=True).start()
    log(f"Push scan server on TCP {PORT}")
    Server(("", PORT), PushHandler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
