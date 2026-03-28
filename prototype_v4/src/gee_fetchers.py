"""
GEE satellite data fetchers for NeoNomad.

Queries Google Earth Engine for real satellite observations at route waypoints.
Each fetcher takes a waypoint dict {lat, lon, ts} and returns a float or None.
Results are cached in SQLite to avoid repeat API calls.

GEE project: deductive-water-426713-b2
"""

import sqlite3
import pathlib
from datetime import datetime, timedelta

_DB_PATH = pathlib.Path(__file__).parent.parent / "db" / "satellite_cache.db"
_GEE_PROJECT = "deductive-water-426713-b2"
_ee = None
_initialized = False


def _ensure_db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sat_cache (
            lat REAL, lon REAL, ts TEXT, instrument TEXT,
            value REAL,
            fetched_at TEXT,
            PRIMARY KEY (lat, lon, ts, instrument)
        )
    """)
    conn.commit()
    return conn


def _cache_get(conn, lat, lon, ts, instrument):
    row = conn.execute(
        "SELECT value FROM sat_cache WHERE lat=? AND lon=? AND ts=? AND instrument=?",
        (round(lat, 4), round(lon, 4), ts, instrument),
    ).fetchone()
    return row[0] if row else None


def _cache_set(conn, lat, lon, ts, instrument, value):
    conn.execute(
        "INSERT OR REPLACE INTO sat_cache (lat, lon, ts, instrument, value, fetched_at) VALUES (?,?,?,?,?,?)",
        (round(lat, 4), round(lon, 4), ts, instrument, value, datetime.utcnow().isoformat()),
    )
    conn.commit()


def init_gee():
    """Initialize GEE. Returns True if successful."""
    global _ee, _initialized
    if _initialized:
        return _ee is not None
    try:
        import ee
        ee.Initialize(project=_GEE_PROJECT)
        _ee = ee
        _initialized = True
        return True
    except Exception as exc:
        print(f"GEE init failed: {exc}")
        _initialized = True
        return False


def is_available():
    return init_gee()


def _time_window(ts_str, days=7):
    """Return a time window ending at ts, looking backward by `days`."""
    t = datetime.strptime(ts_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
    return (t - timedelta(days=days)).strftime("%Y-%m-%d"), t.strftime("%Y-%m-%d")


def _validate_and_print(instrument, value, lat, lon, ts):
    """Print validation info and return value if plausible, else None."""
    if value is None:
        print(f"  {instrument}: no data at ({lat},{lon}) {ts}")
        return None

    # Plausibility checks (ranges are approximate, adjust as needed)
    plausible = True
    if instrument == "NO2" and (value <= 0 or value > 1e-3):
        plausible = False
    elif instrument == "SAR" and (value < -30 or value > 0):
        plausible = False
    elif instrument == "SST" and (value < -2 or value > 40):
        plausible = False
    elif instrument == "CO" and (value <= 0 or value > 0.1):
        plausible = False

    if plausible:
        print(f"  {instrument}: real data = {value:.6f} at ({lat},{lon}) {ts}")
        return value
    else:
        print(f"  {instrument}: WARNING - suspicious value {value:.6f} (may be pseudo-data) at ({lat},{lon}) {ts}")
        return value  # Still return it, but flagged


def fetch_no2(wp):
    """TROPOMI NO2 column density (mol/m2) at a waypoint."""
    if not is_available():
        return None
    ee = _ee
    point = ee.Geometry.Point([wp["lon"], wp["lat"]])
    t0, t1 = _time_window(wp["ts"])
    col = (
        ee.ImageCollection("COPERNICUS/S5P/NRTI/L3_NO2")
        .filterBounds(point)
        .filterDate(t0, t1)
        .select("NO2_column_number_density")
    )
    if col.size().getInfo() == 0:
        return None
    val = col.first().reduceRegion(ee.Reducer.mean(), point, 1000).getInfo().get(
        "NO2_column_number_density"
    )
    return _validate_and_print("NO2", val, wp["lat"], wp["lon"], wp["ts"])


def fetch_sar(wp):
    """Sentinel-1 SAR VV backscatter (dB) at a waypoint."""
    if not is_available():
        return None
    ee = _ee
    point = ee.Geometry.Point([wp["lon"], wp["lat"]])
    t0, t1 = _time_window(wp["ts"])
    col = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(point)
        .filterDate(t0, t1)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .select("VV")
    )
    if col.size().getInfo() == 0:
        return None
    val = col.first().reduceRegion(ee.Reducer.mean(), point, 1000).getInfo().get("VV")
    return _validate_and_print("SAR", val, wp["lat"], wp["lon"], wp["ts"])


def fetch_sst(wp):
    """Sea surface temperature (C) from NOAA OISST."""
    if not is_available():
        return None
    ee = _ee
    point = ee.Geometry.Point([wp["lon"], wp["lat"]])
    t0, t1 = _time_window(wp["ts"])
    col = (
        ee.ImageCollection("NOAA/CDR/OISST/V2_1")
        .filterBounds(point)
        .filterDate(t0, t1)
        .select("sst")
    )
    if col.size().getInfo() == 0:
        return None
    raw = col.first().reduceRegion(ee.Reducer.mean(), point, 1000).getInfo().get("sst")
    val = raw * 0.01 if raw is not None else None
    return _validate_and_print("SST", val, wp["lat"], wp["lon"], wp["ts"])


def fetch_co(wp):
    """CO column density (mol/m2) from TROPOMI."""
    if not is_available():
        return None
    ee = _ee
    point = ee.Geometry.Point([wp["lon"], wp["lat"]])
    t0, t1 = _time_window(wp["ts"])
    col = (
        ee.ImageCollection("COPERNICUS/S5P/NRTI/L3_CO")
        .filterBounds(point)
        .filterDate(t0, t1)
        .select("CO_column_number_density")
    )
    if col.size().getInfo() == 0:
        return None
    val = col.first().reduceRegion(ee.Reducer.mean(), point, 1000).getInfo().get(
        "CO_column_number_density"
    )
    return _validate_and_print("CO", val, wp["lat"], wp["lon"], wp["ts"])


def enrich_waypoints(waypoints, sample_every=4):
    """Fetch all satellite instruments for a list of waypoints.

    Samples every Nth waypoint to keep GEE API calls manageable.
    Returns list of dicts with satellite readings added.
    """
    conn = _ensure_db()
    results = []

    for i, wp in enumerate(waypoints):
        enriched = dict(wp)
        enriched["satellite"] = {}

        if i % sample_every != 0:
            enriched["satellite"]["sampled"] = False
            results.append(enriched)
            continue

        enriched["satellite"]["sampled"] = True

        for instrument, fetcher in [
            ("no2_mol_m2", fetch_no2),
            ("sar_vv_db", fetch_sar),
            ("sst_c", fetch_sst),
            ("co_mol_m2", fetch_co),
        ]:
            cached = _cache_get(conn, wp["lat"], wp["lon"], wp["ts"], instrument)
            if cached is not None:
                enriched["satellite"][instrument] = cached
                print(f"  Using cached {instrument}: {cached:.6f} at ({wp['lat']},{wp['lon']}) {wp['ts']}")
            else:
                try:
                    val = fetcher(wp)
                    if val is not None:
                        _cache_set(conn, wp["lat"], wp["lon"], wp["ts"], instrument, val)
                    enriched["satellite"][instrument] = val
                except Exception as exc:
                    print(f"  GEE error ({instrument}): {exc}")
                    enriched["satellite"][instrument] = None

        results.append(enriched)

    conn.close()
    return results


def satellite_summary(enriched_waypoints):
    """Compute aggregate statistics from enriched waypoints."""
    no2_vals = []
    sar_vals = []
    sst_vals = []
    co_vals = []
    sampled_count = 0

    for wp in enriched_waypoints:
        sat = wp.get("satellite", {})
        if not sat.get("sampled"):
            continue
        sampled_count += 1
        if sat.get("no2_mol_m2") is not None:
            no2_vals.append(sat["no2_mol_m2"])
        if sat.get("sar_vv_db") is not None:
            sar_vals.append(sat["sar_vv_db"])
        if sat.get("sst_c") is not None:
            sst_vals.append(sat["sst_c"])
        if sat.get("co_mol_m2") is not None:
            co_vals.append(sat["co_mol_m2"])

    def _avg(vals):
        return round(sum(vals) / len(vals), 6) if vals else None

    return {
        "waypoints_sampled": sampled_count,
        "waypoints_total": len(enriched_waypoints),
        "no2_mean": _avg(no2_vals),
        "no2_count": len(no2_vals),
        "sar_mean": _avg(sar_vals),
        "sar_count": len(sar_vals),
        "sst_mean": _avg(sst_vals),
        "sst_count": len(sst_vals),
        "co_mean": _avg(co_vals),
        "co_count": len(co_vals),
        "gee_available": is_available(),
    }