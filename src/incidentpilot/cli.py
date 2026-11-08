"""Local configuration helpers that never persist plaintext credentials."""

from __future__ import annotations

import argparse
import json
import secrets
from getpass import getpass

from incidentpilot.auth import AuthService, Role


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def hash_password(password: str) -> str:
    return AuthService.hash_password(password)


def user_record_json(username: str, role: str, password_hash: str) -> str:
    if not username.strip():
        raise ValueError("Username is required")
    validated_role = Role(role)
    return json.dumps(
        {username: {"password_hash": password_hash, "role": validated_role.value}},
        separators=(",", ":"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="IncidentPilot local configuration helpers")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("generate-secret", help="Generate an HMAC signing secret")
    user = subcommands.add_parser("create-user", help="Interactively create a hashed user record")
    user.add_argument("username")
    user.add_argument("--role", choices=[role.value for role in Role], required=True)
    args = parser.parse_args()

    if args.command == "generate-secret":
        print(generate_secret())
        return
    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match")
    print(user_record_json(args.username, args.role, hash_password(password)))


if __name__ == "__main__":
    main()
