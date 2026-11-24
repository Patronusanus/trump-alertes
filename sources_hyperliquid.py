"""Source n°5 : comptes de trading Hyperliquid (positions visibles en direct).

Hyperliquid est une bourse de contrats à terme « on-chain » : chaque ordre
exécuté par n'importe quel compte est public, quelques secondes après.

Compte surveillé par défaut :
  0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae, la « baleine Trump insider » de la
  presse (Decrypt, Cointelegraph, oct. 2025). Elle a ouvert un short géant sur le
  bitcoin moins d'une heure avant l'annonce des droits de douane de 100 % sur la
  Chine (10/10/2025). Lien avec la famille Trump : SOUPÇON NON PROUVÉ (démenti).
  Vérifié le 24/11/2026 : compte liquidé le 31/01/2026 (long ETH), solde ≈ 0 $,
  inactif depuis. Le bot préviendra s'il se remet à trader.

Ajouter d'autres comptes : HL_WALLETS="0xadresse:Nom,0xautre:Nom2".
Seuil : HL_MIN_USD (défaut 100 000 $ par coin et par sens).
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone

import requests

import delay

WALLETS = {
    "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae": "Baleine « Trump insider » (soupçon non prouvé)",
}
for item in filter(None, (x.strip() for x in os.environ.get("HL_WALLETS", "").split(","))):
    addr, _, label = item.partition(":")
    WALLETS[addr.strip().lower()] = label.strip() or addr.strip()

API = "https://api.hyperliquid.xyz/info"
MIN_USD = float(os.environ.get("HL_MIN_USD", "100000"))

DIRS = {
    "Open Long": "🟢 LONG ouvert (pari à la hausse)",
    "Open Short": "🔴 SHORT ouvert (pari à la baisse)",
    "Close Long": "⚪ LONG fermé",
    "Close Short": "⚪ SHORT fermé",
    "Long > Short": "🔴 Bascule LONG → SHORT",
    "Short > Long": "🟢 Bascule SHORT → LONG",
    "Buy": "🟢 ACHAT",
    "Sell": "🔴 VENTE",
}


def _fmt(x: float) -> str:
    if x >= 1_000_000:
        return f"{x / 1_000_000:.1f} M".replace(".", ",")
    return f"{x:,.0f}".replace(",", " ")


def check(state: dict, log=print) -> list[str]:
    seen = set(state.get("hl_seen", []))
    ready = set(state.get("hl_ready", []))
    msgs = []
    for addr, label in WALLETS.items():
        try:
            r = requests.post(API, json={"type": "userFills", "user": addr}, timeout=30)
            r.raise_for_status()
            fills = r.json()
        except Exception as ex:
            log(f"Hyperliquid {label} : erreur {ex!r}")
            continue
        new = []
        for f in fills:
            key = f"{f.get('hash')}:{f.get('tid')}"
            if key not in seen:
                seen.add(key)
                new.append(f)
        if addr not in ready:
            ready.add(addr)  # premier passage : on n'envoie pas l'historique
            continue
        # Regroupe les exécutions par (coin, sens) : un ordre = plusieurs « fills ».
        groups: dict[tuple, dict] = {}
        for f in new:
            g = groups.setdefault((f["coin"], f.get("dir", "")), {"sz": 0.0, "usd": 0.0, "t": 0})
            sz, px = float(f["sz"]), float(f["px"])
            g["sz"] += sz
            g["usd"] += sz * px
            g["t"] = max(g["t"], int(f["time"]))
        for (coin, d), g in groups.items():
            if g["usd"] < MIN_USD and not d.startswith("Liquidated"):
                continue
            title = DIRS.get(d, "💥 LIQUIDATION" if d.startswith("Liquidated") else d)
            px = g["usd"] / g["sz"] if g["sz"] else 0
            ts = datetime.fromtimestamp(g["t"] / 1000, timezone.utc).isoformat()
            log("HYPERLIQUID NOUVEAU :", label, coin, d)
            px_s = f"{px:,.2f}".replace(",", " ").replace(".", ",")
            msgs.append(
                f"{title} <b>{html.escape(coin)}</b> · <b>{_fmt(g['usd'])} $</b>\n"
                f"📈 {html.escape(label)} · {_fmt(g['sz'])} {html.escape(coin)} à {px_s} $"
                f" · ⏱️ il y a <b>{delay.since_timestamp(ts)}</b>\n"
                f'<a href="https://app.hyperliquid.xyz/explorer/address/{addr}">📎 Compte</a>'
            )
    state["hl_seen"] = sorted(seen)[-6000:]
    state["hl_ready"] = sorted(ready)
    return msgs
