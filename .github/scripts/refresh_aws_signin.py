# ruff: noqa: T201
"""Exchange an AWS sign-in refresh token for temporary CLI credentials.

GitHub Actions cannot use the local ``aws login`` cache. This script repeats the
CLI's DPoP-bound refresh against the regional sign-in token endpoint and exports
the resulting credentials for later steps. Secrets stay in the environment.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

SIGNIN_ENDPOINT = "https://eu-north-1.signin.aws.amazon.com/v1/token"
CLIENT_ID = "arn:aws:signin:::devtools/same-device"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_private_key(pem: str) -> EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, EllipticCurvePrivateKey):
        raise TypeError("DPoP key must be an EC private key")
    return key


def _public_jwk(key: EllipticCurvePrivateKey) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "EC",
        "x": _b64url(numbers.x.to_bytes(32, "big")),
        "y": _b64url(numbers.y.to_bytes(32, "big")),
        "crv": "P-256",
    }


def _dpop_proof(key: EllipticCurvePrivateKey) -> str:
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": _public_jwk(key)}
    payload = {
        "htm": "POST",
        "htu": SIGNIN_ENDPOINT,
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
    }
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r_value, s_value = utils.decode_dss_signature(signature)
    raw_signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    return f"{signing_input}.{_b64url(raw_signature)}"


def refresh_credentials(refresh_token: str, pem: str) -> dict[str, str]:
    """Return access key fields from a DPoP-bound refresh token."""
    key = _load_private_key(pem)
    body = json.dumps(
        {
            "clientId": CLIENT_ID,
            "refreshToken": refresh_token,
            "grantType": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        SIGNIN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "DPoP": _dpop_proof(key)},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    access = payload["accessToken"]
    return {
        "AWS_ACCESS_KEY_ID": access["accessKeyId"],
        "AWS_SECRET_ACCESS_KEY": access["secretAccessKey"],
        "AWS_SESSION_TOKEN": access["sessionToken"],
    }


def _pem_from_env() -> str:
    pem = os.environ["AWS_SIGNIN_DPOP_KEY"]
    if "\\n" in pem:
        pem = pem.replace("\\n", "\n")
    return pem


def _write_github_env(credentials: dict[str, str]) -> None:
    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        raise RuntimeError("GITHUB_ENV is not set")
    with open(github_env, "a", encoding="utf-8") as handle:
        for name, value in credentials.items():
            print(f"::add-mask::{value}")
            delimiter = f"AWS_CRED_{uuid.uuid4().hex}"
            handle.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
        handle.write("AWS_DEFAULT_REGION=eu-north-1\nAWS_REGION=eu-north-1\n")


def main() -> None:
    credentials = refresh_credentials(
        os.environ["AWS_SIGNIN_REFRESH_TOKEN"],
        _pem_from_env(),
    )
    _write_github_env(credentials)


if __name__ == "__main__":
    main()
