########
# Main #
########

import streamlit as st
# import pkg_resources

from app_const import *
from app_utils import *
from app_metier import *
from app_ui import *
from carnet_addr import *
import tracer
from sections_critiques import traiter_sections_critiques
import sql_api as sql 
import gsheet_api as gs
import sync_worker as wk

# Trace le début d'un rerun
def rerun_trace():
    st.session_state.setdefault("main_counter", 0)
    st.session_state.main_counter += 1
    tracer.log(f"____________MAIN {st.session_state.main_counter}______________", types=["gen","main"])

# Opérations à ne faire qu'une seule fois au boot de l'appli
@st.cache_resource
def app_boot():

    cold_start = not sql.db_exists()
    tracer.log(f"Cold Start {cold_start}")

    # DEBUG ONLY - Reset DB
    # with sqlite3.connect(DB_PATH) as con:
    #     cur = con.cursor()
    #     # supprime les tables si elles existent
    #     cur.executescript("""
    #         DROP TABLE IF EXISTS df_principal;
    #         DROP TABLE IF EXISTS meta;
    #         DROP TABLE IF EXISTS carnet;
    #     """)
    #     con.commit()
    # DEBUG ONLY - Reset DB

    sql.init_db()                           # Crée les tables si besoin
    if cold_start and WITH_GOOGLE_SHEET:    # Hydratation des tables avec les données Google Sheet en cas de cold start et si Google Sheet est utilisé
        charger_contexte_depuis_gsheet()
        sql.sauvegarder_contexte(enqueue=False)

def main():

    # Affichage de la version de streamlit-aggrid
    # import pkg_resources
    # version = pkg_resources.get_distribution("streamlit-aggrid").version
    # st.write("Version streamlit-aggrid :", version)

    # Trace le début d'un rerun
    rerun_trace()
  
    # Connexion à la Google Sheet et lancement du GS Worker chargé de la sauvegarde Google Sheet en temps masqué (seulement si WITH_GOOGLE_SHEET est True)
    if WITH_GOOGLE_SHEET:
        gs.connect()
        wk.ensure_worker_alive()

    # Opérations à ne faire qu'une seule fois au démarrage appli 
    app_boot()

    # Chargement de contexte depuis SqLite en charge de la persistence à chaud 
    # (à faire à chaque rerun pour tenir compte des reinit de st.session_state en cours de session)
    charger_contexte_depuis_sql()

    # Gestion des sections critiques
    traiter_sections_critiques()

    # Configuration de la page HTML
    initialiser_page()

    # Affichage du titre
    afficher_titre("Planificateur Avignon Off")

    # Affichage de la sidebar
    afficher_sidebar()

   # Si le contexte est valide, on le traite
    if est_contexte_valide():

        # Affichage des infos générales
        afficher_infos_generales()
        
        # Affichage des activités programmées
        afficher_activites_programmees()

        # Affichage des activités non programmées
        afficher_activites_non_programmees()

        # Affichage des créneaux disponibles et des activités programmables
        afficher_creneaux_disponibles()      

        # Affichage du carnet d'adresses
        afficher_ca()      

        # Affichage du menu activité de la sidebar
        afficher_menu_activite()

        # Affichage du menu carnet d'adresse de la sidebar
        afficher_menu_ca()
    
        # Affichage du statut du GS worker thread
        afficher_worker_status_discret()
    else:
        message = st.session_state.get("contexte_invalide_message")
        if message is not None:
            st.error(st.session_state.get("contexte_invalide_message"))

if __name__ == "__main__":
    main()
    
