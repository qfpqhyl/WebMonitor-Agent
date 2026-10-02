"""Environment configuration. Secrets never enter frontend configuration."""
import ipaddress
import json
import os
import stat
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


def read_credentials(path: Path, keys: set[str]) -> dict[str, str]:
    """Read one owner-only regular file without following a symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise ValueError("Credential file must be owned by this process and have mode 0600")
        if info.st_size > 4096:
            raise ValueError("Credential file is too large")
        values = json.load(stream)
    if not isinstance(values, dict) or set(values) != keys or any(
        not isinstance(value, str) or not value or any(char in value for char in "\r\n\x00")
        for value in values.values()
    ):
        raise ValueError("Invalid credential file")
    return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    pghost: str = "postgres"
    pgport: int = 5432
    pgdatabase: str = "webmonitor"
    pguser: str = "webmonitor_runtime"
    postgres_password: SecretStr = SecretStr("")
    pgpassword: SecretStr | None = None
    pgcredentials_file: Path | None = None
    bootstrap_runtime: bool = False
    require_runtime_bootstrap: bool = False
    trusted_proxy_ip: str | None = None
    app_origin: str = "http://localhost:8080"
    session_ttl_hours: int = 168
    registration_allowed_emails: str = ""
    secret_file: Path = Path("/run/webmonitor/secrets/signing.key")
    development_mode: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_container_base_url: str | None = None
    openai_model: str = ""
    container_runtime: bool = False
    s3_endpoint_url: str = "http://s3:9000"
    s3_bucket: str = "webmonitor-evidence"
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_credentials_file: Path | None = None
    egress_proxy_url: str = "http://egress:3128"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: SecretStr = SecretStr("")
    smtp_password: SecretStr = SecretStr("")
    smtp_use_ssl: bool = True
    smtp_starttls: bool = False
    mail_from: str = ""
    smoke_fixture_origin: str | None = None

    @model_validator(mode="after")
    def validate_origin(self):
        if self.pgcredentials_file is not None:
            credentials = read_credentials(self.pgcredentials_file, {"username", "password"})
            self.pguser = credentials["username"]
            self.pgpassword = SecretStr(credentials["password"])
            self.postgres_password = SecretStr("")
        if self.s3_credentials_file is not None:
            credentials = read_credentials(self.s3_credentials_file, {"access_key_id", "secret_access_key"})
            self.s3_access_key_id = SecretStr(credentials["access_key_id"])
            self.s3_secret_access_key = SecretStr(credentials["secret_access_key"])
        if self.trusted_proxy_ip is not None:
            self.trusted_proxy_ip = str(ipaddress.ip_address(self.trusted_proxy_ip))
        origin = urlsplit(self.app_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname:
            raise ValueError("APP_ORIGIN must be an HTTP(S) origin")
        if origin.path or origin.query or origin.fragment or origin.username:
            raise ValueError("APP_ORIGIN must not contain path or credentials")
        if origin.scheme == "http" and not self.development_mode:
            raise ValueError("HTTP requires explicit DEVELOPMENT_MODE=true")
        if self.session_ttl_hours <= 0:
            raise ValueError("SESSION_TTL_HOURS must be positive")
        if self.smoke_fixture_origin not in {None, "http://fixture:8000"}:
            raise ValueError("Smoke exception must exactly match http://fixture:8000")
        return self

    @property
    def database_url(self) -> URL:
        password = self.pgpassword or self.postgres_password
        return URL.create("postgresql+asyncpg", username=self.pguser,
                          password=password.get_secret_value(), host=self.pghost,
                          port=self.pgport, database=self.pgdatabase)

    @property
    def model_base_url(self) -> str:
        if not self.container_runtime:
            return self.openai_base_url
        if self.openai_container_base_url:
            return self.openai_container_base_url
        parts = urlsplit(self.openai_base_url)
        if parts.hostname in {"127.0.0.1", "localhost"}:
            authority = "host.docker.internal"
            if parts.port:
                authority += f":{parts.port}"
            return urlunsplit(parts._replace(netloc=authority))
        return self.openai_base_url

    @property
    def allowed_registration_emails(self) -> set[str]:
        return {item.strip().casefold() for item in self.registration_allowed_emails.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
