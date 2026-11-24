"""Source n°6 : adresses Bitcoin (ex. trésorerie BTC de Trump Media, DJT).

Les mouvements sont visibles dès qu'ils arrivent dans le « mempool », avant
même d'être confirmés : quelques secondes. On interroge mempool.space (gratuit,
sans clé).

Aucune adresse par défaut : celles de Trump Media sont identifiées par Arkham
(arkm.com, entité « Trump Media »), site qu'on ne peut pas lire automatiquement.
Ajouter : WATCH_BTC="bc1q...:Trump Media,bc1q...:Trump Media 2".
Seuil : BTC_MIN (défaut 1 BTC).
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone

import requests

import delay

WALLETS = {}
for item in filter(None, (x.strip() for x in os.environ.get("WATCH_BTC", "").split(","))):
    addr, _, label = item.partition(":")
    WALLETS[addr.strip()] = label.strip() or addr.strip()

API = "https://mempool.space/api"
MIN_BTC = float(os.environ.get("BTC_MIN", "1"))


def check(state: dict, log=print) -> list[str]:
    seen = set(state.get("btc_seen", []))
    ready = set(state.get("btc_ready", []))
    msgs = []
    for addr, label in WALLETS.items():
        try:
            r = requests.get(f"{API}/address/{addr}/txs", timeout=30)
            r.raise_for_status()
            txs = r.json()
        except Exception as ex:
            log(f"BTC {label} : erreur {ex!r}")
            continue
        silent = addr not in ready
        ready.add(addr)
        for tx in reversed(txs):
            if tx["txid"] in seen:
                continue
            seen.add(tx["txid"])
            if silent:
                continue
            out_ = sum(v["value"] for v in tx["vout"] if v.get("scriptpubkey_address") == addr)
            in_ = sum(
                (v.get("prevout") or {}).get("value", 0)
                for v in tx["vin"]
                if (v.get("prevout") or {}).get("scriptpubkey_address") == addr
            )
            net = (out_ - in_) / 1e8
            if abs(net) < MIN_BTC:
                continue
            st = tx.get("status") or {}
            if st.get("block_time"):
                ts = datetime.fromtimestamp(st["block_time"], timezone.utc).isoformat()
                when = f"⏱️ il y a <b>{delay.since_timestamp(ts)}</b>"
            else:
                when = "⏱️ <b>à l'instant</b> (pas encore confirmé)"
            sens = "🟢 RÉCEPTION" if net > 0 else "🔴 ENVOI (vente possible)"
            log("BTC NOUVEAU :", label, tx["txid"])
            msgs.append(
                f"{sens} <b>{abs(net):,.2f} BTC</b>\n".replace(",", " ")
                + f"₿ {html.escape(label)} · {when}\n"
                f'<a href="https://mempool.space/tx/{tx["txid"]}">📎 Transaction</a>'
            )
    state["btc_seen"] = sorted(seen)[-3000:]
    state["btc_ready"] = sorted(ready)
    return msgs
