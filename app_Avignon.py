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

    # # Si l'URL n'a pas ?user_id, reprendre celui mémorisé localement (WebApp/Safari)
    # components.html("""
    # <script>
    # (function(){
    # try{
    #     var url = new URL(window.location.href);
    #     if (!url.searchParams.get('user_id')) {
    #     var uid = localStorage.getItem('user_id');
    #     if (uid) {
    #         url.searchParams.set('user_id', uid);
    #         history.replaceState(null, '', url.toString());
    #         window.location.reload();
    #     }
    #     }
    # }catch(e){}
    # })();
    # </script>
    # """, height=0)

    # # 1) Si l'URL a déjà ?user_id → on mémorise côté client et on continue
    # uid = st.query_params.get("user_id", None)
    # tracer.log(f"st.query_params: {uid} {type(uid)}", types=["main"])
    # if uid:
    # # if st.query_params.get("user_id"):
    # #     uid = st.query_params["user_id"]
    # #     tracer.log(f"st.query_params: {uid}", types=["main"])
    #     # mémoriser pour les prochains lancements (WebApp)
    #     tracer.log(f"uid vrai", types=["main"])
    #     components.html(f"<script>localStorage.setItem('user_id','{uid}');</script>", height=0)
    # else:
    #     # 2) Sinon: on tente de LIRE le localStorage et de renvoyer la valeur à Python
    #     uid_local = components.html(
    #         """
    #         <script>
    #         (function(){
    #         try{
    #             const uid = localStorage.getItem('user_id') || "";
    #             // Renvoie la valeur à Streamlit (sans rediriger la page)
    #             Streamlit.setComponentValue(uid);
    #         }catch(e){ Streamlit.setComponentValue(""); }
    #         })();
    #         </script>
    #         """,
    #         height=0,
    #     )

    #     # 3) Si on a récupéré un user_id local → on l'applique à l'URL côté Python
    #     if uid_local:
    #         tracer.log(f"uid_local: {uid_local}", types=["main"])
    #         st.query_params.update(user_id=uid_local)
    #         st.rerun()

    #     # 4) Fallback: on demande à l'utilisateur
    #     st.write("Pour commencer, saisis ton *User ID* (environnement) :")
    #     st.session_state.setdefault("new_user_id", uuid.uuid4().hex[:8])
    #     typed = st.text_input("User ID", value=st.session_state["new_user_id"], label_visibility="collapsed")
    #     if st.button("OK") and typed:
    #         # a) URL source de vérité
    #         st.query_params.update(user_id=typed)
    #         # b) stocker pour les prochains lancements (WebApp)
    #         components.html(f"<script>localStorage.setItem('user_id','{typed}');</script>", height=0)
    #         st.rerun()
    #     st.stop()

    # # À partir d’ici, on est GARANTI d’avoir ?user_id dans l’URL
    # user_id = st.query_params.get("user_id", [None])
    # st.session_state["user_id"] = user_id
    # tracer.log(f"user_id: {user_id}", types=["main"])

    # if not st.query_params.get("user_id"):
    #     components.html("""
    #     <!doctype html>
    #     <meta charset="utf-8">
    #     <script>
    #     (function () {
    #     try {
    #         var url = new URL(window.location.href);
    #         if (!url.searchParams.get('user_id')) {
    #         var uid = localStorage.getItem('user_id') || "";
    #         if (uid) {
    #             // 1) Essai JS direct
    #             url.searchParams.set('user_id', uid);
    #             // On tente d'abord replaceState + reload (plus “doux”)
    #             try {
    #             history.replaceState(null, "", url.toString());
    #             window.location.reload();
    #             return;
    #             } catch(e) {}

    #             // 2) Fallback: navigation dure
    #             try {
    #             window.location.replace(url.toString());
    #             return;
    #             } catch(e) {}

    #             // 3) Fallback ultime: meta refresh (bypass CSP/iframe parfois)
    #             document.write('<meta http-equiv="refresh" content="0; url='
    #             + url.toString().replace(/&/g,'&amp;') + '">');
    #             return;
    #         }
    #         }
    #     } catch(e) {}
    #     // Si on arrive ici: pas d'user_id en localStorage -> laissons Python afficher le fallback
    #     })();
    #     </script>
    #     """, height=0)

    #     # Pas d'user_id en URL ni en localStorage -> on demande à l'utilisateur
    #     st.write("Pour commencer, saisis ton *User ID* (environnement) :")
    #     st.session_state.setdefault("new_user_id", uuid.uuid4().hex[:8])
    #     uid = st.text_input("User ID", value=st.session_state["new_user_id"], label_visibility="collapsed")
    #     if st.button("OK") and uid:
    #         # On mémorise pour les prochains lancements (WebApp incluse)
    #         components.html(f"<script>localStorage.setItem('user_id','{uid}');</script>", height=0)
    #         # On met la query (source de vérité) puis on relance
    #         st.query_params.update(user_id=uid)
    #         st.rerun()

    #     st.stop()

    # # ---------- À partir d'ici, on a ?user_id dans l'URL ----------
    # user_id = st.query_params.get("user_id")
    # tracer.log(f"user_id: {user_id}", types=["main"])
    # st.session_state["user_id"] = user_id
    # # (Optionnel) resynchroniser localStorage si on arrive via une URL signée
    # components.html(f"<script>localStorage.setItem('user_id','{user_id}');</script>", height=0)

    # # --- Gate: si l'URL n'a pas ?user_id, essayer de le reprendre du localStorage (top window) ---
    # st.markdown("""
    # <script>
    # (function(){
    # try {
    #     var url = new URL(window.location.href);
    #     if (!url.searchParams.get('user_id')) {
    #     var uid = window.localStorage.getItem('user_id') || "";
    #     if (uid) {
    #         url.searchParams.set('user_id', uid);
    #         if (history.replaceState) {
    #         history.replaceState(null, "", url.toString());
    #         window.location.reload();
    #         } else {
    #         window.location.replace(url.toString());
    #         }
    #     }
    #     }
    # } catch(e) {}
    # })();
    # </script>
    # """, unsafe_allow_html=True)

    # --- Gate: si pas de ?user_id, proposer ouverture ou création ---
    if not st.query_params.get("user_id"):
        # --- Bloc HTML : bouton "Ouvrir ma session" si déjà un user_id en localStorage ---
        st.markdown("""
        <div id="gate" style="font:16px system-ui,-apple-system,Segoe UI,Roboto,Arial; padding:2rem">
        <div id="hasUid" style="display:none">
            <p>Appuyer pour ouvrir votre session enregistrée.</p>
            <button id="openBtn" style="padding:.7rem 1rem">Ouvrir ma session</button>
        </div>
        <div id="noUid" style="display:none">
            <p>Aucune session enregistrée. Saisissez un identifiant ci-dessous.</p>
        </div>
        </div>
        <script>
        (function(){
        try{
            const uid = window.localStorage.getItem('user_id') || "";
            const has = !!uid;
            document.getElementById(has ? "hasUid" : "noUid").style.display = "block";
            if (has) {
            document.getElementById("openBtn").addEventListener("click", function(){
                try{
                const url = new URL(window.location.href);
                url.searchParams.set('user_id', uid);
                window.location.replace(url.toString());
                }catch(e){ window.location.reload(); }
            }, {once:true});
            }
        }catch(e){}
        })();
        </script>
        """, unsafe_allow_html=True)

        # --- Fallback Python: aucun ID stocké -> on le demande ---
        st.session_state.setdefault("new_user_id", uuid.uuid4().hex[:8])
        typed = st.text_input("User ID", value=st.session_state["new_user_id"], label_visibility="collapsed")

        if st.button("OK") and typed:
            # (1) mettre à jour l'URL (source de vérité)
            st.query_params.update(user_id=typed)
            # (2) stocker pour la prochaine ouverture (dans le vrai localStorage)
            st.markdown(f"""
            <script>
            try {{
                window.localStorage.setItem('user_id', {typed!r});
            }} catch(e) {{}}
            </script>
            """, unsafe_allow_html=True)
            # (3) relancer proprement
            st.rerun()

        st.stop()

    # --- À partir d’ici, on a un ?user_id valide ---
    user_id = st.query_params.get("user_id")
    st.session_state["user_id"] = user_id

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
    st.write(get_user_id())
    st.write(dict(st.query_params))

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
    
