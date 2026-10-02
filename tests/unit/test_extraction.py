"""Extraction boundaries that protect trustworthy baselines and list identity."""
from uuid import uuid4

import pytest

from webmonitor.api.errors import DomainError
from webmonitor.collection.extraction import extract_fields
from webmonitor.collection.validation import validate_collection
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.schemas.notifications import EVENT_TYPES


def spec_for(field, *, rules=None, key=None, coverage='full'):
    return DraftSpec.model_validate({
        'name': 'Extraction boundary', 'url': 'https://example.com/products',
        'collection_mode': 'http', 'fields': [field], 'business_key': key,
        'change_rules': {'mode': 'any', 'rules': rules or [
            {'type': 'text_changed', 'field': field['name']}]},
        'schedule': {'interval_seconds': 60, 'timezone': 'UTC'},
        'notification_group_ids': [str(uuid4())],
        'template_bindings': {event: {'template_id': str(uuid4()), 'version': 1} for event in EVENT_TYPES},
        'coverage': {'scope': coverage, 'description': 'Visible product rows'},
    })


def scalar(kind='text', **options):
    field = {'name': 'value', 'type': kind, 'semantic': 'Value', 'selector': '.value', **options}
    rule = {'type': 'text_changed', 'field': 'value'} if kind == 'text' else {
        'type': 'threshold', 'field': 'value', 'operator': 'gt', 'value': '0'}
    # Non-rule scalar types coexist with a text field to maintain a valid DraftSpec.
    if kind not in ('text', 'number', 'money'):
        data = spec_for({'name': 'label', 'type': 'text', 'semantic': 'Label', 'selector': '.label'}).model_dump(mode='json')
        data['fields'].append(field)
        return DraftSpec.model_validate(data)
    return spec_for(field, rules=[rule])


def extract(spec, html):
    return extract_fields(html, spec, 'https://example.com/products')


def rejected(call, reason):
    with pytest.raises(DomainError) as error:
        call()
    assert error.value.code == 'collection_invalid'
    assert error.value.details['reason'] == reason


def test_unique_scalar_and_missing_attribute_are_not_empty_text():
    spec = scalar(required=False, attribute='data-value')
    assert extract(spec, '<p class="value"></p>') == {}
    assert extract(spec, '<p class="value" data-value=""></p>') == {'value': ''}
    rejected(lambda: extract(spec, '<p class="value"></p><p class="value"></p>'), 'multiple_matches')
    required = scalar(attribute='data-value')
    rejected(lambda: extract(required, '<p class="value"></p>'), 'required_missing')
    rejected(lambda: extract(required, '<p class="value" data-value=" "></p>'), 'required_empty')


def test_optional_empty_text_cannot_replace_nonempty_baseline():
    spec = scalar(required=False)
    validate_collection(spec, {'value': ''})
    rejected(lambda: validate_collection(spec, {'value': ''}, {'value': 'trusted'}), 'historical_nonempty_to_empty')
    rejected(lambda: validate_collection(spec, {}, {'value': 'trusted'}), 'historical_value_missing')


@pytest.mark.parametrize('raw,expected', [('1.234,50', '1234.50'), ('-0,25', '-0.25'), ('1234', '1234')])
def test_explicit_decimal_and_thousands_separators(raw, expected):
    spec = scalar('number', normalize={'decimal_separator': ',', 'thousands_separator': '.'})
    assert extract(spec, f'<b class="value">{raw}</b>') == {'value': expected}


@pytest.mark.parametrize('raw', ['12.34,50', '1,234.50', 'NaN', 'Infinity', '1e3', '12 junk', '.50', '1,'])
def test_number_rejects_ambiguous_or_invalid_formats(raw):
    spec = scalar('number', normalize={'decimal_separator': ',', 'thousands_separator': '.'})
    rejected(lambda: extract(spec, f'<b class="value">{raw}</b>'), 'invalid_number')


def test_money_currency_markers_and_historical_currency_are_checked():
    spec = scalar('money', normalize={'decimal_separator': '.', 'currency': 'USD'})
    assert extract(spec, '<b class="value">USD 12.50</b>') == {'value': {'amount': '12.50', 'currency': 'USD'}}
    assert extract(spec, '<b class="value">$12.50</b>') == {'value': {'amount': '12.50', 'currency': 'USD'}}
    rejected(lambda: extract(spec, '<b class="value">EUR 12.50</b>'), 'currency_mismatch')
    rejected(lambda: extract(spec, '<b class="value">€12.50</b>'), 'currency_mismatch')
    rejected(lambda: validate_collection(spec, {'value': {'amount': '12', 'currency': 'USD'}},
                                        {'value': {'amount': '12', 'currency': 'EUR'}}), 'currency_changed')


def test_boolean_uses_exact_normalized_configured_values():
    spec = scalar('boolean', normalize={'collapse_whitespace': True, 'true_values': ['in stock'], 'false_values': ['sold out']})
    assert extract(spec, '<b class="label">Availability</b><b class="value"> in \n stock </b>')['value'] is True
    assert extract(spec, '<b class="label">Availability</b><b class="value">sold out</b>')['value'] is False
    rejected(lambda: extract(spec, '<b class="label">Availability</b><b class="value">In Stock</b>'), 'invalid_boolean')


def test_dates_convert_explicit_zone_to_utc_and_reject_dst_guessing():
    spec = scalar('date', normalize={'date_format': '%Y-%m-%d %H:%M', 'timezone': 'America/New_York'})
    def html(value):
        return f'<b class="label">Date</b><b class="value">{value}</b>'
    assert extract(spec, html('2026-01-02 12:30'))['value'] == '2026-01-02T17:30:00Z'
    rejected(lambda: extract(spec, html('2026-03-08 02:30')), 'ambiguous_or_nonexistent_date')
    rejected(lambda: extract(spec, html('2026-11-01 01:30')), 'ambiguous_or_nonexistent_date')


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://127.0.0.1/x', 'http://[::1]/', 'https://user:pass@example.com/', 'https://example.com:8443/'])
def test_extracted_urls_use_target_url_restrictions(url):
    spec = scalar('url', attribute='href')
    with pytest.raises(DomainError) as error:
        extract(spec, f'<b class="label">Link</b><a class="value" href="{url}">link</a>')
    assert error.value.code == 'url_forbidden'


def test_relative_urls_are_absolute_and_fragments_removed():
    spec = scalar('url', attribute='href')
    assert extract(spec, '<b class="label">Link</b><a class="value" href="../item#price">link</a>')['value'] == 'https://example.com/item'


def list_spec(**options):
    field = {'name': 'products', 'type': 'list', 'semantic': 'Products', 'selector': '.row',
             'item_fields': [
                 {'name': 'id', 'type': 'text', 'semantic': 'Stable ID', 'selector': '.id'},
                 {'name': 'title', 'type': 'text', 'semantic': 'Title', 'selector': '.title', 'required': False}], **options}
    return spec_for(field, rules=[{'type': 'list_added', 'field': 'products'}],
                    key={'field': 'products', 'item_fields': ['id']})


def test_list_selectors_are_relative_and_duplicate_or_empty_keys_fail():
    spec = list_spec()
    rows = extract(spec, '<span class="id">outside</span><div class="row"><b class="id">A</b></div><div class="row"><b class="id">B</b><span class="title">Beta</span></div>')
    assert rows == {'products': [{'id': 'A'}, {'id': 'B', 'title': 'Beta'}]}
    validate_collection(spec, rows)
    rejected(lambda: validate_collection(spec, {'products': [{'id': 'A'}, {'id': 'A'}]}), 'duplicate_business_key')
    rejected(lambda: validate_collection(spec, {'products': [{'id': ''}]}), 'required_empty')
    rejected(lambda: validate_collection(spec, {'products': [{}]}), 'required_missing')


def test_initial_empty_list_and_historical_empty_are_distinct():
    spec = list_spec()
    validate_collection(spec, extract(spec, '<p>No products</p>'))
    rejected(lambda: validate_collection(spec, {'products': []}, {'products': [{'id': 'A'}]}), 'historical_nonempty_to_empty')
    rejected(lambda: validate_collection(list_spec(min_items=1), {'products': []}), 'min_items')
    rejected(lambda: validate_collection(list_spec(max_items=1), {'products': [{'id': 'A'}, {'id': 'B'}]}), 'max_items')


def test_current_coverage_downgrade_cannot_authorize_deletion():
    data = list_spec().model_dump(mode='json')
    data['change_rules']['rules'] = [{'type': 'list_removed', 'field': 'products'}]
    spec = DraftSpec.model_validate(data)
    rejected(lambda: validate_collection(spec, {'products': [{'id': 'A'}]},
                                        coverage={'scope': 'bounded', 'description': 'One page only'}),
             'deletion_requires_full_coverage')


def test_same_business_key_cannot_change_currency_inside_list():
    data = list_spec().model_dump(mode='json')
    data['fields'][0]['item_fields'].append({'name': 'price', 'type': 'money', 'semantic': 'Price', 'selector': '.price',
                                           'normalize': {'decimal_separator': '.', 'currency': 'USD'}})
    spec = DraftSpec.model_validate(data)
    rejected(lambda: validate_collection(spec, {'products': [{'id': 'A', 'price': {'amount': '9', 'currency': 'USD'}}]},
                                        {'products': [{'id': 'A', 'price': {'amount': '10', 'currency': 'EUR'}}]}),
             'currency_changed')


def test_row_own_attribute_and_whitespace_identity():
    data = list_spec().model_dump(mode='json')
    data['fields'][0]['item_fields'][0].update(selector=':scope', attribute='data-id', normalize={'trim': False})
    spec = DraftSpec.model_validate(data)
    assert extract(spec, '<div class="row" data-id="A"><span class="title">Alpha</span></div>') == {
        'products': [{'id': 'A', 'title': 'Alpha'}]}
    rejected(lambda: validate_collection(spec, {'products': [{'id': '   '}]}), 'empty_business_key')


def test_list_limits_apply_to_final_merged_pages_not_each_page():
    spec = list_spec(min_items=2, max_items=3)
    first = extract(spec, '<div class="row"><span class="id">A</span></div>')
    second = extract(spec, '<div class="row"><span class="id">B</span></div>')
    validate_collection(spec, {'products': first['products'] + second['products']})
    rejected(lambda: validate_collection(spec, first), 'min_items')
