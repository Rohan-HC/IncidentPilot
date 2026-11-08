import json

from incidentpilot.cli import generate_secret, hash_password, user_record_json


def test_generate_secret_is_suitable_for_hmac_configuration():
    first = generate_secret()
    second = generate_secret()

    assert first != second
    assert len(first.encode("utf-8")) >= 32


def test_hash_password_returns_argon2_not_plaintext():
    encoded = hash_password("a-long-local-test-password")

    assert encoded.startswith("$argon2")
    assert "a-long-local-test-password" not in encoded


def test_user_record_json_contains_hash_and_role_but_no_password():
    payload = json.loads(user_record_json("alice", "approver", "$argon2id$example"))

    assert payload == {"alice": {"password_hash": "$argon2id$example", "role": "approver"}}
