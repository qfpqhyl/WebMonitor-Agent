"""Bootstrap credentials fail closed without exposing their input values."""
import pytest
from pydantic import SecretStr

from webmonitor.api.errors import DomainError
from webmonitor.config import Settings
from webmonitor.services.accounts import bootstrap_admin_credentials


def settings(email="", password="", *, runtime=False):
    return Settings(development_mode=True, admin_email=email,
                    admin_password=SecretStr(password), bootstrap_runtime=runtime)


def test_standalone_initialization_can_omit_admin():
    assert bootstrap_admin_credentials(settings()) is None


@pytest.mark.parametrize("email,password,runtime,code", [
    ("", "", True, "admin_credentials_required"),
    ("admin@example.com", "", False, "admin_credentials_required"),
    ("", "a-valid-password-123", False, "admin_credentials_required"),
    ("not-an-email", "a-valid-password-123", False, "invalid_email"),
    ("admin@example.com", "short", False, "invalid_password"),
    ("admin@example.com", "x" * 129, False, "invalid_password"),
])
def test_invalid_bootstrap_credentials_are_safe(email, password, runtime, code):
    with pytest.raises(DomainError) as caught:
        bootstrap_admin_credentials(settings(email, password, runtime=runtime))
    assert caught.value.code == code
    if password:
        assert password not in str(caught.value)


def test_bootstrap_normalizes_email_without_modifying_password():
    password = "  a-valid-password-123  "
    assert bootstrap_admin_credentials(settings(" Admin@Example.COM ", password)) == (
        "admin@example.com", password)




@pytest.mark.asyncio
async def test_invalid_credentials_do_not_create_signing_secret(monkeypatch, tmp_path):
    from webmonitor.db import initialize
    configured = settings("admin@example.com", "", runtime=True)
    configured.secret_file = tmp_path / "new-secrets" / "signing.key"
    monkeypatch.setattr(initialize, "get_settings", lambda: configured)
    with pytest.raises(DomainError) as caught:
        await initialize.initialize_database()
    assert caught.value.code == "admin_credentials_required"
    assert not configured.secret_file.parent.exists()
