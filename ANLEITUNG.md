# Digitaler Bilderrahmen — Anleitung

Ein eigenständiger digitaler Bilderrahmen für den Raspberry Pi. Er lädt Fotos
direkt aus deinen geteilten Alben (Synology Photos, Google Photos, Immich) und
zeigt sie als Vollbild-Diashow.

> English documentation: see [README.md](README.md).

## Was du brauchst

- **Raspberry Pi Zero 2 W** oder neuer (das Image ist 64-Bit/arm64).
- microSD-Karte (mind. 8 GB) und ein Kartenlesegerät.
- Display mit HDMI (optional mit Touch für Wischen/Einstellungen).
- Ein WLAN und mindestens ein geteiltes Fotoalbum.

## Schnellstart (fertiges Image)

1. **Image herunterladen:** die neueste Datei `digital-photo-frame-*.img.xz`
   von der [Releases-Seite](https://github.com/rwkaspar/digital_photo_frame/releases).
2. **Mit dem [Raspberry Pi Imager](https://www.raspberrypi.com/software/) flashen:**
   *OS wählen → Eigenes Image verwenden* → die `.img.xz` auswählen.
   Im Anpassungsdialog **WLAN**, **Sprache/Zeitzone** setzen und bei Bedarf
   **SSH aktivieren**. Diese Einstellungen gelten zusätzlich zum Image.
3. **Karte einlegen und Pi einschalten.** Der erste Start richtet sich selbst
   ein (ein paar Minuten) und zeigt dann den Einrichtungs-Assistenten.
4. **Alben einrichten** über den Assistenten am Bildschirm. Falls kein WLAN
   gesetzt wurde, spannt der Rahmen ein WLAN **`PhotoFrame-Setup`** auf —
   damit verbinden und der Anleitung im Browser folgen.

**Zugang:** Benutzer `frame`, Passwort `photoframe` — nach dem ersten Login
mit `passwd` ändern. Bei aktiviertem SSH erreichbar unter `photoframe.local`.

## Bedienung

- **Diashow:** läuft automatisch. Mit **Wischen** nach links/rechts manuell
  weiter-/zurückblättern.
- **Einstellungen:** die **Ecke oben rechts** ca. 3 Sekunden gedrückt halten.
- **Ausrichtung:** in den Einstellungen zwischen **Horizontal** und
  **Vertikal** umschalten (bei Vertikal dreht sich die Oberfläche mit).
- **Helligkeit:** Regler in den Einstellungen — steuert die echte
  Display-Helligkeit. „Auto" nutzt (falls vorhanden) den Umgebungslichtsensor.
- **Uhr:** optionale Uhr-Einblendung (Position und Größe wählbar).
- **Schlafzeiten:** pro Wochentag einstellbar. Im Schlaf schaltet der Rahmen
  das Display aus, holt Updates und synchronisiert Fotos (inkl. Videos).
  Antippen weckt ihn.

## Fotos & Videos

- Unterstützte Quellen: **Synology Photos**, **Google Photos**, **Immich**
  (jeweils geteilte Alben / Freigabe-Links).
- Fotos werden auf dem Pi passend zur Ausrichtung aufbereitet; Querformate im
  Hochformat (und umgekehrt) bekommen einen weichen Unschärfe-Hintergrund.
- **Videos** werden unterstützt und während der Schlafphase aufbereitet
  (spart Ressourcen im laufenden Betrieb).

## Fehlerbehebung

- **Kein Bild / nur Platzhalter:** Es sind noch keine Alben konfiguriert —
  Einstellungen öffnen und ein geteiltes Album hinzufügen.
- **Kein WLAN:** Mit dem Hotspot `PhotoFrame-Setup` verbinden und WLAN im
  Browser-Portal eintragen.
- **Touch reagiert nicht:** USB-Kabel des Touchscreens kurz ab- und wieder
  anstecken.
- **Status prüfen** (per SSH):
  ```bash
  systemctl status photo_frame_server photo_frame_cage
  ```

## Updates

Der Rahmen aktualisiert sich beim Start bzw. während der Schlafphase
automatisch aus GitHub. Manuell:

```bash
bash ~/digital_photo_frame/scripts/deploy_update.sh
```
