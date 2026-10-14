"""Pure site-local routine classification, with conservative ambiguity handling."""
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.enums import TimingClassification


def classify_timing(history: Sequence[datetime], observed: datetime, timezone: ZoneInfo) -> TimingClassification:
    if len(history) < 3:
        return TimingClassification.UNKNOWN
    def minutes(instant: datetime) -> int:
        local = instant.astimezone(timezone)
        return local.hour*60 + local.minute
    ordered = sorted(minutes(instant) for instant in history)
    # Cut at the largest empty arc. The remaining samples have a unique compact
    # wall-clock interpretation even when that routine crosses midnight.
    gaps = [(ordered[(i+1) % len(ordered)] - value) % 1440 for i, value in enumerate(ordered)]
    if ordered[0] == ordered[-1]:
        gaps[-1] = 1440
    cut = max(range(len(gaps)), key=lambda i: (gaps[i], -i))
    start = ordered[(cut+1) % len(ordered)]
    unwrapped = [(value-start) % 1440 for value in ordered]
    if max(unwrapped) >= 360:
        return TimingClassification.UNKNOWN  # Six-hour/multimodal routines are not a reliable baseline.
    baseline = (start + sum(unwrapped)/len(unwrapped)) % 1440
    delta = (minutes(observed)-baseline+720) % 1440 - 720
    if abs(delta) == 720:
        return TimingClassification.UNKNOWN  # Neither early nor late has a unique interpretation.
    if delta < -45:
        return TimingClassification.EARLIER_THAN_USUAL
    if delta > 45:
        return TimingClassification.LATER_THAN_USUAL
    return TimingClassification.NORMAL
