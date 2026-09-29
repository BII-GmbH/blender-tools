"""Topologie der Gleisachsen; keine bpy-Abhaengigkeit.

OSM-Knoten-IDs entscheiden ueber Verbindungen, niemals geometrische Naehe.
Ein V ersetzt die ersten beiden ausgehenden Kanten einer einfachen Weiche.
Seine Schenkel enden spaetestens am naechsten OSM-Knoten, ohne Kurven zu
begradigen oder benachbarte Weichen zu ueberlappen.

Kreuzungen (ein OSM-Knoten mit vier Nachbarn, je zwei zu einer Seite) werden
als zwei V-Weichen dargestellt, Spitze an Spitze. Die Fahrsimulation verbindet
Gleisenden nur bis zu einem Grenzwinkel (RailNetworkConstants.MaxDivergingAngle,
10 Grad); steilere Schenkel werden auf diesen Winkel gedreht. Das anschliessende
Gleisstueck bis zum naechsten OSM-Knoten wird dann sanft eingebogen, damit am
Schenkelende kein neuer Knick entsteht. OSM-Knoten selbst bewegen sich nie.
"""
import math
from collections import defaultdict

# Sicherheitsabstand zum Grenzwinkel, damit Rundungen ihn nicht ueberschreiten
ANGLE_MARGIN = 0.05
# Einfache Kreuzungen, die steiler sind, werden nicht auf den Grenzwinkel gebogen
CROSSING_MAX = 45.0
# Punktabstand in eingebogenen Gleisstuecken (Meter)
BEND_SPACING = 1.0
SLIP_TYPES = ('double_slip', 'single_slip')


class TrackPath(list):
    """Polyline with exact OSM way provenance, retained through clipping."""
    def __init__(self, points=(), way_ids=(), refs=()):
        super().__init__(points)
        self.way_ids = tuple(sorted(set(way_ids)))
        self.refs = tuple(sorted(set(refs)))


def copy_path(points, source):
    return TrackPath(points, getattr(source, 'way_ids', ()), getattr(source, 'refs', ()))


def edge_sources(rail, way_ids):
    sources = defaultdict(set)
    for wid in sorted(set(way_ids)):
        nodes = rail.ways[wid]['nodes']
        for a, b in zip(nodes, nodes[1:]):
            sources[frozenset((a, b))].add(wid)
    return sources


def path_metadata(rail, way_ids):
    # ref on a railway way is a ROUTE number, never a track number.
    refs = {str(rail.ways[w].get('tags', {}).get('railway:track_ref', '')).strip()
            for w in way_ids}
    return tuple(sorted(r for r in refs if r))


def numbered_chain(rail, way_ids, nodes):
    sources = edge_sources(rail, way_ids)
    paths = []
    for a, b in zip(nodes, nodes[1:]):
        if a not in rail.nodes or b not in rail.nodes:
            continue
        ids = tuple(sorted(sources.get(frozenset((a,b)), ())))
        refs = path_metadata(rail, ids)
        if paths and paths[-1].way_ids == ids and paths[-1][-1] == rail.nodes[a]:
            paths[-1].append(rail.nodes[b])
        else:
            paths.append(TrackPath([rail.nodes[a],rail.nodes[b]], ids, refs))
    return paths


def track_name(path):
    refs = getattr(path, 'refs', ())
    if len(refs) == 1:
        return 'Gleis ' + refs[0]
    if refs:
        return 'Gleis (OSM widerspruechlich: %s)' % ' / '.join(refs)
    ids = getattr(path, 'way_ids', ())
    return 'Gleis ohne Nummer' + (' [OSM %s]' % ids[0] if ids else '')


def densify(points, spacing):
    """Nur Punkte ergaenzen: alle Original- und Anschlussknoten bleiben exakt."""
    if not points:
        return []
    result = [points[0]]
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if length == 0:
            continue
        count = max(1, math.ceil(length / spacing)) if spacing > 0 else 1
        for i in range(1, count):
            result.append(tuple(x + (y - x) * i / count for x, y in zip(a, b)))
        result.append(b)
    return copy_path(result, points)


def _unit(dx, dy):
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > 1e-12 else None


def _signed(u, v):
    """Winkel von u nach v in Grad, gegen den Uhrzeigersinn positiv."""
    return math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]))


def _rotate(u, degrees):
    a = math.radians(degrees)
    return (u[0] * math.cos(a) - u[1] * math.sin(a), u[0] * math.sin(a) + u[1] * math.cos(a))


def divergence(u, v):
    """Richtungsaenderung zwischen zwei Gleisenden am selben Punkt (beide zeigen weg)."""
    dot = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return 180.0 - math.degrees(math.acos(dot))


def _limit_pair(sa, sb, cap):
    """Zwei Schenkelwinkel relativ zur Stammrichtung in die Grenzen bringen.

    Beide Schenkel duerfen hoechstens ``cap`` von der Stammrichtung abweichen und
    hoechstens ``cap`` auseinanderstehen. Bewegt wird so wenig wie moeglich: zuerst
    in den zulaessigen Bereich, dann der steilere Schenkel zum flacheren hin.
    """
    na, nb = max(-cap, min(cap, sa)), max(-cap, min(cap, sb))
    if abs(na - nb) > cap:
        if abs(na) >= abs(nb):
            na = nb + math.copysign(cap, na - nb)
        else:
            nb = na + math.copysign(cap, nb - na)
    return na, nb


def _crossing_sides(center, neighbours, coords):
    """Teilt die vier Nachbarn einer Kreuzung in zwei Seiten zu je zwei auf - oder None."""
    dirs = {n: _unit(coords[n][0] - center[0], coords[n][1] - center[1]) for n in neighbours}
    if any(d is None for d in dirs.values()):
        return None
    pairs = sorted((dirs[a][0] * dirs[b][0] + dirs[a][1] * dirs[b][1], a, b)
                   for i, a in enumerate(neighbours) for b in neighbours[i + 1:])
    dot, a, b = pairs[0]
    if dot >= 0.0:
        return None
    axis = _unit(dirs[a][0] - dirs[b][0], dirs[a][1] - dirs[b][1])
    side_a = [n for n in neighbours if dirs[n][0] * axis[0] + dirs[n][1] * axis[1] >= 0.0]
    side_b = [n for n in neighbours if n not in side_a]
    if len(side_a) != 2 or len(side_b) != 2:
        return None
    for x, y in (side_a, side_b):
        if dirs[x][0] * dirs[y][0] + dirs[x][1] * dirs[y][1] < math.cos(math.radians(75)):
            return None
    return tuple(side_a), tuple(side_b)


def _hermite(p0, t0, p1, t1, spacing=BEND_SPACING):
    """Punkte (ohne Endpunkte) einer kubischen Kurve von p0 nach p1 mit den Tangenten t0, t1."""
    length = math.dist(p0, p1)
    count = max(2, int(math.ceil(length / spacing)))
    result = []
    for k in range(1, count):
        t = k / count
        h00, h10 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t
        h01, h11 = -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
        result.append(tuple(h00 * p0[i] + h10 * length * t0[i] + h01 * p1[i] + h11 * length * t1[i]
                            for i in range(2)))
    return result


def _bend_points(p0, t0, p1, t1, line_origin, line_dir, transition):
    """Eingebogenes Gleisstueck von p0 nach p1.

    ``t0``/``t1`` sind die Richtungen an den Enden (None = unveraendert entlang der
    OSM-Kante). Die Biegung bleibt auf ``transition`` Meter hinter einem gedrehten
    Ende beschraenkt; dazwischen verlaeuft das Gleis wieder auf der OSM-Kante.
    """
    along = lambda p: (p[0] - line_origin[0]) * line_dir[0] + (p[1] - line_origin[1]) * line_dir[1]
    on_line = lambda s: (line_origin[0] + line_dir[0] * s, line_origin[1] + line_dir[1] * s)
    s0, s1 = along(p0), along(p1)
    start_tangent = t0 or line_dir
    end_tangent = t1 or line_dir
    needed = (transition if t0 else 0.0) + (transition if t1 else 0.0)
    if s1 - s0 <= needed + 1.0:
        return _hermite(p0, start_tangent, p1, end_tangent)
    points = []
    q0 = on_line(s0 + transition) if t0 else p0
    q1 = on_line(s1 - transition) if t1 else p1
    if t0:
        points += _hermite(p0, start_tangent, q0, line_dir) + [q0]
    if t1:
        points += [q1] + _hermite(q1, line_dir, p1, end_tangent)
    return points


def build(rail, way_ids, separate=True, arm_length=10.0, split_ways=False, crossings=True,
          max_angle=10.0, transition=20.0, cap_crossings=True):
    """Rueckgabe (Gleis-Polylinien, Weichen, Diagnose).

    Weichen: {node_id, points: [Spitze, Ende1, Ende2], kind, side}. Grad-3-Knoten
    mit zwei gleichgerichteten Aesten werden als einfache Weichen separiert. Mit
    ``crossings`` werden Grad-4-Knoten mit je zwei Aesten zu jeder Seite als zwei
    V-Weichen separiert (Kreuzung, Kreuzungsweiche). Unklare Knoten bleiben
    unveraendert.

    ``max_angle`` (Grad, 0 = aus) begrenzt, wie weit die Schenkel einer Weiche
    auseinander- und vom Stammgleis abstehen; ``cap_crossings`` wendet das auch auf
    einfache Kreuzungen (ohne Weichenfunktion) an. ``transition`` ist die Laenge,
    auf der ein gedrehter Schenkel wieder in das OSM-Gleis einlaeuft.
    """
    sources = edge_sources(rail, way_ids)
    coords = dict(rail.nodes)
    edges = set()
    adjacency = defaultdict(set)
    for wid in sorted(set(way_ids)):
        nodes = rail.ways[wid]['nodes']
        for a, b in zip(nodes, nodes[1:]):
            if a == b or a not in coords or b not in coords:
                continue
            if math.dist(coords[a], coords[b]) < 1e-8:
                continue
            edge = tuple(sorted((a, b)))
            edges.add(edge)
            adjacency[a].add(b)
            adjacency[b].add(a)

    plans = {}
    ambiguous = []
    for node in sorted(adjacency):
        neighbours = sorted(adjacency[node])
        if len(neighbours) != 3:
            continue
        center = coords[node]
        directions = []
        for other in neighbours:
            length = math.dist(center, coords[other])
            directions.append(tuple((v - u) / length for u, v in zip(center, coords[other])))
        pairs = [(sum(x*y for x,y in zip(directions[i], directions[j])), i, j)
                 for i in range(3) for j in range(i+1, 3)]
        dot, i, j = max(pairs)
        stem = 3 - i - j
        # V-Aeste < 75 Grad; der Stamm muss beiden gegenueberliegen.
        if (dot < math.cos(math.radians(75)) or
                any(sum(x*y for x,y in zip(directions[stem], directions[k])) >= 0
                    for k in (i,j))):
            ambiguous.append(node)
            continue
        plans[node] = (neighbours[i], neighbours[j])

    node_tags = getattr(rail, 'node_tags', {})
    crossing_plans = {}
    if crossings:
        for node in sorted(adjacency):
            neighbours = sorted(adjacency[node])
            if len(neighbours) != 4:
                continue
            sides = _crossing_sides(coords[node], neighbours, coords)
            if sides is None:
                ambiguous.append(node)
                continue
            crossing_plans[node] = sides

    def direction(node, other):
        return _unit(coords[other][0] - coords[node][0], coords[other][1] - coords[node][1])

    # ---- Grenzwinkel: Zielrichtungen der Schenkel, die zu steil stehen
    cap = max_angle - ANGLE_MARGIN if max_angle and max_angle > 0 else None
    targets = {}
    stats = {'Weiche': len(plans), 'Kreuzungsweiche': 0, 'Kreuzung': 0,
             'angepasst': 0, 'belassen': 0, 'max_drehung': 0.0}
    kinds = {}
    for node, outgoing in plans.items():
        kinds[node] = 'Weiche'
        if cap is None:
            continue
        stem = next(n for n in adjacency[node] if n not in outgoing)
        stem_dir = direction(node, stem)
        reference = (-stem_dir[0], -stem_dir[1])
        angles = [_signed(reference, direction(node, other)) for other in outgoing]
        limited = _limit_pair(angles[0], angles[1], cap)
        changed = False
        for other, before, after in zip(outgoing, angles, limited):
            if abs(after - before) > 1e-6:
                targets[(node, other)] = _rotate(reference, after)
                stats['max_drehung'] = max(stats['max_drehung'], abs(after - before))
                changed = True
        stats['angepasst'] += changed
    for node, (side_a, side_b) in crossing_plans.items():
        tags = node_tags.get(node, {})
        kind = 'Kreuzungsweiche' if tags.get('railway:switch') in SLIP_TYPES else 'Kreuzung'
        kinds[node] = kind
        stats[kind] += 1
        if cap is None:
            continue
        dirs = {n: direction(node, n) for n in side_a + side_b}
        worst = max(divergence(dirs[x], dirs[y]) for x in side_a for y in side_b)
        # auch die Oeffnung jedes einzelnen V zaehlt - es ist eine Weiche
        opening = max(180.0 - divergence(dirs[x], dirs[y]) for x, y in (side_a, side_b))
        if worst <= cap and opening <= cap:
            continue
        mean_a = _unit(dirs[side_a[0]][0] + dirs[side_a[1]][0], dirs[side_a[0]][1] + dirs[side_a[1]][1])
        mean_b = _unit(dirs[side_b[0]][0] + dirs[side_b[1]][0], dirs[side_b[0]][1] + dirs[side_b[1]][1])
        axis = _unit(mean_a[0] - mean_b[0], mean_a[1] - mean_b[1])
        sides = ((side_a, axis), (side_b, (-axis[0], -axis[1])))
        half = sum(abs(_signed(ax, dirs[side[0]]) - _signed(ax, dirs[side[1]]))
                   for side, ax in sides) / 4.0
        if kind == 'Kreuzung' and (not cap_crossings or 2.0 * half > CROSSING_MAX):
            stats['belassen'] += 1
            continue
        half = min(half, cap / 2.0)
        for side, ax in sides:
            ordered = sorted(side, key=lambda n: _signed(ax, dirs[n]))
            for other, offset in zip(ordered, (-half, half)):
                target = _rotate(ax, offset)
                turn = abs(_signed(dirs[other], target))
                if turn > 1e-6:
                    targets[(node, other)] = target
                    stats['max_drehung'] = max(stats['max_drehung'], turn)
        stats['angepasst'] += 1

    # V-Schenkel je Knoten: bei einer Kreuzung zwei V-Weichen, Spitze an Spitze
    v_groups = [(node, outgoing, 0) for node, outgoing in plans.items()]
    for node, (side_a, side_b) in crossing_plans.items():
        v_groups += [(node, side_a, 1), (node, side_b, 2)]
    v_edges = defaultdict(set)
    for node, outgoing, _side in v_groups:
        v_edges[node].update(outgoing)

    # Kanten mit zwei Weichen bekommen zwei getrennte Anschlussstellen.
    cuts = {}
    leg_dirs = {}
    switches = []
    if separate:
        for node, outgoing, side in v_groups:
            points = [coords[node]]
            for other in outgoing:
                a, b = coords[node], coords[other]
                length = math.dist(a, b)
                shared = node in v_edges.get(other, ())
                target = targets.get((node, other))
                # Ein gedrehter Schenkel belegt hoechstens die halbe Kante (bei zwei
                # Weichen auf einer Kante je 30 %): Der Rest wird gebraucht, um das
                # Gleis bis zum OSM-Nachbarknoten wieder einzubiegen. Den Knoten
                # selbst erreicht er nie, sonst muesste dieser verschoben werden.
                if shared:
                    share = 0.3 if (target is not None or (other, node) in targets) else 0.45
                else:
                    share = 0.5 if target is not None else 1.0
                distance = min(max(0.001, arm_length), length * share)
                if target is not None:
                    key = ('switch', node, other)
                    point = (a[0] + target[0] * distance, a[1] + target[1] * distance)
                    coords[key] = point
                    leg_dirs[(node, other)] = target
                elif distance >= length:
                    key, point = other, b
                else:
                    key = ('switch', node, other)
                    point = tuple(x + (y-x)*distance/length for x,y in zip(a,b))
                    coords[key] = point
                cuts[(node, other)] = key
                points.append(point)
            switches.append({'node_id': node, 'points': points,
                             'kind': kinds.get(node, 'Weiche'), 'side': side,
                             'tags': dict(node_tags.get(node, {})),
                             'arm_way_ids': [tuple(sorted(sources[frozenset((node,other))])) for other in outgoing]})

    # Restliche Gleise enthalten die V-Kanten nicht mehr.
    remaining = defaultdict(set)
    remaining_sources = {}
    bends = {}
    for a, b in sorted(edges):
        start, end = cuts.get((a,b), a), cuts.get((b,a), b)
        if start == end or math.dist(coords[start], coords[end]) < 1e-8:
            continue
        remaining_sources[frozenset((start,end))] = sources[frozenset((a,b))]
        remaining[start].add(end)
        remaining[end].add(start)
        # Anschluss an einen gedrehten Schenkel: das Stueck bis zum naechsten
        # OSM-Knoten beginnt in Schenkelrichtung und laeuft dann in die Kante ein
        t0 = leg_dirs.get((a, b))
        t1 = leg_dirs.get((b, a))
        if t0 or t1:
            line = direction(a, b)
            bends[(start, end)] = _bend_points(
                coords[start], t0, coords[end], (-t1[0], -t1[1]) if t1 else None,
                coords[a], line, transition)
    used = set()
    tracks = []
    def walk(start, nxt):
        chain = [coords[start]]
        ids = set()
        first_sources = remaining_sources[frozenset((start,nxt))]
        previous, current = start, nxt
        while True:
            edge = frozenset((previous, current))
            if edge in used or (split_ways and remaining_sources[edge] != first_sources):
                break
            ids.update(remaining_sources[edge])
            used.add(edge)
            if (previous, current) in bends:
                chain.extend(bends[(previous, current)])
            elif (current, previous) in bends:
                chain.extend(reversed(bends[(current, previous)]))
            chain.append(coords[current])
            if len(remaining[current]) != 2:
                break
            onward = next(n for n in remaining[current] if n != previous)
            previous, current = current, onward
        if len(chain) >= 2:
            tracks.append(TrackPath(chain, ids, path_metadata(rail, ids)))
    # Erst offene Strecken, dann geschlossene Ringe; stabile Sortierung.
    for node in sorted(remaining, key=repr):
        if len(remaining[node]) != 2:
            for other in sorted(remaining[node], key=repr):
                walk(node, other)
    for node in sorted(remaining, key=repr):
        for other in sorted(remaining[node], key=repr):
            walk(node, other)
    return tracks, switches, {'ambiguous': ambiguous, 'edges': len(edges), 'stats': stats}


def switch_name(node_id, tags, kind='Weiche', side=0):
    """OSM/ORM-Weichenreferenz; keine erfundenen Betriebsnummern.

    Eine Kreuzung besteht aus zwei V-Weichen; ``side`` (1/2) unterscheidet sie.
    """
    ref = str(tags.get('ref') or tags.get('railway:ref') or '').strip()
    name = str(tags.get('name') or '').strip()
    if ref and name and name != ref:
        text = '%s %s - %s' % (kind, ref, name)
    else:
        text = '%s %s' % (kind, ref or name or ('OSM ' + str(node_id)))
    return text + (' (Seite %d)' % side if side else '')


def include_crossovers(rail, way_ids):
    """Include crossover components connecting at least two selected track nodes.

    Crossovers commonly lack a route ref/name. Keep route grouping unchanged;
    only complete the topology for track-axis generation.
    """
    selected = set(way_ids)
    anchors = {n for wid in selected for n in rail.ways[wid]['nodes']}
    candidates = {wid for wid, way in rail.ways.items()
                  if wid not in selected and way['tags'].get('service') == 'crossover'}
    by_node = defaultdict(set)
    for wid in candidates:
        for n in rail.ways[wid]['nodes']:
            by_node[n].add(wid)
    while candidates:
        todo = [min(candidates)]
        component, nodes = set(), set()
        while todo:
            wid = todo.pop()
            if wid not in candidates:
                continue
            candidates.remove(wid)
            component.add(wid)
            for n in rail.ways[wid]['nodes']:
                nodes.add(n)
                todo.extend(by_node[n] & candidates)
        if len(nodes & anchors) >= 2:
            selected.update(component)
    return selected


def check_connections(tracks, switches, max_angle=10.0):
    """Prueft die Anschluesse so, wie die Fahrsimulation sie sieht.

    An jedem Punkt, an dem sich Gleisenden oder Weichenschenkel treffen (exakt
    gleiche Koordinaten), wird jeder Uebergang in Gegenrichtung bewertet: vom
    Stammgleis in beide Schenkel, vom Schenkelende ins weiterfuehrende Gleis und
    an einer Kreuzung von jedem Schenkel in beide Schenkel der anderen V-Weiche.
    Zwei Enden, die in dieselbe Richtung zeigen (die Schenkel eines V), sind keine
    Verbindung. Zusaetzlich wird die Oeffnung jedes V (Winkel zwischen seinen
    Schenkeln) bewertet. Rueckgabe: dict mit ``uebergaenge``, ``zu_steil`` (Liste),
    ``max``, ``oeffnung_max`` und ``oeffnung_zu_steil``.
    """
    ends = defaultdict(list)          # Punkt -> [(Element, Richtung)]
    for number, points in enumerate(tracks):
        if len(points) < 2:
            continue
        for index, neighbour in ((0, 1), (len(points) - 1, len(points) - 2)):
            d = _unit(points[neighbour][0] - points[index][0], points[neighbour][1] - points[index][1])
            if d:
                ends[tuple(points[index])].append((('Gleis', number), d))
    for number, switch in enumerate(switches):
        apex, *legs = switch['points']
        for leg in legs:
            d = _unit(leg[0] - apex[0], leg[1] - apex[1])
            if d:
                ends[tuple(apex)].append((('Weiche', number, tuple(leg)), d))
                ends[tuple(leg)].append((('Weiche', number, tuple(leg)), (-d[0], -d[1])))
    openings = []
    for switch in switches:
        apex, leg_a, leg_b = switch['points']
        da = _unit(leg_a[0] - apex[0], leg_a[1] - apex[1])
        db = _unit(leg_b[0] - apex[0], leg_b[1] - apex[1])
        if da and db:
            openings.append(180.0 - divergence(da, db))
    values = []
    for entries in ends.values():
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                (ea, da), (eb, db) = entries[i], entries[j]
                if ea == eb:
                    continue
                value = divergence(da, db)
                if value < 90.0:
                    values.append(value)
    return {'uebergaenge': len(values), 'zu_steil': sorted(v for v in values if v > max_angle),
            'max': max(values, default=0.0),
            'oeffnung_max': max(openings, default=0.0),
            'oeffnung_zu_steil': sorted(v for v in openings if v > max_angle)}
