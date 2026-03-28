"""
waypoint_generator.py - Great-circle interpolation for route legs.

Given two endpoint coordinates, generates intermediate waypoints
along the great-circle path at regular distance intervals.
These waypoints are then passed to GEE fetchers for satellite enrichment.
"""

import math
from datetime import datetime, timedelta


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _intermediate_point(lat1, lon1, lat2, lon2, fraction):
    """Compute a point along the great-circle at a given fraction (0..1)."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d = 2 * math.asin(
        math.sqrt(
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
    )
    if d < 1e-12:
        return math.degrees(lat1), math.degrees(lon1)

    a = math.sin((1 - fraction) * d) / math.sin(d)
    b = math.sin(fraction * d) / math.sin(d)

    x = a * math.cos(lat1) * math.cos(lon1) + b * math.cos(lat2) * math.cos(lon2)
    y = a * math.cos(lat1) * math.sin(lon1) + b * math.cos(lat2) * math.sin(lon2)
    z = a * math.sin(lat1) + b * math.sin(lat2)

    lat_i = math.atan2(z, math.sqrt(x ** 2 + y ** 2))
    lon_i = math.atan2(y, x)
    return round(math.degrees(lat_i), 4), round(math.degrees(lon_i), 4)


def generate_waypoints(
    lat1, lon1, lat2, lon2,
    interval_km=200,
    speed_kmh=None,
    mode="ocean",
    start_ts=None,
):
    """Generate waypoints along a great-circle between two coordinates.

    Args:
        lat1, lon1: Origin coordinates
        lat2, lon2: Destination coordinates
        interval_km: Distance between waypoints (default 200 km)
        speed_kmh: Speed in km/h (auto-set per mode if None)
        mode: Transport mode (ocean/truck/rail/air/last_mile)
        start_ts: Starting timestamp string (default: now)

    Returns:
        List of dicts: [{lat, lon, ts, index}, ...]
    """
    default_speeds = {
        "ocean": 25.9,      # ~14 knots
        "truck": 65.0,
        "rail": 80.0,
        "air": 850.0,
        "last_mile": 40.0,
    }
    if speed_kmh is None:
        speed_kmh = default_speeds.get(mode, 50.0)

    if start_ts is None:
        start_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    t0 = datetime.strptime(start_ts.split(".")[0], "%Y-%m-%d %H:%M:%S")

    total_km = _haversine_km(lat1, lon1, lat2, lon2)
    if total_km < 1.0:
        return [{"lat": lat1, "lon": lon1, "ts": t0.strftime("%Y-%m-%d %H:%M:%S"), "index": 0}]

    n_points = max(int(total_km / interval_km), 2)
    waypoints = []
    for i in range(n_points + 1):
        frac = i / n_points
        lat, lon = _intermediate_point(lat1, lon1, lat2, lon2, frac)
        elapsed_km = total_km * frac
        elapsed_hours = elapsed_km / speed_kmh if speed_kmh > 0 else 0
        ts = (t0 + timedelta(hours=elapsed_hours)).strftime("%Y-%m-%d %H:%M:%S")
        waypoints.append({"lat": lat, "lon": lon, "ts": ts, "index": i})

    return waypoints


def waypoints_for_route(route_dict, start_ts=None):
    """Generate waypoints for every leg of a route.

    Args:
        route_dict: A route from routing.find_routes() with 'edges' list.
        start_ts: Optional starting timestamp.

    Returns:
        List of leg dicts:
        [{
            from_id, to_id, mode,
            waypoints: [{lat, lon, ts, index}, ...],
        }, ...]
    """
    if start_ts is None:
        start_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    current_ts = start_ts
    legs = []

    for edge in route_dict["edges"]:
        mode = edge["mode"]
        # Only generate dense waypoints for ocean and air (long-haul)
        if mode in ("ocean", "air"):
            interval = 300 if mode == "ocean" else 500
        else:
            interval = 100

        wps = generate_waypoints(
            edge["from_lat"], edge["from_lon"],
            edge["to_lat"], edge["to_lon"],
            interval_km=interval,
            mode=mode,
            start_ts=current_ts,
        )

        legs.append({
            "from_id": edge["from_id"],
            "to_id": edge["to_id"],
            "from": edge["from"],
            "to": edge["to"],
            "mode": mode,
            "distance_km": edge["distance_km"],
            "waypoints": wps,
        })

        # Advance clock by transit time for next leg
        t = datetime.strptime(current_ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
        t += timedelta(days=edge["transit_days"])
        current_ts = t.strftime("%Y-%m-%d %H:%M:%S")

    return legs
