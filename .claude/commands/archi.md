---
description: Produit une architecture implémentable, sans implémentation
---

Tu es en phase de conception. Tu n'écris AUCUNE implémentation.

Besoin : $ARGUMENTS

D'abord : pose-moi uniquement les questions dont la réponse
changerait l'architecture. Attends mes réponses.

Ensuite produis :
1. Responsabilité de chaque classe, en une phrase
2. Squelette : signatures typées + docstrings, corps réduits à `...`
3. Relations : composition ou héritage, justifié à chaque fois
4. Séquence du cas d'usage principal
5. Invariants et cas d'erreur
6. Deux architectures alternatives écartées + le critère décisif
7. Ordre d'implémentation, avec le test qui valide chaque étape

Contraintes :
- Aucune abstraction sans un second cas d'usage concret qui la justifie
- Composition et Protocol par défaut ; héritage réservé à un vrai
  is-a avec implémentation partagée
- Si une fonction suffit, dis-le au lieu de proposer une classe