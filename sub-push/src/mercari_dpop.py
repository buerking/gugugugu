import base64
import os
import time
import uuid
from typing import Any, Dict, Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src import settings


_KEY_FILE = os.path.join(settings.APP_ROOT, "config", "dpop_es256_key.pem")


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_or_create_key():
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)

    os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(_KEY_FILE, "wb") as f:
        f.write(pem)
    return private_key


def jwk_from_key(private_key) -> Dict[str, str]:
    pub = private_key.public_key().public_numbers()
    x = _b64url_no_pad(pub.x.to_bytes(32, "big"))
    y = _b64url_no_pad(pub.y.to_bytes(32, "big"))
    return {"kty": "EC", "crv": "P-256", "x": x, "y": y}


def make_dpop_jwt(*, htm: str, htu: str, web_uuid: Optional[str] = None) -> str:
    private_key = load_or_create_key()
    jwk = jwk_from_key(private_key)

    payload: Dict[str, Any] = {
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
        "htm": (htm or "GET").upper(),
        "htu": htu,
        "uuid": web_uuid if web_uuid else str(uuid.uuid4()),
    }
    headers = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

