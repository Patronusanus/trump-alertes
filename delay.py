"""Calcul du délai entre l'opération de Trump et la notification."""
from __future__ import annotations

from datetime import date, datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_seconds(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec} s"
    m = sec // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} h {m:02d}" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} j {h} h" if d < 7 and h else f"{d} jours"


def since_timestamp(iso: str) -> str:
    """Délai depuis un horodatage précis (blockchain, Truth Social)."""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return fmt_seconds((now() - t).total_seconds())


def days_since(d: date | None) -> int | None:
    if d is None:
        return None
    return (now().date() - d).days


def parse_day(s: str) -> date | None:
    """Accepte AAAA-MM-JJ ou JJ/MM/AAAA."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def fmt_days(n: int | None) -> str:
    if n is None:
        return "?"
    if n <= 0:
        return "aujourd'hui"
    if n == 1:
        return "1 jour"
    return f"{n} jours"


def line_for_days(days: list[int]) -> str:
    """Résumé « ⏱️ Délai » pour un groupe d'opérations datées au jour près."""
    days = [d for d in days if d is not None]
    if not days:
        return "⏱️ Délai : date de l'opération illisible"
    lo, hi = min(days), max(days)
    span = fmt_days(lo) if lo == hi else f"{lo} à {hi} jours"
    return f"⏱️ <b>Délai : {span}</b> entre l'ordre et cette notification"
