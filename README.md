# Shaarp48H

Afin d'en savoir plus sur les différentes parties, merci de vous référer aux readme présents dans les dossiers backend et frontend

## RGPD ##

1. Nature des données collectés 

L’application Exhibition Scraper Agent extrait des données accessibles publiquement depuis des sites de salons professionnels.

Les données susceptibles d’être collectées incluent :

Nom de l’entreprise
Description
Site web
Pays
Stand
Linkedin
Twitter / X
Catégories / tags
Email
Téléphone

Ces données peuvent être considérées comme des données à caractère personnel au sens du Règlement Général sur la Protection des Données si elles permettent d’identifier directement ou indirectement une personne physique.

2. Base légal du traitement 

L’intérêt légitime (article 6.1.f du RGPD) : collecte de données publiques dans un cadre professionnel (veille, prospection B2B, analyse de marché)

3. Principe de minimisation

L’application doit respecter le principe de minimisation :

    - Ne collecter que les données strictement nécessaires

4. Transparence et information

Les utilisateurs de l’outil doivent être informés :

Que les données proviennent de sources publiques
Que leur réutilisation doit respecter le RGPD

S'il y a une mise à jour de politiques, on met à jour la politique de confidentialité

5. Droits de personnes 

Les personnes concernées disposent de droits :

Droit d’accès
Droit de rectification
Droit d’opposition
Droit à l’oubli

6. Durée de conservation

Ne pas stocker les données indéfiniment, elle doivent être supprimé aprés un délai ou stocker temporairement.

7. Sécurité des données 

- Stockage sécurisée

- Accès restreint

- Protection contre les abus (rate limiting)

8. Scraping et légalité 

Le scraping doit respecter :

- Les conditions d’utilisation des sites

- Les restrictions techniques (anti-bot)

9. Responsabilité 

Niveaux de responsabilité :

- Développeurs : conception conforme envers l'utilisateur 

- Utilisateurs : usage conforme des données extraites

10. Privacy by Design

- Logs limités 

- filtrage des données


## Légalité du scraping ##

Le scraping n'est pas illégal en soi

Autorisé : 

- Scraper des données publiques accessibles sans authentification
- Utiliser les données à des fins personnelles ou éducatives
- Respecter les limitations techniques (rate limiting)

Non Autorisé : 

- Scraper des données protégées (compte, login)
- Ignorer les conditions d’utilisation du site
- Extraire des données personnelles (emails, téléphones…) → impact RGPD
- Surcharger un site (requêtes trop fréquentes)

Lois :

Le scraping est encadré par plusieurs notions :

- RGPD (Règlement Général sur la Protection des Données) : interdit de collecter et traiter des données personnelles sans base légale

- Droit des bases de données : protection contre l’extraction massive de données

- Concurrence déloyale et parasitisme : si le scraping reproduit un service existant


## Alternatives au scraping ##

1. API officielles

Certaines plateformes proposent des API permettant d’accéder aux données de manière légale.

Avantages :

- stabilité

- conformité légale

2. Open Data

De plus en plus d’organisations publient leurs données librement.

Avantages :

- données libres d’utilisation
- souvent déjà structurées
- aucune contrainte légale forte

3. Partenariat ou accés autorisé 

Dans un contexte professionnel, il est souvent préférable de :

- demander un accès officiel aux données
- établir un partenariat avec l’organisateur du salon

Avantages :

- accès fiable et complet
- aucune ambiguïté juridique
- meilleure qualité de données
