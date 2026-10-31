"""Parsing tests for the Cloud Logging entry normalization."""

from app.infrastructure.logs.gcp.entries import format_latency, parse_entry


def test_parse_text_payload():
    entry = parse_entry(
        {
            "timestamp": "2026-06-12T10:00:00Z",
            "severity": "WARNING",
            "textPayload": "disk almost full\nsecond line",
            "resource": {"type": "gce_instance", "labels": {"zone": "us-east1-b"}},
            "logName": "projects/p/logs/syslog",
            "insertId": "abc123",
        }
    )
    assert entry.severity == "WARNING"
    assert entry.summary == "disk almost full"
    assert entry.resource_type == "gce_instance"
    assert entry.resource_labels == {"zone": "us-east1-b"}
    assert entry.payload == "disk almost full\nsecond line"


def test_parse_entry_keeps_full_raw_record_for_formatting():
    raw = {
        "timestamp": "2026-06-22T10:33:28Z",
        "severity": "INFO",
        "textPayload": "Final log of request to /dashboard",
        "httpRequest": {"requestMethod": "GET", "status": 200},
        "labels": {"requestUserEmail": "erika@example.com"},
        "trace": "projects/p/traces/abc",
        "resource": {"type": "gae_app", "labels": {"version_id": "main-5037"}},
    }
    entry = parse_entry(raw)
    # the normalized fields stay, and the complete record is carried through
    assert entry.fields == raw
    assert entry.fields["httpRequest"]["status"] == 200
    assert entry.fields["labels"]["requestUserEmail"] == "erika@example.com"
    assert entry.trace == "projects/p/traces/abc"


def test_parse_entry_exposes_trace_span_and_operation():
    entry = parse_entry(
        {
            "trace": "projects/p/traces/0123abcd",
            "spanId": "span-9",
            "operation": {"id": "op-42", "producer": "worker"},
        }
    )
    assert entry.trace == "projects/p/traces/0123abcd"
    assert entry.span_id == "span-9"
    assert entry.operation_id == "op-42"


def test_parse_entry_defaults_correlation_fields_to_empty():
    entry = parse_entry({"severity": "INFO", "operation": None})
    assert entry.trace == ""
    assert entry.span_id == ""
    assert entry.operation_id == ""


def test_parse_json_payload_prefers_message_key():
    entry = parse_entry({"jsonPayload": {"message": "request failed", "code": 500}})
    assert entry.summary == "request failed"
    assert entry.severity == "DEFAULT"
    assert entry.payload == {"message": "request failed", "code": 500}


def test_parse_entry_without_payload():
    entry = parse_entry({"severity": "INFO"})
    assert entry.summary == "(no payload)"
    assert entry.payload is None


def test_parse_entry_without_payload_summarizes_http_request():
    entry = parse_entry(
        {
            "httpRequest": {
                "requestMethod": "GET",
                "requestUrl": "https://svc.run.app/health",
                "status": 200,
                "latency": "0.004321s",
            }
        }
    )
    assert entry.summary == "GET https://svc.run.app/health → 200 (4 ms)"
    assert entry.payload is None


def test_parse_app_engine_request_log_summarizes_request_line():
    entry = parse_entry(
        {
            "protoPayload": {
                "@type": "type.googleapis.com/google.appengine.logging.v1.RequestLog",
                "method": "POST",
                "resource": "/api/workflow",
                "status": 422,
                "latency": "1.250000s",
                "host": "pipes.example.com",
            },
            "httpRequest": {"status": 422},
        }
    )
    assert entry.summary == "POST /api/workflow → 422 (1.2 s)"


def test_summarize_prefers_message_over_request_line():
    entry = parse_entry(
        {
            "jsonPayload": {"message": "done"},
            "httpRequest": {"requestMethod": "GET", "requestUrl": "/x", "status": 200},
        }
    )
    assert entry.summary == "done"


def test_format_latency_variants():
    assert format_latency("0.043460s") == "43 ms"
    assert format_latency("2.5s") == "2.5 s"
    assert format_latency({"seconds": 1, "nanos": 500000000}) == "1.5 s"
    assert format_latency(None) == ""
    assert format_latency("weird") == "weird"
