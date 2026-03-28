"""
ocean_router.py – Generate water-safe waypoints for ocean edges.

Uses Natural Earth 110m land polygons + a sparse graph of ocean
routing nodes (straits, capes, open-water turning points) to produce
multi-point polylines that avoid crossing land masses.
"""

import json, math, os, pathlib
from functools import lru_cache

import networkx as nx
from shapely.geometry import shape, MultiPolygon, Point, LineString
from shapely.prepared import prep

# ── paths ──────────────────────────────────────────────────────
_HERE = pathlib.Path(__file__).resolve().parent
_DATA = _HERE.parent / "data"
_LAND_FILE = _DATA / "ne_land_110m.geojson"


# ── land mask (loaded once) ────────────────────────────────────
@lru_cache(maxsize=1)
def _load_land():
    with open(_LAND_FILE) as f:
        gj = json.load(f)
    polys = []
    for ft in gj["features"]:
        g = shape(ft["geometry"])
        if g.geom_type == "Polygon":
            polys.append(g)
        elif g.geom_type == "MultiPolygon":
            polys.extend(g.geoms)
    mp = MultiPolygon(polys)
    return prep(mp), mp          # prepared (fast contains) + raw


def _land_contains(lon, lat):
    """True if (lon, lat) falls on land.  Shapely uses (x=lon, y=lat)."""
    prepared, _ = _load_land()
    return prepared.contains(Point(lon, lat))


# ── great-circle helpers (copied from waypoint_generator) ──────
def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _interp(lat1, lon1, lat2, lon2, frac):
    """Great-circle slerp returning (lat, lon)."""
    la1, lo1, la2, lo2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d = 2 * math.asin(math.sqrt(
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    if d < 1e-12:
        return lat1, lon1
    a = math.sin((1 - frac) * d) / math.sin(d)
    b = math.sin(frac * d) / math.sin(d)
    x = a * math.cos(la1) * math.cos(lo1) + b * math.cos(la2) * math.cos(lo2)
    y = a * math.cos(la1) * math.sin(lo1) + b * math.cos(la2) * math.sin(lo2)
    z = a * math.sin(la1) + b * math.sin(la2)
    return (round(math.degrees(math.atan2(z, math.sqrt(x*x + y*y))), 4),
            round(math.degrees(math.atan2(y, x)), 4))


# ── ocean routing node graph ──────────────────────────────────
# Sparse set of open-water waypoints at known straits / capes /
# turning points.  Each tuple: (id, lat, lon).
OCEAN_NODES = [
    # Atlantic
    ("ow_gibraltar",      36.0,   -5.6),
    ("ow_off_dakar",      14.0,  -20.0),
    ("ow_mid_atlantic_n", 30.0,  -40.0),
    ("ow_mid_atlantic_eq", 5.0,  -25.0),
    ("ow_off_brazil",     -5.0,  -32.0),
    ("ow_caribbean",      15.0,  -72.0),
    ("ow_off_nova_scotia", 42.0, -60.0),
    ("ow_north_sea",      55.0,    3.0),
    ("ow_english_channel", 50.0,  -2.0),
    ("ow_biscay",         45.0,   -5.0),

    # West Africa
    ("ow_guinea_gulf",     3.0,    3.0),
    ("ow_off_luanda",     -9.0,   10.0),
    ("ow_off_walvis",    -23.0,   11.0),

    # South Africa / Indian Ocean
    ("ow_cape_good_hope",-35.5,   18.0),
    ("ow_off_mozambique",-15.0,   42.0),
    ("ow_off_mombasa",    -3.0,   43.0),

    # Red Sea / Gulf
    ("ow_bab_el_mandeb",  12.5,   43.5),
    ("ow_red_sea_mid",    20.0,   38.0),
    ("ow_suez_approach",  31.0,   32.6),
    ("ow_hormuz",         26.5,   56.5),
    ("ow_arabian_sea",    18.0,   62.0),

    # Indian Ocean
    ("ow_off_mumbai",     18.5,   70.0),
    ("ow_off_sri_lanka",   6.0,   78.0),
    ("ow_central_indian", -5.0,   70.0),

    # SE Asia
    ("ow_malacca_w",       4.0,   96.0),
    ("ow_singapore",       1.0,  104.5),
    ("ow_south_china_sea",12.0,  112.0),
    ("ow_off_vietnam",    10.0,  108.0),

    # East Asia / Pacific
    ("ow_taiwan_strait",  24.0,  119.5),
    ("ow_east_china_sea", 30.0,  126.0),
    ("ow_off_japan",      34.0,  140.0),
    ("ow_off_korea",      35.0,  130.0),
    ("ow_north_pacific",  35.0, -170.0),
    ("ow_mid_pacific_n",  20.0,  180.0),

    # Panama
    ("ow_panama_atlantic", 10.0, -80.0),
    ("ow_panama_pacific",   7.0, -81.0),

    # South America Pacific
    ("ow_off_ecuador",    -2.0,  -83.0),
    ("ow_off_chile",     -33.0,  -75.0),
    ("ow_cape_horn",     -56.0,  -67.5),

    # Oceania
    ("ow_east_australia",-30.0,  157.0),
    ("ow_coral_sea",     -15.0,  155.0),
    ("ow_tasman",        -38.0,  165.0),
    ("ow_off_nz",        -37.0,  174.0),
    ("ow_south_pacific",  -20.0, -160.0),
    ("ow_fiji_area",     -18.0,  175.0),

    # US coasts
    ("ow_off_la",         33.0, -120.0),
    ("ow_off_seattle",    48.0, -127.0),
    ("ow_gulf_mexico",    26.0,  -90.0),
    ("ow_off_ny",         40.0,  -72.0),
    ("ow_off_miami",      25.0,  -79.0),

    # Mediterranean
    ("ow_west_med",       38.0,    2.0),
    ("ow_central_med",    36.0,   15.0),
    ("ow_east_med",       34.0,   28.0),

    # Black Sea
    ("ow_black_sea",      43.0,   33.0),
    ("ow_bosphorus",      41.2,   29.0),

    # Gulf / Persian
    ("ow_persian_gulf",   27.0,   51.0),

    # Southern Ocean
    ("ow_south_atlantic", -45.0, -10.0),
    ("ow_south_indian",   -40.0,  60.0),
]

#  Adjacency – pairs of ocean-node IDs that can be connected
#  by a straight line over water.
OCEAN_EDGES = [
    # Atlantic N-S spine
    ("ow_north_sea", "ow_english_channel"),
    ("ow_english_channel", "ow_biscay"),
    ("ow_biscay", "ow_gibraltar"),
    ("ow_gibraltar", "ow_off_dakar"),
    ("ow_off_dakar", "ow_mid_atlantic_eq"),
    ("ow_mid_atlantic_eq", "ow_off_brazil"),
    ("ow_mid_atlantic_n", "ow_off_nova_scotia"),
    ("ow_mid_atlantic_n", "ow_mid_atlantic_eq"),
    ("ow_off_nova_scotia", "ow_off_ny"),
    ("ow_off_ny", "ow_off_miami"),
    ("ow_off_miami", "ow_caribbean"),
    ("ow_caribbean", "ow_panama_atlantic"),
    ("ow_off_miami", "ow_gulf_mexico"),
    ("ow_gulf_mexico", "ow_panama_atlantic"),
    ("ow_mid_atlantic_n", "ow_biscay"),
    ("ow_off_nova_scotia", "ow_north_sea"),
    ("ow_north_sea", "ow_mid_atlantic_n"),

    # West Africa coast
    ("ow_off_dakar", "ow_guinea_gulf"),
    ("ow_guinea_gulf", "ow_off_luanda"),
    ("ow_off_luanda", "ow_off_walvis"),
    ("ow_off_walvis", "ow_cape_good_hope"),

    # South/East Africa
    ("ow_cape_good_hope", "ow_off_mozambique"),
    ("ow_off_mozambique", "ow_off_mombasa"),
    ("ow_off_mombasa", "ow_bab_el_mandeb"),
    ("ow_bab_el_mandeb", "ow_red_sea_mid"),
    ("ow_red_sea_mid", "ow_suez_approach"),

    # Mediterranean
    ("ow_suez_approach", "ow_east_med"),
    ("ow_east_med", "ow_central_med"),
    ("ow_central_med", "ow_west_med"),
    ("ow_west_med", "ow_gibraltar"),
    ("ow_east_med", "ow_bosphorus"),
    ("ow_bosphorus", "ow_black_sea"),

    # Red Sea -> Indian Ocean
    ("ow_bab_el_mandeb", "ow_arabian_sea"),
    ("ow_arabian_sea", "ow_hormuz"),
    ("ow_hormuz", "ow_persian_gulf"),
    ("ow_arabian_sea", "ow_off_mumbai"),
    ("ow_off_mumbai", "ow_off_sri_lanka"),
    ("ow_off_sri_lanka", "ow_malacca_w"),
    ("ow_malacca_w", "ow_singapore"),
    ("ow_off_sri_lanka", "ow_central_indian"),
    ("ow_central_indian", "ow_off_mozambique"),
    ("ow_central_indian", "ow_south_indian"),

    # SE Asia
    ("ow_singapore", "ow_off_vietnam"),
    ("ow_singapore", "ow_south_china_sea"),
    ("ow_off_vietnam", "ow_south_china_sea"),
    ("ow_south_china_sea", "ow_taiwan_strait"),
    ("ow_taiwan_strait", "ow_east_china_sea"),
    ("ow_east_china_sea", "ow_off_korea"),
    ("ow_off_korea", "ow_off_japan"),
    ("ow_east_china_sea", "ow_off_japan"),

    # Pacific
    ("ow_off_japan", "ow_north_pacific"),
    ("ow_north_pacific", "ow_off_la"),
    ("ow_north_pacific", "ow_off_seattle"),
    ("ow_off_la", "ow_off_seattle"),
    ("ow_off_japan", "ow_mid_pacific_n"),
    ("ow_mid_pacific_n", "ow_north_pacific"),
    ("ow_mid_pacific_n", "ow_fiji_area"),

    # Panama Pacific side
    ("ow_panama_pacific", "ow_off_ecuador"),
    ("ow_off_ecuador", "ow_off_chile"),
    ("ow_off_chile", "ow_cape_horn"),
    ("ow_panama_pacific", "ow_off_la"),
    ("ow_panama_atlantic", "ow_panama_pacific"),  # canal transit

    # South America
    ("ow_cape_horn", "ow_south_atlantic"),
    ("ow_south_atlantic", "ow_cape_good_hope"),
    ("ow_off_brazil", "ow_cape_horn"),
    ("ow_off_brazil", "ow_off_dakar"),

    # Oceania
    ("ow_singapore", "ow_coral_sea"),
    ("ow_coral_sea", "ow_east_australia"),
    ("ow_east_australia", "ow_tasman"),
    ("ow_tasman", "ow_off_nz"),
    ("ow_off_nz", "ow_fiji_area"),
    ("ow_fiji_area", "ow_south_pacific"),
    ("ow_south_pacific", "ow_off_chile"),

    # Southern Ocean crossings
    ("ow_cape_good_hope", "ow_south_atlantic"),
    ("ow_south_indian", "ow_east_australia"),
    ("ow_cape_good_hope", "ow_south_indian"),

    # US east-west via Panama
    ("ow_off_la", "ow_panama_pacific"),
    ("ow_off_miami", "ow_panama_atlantic"),
]


# ── build the routing graph (loaded once) ──────────────────────
@lru_cache(maxsize=1)
def _build_ocean_graph():
    G = nx.Graph()
    for nid, lat, lon in OCEAN_NODES:
        G.add_node(nid, lat=lat, lon=lon)
    for a, b in OCEAN_EDGES:
        la1, lo1 = G.nodes[a]["lat"], G.nodes[a]["lon"]
        la2, lo2 = G.nodes[b]["lat"], G.nodes[b]["lon"]
        G.add_edge(a, b, dist=_haversine(la1, lo1, la2, lo2))
    return G


# ── core API ───────────────────────────────────────────────────
def crosses_land(lat1, lon1, lat2, lon2, samples=30):
    """Check if the great-circle between two points crosses land."""
    for i in range(1, samples):
        frac = i / samples
        lat, lon = _interp(lat1, lon1, lat2, lon2, frac)
        if _land_contains(lon, lat):
            return True
    return False


def _nearest_ocean_node(lat, lon, G):
    """Find the closest ocean routing node to a given coordinate."""
    best, best_d = None, float("inf")
    for nid, data in G.nodes(data=True):
        d = _haversine(lat, lon, data["lat"], data["lon"])
        if d < best_d:
            best, best_d = nid, d
    return best


def _densify_segment(lat1, lon1, lat2, lon2, target_pts=6):
    """Interpolate a segment into several intermediate points."""
    pts = []
    for i in range(1, target_pts):
        frac = i / target_pts
        pts.append(_interp(lat1, lon1, lat2, lon2, frac))
    return pts


def _find_offshore_midpoint(lat1, lon1, lat2, lon2):
    """
    For short coastal edges, find a water-safe midpoint by searching
    perpendicular to the edge direction for a point in the ocean.
    """
    mid_lat = (lat1 + lat2) / 2
    dlon = lon2 - lon1
    if dlon > 180:
        mid_lon = ((lon1 + lon2 + 360) / 2) % 360
        if mid_lon > 180:
            mid_lon -= 360
    elif dlon < -180:
        mid_lon = ((lon1 + lon2 - 360) / 2) % 360
        if mid_lon < -180:
            mid_lon += 360
    else:
        mid_lon = (lon1 + lon2) / 2

    # Perpendicular direction (rotate 90°)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    # Two perpendicular candidates (left and right of the line)
    perp1 = (-dlon, dlat)
    perp2 = (dlon, -dlat)

    # Normalize and try increasing offsets
    for perp in (perp1, perp2):
        mag = math.sqrt(perp[0] ** 2 + perp[1] ** 2)
        if mag < 1e-6:
            continue
        ulat, ulon = perp[0] / mag, perp[1] / mag
        for offset in (1.5, 2.5, 3.5, 5.0, 7.0, 10.0):
            test_lat = mid_lat + ulat * offset
            test_lon = mid_lon + ulon * offset
            if not _land_contains(test_lon, test_lat):
                return test_lat, test_lon
    return None


def generate_ocean_waypoints(lat1, lon1, lat2, lon2, total_pts=35):
    """
    Given an ocean edge's start and end coordinates, return a list
    of [lat, lon] waypoints that route around land masses.

    Returns None if the direct great-circle doesn't cross land
    (no waypoints needed — frontend draws a direct line).
    """
    if not crosses_land(lat1, lon1, lat2, lon2):
        return None

    dist = _haversine(lat1, lon1, lat2, lon2)

    # ── short coastal edges (<1200km): use offshore midpoint ──
    if dist < 1200:
        offshore = _find_offshore_midpoint(lat1, lon1, lat2, lon2)
        if offshore is not None:
            mid_lat, mid_lon = offshore
            # Build a 3-control-point arc: start → offshore mid → end
            pts_per_seg = max(4, total_pts // 2)
            waypoints = [[lat1, lon1]]
            for j in range(1, pts_per_seg + 1):
                frac = j / (pts_per_seg + 1)
                lt, ln = _interp(lat1, lon1, mid_lat, mid_lon, frac)
                waypoints.append([lt, ln])
            waypoints.append([mid_lat, mid_lon])
            for j in range(1, pts_per_seg + 1):
                frac = j / (pts_per_seg + 1)
                lt, ln = _interp(mid_lat, mid_lon, lat2, lon2, frac)
                waypoints.append([lt, ln])
            waypoints.append([lat2, lon2])
            return waypoints
        # fallback to graph routing below

    # ── long edges: use ocean routing node graph ──
    G = _build_ocean_graph()

    start_node = _nearest_ocean_node(lat1, lon1, G)
    end_node = _nearest_ocean_node(lat2, lon2, G)

    if start_node == end_node:
        rn_lat = G.nodes[start_node]["lat"]
        rn_lon = G.nodes[start_node]["lon"]
        return [[lat1, lon1], [rn_lat, rn_lon], [lat2, lon2]]

    try:
        path = nx.shortest_path(G, start_node, end_node, weight="dist")
    except nx.NetworkXNoPath:
        return None

    # Build control points: start -> routing nodes -> end
    control = [(lat1, lon1)]
    for nid in path:
        control.append((G.nodes[nid]["lat"], G.nodes[nid]["lon"]))
    control.append((lat2, lon2))

    # Densify: distribute target_pts across all segments proportionally
    seg_dists = []
    for i in range(len(control) - 1):
        seg_dists.append(_haversine(control[i][0], control[i][1],
                                     control[i + 1][0], control[i + 1][1]))
    total_dist = sum(seg_dists)
    if total_dist < 1:
        return None

    waypoints = [[control[0][0], control[0][1]]]
    for sd, (la1, lo1), (la2, lo2) in zip(
            seg_dists, control[:-1], control[1:]):
        n_seg = max(2, round(total_pts * sd / total_dist))
        for j in range(1, n_seg + 1):
            frac = j / (n_seg + 1)
            lat, lon = _interp(la1, lo1, la2, lo2, frac)
            waypoints.append([lat, lon])
    waypoints.append([control[-1][0], control[-1][1]])

    return waypoints
