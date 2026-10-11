"""Pure plate candidate ranking and debounce grouping, independent of worker I/O."""

from difflib import SequenceMatcher
from typing import Any

from app.services.movement.sessions import normalize_registration_number


def match_candidates(
    candidates: tuple[str, ...],
    registrations: list[str],
    threshold: float,
) -> dict[str, Any] | None:
    # Normalize stored plates once across all candidate comparisons.
    stored = [
        (str(value).strip().upper().replace(" ", ""), normalize_registration_number(value))
        for value in registrations
    ]
    best: dict[str, Any] | None = None
    best_rank: tuple[bool, float, int, str] | None = None
    for index, candidate in enumerate(candidates):
        detected = normalize_registration_number(candidate)
        if not detected:
            continue
        for lookup, normalized in stored:
            if not normalized:
                continue
            exact = detected == normalized
            similarity = 1.0 if exact else SequenceMatcher(a=detected, b=normalized).ratio()
            if not exact and similarity < threshold:
                continue
            rank = (exact, similarity, -index, lookup or normalized)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = {
                    "detected_registration_number": detected,
                    "registration_number": lookup or normalized,
                    "normalized_registration_number": normalized,
                    "similarity": similarity,
                    "threshold": threshold,
                    "exact": exact,
                }
    return best
