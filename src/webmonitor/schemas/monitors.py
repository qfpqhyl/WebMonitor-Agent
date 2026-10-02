"""Validated monitor configuration shared by HTTP, Agent and workers."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

from webmonitor.schemas.notifications import Contract, EVENT_TYPES, EventType, TemplateBinding

# ISO 4217 monetary codes, including fund/metal units and no-currency XXX.
ISO_CURRENCIES = frozenset('''
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV
BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK
DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD
HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD
KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK
MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR
RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL
THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES
VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS
XUA XXX YER ZAR ZMW ZWG
'''.split())


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode='python'))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('non-finite decimal')
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('datetime must have a timezone')
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('JSON object keys must be strings')
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('timezone must be an IANA identifier') from exc
    return value


class Normalization(Contract):
    trim: StrictBool = True
    collapse_whitespace: StrictBool = False
    decimal_separator: Literal['.', ','] | None = None
    thousands_separator: Literal['.', ',', ' ', "'"] | None = None
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    date_format: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = None
    true_values: list[str] = Field(default_factory=list)
    false_values: list[str] = Field(default_factory=list)

    @field_validator('currency')
    @classmethod
    def valid_currency(cls, value: str | None) -> str | None:
        if value is not None and value not in ISO_CURRENCIES:
            raise ValueError('currency must be an ISO 4217 code')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        return _timezone(value) if value is not None else None

    @model_validator(mode='after')
    def unambiguous(self) -> 'Normalization':
        if self.thousands_separator is not None and self.thousands_separator == self.decimal_separator:
            raise ValueError('decimal and thousands separators must differ')
        values = self.true_values + self.false_values
        if self.trim:
            values = [value.strip() for value in values]
        if self.collapse_whitespace:
            values = [' '.join(value.split()) for value in values]
        if len(set(values)) != len(values):
            raise ValueError('boolean values must be distinct and disjoint')
        split = len(self.true_values)
        self.true_values = values[:split]
        self.false_values = values[split:]
        return self


class FieldSpec(Contract):
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    type: Literal['text', 'number', 'money', 'date', 'boolean', 'url', 'list']
    semantic: str = Field(min_length=1, max_length=500)
    selector: str = Field(min_length=1, max_length=1000)
    attribute: str | None = Field(default=None, pattern=r'^[A-Za-z_][A-Za-z0-9_:-]*$')
    required: StrictBool = True
    normalize: Normalization = Field(default_factory=Normalization)
    item_fields: list['FieldSpec'] | None = None
    min_items: int | None = Field(default=None, ge=0, strict=True)
    max_items: int | None = Field(default=None, ge=0, strict=True)

    @field_validator('attribute', mode='before')
    @classmethod
    def empty_attribute(cls, value: Any) -> Any:
        return None if value == '' else value

    @field_validator('selector')
    @classmethod
    def css_only(cls, value: str) -> str:
        value = value.strip()
        if not value or value.startswith(('/', 'xpath=', 'javascript:', 'js=')) or '::' in value:
            raise ValueError('only CSS element selectors are supported')
        # Parse CSS without allowing XPath, script or expressions.
        import soupsieve
        try:
            soupsieve.compile(value)
        except soupsieve.SelectorSyntaxError as exc:
            raise ValueError('invalid CSS selector') from exc
        return value

    @model_validator(mode='after')
    def type_contract(self) -> 'FieldSpec':
        normal = self.normalize
        if self.type == 'list':
            if not self.item_fields or self.attribute is not None:
                raise ValueError('list requires item_fields and cannot use an attribute')
            if any(item.type == 'list' for item in self.item_fields):
                raise ValueError('nested lists are not supported')
            if len({item.name for item in self.item_fields}) != len(self.item_fields):
                raise ValueError('duplicate item field name')
            if self.min_items is not None and self.max_items is not None and self.min_items > self.max_items:
                raise ValueError('min_items exceeds max_items')
        elif self.item_fields is not None or self.min_items is not None or self.max_items is not None:
            raise ValueError('item_fields and item limits apply only to lists')
        if self.type in ('number', 'money'):
            if normal.decimal_separator is None:
                raise ValueError('numeric fields require an explicit decimal_separator')
        elif normal.decimal_separator is not None or normal.thousands_separator is not None:
            raise ValueError('numeric separators apply only to number/money')
        if self.type == 'money':
            if normal.currency is None:
                raise ValueError('money requires an explicit ISO currency')
        elif normal.currency is not None:
            raise ValueError('currency applies only to money')
        if self.type == 'date':
            if normal.date_format is None or normal.timezone is None:
                raise ValueError('date requires an explicit date_format and timezone')
        elif normal.date_format is not None or normal.timezone is not None:
            raise ValueError('date configuration applies only to dates')
        if self.type == 'boolean':
            if not normal.true_values or not normal.false_values:
                raise ValueError('boolean requires true_values and false_values')
        elif normal.true_values or normal.false_values:
            raise ValueError('boolean values apply only to boolean fields')
        return self


class BusinessKey(Contract):
    field: str
    item_fields: list[str] = Field(min_length=1)

    @field_validator('item_fields')
    @classmethod
    def unique_fields(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError('duplicate business key field')
        return value


class Coverage(Contract):
    scope: Literal['full', 'bounded']
    description: str = Field(min_length=1, max_length=2000)


class RuleBase(Contract):
    field: str


class TextChanged(RuleBase):
    type: Literal['text_changed']


class KeywordPresent(RuleBase):
    type: Literal['keyword_present']
    keyword: str = Field(min_length=1)


class KeywordAbsent(RuleBase):
    type: Literal['keyword_absent']
    keyword: str = Field(min_length=1)


class NumericChange(RuleBase):
    operator: Literal['increase_gte', 'decrease_gte', 'change_gte']
    value: Decimal = Field(gt=0, allow_inf_nan=False)


class NumberAbsoluteChange(NumericChange):
    type: Literal['number_absolute_change']


class NumberPercentChange(NumericChange):
    type: Literal['number_percent_change']


class Threshold(RuleBase):
    type: Literal['threshold']
    operator: Literal['gt', 'gte', 'lt', 'lte', 'eq']
    value: Decimal = Field(allow_inf_nan=False)


class ListAdded(RuleBase):
    type: Literal['list_added']


class ListRemoved(RuleBase):
    type: Literal['list_removed']


class ListUpdated(RuleBase):
    type: Literal['list_updated']
    watch_fields: list[str] = Field(min_length=1)

    @field_validator('watch_fields')
    @classmethod
    def unique_fields(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError('duplicate watch field')
        return value


ChangeRule = Annotated[TextChanged | KeywordPresent | KeywordAbsent | NumberAbsoluteChange | NumberPercentChange | Threshold | ListAdded | ListRemoved | ListUpdated, Field(discriminator='type')]


class ChangeRules(Contract):
    mode: Literal['all', 'any']
    rules: list[ChangeRule] = Field(min_length=1)


class Schedule(Contract):
    interval_seconds: int = Field(ge=60, le=86400, strict=True)
    timezone: str

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _timezone(value)


class WaitForSelector(Contract):
    type: Literal['wait_for_selector']
    selector: str
    timeout_ms: int = Field(default=10000, gt=0, le=10000, strict=True)

    @field_validator('selector')
    @classmethod
    def valid_selector(cls, value: str) -> str:
        return FieldSpec.css_only(value)


class Scroll(Contract):
    type: Literal['scroll']
    max_steps: int = Field(ge=1, le=5, strict=True)


class ClickNext(Contract):
    type: Literal['click_next']
    selector: str
    max_pages: int = Field(ge=1, le=3, strict=True)

    @field_validator('selector')
    @classmethod
    def valid_selector(cls, value: str) -> str:
        return FieldSpec.css_only(value)


BrowserStep = Annotated[WaitForSelector | Scroll | ClickNext, Field(discriminator='type')]


class DraftSpec(Contract):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=4096)
    collection_mode: Literal['http', 'browser']
    fields: list[FieldSpec] = Field(min_length=1)
    business_key: BusinessKey | None = None
    change_rules: ChangeRules
    schedule: Schedule
    notification_group_ids: list[UUID] = Field(min_length=1)
    template_bindings: dict[EventType, TemplateBinding] = Field(description='Copy all five bindings from list_notification_groups: price_changed, list_changed, content_changed, run_failed, recovered. All five are required even when the monitor does not use every business event type.')
    coverage: Coverage
    browser_steps: list[BrowserStep] = Field(default_factory=list)

    @field_validator('url')
    @classmethod
    def http_url(cls, value: str) -> str:
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(value)
            _ = parsed.port  # Force malformed port validation; egress owns port policy.
        except ValueError as exc:
            raise ValueError('invalid HTTP(S) URL') from exc
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError('HTTP(S) URL without user information required')
        return value

    @model_validator(mode='after')
    def references(self) -> 'DraftSpec':
        fields = {field.name: field for field in self.fields}
        if len(fields) != len(self.fields):
            raise ValueError('duplicate field name')
        if len(set(self.notification_group_ids)) != len(self.notification_group_ids):
            raise ValueError('duplicate notification group')
        if set(self.template_bindings) != EVENT_TYPES:
            raise ValueError('bindings must include all five event types')
        if self.collection_mode == 'http' and self.browser_steps:
            raise ValueError('browser_steps require browser collection mode')
        if any(field.type == 'list' for field in self.fields) and self.business_key is None:
            raise ValueError('list fields require a designated business_key')
        if self.business_key:
            target = fields.get(self.business_key.field)
            if target is None or target.type != 'list':
                raise ValueError('business_key must reference a list field')
            items = {item.name: item for item in target.item_fields or []}
            for key in self.business_key.item_fields:
                if key not in items or not items[key].required:
                    raise ValueError('business key fields must exist and be required')
        for rule in self.change_rules.rules:
            target = fields.get(rule.field)
            if target is None:
                raise ValueError('rule references missing field')
            if rule.type in ('text_changed', 'keyword_present', 'keyword_absent') and target.type != 'text':
                raise ValueError('text rules require a text field')
            if rule.type in ('number_absolute_change', 'number_percent_change', 'threshold') and target.type not in ('number', 'money'):
                raise ValueError('numeric rules require number/money field')
            if rule.type.startswith('list_'):
                if target.type != 'list' or self.business_key is None or self.business_key.field != rule.field:
                    raise ValueError('list rules require the designated business_key list')
                if rule.type == 'list_removed' and self.coverage.scope != 'full':
                    raise ValueError('list deletion requires full coverage')
                if isinstance(rule, ListUpdated):
                    item_names = {item.name for item in target.item_fields or []}
                    if not set(rule.watch_fields) <= item_names or set(rule.watch_fields) & set(self.business_key.item_fields):
                        raise ValueError('watch_fields must be existing non-business-key fields')
        return self


class CreateMonitorResult(Contract):
    task_id: UUID
    monitor_version_id: UUID
    status: Literal['active'] = 'active'
    baseline_status: Literal['pending_first_success'] = 'pending_first_success'
    next_run_at: datetime
    warnings: list[str] = Field(default_factory=list)

    @field_validator('next_run_at')
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError('next_run_at must have timezone')
        return value.astimezone(timezone.utc)
