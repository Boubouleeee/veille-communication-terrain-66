# Veille terrain 66

Application web statique de veille numérique consacrée à la communication terrain dans les Pyrénées-Orientales et en Occitanie. L’interface est publiée sur GitHub Pages; un collecteur Python sans dépendance payante lit les flux RSS/Atom publics explicitement autorisés et publie des données JSON, un historique et des synthèses Markdown.

> **Sources préconfigurées :** deux fils RSS officiels Service Public (DILA) sont activés : actualités professionnelles/entreprises et actualités générales. Ils apportent un contexte réglementaire national, et ne sont **pas** des sources locales propres aux Pyrénées-Orientales. Les signaux locaux n’apparaîtront que si des flux du territoire sont ajoutés après vérification. La note `notes/demo-semaine.md` est un exemple fictif isolé : elle n’alimente jamais les statistiques, les archives ou les résultats de recherche.

## Fonctionnalités

- Tableau de bord responsive avec recherche, filtres par thématique et période, indice de pertinence, indicateurs, aperçu d’activité et accès aux publications sources.
- Catégories marketing terrain, événementiel, territoires & institutions, emploi & appels d’offres.
- Favoris et pistes de prospection conservés dans le stockage local du navigateur; comparaison de trois signaux maximum.
- Export CSV des signaux filtrés et de l’historique; impression navigateur et enregistrement PDF.
- Historique journalier réel, comparaison des thèmes, notes générées et archives hebdomadaires immuables par semaine ISO.
- Références des publications et des flux formatées en APA 7 (auteur institutionnel, date, titre, site et URL), dans l’interface, les synthèses et les exports CSV.
- Collecte quotidienne, synthèse automatique le lundi à 09:00 heure de Paris et exécution manuelle depuis l’onglet **Actions**.
- État de collecte visible. En cas d’échec de tous les flux, la dernière collecte réussie est conservée et signalée comme telle; aucune donnée de remplacement n’est générée.
- Aucun compte tiers, clé secrète, service payant ou base de données requise.

## Démarrage local

Le site doit être servi par HTTP afin que le navigateur puisse lire les fichiers JSON. Depuis la racine du dépôt, avec Python 3.10 ou plus récent :

```bash
python -m http.server 8000
```

Ouvrir ensuite `http://localhost:8000`. Les fonctions du tableau de bord, les vues vides, les filtres, l’impression et l’export fonctionnent sans serveur d’API. Les favoris et pistes sont propres à ce navigateur et à cette adresse.

Exécuter le collecteur et ses tests :

```bash
python src/collect.py
python -m unittest discover -s tests -v
node --check assets/app.js
```

Le collecteur consulte quotidiennement les deux fils préconfigurés et filtre les publications par date et pertinence. Les actions programmées et les exécutions manuelles sont définies dans GitHub Actions. Un premier passage manuel peut être lancé avec `python src/collect.py`; il actualise les JSON et l’historique à partir des flux distants. En cas d’échec de tous les fils, les dernières données valides sont conservées et l’erreur est signalée. Si les deux sources sont désactivées, le statut devient `unconfigured`, sans inventer de statistiques.

## Ajouter une source : contrôle préalable obligatoire

N’ajoutez que des URL HTTPS de flux RSS/Atom mis publiquement à disposition par leur éditeur. Avant activation, vérifiez la page de conditions ou de licence, l’autorisation d’accès automatisé, les règles de réutilisation et les éventuelles limites de fréquence. N’utilisez pas de pages protégées, de comptes privés, de flux obtenus par contournement, d’API payante ou d’extraits complets d’articles. Gardez uniquement les métadonnées et un court extrait, conservez le lien d’origine, limitez les requêtes à une fois par jour et retirez rapidement toute source dont les conditions changent.

Ajoutez un objet à `config/sources.json` :

```json
{
  "id": "identifiant-stable",
  "name": "Nom de l’éditeur",
  "author": "Nom complet de l’auteur institutionnel",
  "site_name": "Nom du site",
  "apa_title": "Titre descriptif du flux [Flux RSS]",
  "url": "https://exemple.fr/flux-rss.xml",
  "homepage": "https://exemple.fr/",
  "description": "Brève description du périmètre du flux.",
  "category": "institutionnel",
  "location": "Pyrénées-Orientales",
  "terms_url": "https://exemple.fr/conditions",
  "terms_reviewed_at": "2026-11-25",
  "terms_authorized": true,
  "active": true
}
```

`author`, `site_name` et `apa_title` servent à générer les références APA 7; vérifiez le nom officiel de l’organisme et du site avant d’ajouter un flux. Les dates sans date de publication sont indiquées « s. d. ». `terms_authorized` est une attestation du mainteneur après vérification, pas une autorisation fournie par le programme. Les deux fils Service Public ci-dessus sont documentés par l’éditeur sur [sa page RSS](https://www.service-public.gouv.fr/P10008), qui indique que les actualités peuvent être rediffusées librement avec mention visible de `www.service-public.gouv.fr`. Cette attribution est rendue dans l’application via le nom de la source et le lien vers chaque publication. `terms_reviewed_at` doit être une date ISO `AAAA-MM-JJ`; revérifiez régulièrement les conditions. `category` accepte `marketing`, `evenementiel`, `institutionnel`, `emploi` ou `autre`. Une source inactive (`"active": false`) n’est pas récupérée. Les URL du flux et des conditions doivent être en HTTPS et ne peuvent pas viser `localhost` ou une IP privée.

Les fils préconfigurés couvrent la France entière, pas spécifiquement le 66. Le score privilégie les mots-clés du secteur et le territoire lorsqu’ils sont présents; il ne transforme pas une actualité nationale en signal local. Pour une couverture locale plus riche, ajoutez ensuite les flux des collectivités ou éditeurs locaux après vérification de leur URL et de leurs conditions de réutilisation.

Les éléments sont dédupliqués entre les flux par URL canonique (sans paramètres de suivi), limités aux 90 derniers jours par défaut, puis classés par un score heuristique de pertinence et de proximité. Ajustez `minimum_score` et `lookback_days` au niveau de la configuration. Le score sert uniquement à trier des publications; il ne représente ni leur véracité ni leur importance. L’absence de résultat ne signifie pas l’absence d’activité.

## Automatisations GitHub

Les workflows se trouvent dans `.github/workflows/` :

- `collect.yml` lit les flux chaque jour à 06:00 UTC. Il déclenche aussi un contrôle aux créneaux UTC 07:00 et 08:00 le lundi; le collecteur ne publie la note que lorsque l’heure locale est bien **lundi 09:00 Europe/Paris**. Les deux horaires UTC tiennent compte de l’heure d’été et de l’heure d’hiver. Le second contrôle est ignoré.
- Le menu **Actions → Collecte et publication de la veille → Run workflow** permet de choisir `collect` ou `weekly`. `weekly` publie depuis la dernière collecte réussie sans récupérer de flux; utilisez `collect` pour actualiser d’abord.
- Les données produites sont enregistrées dans le dépôt par `github-actions[bot]`. Une défaillance totale est enregistrée, l’ancienne collecte est conservée, et le workflow reste en échec visible.
- `pages.yml` assemble le site et le publie sur GitHub Pages à chaque mise à jour de l’application ou des données sur `main`.
- `tests.yml` exécute les tests Python et vérifie la syntaxe JavaScript.

Pour activer Pages : dans **Settings → Pages**, sélectionner **GitHub Actions** comme source de build. Dans **Settings → Actions → General → Workflow permissions**, autoriser l’écriture des contenus pour que le collecteur puisse enregistrer l’historique et les notes. Ne stockez aucun secret de source dans le dépôt; les flux publics prévus ici n’exigent pas de clé.

Les horaires de GitHub Actions sont approximatifs : GitHub peut retarder un déclenchement en période de charge. La vérification locale empêche une publication automatique à une mauvaise heure, et l’exécution manuelle `weekly` permet de publier en cas de retard.

## Fichiers et données publiées

| Chemin | Rôle |
| --- | --- |
| `index.html`, `assets/` | Application statique, styles et interactions |
| `config/sources.json` | Liste de sources dont les conditions ont été vérifiées |
| `data/latest.json` | Dernière collecte réussie |
| `data/collection-status.json` | Résultat et erreurs du passage le plus récent |
| `data/history.json` | Instantanés journaliers agrégés, sans données inventées |
| `notes/latest.md` | Synthèse hebdomadaire courante |
| `notes/weekly/YYYY-Www.md` | Archive par semaine ISO, créée une seule fois |
| `notes/demo-semaine.md` | Exemple rédactionnel explicitement fictif, non chargé par l’application |

Les indicateurs et graphiques sont calculés à partir de `data/latest.json` et `data/history.json`. Les fichiers `notes/demo-semaine.md` et `notes/weekly/index.json` ne sont pas des sources de statistiques. Les données de veille sont publiques par nature et versionnées avec le site; ne configurez pas de flux ou d’informations confidentielles.

## Accessibilité, confidentialité et limites

L’application fonctionne sur écran large comme sur mobile, propose une navigation clavier, des libellés accessibles, une mise en page imprimable et une recherche au raccourci `/`. Les notes sont affichées comme texte Markdown échappé; les publications ouvrent leur source dans un nouvel onglet. Seuls les favoris et statuts de prospection sont conservés localement; effacer les données du navigateur les supprime.

Le collecteur ne contourne pas les contrôles d’accès et n’extrait pas de contenus depuis les réseaux sociaux. La pertinence dépend des termes de recherche et de la qualité des flux configurés. Il ne garantit pas l’exhaustivité, la fraîcheur d’un flux, la véracité d’une publication, ni la conformité juridique d’une source : le mainteneur reste responsable de la sélection des sources et du respect de leurs conditions.
