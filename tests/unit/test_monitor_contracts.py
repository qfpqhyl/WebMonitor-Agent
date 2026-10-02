"""Reject ambiguous extraction contracts before any preview or approval."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from webmonitor.schemas.monitors import FieldSpec, Normalization, canonical_json, content_hash


def field(kind, **options):
    return FieldSpec.model_validate({'name': 'value', 'type': kind, 'semantic': 'Extracted value',
                                    'selector': '.value', **options})


@pytest.mark.parametrize('kind,normalization', [
    ('number', {}), ('money', {'decimal_separator': '.'}),
    ('date', {'date_format': '%Y-%m-%d'}),
    ('boolean', {'true_values': ['yes'], 'false_values': []}),
    ('text', {'currency': 'USD'}),
])
def test_typed_fields_reject_missing_or_inapplicable_parse_configuration(kind, normalization):
    with pytest.raises(ValidationError):
        field(kind, normalize=normalization)


def test_ambiguous_decimal_and_boolean_maps_rejected():
    with pytest.raises(ValidationError):
        Normalization(decimal_separator=',', thousands_separator=',')
    with pytest.raises(ValidationError):
        Normalization(true_values=[' yes '], false_values=['yes'])
    with pytest.raises(ValidationError):
        Normalization(true_values=['a  b'], false_values=['a b'], collapse_whitespace=True)


@pytest.mark.parametrize('selector', ['//span', 'xpath=//span', 'javascript:alert(1)', 'div[', 'div::before'])
def test_non_css_or_non_element_selectors_are_rejected(selector):
    with pytest.raises(ValidationError):
        field('text', selector=selector)


def test_unknown_code_and_scalar_list_options_rejected():
    with pytest.raises(ValidationError):
        field('text', expression='document.cookie')
    with pytest.raises(ValidationError):
        field('text', min_items=1)
    with pytest.raises(ValidationError):
        field('list', item_fields=[{'name': 'id', 'type': 'text', 'semantic': 'ID', 'selector': '.id'}],
              min_items=2, max_items=1)


def test_canonical_hash_normalizes_timezone_and_object_order():
    utc = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    offset = utc.astimezone(timezone(timedelta(hours=8)))
    first = {'at': utc, 'value': Decimal('12.50')}
    second = {'value': Decimal('12.50'), 'at': offset}
    assert content_hash(first) == content_hash(second)
    assert json.loads(canonical_json(first)) == {'at': '2026-10-02T12:00:00Z', 'value': '12.50'}
    assert content_hash(first) != content_hash({'at': utc, 'value': Decimal('13.50')})
    with pytest.raises(ValueError):
        canonical_json({'at': utc.replace(tzinfo=None)})
