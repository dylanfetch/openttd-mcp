"""Shared parsing and aggregation helpers for network verifiers.

Verifier-specific modules own their emitted fields and acceptance policy.  This
module owns the common mechanics around those policies: typed pipe-delimited
record parsing, log collection, route-signature deduplication, and construction
of the common network result.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

FieldParser: TypeAlias = Callable[[str], object] | Literal["bool"]
FieldSpec: TypeAlias = tuple[str, FieldParser]
RecordParser: TypeAlias = Callable[[str], dict | None]
RouteEvaluator: TypeAlias = Callable[[dict], dict]
RouteSignature: TypeAlias = tuple[object, ...]
RouteSignatureFactory: TypeAlias = Callable[[dict], RouteSignature]
DerivedResultAdditions: TypeAlias = Callable[
    [list[dict], RouteSignatureFactory], Mapping[str, object]
]


def parse_bool(text: str) -> bool:
    """Parse the lowercase boolean spelling emitted by Squirrel."""
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"Expected boolean string, got {text!r}")


def _parse_record_line(
    line: str,
    *,
    prefix: str,
    record_kind: str,
    fields: Sequence[FieldSpec],
) -> dict | None:
    marker = f"{prefix}|{record_kind}|"
    if marker not in line:
        return None

    payload = line.split(f"{prefix}|", 1)[1].strip()
    parts = payload.split("|")
    if parts[0] != record_kind or len(parts) != len(fields) + 1:
        raise RuntimeError(
            f"Unexpected {record_kind} payload ({len(parts)} parts): {payload!r}"
        )

    record: dict[str, object] = {}
    for (name, parser), raw in zip(fields, parts[1:]):
        record[name] = parse_bool(raw) if parser == "bool" else parser(raw)
    return record


@dataclass(frozen=True)
class NetworkLogSchema:
    """Describe the typed records emitted by one network verifier."""

    prefix: str
    route_fields: Sequence[FieldSpec]
    summary_fields: Sequence[FieldSpec]

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_fields", tuple(self.route_fields))
        object.__setattr__(self, "summary_fields", tuple(self.summary_fields))

    def parse_route_line(self, line: str) -> dict | None:
        """Parse a route record, returning ``None`` for an unrelated line."""
        return _parse_record_line(
            line,
            prefix=self.prefix,
            record_kind="route",
            fields=self.route_fields,
        )

    def parse_summary_line(self, line: str) -> dict | None:
        """Parse a summary record, returning ``None`` for an unrelated line."""
        return _parse_record_line(
            line,
            prefix=self.prefix,
            record_kind="summary",
            fields=self.summary_fields,
        )

    def parse_log(
        self,
        log_path: Path,
        *,
        parse_route_line: RecordParser,
        parse_summary_line: RecordParser,
    ) -> tuple[list[dict], dict | None]:
        """Collect records through the caller's public parsers and sort routes."""
        routes: list[dict] = []
        summary: dict | None = None
        summary_marker = f"{self.prefix}|summary|"
        route_marker = f"{self.prefix}|route|"
        with log_path.open() as source:
            for line in source:
                if summary_marker in line:
                    summary = parse_summary_line(line)
                elif route_marker in line:
                    route = parse_route_line(line)
                    if route is not None:
                        routes.append(route)
        routes.sort(key=lambda route: route["route_index"])
        return routes, summary


def route_signature(route: Mapping[str, object]) -> RouteSignature:
    """Return the order-independent station-tile signature for a route."""
    station_a_tile = route["station_a_tile"]
    station_b_tile = route["station_b_tile"]
    if not isinstance(station_a_tile, int) or not isinstance(station_b_tile, int):
        raise TypeError("Route station tiles must be integers")
    return tuple(sorted((station_a_tile, station_b_tile)))


_SHARED_RESULT_FIELDS = {
    "min_routes_required",
    "valid_route_count",
    "passing_route_count",
    "distinct_passing_route_count",
    "passing_route_signatures",
    "network_ok",
    "routes",
    "summary",
}


def _reject_result_collisions(
    additions: Mapping[str, object],
    occupied_fields: set[str],
) -> None:
    conflicts = occupied_fields & additions.keys()
    if conflicts:
        joined = ", ".join(sorted(conflicts))
        raise ValueError(f"Result additions override shared fields: {joined}")


def aggregate_network(
    routes: Sequence[dict],
    summary: dict | None,
    *,
    evaluator: RouteEvaluator,
    route_signature: RouteSignatureFactory,
    min_routes: int = 2,
    configuration_metadata: Mapping[str, object] | None = None,
    derived_additions: DerivedResultAdditions | None = None,
) -> dict:
    """Evaluate routes and construct the shared distinct-route network verdict.

    Configuration metadata is inserted immediately after the common minimum
    route requirement. Derived additions are inserted after passing-route facts
    and before the final verdict and evaluated routes.
    """
    evaluated = [evaluator(route) for route in routes]
    passing = [route for route in evaluated if route["route_ok"]]
    passing_signatures = sorted({route_signature(route) for route in passing})
    metadata = configuration_metadata or {}
    additions = (
        derived_additions(evaluated, route_signature)
        if derived_additions is not None
        else {}
    )

    _reject_result_collisions(metadata, _SHARED_RESULT_FIELDS)
    _reject_result_collisions(
        additions,
        _SHARED_RESULT_FIELDS | set(metadata),
    )
    result: dict[str, object] = {
        "min_routes_required": min_routes,
    }
    result.update(metadata)
    result.update(
        {
            "valid_route_count": len(evaluated),
            "passing_route_count": len(passing),
            "distinct_passing_route_count": len(passing_signatures),
            "passing_route_signatures": [
                list(signature) for signature in passing_signatures
            ],
        }
    )
    result.update(additions)
    result["network_ok"] = len(passing_signatures) >= min_routes
    result["routes"] = evaluated
    if summary is not None:
        result["summary"] = summary
    return result
