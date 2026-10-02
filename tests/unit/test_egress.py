"""Public URL admission never replaces the connection-level proxy ACL."""
import pytest
from webmonitor.api.errors import DomainError
from webmonitor.config import get_settings
from webmonitor.security.egress import public_address, validate_url


@pytest.fixture(autouse=True)
def development_config(monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_MODE", "true")
    monkeypatch.delenv("SMOKE_FIXTURE_ORIGIN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://10.0.0.1/", "http://172.16.0.1/",
    "http://192.168.1.1/", "http://224.0.0.1/", "http://localhost./",
    "http://a.localhost/", "file:///etc/passwd", "https://user:pass@example.com/",
    "http://example.com:8000/", "http://fixture:8000/", "http://host.docker.internal/",
    "http://[fe80::1%25eth0]/", "http://example.com\\@127.0.0.1/",
    "http://postgres/", "http://example.com:0/", "http://2130706433/",
])
def test_forbidden_targets(url):
    with pytest.raises(DomainError) as exc:
        validate_url(url)
    assert exc.value.code == "url_forbidden"


def test_smoke_exception_is_exact(monkeypatch):
    monkeypatch.setenv("SMOKE_FIXTURE_ORIGIN", "http://fixture:8000")
    get_settings.cache_clear()
    assert validate_url("http://fixture:8000/static/product") == "http://fixture:8000/static/product"
    for url in ("http://127.0.0.1:8000/", "http://fixture:8001/", "https://fixture:8000/", "http://fixture.:8000/"):
        with pytest.raises(DomainError):
            validate_url(url)


def test_mapped_ipv6_private_address_rejected():
    assert not public_address("::ffff:192.168.1.1")
    assert public_address("2606:4700:4700::1111")
