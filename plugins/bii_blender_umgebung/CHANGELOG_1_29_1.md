# Umgebung 1.29.1 – Projektionsmittelpunkt bei jedem Bauschritt

Basis: Umgebung-1.29.0; alle Funktionen bleiben enthalten.

## Fehlerbehebung

- **Projektionsmittelpunkt immer in der Szene.** Bisher wurde `scene["lat"]` /
  `scene["lon"]` nur beim Laden der Gleisdaten geschrieben. „Gelände im Korridor laden“,
  „Kartentextur laden“, „Gebiet als Fläche laden“ und „Gebäude erzeugen“ rechneten mit
  einem eigenen Mittelpunkt, ohne ihn zu speichern. Eine Szene nur aus Karte, Gelände oder
  Gebäuden hatte damit keinen Mittelpunkt, und andere Add-ons (BLOSM, BII Import) fanden
  keinen. Jetzt hinterlegt jeder Bauschritt einen neuen Mittelpunkt sofort.
