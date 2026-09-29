"""Eigenschaften (UI-Zustand) des Kilometrierungs-Addons."""

import math

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy.types import PropertyGroup


TRACK_TYPE_ITEMS = (
    ('rail', "Eisenbahn", "railway=rail - Voll- und Regelspurbahnen"),
    ('light_rail', "Stadtbahn", "railway=light_rail"),
    ('narrow_gauge', "Schmalspur", "railway=narrow_gauge"),
    ('subway', "U-Bahn", "railway=subway"),
    ('tram', "Straßenbahn", "railway=tram"),
)


class RC_RouteItem(PropertyGroup):
    """Eine im Gebiet gefundene Strecke."""

    # 'name' wird von der UIList als Anzeigetext verwendet
    key: StringProperty(name="Schlüssel")
    ref: StringProperty(name="Streckennummer")
    route_name: StringProperty(name="Streckenname")
    length_km: FloatProperty(name="Länge", unit='NONE')
    way_count: IntProperty(name="Segmente")
    milestone_count: IntProperty(name="Kilometrierungstafeln")
    usage: StringProperty(name="Streckenart")
    is_station: BoolProperty(name="Betriebsstelle", default=False)
    track_count: IntProperty(name="Gleise", default=1)
    selected: BoolProperty(name="Auswählen", default=False)


class RC_Props(PropertyGroup):

    # ------------------------------------------------------------- Gebiet
    area_mode: EnumProperty(
        name="Gebiet",
        items=(
            ('BBOX', "Bounding Box", "Gebiet über vier Eckkoordinaten festlegen"),
            ('CENTER', "Mittelpunkt + Radius", "Gebiet über einen Mittelpunkt und einen Radius festlegen"),
        ),
        default='BBOX',
    )
    min_lat: FloatProperty(name="min. Breite", default=48.01083, min=-90.0, max=90.0, precision=6)
    max_lat: FloatProperty(name="max. Breite", default=48.19058, min=-90.0, max=90.0, precision=6)
    min_lon: FloatProperty(name="min. Länge", default=9.73835, min=-180.0, max=180.0, precision=6)
    max_lon: FloatProperty(name="max. Länge", default=9.84306, min=-180.0, max=180.0, precision=6)
    center_lat: FloatProperty(name="Breite", default=48.10071, min=-90.0, max=90.0, precision=6)
    center_lon: FloatProperty(name="Länge", default=9.79071, min=-180.0, max=180.0, precision=6)
    radius: FloatProperty(name="Radius", default=3000.0, min=100.0, max=50000.0,
                          subtype='DISTANCE', description="Halbe Kantenlänge des quadratischen Suchgebiets")

    use_scene_origin: BoolProperty(
        name="Nullpunkt der Szene verwenden",
        default=True,
        description="Vorhandenen Projektionsmittelpunkt der Szene (scene['lat'] / scene['lon'], "
                    "wird auch von BLOSM gesetzt) verwenden, damit die Kilometrierung "
                    "deckungsgleich über einem bestehenden Import liegt",
    )

    # -------------------------------------------------------------- Daten
    track_types: EnumProperty(
        name="Gleisarten",
        items=TRACK_TYPE_ITEMS,
        default={'rail'},
        options={'ENUM_FLAG'},
    )
    include_station_tracks: BoolProperty(
        name="Bahnhofsgleise aufnehmen", default=True,
        description="Zusätzlich die Gleise im Bereich von Bahnhöfen und Haltepunkten "
                    "auflisten - je Betriebsstelle eine eigene Gruppe. Sie sind nicht "
                    "kilometriert und lassen sich nur als Gleisachsen erzeugen")
    station_radius: FloatProperty(
        name="Umkreis Betriebsstelle", default=600.0, min=50.0, max=5000.0, subtype='DISTANCE',
        description="Bis zu diesem Abstand wird ein Gleis einer Betriebsstelle zugerechnet")
    skip_service_tracks: BoolProperty(
        name="Rangier- und Nebengleise ignorieren",
        default=True,
        description="Gleise mit service=yard/siding/spur/crossover überspringen - sie sind nicht kilometriert",
    )
    overpass_mode: EnumProperty(
        name="OSM-Server",
        items=(
            ('PUBLIC', "Öffentlich", "Die frei zugänglichen Overpass-Server verwenden"),
            ('CUSTOM', "Eigener Server", "Eine eigene Overpass-Instanz verwenden"),
        ),
        default='PUBLIC',
    )
    overpass_url: StringProperty(
        name="Adresse", default="http://2.28.27.1/api/interpreter",
        description="Vollständige Adresse des Overpass-Endpunkts, z. B. "
                    "http://server/api/interpreter")

    map_view_url: StringProperty(
        name="Kartenansicht", default="http://2.28.27.1:8080/",
        description="Adresse der eigenen Kartenanwendung. Enthält sie {lat}, {lon} oder {zoom}, "
                    "werden diese durch das eingestellte Gebiet ersetzt; sonst wird für "
                    "Overpass-Turbo-Adressen automatisch der Parameter C=lat;lon;zoom angehängt")

    tile_url: StringProperty(
        name="Kachel-Adresse", default="",
        description="Eigener Kachelserver als Vorlage mit {z}/{x}/{y}, z. B. "
                    "http://server:8080/tile/{z}/{x}/{y}.png. Leer = die gewählte "
                    "öffentliche Karte verwenden")

    use_cache: BoolProperty(
        name="Antworten zwischenspeichern",
        default=True,
        description="Heruntergeladene Daten für einen Tag lokal speichern (spart Anfragen an den Server)",
    )
    request_timeout: IntProperty(name="Zeitlimit", default=180, min=30, max=900,
                                 description="Zeitlimit der Serverabfrage in Sekunden")

    busy: BoolProperty(default=False)
    progress: FloatProperty(name="Fortschritt", default=0.0, min=0.0, max=100.0,
                            subtype='PERCENTAGE')

    routes: CollectionProperty(type=RC_RouteItem)
    routes_index: IntProperty(default=0)
    scan_info: StringProperty(default="")

    # ----------------------------------------------------- Kilometrierung
    chainage_source: EnumProperty(
        name="Quelle",
        items=(
            ('MILESTONES', "Kilometrierungstafeln (OSM)",
             "Kilometrierung aus railway:position der Tafeln übernehmen - die Daten, die auch "
             "die OpenRailwayMap anzeigt"),
            ('MILESTONES_PW', "Tafeln, stückweise",
             "Interpolation zwischen OSM-Tafeln; ungleiche Abstände. "
             "Kilometrierungssprünge werden ohne explizite Sprungpunkte nicht korrekt abgebildet"),
            ('MANUAL', "Manuell",
             "Kilometrierung am Anfang der Linie mit einem festen Wert beginnen"),
        ),
        default='MILESTONES',
    )
    start_km: FloatProperty(name="Start-km", default=0.0, precision=3,
                            description="Kilometerwert am Anfang der Linie")
    reverse: BoolProperty(name="Richtung umkehren", default=False,
                          description="Kilometrierung entgegen der Linienrichtung zählen")
    milestone_distance: FloatProperty(
        name="Suchradius Tafeln", default=30.0, min=1.0, max=200.0, subtype='DISTANCE',
        description="Maximaler Abstand einer Kilometrierungstafel zur Gleisachse")
    milestone_tolerance: FloatProperty(
        name="Toleranz", default=30.0, min=1.0, max=200.0, subtype='DISTANCE',
        description="Zulässige Abweichung einer Tafel von der ausgeglichenen Kilometrierung; "
                    "größere Abweichungen gelten als Ausreißer")
    fallback_manual: BoolProperty(
        name="Ersatzweise manuell", default=True,
        description="Wenn keine Kilometrierungstafeln gefunden werden, mit dem Start-km beginnen")

    # ------------------------------- Lage der Achse (Ril 883.0010, Abschnitt 3)
    axis_mode: EnumProperty(
        name="Lage der Linie",
        items=(
            ('CENTER', "Streckenachse (Ril 883)",
             "Bei mehrgleisigen Strecken mittig zwischen den Gleisen, bei eingleisigen auf der "
             "Gleisachse - die Regelfestlegung nach Ril 883.0010, Abschnitt 3 (1)"),
            ('TRACK', "Gleisachse",
             "Unmittelbar auf der Achse des längsten durchgehenden Gleises"),
            ('PARALLEL', "Parallel zur Gleisachse",
             "Im festen seitlichen Abstand zur Gleisachse - ebenfalls nach "
             "Ril 883.0010, Abschnitt 3 (1) zulässig"),
        ),
        default='CENTER',
    )
    track_spacing: FloatProperty(
        name="Gleisabstand max.", default=15.0, min=1.0, max=100.0, subtype='DISTANCE',
        description="Bis zu diesem seitlichen Abstand zählt ein Nachbargleis zur Streckenachse. "
                    "Weiter entfernte oder abzweigende Gleise bleiben unberücksichtigt")
    axis_transition: FloatProperty(
        name="Übergangslänge", default=150.0, min=0.0, max=2000.0, subtype='DISTANCE',
        description="Länge, über die die Linie beim Wechsel zwischen ein- und zweigleisigen "
                    "Abschnitten einschwenkt. Ril 883.0010, Abschnitt 3 (2) verbietet seitliche "
                    "Sprünge, Abschnitt 3 (3) verlangt einen mittig einlaufenden Übergang")
    parallel_offset: FloatProperty(
        name="Seitlicher Abstand", default=2.25, min=-50.0, max=50.0, subtype='DISTANCE',
        description="Abstand der Kilometrierungslinie zur Gleisachse (2,25 m = halber "
                    "Regelgleisabstand)")
    parallel_side: EnumProperty(
        name="Seite", items=(('LEFT', "Links", ""), ('RIGHT', "Rechts", "")), default='LEFT')
    check_crossings: BoolProperty(
        name="Überschneidungen prüfen", default=True,
        description="Meldet, wenn eine Gleisachse die Kilometrierungslinie schneidet - nach "
                    "Ril 883.0010, Abschnitt 3 (1) bei zweigleisigen Strecken unzulässig")

    # ----------------------------------------------------------- Geometrie
    clip_to_area: BoolProperty(
        name="Auf Gebiet zuschneiden", default=True,
        description="Die Kilometrierung endet exakt an der Grenze des eingestellten Gebiets. "
                    "Ohne diese Option reichen die Linien so weit, wie die vom Server "
                    "gelieferten Gleisabschnitte reichen - meist einige Kilometer darüber hinaus")
    only_longest_chain: BoolProperty(
        name="Nur längste Linie je Strecke", default=True,
        description="Bei mehrgleisigen Strecken nur eine Kilometrierungslinie erzeugen")
    min_chain_length: FloatProperty(name="Mindestlänge", default=300.0, min=0.0, max=100000.0,
                                    subtype='DISTANCE')
    max_angle: FloatProperty(name="max. Knickwinkel", default=75.0, min=5.0, max=170.0,
                             description="Grad - an Weichen wird der geradlinigste Anschluss gewählt; "
                                         "größere Richtungsänderungen beenden die Linie")
    line_resample: FloatProperty(
        name="Stützpunktabstand", default=10.0, min=0.0, max=500.0, subtype='DISTANCE',
        description="Die Kilometrierungslinie wird mit diesem Punktabstand neu gestützt. "
                    "Die Rohdaten enthalten oft nur alle 50 bis 100 m einen Punkt - zu wenig, "
                    "damit die Linie dem Gelände folgen kann. 0 = Punkte unverändert lassen")
    interval_mode: EnumProperty(
        name="Markenabstand",
        items=(
            ('RIL883', "Nach Ril 883",
             "Hauptstrecken alle 100 m (gerade Hektometer), Nebenstrecken alle 500 m - "
             "Ril 883.0010, Abschnitt 6 (1)"),
            ('FIXED', "Fester Wert", "Eigener Abstand"),
        ),
        default='RIL883',
    )
    interval: FloatProperty(name="Intervall", default=100.0, min=1.0, max=10000.0, subtype='DISTANCE',
                            description="Abstand der Kilometrierungsmarken")
    z_offset: FloatProperty(name="Höhe", default=0.5, subtype='DISTANCE',
                            description="Höhe der Linie über der Nullebene")
    line_width: FloatProperty(name="Linienstärke", default=1.0, min=0.0, max=20.0, subtype='DISTANCE',
                              description="Durchmesser des Linienprofils. 0 = reine Kurve ohne "
                                          "Geometrie - dann ist die Linie nur als Drahtgitter und "
                                          "nicht im Rendering sichtbar")

    # ---------------------------------------------------------- Marken
    make_ticks: BoolProperty(name="Marken zeichnen", default=True,
                             description="Querstriche an jeder Kilometrierungsmarke")
    tick_length: FloatProperty(name="Markenlänge", default=6.0, min=0.0, max=200.0, subtype='DISTANCE')
    tick_length_km: FloatProperty(name="Marke ganzer km", default=15.0, min=0.0, max=200.0,
                                  subtype='DISTANCE')

    # -------------------------------------------------------- Beschriftung
    make_labels: BoolProperty(name="Beschriftung", default=True)
    label_mode: EnumProperty(
        name="Beschriften",
        items=(
            ('ALL', "Jede Marke", "Alle 100 m beschriften"),
            ('KM_ONLY', "Nur ganze Kilometer", "Nur an vollen Kilometern beschriften"),
        ),
        default='ALL',
    )
    label_format: EnumProperty(
        name="Format",
        items=(
            ('DB', "Ril 883 (13,4+23,05)",
             "Schreibweise nach Ril 883.0010, Abschnitt 3 (4): Kilometer mit Hektometer, "
             "bei Zwischenwerten ergänzt um Meter und Zentimeter"),
            ('DECIMAL', "Dezimal (13,4)", "Kilometerwert mit fester Zahl von Nachkommastellen"),
        ),
        default='DB',
    )
    decimals: IntProperty(name="Nachkommastellen", default=1, min=0, max=3)
    decimal_separator: EnumProperty(
        name="Dezimaltrenner",
        items=((',', "Komma (12,3)", ""), ('.', "Punkt (12.3)", "")),
        default=',',
    )
    label_prefix: StringProperty(name="Präfix", default="",
                                 description='z. B. "km " für "km 12,3"')
    text_size: FloatProperty(name="Schriftgröße", default=10.0, min=0.01, max=1000.0, subtype='DISTANCE')
    km_text_factor: FloatProperty(name="Faktor ganze km", default=1.6, min=1.0, max=10.0,
                                  description="Vergrößerung der Beschriftung an vollen Kilometern")
    label_offset: FloatProperty(name="Seitlicher Abstand", default=12.0, subtype='DISTANCE')
    label_side: EnumProperty(
        name="Seite",
        items=(('LEFT', "Links", ""), ('RIGHT', "Rechts", "")),
        default='LEFT',
    )
    label_orientation: EnumProperty(
        name="Ausrichtung",
        items=(
            ('ALONG', "Längs zur Strecke", ""),
            ('ACROSS', "Quer zur Strecke", ""),
            ('WORLD', "Achsparallel", "Immer parallel zur X-Achse"),
        ),
        default='ALONG',
    )
    # ------------------------------------------- Gelände und Korridor
    corridor_radius: FloatProperty(
        name="Korridorbreite", default=100.0, min=5.0, max=5000.0, subtype='DISTANCE',
        description="Abstand beiderseits der Kilometrierungslinie, der erhalten bzw. "
                    "mit Gelände versehen wird")
    terrain_source: EnumProperty(
        name="Geländeart",
        items=(
            ('DEM', "Höhenmodell",
             "Amtliche Höhendaten laden und das Gelände daraus aufbauen"),
            ('FLAT', "Eben (ohne Höhenmodell)",
             "Ebene Fläche ohne Höhendaten - lässt sich genauso mit der Karte belegen "
             "und eignet sich als schnelle Arbeitsgrundlage"),
        ),
        default='DEM',
    )
    flat_height: FloatProperty(
        name="Geländehöhe", default=0.0, subtype='DISTANCE',
        description="Höhe der ebenen Fläche über dem Szenennullpunkt")
    flat_spacing: FloatProperty(
        name="Rasterweite", default=25.0, min=1.0, max=1000.0, subtype='DISTANCE',
        description="Punktabstand der ebenen Fläche. Eine Ebene braucht kein feines Raster - "
                    "gröbere Werte halten die Szene klein")
    dem_service: EnumProperty(
        name="Höhendaten",
        items=(
            ('AUTO', "Automatisch nach Lage",
             "Den Dienst wählen, der das eingestellte Gebiet abdeckt"),
            ('BW_DGM1', "Baden-Württemberg DGM1 (LGL)",
             "Amtliches Geländemodell mit 1 m Raster, offene Daten (dl-de/by-2-0)"),
            ('NRW_DGM1', "Nordrhein-Westfalen DGM1 (Geobasis NRW)",
             "Amtliches Geländemodell mit 1 m Raster, offene Daten (dl-de/zero-2-0)"),
        ),
        default='AUTO',
    )
    dem_spacing: EnumProperty(
        name="Rasterweite",
        items=(('1', "1 m", "Feinstes Raster - sehr viele Punkte"),
               ('2', "2 m", ""),
               ('5', "5 m", "Guter Kompromiss für Streckenkorridore"),
               ('10', "10 m", ""),
               ('25', "25 m", "Grob, für lange Strecken")),
        default='5',
    )
    area_with_map: BoolProperty(
        name="Mit Kartentextur", default=True,
        description="Das Flächengelände gleich mit der Karte belegen")
    dem_fill_gaps: BoolProperty(
        name="Lücken füllen", default=True,
        description="Wo der Landesdienst keine Höhen liefert (etwa jenseits der Landesgrenze), "
                    "werden weltweite Höhenkacheln verwendet. Deren Raster ist mit rund 12 bis "
                    "30 m deutlich gröber als ein DGM1")
    dem_fallback_zoom: IntProperty(
        name="Raster der Ersatzdaten", default=13, min=10, max=15,
        description="Zoomstufe der weltweiten Höhenkacheln: 12 entspricht etwa 24 m, "
                    "13 etwa 12 m, 14 etwa 6 m Punktabstand (die Datenbasis bleibt rund 30 m)")
    dem_zero_level: BoolProperty(
        name="Auf Null beziehen", default=True,
        description="Niedrigsten Geländepunkt auf z = 0 legen (wie beim Geländeimport von "
                    "BLOSM). Andernfalls werden die Höhen über Normalnull verwendet")
    dem_use_blosm_offset: BoolProperty(
        name="Höhenbezug von BLOSM übernehmen", default=True,
        description="Vorhandenes BLOSM-Gelände als Höhenbezug verwenden, damit beide Modelle "
                    "übereinanderliegen")
    drape_mode: EnumProperty(
        name="Auf Gelände legen",
        items=(
            ('DIRECT', "Direkt (Punkte setzen)",
             "Höhen der Linie, Marken und Beschriftung fest auf die Geländehöhe setzen"),
            ('SHRINKWRAP', "Shrinkwrap-Modifier",
             "Linie und Marken über einen Shrinkwrap-Modifier auf dem Gelände halten - "
             "nicht zerstörend, folgt späteren Änderungen am Gelände"),
            ('NONE', "Nicht", "Linie auf der eingestellten Höhe belassen"),
        ),
        default='DIRECT',
    )

    # --------------------------------------------------------------- Gleise
    rails_object_mode: EnumProperty(
        name="Gleisobjekte",
        items=(('JOINED', "Ein gemeinsames Objekt", "Alle Abschnitte als Splines eines Objekts"),
               ('SEPARATE', "Einzelteile mit Gleisnummern", "Getrennte Abschnitte nach OSM-Ways und Weichen; Nummern aus railway:track_ref")),
        default='JOINED',
    )
    rails_separate_switches: BoolProperty(
        name="Weichen als einzelne Objekte", default=True,
        description="Einfache Weichen als V-Mesh mit drei Vertices und zwei Kanten. "
                    "Gleisenden schließen exakt an")
    rails_crossings: BoolProperty(
        name="Kreuzungen als zwei V-Weichen", default=True,
        description="Kreuzungen und Kreuzungsweichen (OSM-Knoten mit vier Nachbarn, je zwei zu "
                    "einer Seite) als zwei V-Meshes Spitze an Spitze anlegen. Ausgeschaltet enden "
                    "dort wie bisher vier Gleise im selben Punkt")
    rails_max_diverging: FloatProperty(
        name="Max. Abzweigwinkel", default=math.radians(10.0), min=0.0,
        max=math.radians(45.0), subtype='ANGLE',
        description="So weit dürfen die Schenkel einer Weiche höchstens auseinander- und vom "
                    "Stammgleis abstehen, damit die Fahrsimulation die Verbindung befahrbar "
                    "verknüpft (RailNetworkConstants.MaxDivergingAngle). Steilere Schenkel werden "
                    "gedreht. 0 = unverändert lassen")
    rails_transition: FloatProperty(
        name="Übergangslänge", default=20.0, min=1.0, max=200.0, subtype='DISTANCE',
        description="Auf dieser Länge läuft das Gleis hinter einem gedrehten Schenkel wieder in "
                    "die OSM-Kante ein - höchstens bis zum nächsten OSM-Knoten, der selbst nie "
                    "verschoben wird")
    rails_cap_crossings: BoolProperty(
        name="Einfache Kreuzungen begrenzen", default=True,
        description="Auch Kreuzungen ohne Weichenfunktion (railway=railway_crossing) auf den "
                    "Grenzwinkel bringen. Die Simulation lässt dort dann auch das Abbiegen zu. "
                    "Ausgeschaltet bleibt ihr Winkel erhalten: geradeaus befahrbar, abbiegen "
                    "nicht. Kreuzungen steiler als 45° bleiben in jedem Fall unverändert")
    rails_switch_length: FloatProperty(
        name="Max. Weichenschenkellänge", default=10.0, min=0.1, max=100.0,
        subtype='DISTANCE',
        description="Schenkel reichen maximal bis zum nächsten OSM-Knoten. "
                    "Schematische Darstellung, keine vermessene Weichenlänge")
    rails_spacing: FloatProperty(
        name="Stützpunktabstand", default=10.0, min=0.5, max=200.0, subtype='DISTANCE',
        description="Abstand der Stützpunkte der Gleisachsen. Feinere Stützung bildet Bögen "
                    "genauer ab und ist Voraussetzung dafür, dass die Achse dem Gelände folgt")
    rails_all_tracks: BoolProperty(
        name="Alle Gleise", default=True,
        description="Alle Gleise der Strecke anlegen, nicht nur das längste durchgehende")
    rails_width: FloatProperty(
        name="Linienstärke (Altbestand, unbenutzt)", default=0.0, min=0.0, max=20.0, subtype='DISTANCE',
        description="Durchmesser des Linienprofils. 0 = reine Kurve ohne Geometrie")
    rails_drape: EnumProperty(
        name="Auf Gelände legen",
        items=(
            ('SHRINKWRAP', "Shrinkwrap-Modifier",
             "Gleisachsen über einen Shrinkwrap-Modifier auf dem Gelände halten"),
            ('DIRECT', "Direkt (Punkte setzen)",
             "Höhen der Stützpunkte fest vom Gelände übernehmen"),
            ('NONE', "Nicht", "Achsen auf der eingestellten Höhe belassen"),
        ),
        default='SHRINKWRAP',
    )

    terrain_segment_length: FloatProperty(
        name="Abschnittslänge", default=5000.0, min=0.0, max=100000.0, subtype='DISTANCE',
        description="Das Gelände wird in Abschnitte dieser Länge unterteilt. Jeder Abschnitt "
                    "bekommt eine eigene Kartentextur und damit eine deutlich höhere "
                    "Auflösung. 0 = nicht unterteilen")

    map_selected_only: BoolProperty(
        name="Nur ausgewählte Terrainabschnitte", default=False,
        description="Nur ausgewählte Korridor-Geländeobjekte mit der gewählten Kartenqualität neu texturieren")
    map_source: EnumProperty(
        name="Karte",
        items=(
            ('BASEMAP_DE', "basemap.de (BKG)",
             "Amtliche Basiskarte für Deutschland, für die Nutzung in Anwendungen "
             "vorgesehen (dl-de/by-2-0)"),
            ('TOPPLUS', "TopPlusOpen (BKG)",
             "Amtliche Weltkarte des Bundesamtes für Kartographie und Geodäsie (dl-de/by-2-0)"),
            ('TOPPLUS_GRAU', "TopPlusOpen, grau",
             "Wie TopPlusOpen, zurückhaltend eingefärbt"),
            ('OSM', "OpenStreetMap",
             "Standardkarte von OpenStreetMap. Achtung: ehrenamtlich betriebene Server, "
             "nur für kleine Ausschnitte verwenden"),
            ('OSM_ORM', "OSM + OpenRailwayMap",
             "OpenStreetMap mit der Bahn-Infrastrukturkarte darüber. Ebenfalls "
             "ehrenamtliche Server"),
            ('TOPO', "OpenTopoMap",
             "Topografische Karte mit Höhenlinien. Ebenfalls ehrenamtliche Server"),
            ('BKG_ORM', "basemap.de + OpenRailwayMap",
             "BKG-Basiskarte mit Bahn-Infrastruktur; für kleine ausgewählte Abschnitte"),
        ),
        default='BASEMAP_DE',
    )
    map_quality: EnumProperty(
        name="Kartenqualität",
        items=(
            ('4', "Übersicht (4 m/Px)", "Grob, für lange Strecken"),
            ('2', "Mittel (2 m/Px)", "Für Übersichten über mehrere Kilometer"),
            ('1', "Standard (1 m/Px)", "Guter Kompromiss - Beschriftungen lesbar"),
            ('0.5', "Detail (0,5 m/Px)", "Fein, für kurze Abschnitte"),
            ('0.25', "Maximal (0,25 m/Px)", "Sehr fein; nur für kleine Gebiete und mit "
                                            "eigenem Kachelserver sinnvoll"),
            ('CUSTOM', "Eigener Wert", "Auflösung frei eingeben"),
        ),
        default='1',
    )
    map_resolution: FloatProperty(
        name="Auflösung", default=1.0, min=0.1, max=50.0, subtype='DISTANCE',
        description="Angestrebte Bodenauflösung der Textur in Metern je Pixel. Die tatsächliche "
                    "Auflösung ergibt sich zusätzlich aus der Streckenlänge und der "
                    "Bildbegrenzung")
    map_max_edge: IntProperty(
        name="max. Bildbreite", default=16384, min=1024, max=32768,
        description="Obergrenze für die Pixelbreite der Textur. Grafikkarten unterstützen "
                    "meist bis 16384 Pixel")
    map_max_tiles: IntProperty(
        name="max. Kacheln", default=500, min=10, max=5000,
        description="Sicherheitsgrenze für die Zahl der Kartenkacheln, die geladen werden. "
                    "Die Kartendienste werden ehrenamtlich betrieben - bitte sparsam abrufen")

    clip_crop_textures: BoolProperty(
        name="Texturen zuschneiden", default=True,
        description="Bildtexturen auf den Korridor zuschneiden statt das gesamte Areal in "
                    "voller Auflösung in der Datei zu behalten")
    clip_max_texture: IntProperty(
        name="max. Kantenlänge", default=4096, min=0, max=16384,
        description="Zugeschnittene Texturen zusätzlich auf diese Kantenlänge begrenzen "
                    "(0 = unbegrenzt)")

    # -------------------------------------------------------------- Gebäude
    buildings_default_height: FloatProperty(
        name="Vorgabehöhe", default=9.0, min=1.0, max=500.0, subtype='DISTANCE',
        description="Bauhöhe für Gebäude ohne Angabe in OpenStreetMap")
    buildings_level_height: FloatProperty(
        name="Geschosshöhe", default=3.0, min=1.0, max=10.0, subtype='DISTANCE',
        description="Höhe je Geschoss, wenn das Gebäude nur „building:levels“ trägt")
    buildings_min_area: FloatProperty(
        name="Mindestfläche", default=8.0, min=0.0, max=1000.0,
        description="Grundrisse unterhalb dieser Grundfläche in m² werden übergangen "
                    "(Carports, Gartenhäuser)")
    buildings_parts: BoolProperty(
        name="Gebäudeteile einbeziehen", default=False,
        description="Auch „building:part“ auswerten - genauer bei gegliederten Bauten, "
                    "erzeugt aber überlappende Körper")
    buildings_only_corridor: BoolProperty(
        name="Nur im Korridor", default=False,
        description="Nur Gebäude übernehmen, deren Schwerpunkt innerhalb der eingestellten "
                    "Korridorbreite um die Kilometrierungslinie liegt")
    buildings_drape: BoolProperty(
        name="Auf Gelände setzen", default=True,
        description="Die Gebäude auf die Höhe des vorhandenen Geländes stellen")
    buildings_skirt: FloatProperty(
        name="Einbindetiefe", default=1.0, min=0.0, max=20.0, subtype='DISTANCE',
        description="So weit reicht die Grundfläche unter den tiefsten Geländepunkt, damit "
                    "am Hang keine Lücke entsteht")
    buildings_max: IntProperty(
        name="Höchstzahl", default=20000, min=100, max=200000,
        description="Obergrenze, damit ein zu großes Gebiet Blender nicht überlastet")

    color: FloatVectorProperty(name="Farbe", subtype='COLOR', size=4, min=0.0, max=1.0,
                               default=(0.9, 0.15, 0.05, 1.0))
    create_material: BoolProperty(name="Material zuweisen", default=True)
    parent_objects: BoolProperty(
        name="An Linie anhängen", default=True,
        description="Marken und Beschriftung an die Linie parenten, damit sich alles gemeinsam "
                    "bewegen lässt. Die gestrichelten Beziehungslinien im Viewport lassen sich "
                    "über Overlays ▸ Beziehungslinien ausblenden")


classes = (RC_RouteItem, RC_Props)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.railway_chainage = bpy.props.PointerProperty(type=RC_Props)


def unregister():
    del bpy.types.Scene.railway_chainage
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
