"""Projektion geografischer Koordinaten in lokale Blender-Meter-Koordinaten.

Die Transverse-Mercator-Formeln sind bewusst identisch zu denen des
BLOSM-Addons (util/transverse_mercator.py). Dadurch liegen erzeugte
Kilometrierungslinien deckungsgleich ueber BLOSM-Importen, sofern der
gleiche Projektionsmittelpunkt (scene["lat"] / scene["lon"]) benutzt wird.
"""

import math


class TransverseMercator:

    radius = 6378137.0

    def __init__(self, lat=0.0, lon=0.0, k=1.0):
        self.lat = lat
        self.lon = lon
        self.k = k
        self.lat_rad = math.radians(lat)

    def from_geographic(self, lat, lon):
        """(lat, lon) in Grad -> (x, y) in Metern relativ zum Mittelpunkt."""
        lat = math.radians(lat)
        lon = math.radians(lon - self.lon)
        b = math.sin(lon) * math.cos(lat)
        # Schutz gegen numerische Ausreisser weit ausserhalb des Mittelpunkts
        b = max(-0.999999999, min(0.999999999, b))
        x = 0.5 * self.k * self.radius * math.log((1.0 + b) / (1.0 - b))
        y = self.k * self.radius * (math.atan(math.tan(lat) / math.cos(lon)) - self.lat_rad)
        return x, y

    def to_geographic(self, x, y):
        """(x, y) in Metern -> (lat, lon) in Grad."""
        x = x / (self.k * self.radius)
        y = y / (self.k * self.radius)
        d = y + self.lat_rad
        lon = math.atan(math.sinh(x) / math.cos(d))
        lat = math.asin(math.sin(d) / math.cosh(x))
        return math.degrees(lat), self.lon + math.degrees(lon)

    def to_geographic_array(self, x, y):
        """Wie :meth:`to_geographic`, aber für numpy-Arrays."""
        import numpy as np
        x = np.asarray(x, dtype=np.float64) / (self.k * self.radius)
        y = np.asarray(y, dtype=np.float64) / (self.k * self.radius)
        d = y + self.lat_rad
        lon = np.arctan(np.sinh(x) / np.cos(d))
        lat = np.arcsin(np.clip(np.sin(d) / np.cosh(x), -1.0, 1.0))
        return np.degrees(lat), self.lon + np.degrees(lon)

    def from_geographic_array(self, lat, lon):
        """Wie :meth:`from_geographic`, aber für numpy-Arrays."""
        import numpy as np
        lat = np.radians(np.asarray(lat, dtype=np.float64))
        lon = np.radians(np.asarray(lon, dtype=np.float64) - self.lon)
        b = np.clip(np.sin(lon) * np.cos(lat), -0.999999999, 0.999999999)
        x = 0.5 * self.k * self.radius * np.log((1.0 + b) / (1.0 - b))
        y = self.k * self.radius * (np.arctan(np.tan(lat) / np.cos(lon)) - self.lat_rad)
        return x, y
