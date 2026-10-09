"""CGM summary metrics: the numbers an AGP report prints and the curves it draws.

Pure functions over a sensor's readings `[(local time, mmol/L)]`; nothing here draws a value. The report
renderer (`render/agp.py`) prints what these return, and the truth records the same numbers, so an
extractor reading a report is scored against the arithmetic, not against the renderer.

- Ranges, mean, SD, CV and GMI follow the International Consensus on Time in Range (Battelino et al.,
  Diabetes Care 2019;42:1593) and Bergenstal et al. (Diabetes Care 2018;41:2275).
- The ambulatory glucose profile is the 5th/25th/50th/75th/95th percentile of every reading falling in
  each time-of-day bin across the days worn, lightly smoothed around the clock (the AGP's own
  presentation; bin width and smoothing are ours and stated in `profile`).
- MAGE (mean amplitude of glycaemic excursions; Service et al., Diabetes 1970;19:644): the mean of the
  peak-to-nadir swings larger than one SD, in the direction of the first such swing. Turning points are
  found with an SD hysteresis (a swing counts once the trace has moved one SD back from its extreme),
  the usual automation of Service's hand method.
- MODD (mean of daily differences; Molnar et al., Diabetologia 1972;8:342): the mean absolute
  difference between readings 24 hours apart.
- LAGE (largest amplitude of glycaemic excursions): the highest reading minus the lowest.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

MGDL_PER_MMOL = 18.0156
#: Consensus bands, mmol/L: very low < 3.0 ≤ low < 3.9 ≤ target ≤ 10.0 < high ≤ 13.9 < very high.
BANDS = (("very_low", None, 3.0), ("low", 3.0, 3.9), ("target", 3.9, 10.0), ("high", 10.0, 13.9),
         ("very_high", 13.9, None))
PERCENTILES = (5, 25, 50, 75, 95)


def band_of(v: float) -> str:
    if v < 3.0:
        return "very_low"
    if v < 3.9:
        return "low"
    if v <= 10.0:
        return "target"
    if v <= 13.9:
        return "high"
    return "very_high"


def ranges(values: list[float]) -> dict[str, float]:
    """Percent of readings in each consensus band, unrounded: a report rounds once, when it prints (rounding
    here first turned a 99.47% time in range into a printed 100%)."""
    n = max(len(values), 1)
    counts = {name: 0 for name, _, _ in BANDS}
    for v in values:
        counts[band_of(v)] += 1
    return {k: 100 * c / n for k, c in counts.items()}


def basic(values: list[float]) -> dict[str, float]:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    return {"mean_mmol": mean, "sd_mmol": sd, "cv_pct": 100 * sd / mean,
            "gmi_pct": 3.31 + 0.02392 * mean * MGDL_PER_MMOL, "min_mmol": min(values), "max_mmol": max(values)}


def percentile(sorted_values: list[float], p: float) -> float:
    """Linear interpolation between closest ranks (the definition numpy and most CGM software use)."""
    if not sorted_values:
        return math.nan
    k = (len(sorted_values) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def profile(points: list[tuple[datetime, float]], bin_min: int = 15, smooth: int = 1,
            percentiles: tuple[int, ...] = PERCENTILES) -> list[dict]:
    """The AGP: for each `bin_min`-minute bin of the day, the given percentiles (5/25/50/75/95 by default;
    some apps draw 10/90) of all readings in it across days, then a circular moving average over ±`smooth`
    bins so the bands read as curves."""
    bins: list[list[float]] = [[] for _ in range(1440 // bin_min)]
    for t, v in points:
        bins[(t.hour * 60 + t.minute) // bin_min].append(v)
    raw = []
    for b in bins:
        s = sorted(b)
        raw.append([percentile(s, p) if s else math.nan for p in percentiles])
    n = len(raw)
    out = []
    for i in range(n):
        row = []
        for j in range(len(percentiles)):
            window = [raw[(i + k) % n][j] for k in range(-smooth, smooth + 1)]
            window = [w for w in window if not math.isnan(w)]
            row.append(sum(window) / len(window) if window else math.nan)
        out.append({"minute": i * bin_min, **{f"p{p}": round(row[j], 2) for j, p in enumerate(percentiles)}})
    return out


def share(values: list[float], lo: float | None, hi: float | None, lo_incl: bool = True, hi_incl: bool = True) -> float:
    """Percent of readings between `lo` and `hi` (None: unbounded), each end inclusive or not, unrounded."""
    def inside(v: float) -> bool:
        return ((lo is None or (v >= lo if lo_incl else v > lo)) and (hi is None or (v <= hi if hi_incl else v < hi)))

    return 100 * sum(1 for v in values if inside(v)) / max(len(values), 1)


def events(points: list[tuple[datetime, float]], threshold: float, below: bool, interval_min: int,
           min_minutes: int = 15) -> list[float]:
    """Durations (minutes) of glucose events: at least `min_minutes` beyond the threshold, ending after at
    least `min_minutes` back (the consensus definition of a CGM event). A gap longer than two readings breaks
    a run."""
    beyond = (lambda v: v < threshold) if below else (lambda v: v > threshold)
    runs: list[list[datetime]] = []
    last = None
    for t, v in sorted(points):
        if beyond(v):
            if runs and last is not None and (t - last).total_seconds() <= 2 * interval_min * 60 and runs[-1][1] == last:
                runs[-1][1] = t
            else:
                runs.append([t, t])
        last = t
    merged: list[list[datetime]] = []
    for a, b in runs:
        if merged and (a - merged[-1][1]).total_seconds() < min_minutes * 60:
            merged[-1][1] = b                          # back for less than 15 min: the same event
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        minutes = (b - a).total_seconds() / 60 + interval_min
        if minutes >= min_minutes:
            out.append(minutes)
    return out


def lbgi(values: list[float]) -> float:
    """Low blood glucose index (Kovatchev et al., Diabetes Care 1998;21:1870): the mean of 10·f² over readings
    with f < 0, f = 1.509 · ((ln mg/dL)^1.084 − 5.381)."""
    total = 0.0
    for v in values:
        f = 1.509 * (math.log(max(v * MGDL_PER_MMOL, 1.0)) ** 1.084 - 5.381)
        if f < 0:
            total += 10 * f * f
    return total / max(len(values), 1)


def by_day(points: list[tuple[datetime, float]]) -> dict[date, list[tuple[datetime, float]]]:
    out: dict[date, list[tuple[datetime, float]]] = {}
    for t, v in points:
        out.setdefault(t.date(), []).append((t, v))
    return out


def mage(values: list[float], sd: float | None = None, smooth: int = 3) -> float | None:
    """Service's MAGE over one trace (typically a day): swings larger than one SD, averaged in the direction
    of the first one. None when the trace has no complete swing that large.

    The trace is first smoothed with a `smooth`-point moving average (sensor noise otherwise creates
    one-reading "excursions"; Baghurst, Diabetes Technol Ther 2011;13:296, automates MAGE the same way).
    Only swings between two confirmed turning points count: the start and end of a trace are not peaks
    or nadirs, and counting the half-swings there biased a clean sine wave's MAGE low by 5%."""
    if len(values) < 3:
        return None
    if sd is None:
        m = sum(values) / len(values)
        sd = math.sqrt(sum((v - m) ** 2 for v in values) / len(values))
    if sd <= 0:
        return None
    if smooth > 1:
        h = smooth // 2
        values = [sum(values[max(0, i - h):i + h + 1]) / len(values[max(0, i - h):i + h + 1])
                  for i in range(len(values))]
    swings: list[float] = []          # signed: + a rise (nadir to peak), − a fall (peak to nadir)
    direction = 0                     # 0 until the first turning point is confirmed
    lo = hi = values[0]
    lo_i = hi_i = 0
    real = False                      # the last confirmed extreme is interior (the first sample is not one)
    for i, v in enumerate(values[1:], start=1):
        if direction >= 0 and v > hi:
            hi, hi_i = v, i
        if direction <= 0 and v < lo:
            lo, lo_i = v, i
        if direction >= 0 and hi - v >= sd:        # a peak at `hi` is confirmed
            if direction > 0 and real:
                swings.append(hi - lo)
            direction, lo, lo_i, real = -1, v, i, hi_i > 0
        elif direction <= 0 and v - lo >= sd:      # a nadir at `lo` is confirmed
            if direction < 0 and real:
                swings.append(-(hi - lo))
            direction, hi, hi_i, real = 1, v, i, lo_i > 0
    if not swings:
        return None
    first = swings[0] > 0
    chosen = [abs(s) for s in swings if (s > 0) == first]
    return sum(chosen) / len(chosen)


def mage_daily_mean(points: list[tuple[datetime, float]], min_share: float = 0.7, interval_min: int = 5) -> float | None:
    """MAGE per complete day (at least `min_share` of its readings present), averaged over days."""
    per_day = []
    expected = 1440 / interval_min
    for _, pts in sorted(by_day(points).items()):
        if len(pts) >= min_share * expected:
            m = mage([v for _, v in sorted(pts)])
            if m is not None:
                per_day.append(m)
    return sum(per_day) / len(per_day) if per_day else None


def modd(points: list[tuple[datetime, float]], interval_min: int) -> float | None:
    """Mean absolute difference between each reading and the one 24 h later (within half an interval)."""
    tol = timedelta(minutes=interval_min / 2)
    by_minute: dict[int, list[tuple[datetime, float]]] = {}
    for t, v in points:
        by_minute.setdefault(int(t.timestamp() // 60), []).append((t, v))
    diffs = []
    for t, v in points:
        target = t + timedelta(days=1)
        best = None
        for m in range(int((target - tol).timestamp() // 60), int((target + tol).timestamp() // 60) + 1):
            for t2, v2 in by_minute.get(m, []):
                if abs(t2 - target) <= tol and (best is None or abs(t2 - target) < abs(best[0] - target)):
                    best = (t2, v2)
        if best is not None:
            diffs.append(abs(best[1] - v))
    return sum(diffs) / len(diffs) if diffs else None


def lage(values: list[float]) -> float | None:
    return max(values) - min(values) if values else None
