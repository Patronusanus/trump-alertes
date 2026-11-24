"""Source n°3 : portefeuilles crypto publics (vrai temps réel, quelques secondes).

Une blockchain est publique : tout mouvement d'un portefeuille connu est visible
immédiatement. On interroge l'explorateur Blockscout (gratuit, sans clé API).

Portefeuille surveillé par défaut (vérifié le 24/11/2026) :
  0x94845333028B1204Fbe14E1278Fd4Adde46B22ce — étiqueté « Donald Trump » par
  Etherscan (il a reçu les revenus de ses NFT « Trump Digital Trading Cards »).

Ce portefeuille reçoit énormément de « spam » (jetons sans valeur envoyés par
des inconnus). On ne signale donc que :
  - les opérations ENVOYÉES par le portefeuille (c'est lui qui agit) ;
  - les jetons reçus ayant une vraie valeur (≥ CRYPTO_MIN_USD, 1 000 $ par défaut).

Pour ajouter des portefeuilles (ex. ceux de World Liberty Financial identifiés
sur Arkham) : variable WATCH_WALLETS="0xadresse:Nom,0xautre:Nom2".
"""
from __future__ import annotations

import html
import os

import requests

import delay

WALLETS = {"0x94845333028B1204Fbe14E1278Fd4Adde46B22ce": "Donald Trump (portefeuille Ethereum)"}
for item in filter(None, (x.strip() for x in os.environ.get("WATCH_WALLETS", "").split(","))):
    addr, _, label = item.partition(":")
    WALLETS[addr.strip()] = label.strip() or addr.strip()

API = "https://eth.blockscout.com/api/v2"
MIN_USD = float(os.environ.get("CRYPTO_MIN_USD", "1000"))
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0 (disclosure-alert-bot)"})


def _get(path, **params):
    r = _s.get(API + path, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _amount(total: dict | None) -> float | None:
    if not total or total.get("value") in (None, ""):
        return None
    dec = int(total.get("decimals") or 0)
    return int(total["value"]) / (10**dec)


def _fmt(x: float) -> str:
    if x >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    return f"{x:,.4g}"


def _short(a: str) -> str:
    return a[:6] + "…" + a[-4:]


def check(state: dict, log=print) -> list[str]:
    seen = set(state.get("crypto_seen", []))
    first = "crypto_seen" not in state
    msgs = []
    for addr, label in WALLETS.items():
        low = addr.lower()
        events = []
        try:
            for t in _get(f"/addresses/{addr}/token-transfers").get("items", []):
                key = f"tok:{t['transaction_hash']}:{t.get('log_index')}"
                events.append((key, "token", t))
            for t in _get(f"/addresses/{addr}/transactions").get("items", []):
                events.append((f"tx:{t['hash']}", "tx", t))
        except Exception as ex:
            log(f"Crypto {label} : erreur {ex!r}")
            continue
        for key, kind, t in reversed(events):
            if key in seen:
                continue
            seen.add(key)
            if first:
                continue
            msg = _describe(addr, low, label, kind, t)
            if msg:
                log("CRYPTO NOUVEAU :", label, key)
                msgs.append(msg)
    state["crypto_seen"] = sorted(seen)[-3000:]
    return msgs


def _describe(addr, low, label, kind, t) -> str | None:
    e = html.escape
    if kind == "token":
        frm, to = t["from"]["hash"].lower(), t["to"]["hash"].lower()
        tok = t.get("token") or {}
        sym = tok.get("symbol") or "?"
        qty = _amount(t.get("total"))
        rate = float(tok["exchange_rate"]) if tok.get("exchange_rate") else None
        usd = qty * rate if (qty is not None and rate) else None
        outgoing = frm == low
        if not outgoing and (usd is None or usd < MIN_USD):
            return None  # spam / poussière
        sens = "🔴 ENVOI/VENTE" if outgoing else "🟢 RÉCEPTION/ACHAT"
        other = to if outgoing else frm
        value = f" (≈ {_fmt(usd)} $)" if usd else ""
        qty_s = _fmt(qty) if qty is not None else "1 NFT"
        tx = t["transaction_hash"]
        ts = t.get("timestamp", "")
        when = ts[:16].replace("T", " ")
    else:
        if (t.get("from") or {}).get("hash", "").lower() != low:
            # ETH reçu : on ne garde que les montants significatifs
            eth = int(t.get("value") or 0) / 1e18
            if eth < float(os.environ.get("CRYPTO_MIN_ETH", "0.5")):
                return None
            sens, sym, qty_s, value, other = "🟢 RÉCEPTION", "ETH", _fmt(eth), "", t["from"]["hash"]
        else:
            eth = int(t.get("value") or 0) / 1e18
            method = t.get("method") or "transaction"
            sens, sym, qty_s, value = f"🔴 TRANSACTION ({method})", "ETH", _fmt(eth), ""
            other = (t.get("to") or {}).get("hash", "?")
        tx = t["hash"]
        ts = t.get("timestamp") or ""
        when = ts[:16].replace("T", " ")
    return (
        f"{sens} <b>{e(qty_s)} {e(sym)}</b>{value}\n"
        f"⛓️ {e(label)} · ⏱️ il y a <b>{delay.since_timestamp(ts)}</b>\n"
        f'<a href="https://etherscan.io/tx/{e(tx)}">📎 Transaction</a>'
    )
