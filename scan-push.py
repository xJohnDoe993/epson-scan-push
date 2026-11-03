#!/usr/bin/env python3
"""Epson "Scan to Computer" (Push-Scan) Empfaenger fuer den ET-4800 - ersetzt den Windows Event Manager.

Protokoll (per Mitschnitt der Windows-Software ermittelt):
  1. Der Drucker fragt per Multicast (UDP 239.255.255.253:2968) eine SLPv2-AttrRqst fuer
     "service:NetScanMonitor-agent" (Scope "PID 1185") nach Empfaengern.
  2. Wir antworten per Unicast (UDP Drucker:2968) mit einer SLPv2-AttrRply
     "(ClientName=..),(IPAddress=..),(EventPort=2968)", XID aus der Anfrage. Damit erscheint der
     Rechner am Drucker unter Scannen -> Computer.
  3. Waehlt man dort ein Ziel, verbindet sich der Drucker per TCP auf EventPort und sendet
     "POST /PushScan" (SOAP, PushScanIDIn = gewaehltes Ziel). Wir bestaetigen mit "200 OK / StatusOut=OK".
  4. Danach scannen wir (scan2paperless; Windows verbindet sich hier auf TCP 1865 des Druckers).
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
    """SLPv2 AttrRply (Function 7) wie vom Event Manager gesendet."""
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
    """Klartext 127.0.0.1:1865 -> TLS Drucker:1865.

    Der ET-4800 erwartet fuer den Netzwerkscan (ESC/I-2) TLS (selbstsigniertes Zertifikat); das
    epsonds-Backend von SANE 1.2.1 spricht nur Klartext. Mit diesem Tunnel kann es trotzdem scannen:
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
            # TCP_NODELAY: ESC/I-2 besteht aus vielen kleinen Anfrage/Antwort-Paketen; ohne wird jede
            # Runde durch Nagle + verzoegertes ACK um ~40 ms gebremst (Scan dauerte sonst > 1 Minute).
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            raw = socket.create_connection((PRINTER, 1865), timeout=10)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            raw.settimeout(None)
            up = ctx.wrap_socket(raw, server_hostname=PRINTER)
        except OSError as e:
            log(f"TLS-Tunnel: Verbindung zum Drucker fehlgeschlagen: {e}")
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
    log("TLS-Tunnel: 127.0.0.1:1865 -> Drucker:1865 (TLS)")
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
        # Der Event Manager sendet die Antwort 3x im Abstand von ca. 0.5 s (UDP ist unzuverlaessig)
        pkt = build_attr_reply(xid, NAME, my_ip, PORT)
        for _ in range(3):
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tx.sendto(pkt, (addr[0], PORT))
            tx.close()
            time.sleep(0.5)

    log(f"Discovery: warte auf Anfragen an {MCAST}:{PORT}, melde mich als '{NAME}' ({my_ip})")
    while True:
        data, addr = rx.recvfrom(2048)
        if addr[0] != PRINTER or len(data) < 12 or data[0] != 2 or data[1] != 6 or SERVICE not in data:
            continue
        xid = data[10:12]
        now = time.time()
        if now - last.get(xid, 0) < 3:  # Drucker wiederholt die Anfrage 3x
            continue
        last[xid] = now
        log(f"AttrRqst von {addr[0]} (xid {xid.hex()}), antworte")
        threading.Thread(target=answer, args=(xid, addr), daemon=True).start()


SCAN_BUSY = threading.Lock()


def adf_loaded():
    """True/False laut eSCL-Status des Druckers (Papier im Einzug?), None wenn nicht ermittelbar."""
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
    # Der Drucker sendet den Push teils doppelt: laeuft schon ein Scan, ignorieren.
    if not SCAN_BUSY.acquire(blocking=False):
        log(f"Scan laeuft bereits, PushScan (ID {push_id}) ignoriert")
        return
    try:
        _run_scan(push_id)
    finally:
        SCAN_BUSY.release()


def _run_scan(push_id):
    env = dict(os.environ)
    # Nach einem Push haelt der Drucker die Sitzung offen und erwartet die Netzwerkverbindung
    # (TCP 1865, TLS) - ein USB-Scan scheitert dann mit I/O-Fehler (SANE-Status 9). Deshalb ueber den
    # TLS-Tunnel (s. tls_tunnel). Das ist ca. 2x langsamer als USB (Flachbett 300 dpi: 52 s statt 26 s).
    env.setdefault("SCAN_DEVICE", "epsonds:net:127.0.0.1")
    # Gepatchtes epsonds (siehe scanner/epsonds-jpeg.patch): JPEG statt RAW vom Drucker. Mit RAW (~49 MB
    # pro A4-Seite) staut sich der Scan an der TLS-Strecke des Druckers (Schlitten fuhr ruckelnd).
    env.setdefault("EPSONDS_JPEG", "1")
    # Quelle direkt waehlen: Der ADF-Versuch von scan2* wartet bei leerem Einzug ~26 s (3 Versuche), so lange
    # haelt der Drucker die Push-Sitzung nicht offen (bricht ab, "blinkt 2x und ist weg").
    loaded = adf_loaded()
    sources = ("flatbed",) if loaded is False else ("adf", "flatbed")
    # Nur wenn der Einzug sicher belegt ist, die (langsamen) Wiederholungen bei "leer" zulassen; sonst
    # sofort auf das Flachbett ausweichen (scan2folder liest SCAN_TRIES, scan2paperless ignoriert es).
    env.setdefault("SCAN_TRIES", "3" if loaded else "1")
    log(f"Einzug belegt: {loaded}")
    for source in sources:
        log(f"Scan startet (Ziel-ID {push_id}, Quelle {source}, Geraet {env['SCAN_DEVICE']})")
        r = subprocess.run([SCAN_CMD, source], capture_output=True, text=True, env=env)
        out = (r.stdout.strip() or r.stderr.strip())[-300:]
        log(f"{os.path.basename(SCAN_CMD)} {source}: rc={r.returncode} {out}")
        if r.returncode == 0:
            return
        if r.returncode != 2:  # 2 = keine Seiten (z.B. Einzug leer) -> Flachbett versuchen
            return


class PushHandler(StreamRequestHandler):
    def handle(self):
        if self.client_address[0] != PRINTER:
            log(f"TCP von unbekannter Adresse {self.client_address[0]} ignoriert")
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
        log(f"PushScan vom Drucker: ID={push_id} ({line})")
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
    log(f"Push-Scan-Server auf TCP {PORT}")
    Server(("", PORT), PushHandler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
