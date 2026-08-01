#!/usr/bin/env python3
import base64
from pathlib import Path

for name in ("static", "runtime"):
    source = Path(f".staging-refactor/patch-hy2-{name}.gz.b64")
    raw = source.read_text(encoding="utf-8-sig")
    clean = "".join(raw.split())
    try:
        data = base64.b64decode(clean, validate=True)
    except Exception as exc:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        invalid = sorted({f"U+{ord(ch):04X}" for ch in clean if ch not in alphabet})
        raise SystemExit(
            f"{name} Base64 decode failed: {exc}; "
            f"length={len(clean)}; invalid={invalid[:20]}"
        )
    Path(f"/tmp/hy2-{name}.gz").write_bytes(data)
    print(f"{name}: chars={len(clean)} bytes={len(data)}")
