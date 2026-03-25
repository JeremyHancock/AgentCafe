"""Per-request authorization artifact for jointly-verified services (ADR-031).

Implements the Per-Request Artifact Spec:
- Canonical request hashing (method + normalized path + body bytes)
- Artifact JWT construction and signing (RS256, 30s TTL)
- Human ID hashing for the ``ac_human_id_hash`` claim

Standard-mode services never see artifacts. This module is only invoked
when ``proxy_configs.integration_mode = 'jointly_verified'``.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from agentcafe.keys import sign_artifact_token

_ARTIFACT_TTL_SECONDS = 30
_STANDARD_VERSION = "1.0"


def compute_request_hash(
    method: str,
    backend_path: str,
    inputs: dict,
) -> tuple[str, bytes]:
    """Compute the canonical request hash and serialized body bytes.

    Returns ``(hex_hash, body_bytes)`` — both are needed downstream.
    The ``body_bytes`` must be sent as the HTTP request body so that
    the service can recompute the same hash.
    """
    normalized_path = backend_path.rstrip("/")
    body_bytes = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    hash_input = f"{method.upper()}\n{normalized_path}\n".encode("utf-8") + body_bytes
    return hashlib.sha256(hash_input).hexdigest(), body_bytes


def hash_human_id(ac_human_id: str) -> str:
    """SHA-256 hex digest of the AC internal human ID (full 64 chars)."""
    return hashlib.sha256(ac_human_id.encode("utf-8")).hexdigest()


def sign_artifact(
    *,
    service_id: str,
    service_account_id: str,
    action_id: str,
    consent_ref: str,
    ac_human_id: str,
    identity_binding: str,
    request_hash: str,
    jti: str | None = None,
) -> str:
    """Build and sign a per-request authorization artifact JWT.

    The artifact is a short-lived RS256 JWT (30 s TTL) that the Cafe
    attaches to every proxied request for jointly-verified actions.
    """
    now = int(time.time())
    payload = {
        "iss": "agentcafe",
        "aud": service_id,
        "sub": service_account_id,
        "iat": now,
        "exp": now + _ARTIFACT_TTL_SECONDS,
        "jti": jti or str(uuid.uuid4()),
        "action": action_id,
        "scopes": [f"{service_id}:{action_id}"],
        "consent_ref": consent_ref,
        "ac_human_id_hash": hash_human_id(ac_human_id),
        "identity_binding": identity_binding,
        "request_hash": request_hash,
        "standard_version": _STANDARD_VERSION,
    }
    return sign_artifact_token(payload)
