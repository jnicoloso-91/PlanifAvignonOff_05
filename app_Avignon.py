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
import streamlit.components.v1 as components

# Trace le début d'un rerun
def rerun_trace():
    st.session_state.setdefault("main_counter", 0)
    st.session_state.main_counter += 1
    tracer.log(f"____________MAIN {st.session_state.main_counter}______________", types=["gen","main"])

# Script de promotion de #user_id/#session_id en ?user_id/?session_id (sans écraser la query existante) dans l'URL de connexion.
# Permet de spécifier une URL de connexion utilisant #user_id au lieu de ?user_id pour le bon fonctionnement en mode WebApp sur IOS.
# En effet en mode WebApp le ?user_id est ecrasé de l'URL et sans ce workaround l'appli ne pourrait pas démarrer avec une spécification de user_id.
def promote_hash_user_id_for_webapp_mode():

    # -------- GATE: promouvoir #user_id -> ?user_id AVANT tout le reste --------
    if not st.query_params.get("user_id"):
        # Injecte un vrai <script> via components.html (fiable sur Firefox/Streamlit Cloud)
        components.html(
            """
            <script>
            (function () {
            try {
                var url  = new URL(window.location.href);
                var q    = url.searchParams;
                var hash = url.hash ? url.hash.substring(1) : "";
                var hp   = new URLSearchParams(hash);
                var uid  = hp.get("user_id");

                // Si on a #user_id et PAS ?user_id -> promotion + reload
                if (uid && !q.get("user_id")) {
                q.set("user_id", uid);
                url.hash = "";
                // Remplace l'URL puis force un reload pour que Python voie la query
                if (history.replaceState) {
                    history.replaceState(null, "", url.toString());
                    window.location.reload();
                } else {
                    window.location.replace(url.toString());
                }
                return;
                }
            } catch (e) {}
            })();
            </script>
            """,
            height=0,  # pas d'UI
        )

        # UI de secours si aucun #user_id fourni (première visite)
        st.write("Pour commencer, clique ci-dessous pour ouvrir ton espace personnel.")
        if "new_user_id" not in st.session_state:
            import uuid
            st.session_state["new_user_id"] = uuid.uuid4().hex[:8]
        new_uid = st.session_state["new_user_id"]

        if st.button("Créer ma session privée"):
            st.query_params.update(user_id=new_uid)
            st.rerun()

        st.stop()  # IMPORTANT: on bloque l'exécution tant que la promo n'a pas eu lieu


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

    # Récupération du user_id dans l'URL de connexion
    promote_hash_user_id_for_webapp_mode()
    user_id = get_user_id()
  
    # Connexion à la Google Sheet et lancement du GS Worker chargé de la sauvegarde Google Sheet en temps masqué (seulement si WITH_GOOGLE_SHEET est True)
    if WITH_GOOGLE_SHEET:
        gs.connect(user_id)
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
    
