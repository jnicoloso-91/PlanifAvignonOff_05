##########################
# Constantes application #
##########################

import datetime

# Variables globales de l'application
BASE_DATE = datetime.date(2000, 1, 1)
MARGE = datetime.timedelta(minutes=30)
PAUSE_DEJ_DEBUT_MIN = datetime.time(11, 0)
PAUSE_DEJ_DEBUT_MAX = datetime.time(14, 0)
PAUSE_DIN_DEBUT_MIN = datetime.time(19, 0)
PAUSE_DIN_DEBUT_MAX = datetime.time(21, 0)
DUREE_REPAS = datetime.timedelta(hours=1)
DUREE_CAFE = datetime.timedelta(minutes=30)
MAX_HISTORIQUE = 20

COLONNES_ATTENDUES = ["Date", "Debut", "Fin", "Duree", "Activite", "Lieu", "Relache", "Reserve", "Priorite", "Commentaire"]
COLONNES_ATTENDUES_ACCENTUEES = ["Date", "Début", "Fin", "Durée", "Activité", "Lieu", "Relâche", "Réservé", "Priorité", "Commentaire"]
COLONNES_TYPE_INT = ["Date", "Priorite"]
COLONNES_TYPE_STRING = ["Debut", "Fin", "Duree", "Activite", "Lieu"]
COLONNES_TYPE_OBJECT = ["Relache", "Reserve", "Commentaire"]
COLONNES_ATTENDUES_CARNET_ADRESSES = ["Nom", "Adresse", "Tel", "Web"]

RENOMMAGE_COLONNES = {
    "Debut": "Début",
    "Duree": "Durée",
    "Reserve": "Réservé",
    "Priorite": "Prio",
    "Relache": "Relâche",
    "Activite": "Activité",
}

RENOMMAGE_COLONNES_INVERSE = {
    "Début": "Debut",
    "Durée": "Duree",
    "Réservé": "Reserve",
    "Prio": "Priorite",
    "Relâche": "Relache",
    "Activité": "Activite",
}

ACTIVITES_PROGRAMMEES_WORK_COLS = ["__index", "__jour", "__options_date", "__non_reserve", "__uuid", "__sel_id", "__sel_ver", "__desel_ver", "__desel_id", "__sel_source", "__df_push_ver", "__addr_enc"]
ACTIVITES_NON_PROGRAMMEES_WORK_COLS = ["__index", "__options_date", "__uuid", "__sel_id", "__sel_ver", "__desel_ver", "__desel_id", "__sel_source", "__df_push_ver", "__addr_enc"]

LABEL_BOUTON_NOUVEAU = "Nouveau"
LABEL_BOUTON_SAUVEGARDER = "Sauvegarder"
LABEL_BOUTON_DEFAIRE = "Défaire"
LABEL_BOUTON_REFAIRE = "Refaire"
LABEL_BOUTON_NOUVELLE_ACTIVITE = "Nouvelle activité"
LABEL_BOUTON_NOUVELLE_ADRESSE = "Nouvelle adresse"
LABEL_BOUTON_SUPPRIMER = "Supprimer"
LABEL_BOUTON_CHERCHER_WEB = "Web"
LABEL_BOUTON_CHERCHER_ITINERAIRE = "Itinéraire"
LABEL_BOUTON_PROGRAMMER = "Programmer"
LABEL_BOUTON_REPROGRAMMER = "Reprogrammer"
LABEL_BOUTON_DEPROGRAMMER = "Déprogrammer"
LABEL_BOUTON_VALIDER = "Valider"
LABEL_BOUTON_ANNULER = "Annuler"
LABEL_BOUTON_TERMINER = "Terminer"
LABEL_BOUTON_EDITER = "Editer"

CENTRER_BOUTONS = True

# 🎨 Palette de styles pour les boutons colorés
PALETTE_COULEUR_PRIMARY_BUTTONS = {
    "info":    {"bg": "#dbeafe", "text": "#0b1220"},
    "error":   {"bg": "#fee2e2", "text": "#0b1220"},
    "warning": {"bg": "#fef3c7", "text": "#0b1220"},
    "success": {"bg": "#dcfce7", "text": "#0b1220"},
}

# Palette de couleurs jours
PALETTE_COULEURS_JOURS = {
    1: "#fce5cd",   2: "#fff2cc",   3: "#d9ead3",   4: "#cfe2f3",   5: "#ead1dc",
    6: "#f4cccc",   7: "#fff2cc",   8: "#d0e0e3",   9: "#f9cb9c",  10: "#d9d2e9",
    11: "#c9daf8",  12: "#d0e0e3",  13: "#f6b26b",  14: "#ffe599",  15: "#b6d7a8",
    16: "#a2c4c9",  17: "#b4a7d6",  18: "#a4c2f4",  19: "#d5a6bd",  20: "#e6b8af",
    21: "#fce5cd",  22: "#fff2cc",  23: "#d9ead3",  24: "#cfe2f3",  25: "#ead1dc",
    26: "#f4cccc",  27: "#d9d2e9",  28: "#b6d7a8",  29: "#d5a6bd",  30: "#f6b26b",
    31: "#d0e0e3"
}

# Couleur des activités programmables
COULEUR_ACTIVITE_PROGRAMMABLE = "#d9fcd9"  # ("#ccffcc" autre vert clair  "#cfe2f3" bleu clair)

DEBOUNCE_S = 0.30

SEL_REQUEST_DEFAUT = {"sel": {"ver": 0, "id": None, "pending": False}, "desel": {"ver": 0, "id": None, "pending": False}}

# Indique si l'on utilise Google Sheet pour gérer la persistence "Cold Start".
# On appelle Cold Start le démarrage intervenant lorsque l'appli a été mise en hibernation par la plateforme d'hébergement Streamlit Share 
# qui dans ces conditions recrée un container d'exécution vide - donc sans db SQLite - au démarrage de l'appli. Il faut alors recréer une 
# db SQLite et, ou bien repartir d'un contexte vide, ou bien, si la persistence "Cold Start" est activée, le réhydrater avec les données 
# enregistrées dans la Goggle Sheet. Pendant le fonctionnement de l'appli les données de la Google Sheet sont mises à jour par un thread 
# indépendant (GS Worker) qui dépile les commandes de mise à jour mises en file d'attente par les fonctions de sauvegarde SQLite. Streamlit 
# Share ne laisse pas persister le container d'exécution plus de 12h. Donc si l'on n'utilise pas la persistence Cold Start, il faut s'attendre 
# à devoir repartir d'un contexte vide et recharger un Excel de référence au moins tous les jours. La persistence Cold Start implique un 
# temps de démarrage plus long de quelques secondes (le temps de se connecter à la Google Sheet), mais une fois ce démarrage effectué le 
# temps de réponse de l'appli n'est pas dégradé pas de manière significative, la sauvegarde de données par le GS Worker s'effectuant en arrière 
# plan et les rerun Streamlit étant prioritaires par rapport au GS Worker.
WITH_GOOGLE_SHEET = True

