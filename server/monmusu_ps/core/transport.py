import base64
import binascii
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from enum import IntEnum
from secrets import token_bytes
from typing import Any
from urllib.parse import unquote_to_bytes

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class Protocol(IntEnum):
    PLAIN = 1
    AES = 2
    RSA = 3


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedRequest:
    payload: dict[str, Any]
    protocol: Protocol
    rsa_public_key: RSAPublicKey | None = None


_RSA_SIGNATURE_SECRET = b"dka#oc-@i0eowyghjkl"
_INVALID_ESCAPE = re.compile(rb"%(?![0-9a-fA-F]{2})")


def content_type(protocol: Protocol) -> str:
    match protocol:
        case Protocol.PLAIN:
            return "application/json"
        case Protocol.AES:
            return "application/octet-stream"
        case Protocol.RSA:
            return "text/plain; charset=UTF-8"
        case _:
            raise ProtocolError("Unsupported transport protocol")


def decode_request(
    body: bytes, protocol: Protocol, password: str | None
) -> DecodedRequest:
    public_key = None
    match protocol:
        case Protocol.PLAIN:
            plaintext = body
        case Protocol.AES:
            plaintext = _decrypt_aes(body, password)
        case Protocol.RSA:
            plaintext, public_key = _decode_rsa(body)
        case _:
            raise ProtocolError("Unsupported transport protocol")
    try:
        payload = json.loads(plaintext.decode("utf-8"), parse_constant=_reject_constant)
    except ValueError as exc:
        raise ProtocolError("Request body must contain valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("Request JSON must be an object")
    return DecodedRequest(payload, protocol, public_key)


def encode_response(
    payload: dict[str, Any], request: DecodedRequest, password: str | None
) -> bytes:
    if not isinstance(payload, dict):
        raise ProtocolError("Response JSON must be an object")
    plaintext = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    match request.protocol:
        case Protocol.PLAIN:
            return plaintext
        case Protocol.AES:
            return _encrypt_aes(plaintext, password)
        case Protocol.RSA:
            if request.rsa_public_key is None:
                raise ProtocolError("RSA response requires the request public key")
            public_key = request.rsa_public_key
            oaep = rsa_padding.OAEP(
                mgf=rsa_padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None
            )
            chunk_size = (
                (public_key.key_size + 7) // 8 - 2 * hashes.SHA1.digest_size - 2
            )
            return b"\n".join(
                base64.b64encode(
                    public_key.encrypt(plaintext[offset : offset + chunk_size], oaep)
                )
                for offset in range(0, len(plaintext), chunk_size)
            )
        case _:
            raise ProtocolError("Unsupported transport protocol")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite numbers are not valid JSON")


def _aes_key(password: str | None, salt: bytes) -> bytes:
    if password is None:
        raise ProtocolError("AES session password is missing")
    return hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), salt, 10000, 32)


def _decrypt_aes(body: bytes, password: str | None) -> bytes:
    if len(body) < 64 or (len(body) - 48) % 16:
        raise ProtocolError("Invalid AES body length")
    key = _aes_key(password, body[:32])
    decryptor = Cipher(algorithms.AES(key), modes.CBC(body[32:48])).decryptor()
    padded = decryptor.update(body[48:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise ProtocolError("Invalid AES padding") from exc


def _encrypt_aes(plaintext: bytes, password: str | None) -> bytes:
    salt, iv = token_bytes(32), token_bytes(16)
    key = _aes_key(password, salt)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return salt + iv + encryptor.update(padded) + encryptor.finalize()


def _unpack_pair(encoded: bytes) -> tuple[bytes, bytes]:
    try:
        packed = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ProtocolError("Invalid RSA Base64 data") from exc
    if len(packed) < 4:
        raise ProtocolError("Incomplete RSA length header")
    first_size, second_size = struct.unpack_from(">HH", packed)
    if not first_size or not second_size or len(packed) != 4 + first_size + second_size:
        raise ProtocolError("Invalid RSA field lengths")
    return packed[4 : 4 + first_size], packed[4 + first_size :]


def _decode_rsa(body: bytes) -> tuple[bytes, RSAPublicKey]:
    if _INVALID_ESCAPE.search(body):
        raise ProtocolError("Invalid RSA form URL encoding")
    lines = unquote_to_bytes(body).split(b"\n")
    if len(lines) != 3:
        raise ProtocolError("RSA request must contain three lines")
    encoded_payload, nonce_pack, public_key_pack = lines
    if _INVALID_ESCAPE.search(encoded_payload):
        raise ProtocolError("Invalid RSA payload URL encoding")
    plaintext = unquote_to_bytes(encoded_payload.replace(b"+", b" "))
    nonce, signature = _unpack_pair(nonce_pack)
    modulus, exponent = _unpack_pair(public_key_pack)
    try:
        nonce.decode("utf-8")
        public_key = RSAPublicNumbers(
            int.from_bytes(exponent, "big"), int.from_bytes(modulus, "big")
        ).public_key()
        public_key.verify(
            signature,
            _RSA_SIGNATURE_SECRET + b"@" + plaintext + b"@" + nonce,
            rsa_padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise ProtocolError("Invalid RSA request signature") from exc
    except ValueError as exc:
        raise ProtocolError("Invalid RSA key or nonce") from exc
    return plaintext, public_key
