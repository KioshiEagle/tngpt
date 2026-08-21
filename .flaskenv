# Sans ça, le CLI Flask découvre le paquet `app/` et n'y trouve aucune
# application : toutes les commandes `flask` échouent hors du conteneur.
FLASK_APP=main
