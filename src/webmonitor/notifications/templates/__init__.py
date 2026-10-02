"""Built-in immutable HTML and plain-text email template resources.

Load with importlib.resources.files(__package__). Render HTML with Jinja2
autoescaping and all templates with StrictUndefined. Context keys are the
NotificationPayload fields: monitor, event, change, fields, evidence_url.
The price_changed pair additionally requires fields.current_price.
"""
