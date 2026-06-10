# Les Capacités Avancées de GitLab CI/CD

GitLab CI/CD est un outil extrêmement complet qui va bien au-delà de la simple exécution de tests et du déploiement. Puisque vous avez déjà une très bonne base de pipeline (tests, analyse de code, construction d'image avec Kaniko, déploiement SSH), voici comment vous pouvez explorer les autres dimensions de GitLab.

## 1. Documentation et GitLab Pages
Vous pouvez générer automatiquement votre documentation technique (via `Sphinx`, `MkDocs`, etc.) et l'héberger gratuitement grâce à **GitLab Pages**.
- **Comment ça marche** : Un job spécifique obligatoirement nommé `pages` génère des fichiers HTML statiques dans un dossier `public/`. GitLab se charge de déployer ce dossier sur une URL dédiée (ex: `https://votre-groupe.gitlab.io/votre-projet`).
- **Exemple avec MkDocs** :
  ```yaml
  pages:
    stage: deploy
    image: python:3.13
    script:
      - uv sync
      - uv run mkdocs build --site-dir public
    artifacts:
      paths:
        - public
    rules:
      - if: $CI_COMMIT_BRANCH == "master"
  ```

## 2. Génération automatique du Wiki GitLab
Chaque projet GitLab dispose d'un wiki intégré. Sous le capot, **ce wiki est en fait un dépôt Git à part entière** (son URL se termine généralement par `.wiki.git`).
- **Comment ça marche** : Vous pouvez créer un job CI qui génère des fichiers Markdown (par exemple à partir des docstrings de votre code), clone le dépôt Git du wiki, y copie les fichiers générés, et effectue un `git push`.
- **Prérequis** : Il faut utiliser un Token d'Accès de Projet (Project Access Token) configuré dans les variables CI/CD pour autoriser le pipeline à "pousser" vers le dépôt du wiki.

## 3. Sécurité et Rapports de Vulnérabilité
Bien que vous utilisiez déjà `bandit` et `uv audit`, GitLab propose ses propres analyseurs (SAST, DAST, Secret Detection) qui s'intègrent nativement à l'interface (onglet **Sécurité**).
- **Comment ça marche** : Au lieu d'écrire vos propres scripts, vous incluez les templates officiels de GitLab.
- **Exemple** :
  ```yaml
  include:
    - template: Security/SAST.gitlab-ci.yml
    - template: Security/Secret-Detection.gitlab-ci.yml
  ```
> [!NOTE]
> L'affichage des rapports de sécurité dans l'interface graphique (Dashboards de vulnérabilité) nécessite souvent l'édition GitLab Ultimate, mais les rapports dans les logs de pipeline (ou téléchargeables au format JSON) sont gratuits.

## 4. Gestion des Dépendances (SBOM)
Une SBOM (Software Bill of Materials) est une nomenclature logicielle : c'est la liste exhaustive des librairies (et de leurs versions) utilisées par votre projet.
- **Comment ça marche** : Vous générez un fichier au standard `CycloneDX` que GitLab va interpréter pour remplir l'onglet **Dependency List**.
- **Exemple** :
  ```yaml
  generate_sbom:
    stage: test
    script:
      - uv pip install cyclonedx-bom
      - uv run cyclonedx-py environment --outfile gl-sbom-python.cdx.json
    artifacts:
      reports:
        cyclonedx: gl-sbom-python.cdx.json
  ```

## 5. Planification de Pipeline (Schedules)
Les pipelines planifiés permettent d'exécuter des jobs à intervalles réguliers (comme une tâche Cron Linux), sans qu'il y ait de `git push`. C'est très utile pour des tâches lourdes ou de maintenance (audit de sécurité nocturne, nettoyage, etc.).
- **Comment ça marche** : Cela se configure dans l'interface de GitLab : **Build > Pipeline schedules**.
- Vous pouvez créer des jobs qui **ne tournent que** lors de ces événements planifiés :
  ```yaml
  nightly_audit:
    stage: check_code_quality
    script:
      - uv audit
    rules:
      - if: $CI_PIPELINE_SOURCE == "schedule"
  ```

## 6. Suivi des Erreurs (Error Tracking) et Alertes
GitLab peut devenir votre tour de contrôle pour surveiller l'application en production.
- **Error Tracking avec Sentry** : Vous pouvez instrumenter votre code Python (`import sentry_sdk`), puis lier votre projet Sentry à GitLab (**Monitor > Error Tracking**). Les erreurs de production s'afficheront dans GitLab, vous permettant de créer des tickets en un clic.
- **Alertes** : GitLab permet de recevoir des Webhooks depuis des outils de monitoring (comme Prometheus, Grafana ou Datadog). Si votre serveur tombe ou si le CPU sature, une Alerte est déclenchée dans GitLab (**Monitor > Alerts**) pour prévenir l'équipe.

## 7. Autres fonctionnalités notables
- **Review Apps** : Déployer automatiquement une copie de votre application pour **chaque** Merge Request (sur une URL temporaire). Cela permet de tester visuellement les changements avant de les valider.
- **Releases** : Vous pouvez automatiser la création des "Releases" (versions packagées avec les notes de mise à jour / changelog) dès qu'un Tag Git (ex: `v1.2.0`) est poussé.
