"""
ets_advisor.py - EU Emissions Trading System compliance assessor.

Evaluates each route leg for EU ETS / FuelEU Maritime / CORSIA exposure
and estimates carbon costs based on current phase-in schedule.
"""

# EU ETS maritime phase-in schedule (% of emissions to surrender allowances)
_PHASE_IN = {2024: 0.40, 2025: 0.70, 2026: 1.00}

# Approximate EU ETS allowance price (EUR per tonne CO2)
_EUA_PRICE = {2024: 58, 2025: 65, 2026: 70}

# EU port regions (nodes with ets_scope=True in the graph)
_EU_REGIONS = {"europe"}

# IMO fuel consumption model constants
_IMO_K = 0.003  # fuel consumption coefficient
_CO2_PER_T_FUEL = 3.206  # tonnes CO2 per tonne HFO


def _current_year():
    from datetime import datetime
    return datetime.utcnow().year


def _get_phase_in(year=None):
    if year is None:
        year = _current_year()
    if year >= 2026:
        return 1.00
    return _PHASE_IN.get(year, 0.0)


def _get_eua_price(year=None):
    if year is None:
        year = _current_year()
    if year >= 2026:
        return _EUA_PRICE.get(2026, 70)
    return _EUA_PRICE.get(year, 65)


def assess_leg(leg, from_region=None, to_region=None, from_ets=False, to_ets=False, year=None):
    """Assess EU ETS exposure for a single route leg.

    Args:
        leg: Edge dict from route summary (mode, co2_kg, distance_km, etc.)
        from_region: Region string of origin node
        to_region: Region string of destination node
        from_ets: Whether origin node is in ETS scope
        to_ets: Whether destination node is in ETS scope
        year: Assessment year (default: current)

    Returns:
        Dict with ETS assessment details.
    """
    mode = leg["mode"]
    co2_kg = leg.get("co2_kg", 0)
    phase_in = _get_phase_in(year)
    eua_price = _get_eua_price(year)

    result = {
        "mode": mode,
        "from": leg.get("from", ""),
        "to": leg.get("to", ""),
        "co2_kg": co2_kg,
        "ets_scope_pct": 0.0,
        "liable_co2_kg": 0.0,
        "phase_in_factor": phase_in,
        "eua_price_eur": eua_price,
        "ets_cost_eur": 0.0,
        "regulation": "",
        "notes": "",
    }

    if mode == "ocean":
        if from_ets and to_ets:
            result["ets_scope_pct"] = 1.0
            result["regulation"] = "EU ETS Maritime (intra-EU)"
            result["notes"] = "100% of emissions — both ports in EU/EEA"
        elif from_ets or to_ets:
            result["ets_scope_pct"] = 0.5
            result["regulation"] = "EU ETS Maritime (extra-EU)"
            result["notes"] = "50% of emissions — one port in EU/EEA"
        else:
            result["regulation"] = "Outside EU ETS Maritime scope"
            result["notes"] = "Neither port in EU/EEA — no ETS obligation"

    elif mode == "air":
        if from_ets and to_ets:
            result["ets_scope_pct"] = 1.0
            result["regulation"] = "EU ETS Aviation (intra-EEA)"
            result["notes"] = "Full ETS coverage for intra-EEA flights"
        elif from_ets or to_ets:
            result["ets_scope_pct"] = 0.0
            result["regulation"] = "CORSIA (extra-EEA)"
            result["notes"] = "International flights covered by CORSIA, not EU ETS"
        else:
            result["regulation"] = "CORSIA"
            result["notes"] = "International aviation — CORSIA applies"

    elif mode == "truck":
        result["regulation"] = "EU ETS2 (road transport)"
        if from_ets or to_ets:
            result["notes"] = "ETS2 for road transport begins 2027. Currently exempt."
        else:
            result["notes"] = "Outside EU — not subject to EU ETS2"

    elif mode == "rail":
        result["regulation"] = "Rail — exempt from EU ETS"
        result["notes"] = "Rail transport is currently exempt from EU ETS"

    elif mode == "last_mile":
        if from_ets or to_ets:
            result["regulation"] = "EU ETS2 (road transport)"
            result["notes"] = "Last-mile delivery — ETS2 from 2027"
        else:
            result["regulation"] = "Outside EU ETS scope"
            result["notes"] = "Last-mile outside EU"

    # Calculate liable CO2 and cost
    result["liable_co2_kg"] = round(co2_kg * result["ets_scope_pct"] * phase_in, 2)
    result["ets_cost_eur"] = round(
        result["liable_co2_kg"] / 1000.0 * eua_price, 2
    )

    return result


def assess_route(route_dict, graph_nodes, year=None):
    """Assess full route for ETS compliance.

    Args:
        route_dict: Route from routing.find_routes()
        graph_nodes: Dict mapping node_id -> node data (with region, ets_scope)
        year: Assessment year

    Returns:
        Dict with per-leg assessments and totals.
    """
    legs = []
    total_liable_co2 = 0.0
    total_ets_cost = 0.0
    has_ets_exposure = False

    for edge in route_dict["edges"]:
        from_node = graph_nodes.get(edge["from_id"], {})
        to_node = graph_nodes.get(edge["to_id"], {})

        assessment = assess_leg(
            edge,
            from_region=from_node.get("region", ""),
            to_region=to_node.get("region", ""),
            from_ets=from_node.get("ets_scope", False),
            to_ets=to_node.get("ets_scope", False),
            year=year,
        )
        legs.append(assessment)
        total_liable_co2 += assessment["liable_co2_kg"]
        total_ets_cost += assessment["ets_cost_eur"]
        if assessment["ets_scope_pct"] > 0:
            has_ets_exposure = True

    return {
        "legs": legs,
        "total_liable_co2_kg": round(total_liable_co2, 2),
        "total_ets_cost_eur": round(total_ets_cost, 2),
        "has_ets_exposure": has_ets_exposure,
        "phase_in_factor": _get_phase_in(year),
        "eua_price_eur": _get_eua_price(year),
        "year": year or _current_year(),
    }
