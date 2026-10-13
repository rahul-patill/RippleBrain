"""Tests for the deterministic traversal layer.

These were written before ripple/graph.py. The direction test is the one that
matters most: an edge points from a dependency to its consumer, so impact must
flow forward (to consumers) and never backward (to dependencies).
"""
import sys
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ripple import graph as g


@pytest.fixture(scope="module")
def G():
    """The pristine sample graph: ingested docs must not change these results."""
    return g.load_graph(include_overlay=False)


# --- direction -------------------------------------------------------------

def test_direction_impact_hits_consumers_not_dependencies(G):
    """Changing auth_api affects things that call it, not things it calls."""
    affected = g.impact(G, "auth_api")
    ids = set(affected)

    # consumers, directly and transitively
    assert {"web_app", "mobile_app", "billing_service", "partner_portal"} <= ids
    assert {"mobile_tests", "web_tests", "billing_tests"} <= ids
    assert "legacy_token_runbook" in ids
    assert "reporting_tool" in ids  # 4 hops downstream

    # dependencies of auth_api must never appear
    assert ids.isdisjoint({"user_db", "session_cache", "token_service"})

    # and the changed node is not its own impact
    assert "auth_api" not in ids


def test_direction_edge_semantics_are_not_reversed(G):
    """auth_api -> mobile_app means mobile_app depends on auth_api."""
    assert G.has_edge("auth_api", "mobile_app")
    assert not G.has_edge("mobile_app", "auth_api")
    assert "mobile_app" in g.consumers(G, "auth_api")
    assert "mobile_app" not in g.dependencies(G, "auth_api")
    assert "user_db" in g.dependencies(G, "auth_api")


def test_hop_distances_are_shortest_paths(G):
    affected = g.impact(G, "auth_api")
    assert affected["mobile_app"]["hop"] == 1
    assert affected["mobile_tests"]["hop"] == 2
    assert affected["data_warehouse"]["hop"] == 3
    assert affected["reporting_tool"]["hop"] == 4


def test_paths_are_reported_from_the_changed_node(G):
    affected = g.impact(G, "auth_api")
    assert affected["mobile_tests"]["path"] == ["auth_api", "mobile_app", "mobile_tests"]
    assert affected["reporting_tool"]["path"][0] == "auth_api"
    assert affected["reporting_tool"]["path"][-1] == "reporting_tool"


# --- cycles ----------------------------------------------------------------

def test_sample_graph_has_a_real_cycle(G):
    """billing_service <-> notification_service is a deliberate two-way edge."""
    assert G.has_edge("billing_service", "notification_service")
    assert G.has_edge("notification_service", "billing_service")
    assert list(nx.simple_cycles(G))


def test_cycle_does_not_hang_and_visits_each_node_once(G):
    affected = g.impact(G, "auth_api")
    assert affected["notification_service"]["hop"] == 2
    # billing_service is reached at hop 1, not re-entered at hop 3 via the cycle
    assert affected["billing_service"]["hop"] == 1


def test_tight_cycle_terminates():
    """A -> B -> C -> A traversal returns, it does not recurse forever."""
    G = nx.DiGraph()
    G.add_edges_from([("a", "b"), ("b", "c"), ("c", "a")])
    for n in "abc":
        G.nodes[n].update(id=n, name=n.upper(), criticality="low", type="service")
    affected = g.impact(G, "a")
    assert set(affected) == {"b", "c"}
    assert affected["b"]["hop"] == 1 and affected["c"]["hop"] == 2


def test_self_loop_terminates():
    G = nx.DiGraph()
    G.add_edge("a", "a")
    G.nodes["a"].update(id="a", name="A", criticality="low", type="service")
    assert g.impact(G, "a") == {}


# --- leaves and edge cases -------------------------------------------------

def test_leaf_node_returns_empty(G):
    assert g.impact(G, "mobile_tests") == {}
    assert g.impact(G, "reporting_tool") == {}


def test_unknown_node_raises(G):
    with pytest.raises(KeyError):
        g.impact(G, "no_such_component")


# --- risk ------------------------------------------------------------------

def test_risk_score_formula(G):
    """score = criticality_weight(target) / hop_distance"""
    assert g.risk_score("high", 1) == 3.0
    assert g.risk_score("medium", 2) == 1.0
    assert g.risk_score("low", 3) == pytest.approx(1 / 3)


def test_risk_level_thresholds():
    assert g.risk_level(3.0) == "High"
    assert g.risk_level(2.5) == "High"
    assert g.risk_level(2.49) == "Medium"
    assert g.risk_level(1.2) == "Medium"
    assert g.risk_level(1.19) == "Low"
    assert g.risk_level(0.0) == "Low"


def test_overall_risk_is_max_across_affected(G):
    result = g.analyse(G, "auth_api")
    # high-criticality consumers sit at hop 1 -> 3.0
    assert result["risk"]["score"] == pytest.approx(3.0)
    assert result["risk"]["level"] == "High"
    assert "criticality" in result["risk"]["formula"]


def test_leaf_analysis_is_low_risk(G):
    result = g.analyse(G, "mobile_tests")
    assert result["affected"] == []
    assert result["risk"]["level"] == "Low"


# --- the deliberate gap ----------------------------------------------------

def test_gap_component_has_no_owner_and_no_tests(G):
    """notification_service exists so Unknowns has something real to say."""
    assert not G.nodes["notification_service"].get("owner")
    tested = [t for t in g.consumers(G, "notification_service")
              if G.nodes[t].get("type") == "test"]
    assert tested == []
    gaps = g.analyse(G, "auth_api")["gaps"]
    assert any(x["id"] == "notification_service" for x in gaps)


# --- loading ---------------------------------------------------------------

def test_every_edge_carries_evidence(G):
    for u, v, data in G.edges(data=True):
        assert data.get("relationship")
        assert data.get("confidence") in {"documented", "inferred", "unknown"}
        if data["confidence"] == "documented":
            assert data["evidence"]["doc"]
            assert data["evidence"]["snippet"]


def test_graph_shape_matches_the_spec(G):
    assert G.number_of_nodes() >= 15
    assert any(not list(G.successors(n)) for n in G)          # a leaf exists
    assert nx.dag_longest_path_length(nx.DiGraph(
        [(u, v) for u, v in G.edges if not G.has_edge(v, u)])) >= 3
