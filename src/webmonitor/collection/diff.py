"""Pure rule evaluation between successful, contract-validated snapshots.

Persistent predicates transition on the aggregate; change predicates are new
per-snapshot evidence and must never be suppressed by a previous true state.
"""
from decimal import Decimal
from typing import Any

from webmonitor.schemas.monitors import DraftSpec, canonical_json

PERSISTENT = frozenset(('threshold', 'keyword_present', 'keyword_absent'))
_MISSING = object()


def _number(value: Any, currency: str | None) -> Decimal:
    if currency is not None:
        if not isinstance(value, dict) or value.get('currency') != currency:
            raise ValueError('money currency does not match field contract')
        value = value['amount']
    if isinstance(value, bool):
        raise ValueError('boolean is not a number')
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('non-finite number')
    return number

def _same_value(field, before, after):
    if before is _MISSING or after is _MISSING:
        return before is after
    if field.type in ('number', 'money'):
        return _number(before, field.normalize.currency) == _number(after, field.normalize.currency)
    return before == after


def _rows(rows: Any, keys: list[str]) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError('list field must contain rows')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or any(key not in row or row[key] is None for key in keys):
            raise ValueError('missing business key')
        key = canonical_json([row[name] for name in keys])
        if key in result:
            raise ValueError('duplicate business key')
        result[key] = row
    return result


def evaluate_rules(spec: DraftSpec, previous: dict | None, current: dict, previous_state: dict) -> dict:
    """Return JSON-safe event evidence and next persistent state.

    ``rule_state`` is {predicates: {rule-index: bool}, aggregate: bool}.
    ``differences`` includes every currently matching rule when an event fires;
    list entries include only this snapshot's added/removed/updated rows.
    """
    fields = {field.name: field for field in spec.fields}
    predicates: list[bool] = []
    matches: list[dict] = []
    warnings: list[dict] = []
    has_change = False
    change_evidence = False
    persistent_state: dict[str, bool] = {}
    for index, rule in enumerate(spec.change_rules.rules):
        before = previous.get(rule.field, _MISSING) if previous is not None else _MISSING
        after = current.get(rule.field, _MISSING)
        field = fields[rule.field]
        persistent = rule.type in PERSISTENT
        has_change |= not persistent
        matched = False
        detail: dict[str, Any] = {}
        if rule.type in ('keyword_present', 'keyword_absent'):
            if after is not _MISSING:
                present = rule.keyword in after
                matched = present if rule.type == 'keyword_present' else not present
                detail['keyword'] = rule.keyword
        elif rule.type == 'threshold':
            if after is not _MISSING:
                value = _number(after, field.normalize.currency)
                matched = {'gt': value > rule.value, 'gte': value >= rule.value,
                           'lt': value < rule.value, 'lte': value <= rule.value,
                           'eq': value == rule.value}[rule.operator]
                detail.update(operator=rule.operator, value=str(rule.value))
        elif rule.type == 'text_changed':
            matched = before is not _MISSING and after is not _MISSING and before != after
        elif rule.type in ('number_absolute_change', 'number_percent_change'):
            if before is not _MISSING and after is not _MISSING:
                old = _number(before, field.normalize.currency)
                new = _number(after, field.normalize.currency)
                delta = new - old
                if rule.type == 'number_percent_change' and old == 0:
                    warnings.append({'code': 'rule_undefined', 'rule_index': index,
                                     'field': rule.field, 'reason': 'previous_zero'})
                else:
                    metric = delta if rule.type == 'number_absolute_change' else delta / abs(old) * 100
                    compared = {'increase_gte': metric, 'decrease_gte': -metric,
                                'change_gte': abs(metric)}[rule.operator]
                    matched = compared >= rule.value
                    detail.update(delta=str(delta), operator=rule.operator, value=str(rule.value))
                    if rule.type == 'number_percent_change':
                        detail['percent_change'] = str(metric)
        elif rule.type.startswith('list_'):
            keys = spec.business_key.item_fields
            # Validate the current key set even when initializing the baseline.
            new_rows = _rows(after, keys) if after is not _MISSING else {}
            if before is not _MISSING and after is not _MISSING:
                old_rows = _rows(before, keys)
                if old_rows and not new_rows:
                    raise ValueError('nonempty historical list became empty')
                if rule.type == 'list_added':
                    detail['items'] = [row for key, row in new_rows.items() if key not in old_rows]
                elif rule.type == 'list_removed':
                    detail['items'] = [row for key, row in old_rows.items() if key not in new_rows]
                else:
                    updated = []
                    item_fields = {item.name: item for item in field.item_fields}
                    for key, row in new_rows.items():
                        if key in old_rows:
                            changed = [name for name in rule.watch_fields if not _same_value(item_fields[name], old_rows[key].get(name, _MISSING), row.get(name, _MISSING))]
                            if changed:
                                updated.append({'before': old_rows[key], 'after': row, 'fields': changed})
                    detail['items'] = updated
                matched = bool(detail['items'])
        predicates.append(matched)
        if persistent:
            persistent_state[str(index)] = matched
        else:
            change_evidence |= matched
        if matched:
            matches.append({'rule_index': index, 'type': rule.type, 'field': rule.field,
                            'before': None if before is _MISSING else before,
                            'after': None if after is _MISSING else after, **detail})
    aggregate = all(predicates) if spec.change_rules.mode == 'all' else any(predicates)
    triggered = previous is not None and aggregate and (
        change_evidence if has_change else not previous_state.get('aggregate', False)
    )
    event_type = None
    if triggered:
        matched_fields = [fields[item['field']].type for item in matches]
        event_type = 'price_changed' if 'money' in matched_fields else 'list_changed' if 'list' in matched_fields else 'content_changed'
    # Round-trip through canonical serialization avoids Decimal/UUID leaking to JSONB.
    import json
    return json.loads(canonical_json({
        'triggered': triggered, 'event_type': event_type,
        'differences': matches if triggered else [],
        'rule_state': {'predicates': persistent_state, 'aggregate': aggregate},
        'warnings': warnings,
    }))
