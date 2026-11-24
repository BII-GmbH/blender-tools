# Umgebung 1.30.0 – Gelände nur unter Plänen

Basis: Umgebung-1.29.1; alle Funktionen bleiben enthalten.

## Neu

- **„Nur unter Plänen“** (Gelände im Korridor laden): lädt das Gelände nur dort, wo flache
  Lagepläne des BII Import liegen, plus einen Korridorradius davor und dahinter. Bisher
  entstand das Gelände entlang der ganzen Kilometrierungslinie, auch weit über die Pläne
  hinaus. Wo zwei Kilometrierungslinien nebeneinander laufen (etwa in einem Bahnhof), wird das
  Gelände nur einmal erzeugt; vorher lagen zwei gleiche Flächen übereinander und flimmerten.
- Die Geländeabschnitte behalten ihre Stationen auf der ganzen Linie, die Kartentextur
  schneidet daher weiter den richtigen Abschnitt aus.
