"""Lecture des formulaires OGE 278-T (déclarations de transactions).

Les PDF publiés par l'OGE pour Donald Trump sont des scans (images).
On passe donc par l'OCR (Tesseract) :
  1. chaque page est convertie en image ;
  2. on repère les lignes du tableau (traits horizontaux et verticaux) ;
  3. on lit chaque colonne séparément puis on reconstruit chaque ligne ;
  4. les montants sont « recalés » sur les fourchettes officielles de l'OGE,
     ce qui corrige la plupart des erreurs de lecture.
Si le PDF contient du texte (pas un scan), on lit directement le texte.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

# Fourchettes officielles (formulaire OGE 278), en dollars.
AMOUNT_RANGES = [
    (1_001, 15_000),
    (15_001, 50_000),
    (50_001, 100_000),
    (100_001, 250_000),
    (250_001, 500_000),
    (500_001, 1_000_000),
    (1_000_001, 5_000_000),
    (5_000_001, 25_000_000),
    (25_000_001, 50_000_000),
    (50_000_001, None),  # "Over $50,000,000"
]

DATE_RE = re.compile(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4}|\d{2})")


@dataclass
class Transaction:
    number: str
    description: str
    kind: str  # "achat", "vente", "vente partielle", "échange", "?"
    date: str  # JJ/MM/AAAA ou ""
    amount_min: int | None
    amount_max: int | None
    late: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def amount_label(self) -> str:
        if self.amount_min is None:
            return "montant illisible"
        if self.amount_max is None:
            return f"plus de {fmt_usd(self.amount_min - 1)}"
        return f"{fmt_usd(self.amount_min)} – {fmt_usd(self.amount_max)}"


def fmt_usd(v: int) -> str:
    return f"{v:,}".replace(",", " ") + " $"


# ---------------------------------------------------------------- champs


def normalize_kind(text: str) -> str:
    t = text.lower().replace(" ", "")
    t = t.translate(str.maketrans({"0": "o", "1": "l", "5": "s", "|": "l", "€": "e"}))
    if "partial" in t or "partiel" in t:
        return "vente partielle"
    if "exch" in t or "xchan" in t:
        return "échange"
    # "purchase" est souvent lu "purchaso", "purenaso", "perenese"...
    if t.startswith("p") or "urch" in t or "chas" in t or "rchase" in t or "naso" in t:
        return "achat"
    if t.startswith("s") or "ale" in t or "sal" in t or "lea" in t or "aio" in t or "sat" in t:
        return "vente"
    return "?"


def normalize_date(text: str) -> str:
    m = DATE_RE.search(text.replace(" ", ""))
    if not m:
        return ""
    mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(y) == 2:
        y = "20" + y
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{d:02d}/{mo:02d}/{y}"


def normalize_amount(text: str) -> tuple[int | None, int | None]:
    """Recale un montant lu par OCR sur une fourchette officielle."""
    t = text.lower()
    if "over" in t or "plus" in t:
        digits = re.sub(r"[^\d]", "", t)
        if digits.startswith("50") or len(digits) >= 8:
            return AMOUNT_RANGES[-1]
        if digits.startswith("1"):
            return (1_000_001, None)  # « Over $1,000,000 » (conjoint / enfant)
    # On s'intéresse surtout à la borne haute (plus facile à lire) :
    parts = re.split(r"[-–~=+]|\s-\s", text)
    nums = [re.sub(r"[^\d]", "", p) for p in parts]
    nums = [n for n in nums if n]
    candidates: list[tuple[int, int | None]] = []
    if nums:
        hi = nums[-1]
        for lo_v, hi_v in AMOUNT_RANGES:
            if hi_v is not None and str(hi_v) == hi.lstrip("0"):
                return (lo_v, hi_v)
        # comparaison souple : même nombre de chiffres et même premier chiffre
        for lo_v, hi_v in AMOUNT_RANGES:
            if hi_v is None:
                continue
            s = str(hi_v)
            if len(hi) == len(s) and hi[0] == s[0]:
                candidates.append((lo_v, hi_v))
        if len(candidates) == 1:
            return candidates[0]
        # borne basse : 1001, 15001, 50001...
        lo = nums[0]
        for lo_v, hi_v in AMOUNT_RANGES:
            if str(lo_v) == lo.lstrip("0") or (len(nums) == 1 and str(lo_v) == lo):
                return (lo_v, hi_v)
        if candidates:
            return candidates[0]
    return (None, None)


# ---------------------------------------------------------------- OCR


def _runs(mask_1d: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate(([0], mask_1d.astype(np.int8), [0])))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _group(positions: list[int], gap: int = 6) -> list[int]:
    groups: list[list[int]] = []
    for p in positions:
        if groups and p - groups[-1][-1] <= gap:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [int(np.mean(g)) for g in groups]


def find_grid(gray: np.ndarray) -> tuple[list[int], list[int]]:
    """Renvoie (lignes horizontales, lignes verticales) du tableau."""
    dark = gray < 150
    h, w = dark.shape
    # traits horizontaux : beaucoup de pixels sombres sur la ligne
    row_frac = dark.mean(axis=1)
    rows = _group([y for y in range(h) if row_frac[y] > 0.35])
    if len(rows) < 3:
        return rows, []
    # Trait vertical = colonne de pixels qui traverse (presque) toute la hauteur
    # de chaque ligne du tableau. On tolère un léger décalage (scan penché)
    # en regardant une fenêtre de ±2 px.
    bands = [(a, b) for a, b in zip(rows[:-1], rows[1:]) if b - a > 15]
    score = np.zeros(w)
    for a, b in bands:
        seg = dark[a + 4 : b - 3]
        if seg.size == 0:
            continue
        full = seg.mean(axis=0) > 0.8
        widened = full.copy()
        for k in (1, 2):
            widened[k:] |= full[:-k]
            widened[:-k] |= full[k:]
        score += widened
    need = max(3, int(len(bands) * 0.5))
    cols = _group([x for x in range(w) if score[x] >= need])
    return rows, cols


def _ocr_strip(img: Image.Image, config: str) -> list[tuple[int, str]]:
    """OCR d'une bande verticale ; renvoie [(y_centre, mot), ...]."""
    import pytesseract

    data = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
    out = []
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        y = data["top"][i] + data["height"][i] // 2
        out.append((y, word, data["left"][i]))
    return out


def _clean_for_ocr(gray: np.ndarray) -> Image.Image:
    """Binarise et efface les traits du tableau pour aider Tesseract."""
    dark = gray < 150
    h, w = dark.shape
    clean = dark.copy()
    for y in range(h):
        for a, z in _runs(dark[y]):
            if z - a > 80:
                clean[y, a:z] = False
    for x in range(w):
        for a, z in _runs(dark[:, x]):
            if z - a > 45:
                clean[a:z, x] = False
    return Image.fromarray(np.where(clean, 0, 255).astype(np.uint8))


def parse_page_image(img: Image.Image) -> list[Transaction]:
    gray = np.array(img.convert("L"))
    # Les scans font ~2350 px de large ; on normalise pour stabiliser les seuils.
    if gray.shape[1] < 2000:
        scale = 2350 / gray.shape[1]
        img = img.convert("L").resize((int(gray.shape[1] * scale), int(gray.shape[0] * scale)), Image.LANCZOS)
        gray = np.array(img)
    rows, cols = find_grid(gray)
    clean = _clean_for_ocr(gray)
    if len(cols) >= 7 and len(rows) >= 3:
        return _parse_with_grid(clean, rows, cols)
    return _parse_free_text(clean)


def _parse_with_grid(clean: Image.Image, rows: list[int], cols: list[int]) -> list[Transaction]:
    # colonnes : # | description | type | date | notification >30j | montant
    cols = cols[-7:] if len(cols) > 7 else cols
    bounds = list(zip(cols[:-1], cols[1:]))[:6]
    configs = [
        "--psm 6 -c tessedit_char_whitelist=0123456789",
        "--psm 6",
        "--psm 6",
        "--psm 6 -c tessedit_char_whitelist=0123456789/",
        "--psm 6",
        "--psm 6 -c tessedit_char_whitelist=0123456789$,-Overov ",
    ]
    names = ["num", "desc", "type", "date", "late", "amount"]
    w, h = clean.size
    cells: dict[int, dict[str, list]] = {}
    bands = list(zip(rows[:-1], rows[1:]))
    for (x0, x1), cfg, name in zip(bounds, configs, names):
        strip = clean.crop((x0 + 3, 0, x1 - 2, h))
        for y, word, left in _ocr_strip(strip, cfg):
            for bi, (y0, y1) in enumerate(bands):
                if y0 <= y < y1:
                    cells.setdefault(bi, {}).setdefault(name, []).append((left, word))
                    break
    # Les dates sont petites et serrées : on relit chaque cellule en 3x.
    import pytesseract

    dx0, dx1 = bounds[3]
    for bi in list(cells):
        if normalize_date(" ".join(w for _, w in cells[bi].get("date", []))):
            continue
        y0, y1 = bands[bi]
        cell = clean.crop((dx0 + 3, y0 + 2, dx1 - 2, y1 - 1))
        cell = cell.resize((cell.width * 3, cell.height * 3), Image.LANCZOS)
        best = ""
        for psm in (7, 8, 13):
            txt = pytesseract.image_to_string(
                cell, config=f"--psm {psm} -c tessedit_char_whitelist=0123456789/"
            ).strip()
            if normalize_date(txt):
                best = txt
                break
            best = best or txt
        cells[bi]["date"] = [(0, best)]
    txs: list[Transaction] = []
    for bi in sorted(cells):
        c = {k: " ".join(w for _, w in sorted(v)) for k, v in cells[bi].items()}
        kind = normalize_kind(c.get("type", ""))
        amin, amax = normalize_amount(c.get("amount", ""))
        if kind == "?" and amin is None:
            continue  # en-tête ou ligne vide
        if "description" in c.get("desc", "").lower():
            continue
        txs.append(
            Transaction(
                number=c.get("num", ""),
                description=_clean_desc(c.get("desc", "")),
                kind=kind,
                date=normalize_date(c.get("date", "")),
                amount_min=amin,
                amount_max=amax,
                late="yes" in c.get("late", "").lower() or "yos" in c.get("late", "").lower(),
                raw=c,
            )
        )
    return txs


LINE_RE = re.compile(
    r"^\W*(?P<num>\d{1,3})?\W+(?P<desc>.+?)\W+(?P<type>purchase|sale(?:\s*\(partial\))?|exchange|\w{3,9})\W+"
    r"(?P<date>\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}).*?(?P<amount>(?:over\s*)?\$?[\d,.\s]+(?:[-–~=+]\s*\$?[\d,.\s]+)?)\W*$",
    re.I,
)


def _parse_free_text(clean: Image.Image) -> list[Transaction]:
    import pytesseract

    text = pytesseract.image_to_string(clean, config="--psm 6")
    return parse_text(text)


def parse_text(text: str) -> list[Transaction]:
    txs = []
    for line in text.splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        kind = normalize_kind(m.group("type"))
        amin, amax = normalize_amount(m.group("amount"))
        if kind == "?" and amin is None:
            continue
        txs.append(
            Transaction(
                number=m.group("num") or "",
                description=_clean_desc(m.group("desc")),
                kind=kind,
                date=normalize_date(m.group("date")),
                amount_min=amin,
                amount_max=amax,
                raw={"line": line},
            )
        )
    return txs


def _clean_desc(s: str) -> str:
    s = re.sub(r"[_|\[\]{}]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .-—")
    words = s.split(" ")
    # retire les « miettes » d'OCR en fin de ligne (a, ee, 7, Lo, ., ...)
    while len(words) > 1 and (
        len(words[-1]) <= 2 and not words[-1].isupper() or not re.search(r"[A-Za-z0-9]", words[-1])
    ):
        words.pop()
    while len(words) > 1 and not re.search(r"[A-Za-z0-9]", words[0]):
        words.pop(0)
    words = [w for w in words if re.search(r"[A-Za-z0-9&]", w)]
    if words and words[-1] in ("ING", "NC", "INC'", 'ING"', "INC."):
        words[-1] = "INC"
    return " ".join(words).replace('"', "")


# ---------------------------------------------------------------- PDF


def parse_pdf(pdf_bytes: bytes, dpi: int = 200) -> list[Transaction]:
    """Extrait toutes les transactions d'un PDF 278-T."""
    # 1) PDF « texte » : lecture directe, rapide et exacte.
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        if len(re.findall(r"purchase|sale", text, re.I)) >= 1:
            txs = parse_text(text)
            if txs:
                return txs
    except Exception:
        pass
    # 2) PDF scanné : OCR page par page.
    from pdf2image import convert_from_bytes

    txs: list[Transaction] = []
    pages = convert_from_bytes(pdf_bytes, dpi=dpi, grayscale=True)
    for page in pages:
        txs.extend(parse_page_image(page))
    return txs
