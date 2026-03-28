"""
Synthetic satellite data generator for NeoNomad.

Produces realistic mock satellite readings when GEE is unavailable.
Uses deterministic seeding from waypoint coordinates so results are
reproducible for the same route.
"""

import math
import hashlib


def _seed_from_coords(lat, lon):
    """Deterministic pseudo-random seed from coordinates."""
    h = hashlib.md5(f"{lat:.4f},{lon:.4f}".encode()).hexdigest()
    return int(h[:8], 16)


def _pseudo_random(seed, n=1):
    """Simple deterministic float(s) in [0, 1) from a seed."""
    vals = []
    s = seed
    for _ in range(n):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        vals.append(s / 0x7FFFFFFF)
    return vals if n > 1 else vals[0]


def _is_ocean_point(lat, lon):
    """Very rough heuristic: True if likely over open ocean."""
    # Major landmasses rough bounding boxes
    land = [
        (-10, 95, 10, 145),    # Indonesia
        (20, 100, 55, 145),    # East Asia
        (35, -10, 70, 40),     # Europe
        (25, -130, 50, -65),   # North America
        (-35, -75, 15, -35),   # South America
        (-40, 15, 35, 55),     # Africa
        (-45, 110, -10, 155),  # Australia
    ]
    for lat0, lon0, lat1, lon1 in land:
        if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
            return False
    return True


def _port_proximity_factor(lat, lon, route):
    """0-1 factor: 1.0 at route origin/dest ports, fading with distance."""
    edges = route.get("edges", [])
    if not edges:
        return 0.0
    ports = [
        (edges[0]["from_lat"], edges[0]["from_lon"]),
        (edges[-1]["to_lat"], edges[-1]["to_lon"]),
    ]
    min_dist = min(
        math.sqrt((lat - p[0]) ** 2 + (lon - p[1]) ** 2) for p in ports
    )
    return max(0.0, 1.0 - min_dist / 5.0)  # ~5 degrees ≈ 500 km fade


def generate_synthetic_waypoints(waypoints, route, sample_every=4):
    """Generate synthetic satellite-enriched waypoints.

    Mimics the structure of gee_fetchers.enrich_waypoints() output.
    """
    results = []

    for i, wp in enumerate(waypoints):
        enriched = dict(wp)
        enriched["satellite"] = {}

        if i % sample_every != 0:
            enriched["satellite"]["sampled"] = False
            results.append(enriched)
            continue

        enriched["satellite"]["sampled"] = True
        seed = _seed_from_coords(wp["lat"], wp["lon"])
        r1, r2, r3, r4, r5 = _pseudo_random(seed, 5)

        is_ocean = _is_ocean_point(wp["lat"], wp["lon"])
        port_factor = _port_proximity_factor(wp["lat"], wp["lon"], route)

        # NO₂: higher near ports/cities, lower over ocean
        base_no2 = 2e-5 if is_ocean else 5e-5
        no2 = base_no2 + r1 * 1.2e-4 * (0.3 + 0.7 * port_factor)
        enriched["satellite"]["no2_mol_m2"] = round(no2, 8)

        # CO: correlated with NO₂ but different scale
        base_co = 0.015 if is_ocean else 0.025
        co = base_co + r2 * 0.035 * (0.4 + 0.6 * port_factor)
        enriched["satellite"]["co_mol_m2"] = round(co, 6)

        # SAR VV: -20 (calm) to -5 (very rough), ocean only meaningful
        if is_ocean:
            sar = -20.0 + r3 * 15.0  # range -20 to -5
        else:
            sar = -18.0 + r3 * 6.0   # land: mostly calm signal
        enriched["satellite"]["sar_vv_db"] = round(sar, 2)

        # SST: latitude-dependent with some noise
        abs_lat = abs(wp["lat"])
        base_sst = 30.0 - abs_lat * 0.4  # ~30°C at equator, ~6°C at 60°
        sst = base_sst + (r4 - 0.5) * 6.0
        enriched["satellite"]["sst_c"] = round(max(-2.0, sst), 1)

        results.append(enriched)

    return results
