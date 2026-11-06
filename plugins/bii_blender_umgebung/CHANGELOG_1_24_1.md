# Umgebung 1.24.1

Basis: Umgebung-1.24.0-certificate-fix.zip; alle Funktionen aus 1.24.0 bleiben enthalten.

- HTTPS-Kartenabrufe verwenden einen gemeinsamen, geprüften TLS-Kontext.
- Systemzertifikate (unter Windows ROOT und CA mit passendem Vertrauenszweck)
  werden durch Pythons Standard-SSL-Verarbeitung geladen.
- Falls certifi verfügbar ist, ergänzt dessen CA-Bündel den Systemzertifikatsspeicher.
- Zertifikats- und Hostnamenprüfung bleiben aktiv. Keine zusätzlichen Abhängigkeiten.
- Versionsangaben aktualisiert und Umlaute im Manifest korrigiert.

Installation: In Blender über Einstellungen > Add-ons > Menü > Von Festplatte
installieren das ZIP auswählen. Nach dem Update Blender neu starten.

Der Fix betrifft TLS-Zertifikate bei Kartenabrufen. Er behebt keine falschen
Kachel-Adressen, fehlenden Kartendienste, HTTP-Fehler oder ungültigen Bilddateien.
