from src.sat_analysis import analyze_route

fake_route = {
    "edges": [
        {"from": "Shanghai", "to": "Singapore", "from_lat": 31.2, "from_lon": 121.5, "to_lat": 1.3, "to_lon": 103.8, "mode": "ocean"},
        {"from": "Singapore", "to": "Rotterdam", "from_lat": 1.3, "from_lon": 103.8, "to_lat": 51.9, "to_lon": 4.5, "mode": "ocean"},
    ],
    "total_distance_km": 18000,
}
# Match the real enriched waypoint format from gee_fetchers.enrich_waypoints
fake_enriched = [
    {"lat": 31.2, "lon": 121.5, "satellite": {"sampled": True, "no2_mol_m2": 8e-5, "co_mol_m2": 0.035, "sar_vv_db": -14, "sst_c": 24}},
    {"lat": 1.3, "lon": 103.8, "satellite": {"sampled": True, "no2_mol_m2": 1.2e-4, "co_mol_m2": 0.045, "sar_vv_db": -6, "sst_c": 30}},
    {"lat": 51.9, "lon": 4.5, "satellite": {"sampled": True, "no2_mol_m2": 3e-5, "co_mol_m2": 0.02, "sar_vv_db": -15, "sst_c": 12}},
]

result = analyze_route(fake_enriched, fake_route)
print("risk_score:", result["satellite_risk_score"])
ev = result["emissions_verification"]
print("emissions band:", ev["pollution_band"], "score:", ev["verification_score"])
ss = result["sea_state"]
print("sea_state:", ss["risk_level"], "rough_count:", ss["rough_count"])
pc = result["port_congestion"]
print("port count:", len(pc), "levels:", [p["congestion_level"] for p in pc])
aq = result["air_quality"]
print("air_quality clean%:", aq["clean_pct"], "narrative:", aq["esg_narrative"][:60])
print("warning_zones count:", len(result["warning_zones"]))
print("PASS")
