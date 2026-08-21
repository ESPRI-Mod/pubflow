import ast
import json
import re
from dataclasses import dataclass
from difflib import get_close_matches


ERROR_MARKER = re.compile(
    r"Failed to publish: Error (?P<status>\d+):\s*(?P<body>\{.*\})"
)
SCHEMA_PATTERN = re.compile(r"failed validation against `([^`]+)`")
ENUM_PATTERN = re.compile(
    r"`:\s*(?P<value>.+?) is not one of (?P<choices>\[.*\])$"
)


@dataclass
class ParsedDiagnostic:
    http_status: int | None = None
    error_type: str | None = None
    schema_url: str | None = None
    rejected_value: str | None = None
    suggested_value: str | None = None
    summary: str = "Publisher failed without a structured server diagnostic."
    server_instance: str | None = None


def _parse_enum_failure(detail):
    match = ENUM_PATTERN.search(detail)
    if match is None:
        return None, None

    raw_value = match.group("value")
    try:
        rejected = ast.literal_eval(raw_value)
    except (SyntaxError, ValueError):
        rejected = raw_value.strip("'\"")

    suggested = None
    try:
        choices = ast.literal_eval(match.group("choices"))
        if isinstance(rejected, str) and isinstance(choices, list):
            matches = get_close_matches(rejected, choices, n=1, cutoff=0.75)
            if matches:
                suggested = matches[0]
    except (SyntaxError, ValueError):
        pass

    return str(rejected), suggested


def parse_server_diagnostic(output):
    """Extract and condense the last RFC 9457 response in publisher output."""
    matches = list(ERROR_MARKER.finditer(output or ""))
    if not matches:
        return ParsedDiagnostic()

    match = matches[-1]
    http_status = int(match.group("status"))
    try:
        body = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return ParsedDiagnostic(
            http_status=http_status,
            summary=f"Publisher server returned HTTP {http_status}.",
        )

    detail = str(body.get("detail") or body.get("title") or "")
    schema_match = SCHEMA_PATTERN.search(detail)
    schema_url = schema_match.group(1) if schema_match else None
    rejected, suggested = _parse_enum_failure(detail)
    error_type = body.get("type")

    if rejected and schema_url:
        summary = (
            f"STAC validation failed against {schema_url}: "
            f"{rejected!r} is not an allowed value."
        )
        if suggested:
            summary += f" Likely accepted value: {suggested!r}."
    elif detail:
        summary = detail
        if len(summary) > 1000:
            summary = summary[:997] + "..."
    else:
        summary = f"Publisher server returned HTTP {http_status}."

    return ParsedDiagnostic(
        http_status=body.get("status_code", http_status),
        error_type=error_type,
        schema_url=schema_url,
        rejected_value=rejected,
        suggested_value=suggested,
        summary=summary,
        server_instance=body.get("instance"),
    )
