"""Consumer-visible baseline and event transition boundaries."""
from uuid import uuid4

from webmonitor.collection.diff import evaluate_rules
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.schemas.notifications import EVENT_TYPES


def make_spec(fields, rules, *, business_key=None, mode='any'):
    return DraftSpec.model_validate({
        'name': 'Regression monitor', 'url': 'https://example.com/product',
        'collection_mode': 'http', 'fields': fields, 'business_key': business_key,
        'change_rules': {'mode': mode, 'rules': rules},
        'schedule': {'interval_seconds': 60, 'timezone': 'UTC'},
        'notification_group_ids': [str(uuid4())],
        'template_bindings': {event: {'template_id': str(uuid4()), 'version': 1} for event in EVENT_TYPES},
        'coverage': {'scope': 'full', 'description': 'Complete product list'},
    })


def number_spec(rule):
    return make_spec([{'name': 'price', 'type': 'number', 'semantic': 'Current price',
                       'selector': '.price', 'normalize': {'decimal_separator': '.'}}], [rule])


def test_percent_zero_is_undefined_then_uses_latest_success():
    spec = number_spec({'type': 'number_percent_change', 'field': 'price',
                        'operator': 'decrease_gte', 'value': '10'})
    initialized = evaluate_rules(spec, None, {'price': '0'}, {})
    assert initialized['triggered'] is False
    undefined = evaluate_rules(spec, {'price': '0'}, {'price': '100'}, initialized['rule_state'])
    assert undefined['triggered'] is False
    assert undefined['warnings'] == [{'code': 'rule_undefined', 'rule_index': 0,
                                      'field': 'price', 'reason': 'previous_zero'}]
    dropped = evaluate_rules(spec, {'price': '100'}, {'price': '80'}, undefined['rule_state'])
    assert dropped['triggered'] is True
    assert dropped['differences'][0]['before'] == '100'
    unchanged = evaluate_rules(spec, {'price': '80'}, {'price': '80'}, dropped['rule_state'])
    assert unchanged['triggered'] is False


def test_negative_previous_percent_uses_absolute_denominator():
    spec = number_spec({'type': 'number_percent_change', 'field': 'price',
                        'operator': 'increase_gte', 'value': '10'})
    result = evaluate_rules(spec, {'price': '-100'}, {'price': '-80'}, {})
    assert result['triggered'] is True


def test_successive_list_additions_each_emit_only_new_rows():
    spec = make_spec([
        {'name': 'products', 'type': 'list', 'semantic': 'Products', 'selector': '.product',
         'item_fields': [{'name': 'id', 'type': 'text', 'semantic': 'Stable ID', 'selector': '.id'},
                         {'name': 'title', 'type': 'text', 'semantic': 'Title', 'selector': '.title'}]}
    ], [{'type': 'list_added', 'field': 'products'}],
       business_key={'field': 'products', 'item_fields': ['id']})
    a = {'id': 'A', 'title': 'Alpha'}
    b = {'id': 'B', 'title': 'Beta'}
    c = {'id': 'C', 'title': 'Gamma'}
    state = evaluate_rules(spec, None, {'products': [a]}, {})
    assert state['triggered'] is False
    added_b = evaluate_rules(spec, {'products': [a]}, {'products': [a, b]}, state['rule_state'])
    assert added_b['event_type'] == 'list_changed'
    assert added_b['differences'][0]['items'] == [b]
    added_c = evaluate_rules(spec, {'products': [a, b]}, {'products': [a, b, c]}, added_b['rule_state'])
    assert added_c['triggered'] is True
    assert added_c['differences'][0]['items'] == [c]
    unchanged = evaluate_rules(spec, {'products': [a, b, c]}, {'products': [a, b, c]}, added_c['rule_state'])
    assert unchanged['triggered'] is False


def test_threshold_initial_true_and_continuous_true_do_not_notify():
    spec = number_spec({'type': 'threshold', 'field': 'price', 'operator': 'gte', 'value': '100'})
    initial = evaluate_rules(spec, None, {'price': '110'}, {})
    assert initial['triggered'] is False
    sustained = evaluate_rules(spec, {'price': '110'}, {'price': '120'}, initial['rule_state'])
    assert sustained['triggered'] is False
    off = evaluate_rules(spec, {'price': '120'}, {'price': '90'}, sustained['rule_state'])
    assert off['triggered'] is False
    on = evaluate_rules(spec, {'price': '90'}, {'price': '100'}, off['rule_state'])
    assert on['triggered'] is True
    assert on['event_type'] == 'content_changed'


def test_persistent_predicate_does_not_swallow_new_change_in_mixed_any_rules():
    spec = make_spec([
        {'name': 'price', 'type': 'number', 'semantic': 'Current price', 'selector': '.price',
         'normalize': {'decimal_separator': '.'}},
        {'name': 'title', 'type': 'text', 'semantic': 'Title', 'selector': '.title'},
    ], [
        {'type': 'threshold', 'field': 'price', 'operator': 'gte', 'value': '100'},
        {'type': 'text_changed', 'field': 'title'},
    ])
    initial = evaluate_rules(spec, None, {'price': '100', 'title': 'A'}, {})
    unchanged = evaluate_rules(spec, {'price': '100', 'title': 'A'},
                               {'price': '100', 'title': 'A'}, initial['rule_state'])
    assert unchanged['triggered'] is False
    first = evaluate_rules(spec, {'price': '100', 'title': 'A'},
                           {'price': '100', 'title': 'B'}, unchanged['rule_state'])
    second = evaluate_rules(spec, {'price': '100', 'title': 'B'},
                            {'price': '100', 'title': 'C'}, first['rule_state'])
    assert first['triggered'] is True
    assert second['triggered'] is True
    assert [item['type'] for item in second['differences']] == ['threshold', 'text_changed']
    assert second['differences'][1]['before'] == 'B'


def test_bounded_coverage_rejects_deletion_and_foreign_list_rules():
    import pytest
    from pydantic import ValidationError

    row = [{'name': 'id', 'type': 'text', 'semantic': 'ID', 'selector': '.id'}]
    spec = make_spec([
        {'name': 'products', 'type': 'list', 'semantic': 'Products', 'selector': '.product', 'item_fields': row},
        {'name': 'related', 'type': 'list', 'semantic': 'Related', 'selector': '.related', 'item_fields': row},
    ], [{'type': 'list_removed', 'field': 'products'}],
       business_key={'field': 'products', 'item_fields': ['id']})
    bounded = spec.model_dump(mode='json')
    bounded['coverage']['scope'] = 'bounded'
    with pytest.raises(ValidationError):
        DraftSpec.model_validate(bounded)
    foreign = spec.model_dump(mode='json')
    foreign['change_rules']['rules'][0]['field'] = 'related'
    with pytest.raises(ValidationError):
        DraftSpec.model_validate(foreign)



def test_list_numeric_formatting_is_not_a_business_update():
    spec = make_spec([{'name': 'products', 'type': 'list', 'semantic': 'Products', 'selector': '.row',
        'item_fields': [
            {'name': 'id', 'type': 'text', 'semantic': 'Identity', 'selector': '.id'},
            {'name': 'price', 'type': 'money', 'semantic': 'Price', 'selector': '.price',
             'normalize': {'decimal_separator': '.', 'currency': 'USD'}},
            {'name': 'quantity', 'type': 'number', 'semantic': 'Quantity', 'selector': '.qty',
             'normalize': {'decimal_separator': '.'}}]}],
        [{'type': 'list_updated', 'field': 'products', 'watch_fields': ['price', 'quantity']}],
        business_key={'field': 'products', 'item_fields': ['id']})
    before = {'products': [{'id': 'A', 'price': {'amount': '12.50', 'currency': 'USD'}, 'quantity': '2.0'}]}
    same = {'products': [{'id': 'A', 'price': {'amount': '12.5', 'currency': 'USD'}, 'quantity': '2'}]}
    assert not evaluate_rules(spec, before, same, {})['triggered']
    changed = {'products': [{'id': 'A', 'price': {'amount': '12.6', 'currency': 'USD'}, 'quantity': '2'}]}
    result = evaluate_rules(spec, same, changed, {})
    assert result['triggered']
    assert result['differences'][0]['items'][0]['fields'] == ['price']
