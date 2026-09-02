# Où partent les données, et ce que coûterait de les rapatrier

Cette page répond à une objection reçue sur le projet : les conversations
partent-elles en Chine ? Elle sépare ce qui relève du transfert réel de ce qui
relève de l'origine des poids d'un modèle — les deux n'ont ni la même
conséquence juridique ni le même coût à corriger.

## Le trajet réel d'une question

| Étape | Destination | Pays | Ce qui y transite |
|---|---|---|---|
| Vectorisation | Workers AI (`@cf/baai/bge-m3`) | Cloudflare, edge | la question |
| Recherche | Qdrant Cloud | GCP `europe-west3` (Francfort) | le vecteur de la question |
| Reclassement | Workers AI (`@cf/baai/bge-reranker-base`) | Cloudflare, edge | question + extraits |
| Génération | Groq (`qwen/qwen3.6-27b`) | États-Unis | question + contexte + historique |
| Stockage | PostgreSQL du VPS | serveur du projet | la conversation entière |

Deux confusions fréquentes méritent d'être levées.

**Les modèles `bge-*` et `qwen` sont d'origine chinoise, mais aucune donnée ne
part en Chine.** Ce sont des poids ouverts, publiés par BAAI et Alibaba, qui
s'exécutent sur du matériel Cloudflare et Groq. Le nom du modèle ne dit rien de
la destination des octets.

**Le seul transfert vers la Chine était le fournisseur DeepSeek**, joignable via
le pool de clés (`api.deepseek.com`, société chinoise, CGU autorisant
l'entraînement sur les entrées). C'est le point qui justifiait l'objection.

## Ce qui a été mesuré

L'hypothèse testée : déplacer la génération chez Mistral AI — société
française, traitement en UE, donc RGPD en droit direct — sans perdre en
qualité de réponse.

Deux bancs déjà existants ont été rejoués, sans rien changer au reste de la
chaîne : mêmes questions, mêmes contextes, même recherche.

### Qualité de génération

Questions réelles tirées du journal d'usage, contexte récupéré une seule fois
puis servi à tous les modèles — on compare des générateurs, pas des recherches.
Notation par un juge d'une famille tierce (`llama-3.3-70b` sur Workers AI), sur
les 19 questions communes aux quatre modèles. Les demandes de carte des mers
sont écartées : elles relèvent d'une autre fonctionnalité et sont refusées par
tous.

| Modèle | Hébergement | Fidélité | Pertinence | Complétude | Ton | Global | Sortie |
|---|---|---|---|---|---|---|---|
| `qwen/qwen3.6-27b` *(prod)* | Groq, US | 0,800 | 0,811 | 0,626 | 0,753 | **0,747** | 94 tok |
| `mistral-small-2603` | Mistral, FR | 0,779 | 0,805 | 0,626 | 0,705 | **0,729** | 94 tok |
| `mistral-medium-3.5` | Mistral, FR | 0,753 | 0,747 | 0,579 | 0,637 | **0,679** | 66 tok |
| `openai/gpt-oss-120b` | Groq, US | 0,853 | 0,832 | 0,689 | 0,811 | **0,796** | 154 tok |

En comparaison appariée question par question contre la production :

| Modèle | Écart global | Gagne | Perd | Nul |
|---|---|---|---|---|
| `mistral-small-2603` | −0,018 | 5 | 7 | 7 |
| `mistral-medium-3.5` | −0,068 | 2 | 9 | 8 |
| `openai/gpt-oss-120b` | +0,049 | 12 | 4 | 3 |

Le petit modèle fait mieux que le gros, ce qui surprend jusqu'à lire les
réponses : `mistral-medium-3.5` est nettement plus laconique (66 tokens contre
94) et abandonne le registre du chat, répondant à côté sur les questions de
lore. C'est sa note de ton qui décroche, pas sa compréhension.

### Résistance à l'injection de prompt

Corpus d'attaques du challenge social, 37 attaques réparties en dix familles.
Une fuite est un flag correctement extrait.

| Fournisseur | Fuites | Familles touchées |
|---|---|---|
| Ligne de base | 4/37 | `fiction` (4/8) |
| Mistral | 5/37 | `fiction` (4/8), `injection-contexte` (1/2) |

Une attaque d'écart sur 37 : les deux se valent. La famille `fiction` reste la
faiblesse du challenge lui-même, indépendamment du modèle.

## Ce que ça donne

`mistral-small-2603` tient la comparaison — l'écart de 0,018 est dans le bruit
d'un jury de 19 questions noté par un LLM, et sa résistance à l'injection est
équivalente. `mistral-medium-3.5` est écarté : plus cher et moins bon.

Une réserve qui ne se lit pas dans les moyennes : les deux modèles Mistral
inventent davantage. Sur des questions où la fiche officielle donne la réponse,
ils ont produit des affirmations que le contexte ne soutenait pas — un
événement daté qui n'existe pas, une contradiction directe d'une fiche. Pour un
RAG dont la promesse est de n'avoir aucune connaissance propre, c'est plus grave
qu'un point de ton. La note de fidélité le montre en partie (0,779 et 0,753
contre 0,800) mais la sous-estime.

Dix-neuf questions restent un signal, pas une preuve. Élargir le jeu de
questions avant d'arbitrer coûte peu : le banc reprend là où il s'arrête.

## Ce qui resterait hors de France

Déplacer la génération ne ferme pas tout. La question de l'étudiant continue de
transiter par Cloudflare pour être vectorisée et reclassée, et l'index Qdrant
est hébergé sur GCP à Francfort — en UE, donc, mais sur l'infrastructure d'une
société américaine.

Fermer le premier point supposerait de quitter Workers AI pour les embeddings,
donc de changer de modèle de vectorisation, donc de ré-indexer entièrement le
corpus : les dimensions ne correspondent pas d'un modèle à l'autre. C'est le
poste de coût principal d'une souveraineté complète, et il est sans rapport avec
le choix du modèle de chat.

Enfin, aucune API ne garantit que « personne ne peut lire » les données. Ce
qu'un fournisseur français apporte, c'est un traitement en France, un
engagement contractuel de non-entraînement et le RGPD applicable directement.
L'inviolabilité réelle demanderait un modèle auto-hébergé, que ce projet exclut
par construction.
