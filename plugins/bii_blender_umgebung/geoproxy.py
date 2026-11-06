"""Lokaler Zugang zur Geo-Box für den Browser (Overpass Turbo).

Die Geo-Box verlangt für jede Seite einen Keycloak-Token. Ein Browser hat ihn
nicht - Overpass Turbo unter ``https://geo.simbox.site/turbo/`` antwortet daher
mit „Unauthorized“. Dieser kleine Server läuft für die Dauer der Blender-Sitzung
auf ``127.0.0.1`` und reicht Turbo, die Overpass-Abfragen und die Kartenkacheln
mit dem Service-Konto aus dem Panel „Datenquelle“ an die Geo-Box weiter.

Er ist nur lokal erreichbar. Anfragen anderer Webseiten (fremder ``Origin`` oder
``Sec-Fetch-Site: cross-site``) und fremde ``Host``-Köpfe werden abgewiesen, damit
keine Webseite den Zugang über den Browser mitbenutzen kann.

Das Modul kommt ohne bpy aus.
"""

import http.server
import threading
import urllib.error
import urllib.request

# Feste Adresse, damit Turbo seine gespeicherten Abfragen (localStorage je
# Adresse) über Blender-Neustarts hinweg behält. Ist sie belegt, wird eine freie
# Adresse gewählt.
PREFERRED_PORT = 8771

# Pfade, die an die Geo-Box gehen - dieselben, die Turbo dort selbst benutzt
PREFIXES = ("/turbo/", "/overpass/api/", "/tiles/", "/nominatim/")

# Kopfzeilen der Geo-Box, die an den Browser weitergegeben werden
PASS_HEADERS = ("Content-Type", "Content-Encoding", "Cache-Control", "Last-Modified", "ETag",
                "Retry-After", "X-Imagery-Sources")

MAX_BODY = 8 * 1024 * 1024

_lock = threading.Lock()
_server = {"instance": None, "thread": None}


def local_request_allowed(handler):
    """Nur Anfragen der lokalen Seite dieses Servers selbst - keine fremden Webseiten.

    Prüft ``Host`` (gegen DNS-Rebinding), ``Origin`` und ``Sec-Fetch-Site``. Gilt für
    jeden lokalen Server, der Anfragen mit dem Token der Geo-Box weiterreicht.
    """
    port = handler.server.server_address[1]
    own_hosts = ("127.0.0.1:%d" % port, "localhost:%d" % port)
    if handler.headers.get("Host", "") not in own_hosts:
        return False
    origin = handler.headers.get("Origin")
    if origin and origin not in ("http://" + h for h in own_hosts):
        return False
    return handler.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "UmgebungGeoProxy/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code, content_type, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _text(self, code, text):
        self._send(code, "text/plain; charset=utf-8", text.encode("utf-8"))

    def _forward(self, method):
        from . import simbox
        if not local_request_allowed(self):
            self._text(403, "Nur für die lokale Turbo-Seite")
            return
        path = self.path
        if path in ("/", "/turbo"):
            self.send_response(302)
            self.send_header("Location", "/turbo/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not path.startswith(PREFIXES):
            self._text(404, "nicht gefunden")
            return
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._text(413, "Anfrage zu groß")
                return
            body = self.rfile.read(length)
        url = simbox.base_url() + path
        for attempt in range(2):
            try:
                token = simbox.token(force=attempt > 0)
            except simbox.SimboxError as exc:
                self._text(502, str(exc))
                return
            headers = {"User-Agent": simbox.USER_AGENT + " Turbo", "Authorization": "Bearer " + token,
                       "Accept": self.headers.get("Accept", "*/*")}
            if body is not None:
                headers["Content-Type"] = self.headers.get(
                    "Content-Type", "application/x-www-form-urlencoded")
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=330,
                                            context=simbox._tls_context()) as response:
                    self._reply(response.status, response.headers, response.read())
                return
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    continue
                # Fehlertext der Geo-Box (z. B. Overpass-Syntaxfehler) unverändert zeigen
                self._reply(exc.code, exc.headers, exc.read() or b"")
                return
            except (urllib.error.URLError, OSError) as exc:
                self._text(502, "Geo-Box nicht erreichbar: %s" % exc)
                return

    def _reply(self, status, headers, data):
        # Kachel- und Stiladressen der Geo-Box in den Antworten setzt Turbo selbst auf
        # die eigene Adresse um (transformRequest) - hier bleibt alles unverändert.
        # Content-Encoding muss mit: Vektorkacheln kommen gzip-komprimiert.
        art = headers.get("Content-Type") or "application/octet-stream"
        weiter = {k: headers[k] for k in PASS_HEADERS if k != "Content-Type" and headers.get(k)}
        self._send(status, art, data, weiter)

    def do_GET(self):                           # noqa: N802
        self._forward("GET")

    def do_POST(self):                          # noqa: N802
        self._forward("POST")


def start():
    """Startet den Server (falls nötig) und gibt seine Adresse zurück."""
    with _lock:
        if _server["instance"] is not None:
            return url()
        try:
            instance = http.server.ThreadingHTTPServer(("127.0.0.1", PREFERRED_PORT), _Handler)
        except OSError:
            instance = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        instance.daemon_threads = True
        thread = threading.Thread(target=instance.serve_forever,
                                  kwargs={"poll_interval": 0.5}, daemon=True)
        thread.start()
        _server.update({"instance": instance, "thread": thread})
        return url()


def url():
    instance = _server["instance"]
    return None if instance is None else "http://127.0.0.1:%d/" % instance.server_address[1]


def stop():
    with _lock:
        instance = _server["instance"]
        _server.update({"instance": None, "thread": None})
    if instance is not None:
        try:
            instance.shutdown()
            instance.server_close()
        except Exception:                                         # noqa: BLE001
            pass
