# 🇺🇸 Bot d'alertes : transactions de Donald Trump et de ses fils

Ce bot t'envoie un message **Telegram** dès qu'une opération financière de
Donald Trump, Donald Trump Jr. ou Eric Trump devient publique, le plus vite
possible. Il tourne **gratuitement** sur GitHub Actions et vérifie les sources
**toutes les 60 secondes**, sans que ton ordinateur ait besoin d'être allumé.

---

## ⚡ Les sources surveillées, de la plus rapide à la plus lente

| Source | Qui | Délai | Précision |
|---|---|---|---|
| ⛓️ **Blockchain** (portefeuille Ethereum de Trump + trésorerie de World Liberty Financial, la société crypto de la famille) | Trump, famille | **≈ 1 minute** | montant exact, achats/ventes détectés |
| 📈 **Hyperliquid** (compte de la « baleine Trump insider » de la presse ; lien avec Trump **non prouvé**, compte inactif depuis sa liquidation du 31/01/2026) | trader anonyme | **≈ 1 minute** | taille et prix exacts |
| ₿ **Bitcoin** (adresses à ajouter, ex. trésorerie BTC de Trump Media repérée par Arkham) | Trump Media | **≈ 1 minute**, avant confirmation | montant exact |
| 📣 **Truth Social** (posts qui parlent de bourse, crypto, droits de douane…) | Donald Trump | **en direct** | annonce, pas un ordre |
| 🏛️ **SEC – formulaire 4** (actions des sociétés cotées où ils sont dirigeants ou gros actionnaires : Trump Media DJT, PSQ Holdings, GrabAGun, American Bitcoin…) | Trump, Don Jr., Eric (+ 13D/13G : part détenue en hausse/baisse) | **≤ 2 jours ouvrés** | nombre d'actions et **prix exacts** |
| 🏢 **SEC – sociétés liées à la famille** (Trump Media DJT, American Bitcoin ABTC, GrabAGun PEW, Dominari DOMH, PSQ Holdings PSQH, ALT5 Sigma ALTS, World Liberty Financial, Thumzup TZUP, SPAC New America I et Colombier III) : annonces 8-K (achats d'actifs, rachats, accords), ventes annoncées par un dirigeant (formulaire 144, **avant** la vente), achats/ventes des dirigeants | les sociétés | **≈ 1 minute** après le dépôt | montants exacts |
| 🇺🇸 **OGE – formulaire 278-T** (tout son portefeuille boursier : Apple, ETF, obligations…) — *coupé par défaut (30–45 jours de retard) ; secret `OGE_ENABLED=1` pour le réactiver* | Donald Trump | 30 à 45 jours | fourchettes officielles |

Chaque notification affiche le **⏱️ délai** entre l'opération et le moment où
tu la reçois : à la seconde près pour la blockchain et Truth Social, au jour près
pour la SEC et l'OGE (ces déclarations ne donnent que la date, pas l'heure).

### ⚠️ Ce qu'il faut savoir

**Personne ne peut voir les ordres de bourse de Trump au moment où il les
passe.** Aucun flux de ce type n'existe publiquement : ni pour lui, ni pour
aucun particulier. Le bot te prévient au plus vite **dès que l'information
devient publique**, avant la presse.

- **Ses fils ne sont pas fonctionnaires** : on ne voit leurs opérations que sur
  les sociétés cotées dont ils sont administrateurs ou gros actionnaires (SEC).
  Leurs autres placements privés ne sont publiés nulle part.
- **Les portefeuilles crypto** surveillés sont celui étiqueté « Donald Trump »
  par Etherscan et la trésorerie « World Liberty: Multisig ». Un échange
  (un jeton envoyé, un autre reçu dans la même transaction) s'affiche comme un
  ordre : « 🔄 ACHAT X · VENTE Y ». Les jetons sans valeur (spam) sont ignorés.
  Tu peux ajouter d'autres adresses (voir Options).
- **Truth Social** bloque souvent les serveurs : cette source peut ne pas
  fonctionner depuis GitHub Actions. Elle marche si tu lances le bot sur ton PC.
- **Les PDF de l'OGE sont des scans** lus par OCR : type d'opération et montant
  fiables, nom d'actif ou date parfois approximatifs. Le lien vers le PDF
  officiel est toujours joint.

---

## 🚀 Mise en route (10 minutes, une seule fois)

### 1. Créer ton bot Telegram

1. Dans Telegram, ouvre une conversation avec **@BotFather**.
2. Envoie `/newbot`, choisis un nom (ex. `Alertes Trump`) puis un identifiant qui
   finit par `bot` (ex. `alertes_trump_lucas_bot`).
3. BotFather te répond avec un **token** du genre
   `7123456789:AAH...` → copie-le.
4. Ouvre la conversation avec ton nouveau bot et envoie-lui **/start**
   (c'est comme ça que le bot saura à qui écrire).

### 2. Donner le token à GitHub

Dans ce dépôt GitHub :
**Settings → Secrets and variables → Actions → New repository secret**

- Name : `TELEGRAM_BOT_TOKEN`
- Secret : le token copié à l'étape 1

C'est tout. ✅

### 3. Vérifier que ça marche

Onglet **Actions → Alertes transactions Trump → Run workflow**.
Une minute plus tard (3–4 min s'il y a un PDF à lire), tu reçois sur Telegram
« ✅ Bot d'alertes activé » suivi du détail de la dernière déclaration OGE de Trump.
Les autres sources démarrent en silence : tu ne recevras que les **nouveautés**.

Ensuite, le bot vérifie tout seul toutes les 60 secondes : chaque lancement
GitHub tourne environ 5 h 30, puis le suivant prend le relais. Pour renvoyer la
dernière déclaration à tout moment : *Run workflow* en cochant « demo ».

---

## 🔧 Options (facultatif)

À ajouter dans *Settings → Secrets and variables → Actions* :

| Nom | Rôle |
|---|---|
| `TELEGRAM_CHAT_ID` | Forcer le(s) destinataire(s) (ids séparés par des virgules). Sinon, toute personne qui envoie `/start` au bot est ajoutée. |
| `WATCH_WALLETS` | Portefeuilles Ethereum en plus, ex. `0xabc…:World Liberty,0xdef…:Eric Trump` |
| `SEC_EXTRA_CIKS` | Personnes en plus à la SEC, ex. `2031474:Jared Kushner` |
| `SEC_COMPANIES` | Sociétés en plus, ex. `1234567:Nom` (numéro CIK SEC) ; seuil des ventes annoncées `CO_MIN_USD` (défaut 100 000 $) |
| `HL_WALLETS` | Comptes Hyperliquid en plus, ex. `0xabc…:Nom` (seuil `HL_MIN_USD`, défaut 100 000 $) |
| `WATCH_BTC` | Adresses Bitcoin à surveiller, ex. `bc1q…:Trump Media` (seuil `BTC_MIN`, défaut 1 BTC) |
| `CRYPTO_MIN_USD` | Valeur minimale d'un jeton reçu pour être signalé (défaut 1000) |
| `TRUTH_ENABLED` | Mettre `0` pour couper Truth Social |
| `TRUTH_KEYWORDS` | Mots-clés Truth Social, séparés par des virgules |

Pour qu'une option soit prise en compte, ajoute-la aussi dans
`.github/workflows/bot.yml`, section `env:` (ex. `WATCH_WALLETS: ${{ secrets.WATCH_WALLETS }}`).

Variables d'environnement reconnues par `bot.py` : `FILER_FILTER` (nom
surveillé, défaut `Trump`), `MAX_LINES` (nombre d'opérations détaillées dans le
message OGE, défaut 5), `SEND_CSV=1` (joindre la liste complète en CSV).

## 💻 Lancer sur ton PC (optionnel)

```bash
pip install -r requirements.txt
# + installer Tesseract OCR et Poppler (Windows : voir
#   https://github.com/UB-Mannheim/tesseract/wiki et
#   https://github.com/oschwartz10612/poppler-windows)
set TELEGRAM_BOT_TOKEN=ton_token        # PowerShell : $env:TELEGRAM_BOT_TOKEN="ton_token"
python bot.py --loop 60                 # vérifie toutes les 60 secondes
```

## 📂 Fichiers

| Fichier | Rôle |
|---|---|
| `delay.py` | calcule le délai entre l'opération et la notification |
| `bot.py` | interroge l'OGE, détecte les nouveautés, envoie sur Telegram |
| `parse_278t.py` | lit les PDF 278-T (texte ou OCR) et en extrait les opérations |
| `sources_sec.py` | formulaires 4 / 13D / 13G de la SEC (Trump, Don Jr., Eric) |
| `sources_crypto.py` | portefeuilles Ethereum (via l'explorateur public Blockscout) |
| `sources_companies.py` | déclarations SEC des sociétés liées à la famille Trump |
| `sources_hyperliquid.py` | comptes de trading Hyperliquid |
| `sources_btc.py` | adresses Bitcoin (via mempool.space) |
| `sources_truth.py` | posts Truth Social contenant des mots-clés financiers |
| `state.json` | déclarations déjà vues (créé et mis à jour automatiquement) |
| `.github/workflows/bot.yml` | exécution automatique en continu (vérification toutes les 60 s) |

Source des données : [OGE – Officials' Individual Disclosures](https://www.oge.gov/web/oge.nsf/Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm).
Ce bot ne donne aucun conseil d'investissement.
