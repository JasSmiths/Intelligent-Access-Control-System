"""Pure plate candidate ranking and debounce grouping, independent of worker I/O."""

from difflib import SequenceMatcher
from typing import Any

from app.modules.lpr.base import PlateRead
from app.services.access.reads import DebounceWindow
from app.services.movement.sessions import normalize_registration_number, plates_are_similar


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


def add_debounce_read(
    pending: list[DebounceWindow],
    read: PlateRead,
    threshold: float,
) -> DebounceWindow:
    for window in pending:
        best = window.best_read
        if read.source == best.source and plates_are_similar(
            read.registration_number,
            best.registration_number,
            threshold,
        ):
            window.reads.append(read)
            window.updated_at = read.captured_at
            return window
    window = DebounceWindow(first_seen=read.captured_at, updated_at=read.captured_at, reads=[read])
    pending.append(window)
    return window
