# 🍪 Appart Hunter — Tuto pour t'inscrire

Appart Hunter est un bot perso qui scanne ~21 sites immobiliers parisiens
(LeBonCoin, SeLoger, PAP, agences, etc.) toutes les 90 secondes à 15 min
selon la source, et qui te ping **dans ton groupe Telegram** uniquement
quand une vraie pépite sort.

Chaque annonce filtrée est analysée par Claude (IA) qui :
- juge le **cachet parisien** (parquet, moulures, cheminée, luminosité)
- évalue les photos visuellement
- détecte les arnaques + meublés
- donne un score `/100`

Tu reçois une alerte uniquement si **score ≥ ton seuil** (70 par défaut).

---

## ✅ Ce qu'il te faut avant de commencer

### a. Une clé API Anthropic (Claude)

C'est **toi qui paies** ton scoring (≈ **5-15 €/mois** selon volume).
L'infra (scraping, serveur, Telegram) est offerte par l'admin.

1. Va sur [console.anthropic.com](https://console.anthropic.com)
2. Crée un compte (email + mot de passe)
3. Onglet **Billing** → ajoute une carte (€5 minimum pour démarrer)
4. Onglet **API Keys** → clique **Create Key**
5. Nomme-la "AppartHunter" → copie-la (commence par `sk-ant-…`)

⚠️ **Garde-la précieusement** : elle ne s'affiche qu'une fois. Si tu la
perds, tu en regénères une.

### b. Telegram installé

Si tu ne l'as pas : [telegram.org](https://telegram.org) — disponible
sur iOS, Android, Mac, Windows, Linux, web. Compte gratuit en 1 minute.

---

## 🚀 Setup en 3 étapes

### Étape 1 — Crée ton groupe Telegram

Le bot fonctionne **uniquement en groupe** (jamais en DM perso) pour
permettre de partager les alertes avec ton/ta partenaire et **commenter
ensemble**.

1. Ouvre Telegram
2. **New Group** (en haut à droite ou menu burger)
3. Ajoute :
   - **Ton/ta partenaire** (facultatif, tu peux être seul)
   - **`@AppartHunterBot`** ← demande à l'admin le nom exact si tu ne le retrouves pas
4. Nomme ton groupe (ex: `🍪 Appart Hunter — Marie & Tom`)
5. Crée → tu arrives dans le groupe

### Étape 2 — Lance le wizard

Dans le groupe, tape simplement :

```
/start
```

Le bot répond avec un message de bienvenue et te pose **7 questions** :

| # | Question | Exemple de réponse |
|---|---|---|
| 1 | Ta clé Anthropic | `sk-ant-api03-xxx…` (copie-colle) |
| 2 | Budget max CC en €/mois | `2000` |
| 3 | Surface minimum en m² | `40` |
| 4 | Types de bien | Tape sur le bouton "T2 + T3 (recommandé)" |
| 5 | Arrondissements | `3 4 9 10 11 12 13 14 18` (séparés par espace) |
| 6 | Mots-clés exclus | Tape `ok` pour garder le défaut `meuble, colocation, sous-location` (ou personnalise) |
| 7 | Seuil d'alerte | `70` (= pépite). `60` pour plus large, `80` pour ultra-strict |

Récap final → tape **✅ Oui go !**

Tu es inscrit. 🎉

### Étape 3 — Attends ta première pépite

Le bot scanne déjà. Dès qu'une annonce :
- est dans tes arrondissements
- respecte ton budget + surface
- passe le filtre Python (anti-meublé, anti-RDC, anti-arnaque)
- est scorée ≥ ton seuil par Claude

… elle arrive dans ton groupe avec :

- 📸 La photo principale
- 🛋️/🪑 Badge meublé / vide
- 💶 Loyer · 📐 Surface · 🧱 Type
- ✅ Atouts vus par Claude
- ⚠️ Red flags
- 🗨️ Verdict en 2 phrases
- 🔗 Bouton "Ouvrir l'annonce"

---

## 💬 Commandes utiles

| Commande | Effet |
|---|---|
| `/criteres` | Voir tes critères actuels |
| `/modifier` | Refaire le wizard (écrase) |
| `/pause` | Stopper les alertes (vacances, déjà trouvé, etc.) |
| `/reprendre` | Ré-activer |
| `/stats` | Ton bilan (annonces vues, alertes envoyées) |
| `/cle sk-ant-…` | Changer ta clé Anthropic |
| `/aide` | Liste des commandes |
| `/demande <question>` | Parler à Claude avec contexte ! Ex: `/demande pourquoi je reçois si peu d'alertes ?` |

---

## 🔒 Sécurité & vie privée

- Ta clé Anthropic est **chiffrée at-rest** (Fernet AES) dans la base
- Personne d'autre que toi ne peut la lire (même pas l'admin, sauf accès SSH au serveur)
- Personne ne voit tes critères ou tes alertes (chaque user a son groupe isolé)
- Tu peux **/pause** ou demander suppression à tout moment
- **Coût** : tu paies ta propre clé Anthropic. ~5-15 €/mois selon ton seuil.
  L'infra (Hetzner + Scrapfly + Iproyal) est offerte par l'admin.

---

## ❓ FAQ rapide

**Combien de temps avant la première alerte ?**
Variable. Marché vivant → quelques heures. Marché sec → jours.
Tape `/demande combien d'annonces vues aujourd'hui` pour voir l'activité.

**Le bot voit-il les annonces meublées ?**
Oui (depuis cette semaine), avec un badge 🛋️ MEUBLÉ bien visible.
Si tu veux les exclure, ajoute "meuble" dans tes mots-clés exclus (`/modifier`).

**Je ne reçois rien depuis 3 jours, c'est normal ?**
Possible si critères très stricts. Vérifie :
- `/stats` → si le bot voit bien des annonces mais aucune ne passe ton seuil
- `/demande conseille-moi pour élargir mes critères` → Claude te suggère

**Je peux changer le budget / la surface ?**
Oui, n'importe quand. Tape `/modifier` → refais le wizard.

**Les alertes me dérangent la nuit ?**
Mets le groupe Telegram en **silent** côté app (icône cloche en haut du
chat). Les pépites urgentes (`score ≥ 85`) feront tout de même `notif`.

---

## 🐛 Bug / question

Écris à l'admin du bot. Toujours utile :
- Capture du message bizarre
- `/stats` (ton compte + activité)
- Brève description de ce qui se passe vs ce que tu attendais
