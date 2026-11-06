"""Auswahl des Kartenausschnitts - Kartenseite und kleiner lokaler Server.

Die Seite zeigt eine Kartenansicht, in der sich ein Rechteck aufziehen, an den
Ecken anfassen und verschieben lässt. Anders als früher wandern die Koordinaten
nicht über die Zwischenablage: Das Addon startet für die Dauer der Auswahl einen
winzigen HTTP-Server auf ``127.0.0.1`` (nur lokal erreichbar), liefert die Seite
von dort aus und nimmt das Ergebnis über ``/set?bbox=…`` direkt entgegen. Ein
Klick auf *Nach Blender übernehmen* setzt das Gebiet also unmittelbar.

Die Seite kommt ohne Kartenbibliothek aus, damit sie auch in abgeschotteten
Netzen läuft; gebraucht werden nur die Kacheln des eingestellten Servers. Die
Zwischenablage bleibt als zweiter Weg erhalten - für den Fall, dass der Browser
den lokalen Server nicht erreichen darf.
"""

import html
import http.server
import json
import os
import tempfile
import threading
import urllib.parse

DEFAULT_TILES = ("https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/"
                 "de_basemapde_web_raster_farbe/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png")
DEFAULT_SEARCH = "https://nominatim.openstreetmap.org/search"

# Auswahlseite des BLOSM-Addons (prochitecture.com) - dieselbe Karte, die BLOSM
# über "select" öffnet. Sie gibt das Gebiet als "bbox=west,süd,ost,nord" aus.
BLOSM_URL = "https://prochitecture.com/blender-osm/extent/"

_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gebiet auswählen – Kilometrierung</title>
<style>
  :root { color-scheme: light dark; --akzent: #d2451e; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 system-ui, -apple-system, sans-serif;
         background: #1d1d1d; color: #eee; overflow: hidden; }
  #karte { position: absolute; inset: 0 0 118px 0; overflow: hidden; background: #dfdcd6;
           cursor: grab; touch-action: none; user-select: none; }
  #karte.greift { cursor: grabbing; }
  #karte.zeichnet { cursor: crosshair; }
  #lagen { position: absolute; left: 0; top: 0; }
  #lagen img { position: absolute; width: 256px; height: 256px; }
  #rahmen { position: absolute; border: 2px solid var(--akzent);
            background: rgba(210,69,30,.14); display: none; cursor: move; }
  .griff { position: absolute; width: 14px; height: 14px; margin: -8px 0 0 -8px;
           background: #fff; border: 2px solid var(--akzent); border-radius: 3px; }
  .griff[data-ecke="nw"] { left: 0;    top: 0;    cursor: nwse-resize; }
  .griff[data-ecke="no"] { left: 100%; top: 0;    cursor: nesw-resize; }
  .griff[data-ecke="sw"] { left: 0;    top: 100%; cursor: nesw-resize; }
  .griff[data-ecke="so"] { left: 100%; top: 100%; cursor: nwse-resize; }
  #werkzeuge { position: absolute; right: 12px; top: 12px; display: flex;
               flex-direction: column; gap: 4px; }
  #werkzeuge button { width: 38px; height: 34px; font-size: 17px; cursor: pointer;
                      border: 1px solid #0003; background: #fffe; color: #222; border-radius: 4px; }
  #quelle { position: absolute; right: 0; bottom: 0; background: #fffc; color: #333;
            font-size: 11px; padding: 2px 6px; border-radius: 3px 0 0 0; }
  #leiste { position: absolute; left: 0; right: 0; bottom: 0; height: 118px;
            padding: 10px 14px; background: #1d1d1d; border-top: 1px solid #000; }
  .zeile { display: flex; align-items: center; gap: 6px; margin-bottom: 7px; }
  input { font: inherit; padding: 5px 7px; border: 1px solid #555; border-radius: 3px;
          background: #2a2a2a; color: #eee; }
  button { font: inherit; padding: 6px 12px; cursor: pointer; border-radius: 3px;
           border: 1px solid #555; background: #333; color: #eee; }
  button:hover { background: #3d3d3d; }
  button.haupt { background: var(--akzent); border-color: var(--akzent); color: #fff;
                 font-weight: 600; }
  button.aktiv { background: var(--akzent); border-color: var(--akzent); color: #fff; }
  #werte { width: 23em; font-family: ui-monospace, monospace; }
  #suche { width: 17em; }
  .hinweis { opacity: .7; font-size: 12.5px; }
  #status { color: #ffd9c9; }
  #treffer { position: absolute; left: 14px; top: 12px; max-width: 30em; background: #fff;
             color: #222; border-radius: 4px; box-shadow: 0 2px 12px #0006; display: none; }
  #treffer div { padding: 7px 10px; cursor: pointer; border-bottom: 1px solid #eee;
                 font-size: 13px; }
  #treffer div:hover { background: #eef2f7; }
</style>
</head>
<body>
<div id="karte">
  <div id="lagen"></div>
  <div id="rahmen">
    <div class="griff" data-ecke="nw"></div><div class="griff" data-ecke="no"></div>
    <div class="griff" data-ecke="sw"></div><div class="griff" data-ecke="so"></div>
  </div>
  <div id="werkzeuge"><button id="plus" title="näher">+</button>
    <button id="minus" title="weiter weg">−</button>
    <button id="zeigen" title="zur Auswahl springen">⤢</button></div>
  <div id="quelle">@@QUELLE@@</div>
</div>
<div id="treffer"></div>
<div id="leiste">
  <div class="zeile">
    <input id="suche" placeholder="Ort, Bahnhof oder Bauwerk suchen …">
    <button id="suchen">Suchen</button>
    <button id="modus">Rechteck aufziehen</button>
    <span class="hinweis">Karte ziehen = verschieben · Mausrad = Zoom ·
      Umschalt+Ziehen = neues Rechteck · Ecken anfassen = ändern</span>
  </div>
  <div class="zeile">
    <input id="werte" readonly>
    <button id="uebernehmen" class="haupt">Nach Blender übernehmen</button>
    <button id="kopieren">Koordinaten kopieren</button>
    <span id="groesse" class="hinweis"></span>
  </div>
  <div class="zeile"><span id="status" class="hinweis">@@STARTTEXT@@</span></div>
</div>
<script>
const TILES = @@TILES@@, SUCHE = @@SUCHE@@, MAXZOOM = @@MAXZOOM@@, ZURUECK = @@ZURUECK@@;
let zoom = @@ZOOM@@, mitte = {lat: @@LAT@@, lon: @@LON@@};
let auswahl = @@AUSWAHL@@;

const karte = document.getElementById('karte'), lagen = document.getElementById('lagen');
const rahmen = document.getElementById('rahmen'), werte = document.getElementById('werte');
const meldung = document.getElementById('status');

const lonZuX = (lon, z) => (lon + 180) / 360 * Math.pow(2, z) * 256;
const latZuY = (lat, z) => (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2
                            * Math.pow(2, z) * 256;
const xZuLon = (x, z) => x / (Math.pow(2, z) * 256) * 360 - 180;
const yZuLat = (y, z) => {
  const n = Math.PI - 2 * Math.PI * y / (Math.pow(2, z) * 256);
  return 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
};
const klemme = (v, a, b) => Math.max(a, Math.min(b, v));

/* Ursprung des Kartenbildes in Weltpixeln der aktuellen Zoomstufe */
function ursprung() {
  const b = karte.getBoundingClientRect();
  return {x: lonZuX(mitte.lon, zoom) - b.width / 2,
          y: latZuY(mitte.lat, zoom) - b.height / 2, b: b};
}
/* Mausposition -> geografische Koordinate */
function orten(e) {
  const u = ursprung();
  return {lon: xZuLon(u.x + e.clientX - u.b.left, zoom),
          lat: yZuLat(u.y + e.clientY - u.b.top, zoom)};
}

/* ---------------------------------------------------- Kachelebene
   Die Kacheln werden wiederverwendet statt bei jeder Bewegung neu erzeugt -
   sonst flackert die Karte beim Verschieben und bleibt grau. */
const bilder = new Map();

function kacheln(u) {
  const max = Math.pow(2, zoom);
  const x0 = Math.floor(u.x / 256), x1 = Math.floor((u.x + u.b.width) / 256);
  const y0 = Math.floor(u.y / 256), y1 = Math.floor((u.y + u.b.height) / 256);
  const gebraucht = new Set();
  for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
    if (y < 0 || y >= max) continue;
    const schluessel = zoom + '/' + x + '/' + y;
    gebraucht.add(schluessel);
    let img = bilder.get(schluessel);
    if (!img) {
      const kx = ((x % max) + max) % max;
      img = new Image();
      img.draggable = false;
      img.alt = '';
      img.src = TILES.replace('{z}', zoom).replace('{x}', kx)
                     .replace('{y}', y).replace('{-y}', max - 1 - y);
      img.onerror = () => { img.style.visibility = 'hidden'; };
      lagen.appendChild(img);
      bilder.set(schluessel, img);
    }
    img.style.display = '';
    img.style.left = (x * 256 - u.x) + 'px';
    img.style.top = (y * 256 - u.y) + 'px';
  }
  /* nicht sichtbare Kacheln nur ausblenden - beim Zurückschwenken sind sie
     dadurch sofort wieder da; erst bei sehr vielen wird aufgeräumt */
  for (const [schluessel, img] of bilder) {
    if (!gebraucht.has(schluessel)) {
      img.style.display = 'none';
      if (bilder.size > 400) { img.remove(); bilder.delete(schluessel); }
    }
  }
}

function zeichne() {
  const u = ursprung();
  kacheln(u);
  if (auswahl) {
    const l = lonZuX(auswahl.west, zoom) - u.x, r = lonZuX(auswahl.ost, zoom) - u.x;
    const o = latZuY(auswahl.nord, zoom) - u.y, s = latZuY(auswahl.sued, zoom) - u.y;
    rahmen.style.display = 'block';
    rahmen.style.left = l + 'px'; rahmen.style.top = o + 'px';
    rahmen.style.width = Math.max(1, r - l) + 'px';
    rahmen.style.height = Math.max(1, s - o) + 'px';
    werte.value = [auswahl.west, auswahl.sued, auswahl.ost, auswahl.nord]
      .map(v => v.toFixed(5)).join(',');
    const breite = (auswahl.ost - auswahl.west) * 111.32
                   * Math.cos((auswahl.sued + auswahl.nord) / 2 * Math.PI / 180);
    const hoehe = (auswahl.nord - auswahl.sued) * 111.32;
    document.getElementById('groesse').textContent =
      breite.toFixed(2) + ' × ' + hoehe.toFixed(2) + ' km';
  } else {
    rahmen.style.display = 'none';
    werte.value = '';
    document.getElementById('groesse').textContent = '';
  }
}

/* ---------------------------------------------------- Bedienung */
let zeichenModus = false;
const knopf = document.getElementById('modus');
knopf.onclick = () => {
  zeichenModus = !zeichenModus;
  knopf.classList.toggle('aktiv', zeichenModus);
  karte.classList.toggle('zeichnet', zeichenModus);
  knopf.textContent = zeichenModus ? 'Aufziehen beenden' : 'Rechteck aufziehen';
};

const GEGENECKE = {nw: 'so', no: 'sw', sw: 'no', so: 'nw'};
let zieht = null;

function ecke(name) {
  return {lon: (name === 'nw' || name === 'sw') ? auswahl.west : auswahl.ost,
          lat: (name === 'nw' || name === 'no') ? auswahl.nord : auswahl.sued};
}
function ausZweiPunkten(a, b) {
  return {west: Math.min(a.lon, b.lon), ost: Math.max(a.lon, b.lon),
          sued: Math.min(a.lat, b.lat), nord: Math.max(a.lat, b.lat)};
}

karte.addEventListener('pointerdown', e => {
  if (e.button !== 0) return;
  karte.setPointerCapture(e.pointerId);
  const ort = orten(e);
  if (e.target.classList.contains('griff')) {
    zieht = {art: 'ecke', anker: ecke(GEGENECKE[e.target.dataset.ecke])};
  } else if (e.target === rahmen && !e.shiftKey && !zeichenModus) {
    zieht = {art: 'schieben', start: ort, anfang: Object.assign({}, auswahl)};
  } else if (e.shiftKey || zeichenModus) {
    zieht = {art: 'ecke', anker: ort};
    auswahl = ausZweiPunkten(ort, ort);
  } else {
    zieht = {art: 'karte', x: e.clientX, y: e.clientY};
    karte.classList.add('greift');
  }
  if (zieht.art !== 'karte') e.preventDefault();
});

karte.addEventListener('pointermove', e => {
  if (!zieht) return;
  if (zieht.art === 'karte') {
    const u = ursprung();
    mitte = {lon: xZuLon(lonZuX(mitte.lon, zoom) - (e.clientX - zieht.x), zoom),
             lat: klemme(yZuLat(latZuY(mitte.lat, zoom) - (e.clientY - zieht.y), zoom), -85, 85)};
    zieht.x = e.clientX; zieht.y = e.clientY;
  } else if (zieht.art === 'ecke') {
    auswahl = ausZweiPunkten(zieht.anker, orten(e));
  } else {
    const ort = orten(e);
    const dLon = ort.lon - zieht.start.lon, dLat = ort.lat - zieht.start.lat;
    auswahl = {west: zieht.anfang.west + dLon, ost: zieht.anfang.ost + dLon,
               sued: zieht.anfang.sued + dLat, nord: zieht.anfang.nord + dLat};
  }
  zeichne();
});

function losgelassen() {
  if (zieht && zieht.art !== 'karte' && auswahl
      && (auswahl.ost - auswahl.west < 1e-6 || auswahl.nord - auswahl.sued < 1e-6)) {
    meldung.textContent = 'Das Rechteck war zu klein – bitte größer aufziehen.';
  }
  zieht = null;
  karte.classList.remove('greift');
  zeichne();
}
karte.addEventListener('pointerup', losgelassen);
karte.addEventListener('pointercancel', losgelassen);

function zoomen(stufe, e) {
  const neu = klemme(stufe, 2, MAXZOOM);
  if (neu === zoom) return;
  let ziel = mitte;
  if (e) {                       /* zum Mauszeiger hin zoomen */
    const vor = orten(e);
    zoom = neu;
    const u = ursprung(), nach = {lon: xZuLon(u.x + e.clientX - u.b.left, zoom),
                                  lat: yZuLat(u.y + e.clientY - u.b.top, zoom)};
    ziel = {lon: mitte.lon + vor.lon - nach.lon, lat: mitte.lat + vor.lat - nach.lat};
  } else { zoom = neu; }
  mitte = {lon: ziel.lon, lat: klemme(ziel.lat, -85, 85)};
  zeichne();
}
karte.addEventListener('wheel', e => {
  e.preventDefault();
  zoomen(zoom + (e.deltaY < 0 ? 1 : -1), e);
}, {passive: false});
document.getElementById('plus').onclick = () => zoomen(zoom + 1);
document.getElementById('minus').onclick = () => zoomen(zoom - 1);
document.getElementById('zeigen').onclick = () => zeigeAuswahl();

function zeigeAuswahl() {
  if (!auswahl) return;
  mitte = {lat: (auswahl.sued + auswahl.nord) / 2, lon: (auswahl.west + auswahl.ost) / 2};
  const b = karte.getBoundingClientRect();
  for (let z = MAXZOOM; z >= 2; z--) {
    const w = lonZuX(auswahl.ost, z) - lonZuX(auswahl.west, z);
    const h = latZuY(auswahl.sued, z) - latZuY(auswahl.nord, z);
    if (w <= b.width * 0.85 && h <= b.height * 0.85) { zoom = z; break; }
  }
  zeichne();
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && zeichenModus) knopf.click();
});

/* ---------------------------------------------------- Übergabe an Blender */
document.getElementById('uebernehmen').onclick = async () => {
  if (!werte.value) { meldung.textContent = 'Bitte zuerst ein Rechteck aufziehen.'; return; }
  if (!ZURUECK) {
    meldung.textContent = 'Kein Rückkanal – bitte „Koordinaten kopieren“ und in Blender '
      + '„Aus Zwischenablage“ verwenden.';
    return;
  }
  meldung.textContent = 'Wird übergeben …';
  try {
    const antwort = await fetch('/set?bbox=' + encodeURIComponent(werte.value));
    if (!antwort.ok) throw new Error('HTTP ' + antwort.status);
    meldung.textContent = 'Übernommen – das Gebiet steht jetzt in Blender. '
      + 'Dieses Fenster kann geschlossen werden.';
    document.getElementById('uebernehmen').textContent = '✓ Übernommen';
  } catch (err) {
    meldung.textContent = 'Blender antwortet nicht (' + err.message
      + '). Bitte „Koordinaten kopieren“ und dort „Aus Zwischenablage“ wählen.';
  }
};

document.getElementById('kopieren').onclick = async () => {
  if (!werte.value) { meldung.textContent = 'Bitte zuerst ein Rechteck aufziehen.'; return; }
  werte.select();
  try {
    await navigator.clipboard.writeText('bbox=' + werte.value);
    meldung.textContent = 'Kopiert – in Blender auf „Aus Zwischenablage“ klicken.';
  } catch (err) {
    document.execCommand('copy');
    meldung.textContent = 'Kopiert (Textfeld markiert) – in Blender „Aus Zwischenablage“.';
  }
};

/* ---------------------------------------------------- Suche */
const trefferBox = document.getElementById('treffer');
async function suchen() {
  const text = document.getElementById('suche').value.trim();
  if (!text) return;
  meldung.textContent = 'Suche läuft …';
  trefferBox.style.display = 'none';
  try {
    const url = SUCHE + (SUCHE.indexOf('?') < 0 ? '?' : '&')
              + 'q=' + encodeURIComponent(text) + '&format=json&limit=8';
    const antwort = await fetch(url, {headers: {'Accept': 'application/json'}});
    const treffer = await antwort.json();
    if (!treffer.length) { meldung.textContent = 'Kein Treffer.'; return; }
    meldung.textContent = treffer.length + ' Treffer – bitte auswählen.';
    trefferBox.innerHTML = '';
    treffer.forEach(t => {
      const zeile = document.createElement('div');
      zeile.textContent = t.display_name;
      zeile.onclick = () => {
        if (t.boundingbox) {
          const [s1, n1, w1, o1] = t.boundingbox.map(parseFloat);
          auswahl = {sued: s1, nord: n1, west: w1, ost: o1};
          zeigeAuswahl();
        } else {
          mitte = {lat: parseFloat(t.lat), lon: parseFloat(t.lon)};
          zoom = 14;
          zeichne();
        }
        trefferBox.style.display = 'none';
        meldung.textContent = 'Ausschnitt gesetzt – bei Bedarf an den Ecken anpassen.';
      };
      trefferBox.appendChild(zeile);
    });
    trefferBox.style.display = 'block';
  } catch (err) {
    meldung.textContent = 'Suchdienst nicht erreichbar (' + err.message
      + '). Im Addon lässt sich eine eigene Nominatim-Adresse eintragen.';
  }
}
document.getElementById('suchen').onclick = suchen;
document.getElementById('suche').addEventListener('keydown', e => {
  if (e.key === 'Enter') suchen();
});

window.addEventListener('resize', zeichne);
zeichne();
</script>
</body>
</html>
"""


def _auto_zoom(min_lat, min_lon, max_lat, max_lon):
    span = max(abs(max_lat - min_lat), abs(max_lon - min_lon) * 0.6) or 0.01
    zoom = 14
    while zoom > 3 and span > 360.0 / (2 ** zoom) * 1.5:
        zoom -= 1
    return zoom


def build_html(min_lat, min_lon, max_lat, max_lon, tile_url="", zoom=None, search_url="",
               attribution="", max_zoom=19, with_server=True):
    """Auswahlseite als HTML-Text."""
    tiles = tile_url.strip() or DEFAULT_TILES
    if zoom is None:
        zoom = _auto_zoom(min_lat, min_lon, max_lat, max_lon)

    auswahl = ("{west: %.6f, sued: %.6f, ost: %.6f, nord: %.6f}"
               % (min_lon, min_lat, max_lon, max_lat))
    starttext = ("Rechteck anpassen oder neu aufziehen, dann „Nach Blender übernehmen“."
                 if with_server else
                 "Rechteck aufziehen, „Koordinaten kopieren“ und in Blender "
                 "„Aus Zwischenablage“ wählen.")
    page = _TEMPLATE
    for schluessel, wert in (
        ("@@TILES@@", json.dumps(tiles)),
        ("@@SUCHE@@", json.dumps(search_url.strip() or DEFAULT_SEARCH)),
        ("@@MAXZOOM@@", str(int(max_zoom))),
        ("@@ZURUECK@@", "true" if with_server else "false"),
        ("@@ZOOM@@", str(int(zoom))),
        ("@@LAT@@", "%.6f" % ((min_lat + max_lat) / 2.0)),
        ("@@LON@@", "%.6f" % ((min_lon + max_lon) / 2.0)),
        ("@@AUSWAHL@@", auswahl),
        ("@@QUELLE@@", html.escape(attribution or "Kartendaten des eingestellten Dienstes")),
        ("@@STARTTEXT@@", html.escape(starttext)),
    ):
        page = page.replace(schluessel, wert)
    return page


def build_page(min_lat, min_lon, max_lat, max_lon, **kwargs):
    """Schreibt die Auswahlseite als Datei und gibt ihren Pfad zurück.

    Wird nur noch als Rückfallweg gebraucht, wenn sich der lokale Server nicht
    starten lässt - dann läuft die Übergabe über die Zwischenablage.
    """
    kwargs.setdefault("with_server", False)
    page = build_html(min_lat, min_lon, max_lat, max_lon, **kwargs)
    folder = os.path.join(tempfile.gettempdir(), "blender_railway_chainage")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "gebiet_auswaehlen.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)
    return path


# ------------------------------------------------------------------ Server

class _Handler(http.server.BaseHTTPRequestHandler):
    """Liefert die Seite und nimmt das gewählte Gebiet entgegen."""

    protocol_version = "HTTP/1.1"
    server_version = "RailwayChainageSelector/1.0"

    def log_message(self, *args):
        pass                                    # keine Ausgabe in der Konsole

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):                           # noqa: N802 (Vorgabe der Basisklasse)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.server.page.encode("utf-8"))
            return
        if parsed.path == "/set":
            werte = urllib.parse.parse_qs(parsed.query).get("bbox", [""])[0]
            try:
                west, sued, ost, nord = [float(v) for v in werte.split(",")]
            except (ValueError, TypeError):
                self._send(400, "text/plain; charset=utf-8", b"ungueltige Koordinaten")
                return
            self.server.bbox = (min(sued, nord), min(west, ost),
                                max(sued, nord), max(west, ost))
            self._send(200, "text/plain; charset=utf-8", b"ok")
            return
        self._send(404, "text/plain; charset=utf-8", b"nicht gefunden")


class SelectorServer:
    """Kurzlebiger Server auf 127.0.0.1 für die Dauer der Gebietsauswahl.

    Er ist ausschließlich lokal erreichbar und wird beendet, sobald das Gebiet
    übernommen oder die Auswahl abgebrochen wurde.
    """

    def __init__(self, page):
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.page = page
        self._server.bbox = None
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.2}, daemon=True)

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def url(self):
        return "http://127.0.0.1:%d/" % self.port

    @property
    def bbox(self):
        """(min_lat, min_lon, max_lat, max_lon) oder None, solange nichts kam."""
        return self._server.bbox

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:                                         # noqa: BLE001
            pass


def blosm_url(blender_version=(5, 1), addon_version=(2, 7, 2)):
    """Adresse der BLOSM-Auswahlkarte (prochitecture.com)."""
    return ("%s?blender_version=%s.%s&addon=blosm&addon_version=%s.%s.%s"
            % (BLOSM_URL, blender_version[0], blender_version[1],
               addon_version[0], addon_version[1], addon_version[2]))
