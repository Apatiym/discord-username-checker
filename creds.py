# -*- coding: utf-8 -*-
"""Reads the Discord token straight out of the client folder.

How the Electron client stores it:
  * <AppData>\\discord\\Local State -> os_crypt.encrypted_key (base64, DPAPI prefix)
  * DPAPI (CryptUnprotectData) -> 32-byte AES-256-GCM key
  * <AppData>\\discord\\Local Storage\\leveldb\\*.ldb|*.log -> "dQw4w9WgXcQ:v10..."
    which is the encrypted token.

Standard library + ctypes only, no pip install. AES-256-GCM is implemented
below because the standard library does not expose it.
"""

import base64
import ctypes
import json
import os
import re

DPAPI_PREFIX = b"DPAPI"
ENC_RE = re.compile(rb"dQw4w9WgXcQ:([A-Za-z0-9+/=]{40,})")
# old/new plain token format, if the client ever stores it unencrypted
PLAIN_RE = re.compile(rb"[\w-]{24,28}\.[\w-]{6}\.[\w-]{25,40}")


# ---------------- AES-256-GCM (decrypt only) ----------------

SBOX = (
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
)

RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a):
    a <<= 1
    return (a ^ 0x11B) & 0xFF if a & 0x100 else a


class AES:
    """Block encryption only - that is all GCM needs."""

    def __init__(self, key):
        if len(key) not in (16, 24, 32):
            raise ValueError("key must be 16/24/32 bytes")
        self.rounds = len(key) // 4 + 6
        self.round_keys = self._expand(key)

    def _expand(self, key):
        nk = len(key) // 4
        total = 4 * (self.rounds + 1)
        w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
        for i in range(nk, total):
            temp = list(w[i - 1])
            if i % nk == 0:
                temp = temp[1:] + temp[:1]
                temp = [SBOX[b] for b in temp]
                temp[0] ^= RCON[i // nk - 1]
            elif nk > 6 and i % nk == 4:
                temp = [SBOX[b] for b in temp]
            w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
        return [sum(w[4 * r:4 * r + 4], []) for r in range(self.rounds + 1)]

    @staticmethod
    def _mix_column(a):
        t = a[0] ^ a[1] ^ a[2] ^ a[3]
        return [
            a[0] ^ t ^ _xtime(a[0] ^ a[1]),
            a[1] ^ t ^ _xtime(a[1] ^ a[2]),
            a[2] ^ t ^ _xtime(a[2] ^ a[3]),
            a[3] ^ t ^ _xtime(a[3] ^ a[0]),
        ]

    def encrypt_block(self, block):
        s = [b ^ k for b, k in zip(block, self.round_keys[0])]
        for r in range(1, self.rounds):
            s = [SBOX[b] for b in s]
            s = [s[4 * ((c + row) % 4) + row] for c in range(4) for row in range(4)]
            s = sum((self._mix_column(s[4 * c:4 * c + 4]) for c in range(4)), [])
            s = [b ^ k for b, k in zip(s, self.round_keys[r])]
        s = [SBOX[b] for b in s]
        s = [s[4 * ((c + row) % 4) + row] for c in range(4) for row in range(4)]
        return bytes(b ^ k for b, k in zip(s, self.round_keys[self.rounds]))


def _gf_mul(x, y):
    z, v = 0, y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        v = (v >> 1) ^ 0xE1000000000000000000000000000000 if v & 1 else v >> 1
    return z


def _ghash(h, data):
    y = 0
    for i in range(0, len(data), 16):
        block = data[i:i + 16].ljust(16, b"\x00")
        y = _gf_mul(y ^ int.from_bytes(block, "big"), h)
    return y


def _pad16(data):
    return data + b"\x00" * (-len(data) % 16)


def gcm_decrypt(key, blob, aad=b""):
    """blob = v10/nonce(12)/ct/tag(16). Returns plaintext, or None if the tag fails."""
    if blob[:3] not in (b"v10", b"v11"):
        return None
    body = blob[3:]
    if len(body) < 12 + 16:
        return None
    nonce, rest = body[:12], body[12:]
    ct, tag = rest[:-16], rest[-16:]

    aes = AES(key)
    h = int.from_bytes(aes.encrypt_block(b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"

    s = _ghash(h, _pad16(aad) + _pad16(ct)
               + (len(aad) * 8).to_bytes(8, "big")
               + (len(ct) * 8).to_bytes(8, "big"))
    calc = bytes(a ^ b for a, b in zip(aes.encrypt_block(j0), s.to_bytes(16, "big")))
    if calc != tag:
        return None

    out = bytearray()
    counter = int.from_bytes(j0[12:], "big")
    for i in range(0, len(ct), 16):
        counter = (counter + 1) & 0xFFFFFFFF
        ks = aes.encrypt_block(j0[:12] + counter.to_bytes(4, "big"))
        out += bytes(a ^ b for a, b in zip(ct[i:i + 16], ks))
    return bytes(out)


# ---------------- DPAPI ----------------

def _blob_type():
    """The DATA_BLOB struct, built lazily: ctypes.wintypes is Windows-only,
    and this module has to stay importable on any platform (CI, tests)."""
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    return _BLOB


def dpapi_unprotect(data):
    """Decrypts data protected by Windows DPAPI for the current user."""
    if os.name != "nt":
        raise OSError("DPAPI is Windows-only")
    blob_type = _blob_type()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = blob_type(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = blob_type()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def master_key(data_dir):
    """Reads and decrypts the AES key from Local State."""
    path = os.path.join(data_dir, "Local State")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        state = json.load(f)
    enc = state.get("os_crypt", {}).get("encrypted_key")
    if not enc:
        return None
    raw = base64.b64decode(enc)
    if raw.startswith(DPAPI_PREFIX):
        raw = raw[len(DPAPI_PREFIX):]
    key = dpapi_unprotect(raw)
    return key if len(key) in (16, 24, 32) else None


# ---------------- token search ----------------

def data_dirs(hint=None):
    """Client data folders (stable / PTB / Canary and anything similar)."""
    out = []
    roaming = os.environ.get("APPDATA", "")

    if hint:
        # an install folder (...\AppData\Local\Discord) has no leveldb; the data
        # lives next door in Roaming, so the sibling must be tried too
        if os.path.isdir(os.path.join(hint, "Local Storage", "leveldb")):
            out.append(hint)
        name = os.path.basename(os.path.normpath(hint)).lower()
        sibling = os.path.join(roaming, name)
        if os.path.isdir(sibling):
            out.append(sibling)

    for name in ("discord", "discordptb", "discordcanary", "discorddevelopment"):
        cand = os.path.join(roaming, name)
        if os.path.isdir(cand):
            out.append(cand)

    if roaming and os.path.isdir(roaming):
        for name in os.listdir(roaming):
            cand = os.path.join(roaming, name)
            if name.lower().startswith("discord") and os.path.isdir(
                os.path.join(cand, "Local Storage", "leveldb")
            ):
                out.append(cand)

    seen, uniq = set(), []
    for d in out:
        d = os.path.normpath(d)
        if d.lower() not in seen and os.path.isdir(os.path.join(d, "Local Storage", "leveldb")):
            seen.add(d.lower())
            uniq.append(d)
    return uniq


def _read_leveldb(data_dir):
    leveldb = os.path.join(data_dir, "Local Storage", "leveldb")
    chunks = []
    try:
        names = os.listdir(leveldb)
    except OSError:
        return b""
    for name in sorted(names):
        if not name.endswith((".ldb", ".log")):
            continue
        try:
            with open(os.path.join(leveldb, name), "rb") as f:
                chunks.append(f.read())
        except OSError:
            continue  # the client holds the file open - skip it
    return b"\n".join(chunks)


def extract_tokens(data_dir, key=None):
    """Returns [(token, how_it_was_found)] from the client folder."""
    blob = _read_leveldb(data_dir)
    if not blob:
        return []

    found, seen = [], set()

    for match in ENC_RE.finditer(blob):
        try:
            enc = base64.b64decode(match.group(1))
        except Exception:
            continue
        if not key:
            continue
        plain = gcm_decrypt(key, enc)
        if plain and 20 < len(plain) < 200:
            token = plain.decode("utf-8", "ignore").strip()
            if token and token not in seen:
                seen.add(token)
                found.append((token, "decrypted (DPAPI + AES-GCM)"))

    for match in PLAIN_RE.finditer(blob):
        token = match.group(0).decode("utf-8", "ignore")
        if token not in seen:
            seen.add(token)
            found.append((token, "plain text"))

    return found


def collect(hint=None, log=print):
    """Looks for tokens in every folder found. -> [(token, dir, how)]"""
    result = []
    dirs = data_dirs(hint)
    if not dirs:
        log("  Discord data folder not found")
        return result

    for d in dirs:
        log(f"  scanning: {d}")
        try:
            key = master_key(d)
        except OSError as exc:
            log(f"    key not available: {exc}")
            key = None
        tokens = extract_tokens(d, key)
        if not tokens:
            log("    no tokens found")
        for token, how in tokens:
            log(f"    found: {how}")
            result.append((token, d, how))
    return result
