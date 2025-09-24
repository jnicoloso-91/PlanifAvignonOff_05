######################
# Sections critiques #
######################

import streamlit as st

from app_metier import \
    maj_contexte, \
    modifier_cellule

from app_ui import \
    activites_programmees_modifier_cellule, \
    activites_programmees_deprogrammer, \
    activites_programmees_reprogrammer, \
    activites_non_programmees_modifier_cellule, \
    activites_non_programmees_programmer

# Gestion des sections critiques de traitement.
# Ces sections critiques sont utilisées notamment pour gérer la modification de cellules depuis les grilles.
# Dans ce cas en effet la modification de cellule depuis la grille est validée par un click row 
# qui peut entraîner une interruption du script python et donc une incohérence de contexte.
# Le mécanisme de section critique permet une relance automatique du traitement jusqu'à complétion 
# en cas d'interruption par un rerun Streamlit : une commande est enregistrée dans st.session_state 
# et est automatiquement relancée en début de rerun par la fonction ci-dessous tant qu'elle n'est pas terminée.
def traiter_sections_critiques():

    cmd = st.session_state.get("bd_maj_contexte_cmd")
    if cmd:
        maj_contexte(cmd["maj_donnees_calculees"], cmd["maj_options_date"])
    
    cmd = st.session_state.get("bd_modifier_cellule_cmd")
    if cmd:
        modifier_cellule(cmd["idx"], cmd["col"], cmd["val"])
    
    cmd = st.session_state.get("activites_programmees_modifier_cellule_cmd")
    if cmd:
        activites_programmees_modifier_cellule(cmd["idx"], cmd["col"], cmd["val"])
    
    cmd = st.session_state.get("activites_programmees_deprogrammer_cmd")
    if cmd:
        activites_programmees_deprogrammer(cmd["idx"])
    
    cmd = st.session_state.get("activites_programmees_reprogrammer_cmd")
    if cmd:
        activites_programmees_reprogrammer(cmd["idx"], cmd["jour"])
    
    cmd = st.session_state.get("activites_non_programmees_modifier_cellule_cmd")
    if cmd:
        activites_non_programmees_modifier_cellule(cmd["idx"], cmd["col"], cmd["val"])
    
    cmd = st.session_state.get("activites_non_programmees_programmer_cmd")
    if cmd:
        activites_non_programmees_programmer(cmd["idx"], cmd["jour"])
