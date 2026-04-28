"""Phonetic wording helpers for text destined for TTS engines."""

from __future__ import annotations

import re


VEHICLE_TTS_PHONETICS: dict[str, str] = {
    "BMW": "bee em double you",
    "BYD": "bee why dee",
    "GMC": "gee em see",
    "MG": "em gee",
    "VW": "vee double you",
    "DS": "dee ess",
}

_VEHICLE_TTS_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(value) for value in sorted(VEHICLE_TTS_PHONETICS, key=len, reverse=True)) + r")\b"
)


def apply_vehicle_tts_phonetics(message: str) -> str:
    """Replace standalone vehicle acronyms with TTS-friendly wording."""
    if not message:
        return message
    return _VEHICLE_TTS_PATTERN.sub(lambda match: VEHICLE_TTS_PHONETICS[match.group(1)], message)
