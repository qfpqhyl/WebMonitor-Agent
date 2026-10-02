"""Deterministic CSS extraction; missing optional values remain absent."""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from webmonitor.api.errors import DomainError
from webmonitor.schemas.monitors import DraftSpec, FieldSpec, Normalization
from webmonitor.security.egress import validate_url

# Shared symbols are interpreted only through the explicit currency contract.
_CURRENCY_SYMBOLS = {
    '$': frozenset(('USD', 'CAD', 'AUD', 'NZD', 'SGD', 'HKD', 'MXN', 'ARS', 'CLP', 'COP')),
    '€': frozenset(('EUR',)), '£': frozenset(('GBP',)),
    '¥': frozenset(('JPY', 'CNY')), '₹': frozenset(('INR',)),
    '₩': frozenset(('KRW',)), '₽': frozenset(('RUB',)),
}
_MISSING = object()


def invalid(field: str, reason: str) -> None:
    raise DomainError('collection_invalid', 422, details={'field': field, 'reason': reason})


def normalize_text(value: str, normal: Normalization) -> str:
    if normal.trim:
        value = value.strip()
    if normal.collapse_whitespace:
        value = ' '.join(value.split())
    return value


def _decimal(value: str, field: FieldSpec, path: str) -> str:
    normal = field.normalize
    decimal = re.escape(normal.decimal_separator)
    thousands = normal.thousands_separator
    integer = r'[0-9]+'
    if thousands is not None:
        separator = re.escape(thousands)
        integer = rf'(?:[0-9]+|[0-9]{{1,3}}(?:{separator}[0-9]{{3}})+)'
    # No exponent, guessing, NaN, trailing junk, or malformed grouping.
    if not re.fullmatch(rf'[+-]?{integer}(?:{decimal}[0-9]+)?', value):
        invalid(path, 'invalid_number')
    if thousands is not None:
        value = value.replace(thousands, '')
    value = value.replace(normal.decimal_separator, '.')
    try:
        number = Decimal(value)
    except InvalidOperation:
        invalid(path, 'invalid_number')
    if not number.is_finite():
        invalid(path, 'invalid_number')
    return format(number, 'f')


def _money(value: str, field: FieldSpec, path: str) -> dict:
    currency = field.normalize.currency
    # A currency marker is optional for numeric attributes; if visible it must
    # agree, not be discarded as generic nonnumeric decoration.
    marker = re.match(r'^([A-Za-z]{3}|[^0-9+\-.,\s])\s*', value)
    suffix = re.search(r'\s*([A-Za-z]{3}|[^0-9.,\s])$', value)
    if marker:
        token = marker.group(1)
        value = value[marker.end():]
    elif suffix:
        token = suffix.group(1)
        value = value[:suffix.start()]
    else:
        token = None
    if token is not None and token != currency and currency not in _CURRENCY_SYMBOLS.get(token, ()):
        invalid(path, 'currency_mismatch')
    return {'amount': _decimal(value, field, path), 'currency': currency}


def _date(value: str, field: FieldSpec, path: str) -> str:
    normal = field.normalize
    try:
        parsed = datetime.strptime(value, normal.date_format)
        if parsed.tzinfo is None:
            zone = ZoneInfo(normal.timezone)
            # Do not guess at nonexistent or repeated local times around DST.
            candidates = set()
            for fold in (0, 1):
                local = parsed.replace(tzinfo=zone, fold=fold)
                utc = local.astimezone(timezone.utc)
                if utc.astimezone(zone).replace(tzinfo=None) == parsed:
                    candidates.add(utc)
            if len(candidates) != 1:
                invalid(path, 'ambiguous_or_nonexistent_date')
            parsed = candidates.pop()
        return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except (ValueError, OverflowError):
        invalid(path, 'invalid_date')


def _scalar(value: str, field: FieldSpec, final_url: str, path: str):
    value = normalize_text(value, field.normalize)
    if value == '' and field.required:
        invalid(path, 'required_empty')
    if field.type == 'text':
        return value
    if field.type == 'number':
        return _decimal(value, field, path)
    if field.type == 'money':
        return _money(value, field, path)
    if field.type == 'date':
        return _date(value, field, path)
    if field.type == 'boolean':
        if value in field.normalize.true_values:
            return True
        if value in field.normalize.false_values:
            return False
        invalid(path, 'invalid_boolean')
    if field.type == 'url':
        if not value:
            invalid(path, 'invalid_url')
        return validate_url(urljoin(final_url, value))
    invalid(path, 'unsupported_type')


def _extract(root: BeautifulSoup | Tag, field: FieldSpec, final_url: str, path: str):
    # A row's own attributes/text are selectable without escaping its scope.
    matches = [root] if field.selector == ':scope' and isinstance(root, Tag) and not isinstance(root, BeautifulSoup) else root.select(field.selector)
    if field.type == 'list':
        rows = []
        for index, row in enumerate(matches):
            values = {}
            for item in field.item_fields:
                value = _extract(row, item, final_url, f'{path}[{index}].{item.name}')
                if value is not _MISSING:
                    values[item.name] = value
            rows.append(values)
        return rows
    if len(matches) > 1:
        invalid(path, 'multiple_matches')
    if not matches:
        if field.required:
            invalid(path, 'required_missing')
        return _MISSING
    element = matches[0]
    if field.attribute is None:
        value = element.get_text(separator='')
    else:
        value = element.get(field.attribute, _MISSING)
        if value is _MISSING:
            if field.required:
                invalid(path, 'required_missing')
            return _MISSING
        if isinstance(value, list):
            value = ' '.join(value)
    return _scalar(value, field, final_url, path)


def extract_fields(html: str, spec: DraftSpec, final_url: str) -> dict:
    """Extract JSON-safe values; validate merged rows separately before commit."""

    root = BeautifulSoup(html, 'lxml')
    fields = {}
    for field in spec.fields:
        value = _extract(root, field, final_url, field.name)
        if value is not _MISSING:
            fields[field.name] = value
    return fields
