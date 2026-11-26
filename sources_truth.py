"""Source n°4 : posts Truth Social de Donald Trump qui peuvent faire bouger un marché.

Trump annonce parfois sur Truth Social des choses qui font bouger les cours
(« THIS IS A GREAT TIME TO BUY!!! », droits de douane, crypto...). Le bot relaie
les posts qui parlent d'un marché et indique les MARCHÉS CONCERNÉS (sans dire
d'acheter ou de vendre : ce n'est pas un conseil d'investissement). Avec le secret
ANTHROPIC_API_KEY, chaque post retenu est en plus analysé par une IA (analyse_ia.py) :
impact estimé et mouvements de marché les plus probables.

Accès : l'API de Truth Social bloque les serveurs (dont GitHub Actions). Le bot
passe alors par l'archive publique trumpstruth.org (flux RSS), qui recopie
chaque post avec son identifiant et son heure d'origine.
"""
from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

import analyse_ia
import delay

ACCOUNT_ID = "107780257626128497"  # @realDonaldTrump
API = f"https://truthsocial.com/api/v1/accounts/{ACCOUNT_ID}/statuses"
RSS = "https://www.trumpstruth.org/feed"
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json, application/rss+xml"})

# mot-clé (regex) → marché concerné
MARKETS = [
    (r"tariffs?|trade deal|trade war|import tax", "droits de douane → indices US, entreprises importatrices"),
    (r"china|chinese|xi jinping|beijing", "Chine (actions chinoises, yuan)"),
    (r"europe|european union|\beu\b", "Europe (actions européennes, euro)"),
    (r"canada|canadian", "Canada (dollar canadien)"),
    (r"mexico|mexican", "Mexique (peso)"),
    (r"japan|japanese", "Japon (yen, Nikkei)"),
    (r"bitcoin|\bbtc\b|crypto", "Bitcoin / crypto"),
    (r"ethereum|\beth\b", "Ethereum"),
    (r"world liberty|wlfi|usd1", "WLFI (World Liberty)"),
    (r"\$trump|meme ?coin", "memecoin $TRUMP"),
    (r"truth social|trump media|\bdjt\b", "Trump Media (DJT)"),
    (r"great time to buy|stock market|stocks|\bdow\b|nasdaq|s&p", "indices US (S&P 500, Nasdaq, Dow)"),
    (r"\bfed\b|federal reserve|interest rates?|powell|rate cut", "taux / dollar / or"),
    (r"\boil\b|opec|drill|gasoline|energy prices", "pétrole (WTI, Brent)"),
    (r"\bgold\b", "or"),
    (r"pharma|drug prices|pharmaceutical", "pharmacie"),
    (r"steel|aluminum", "acier / aluminium"),
    (r"chips?\b|semiconductor", "semi-conducteurs"),
    (r"defense|military contract|weapons", "défense"),
    (r"apple", "Apple (AAPL)"),
    (r"tesla|elon", "Tesla (TSLA)"),
    (r"nvidia", "Nvidia (NVDA)"),
    (r"intel\b", "Intel (INTC)"),
    (r"boeing", "Boeing (BA)"),
    (r"amazon", "Amazon (AMZN)"),
    (r"microsoft", "Microsoft (MSFT)"),
    (r"google|alphabet", "Alphabet (GOOGL)"),
    (r"\bmeta\b|facebook", "Meta (META)"),
]
_extra = os.environ.get("TRUTH_KEYWORDS", "")  # ex. "uranium:Uranium,lithium:Lithium"
for item in filter(None, (x.strip() for x in _extra.split(","))):
    kw, _, label = item.partition(":")
    MARKETS.append((re.escape(kw.lower()), label or kw))


def _text(content: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>", "\n", content)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def markets(text: str) -> list[str]:
    low = text.lower()
    out = []
    for pat, label in MARKETS:
        if re.search(pat, low) and label not in out:
            out.append(label)
    return out


def _from_api() -> list[dict]:
    r = _s.get(API, params={"limit": 20, "exclude_replies": "true"}, timeout=20)
    r.raise_for_status()
    return [
        {"id": int(p["id"]), "text": _text(p.get("content") or ""), "at": p.get("created_at", ""), "url": p.get("url") or ""}
        for p in r.json()
    ]


def _from_rss() -> list[dict]:
    r = _s.get(RSS, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        get = lambda name: next((el.text or "" for el in it if el.tag.endswith(name)), "")
        oid = get("originalId")
        if not oid.isdigit():
            continue
        try:
            at = parsedate_to_datetime(get("pubDate")).astimezone(timezone.utc).isoformat()
        except Exception:
            # l'identifiant Truth Social contient l'heure de publication (ms)
            at = datetime.fromtimestamp((int(oid) >> 16) / 1000, timezone.utc).isoformat()
        out.append({"id": int(oid), "text": _text(get("description") or get("title")), "at": at, "url": get("originalUrl") or get("link")})
    return out


def check(state: dict, log=print) -> list[str]:
    if os.environ.get("TRUTH_ENABLED", "1") == "0":
        return []
    try:
        posts = _from_api()
    except Exception:
        try:
            posts = _from_rss()
        except Exception as ex:
            log(f"Truth Social inaccessible ({type(ex).__name__}) : ignoré")
            return []
    first = "truth_last_id" not in state
    last = int(state.get("truth_last_id", 0))
    msgs = []
    for p in sorted(posts, key=lambda p: p["id"]):
        if p["id"] <= last:
            continue
        last = max(last, p["id"])
        if first or not p["text"]:
            continue
        hits = markets(p["text"])
        if not hits:
            continue
        excerpt = p["text"] if len(p["text"]) < 280 else p["text"][:280] + "…"
        analysis = None
        try:
            analysis = analyse_ia.analyse(p["text"], hits)
        except Exception as ex:
            log(f"Analyse IA impossible ({type(ex).__name__}) : liste par mots-clés à la place")
        if analysis:
            body = analyse_ia.format_block(analysis)
            note = "⚠️ <i>Estimation automatique par IA, pas un conseil d'investissement.</i>"
        else:
            body = f"🎯 Marchés concernés : <b>{html.escape(' · '.join(hits[:4]))}</b>"
            note = "<i>Info, pas un conseil d'achat.</i>"
        msgs.append(
            f"📣 <b>Trump sur Truth Social</b> · ⏱️ il y a <b>{delay.since_timestamp(p['at'])}</b>\n"
            f"« {html.escape(excerpt)} »\n"
            f"{body}\n"
            f"{note} "
            f'<a href="{html.escape(p["url"])}">📎 Post</a>'
        )
    state["truth_last_id"] = str(last)
    return msgs
