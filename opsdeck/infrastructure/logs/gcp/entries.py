"""Parsing of Cloud Logging entries in their JSON wire format.

The API client's `entry.to_api_repr()` returns the same shape that
`gcloud logging read --format=json` prints, so the command the UI offers
for copy/paste prints what the UI shows.
"""

import json

from opsdeck.domain.logs import LogEntry

_SUMMARY_KEYS = ("message", "msg", "event", "error", "description")
_MAX_SUMMARY_LENGTH = 300


def parse_entry(item: dict) -> LogEntry:
    payload: dict | str | None
    if "textPayload" in item:
        payload = item["textPayload"]
    elif "jsonPayload" in item:
        payload = item["jsonPayload"]
    elif "protoPayload" in item:
        payload = item["protoPayload"]
    else:
        payload = None
    resource = item.get("resource") or {}
    operation = item.get("operation") or {}
    return LogEntry(
        timestamp=item.get("timestamp", ""),
        severity=item.get("severity", "DEFAULT"),
        summary=summarize(payload, item.get("httpRequest")),
        resource_type=resource.get("type", ""),
        resource_labels=resource.get("labels") or {},
        payload=payload,
        log_name=item.get("logName", ""),
        insert_id=item.get("insertId", ""),
        trace=item.get("trace") or "",
        span_id=item.get("spanId") or "",
        operation_id=operation.get("id") or "",
        fields=item,
    )


def summarize(payload: dict | str | None, http_request: dict | None = None) -> str:
    """One-line summary of an entry: the message when there is one, else the
    request line (method, path, status, latency) for request logs, else the
    serialized payload."""
    if payload is None:
        return request_line(http_request) or "(no payload)"
    if isinstance(payload, str):
        text = payload.strip()
    else:
        text = ""
        for key in _SUMMARY_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text and str(payload.get("@type", "")).endswith("RequestLog"):
            # App Engine request log: protoPayload carries the request itself
            text = request_line(
                {
                    "requestMethod": payload.get("method"),
                    "requestUrl": payload.get("resource"),
                    "status": payload.get("status"),
                    "latency": payload.get("latency"),
                }
            )
        if not text:
            text = request_line(http_request)
        if not text:
            text = json.dumps(payload, ensure_ascii=False)
    first_line = text.splitlines()[0] if text else "(empty)"
    if len(first_line) > _MAX_SUMMARY_LENGTH:
        first_line = first_line[: _MAX_SUMMARY_LENGTH - 1] + "…"
    return first_line


def request_line(http_request: dict | None) -> str:
    """'POST /api/x → 422 (43 ms)' from an httpRequest-shaped dict, or ''."""
    if not http_request:
        return ""
    method = http_request.get("requestMethod") or ""
    url = http_request.get("requestUrl") or ""
    status = http_request.get("status")
    head = " ".join(part for part in (method, url) if part)
    if not head and status is None:
        return ""
    line = head
    if status is not None:
        line = f"{line} → {status}" if line else f"→ {status}"
    latency = format_latency(http_request.get("latency"))
    if latency:
        line = f"{line} ({latency})"
    return line


def format_latency(latency) -> str:
    """Cloud Logging latency ('0.043460s' or {'seconds': 1, 'nanos': ...}) → '43 ms' / '1.2 s'."""
    seconds: float | None = None
    if isinstance(latency, str) and latency.endswith("s"):
        try:
            seconds = float(latency[:-1])
        except ValueError:
            seconds = None
    elif isinstance(latency, dict):
        try:
            seconds = float(latency.get("seconds", 0)) + float(latency.get("nanos", 0)) / 1e9
        except (TypeError, ValueError):
            seconds = None
    if seconds is None:
        return str(latency) if latency else ""
    if seconds < 1:
        return f"{round(seconds * 1000)} ms"
    return f"{seconds:.1f} s"
