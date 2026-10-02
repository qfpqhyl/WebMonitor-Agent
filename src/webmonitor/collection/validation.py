"""Validate snapshots before rules or baseline mutation, without I/O."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from webmonitor.collection.extraction import invalid
from webmonitor.schemas.monitors import Coverage, DraftSpec, FieldSpec, canonical_json
from webmonitor.security.egress import validate_url

_MISSING = object()


def _empty(value) -> bool:
    return value is _MISSING or value is None or value == '' or value == []


def _number(value, path: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?', value):
        invalid(path, 'invalid_number')
    try:
        if not Decimal(value).is_finite():
            invalid(path, 'invalid_number')
    except InvalidOperation:
        invalid(path, 'invalid_number')


def _validate(field: FieldSpec, value, previous, path: str) -> None:
    if value is _MISSING:
        if field.required:
            invalid(path, 'required_missing')
        if not _empty(previous):
            invalid(path, 'historical_value_missing')
        return
    if value is None:
        invalid(path, 'invalid_null')
    if field.type == 'list':
        if not isinstance(value, list):
            invalid(path, 'invalid_list')
        if previous is not _MISSING and not _empty(previous) and not value:
            invalid(path, 'historical_nonempty_to_empty')
        if field.min_items is not None and len(value) < field.min_items:
            invalid(path, 'min_items')
        if field.max_items is not None and len(value) > field.max_items:
            invalid(path, 'max_items')
        for index, row in enumerate(value):
            if not isinstance(row, dict):
                invalid(path, 'invalid_row')
            if set(row) - {item.name for item in field.item_fields}:
                invalid(path, 'unknown_item_field')
            for item in field.item_fields:
                _validate(item, row.get(item.name, _MISSING), _MISSING, f'{path}[{index}].{item.name}')
        return
    if value == '' and field.required:
        invalid(path, 'required_empty')
    if _empty(value) and not _empty(previous):
        invalid(path, 'historical_nonempty_to_empty')
    if field.type == 'text':
        if not isinstance(value, str):
            invalid(path, 'invalid_text')
    elif field.type == 'number':
        _number(value, path)
    elif field.type == 'money':
        if not isinstance(value, dict) or set(value) != {'amount', 'currency'}:
            invalid(path, 'invalid_money')
        if value['currency'] != field.normalize.currency:
            invalid(path, 'currency_mismatch')
        if isinstance(previous, dict) and previous.get('currency') != value['currency']:
            invalid(path, 'currency_changed')
        _number(value['amount'], path)
    elif field.type == 'boolean':
        if type(value) is not bool:
            invalid(path, 'invalid_boolean')
    elif field.type == 'date':
        if not isinstance(value, str) or not value.endswith('Z'):
            invalid(path, 'invalid_date')
        try:
            datetime.fromisoformat(value[:-1] + '+00:00')
        except ValueError:
            invalid(path, 'invalid_date')
    elif field.type == 'url':
        if not isinstance(value, str) or validate_url(value) != value:
            invalid(path, 'invalid_url')


def validate_collection(spec: DraftSpec, fields: dict, previous: dict | None = None,
                        coverage: dict | Coverage | None = None) -> None:
    """Reject untrusted, incomplete or baseline-destructive normalized results.

    Empty initial lists are valid unless min_items forbids them. Missing scalar
    values are never represented by null or an invented empty string.
    """
    if not isinstance(fields, dict):
        invalid('', 'invalid_fields')
    if set(fields) - {field.name for field in spec.fields}:
        invalid('', 'unknown_field')
    actual = spec.coverage if coverage is None else coverage
    if isinstance(actual, dict):
        try:
            actual = Coverage.model_validate(actual)
        except ValueError:
            invalid('', 'invalid_coverage')
    if not isinstance(actual, Coverage):
        invalid('', 'invalid_coverage')
    if actual.scope == 'bounded' and any(rule.type == 'list_removed' for rule in spec.change_rules.rules):
        invalid('', 'deletion_requires_full_coverage')
    if spec.coverage.scope == 'full' and actual.scope != 'full':
        invalid('', 'coverage_incomplete')
    for field in spec.fields:
        _validate(field, fields.get(field.name, _MISSING),
                  previous.get(field.name, _MISSING) if previous is not None else _MISSING, field.name)
    key = spec.business_key
    if key:
        rows = fields.get(key.field, [])
        seen = set()
        historical = {}
        if previous is not None:
            for row in previous.get(key.field, []):
                historical[canonical_json([row[name] for name in key.item_fields])] = row
        list_spec = next(field for field in spec.fields if field.name == key.field)
        for index, row in enumerate(rows):
            if any(_empty(row.get(name, _MISSING)) or
                   (isinstance(row.get(name), str) and not row[name].strip())
                   for name in key.item_fields):
                invalid(key.field, 'empty_business_key')
            identity = canonical_json([row[name] for name in key.item_fields])
            if identity in seen:
                invalid(key.field, 'duplicate_business_key')
            seen.add(identity)
            old = historical.get(identity)
            if old is not None:
                for item in list_spec.item_fields:
                    _validate(item, row.get(item.name, _MISSING), old.get(item.name, _MISSING),
                              f'{key.field}[{index}].{item.name}')
