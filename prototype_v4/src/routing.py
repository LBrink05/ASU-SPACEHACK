"""
routing.py - Multi-objective transport route optimizer.

Builds a directed graph from network_nodes.json and network_edges.json.
Uses Yen's k-shortest paths with a composite weighted cost per edge.
"""

import json
import math
import pathlib
from typing import Optional

import networkx as nx

_DATA = pathlib.Path(__file__).parent.parent / "data"


def _load_graph() -> nx.DiGraph:
    with open(_DATA / "network_nodes.json") as f:
        nodes_doc = json.load(f)
    with open(_DATA / "network_edges.json") as f:
        edges_doc = json.load(f)

    g = nx.DiGraph()

    for node in nodes_doc["nodes"]:
        g.add_node(
            node["id"],
            label=node["label"],
            node_type=node["type"],
            lat=node["lat"],
            lon=node["lon"],
            region=node.get("region", ""),
            ets_scope=node.get("ets_scope", False),
        )

    for edge in edges_doc["edges"]:
        if "from" not in edge:
            continue
        g.add_edge(
            edge["from"],
            edge["to"],
            mode=edge["mode"],
            distance_km=edge["distance_km"],
            transit_days=edge["transit_days"],
            cost_usd_per_tonne=edge["cost_usd_per_tonne"],
            co2_g_per_tonne_km=edge["co2_g_per_tonne_km"],
            compliance_score=edge["compliance_score"],
            ets_scope=edge.get("ets_scope", False),
        )

    return g


_GRAPH: Optional[nx.DiGraph] = None


def get_graph() -> nx.DiGraph:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _load_graph()
    return _GRAPH


def node_options() -> list[dict]:
    """Return sorted list of node dicts for UI dropdowns."""
    g = get_graph()
    options = []
    for n, data in g.nodes(data=True):
        options.append({
            "id": n,
            "label": data["label"],
            "type": data["node_type"],
            "lat": data["lat"],
            "lon": data["lon"],
            "region": data["region"],
            "ets_scope": data["ets_scope"],
        })
    return sorted(options, key=lambda x: (x["region"], x["type"], x["label"]))


def snap_to_nearest_node(lat: float, lon: float) -> str:
    """Find the nearest graph node to arbitrary coordinates (haversine)."""
    g = get_graph()
    best_id = None
    best_dist = float("inf")
    for n, data in g.nodes(data=True):
        d = _haversine_km(lat, lon, data["lat"], data["lon"])
        if d < best_dist:
            best_dist = d
            best_id = n
    return best_id


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _edge_cost(u, v, data, w_emissions, w_cost, w_compliance, w_time, cargo_t):
    co2_kg = data["co2_g_per_tonne_km"] * data["distance_km"] * cargo_t / 1000.0
    total_cost_usd = data["cost_usd_per_tonne"] * cargo_t
    inv_compliance = (6 - data["compliance_score"]) / 4.0
    transit = data["transit_days"]

    co2_scale = 2000.0
    cost_scale = 20000.0
    time_scale = 30.0

    norm_co2 = min(co2_kg / co2_scale, 3.0)
    norm_cost = min(total_cost_usd / cost_scale, 3.0)
    norm_compliance = inv_compliance
    norm_time = min(transit / time_scale, 3.0)

    total_weight = w_emissions + w_cost + w_compliance + w_time
    if total_weight == 0:
        total_weight = 1.0

    return (
        w_emissions * norm_co2
        + w_cost * norm_cost
        + w_compliance * norm_compliance
        + w_time * norm_time
    ) / total_weight


def _assign_weights(graph, w_emissions, w_cost, w_compliance, w_time, cargo_t):
    g = graph.copy()
    for u, v, data in g.edges(data=True):
        data["weight"] = _edge_cost(u, v, data, w_emissions, w_cost, w_compliance, w_time, cargo_t)
    return g


def _summarize_path(path, graph, cargo_t):
    edges = []
    total_co2_kg = 0.0
    total_cost_usd = 0.0
    total_days = 0.0
    total_distance_km = 0.0
    ets_exposure = False
    compliance_scores = []
    modes_used = []

    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        data = graph[u][v]
        co2_kg = data["co2_g_per_tonne_km"] * data["distance_km"] * cargo_t / 1000.0
        cost_usd = data["cost_usd_per_tonne"] * cargo_t
        total_co2_kg += co2_kg
        total_cost_usd += cost_usd
        total_days += data["transit_days"]
        total_distance_km += data["distance_km"]
        if data.get("ets_scope"):
            ets_exposure = True
        compliance_scores.append(data["compliance_score"])
        mode = data["mode"]
        if not modes_used or modes_used[-1] != mode:
            modes_used.append(mode)

        edges.append({
            "from_id": u,
            "to_id": v,
            "from": graph.nodes[u]["label"],
            "to": graph.nodes[v]["label"],
            "from_lat": graph.nodes[u]["lat"],
            "from_lon": graph.nodes[u]["lon"],
            "to_lat": graph.nodes[v]["lat"],
            "to_lon": graph.nodes[v]["lon"],
            "mode": mode,
            "distance_km": data["distance_km"],
            "co2_kg": round(co2_kg, 1),
            "cost_usd": round(cost_usd, 0),
            "transit_days": data["transit_days"],
            "ets_scope": data.get("ets_scope", False),
            "compliance_score": data["compliance_score"],
        })

    avg_compliance = sum(compliance_scores) / len(compliance_scores) if compliance_scores else 0

    return {
        "nodes": path,
        "node_labels": [graph.nodes[n]["label"] for n in path],
        "node_types": [graph.nodes[n]["node_type"] for n in path],
        "edges": edges,
        "total_co2_kg": round(total_co2_kg, 1),
        "total_cost_usd": round(total_cost_usd, 0),
        "total_transit_days": round(total_days, 1),
        "total_distance_km": round(total_distance_km, 0),
        "ets_exposure": ets_exposure,
        "avg_compliance_score": round(avg_compliance, 2),
        "modes_used": modes_used,
    }


def find_routes(
    origin: str,
    destination: str,
    cargo_t: float = 1.0,
    w_emissions: float = 0.35,
    w_cost: float = 0.25,
    w_compliance: float = 0.25,
    w_time: float = 0.15,
    k: int = 3,
) -> list[dict]:
    base_graph = get_graph()
    weighted_graph = _assign_weights(
        base_graph, w_emissions, w_cost, w_compliance, w_time, cargo_t
    )

    try:
        gen = nx.shortest_simple_paths(weighted_graph, origin, destination, weight="weight")
        raw_paths = []
        for path in gen:
            raw_paths.append(path)
            if len(raw_paths) >= k:
                break
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        raw_paths = []

    routes = []
    for rank, path in enumerate(raw_paths, start=1):
        summary = _summarize_path(path, base_graph, cargo_t)
        summary["rank"] = rank
        routes.append(summary)

    return routes
