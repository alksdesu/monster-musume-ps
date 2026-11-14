import base64
import json
import struct
from urllib.parse import quote_from_bytes, unquote_to_bytes

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from monmusu_ps.core import transport
from monmusu_ps.core.transport import (
    DecodedRequest,
    Protocol,
    ProtocolError,
    content_type,
    decode_request,
    encode_response,
)


DOTNET_AES_VECTOR = base64.b64decode(
    "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4v"
    "y49AQ4hXuKTA+dzm8wqqpcNIQoHqF0Zzco8BuljFTnMBMYFgqtMgLuWfuSnV+8t7"
)
VECTOR_PAYLOAD = {"name": "魔物娘", "empty": None, "items": []}


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=1024)


def pack_pair(first, second):
    return base64.b64encode(
        struct.pack(">HH", len(first), len(second)) + first + second
    )


def unity_form_encode(frame):
    forbidden = b"@&;:<>=?\"'/\\!#%+$,{}|^[]`"
    return b"".join(
        b"%20"
        if value == 32
        else f"%{value:02x}".encode()
        if value < 32 or value > 126 or value in forbidden
        else bytes((value,))
        for value in frame
    )


def rsa_request(private_key, plaintext):
    nonce = base64.b64encode(bytes(range(16)))
    signature = private_key.sign(
        b"dka#oc-@i0eowyghjkl@" + plaintext + b"@" + nonce,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    numbers = private_key.public_key().public_numbers()
    modulus = numbers.n.to_bytes((private_key.key_size + 7) // 8, "big")
    exponent = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    frame = b"\n".join(
        (
            quote_from_bytes(plaintext, safe="").encode("ascii"),
            pack_pair(nonce, signature),
            pack_pair(modulus, exponent),
        )
    )
    return unity_form_encode(frame)


def test_plain_unicode_and_empty_values():
    payload = {
        "text": "魔物娘 🎲 + %\n",
        "null": None,
        "empty": "",
        "list": [],
        "object": {},
    }
    request = decode_request(
        json.dumps(payload, ensure_ascii=False).encode(), Protocol.PLAIN, None
    )
    assert request == DecodedRequest(payload, Protocol.PLAIN)
    response = encode_response(payload, request, None)
    assert json.loads(response) == payload
    assert "魔物娘".encode() in response


@pytest.mark.parametrize("body", [b"", b"null", b"[]", b"true", b"1", b'"text"'])
def test_request_requires_json_object(body):
    with pytest.raises(ProtocolError):
        decode_request(body, Protocol.PLAIN, None)


@pytest.mark.parametrize(
    "body", [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":"\xff"}']
)
def test_request_rejects_invalid_json_numbers_and_utf8(body):
    with pytest.raises(ProtocolError, match="valid UTF-8 JSON"):
        decode_request(body, Protocol.PLAIN, None)


def test_aes_decodes_dotnet_vector():
    request = decode_request(DOTNET_AES_VECTOR, Protocol.AES, "kdfp-test-123")
    assert request == DecodedRequest(VECTOR_PAYLOAD, Protocol.AES)


def test_aes_encodes_dotnet_vector(monkeypatch):
    entropy = iter((bytes(range(32)), bytes(range(32, 48))))
    monkeypatch.setattr(transport, "token_bytes", lambda count: next(entropy))
    request = DecodedRequest({}, Protocol.AES)
    assert (
        encode_response(VECTOR_PAYLOAD, request, "kdfp-test-123") == DOTNET_AES_VECTOR
    )


@pytest.mark.parametrize(
    "payload", [{}, {"value": None}, {"text": "魔物娘 + % 🎲", "items": [], "name": ""}]
)
def test_aes_roundtrip(payload):
    request = DecodedRequest({}, Protocol.AES)
    body = encode_response(payload, request, "秘密の鍵")
    assert decode_request(body, Protocol.AES, "秘密の鍵").payload == payload
    assert encode_response(payload, request, "秘密の鍵") != body


def test_aes_requires_session_password():
    with pytest.raises(ProtocolError, match="password is missing"):
        decode_request(DOTNET_AES_VECTOR, Protocol.AES, None)
    with pytest.raises(ProtocolError, match="password is missing"):
        encode_response({}, DecodedRequest({}, Protocol.AES), None)


@pytest.mark.parametrize(
    "body", [b"", bytes(47), bytes(48), bytes(63), bytes(65), DOTNET_AES_VECTOR[:-1]]
)
def test_aes_rejects_invalid_body_length(body):
    with pytest.raises(ProtocolError, match="body length"):
        decode_request(body, Protocol.AES, "kdfp-test-123")


def test_aes_rejects_corrupt_padding():
    body = bytearray(DOTNET_AES_VECTOR)
    body[-17] ^= 1
    with pytest.raises(ProtocolError, match="padding"):
        decode_request(bytes(body), Protocol.AES, "kdfp-test-123")


def test_aes_rejects_wrong_password():
    with pytest.raises(ProtocolError):
        decode_request(DOTNET_AES_VECTOR, Protocol.AES, "different-password")


def test_rsa_verifies_original_json_text(private_key):
    plaintext = '{\n  "z": "魔物娘 + 100%", "a": null, "items": []\n}'.encode()
    body = rsa_request(private_key, plaintext)
    request = decode_request(body, Protocol.RSA, None)
    assert request.payload == json.loads(plaintext)
    assert request.protocol is Protocol.RSA
    assert (
        request.rsa_public_key.public_numbers()
        == private_key.public_key().public_numbers()
    )


def test_unity_post_form_encoding_matches_native_wrapper():
    frame = b"%7b%22x%22%3a%22a+b%22%7d\nAA+/==\nAQAB"
    expected = b"%257b%2522x%2522%253a%2522a%2bb%2522%257d%0aAA%2b%2f%3d%3d%0aAQAB"
    assert unity_form_encode(frame) == expected


def test_rsa_native_post_body_has_encoded_line_separators(private_key):
    plaintext = b'{"url":"https://local/%25?a=1+b&x=(2)","text":"a b\\nc"}'
    body = rsa_request(private_key, plaintext)
    assert b"\n" not in body
    assert body.count(b"%0a") == 2
    assert body.startswith(b"%257B")
    assert decode_request(body, Protocol.RSA, None).payload == json.loads(plaintext)


def test_rsa_decodes_url_space_without_changing_literal_plus(private_key):
    plaintext = b'{"name": "a + b"}'
    frame = unquote_to_bytes(rsa_request(private_key, plaintext))
    body = unity_form_encode(frame.replace(b"%20", b"+"))
    assert decode_request(body, Protocol.RSA, None).payload == {"name": "a + b"}


@pytest.mark.parametrize("part", ["payload", "nonce", "signature"])
def test_rsa_rejects_tampering(private_key, part):
    lines = unquote_to_bytes(rsa_request(private_key, b'{"value":1}')).split(b"\n")
    if part == "payload":
        lines[0] = quote_from_bytes(b'{"value":2}', safe="").encode()
    else:
        packed = bytearray(base64.b64decode(lines[1]))
        packed[4 if part == "nonce" else -1] ^= 1
        lines[1] = base64.b64encode(packed)
    with pytest.raises(ProtocolError, match="signature"):
        decode_request(unity_form_encode(b"\n".join(lines)), Protocol.RSA, None)


@pytest.mark.parametrize("line_index", [1, 2])
@pytest.mark.parametrize(
    "alteration", ["bad-base64", "short-header", "truncated", "trailing", "empty-field"]
)
def test_rsa_rejects_malformed_field_packs(private_key, line_index, alteration):
    lines = unquote_to_bytes(rsa_request(private_key, b"{}")).split(b"\n")
    packed = base64.b64decode(lines[line_index])
    if alteration == "bad-base64":
        lines[line_index] = b"!invalid!"
    elif alteration == "short-header":
        lines[line_index] = base64.b64encode(packed[:3])
    elif alteration == "truncated":
        lines[line_index] = base64.b64encode(packed[:-1])
    elif alteration == "trailing":
        lines[line_index] = base64.b64encode(packed + b"\0")
    else:
        lines[line_index] = pack_pair(b"", b"value")
    with pytest.raises(ProtocolError):
        decode_request(unity_form_encode(b"\n".join(lines)), Protocol.RSA, None)


def test_rsa_rejects_invalid_public_key(private_key):
    lines = unquote_to_bytes(rsa_request(private_key, b"{}")).split(b"\n")
    lines[2] = pack_pair(b"\x01", b"\x01")
    with pytest.raises(ProtocolError, match="key or nonce"):
        decode_request(unity_form_encode(b"\n".join(lines)), Protocol.RSA, None)


@pytest.mark.parametrize("body", [b"{}", b"a\nb", b"a\nb\nc\nd", b"%GG\nAA==\nAA=="])
def test_rsa_rejects_malformed_body(body):
    with pytest.raises(ProtocolError):
        decode_request(body, Protocol.RSA, None)


def test_rsa_requires_json_object_after_signature_verification(private_key):
    with pytest.raises(ProtocolError, match="must be an object"):
        decode_request(rsa_request(private_key, b"[]"), Protocol.RSA, None)


@pytest.mark.parametrize("key_size", [1024, 2048])
def test_rsa_response_chunks_reassemble_utf8_across_boundaries(key_size):
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    request = decode_request(rsa_request(key, b"{}"), Protocol.RSA, None)
    chunk_size = key_size // 8 - 42
    payload = {"data": "x" * (chunk_size - len(b'{"data":"') - 1) + "魔物娘🎲" * 40}
    encoded = encode_response(payload, request, None)
    assert not encoded.endswith(b"\n")
    cipher_blocks = [
        base64.b64decode(line, validate=True) for line in encoded.split(b"\n")
    ]
    assert len(cipher_blocks) > 1
    assert all(len(block) == key_size // 8 for block in cipher_blocks)
    oaep = padding.OAEP(
        mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None
    )
    plain_blocks = [key.decrypt(block, oaep) for block in cipher_blocks]
    assert all(len(block) == chunk_size for block in plain_blocks[:-1])
    with pytest.raises(UnicodeDecodeError):
        plain_blocks[0].decode("utf-8")
    plaintext = b"".join(plain_blocks)
    assert json.loads(plaintext) == payload
    assert (
        plaintext
        == json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    )


def test_rsa_response_requires_public_key():
    with pytest.raises(ProtocolError, match="public key"):
        encode_response({}, DecodedRequest({}, Protocol.RSA), None)


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        (Protocol.PLAIN, "application/json"),
        (Protocol.AES, "application/octet-stream"),
        (Protocol.RSA, "text/plain; charset=UTF-8"),
    ],
)
def test_content_type_matches_client_transport(protocol, expected):
    assert content_type(protocol) == expected
