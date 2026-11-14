"""拉取版本信息 + ablist + MasterData 并解密导出，验证资产管线。"""
import hashlib
import json
import os
import sys

import requests
import UnityPy
from UnityPy.enums import ClassIDType

APP_INFO_URL = "https://api.store.games.dmm.com/freeapp/688044"
VERSION_API = "https://gapi.game-monmusu-td.net/api/asset_bundle/version"
CDN = "https://assets.game-monmusu-td.net/assetbundles"
PLATFORM = "windows_r18"
OUT = os.path.join(os.path.dirname(__file__), "..", "masterdata")

MASTER_FILES = {
    "a4a197b8ff3816f0ddf353ead4768c80.bytes": "Data",
    "75e09e20429de1372f375685aa4386b3.bytes": "Bomcard",
    "2c4638ec28d824dfc4992d2f2b492c1f.bytes": "UnitClass",
    "94840ed598854fe89a08f12ceb25fd1e.bytes": "UnitCard",
    "6ccdbcdbe6ce4a57abcd31247745c242.bytes": "EnemyData",
}


def base_key(src: str) -> bytes:
    s2 = bytes(b ^ 0x55 for b in src.encode("ascii"))
    sha = hashlib.sha256(s2).digest()
    s4 = bytes(b ^ 0xAA for b in sha)
    return s4[::2] + s4[1::2]


def decrypt_table(data: bytes) -> bytes:
    k32 = base_key("KYSSTMDL")
    key = k32 + k32[::-1]
    klen = len(key)
    out = bytearray(len(data))
    for i in range(0, len(data), klen):
        chunk = data[i : i + klen]
        out[i : i + len(chunk)] = bytes(b ^ key[j] for j, b in enumerate(chunk))
    return bytes(out)


def export_bundle(raw: bytes, out_dir: str) -> list[str]:
    env = UnityPy.load(raw)
    names = []
    cmap = {obj.path_id: p for p, obj in env.container.items()}
    for obj in env.objects:
        if obj.type != ClassIDType.MonoBehaviour:
            continue
        tree = obj.read_typetree()
        name = tree.get("m_Name", f"pid_{obj.path_id}")
        rel = os.path.dirname(cmap.get(obj.path_id, "").replace("\\", "/")).lstrip("/")
        d = os.path.normpath(os.path.join(out_dir, rel))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=1)
        names.append(name)
    return names


def main():
    s = requests.Session()
    cvr = s.get(APP_INFO_URL, timeout=15).json()["free_appinfo"]["app_version_name"]
    print("cvr:", cvr)
    r = s.post(VERSION_API, json={"cvr": cvr, "provider": "dmm"}, timeout=15)
    print("version api status:", r.status_code)
    ver = r.json()["data"]["version"]
    print("bundle version:", ver)

    ablist = s.get(f"{CDN}/ver_{ver}/{PLATFORM}/ablist.json", timeout=30).json()
    base_ver = ablist["baseVersion"]
    print("baseVersion:", base_ver, "entries:", len(ablist["data"]))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "ablist.json"), "w", encoding="utf-8") as f:
        json.dump(ablist, f, ensure_ascii=False)

    base_url = f"{CDN}/ver_{base_ver}/{PLATFORM}"
    for a in ablist["data"]:
        label = MASTER_FILES.get(a["path"])
        if not label:
            continue
        blob = s.get(f"{base_url}/{a['hash']}{a['path']}", timeout=60).content
        assert len(blob) == int(a["size"]), f"{label}: size mismatch"
        dec = decrypt_table(blob)
        sig = dec[:7]
        names = export_bundle(dec, OUT)
        print(f"{label}: {len(blob)}B sig={sig} tables={len(names)} -> {names[:6]}")


if __name__ == "__main__":
    main()
