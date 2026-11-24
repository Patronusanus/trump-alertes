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

Un échange (envoi d'un jeton + réception d'un autre dans la même transaction)
est affiché comme un ordre : « 🔄 ACHAT X · VENTE Y ».

Pour ajouter des portefeuilles : variable WATCH_WALLETS="0xadresse:Nom,0xautre:Nom2".
"""
from __future__ import annotations

import html
import os

import requests

import delay

WALLETS = {
    "0x94845333028B1204Fbe14E1278Fd4Adde46B22ce": "Donald Trump (portefeuille Ethereum)",
    # Trésorerie de World Liberty Financial (société crypto de la famille Trump),
    # étiquetée « World Liberty: Multisig » sur Blockscout, vérifié le 24/11/2026.
    "0x5be9a4959308A0D0c7bC0870E319314d8D957dBB": "World Liberty Financial (trésorerie)",
}
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
    # Portefeuilles déjà initialisés : un portefeuille ajouté démarre en silence.
    ready = set(state.get("crypto_ready", []))
    if "crypto_seen" in state and "crypto_ready" not in state:
        ready = {"0x94845333028B1204Fbe14E1278Fd4Adde46B22ce".lower()}
    msgs = []
    for addr, label in WALLETS.items():
        low = addr.lower()
        silent = low not in ready
        try:
            toks = _get(f"/addresses/{addr}/token-transfers").get("items", [])
            txs = _get(f"/addresses/{addr}/transactions").get("items", [])
        except Exception as ex:
            log(f"Crypto {label} : erreur {ex!r}")
            continue
        ready.add(low)
        # Regroupe les transferts de jetons par transaction (pour voir les échanges).
        groups: dict[str, list] = {}
        for t in toks:
            key = f"tok:{t['transaction_hash']}:{t.get('log_index')}"
            if key in seen:
                continue
            seen.add(key)
            groups.setdefault(t["transaction_hash"], []).append(t)
        for t in reversed(txs):
            key = f"tx:{t['hash']}"
            if key in seen:
                continue
            seen.add(key)
            if silent or t["hash"] in groups:
                continue
            msg = _describe_tx(low, label, t)
            if msg:
                log("CRYPTO NOUVEAU :", label, key)
                msgs.append(msg)
        if silent:
            continue
        for h, items in reversed(list(groups.items())):
            msg = _describe_tokens(low, label, h, items)
            if msg:
                log("CRYPTO NOUVEAU :", label, h)
                msgs.append(msg)
    state["crypto_seen"] = sorted(seen)[-5000:]
    state["crypto_ready"] = sorted(ready)
    return msgs


def _leg(t, low):
    tok = t.get("token") or {}
    qty = _amount(t.get("total"))
    rate = float(tok["exchange_rate"]) if tok.get("exchange_rate") else None
    usd = qty * rate if (qty is not None and rate) else None
    out = t["from"]["hash"].lower() == low
    return out, tok.get("symbol") or "?", qty, usd


def _txt_leg(sym, qty, usd):
    q = _fmt(qty) if qty is not None else "1 NFT"
    v = f" (≈ {_fmt(usd)} $)" if usd else ""
    return f"<b>{html.escape(q)} {html.escape(sym)}</b>{v}"


def _footer(label, ts, tx):
    e = html.escape
    return (
        f"⛓️ {e(label)} · ⏱️ il y a <b>{delay.since_timestamp(ts)}</b>\n"
        f'<a href="https://etherscan.io/tx/{e(tx)}">📎 Transaction</a>'
    )


def _describe_tokens(low, label, tx, items) -> str | None:
    legs = [_leg(t, low) for t in items]
    outs = [l for l in legs if l[0]]
    ins = [l for l in legs if not l[0] and l[3] is not None and l[3] >= MIN_USD]
    ins_any = [l for l in legs if not l[0] and l[3] is not None]
    ts = items[0].get("timestamp", "")
    if outs and ins_any:  # échange dans la même transaction = un ordre
        best_in = max(ins_any, key=lambda l: l[3] or 0)
        best_out = max(outs, key=lambda l: l[3] or 0)
        return (
            f"🔄 <b>ACHAT</b> {_txt_leg(*best_in[1:])}\n"
            f"     <b>VENTE</b> {_txt_leg(*best_out[1:])}\n" + _footer(label, ts, tx)
        )
    if outs:
        best = max(outs, key=lambda l: l[3] or 0)
        if best[3] is None and best[1] == "?":
            return None
        return f"🔴 ENVOI {_txt_leg(*best[1:])}\n" + _footer(label, ts, tx)
    if ins:  # jetons sans valeur reçus = spam, ignorés
        best = max(ins, key=lambda l: l[3])
        return f"🟢 RÉCEPTION {_txt_leg(*best[1:])}\n" + _footer(label, ts, tx)
    return None


def _describe_tx(low, label, t) -> str | None:
    eth = int(t.get("value") or 0) / 1e18
    ts = t.get("timestamp") or ""
    if (t.get("from") or {}).get("hash", "").lower() != low:
        if eth < float(os.environ.get("CRYPTO_MIN_ETH", "0.5")):
            return None
        return f"🟢 RÉCEPTION <b>{_fmt(eth)} ETH</b>\n" + _footer(label, ts, t["hash"])
    if eth <= 0:
        return None  # appel de contrat sans ETH (approbation, etc.)
    return f"🔴 ENVOI <b>{_fmt(eth)} ETH</b>\n" + _footer(label, ts, t["hash"])
