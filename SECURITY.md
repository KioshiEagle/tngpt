# Politique de sécurité

## Versions supportées

TN-GPT n'a pas de politique de versions maintenues en parallèle : seule la dernière version présente sur la branche `main` (celle déployée en production) est supportée en matière de correctifs de sécurité.

| Version | Supportée |
| ------- | --------- |
| `main`  | ✅ |
| autre / historique | ❌ |

## Signaler une vulnérabilité

Merci de **ne pas** ouvrir d'issue publique pour signaler une faille de sécurité.

Utilisez l'onglet **[Security](../../security/advisories/new)** de ce dépôt GitHub et cliquez sur **"Report a vulnerability"** (Private Vulnerability Reporting). Cela ouvre un rapport privé, visible uniquement par les mainteneurs, dans lequel vous pouvez décrire :

- le composant concerné et une description de la vulnérabilité,
- les étapes pour la reproduire,
- l'impact potentiel (accès aux données des utilisateurs, contournement de l'authentification, etc.),
- si possible, un correctif ou une piste de correction.

### Ce à quoi vous pouvez vous attendre

TN-GPT est un projet étudiant (TELECOM Nancy), sans SLA commercial. Nous nous engageons néanmoins, sur une base de bonne foi, à :

- accuser réception du rapport,
- évaluer et confirmer (ou infirmer) la vulnérabilité,
- corriger les failles confirmées et vous tenir informé de l'avancement,
- vous créditer dans l'avis de sécurité si vous le souhaitez, une fois le correctif publié.

## Périmètre

Sont concernés : le code de ce dépôt (application Flask, pipeline d'ingestion, Dockerfile, workflows CI/CD) ainsi que sa configuration de déploiement. Les vulnérabilités dans des dépendances tierces doivent être signalées directement aux projets concernés (Dependabot nous alerte déjà automatiquement sur celles-ci).
