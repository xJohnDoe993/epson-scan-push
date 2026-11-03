# Epson ET-4800: "Scan to Computer" without the Windows software

A re-implementation of the Epson Event Manager as a small Python service (`scan-push.py`, standard library only).
The computer shows up on the printer under *Scan -> Computer*; selecting it starts the scan
(document feeder, or flatbed if the feeder is empty) and stores the result as a PDF.

Tested with the Epson ET-4800 on **Debian 13** (target: Paperless consume folder) and **Fedora 44** (target: folder).
Other Epson models that use the `epsonds` backend and have a "Computer" menu entry should work similarly,
but are untested. The IPs below (`10.0.0.x`) are placeholders.

Note: the code comments and log messages in the scripts are in German.

## Protocol (reverse-engineered from a capture of the Windows software)
- **UDP 2968, SLPv2:** The printer sends a multicast request to `239.255.255.253` (AttrRqst,
  `service:NetScanMonitor-agent`, scope `PID 1185`) asking for receivers. We answer by unicast to printer:2968 with an
  AttrRply `(ClientName=..),(IPAddress=..),(EventPort=2968)` (XID copied from the request, sent 3 times, 0.5 s apart).
  Without a reply the printer shows "invalid".
- **TCP 2968:** The printer sends `POST /PushScan` (SOAP, `PushScanIDIn` = selected target). We reply `200 OK` with
  `StatusOut=OK`.
- **Scan:** Port 1865 on the printer speaks **TLS 1.2** (self-signed). SANE `epsonds` only supports plain text, so the
  service runs a plain-text -> TLS tunnel on `127.0.0.1:1865`; the scan device is `epsonds:net:127.0.0.1`.
- **Source selection:** Before scanning, the service asks the printer via eSCL (`/eSCL/ScannerStatus`) whether paper
  is in the feeder. The printer aborts the push session after about 20 s, so there are no long retries. Duplicate
  pushes during a scan are ignored.
- The flatbed is always scanned with `--batch-count=1`; otherwise `scanimage --batch` rescans the glass endlessly.
- Not solved: the printer's "Computer via USB" entry cannot be triggered from Linux.

## JPEG patch (important for speed)
`epsonds` requests RAW data from the printer (~49 MB per A4 page at 300 dpi). This clogs the printer's TLS link:
the carriage moves in jerks and one feeder page takes ~78 s. The printer can deliver JPEG (~0.3 MB).
`epsonds-jpeg.patch` forces JPEG when the environment variable `EPSONDS_JPEG` is set (`scan-push` sets it).
Result: ~23 s per feeder page, ~28 s for a 300 dpi flatbed scan. The system SANE is otherwise left untouched.

## Debian (Paperless) - `debian/`
1. Install `sane-utils img2pdf python3`, then build and replace `libsane-epsonds` with the patch applied
   (Debian source package `sane-backends`, apply the patch, `autoreconf -fi`, `./configure BACKENDS=epsonds`,
   `make -C lib sanei; make -C backend libsane-epsonds.la`, copy `.libs/libsane-epsonds.so` over
   `/usr/lib/x86_64-linux-gnu/sane/libsane-epsonds.so.1.x.y`; back up the original first).
   **An update of `libsane1` overwrites the patch**, so rebuild after such an update.
2. Copy `scan-push.py` and `debian/scan2paperless` to `/usr/local/bin/` and `debian/scan-push.service` to
   `/etc/systemd/system/`, then `systemctl enable --now scan-push`.
3. Adjust the `CONSUME` path in `scan2paperless` (default `/opt/paperless_data/consume`).
4. Configuration via environment variables in the unit: `EPSON_PRINTER` (default `10.0.0.119`), `CLIENT_NAME`
   (name shown on the printer, max. 15 characters, default hostname), `SCAN_CMD`.

## Fedora (laptop) - `fedora/`
The same service as a systemd user unit; PDFs go to `~/Dokumente/Scans` (`SCAN_DIR`). It only works while the
laptop is on the home network.
1. Once: `sudo dnf install autoconf automake libtool gettext-devel autoconf-archive libjpeg-turbo-devel libusb1-devel gcc make python3-img2pdf`
2. Run `fedora/build-epsonds-jpeg.sh` (downloads SANE 1.4.0, patches it, builds `epsonds` into
   `~/.local/lib/sane-jpeg/`). With 1.4.0 the patch in the `OPT_MODE` handler is not enough; the script also sets
   `mode_jpeg` where the PARA command is built.
3. Copy `scan-push.py` and `fedora/scan2folder` to `~/.local/bin/` and `fedora/scan-push.service` to
   `~/.config/systemd/user/`, adjust `CLIENT_NAME`, then `systemctl --user enable --now scan-push`.
4. Firewall: the `FedoraWorkstation` zone allows 1025-65535 tcp/udp; port 2968 needs no opening.

## Troubleshooting
`journalctl -u scan-push` or `journalctl --user -u scan-push`. If the printer shows "invalid", the SLP reply is not
arriving (multicast on the network/VLAN, firewall on UDP 2968).
