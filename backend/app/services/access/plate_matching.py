"""Lossless plate candidate filtering and ranking, independent of database I/O."""

from collections import Counter
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import TypedDict

from app.services.movement.sessions import normalize_registration_number


class PlateMatch(TypedDict):
    detected_registration_number: str
    registration_number: str
    normalized_registration_number: str
    similarity: float
    threshold: float
    exact: bool


def fuzzy_registration_pattern(candidates: tuple[str, ...], threshold: float) -> str:
    """Necessary overlap for *any* candidate. Input plates are normalized ASCII."""
    filters: list[tuple[str, int]] = []
    for candidate in candidates:
        length = len(candidate)
        # With m matched characters the stored plate has length >= m. Even the
        # shortest possible supporting plate scores at most 2*m/(length + m).
        maximum = min(length, 32)  # Vehicle.registration_number is VARCHAR(32).
        minimum = next((m for m in range(maximum + 1) if 2 * m / (length + m) >= threshold), 33)
        if minimum > maximum:
            continue
        counts = Counter(candidate)
        char, frequency = counts.most_common(1)[0]
        required_repeats = minimum - (length - frequency)
        if required_repeats > 1:
            filters.append((char, required_repeats))
        else:
            filters.append(("".join(sorted(counts)), minimum))
    # Keep the database expression bounded for unusually large OCR candidate
    # sets. The union is a looser necessary condition and drops no candidates.
    if not filters:
        return "(?!)"  # No stored plate can reach the threshold.
    if len(filters) > 8:
        filters = [("".join(sorted(set("".join(chars for chars, _ in filters)))), min(n for _, n in filters))]
    patterns = []
    for chars, minimum in filters:
        if minimum == 0:
            return ""
        letters = chars + "".join(char.lower() for char in chars if char.isalpha())
        patterns.append(f"^([^{letters}]*[{letters}]){{{minimum}}}")
    return "|".join(dict.fromkeys(patterns))


def match_candidates(
    candidates: tuple[str, ...], registrations: Iterable[str], threshold: float,
    best: PlateMatch | None = None,
) -> PlateMatch | None:
    detected_plates = tuple(normalize_registration_number(candidate) for candidate in candidates)
    best_rank = None if best is None else (
        best["exact"], best["similarity"], -detected_plates.index(best["detected_registration_number"]),
        best["registration_number"],
    )
    for value in registrations:
        normalized = normalize_registration_number(value)
        if not normalized:
            continue
        for index, detected in enumerate(detected_plates):
            if not detected:
                continue
            exact = detected == normalized
            similarity = 1.0
            if not exact:
                if best_rank and best_rank[0]:
                    continue
                matcher = SequenceMatcher(a=detected, b=normalized)
                cutoff = max(threshold, best_rank[1]) if best_rank else threshold
                # Character multiset overlap bounds the matching subsequences.
                if matcher.real_quick_ratio() < cutoff or matcher.quick_ratio() < cutoff:
                    continue
                similarity = matcher.ratio()
                if similarity < cutoff:
                    continue
            lookup = str(value).strip().upper().replace(" ", "") or normalized
            rank = (exact, similarity, -index, lookup)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = {
                    "detected_registration_number": detected,
                    "registration_number": lookup,
                    "normalized_registration_number": normalized,
                    "similarity": similarity, "threshold": threshold, "exact": exact,
                }
    return best
