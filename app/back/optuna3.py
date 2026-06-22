import optuna

# 1. Configurer le sampler taillé pour les grands espaces et les inter-dépendances
sampler = optuna.samplers.TPESampler(
    multivariate=True,      # Active la recherche des relations entre paramètres
    group=True,             # Optimise le calcul des probabilités croisées
    n_startup_trials=80,    # Augmente la phase de découverte (vu que tu as 1000 essais)
    seed=42                 # Rend le run reproductible pour ton rapport
)

# 2. Créer l'étude persistante sur le disque
study = optuna.create_study(
    study_name="rag_telecom_nancy_weekend",
    storage="sqlite:///weekend_benchmark.db", # Sauvegarde en temps réel
    direction="maximize",
    sampler=sampler,
    load_if_exists=True     # Si le PC redémarre, il reprend au dernier essai !
)

# 3. Lancer l'optimisation
study.optimize(
    objective, 
    n_trials=1000, 
    catch=(Exception,)      # Si un chunk foire, il passe au suivant sans crasher
)