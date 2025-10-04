###############
# Undo / Redo #
###############

import streamlit as st
from collections import deque
import copy

from app_const import *
from app_utils import ajouter_options_date, demander_selection, forcer_reaffichage_df, get_meta, set_meta
import tracer
import sql_api as sql 

# Réactive les sélections dans les grilles à partir d'un snapshot
def _sel_request_update_from_snapshot(snapshot):
    if snapshot["activites_programmees_sel_request"]["sel"]["id"] is not None:
        demander_selection("activites_programmees", snapshot["activites_programmees_sel_request"]["sel"]["id"], deselect="activites_non_programmees")
    elif snapshot["activites_non_programmees_sel_request"]["sel"]["id"] is not None:
        demander_selection("activites_non_programmees", snapshot["activites_non_programmees_sel_request"]["sel"]["id"], deselect="activites_programmees")
    if snapshot["creneaux_disponibles_sel_request"]["sel"]["id"] is not None:
        demander_selection("creneaux_disponibles", snapshot["creneaux_disponibles_sel_request"]["sel"]["id"])
    if snapshot["activites_programmables_sel_request"]["sel"]["id"] is not None:
        demander_selection("activites_programmables", snapshot["activites_programmables_sel_request"]["sel"]["id"])
    
# Initialise les listes d'undo redo
def init(verify=True):
    if "historique_undo" not in st.session_state or "historique_redo" not in st.session_state or not verify:
        st.session_state.historique_undo = deque(maxlen=MAX_HISTORIQUE)
        st.session_state.historique_redo = deque(maxlen=MAX_HISTORIQUE)

# Sauvegarde du contexte courant
def save():
    df = st.session_state.get("df", None)
    if df is None:
        return      
    df_copy = st.session_state.df.copy(deep=True)
    df_copy = ajouter_options_date(df_copy)
    ca_copy = st.session_state.ca.copy()
    menu_activites_copy = st.session_state.menu_activites.copy()
    menu_activites_copy["df"] = df_copy
    activites_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_programmees_sel_request)
    activites_non_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_non_programmees_sel_request)
    creneaux_disponibles_sel_request_copy = copy.deepcopy(st.session_state.creneaux_disponibles_sel_request)
    activites_programmables_sel_request_copy = copy.deepcopy(st.session_state.activites_programmables_sel_request)

    snapshot = {
        "df": df_copy,
        "ca": ca_copy,
        "meta": get_meta(),
        "activites_programmees_sel_request": activites_programmees_sel_request_copy,
        "activites_non_programmees_sel_request": activites_non_programmees_sel_request_copy,
        "creneaux_disponibles_sel_request": creneaux_disponibles_sel_request_copy,
        "activites_programmables_sel_request": activites_programmables_sel_request_copy,
        "menu_activites": menu_activites_copy,
    }
    st.session_state.historique_undo.append(snapshot)
    st.session_state.historique_redo.clear()

# Préparation d'une sauvegarde différée du contexte courant
def save_prepare():
    df = st.session_state.get("df", None)
    if df is None:
        return      
    df_copy = st.session_state.df.copy(deep=True)
    df_copy = ajouter_options_date(df_copy)
    ca_copy = st.session_state.ca.copy()
    menu_activites_copy = st.session_state.menu_activites.copy()
    menu_activites_copy["df"] = df_copy
    activites_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_programmees_sel_request)
    activites_non_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_non_programmees_sel_request)
    creneaux_disponibles_sel_request_copy = copy.deepcopy(st.session_state.creneaux_disponibles_sel_request)
    activites_programmables_sel_request_copy = copy.deepcopy(st.session_state.activites_programmables_sel_request)

    snapshot = {
        "df": df_copy,
        "ca": ca_copy,
        "meta": get_meta(),
        "activites_programmees_sel_request": activites_programmees_sel_request_copy,
        "activites_non_programmees_sel_request": activites_non_programmees_sel_request_copy,
        "creneaux_disponibles_sel_request": creneaux_disponibles_sel_request_copy,
        "activites_programmables_sel_request": activites_programmables_sel_request_copy,
        "menu_activites": menu_activites_copy,
    }
    st.session_state.snapshot = snapshot


# Finalisation d'une sauvegarde différée du contexte courant
def save_finalize():
    snapshot = st.session_state.get("snapshot", None)
    if snapshot: 
        st.session_state.historique_undo.append(snapshot)
        st.session_state.historique_redo.clear()

# Undo
def undo():
    if st.session_state.historique_undo:
        df_copy = st.session_state.df.copy(deep=True)
        df_copy = ajouter_options_date(df_copy)
        ca_copy = st.session_state.ca.copy()
        menu_activites_copy = st.session_state.menu_activites.copy()
        menu_activites_copy["df"] = df_copy
        activites_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_programmees_sel_request)
        activites_non_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_non_programmees_sel_request)
        creneaux_disponibles_sel_request_copy = copy.deepcopy(st.session_state.creneaux_disponibles_sel_request)
        activites_programmables_sel_request_copy = copy.deepcopy(st.session_state.activites_programmables_sel_request)

        current = {
            "df": df_copy,
            "ca": ca_copy,
            "meta": get_meta(),
            "activites_programmees_sel_request": activites_programmees_sel_request_copy,
            "activites_non_programmees_sel_request": activites_non_programmees_sel_request_copy,
            "creneaux_disponibles_sel_request": creneaux_disponibles_sel_request_copy,
            "activites_programmables_sel_request": activites_programmables_sel_request_copy,
            "menu_activites": menu_activites_copy,
        }
        st.session_state.historique_redo.append(current)
        
        snapshot = st.session_state.historique_undo.pop()
        st.session_state.df = snapshot["df"]
        st.session_state.ca = snapshot["ca"]
        set_meta(snapshot["meta"])
        _sel_request_update_from_snapshot(snapshot)
        st.session_state.menu_activites = snapshot["menu_activites"]
        st.session_state.activites_programmables_select_auto = False 
        from app_metier import maj_contexte
        maj_contexte(maj_donnees_calculees=False, maj_options_date=False)
        # forcer_reaffichage_df("creneaux_disponibles")
        sql.sauvegarder_contexte()
        st.rerun()

# Redo
def redo():
    if st.session_state.historique_redo:
        df_copy = st.session_state.df.copy(deep=True)
        df_copy = ajouter_options_date(df_copy)
        ca_copy = st.session_state.ca.copy()
        menu_activites_copy = st.session_state.menu_activites.copy()
        menu_activites_copy["df"] = df_copy
        activites_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_programmees_sel_request)
        activites_non_programmees_sel_request_copy = copy.deepcopy(st.session_state.activites_non_programmees_sel_request)
        creneaux_disponibles_sel_request_copy = copy.deepcopy(st.session_state.creneaux_disponibles_sel_request)
        activites_programmables_sel_request_copy = copy.deepcopy(st.session_state.activites_programmables_sel_request)

        current = {
            "df": df_copy,
            "ca": ca_copy,
            "meta": get_meta(),
            "activites_programmees_sel_request": activites_programmees_sel_request_copy,
            "activites_non_programmees_sel_request": activites_non_programmees_sel_request_copy,
            "creneaux_disponibles_sel_request": creneaux_disponibles_sel_request_copy,
            "activites_programmables_sel_request": activites_programmables_sel_request_copy,
            "menu_activites": menu_activites_copy,
        }
        st.session_state.historique_undo.append(current)
        
        snapshot = st.session_state.historique_redo.pop()
        st.session_state.df = snapshot["df"]
        st.session_state.ca = snapshot["ca"]
        set_meta(snapshot["meta"])
        _sel_request_update_from_snapshot(snapshot)
        st.session_state.menu_activites = snapshot["menu_activites"]
        st.session_state.activites_programmables_select_auto = False 
        from app_metier import maj_contexte
        maj_contexte(maj_donnees_calculees=False, maj_options_date=False)
        # forcer_reaffichage_df("creneaux_disponibles")
        sql.sauvegarder_contexte()
        st.rerun()

