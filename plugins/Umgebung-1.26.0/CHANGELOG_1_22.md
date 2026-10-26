# Umgebung 1.22.0

Im Menü Gleisachsen gibt es jetzt „Gleisobjekte“ mit den Optionen „Ein gemeinsames Objekt“ (Standard) und „Einzelteile mit Gleisnummern“.

Einzelteile entstehen an OSM-Way-Grenzen und Weichen. Die Objektbezeichnung übernimmt ausschließlich railway:track_ref als Gleisnummer. ref auf einem Gleis bezeichnet eine Strecke und wird nicht als Gleisnummer missverstanden. Fehlende Nummern werden mit „Gleis ohne Nummer [OSM …]“ gekennzeichnet. Mehrere Abschnitte dürfen dieselbe betriebliche Gleisnummer haben; OSM-ID und Blender-Suffixe unterscheiden ihre Objektnamen. Ursprungs-Way-IDs und Gleisnummern bleiben als Objekteigenschaften erhalten. Kein Bevel und keine Extrusion.

Die Wahl gilt auch für zukünftige Importe. Weichen bleiben unabhängig davon einzelne benannte Objekte in „Weichen“, sofern ihre Separierung eingeschaltet ist. Beim Wechsel der Importart und erneuten Erzeugen werden die zuvor vom Plugin erzeugten Gleise ersetzt.

Quelle: https://wiki.openstreetmap.org/wiki/Key:railway:track_ref

Geprüft: beide Modi, OSM-Zuordnung nach Gebietsbeschnitt, keine Änderung der Gesamtgleislänge, längstes Einzelgleis, Wiederholung, exakte Weichenanschlüsse und Geländeauflage. In der aktuell geöffneten Szene wurden nur die normalen Gleise ersetzt. Vorhandene Weichen, Kilometrierungen und deren Shrinkwrap wurden beibehalten. Sicherung vor Änderung: work/scene_before_122.blend.
