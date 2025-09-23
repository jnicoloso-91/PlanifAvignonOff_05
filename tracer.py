###############
# Debug trace #
###############

import logging
import streamlit as st
import inspect

TRACER_MODE = True
TRACE_TYPES = [
    "main", 
    #"event", 
    # "demander_selection", 
    # "demander_deselection", 
    # "afficher_activites_programmees",
    # "afficher_activites_non_programmees",
    # "afficher_df",
    # "sel_source",
    "_gs_push_full",
    "_gs_push_df",
    "_gs_push_row",
    "_gs_push_param",
    "wk",
    # "bd_maj_contexte",
    ]  # "all" ou liste des types de trace / noms de fonctions à afficher

def get_logger(nom):
    if "logger" not in st.session_state:
        # Crée un logger
        logger = logging.getLogger(nom)
        logger.setLevel(logging.DEBUG)

        if logger.hasHandlers():
            logger.handlers.clear()

        # Ajoute le handler
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        st.session_state.logger = logger

    return st.session_state.logger

def log(trace="", types=["all"]):
    def get_caller_name():
        return inspect.stack()[2].function
    logger = get_logger("_app")
    caller_name = get_caller_name()
    types_requested = [s.lower() for s in TRACE_TYPES]
    types = [s.lower() for s in types]
    types.append(caller_name)
    if TRACER_MODE and ("all" in types_requested or any(x in types_requested for x in types)):
        logger.debug(f"{caller_name}: {trace}") 
