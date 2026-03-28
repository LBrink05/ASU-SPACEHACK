from src.synthetic_sat import generate_synthetic_waypoints
from src.gee_fetchers import satellite_summary

fake_route = {
    "edges": [
        {"from": "Shanghai", "to": "Singapore", "from_lat": 31.2, "from_lon": 121.5, "to_lat": 1.3, "to_lon": 103.8, "mode": "ocean"},
        {"from": "Singapore", "to": "Rotterdam", "from_lat": 1.3, "from_lon": 103.8, "to_lat": 51.9, "to_lon": 4.5, "mode": "ocean"},
    ],
    "total_distance_km": 18000,
}
wps = [
    {"lat": 31.2, "lon": 121.5, "ts": "2026-03-20 00:00:00"},
    {"lat": 20.0, "lon": 115.0, "ts": "2026-03-22 00:00:00"},
    {"lat": 10.0, "lon": 108.0, "ts": "2026-03-24 00:00:00"},
    {"lat": 1.3, "lon": 103.8, "ts": "2026-03-26 00:00:00"},
    {"lat": 10.0, "lon": 80.0, "ts": "2026-03-28 00:00:00"},
    {"lat": 20.0, "lon": 60.0, "ts": "2026-03-30 00:00:00"},
    {"lat": 35.0, "lon": 30.0, "ts": "2026-04-01 00:00:00"},
    {"lat": 51.9, "lon": 4.5, "ts": "2026-04-03 00:00:00"},
]
enriched = generate_synthetic_waypoints(wps, fake_route, sample_every=2)
summary = satellite_summary(enriched, route=fake_route)

print("Mode test PASS")
print("NO2 mean:", summary["no2_mean"])
print("SAR mean:", summary["sar_mean"])
print("SST mean:", summary["sst_mean"])
print("CO mean:", summary["co_mean"])
print("Sampled:", summary["waypoints_sampled"], "/", summary["waypoints_total"])
print("Risk score:", summary["satellite_risk_score"])
print("Emissions band:", summary["emissions_verification"]["pollution_band"])
print("Sea state:", summary["sea_state"]["risk_level"])
print("Warning zones:", len(summary["warning_zones"]))
print("Air quality:", summary["air_quality"]["clean_pct"], "% clean")
