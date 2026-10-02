"""Only packaged server-owned templates are evaluated, never user template code."""
from importlib.resources import files
from jinja2 import Environment, StrictUndefined, TemplateError
import nh3

from webmonitor.api.errors import DomainError
from webmonitor.schemas.notifications import EVENT_TYPES, NotificationPayload

_html = Environment(undefined=StrictUndefined, autoescape=True)
_text = Environment(undefined=StrictUndefined, autoescape=False)
_SUBJECTS = {"price_changed":"Price changed", "list_changed":"List changed", "content_changed":"Content changed", "run_failed":"Collection failed", "recovered":"Collection recovered"}


def render_email(event_type: str, payload: dict, *, version: int = 1) -> dict:
    if event_type not in EVENT_TYPES or version != 1:
        raise DomainError("template_unavailable",409)
    data = NotificationPayload.model_validate(payload).model_dump(mode="json")
    if event_type == "price_changed" and "current_price" not in data["fields"]:
        raise DomainError("template_contract_failed",422,"Price template requires current_price")
    try:
        resources = files("webmonitor.notifications.templates")
        html = _html.from_string(resources.joinpath(event_type+".html").read_text()).render(**data)
        plain = _text.from_string(resources.joinpath(event_type+".txt").read_text()).render(**data)
    except (TemplateError,TypeError,ValueError):
        raise DomainError("template_contract_failed",422) from None
    subject = f"{_SUBJECTS[event_type]}: {data['monitor']['name']}".replace("\r", " ").replace("\n", " ")
    return {"subject":subject,"html":html,"text":plain,"preview_html":sanitize_preview(html)}


def sanitize_preview(html: str) -> str:
    clean = nh3.clean(html, tags={"html","head","body","title","meta","h1","h2","h3","p","a","strong","em","table","thead","tbody","tr","th","td","ul","ol","li","br","pre","code","div","span"}, attributes={"a":{"href","title"}}, url_schemes={"http","https"}, link_rel="noopener noreferrer")
    return '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"></head><body>'+clean+'</body></html>'
