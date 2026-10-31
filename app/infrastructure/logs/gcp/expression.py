"""The Cloud Logging filter language: refinements spelled as clauses, the
freshness turned into a timestamp clause, and the same query as a
`gcloud logging read` command and a Logs Explorer URL. Pure functions."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from app.domain.errors import LogProviderError
from app.domain.logs import Freshness, LogQuery, Refinement, RefinementValue, SeverityBand, TimeRange

# gcloud duration syntax, e.g. '30m', '1h', '2d', '1d12h'; a bare number
# means days (mirrors `gcloud logging read --freshness`).
_DURATION_TOKEN = re.compile(r"(\d+)([smhdw])", re.IGNORECASE)
_DURATION_FULL = re.compile(r"^(?:\d+[smhdw])+$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

# a path segment that needs no quotes in the filter language
_PLAIN_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# freshness -> ISO-8601 duration of the Logs Explorer's timeRange parameter
_TIME_RANGE = {
    Freshness.M15: "PT15M",
    Freshness.H1: "PT1H",
    Freshness.H6: "PT6H",
    Freshness.D1: "P1D",
    Freshness.D3: "P3D",
    Freshness.D7: "P7D",
}


def _quote(text: str) -> str:
    """A filter-language string literal: quotes and backslashes escaped."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _field(path: tuple[str, ...]) -> str:
    """labels."k8s-pod/app": segments that are not identifiers are quoted."""
    return ".".join(part if _PLAIN_SEGMENT.match(part) else _quote(part) for part in path)


def _literal(value: RefinementValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return _quote(value)


def refinement_clause(refinement: Refinement) -> str:
    operator = "!=" if refinement.negate else "="
    return f"{_field(refinement.path)}{operator}{_literal(refinement.value)}"


# each band as Cloud Logging severities (an ordered enum: DEFAULT < DEBUG < INFO
# < NOTICE < WARNING < ERROR < CRITICAL < ALERT < EMERGENCY)
_BAND_CLAUSES = {
    SeverityBand.ERROR: "severity>=ERROR",
    SeverityBand.WARNING: "severity=WARNING",
    SeverityBand.INFO: "(severity=INFO OR severity=NOTICE)",
    SeverityBand.OTHER: "severity<=DEBUG",
}


def bands_clause(bands: tuple[SeverityBand, ...]) -> str:
    clauses = [_BAND_CLAUSES[band] for band in bands]
    return clauses[0] if len(clauses) == 1 else f"({' OR '.join(clauses)})"


def refined_expression(query: LogQuery) -> str:
    """The saved expression, parenthesized, ANDed with each refinement and
    with the severity bands; never the time range, so it can be saved."""
    clauses = [refinement_clause(item) for item in query.refinements]
    if query.bands:
        clauses.append(bands_clause(query.bands))
    if not clauses:
        return query.expression
    joined = " AND ".join(clauses)
    return f"({query.expression}) AND {joined}" if query.expression else joined


def _and(expression: str, clause: str) -> str:
    return f"({expression}) AND {clause}" if expression else clause


def _instant(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def range_clause(time_range: TimeRange) -> str:
    return f'timestamp>="{_instant(time_range.start)}" AND timestamp<"{_instant(time_range.end)}"'


def gcloud_args(project: str, query: LogQuery) -> list[str]:
    args = ["gcloud", "logging", "read"]
    expression = refined_expression(query)
    if query.time_range:
        expression = _and(expression, range_clause(query.time_range))
    if expression:
        args.append(expression)
    args += [f"--project={project}", f"--limit={query.limit}"]
    if not query.time_range:
        args.append(f"--freshness={query.freshness}")
    args.append("--order=desc")
    return args


def logs_explorer_url(project: str, query: LogQuery) -> str:
    if query.time_range:  # the Explorer takes an explicit range as start/end
        time_range = f"{_instant(query.time_range.start)}/{_instant(query.time_range.end)}"
    else:
        time_range = _TIME_RANGE[query.freshness]
    return (
        "https://console.cloud.google.com/logs/query"
        f";query={quote(refined_expression(query), safe='')}"
        f";timeRange={quote(time_range, safe='')}"
        f"?project={quote(project, safe='')}"
    )


def freshness_to_timedelta(freshness: str) -> timedelta:
    text = (freshness or "").strip()
    if text.isdigit():
        return timedelta(days=int(text))
    if not _DURATION_FULL.match(text):
        raise LogProviderError(f"Invalid freshness: '{freshness}' (e.g. 30m, 1h, 2d)")
    seconds = sum(int(amount) * _UNIT_SECONDS[unit.lower()] for amount, unit in _DURATION_TOKEN.findall(text))
    return timedelta(seconds=seconds)


def api_filter(query: LogQuery, now: datetime) -> str:
    """The filter sent to the API: the refined expression plus the query's
    time range or, without one, a timestamp cutoff from the freshness. gcloud
    only applies --freshness when the filter has no timestamp clause of its
    own; mirror that so saved filters behave the same."""
    expression = refined_expression(query)
    if query.time_range:
        return _and(expression, range_clause(query.time_range))
    if "timestamp" in expression:
        return expression
    cutoff = now - freshness_to_timedelta(query.freshness)
    clause = f'timestamp>="{cutoff.strftime("%Y-%m-%dT%H:%M:%S")}Z"'
    if not expression:
        return clause
    return f"({expression}) AND {clause}"
