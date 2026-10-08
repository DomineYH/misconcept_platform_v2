"""AES-256-GCM key material; infrastructure errors never prevent login."""

import base64
import binascii
import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config import config


class ProviderSecretUnavailableError(Exception):
    """Safe configuration failure without credential or crypto details."""


def master_key():
    try:
        value = config.PROVIDER_SECRET_ENCRYPTION_KEY.get_secret_value()
        key = base64.b64decode(value, validate=True)
        version = config.PROVIDER_SECRET_ENCRYPTION_KEY_VERSION
        if len(key) == 32 and version.strip() and len(version) <= 64:
            return key
    except (ValueError, binascii.Error):
        pass
    return None


def associated_data(provider, connection_id, revision):
    return json.dumps(
        [provider, connection_id, revision], separators=(",", ":")
    ).encode()


def encrypt_key(connection, value, revision):
    key = master_key()
    if key is None:
        raise ProviderSecretUnavailableError("configuration_unavailable")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(
        nonce,
        value.encode(),
        associated_data(connection.provider, connection.id, revision),
    )
    return ciphertext, nonce


def decrypt_key(connection):
    key = master_key()
    if (
        key is None
        or connection.encryption_key_version
        != config.PROVIDER_SECRET_ENCRYPTION_KEY_VERSION
        or not connection.encrypted_key
    ):
        raise ProviderSecretUnavailableError("configuration_unavailable")
    try:
        return (
            AESGCM(key)
            .decrypt(
                connection.nonce,
                connection.encrypted_key,
                associated_data(
                    connection.provider,
                    connection.id,
                    connection.credential_revision,
                ),
            )
            .decode()
        )
    except (InvalidTag, ValueError, UnicodeDecodeError):
        raise ProviderSecretUnavailableError(
            "configuration_unavailable"
        ) from None
