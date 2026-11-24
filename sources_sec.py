"""Source n°2 : SEC / EDGAR (formulaires 4, 3, 13D, 13G).

Quand un Trump est dirigeant ou gros actionnaire d'une société cotée
(Trump Media & Technology Group, PSQ Holdings, American Bitcoin...), chaque achat
ou vente d'actions de cette société doit être déclaré à la SEC sous **2 jours
ouvrés** (formulaire 4), avec le nombre d'actions et le prix exact.
C'est la source la plus rapide et la plus précise qui existe pour la famille.

Identifiants EDGAR (CIK) vérifiés le 24/11/2026 : voir PEOPLE ci-dessous.
"""
from __future__ import annotations

import html
import os
import re
import time
import xml.etree.ElementTree as ET

import requests

import delay

PEOPLE = {
    "947033": "Donald J. Trump",
    "2050191": "Donald J. Trump Revocable Trust",
    "2016181": "Donald Trump Jr.",
    "2057754": "Eric Trump",
}
extra = os.environ.get("SEC_EXTRA_CIKS", "")  # ex. "2031474:Jared Kushner"
for item in filter(None, (x.strip() for x in extra.split(","))):
    cik, _, label = item.partition(":")
    PEOPLE[cik.lstrip("0")] = label or cik

# La SEC demande un User-Agent « Nom contact@domaine » ; personnalisable.
UA = os.environ.get("SEC_USER_AGENT", "TrumpAlertesBot bot@example.com")
_s = requests.Session()
_s.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})

CODES = {
    "P": ("🟢 ACHAT en bourse", "achat"),
    "S": ("🔴 VENTE en bourse", "vente"),
    "A": ("🎁 Attribution d'actions (rémunération)", "autre"),
    "M": ("🔧 Exercice d'options", "autre"),
    "C": ("🔁 Conversion", "autre"),
    "F": ("🧾 Actions retenues pour impôts", "autre"),
    "G": ("🎁 Donation", "autre"),
    "D": ("↩️ Cession à la société", "autre"),
    "J": ("ℹ️ Autre opération", "autre"),
    "X": ("🔧 Exercice d'options", "autre"),
}

FORM_LABELS = {
    "4": "Formulaire 4 (achat/vente d'un dirigeant)",
    "4/A": "Formulaire 4 corrigé",
    "3": "Formulaire 3 (nouvelle participation)",
    "5": "Formulaire 5 (récapitulatif annuel)",
}


def _get(url, **kw):
    for attempt in range(3):
        r = _s.get(url, timeout=30, **kw)
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def recent_filings(cik: str) -> list[dict]:
    j = _get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()
    r = j["filings"]["recent"]
    out = []
    for i in range(min(len(r["form"]), 40)):
        acc = r["accessionNumber"][i]
        doc = r["primaryDocument"][i]
        raw_doc = doc.split("/", 1)[1] if doc.startswith("xsl") else doc
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
        out.append(
            {
                "id": acc,
                "form": r["form"][i],
                "filed": r["filingDate"][i],
                "accepted": r["acceptanceDateTime"][i],
                "xml_url": f"{base}/{raw_doc}",
                "url": f"{base}/{doc}",
                "index": f"{base}/{acc}-index.htm",
            }
        )
    time.sleep(0.15)  # la SEC limite à 10 requêtes/seconde
    return out


def _txt(node, path):
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse_form4(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    issuer = _txt(root, "issuer/issuerName")
    ticker = _txt(root, "issuer/issuerTradingSymbol")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    roles = []
    if rel is not None:
        if _txt(rel, "isDirector") in ("1", "true"):
            roles.append("administrateur")
        if _txt(rel, "isOfficer") in ("1", "true"):
            roles.append(_txt(rel, "officerTitle") or "dirigeant")
        if _txt(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("actionnaire >10 %")
    txs = []
    for tag in ("nonDerivativeTable/nonDerivativeTransaction", "derivativeTable/derivativeTransaction"):
        for t in root.findall(tag):
            shares = _txt(t, "transactionAmounts/transactionShares/value")
            price = _txt(t, "transactionAmounts/transactionPricePerShare/value")
            txs.append(
                {
                    "security": _txt(t, "securityTitle/value"),
                    "date": _txt(t, "transactionDate/value"),
                    "code": _txt(t, "transactionCoding/transactionCode"),
                    "ad": _txt(t, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                    "shares": float(shares) if shares else 0.0,
                    "price": float(price) if re.match(r"^[\d.]+$", price or "") else None,
                    "after": _txt(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                    "derivative": tag.startswith("derivative"),
                }
            )
    return {"issuer": issuer, "ticker": ticker, "roles": roles, "txs": txs}


def _fmt_n(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def _fr(d: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d or "")
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else d


def build_message(person: str, f: dict, parsed: dict | None) -> str:
    e = html.escape
    label = FORM_LABELS.get(f["form"], f"Formulaire {f['form']}")
    lines = [f"🏛️ <b>SEC : {e(person)}</b>", f"📄 {e(label)} · déposé le {_fr(f['filed'])}"]
    if parsed:
        tick = f" ({e(parsed['ticker'])})" if parsed["ticker"] else ""
        lines.append(f"🏢 <b>{e(parsed['issuer'])}{tick}</b>")
        if parsed["txs"]:
            lines.append(delay.line_for_days([delay.days_since(delay.parse_day(t["date"])) for t in parsed["txs"]]))
        if parsed["roles"]:
            lines.append(f"👤 {e(', '.join(parsed['roles']))}")
        for t in parsed["txs"]:
            title, _ = CODES.get(t["code"], (f"Code {t['code']}", "autre"))
            sens = "+" if t["ad"] == "A" else "−" if t["ad"] == "D" else ""
            line = f"{title} · {sens}{_fmt_n(t['shares'])} {e(t['security'][:60])}"
            if t["price"]:
                total = t["shares"] * t["price"]
                line += f"\n    à {t['price']:.2f} $ → <b>{_fmt_n(total)} $</b>"
            line += f" · {_fr(t['date'])}"
            d = delay.days_since(delay.parse_day(t["date"]))
            if d is not None:
                line += f" (il y a {delay.fmt_days(d)})"
            if t["after"]:
                line += f"\n    détenu après : {_fmt_n(float(t['after']))}"
            lines.append(line)
        if not parsed["txs"]:
            lines.append("Aucune opération listée (déclaration de détention).")
    lines.append(f'<a href="{e(f["url"])}">📎 Document SEC</a>')
    return "\n".join(lines)


def check(state: dict, log=print) -> list[str]:
    """Renvoie les messages à envoyer pour les nouveaux dépôts SEC."""
    seen = set(state.get("sec_seen", []))
    first = "sec_seen" not in state
    messages = []
    for cik, person in PEOPLE.items():
        try:
            filings = recent_filings(cik)
        except Exception as ex:
            log(f"SEC {person} : erreur {ex!r}")
            continue
        for f in reversed(filings):
            if f["id"] in seen:
                continue
            seen.add(f["id"])
            if first:
                continue
            parsed = None
            if f["form"] in ("3", "4", "4/A", "5", "5/A"):
                try:
                    parsed = parse_form4(_get(f["xml_url"]).text)
                except Exception as ex:
                    log("SEC lecture XML impossible:", repr(ex))
            log("SEC NOUVEAU :", person, f["form"], f["filed"])
            messages.append(build_message(person, f, parsed))
    state["sec_seen"] = sorted(seen)
    return messages
