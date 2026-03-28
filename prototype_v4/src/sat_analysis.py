"""
Satellite-derived route intelligence analysis.

Consumes enriched waypoints (from gee_fetchers.enrich_waypoints) and produces
actionable metrics: emissions verification, sea-state risk, port congestion,
air quality reports, composite risk scores, and map warning zones.
"""

import math

# ── Thresholds ──────────────────────────────────────────────────────
NO2_HIGH = 1e-4        # mol/m²  — high pollution
NO2_MODERATE = 5e-5    # mol/m²
CO_HIGH = 0.04         # mol/m²
CO_MODERATE = 0.03
SAR_ROUGH = -8.0       # dB  — rough seas
SAR_MODERATE = -12.0   # dB
SST_STORM_RISK = 28.0  # °C  — tropical cyclone threshold
CONGESTION_RADIUS_KM = 80.0


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _sampled(enriched_waypoints):
    """Yield only waypoints that were actually sampled by GEE."""
    for wp in enriched_waypoints:
        sat = wp.get("satellite", {})
        if sat.get("sampled"):
            yield wp


# ── Feature 1: Emissions Verification ──────────────────────────────

def compute_emissions_verification(enriched_waypoints, route):
    """Cross-check observed NO₂ + CO against modeled CO₂.

    Returns:
        {
            verification_score: float 0-1 (1 = clean corridor),
            pollution_band: str (low / moderate / high),
            hotspot_count: int,
            hotspots: [{lat, lon, no2, co, reason}],
            clean_pct: float,
            moderate_pct: float,
            polluted_pct: float,
        }
    """
    hotspots = []
    clean = moderate = polluted = 0

    for wp in _sampled(enriched_waypoints):
        sat = wp["satellite"]
        no2 = sat.get("no2_mol_m2")
        co = sat.get("co_mol_m2")

        is_hot = False
        reasons = []
        if no2 is not None and no2 > NO2_HIGH:
            is_hot = True
            reasons.append("NO₂ high")
        if co is not None and co > CO_HIGH:
            is_hot = True
            reasons.append("CO high")

        is_moderate = False
        if not is_hot:
            if (no2 is not None and no2 > NO2_MODERATE) or (co is not None and co > CO_MODERATE):
                is_moderate = True

        if is_hot:
            polluted += 1
            hotspots.append({
                "lat": wp["lat"], "lon": wp["lon"],
                "no2": no2, "co": co,
                "reason": " + ".join(reasons),
            })
        elif is_moderate:
            moderate += 1
        else:
            clean += 1

    total = clean + moderate + polluted
    if total == 0:
        return {
            "verification_score": None,
            "pollution_band": "unknown",
            "hotspot_count": 0, "hotspots": [],
            "clean_pct": 0, "moderate_pct": 0, "polluted_pct": 0,
        }

    score = round((clean + moderate * 0.5) / total, 3)
    if score >= 0.7:
        band = "low"
    elif score >= 0.4:
        band = "moderate"
    else:
        band = "high"

    return {
        "verification_score": score,
        "pollution_band": band,
        "hotspot_count": len(hotspots),
        "hotspots": hotspots,
        "clean_pct": round(clean / total * 100, 1),
        "moderate_pct": round(moderate / total * 100, 1),
        "polluted_pct": round(polluted / total * 100, 1),
    }


# ── Feature 2: Sea State Risk ──────────────────────────────────────

def compute_sea_state_risk(enriched_waypoints):
    """Assess sea-state risk from SAR VV backscatter.

    Returns:
        {
            sea_risk_score: float 0-1,
            risk_level: str (calm / moderate / rough),
            rough_waypoints: [{lat, lon, sar_vv}],
            calm_count, moderate_count, rough_count: int,
            sst_storm_risk: bool,
        }
    """
    calm = moderate = rough = 0
    rough_wps = []
    sst_risk = False

    for wp in _sampled(enriched_waypoints):
        sat = wp["satellite"]
        sar = sat.get("sar_vv_db")
        sst = sat.get("sst_c")

        if sst is not None and sst > SST_STORM_RISK:
            sst_risk = True

        if sar is None:
            continue
        if sar > SAR_ROUGH:
            rough += 1
            rough_wps.append({"lat": wp["lat"], "lon": wp["lon"], "sar_vv": sar})
        elif sar > SAR_MODERATE:
            moderate += 1
        else:
            calm += 1

    total = calm + moderate + rough
    if total == 0:
        return {
            "sea_risk_score": None,
            "risk_level": "unknown",
            "rough_waypoints": [],
            "calm_count": 0, "moderate_count": 0, "rough_count": 0,
            "sst_storm_risk": sst_risk,
        }

    score = round((rough + moderate * 0.3) / total, 3)
    if score >= 0.5:
        level = "rough"
    elif score >= 0.2:
        level = "moderate"
    else:
        level = "calm"

    return {
        "sea_risk_score": score,
        "risk_level": level,
        "rough_waypoints": rough_wps,
        "calm_count": calm,
        "moderate_count": moderate,
        "rough_count": rough,
        "sst_storm_risk": sst_risk,
    }


# ── Feature 7: Port Congestion Proxy ──────────────────────────────

def compute_port_congestion(enriched_waypoints, route):
    """Estimate congestion at origin/destination ports using SAR + NO₂.

    Returns list of port dicts:
        [{port, lat, lon, congestion_level, no2_reading, sar_reading}]
    """
    edges = route.get("edges", [])
    if not edges:
        return []

    # Identify port nodes (first and last edge endpoints)
    port_nodes = [
        {"name": edges[0]["from"], "lat": edges[0]["from_lat"], "lon": edges[0]["from_lon"]},
        {"name": edges[-1]["to"], "lat": edges[-1]["to_lat"], "lon": edges[-1]["to_lon"]},
    ]

    results = []
    for port in port_nodes:
        nearby_no2 = []
        nearby_sar = []

        for wp in _sampled(enriched_waypoints):
            dist = _haversine(port["lat"], port["lon"], wp["lat"], wp["lon"])
            if dist > CONGESTION_RADIUS_KM:
                continue
            sat = wp["satellite"]
            if sat.get("no2_mol_m2") is not None:
                nearby_no2.append(sat["no2_mol_m2"])
            if sat.get("sar_vv_db") is not None:
                nearby_sar.append(sat["sar_vv_db"])

        avg_no2 = sum(nearby_no2) / len(nearby_no2) if nearby_no2 else None
        avg_sar = sum(nearby_sar) / len(nearby_sar) if nearby_sar else None

        # Combine signals: high NO₂ near port = idling vessels/trucks; high SAR = vessel traffic
        score = 0
        signals = 0
        if avg_no2 is not None:
            signals += 1
            if avg_no2 > NO2_HIGH:
                score += 1.0
            elif avg_no2 > NO2_MODERATE:
                score += 0.5
        if avg_sar is not None:
            signals += 1
            if avg_sar > SAR_ROUGH:
                score += 1.0
            elif avg_sar > SAR_MODERATE:
                score += 0.5

        if signals == 0:
            level = "unknown"
        else:
            avg_score = score / signals
            if avg_score >= 0.7:
                level = "high"
            elif avg_score >= 0.3:
                level = "moderate"
            else:
                level = "low"

        results.append({
            "port": port["name"],
            "lat": port["lat"],
            "lon": port["lon"],
            "congestion_level": level,
            "no2_reading": round(avg_no2, 8) if avg_no2 is not None else None,
            "sar_reading": round(avg_sar, 2) if avg_sar is not None else None,
        })

    return results


# ── Feature 6: Air Quality Impact Report ──────────────────────────

def compute_air_quality_report(enriched_waypoints, route):
    """Classify route corridor into pollution tiers.

    Returns:
        {
            clean_km, moderate_km, polluted_km: float,
            total_km: float,
            clean_pct, moderate_pct, polluted_pct: float,
            esg_narrative: str,
        }
    """
    total_distance = route.get("total_distance_km", 0)

    tier_counts = {"clean": 0, "moderate": 0, "polluted": 0}
    sampled_list = list(_sampled(enriched_waypoints))
    total_sampled = len(sampled_list)

    for wp in sampled_list:
        sat = wp["satellite"]
        no2 = sat.get("no2_mol_m2")
        co = sat.get("co_mol_m2")

        if (no2 is not None and no2 > NO2_HIGH) or (co is not None and co > CO_HIGH):
            tier_counts["polluted"] += 1
        elif (no2 is not None and no2 > NO2_MODERATE) or (co is not None and co > CO_MODERATE):
            tier_counts["moderate"] += 1
        else:
            tier_counts["clean"] += 1

    if total_sampled == 0:
        return {
            "clean_km": 0, "moderate_km": 0, "polluted_km": 0,
            "total_km": total_distance,
            "clean_pct": 0, "moderate_pct": 0, "polluted_pct": 0,
            "esg_narrative": "Insufficient satellite data for air quality assessment.",
        }

    clean_pct = tier_counts["clean"] / total_sampled * 100
    moderate_pct = tier_counts["moderate"] / total_sampled * 100
    polluted_pct = tier_counts["polluted"] / total_sampled * 100

    clean_km = round(total_distance * tier_counts["clean"] / total_sampled, 1)
    moderate_km = round(total_distance * tier_counts["moderate"] / total_sampled, 1)
    polluted_km = round(total_distance * tier_counts["polluted"] / total_sampled, 1)

    # Generate ESG narrative
    if polluted_pct > 30:
        narrative = (
            f"This route traverses {polluted_km:,.0f} km of high-pollution corridors "
            f"({polluted_pct:.0f}% of route). Consider alternative routing through "
            f"cleaner corridors to reduce environmental exposure."
        )
    elif polluted_pct > 10:
        narrative = (
            f"Route passes through {polluted_km:,.0f} km of elevated-pollution zones "
            f"({polluted_pct:.0f}%). Overall air quality is moderate with "
            f"{clean_km:,.0f} km ({clean_pct:.0f}%) through clean corridors."
        )
    else:
        narrative = (
            f"Route primarily traverses clean-air corridors: {clean_km:,.0f} km "
            f"({clean_pct:.0f}%) in clean zones. Suitable for ESG-certified logistics."
        )

    return {
        "clean_km": clean_km,
        "moderate_km": moderate_km,
        "polluted_km": polluted_km,
        "total_km": total_distance,
        "clean_pct": round(clean_pct, 1),
        "moderate_pct": round(moderate_pct, 1),
        "polluted_pct": round(polluted_pct, 1),
        "esg_narrative": narrative,
    }


# ── Composite Risk Score (Feature 3) ──────────────────────────────

def compute_satellite_risk_score(emissions_v, sea_state, congestion):
    """Compute a composite 0-1 satellite risk score.

    Higher = riskier.  Used for post-hoc route re-ranking.
    """
    components = []

    ev = emissions_v.get("verification_score")
    if ev is not None:
        components.append(1.0 - ev)  # invert: high verification = low risk

    sr = sea_state.get("sea_risk_score")
    if sr is not None:
        components.append(sr)

    for port in congestion:
        lvl = port.get("congestion_level", "unknown")
        if lvl == "high":
            components.append(0.9)
        elif lvl == "moderate":
            components.append(0.5)
        elif lvl == "low":
            components.append(0.1)

    if not components:
        return None

    return round(sum(components) / len(components), 3)


# ── Warning Zones for Globe Icons ──────────────────────────────────

def generate_warning_zones(emissions_v, sea_state, congestion):
    """Produce icon markers for the Plotly globe.

    Returns:
        [{lat, lon, icon, label, severity, detail}]
    """
    zones = []

    # Pollution hotspots
    for hs in emissions_v.get("hotspots", []):
        zones.append({
            "lat": hs["lat"], "lon": hs["lon"],
            "icon": "pollution",
            "label": "Pollution Hotspot",
            "severity": "high",
            "detail": hs.get("reason", "Elevated NO₂/CO"),
        })

    # Rough seas
    for rw in sea_state.get("rough_waypoints", []):
        zones.append({
            "lat": rw["lat"], "lon": rw["lon"],
            "icon": "rough_sea",
            "label": "Rough Seas",
            "severity": "high",
            "detail": f"SAR VV: {rw['sar_vv']:.1f} dB",
        })

    # Congested ports
    for port in congestion:
        if port["congestion_level"] in ("high", "moderate"):
            zones.append({
                "lat": port["lat"], "lon": port["lon"],
                "icon": "congestion",
                "label": f"{port['port']} — {port['congestion_level'].title()} Congestion",
                "severity": port["congestion_level"],
                "detail": f"NO₂: {port['no2_reading']}" if port["no2_reading"] else "Traffic indicators elevated",
            })

    return zones


# ── Full Analysis Pipeline ─────────────────────────────────────────

def analyze_route(enriched_waypoints, route):
    """Run all satellite analyses and return combined result dict."""
    ev = compute_emissions_verification(enriched_waypoints, route)
    ss = compute_sea_state_risk(enriched_waypoints)
    pc = compute_port_congestion(enriched_waypoints, route)
    aq = compute_air_quality_report(enriched_waypoints, route)
    risk = compute_satellite_risk_score(ev, ss, pc)
    zones = generate_warning_zones(ev, ss, pc)

    return {
        "emissions_verification": ev,
        "sea_state": ss,
        "port_congestion": pc,
        "air_quality": aq,
        "satellite_risk_score": risk,
        "warning_zones": zones,
    }
