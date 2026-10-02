#!/usr/bin/env python3
"""Provision a dedicated MinIO evidence user using the pinned mc client.

Run only in init-db (not a collection worker). Required environment:
S3_ENDPOINT_URL, S3_BUCKET, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD,
S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY. MC_BINARY defaults to mc.
Copy /usr/local/bin/mc from the locally built MinIO image into init-db.
Secrets are passed through environment/stdin, never command arguments or logs.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit


class ProvisionError(Exception):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or "\n" in value or "\r" in value:
        raise ProvisionError(f"Missing or invalid {name}")
    return value


def provision() -> None:
    endpoint = urlsplit(required("S3_ENDPOINT_URL"))
    if (
        endpoint.scheme not in {"http", "https"}
        or not endpoint.hostname
        or endpoint.username is not None
        or endpoint.password is not None
        or endpoint.path not in {"", "/"}
        or endpoint.query
        or endpoint.fragment
    ):
        raise ProvisionError("S3_ENDPOINT_URL must be an HTTP(S) origin")
    bucket = required("S3_BUCKET")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
        or ".." in bucket
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", bucket)
    ):
        raise ProvisionError("Invalid S3_BUCKET")
    root_user = required("MINIO_ROOT_USER")
    root_secret = required("MINIO_ROOT_PASSWORD")
    user = required("S3_ACCESS_KEY_ID")
    secret = required("S3_SECRET_ACCESS_KEY")
    if user == root_user or not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", user):
        raise ProvisionError("Evidence identity must be a dedicated non-root user")
    if len(secret) < 8 or secret == root_secret:
        raise ProvisionError("Evidence secret must be distinct and at least 8 characters")
    policy_name = "wm-evidence-" + hashlib.sha256(f"{bucket}:{user}".encode()).hexdigest()[:24]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            },
        ],
    }
    # mc writes ancillary configuration only to this private temporary directory.
    with tempfile.TemporaryDirectory(prefix="wm-s3-") as directory:
        env = os.environ.copy()
        env["MC_CONFIG_DIR"] = directory
        env["MC_HOST_provision"] = urlunsplit((
            endpoint.scheme,
            f"{quote(root_user, safe='')}:{quote(root_secret, safe='')}@{endpoint.netloc}",
            "", "", "",
        ))
        binary = os.environ.get("MC_BINARY", "mc")

        def run(step: str, *args: str, stdin: str | None = None) -> dict:
            try:
                result = subprocess.run(
                    [binary, "--json", "--config-dir", directory, *args],
                    input=stdin, capture_output=True, text=True, env=env,
                    timeout=45, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise ProvisionError(f"S3 provisioning failed at {step}") from None
            # mc can emit credentials in success JSON and errors: never print either.
            if result.returncode:
                raise ProvisionError(f"S3 provisioning failed at {step}")
            if step == "identity inspection":
                try:
                    message = json.loads(result.stdout)
                except (ValueError, TypeError):
                    raise ProvisionError("Invalid mc identity response") from None
                if not isinstance(message, dict) or message.get("status") != "success":
                    raise ProvisionError("Invalid mc identity response")
                return message
            return {}

        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        path.chmod(0o600)
        run("bucket creation", "mb", "--ignore-existing", f"provision/{bucket}")
        run("policy creation", "admin", "policy", "create", "provision", policy_name, str(path))
        # Piped keys are supported by the pinned official mc release. AddUser
        # enables the identity, including one disabled by a failed prior run.
        run("identity creation", "admin", "user", "add", "provision", stdin=f"{user}\n{secret}\n")
        try:
            info = run("identity inspection", "admin", "user", "info", "provision", user)
        except ProvisionError:
            run("identity disabling", "admin", "user", "disable", "provision", user)
            raise
        old_policies = info.get("policyName", "")
        if (
            info.get("memberOf")
            or not isinstance(old_policies, str)
            or old_policies not in {"", policy_name}
        ):
            run("identity disabling", "admin", "user", "disable", "provision", user)
            raise ProvisionError("Evidence identity has unexpected grants; left disabled")
        # PolicyDBUpdateBuiltin checks GetUser -> Credentials.IsValid(). Disabled
        # users therefore fail attach/detach with NoSuchUser in this release.
        # Accept only no grant or this exact dedicated policy; never migrate an
        # identity with unknown grants. Skip an existing binding for idempotency.
        if not old_policies:
            try:
                run("policy attachment", "admin", "policy", "attach", "provision", policy_name, "--user", user)
            except ProvisionError:
                run("identity disabling", "admin", "user", "disable", "provision", user)
                raise


if __name__ == "__main__":
    try:
        provision()
    except ProvisionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Evidence bucket and restricted runtime identity provisioned")
