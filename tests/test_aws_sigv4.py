"""Tests for the SigV4 WebSocket presigner.

`derive_signing_key` is checked against AWS's published "Deriving a Signing
Key" test vector, so this is verifiable against the spec, not just self-
consistent.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from custom_components.aiper_irrisense.aws_sigv4 import (
    derive_signing_key,
    presign_iot_wss_url,
)


def test_derive_signing_key_matches_aws_vector() -> None:
    # AWS docs: "Examples of How to Derive a Signing Key for Signature V4".
    key = derive_signing_key(
        "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20120215", "us-east-1", "iam"
    )
    assert key.hex() == (
        "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"
    )


def test_presign_url_structure() -> None:
    url = presign_iot_wss_url(
        "abc-ats.iot.eu-central-1.amazonaws.com",
        "eu-central-1",
        "AKIDEXAMPLE",
        "secretkey",
        session_token="sess/tok+en",
        amz_date="20260101T000000Z",
        datestamp="20260101",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "wss"
    assert parsed.netloc == "abc-ats.iot.eu-central-1.amazonaws.com"
    assert parsed.path == "/mqtt"

    q = parse_qs(parsed.query)
    assert q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert q["X-Amz-Credential"] == ["AKIDEXAMPLE/20260101/eu-central-1/iotdevicegateway/aws4_request"]
    assert q["X-Amz-SignedHeaders"] == ["host"]
    assert len(q["X-Amz-Signature"][0]) == 64          # hex sha256
    # Session token is present and URL-encoded (appended after signing).
    assert q["X-Amz-Security-Token"] == ["sess/tok+en"]


def test_presign_is_deterministic_for_fixed_time() -> None:
    args = ("e.iot.eu-central-1.amazonaws.com", "eu-central-1", "AK", "sk")
    kw = {"amz_date": "20260101T000000Z", "datestamp": "20260101"}
    assert presign_iot_wss_url(*args, **kw) == presign_iot_wss_url(*args, **kw)


def test_presign_without_session_token_omits_it() -> None:
    url = presign_iot_wss_url(
        "e.iot.eu-central-1.amazonaws.com", "eu-central-1", "AK", "sk",
        amz_date="20260101T000000Z", datestamp="20260101",
    )
    assert "X-Amz-Security-Token" not in url
