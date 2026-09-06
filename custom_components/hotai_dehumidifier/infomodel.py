"""Parse ExoHome "information models" (the per-family UI definitions the cloud serves at /api:1/info-model).

They tell us, per ESH field, which values a given appliance family accepts and what they mean —
e.g. for Karo's dehumidifiers H0E is 自動/低/中/高 (0-3) and H03 spans 30-80 %. Devices don't
report `fields_range`, so this is the only source of ranges. Everything here is best-effort:
a missing or odd model just means we fall back to the generic SA04 defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldSpec:
    key: str
    title: str = ""
    allowed: dict[int, str] = field(default_factory=dict)  # value -> label
    min: int | None = None
    max: int | None = None
    step: int | None = None


@dataclass
class ModelSpec:
    family: str
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    error_bits: dict[int, str] = field(default_factory=dict)  # H12 bit value -> text

    def spec(self, key: str) -> FieldSpec | None:
        return self.fields.get(key)

    def decode_error(self, code: int | None) -> list[str]:
        if not code:
            return []
        out = [text for bit, text in sorted(self.error_bits.items()) if code & bit]
        return out or [str(code)]


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def pick_model(models: Any, device_model: str) -> dict[str, Any] | None:
    """Find the informationModel whose familyMembers pattern matches this device's model string."""
    if not isinstance(models, dict) or not device_model:
        return None
    for entry in models.values():
        im = entry.get("informationModel") if isinstance(entry, dict) else None
        if not isinstance(im, dict):
            continue
        for pat in im.get("familyMembers") or []:
            try:
                if re.match(str(pat), device_model):
                    return im
            except re.error:
                continue
    return None


def parse_model(im: dict[str, Any]) -> ModelSpec:
    spec = ModelSpec(family=str(im.get("familyName") or ""))
    for comp in (im.get("components") or {}).values():
        if not isinstance(comp, dict):
            continue
        title = str(comp.get("title") or "")
        for m in comp.get("models") or []:
            if not isinstance(m, dict) or "key" not in m:
                continue
            key = str(m["key"])
            fs = spec.fields.setdefault(key, FieldSpec(key=key, title=title))
            if not fs.title:
                fs.title = title
            values = m.get("values")
            if isinstance(values, list):
                for v in values:
                    if not isinstance(v, dict):
                        continue
                    iv = _int(v.get("value"))
                    if iv is None:  # "*" wildcard rows carry no value
                        continue
                    fs.allowed.setdefault(iv, str(v.get("text") or iv))
            elif isinstance(values, dict):
                lo, hi, st = _int(values.get("min")), _int(values.get("max")), _int(values.get("step"))
                if lo is not None:
                    fs.min = lo if fs.min is None else min(fs.min, lo)
                if hi is not None:
                    fs.max = hi if fs.max is None else max(fs.max, hi)
                if st is not None:
                    fs.step = st
    for ef in im.get("errorFields") or []:
        if isinstance(ef, dict) and ef.get("key") == "H12":
            for v in ef.get("values") or []:
                iv = _int(v.get("value")) if isinstance(v, dict) else None
                if iv:
                    spec.error_bits[iv] = str(v.get("text") or iv)
    # The H12 component itself also carries texts (incl. value 0 = 正常); use them to fill gaps.
    h12 = spec.fields.get("H12")
    if h12:
        for iv, text in h12.allowed.items():
            if iv and iv not in spec.error_bits:
                spec.error_bits[iv] = text
    return spec
