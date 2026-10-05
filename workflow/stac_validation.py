import json
from dataclasses import dataclass, field
from difflib import get_close_matches

import jsonschema
import requests
from jsonschema.exceptions import relevance
from shapely.geometry import shape


SCHEMA_TIMEOUT = 30
_SCHEMA_CACHE = {}


@dataclass
class ValidationIssue:
    schema_url: str
    path: str
    message: str
    validator: str | None = None
    rejected_value: str | None = None
    suggested_value: str | None = None

    def concise(self):
        location = self.path or "<item>"
        text = f"{location}: {self.message}"
        if self.suggested_value:
            text += f" Likely accepted value: {self.suggested_value!r}."
        return text


@dataclass
class LocalValidationResult:
    valid: bool | None
    issues: list[ValidationIssue] = field(default_factory=list)
    error: str | None = None

    @property
    def first_issue(self):
        return self.issues[0] if self.issues else None

    @property
    def summary(self):
        if self.valid is True:
            return "Local STAC validation passed."
        if self.first_issue:
            return f"Local STAC validation failed: {self.first_issue.concise()}"
        if self.error:
            return f"Local STAC validation unavailable: {self.error}"
        return "Local STAC validation produced no result."


def _load_schema(schema_url):
    cached = _SCHEMA_CACHE.get(schema_url)
    if cached is not None:
        return cached

    response = requests.get(schema_url, timeout=SCHEMA_TIMEOUT)
    response.raise_for_status()
    schema = response.json()
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    _SCHEMA_CACHE[schema_url] = schema
    return schema


def _json_path(parts):
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _suggestion(error):
    if error.validator != "enum" or not isinstance(error.instance, str):
        return None
    choices = error.validator_value
    if not isinstance(choices, list):
        return None
    matches = get_close_matches(error.instance, choices, n=1, cutoff=0.75)
    return matches[0] if matches else None


STAC_ITEM_FIELDS = {
    "assets",
    "bbox",
    "collection",
    "geometry",
    "id",
    "links",
    "properties",
    "stac_extensions",
    "stac_version",
    "type",
}


def _resolve_local_ref(root_schema, reference):
    if not reference.startswith("#/"):
        return None
    current = root_schema
    for part in reference[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


def _schema_contract(schema, root_schema=None, seen=None):
    """Return field names governed at the schema's instance root."""
    root_schema = root_schema or schema
    seen = seen or set()
    marker = id(schema)
    if marker in seen:
        return set()
    seen.add(marker)

    names = set(schema.get("required", []))
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        names.update(properties)

    reference = schema.get("$ref")
    if isinstance(reference, str):
        resolved = _resolve_local_ref(root_schema, reference)
        if resolved is not None:
            names.update(_schema_contract(resolved, root_schema, seen))

    for keyword in ("allOf", "anyOf", "oneOf"):
        for subschema in schema.get(keyword, []):
            if isinstance(subschema, dict):
                names.update(_schema_contract(subschema, root_schema, seen))
    return names


def _schema_instance(item, schema):
    """Choose whether a declared extension governs the Item or properties."""
    contract = _schema_contract(schema)
    if contract & STAC_ITEM_FIELDS:
        return item, ()

    item_fields = set(item)
    if contract and not contract & item_fields:
        properties = item.get("properties")
        if isinstance(properties, dict):
            return properties, ("properties",)

    return item, ()


def _schema_issues(item, schema_url, schema):
    instance, path_prefix = _schema_instance(item, schema)
    validator_class = jsonschema.validators.validator_for(schema)
    validator = validator_class(schema)
    errors = sorted(validator.iter_errors(instance), key=relevance)
    issues = []
    for error in errors:
        cause = error.context[0] if error.context else error
        rejected = cause.instance
        if isinstance(rejected, (dict, list)):
            rejected = json.dumps(rejected, sort_keys=True)
        elif rejected is not None:
            rejected = str(rejected)
        issues.append(ValidationIssue(
            schema_url=schema_url,
            path=_json_path((*path_prefix, *cause.absolute_path)),
            message=cause.message,
            validator=cause.validator,
            rejected_value=rejected,
            suggested_value=_suggestion(cause),
        ))
    return issues


def _validate_stac_structure(item):
    try:
        from stac_pydantic.item import Item
    except ImportError as exc:
        raise RuntimeError(
            "stac-pydantic is not installed; reinstall Pubflow dependencies"
        ) from exc

    Item.model_validate(item)
    geometry = shape(item["geometry"])
    if not geometry.is_valid:
        raise ValueError("geometry is invalid")

    minx, miny, maxx, maxy = item["bbox"][:4]
    if not (
        -180.0 <= minx <= 180.0
        and -180.0 <= maxx <= 180.0
        and -90.0 <= miny <= 90.0
        and -90.0 <= maxy <= 90.0
    ):
        raise ValueError(f"bbox is outside WGS84 bounds: {item['bbox']}")


def _structure_issues(exc):
    if hasattr(exc, "errors"):
        issues = []
        for error in exc.errors():
            rejected = error.get("input")
            if isinstance(rejected, (dict, list)):
                rejected = json.dumps(rejected, sort_keys=True)
            elif rejected is not None:
                rejected = str(rejected)
            issues.append(ValidationIssue(
                schema_url="stac-core",
                path=_json_path(error.get("loc", ())),
                message=error.get("msg", str(exc)),
                validator=error.get("type"),
                rejected_value=rejected,
            ))
        if issues:
            return issues
    return [ValidationIssue(
        schema_url="stac-core",
        path="$",
        message=f"{type(exc).__name__}: {exc}",
    )]


def validate_stac_item_file(path):
    """Validate a generated STAC Item without contacting the transaction API."""
    try:
        with open(path) as stream:
            item = json.load(stream)
    except Exception as exc:
        return LocalValidationResult(
            valid=False,
            issues=[ValidationIssue(
                schema_url="stac-item-json",
                path="$",
                message=f"{type(exc).__name__}: {exc}",
            )],
        )

    try:
        _validate_stac_structure(item)
    except RuntimeError as exc:
        return LocalValidationResult(valid=None, error=str(exc))
    except Exception as exc:
        return LocalValidationResult(
            valid=False,
            issues=_structure_issues(exc),
        )

    extensions = item.get("stac_extensions", [])
    if not isinstance(extensions, list) or not extensions:
        return LocalValidationResult(
            valid=False,
            issues=[ValidationIssue(
                schema_url="stac-core",
                path="$.stac_extensions",
                message="must contain at least one schema URL",
            )],
        )

    issues = []
    schema_error = None
    for schema_url in extensions:
        try:
            schema = _load_schema(schema_url)
            issues.extend(_schema_issues(item, schema_url, schema))
        except Exception as exc:
            schema_error = f"{schema_url}: {type(exc).__name__}: {exc}"
            break

    if issues:
        return LocalValidationResult(
            valid=False,
            issues=issues,
            error=schema_error,
        )
    if schema_error:
        return LocalValidationResult(valid=None, error=schema_error)
    return LocalValidationResult(valid=True)
