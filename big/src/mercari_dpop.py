import base64
import os
import time
import uuid
from typing import Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src import settings


def load_or_create_key():
    key_file = settings.DPOP_KEY_PATH
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)

    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(key_file, "wb") as f:
        f.write(pem)
    return private_key


def jwk_from_key(private_key) -> dict:
    pub = private_key.public_key().public_numbers()
    x = base64.urlsafe_b64encode(pub.x.to_bytes(32, "big")).rstrip(b"=").decode()
    y = base64.urlsafe_b64encode(pub.y.to_bytes(32, "big")).rstrip(b"=").decode()
    return {"kty": "EC", "crv": "P-256", "x": x, "y": y}


def make_dpop_jwt(*, htm: str, htu: str, session_uuid: Optional[str] = None) -> str:
    private_key = load_or_create_key()
    jwk = jwk_from_key(private_key)
    payload = {
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
        "htm": htm.upper(),
        "htu": htu,
        "uuid": session_uuid
        or str(settings.MERCARI.get("laplace_device_uuid") or uuid.uuid4()),
    }
    headers = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}
    token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
    return token if isinstance(token, str) else token.decode("utf-8")
