# Rapports de benchmark du retrieval

Deux campagnes d'optimisation Optuna ont servi à régler la brique de recherche
sémantique : quel modèle d'embedding, quelle taille de chunk, quel `top_k`, et
faut-il un reranker. Les fichiers bruts sont versionnés à côté de cette page.

| Campagne | Fichiers | Essais | Poids |
|---|---|---|---|
| v3 | [`v3/optuna_retrieval_trial_log.jsonl`](rapports/v3/optuna_retrieval_trial_log.jsonl) | 300 | 12,1 Mo |
| v5 | [`v5/optuna_retrieval_benchmark.csv`](rapports/v5/optuna_retrieval_benchmark.csv)<br>[`v5/optuna_retrieval_trial_log.jsonl`](rapports/v5/optuna_retrieval_trial_log.jsonl) | 300 (236 exploitables) | 11,0 Mo |

Le `.csv` est le tableau des essais tel qu'Optuna l'exporte : un essai par ligne,
ses paramètres en colonnes `params_*` et ses métriques en colonnes
`user_attrs_*`. Le `.jsonl` est le journal détaillé, une ligne par essai, avec
les chunks effectivement retrouvés pour chaque question — c'est lui qui permet
de rejouer une évaluation sans relancer le banc.

## Ce qui a été exploré (v5)

| Paramètre | Valeurs |
|---|---|
| `chunk_size` | 256, 512, 800, 1024 |
| `overlap_ratio` | 0.0, 0.15, 0.25, 0.3, 0.4 |
| `embedding_model` | arctic-l, bge-m3, e5-small, e5-base, e5-large, miniLM |
| `top_k` | 1 à 5, 10 |
| `use_reranker` | oui / non |
| `similarity_threshold` | continu, échantillonné sur 300 valeurs |

La métrique optimisée est `avg_mrr_in_scope_macro` — le MRR moyen sur les
questions dont la réponse est réellement dans le corpus, moyenné par question et
non par chunk, pour qu'une question à dix chunks ne pèse pas dix fois plus
lourd qu'une question à un seul.

## Meilleur essai

Essai **#177**, score **0,6499** :

| Paramètre | Valeur |
|---|---|
| `embedding_model` | arctic-l |
| `chunk_size` | 800 |
| `overlap_ratio` | 0.0 |
| `top_k` | 1 |
| `use_reranker` | oui |
| `similarity_threshold` | 0.095 |

Ses métriques : MRR document 0,729 (macro) / 0,707 (micro), rappel document
0,677 / 0,689, et surtout **0,0 hors périmètre** — aucune réponse inventée sur
les questions dont la réponse n'est pas dans le corpus.

## Meilleur essai par modèle

| Modèle | Essais | Meilleur score | `top_k` | `chunk_size` | Reranker |
|---|---:|---:|---:|---:|---|
| arctic-l | 194 | 0,6499 | 1 | 800 | oui |
| e5-base | 10 | 0,5722 | 2 | 800 | oui |
| e5-small | 12 | 0,5703 | 1 | 1024 | oui |
| **bge-m3** | 4 | 0,5469 | 10 | 800 | oui |
| e5-large | 3 | 0,5341 | 2 | 512 | oui |
| miniLM | 13 | 0,3883 | 2 | 800 | oui |

Le reranker gagne chez les six modèles, sans exception.

## À lire avec précaution

!!! warning "Ces chiffres ne décrivent pas la production actuelle"

    Le banc a été construit quand les embeddings tournaient en local, via
    `sentence-transformers`. La production utilise aujourd'hui **bge-m3 servi par
    l'API Cloudflare Workers AI**, et plus aucun modèle ne s'exécute localement.

    Trois conséquences :

    - **arctic-l, qui gagne le classement, n'est pas déployable** en l'état : il
      n'existe pas au catalogue Workers AI.
    - **bge-m3 n'a été tiré que 4 fois sur 236** essais exploitables. Son 0,5469
      est le meilleur de quatre tirages, pas un optimum : la comparaison avec les
      194 essais d'arctic-l n'est pas équitable.
    - Le **reranker** mesuré ici est un `CrossEncoder` local, absent du service
      déployé.

    Une campagne v6 restreinte à bge-m3 serait nécessaire pour régler
    `chunk_size`, `top_k` et le seuil sur la configuration réellement en service.

Les scripts qui ont produit ces rapports (`app/back/optuna_*.py`) importent
encore `sentence-transformers` et ne sont plus exécutables en l'état : ils sont
conservés pour mémoire, en attendant d'être repris ou retirés.
