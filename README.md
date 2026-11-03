# Epson ET-4800: "Scan to Computer" ohne Windows-Software

Nachbau des Epson Event Managers als kleiner Python-Dienst (`scan-push.py`, nur Standardbibliothek).
Am Drucker erscheint der Rechner unter *Scannen -> Computer*; ein Druck auf den Eintrag startet den Scan
(Einzug, bei leerem Einzug Flachbett) und legt ein PDF ab.

Getestet mit dem Epson ET-4800 auf **Debian 13** (Ziel: Paperless-Eingang) und **Fedora 44** (Ziel: Ordner).
Andere Epson-Modelle mit `epsonds`-Backend und dem Menüpunkt "Computer" sollten ähnlich funktionieren,
sind aber ungetestet. Die IPs unten (`10.0.0.x`) sind Platzhalter.

## Protokoll (per Mitschnitt der Windows-Software ermittelt)
- **UDP 2968, SLPv2:** Der Drucker fragt per Multicast `239.255.255.253` (AttrRqst, `service:NetScanMonitor-agent`,
  Scope `PID 1185`) nach Empfängern. Wir antworten per Unicast an Drucker:2968 mit einer AttrRply
  `(ClientName=..),(IPAddress=..),(EventPort=2968)` (XID aus der Anfrage, 3x im Abstand von 0,5 s).
  Ohne Antwort zeigt der Drucker "ungültig".
- **TCP 2968:** Der Drucker sendet `POST /PushScan` (SOAP, `PushScanIDIn` = Ziel). Wir antworten `200 OK` mit
  `StatusOut=OK`.
- **Scan:** Port 1865 des Druckers spricht **TLS 1.2** (selbstsigniert). SANE `epsonds` kann nur Klartext, deshalb
  läuft im Dienst ein Klartext->TLS-Tunnel auf `127.0.0.1:1865`; Scan-Gerät ist `epsonds:net:127.0.0.1`.
- **Quellenwahl:** Vor dem Scan fragt der Dienst per eSCL (`/eSCL/ScannerStatus`), ob Papier im Einzug liegt.
  Der Drucker bricht die Push-Sitzung nach ca. 20 s ab, daher keine langen Wiederholungen. Doppelte Pushes
  während eines Scans werden ignoriert.
- Das Flachbett wird immer mit `--batch-count=1` gescannt, sonst scannt `scanimage --batch` endlos.
- Nicht gelöst: Der USB-Eintrag "Computer via USB" lässt sich unter Linux nicht auslösen.

## JPEG-Patch (wichtig für die Geschwindigkeit)
`epsonds` fordert vom Drucker RAW an (~49 MB je A4/300 dpi). Das staut sich an der TLS-Strecke des Druckers:
der Schlitten ruckelt, eine Einzugsseite dauert ~78 s. Der Drucker kann JPEG (~0,3 MB). `epsonds-jpeg.patch`
erzwingt JPEG, wenn die Umgebungsvariable `EPSONDS_JPEG` gesetzt ist (`scan-push` setzt sie).
Ergebnis: Einzugsseite ~23 s, Flachbett 300 dpi ~28 s. Das System-SANE bleibt sonst unverändert.

## Debian (Paperless) - `debian/`
1. `sane-utils img2pdf python3` installieren, `libsane-epsonds` mit Patch bauen und ersetzen
   (Debian-Quellpaket `sane-backends`, Patch anwenden, `autoreconf -fi`, `./configure BACKENDS=epsonds`,
   `make -C lib sanei; make -C backend libsane-epsonds.la`, `.libs/libsane-epsonds.so` an die Stelle von
   `/usr/lib/x86_64-linux-gnu/sane/libsane-epsonds.so.1.x.y` kopieren; Original vorher sichern).
   **Ein Update von `libsane1` überschreibt den Patch**, dann neu bauen.
2. `scan-push.py` und `debian/scan2paperless` nach `/usr/local/bin/`, `debian/scan-push.service` nach
   `/etc/systemd/system/`, dann `systemctl enable --now scan-push`.
3. In `scan2paperless` den Pfad `CONSUME` (Standard `/opt/paperless_data/consume`) anpassen.
4. Konfiguration per Umgebungsvariablen in der Unit: `EPSON_PRINTER` (Standard `10.0.0.119`), `CLIENT_NAME`
   (Name am Drucker, max. 15 Zeichen, Standard Hostname), `SCAN_CMD`.

## Fedora (Notebook) - `fedora/`
Gleicher Dienst als systemd-User-Unit; PDFs landen in `~/Dokumente/Scans` (`SCAN_DIR`). Läuft nur, solange
das Notebook im Heimnetz ist.
1. Einmalig: `sudo dnf install autoconf automake libtool gettext-devel autoconf-archive libjpeg-turbo-devel libusb1-devel gcc make python3-img2pdf`
2. `fedora/build-epsonds-jpeg.sh` ausführen (lädt SANE 1.4.0, patcht, baut `epsonds` nach
   `~/.local/lib/sane-jpeg/`). Bei 1.4.0 reicht der Patch im `OPT_MODE`-Handler nicht; das Skript setzt
   `mode_jpeg` zusätzlich dort, wo der PARA-Befehl gebaut wird.
3. `scan-push.py` und `fedora/scan2folder` nach `~/.local/bin/`, `fedora/scan-push.service` nach
   `~/.config/systemd/user/`, `CLIENT_NAME` anpassen, dann `systemctl --user enable --now scan-push`.
4. Firewall: Zone `FedoraWorkstation` lässt 1025-65535 tcp/udp zu, Port 2968 braucht keine Freigabe.

## Fehlersuche
`journalctl -u scan-push` bzw. `journalctl --user -u scan-push`. Zeigt der Drucker "ungültig", kommt die
SLP-Antwort nicht an (Multicast im Netz/VLAN, Firewall auf UDP 2968).
