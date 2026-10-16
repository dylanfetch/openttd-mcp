from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openttd_mcp.verify._network import (
    NetworkLogSchema,
    aggregate_network,
    parse_bool,
    route_signature,
)

if TYPE_CHECKING:
    from pathlib import Path


SCHEMA = NetworkLogSchema(
    prefix="TEST_NET",
    route_fields=(
        ("route_index", int),
        ("station_a_tile", int),
        ("station_b_tile", int),
        ("operating", "bool"),
        ("label", str),
    ),
    summary_fields=(
        ("valid_route_count", int),
        ("station_count", int),
        ("network_name", str),
    ),
)


def test_parse_bool_accepts_only_squirrel_spellings() -> None:
    assert parse_bool("true") is True
    assert parse_bool("false") is False
    with pytest.raises(ValueError, match="Expected boolean string"):
        parse_bool("True")


def test_schema_parses_prefix_and_field_driven_records() -> None:
    route = SCHEMA.parse_route_line("dbg: TEST_NET|route|3|101|202|true|mixed cargo\n")
    assert route == {
        "route_index": 3,
        "station_a_tile": 101,
        "station_b_tile": 202,
        "operating": True,
        "label": "mixed cargo",
    }
    assert SCHEMA.parse_route_line("TEST_NET|summary|1|2|small") is None
    assert SCHEMA.parse_summary_line("other output") is None
    assert SCHEMA.parse_summary_line("TEST_NET|summary|1|2|small") == {
        "valid_route_count": 1,
        "station_count": 2,
        "network_name": "small",
    }


def test_schema_rejects_wrong_record_shapes() -> None:
    with pytest.raises(
        RuntimeError,
        match="Unexpected route payload",
    ):
        SCHEMA.parse_route_line("TEST_NET|route|0|1|2")
    with pytest.raises(
        RuntimeError,
        match="Unexpected summary payload",
    ):
        SCHEMA.parse_summary_line("TEST_NET|summary|1|2")


def test_schema_freezes_field_specs_passed_as_mutable_sequences() -> None:
    route_fields = [("route_index", int)]
    summary_fields = [("valid_route_count", int)]
    schema = NetworkLogSchema(
        prefix="IMMUTABLE",
        route_fields=route_fields,
        summary_fields=summary_fields,
    )

    route_fields.append(("later", str))
    summary_fields.append(("later", str))

    assert schema.route_fields == (("route_index", int),)
    assert schema.summary_fields == (("valid_route_count", int),)


def test_schema_collects_sorted_routes_and_last_summary(tmp_path: Path) -> None:
    log_path = tmp_path / "verify.log"
    log_path.write_text(
        "TEST_NET|summary|1|2|early\n"
        "TEST_NET|route|2|30|40|false|second\n"
        "noise\n"
        "TEST_NET|route|1|10|20|true|first\n"
        "TEST_NET|summary|2|4|final"
    )

    routes, summary = SCHEMA.parse_log(
        log_path,
        parse_route_line=SCHEMA.parse_route_line,
        parse_summary_line=SCHEMA.parse_summary_line,
    )

    assert [route["route_index"] for route in routes] == [1, 2]
    assert summary == {
        "valid_route_count": 2,
        "station_count": 4,
        "network_name": "final",
    }


def test_route_signature_is_order_independent() -> None:
    route = {"station_a_tile": 20, "station_b_tile": 10, "vehicle_id": 7}
    assert route_signature(route) == (10, 20)


def test_aggregate_network_deduplicates_and_supports_fixed_additions() -> None:
    def evaluate(route: dict) -> dict:
        return {**route, "route_ok": route["operating"]}

    routes = [
        {"station_a_tile": 20, "station_b_tile": 10, "operating": True},
        {"station_a_tile": 10, "station_b_tile": 20, "operating": True},
        {"station_a_tile": 30, "station_b_tile": 40, "operating": False},
    ]

    result = aggregate_network(
        routes,
        {"valid_route_count": 3},
        evaluator=evaluate,
        route_signature=route_signature,
        min_routes=2,
        configuration_metadata={"min_component_tiles": 10},
    )

    assert result["passing_route_count"] == 2
    assert result["distinct_passing_route_count"] == 1
    assert result["passing_route_signatures"] == [[10, 20]]
    assert result["network_ok"] is False
    assert result["min_component_tiles"] == 10
    assert result["summary"] == {"valid_route_count": 3}


def test_aggregate_network_supports_evaluator_specific_derived_additions() -> None:
    def evaluate(route: dict) -> dict:
        return {
            **route,
            "route_ok": route["operating"],
            "delivered": route["operating"] and route["load_dropped"],
        }

    def delivery_additions(
        evaluated_routes: list[dict],
        signature,
    ) -> dict[str, object]:
        delivered = [route for route in evaluated_routes if route["delivered"]]
        signatures = sorted({signature(route) for route in delivered})
        return {
            "delivered_route_count": len(delivered),
            "distinct_delivered_route_count": len(signatures),
            "delivered_route_signatures": [list(signature) for signature in signatures],
        }

    result = aggregate_network(
        [
            {
                "station_a_tile": 10,
                "station_b_tile": 20,
                "operating": True,
                "load_dropped": True,
            },
            {
                "station_a_tile": 30,
                "station_b_tile": 40,
                "operating": True,
                "load_dropped": False,
            },
        ],
        None,
        evaluator=evaluate,
        route_signature=route_signature,
        derived_additions=delivery_additions,
    )

    assert result["network_ok"] is True
    assert result["delivered_route_count"] == 1
    assert result["distinct_delivered_route_count"] == 1
    assert result["delivered_route_signatures"] == [[10, 20]]
    assert "summary" not in result


def test_aggregate_network_prevents_additions_from_overriding_shared_fields() -> None:
    with pytest.raises(
        ValueError,
        match=r"override.*network_ok",
    ):
        aggregate_network(
            [],
            None,
            evaluator=lambda route: route,
            route_signature=route_signature,
            configuration_metadata={"network_ok": True},
        )


def test_aggregate_network_rejects_summary_collision_when_summary_absent() -> None:
    with pytest.raises(
        ValueError,
        match=r"override.*summary",
    ):
        aggregate_network(
            [],
            None,
            evaluator=lambda route: route,
            route_signature=route_signature,
            configuration_metadata={"summary": "hidden"},
        )


def test_aggregate_network_rejects_derived_summary_when_summary_absent() -> None:
    with pytest.raises(
        ValueError,
        match=r"override.*summary",
    ):
        aggregate_network(
            [],
            None,
            evaluator=lambda route: route,
            route_signature=route_signature,
            derived_additions=lambda routes, signature: {"summary": "hidden"},
        )


def test_aggregate_network_builds_config_and_derived_fields_in_declared_order() -> None:
    def derive(evaluated_routes: list[dict], signature) -> dict[str, object]:
        assert evaluated_routes == []
        assert signature is route_signature
        return {"derived_a": 1, "derived_b": 2}

    result = aggregate_network(
        [],
        {"valid_route_count": 0},
        evaluator=lambda route: route,
        route_signature=route_signature,
        configuration_metadata={"config_a": 1, "config_b": 2},
        derived_additions=derive,
    )

    assert tuple(result) == (
        "min_routes_required",
        "config_a",
        "config_b",
        "valid_route_count",
        "passing_route_count",
        "distinct_passing_route_count",
        "passing_route_signatures",
        "derived_a",
        "derived_b",
        "network_ok",
        "routes",
        "summary",
    )
