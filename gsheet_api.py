####################
# API Google Sheet #
####################

###################################################################################################
# A adapter selon fonctionnement local ou hébergé avec streamlit share community cloud via GitHub #
# - Mettre True pour debugger en local sous VsCode                                                #
# - Mettre False avant d'intégrer dans GitHub                                                     #
###################################################################################################
LOCAL = True

import streamlit as st
import pandas as pd
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from google.oauth2.service_account import Credentials
import uuid

from app_const import COLONNES_ATTENDUES, COLONNES_ATTENDUES_CARNET_ADRESSES
from app_utils import curseur_normal, curseur_attente, minutes, to_iso_date, ajouter_options_date, get_meta
from sync_worker import gs_set_client_for_worker

def get_user_id():
    params = st.query_params
    user_id_from_url = params.get("user_id", [None])

    if user_id_from_url[0]:
        st.session_state["user_id"] = user_id_from_url

    if "user_id" not in st.session_state:
        st.write("Pour commencer, clique ci-dessous pour ouvrir ton espace personnel.")
        if "new_user_id" not in st.session_state:     
            st.session_state["new_user_id"] = str(uuid.uuid4())[:8]
        new_user_id = st.session_state.new_user_id
        if st.button("Créer ma session privée"):
            st.session_state["user_id"] = new_user_id
            st.query_params.update(user_id=new_user_id)
            st.rerun()  # Recharge la page avec le nouveau paramètre
        show_user_link(new_user_id)
        st.stop()

    return st.session_state["user_id"]

def show_user_link(user_id):
    app_url = "https://planifavignon-05-hymtc4ahn5ap3e7pfetzvm.streamlit.app/"  
    user_link = f"{app_url}/?user_id={user_id}"
    st.success("Voici ton lien personnel pour revenir plus tard :")
    st.code(user_link, language="text")
    st.download_button(
        label="💾 Télécharger mon lien",
        data=user_link,
        file_name=f"lien_{user_id}.txt"
    )
    
def get_gs_client():
    try:
        creds_dict = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Erreur de connexion à Google Sheets : {e}")
        return None

# @chrono
def get_or_create_user_gsheets(user_id, spreadsheet_id):
    gsheets = None
    client = get_gs_client()
    if client is not None:    
        try:
            sh = client.open_by_key(spreadsheet_id)
        except Exception as e:
            st.error(f"Impossible d'ouvrir la Google Sheet : {e}")
            st.stop()    

        # Adaptation sheet_names selon fonctionnement local ou hébergé
        if LOCAL: # Pour debugger en local
            sheet_names = [f"data", f"meta", f"adrs"]                                            
        else:     # Utilisation nominale en mode multiuser avec hébergement streamlit share community cloud
            sheet_names = [f"data_{user_id}", f"meta_{user_id}", f"adrs_{user_id}"]     

        gsheets = {}

        for name in sheet_names:
            try:
                ws = sh.worksheet(name)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=name, rows=1000, cols=20)
            gsheets[name.split("_")[0]] = ws  # 'data', 'meta', 'adrs'

    return gsheets

def connect():
    if "gsheets" not in st.session_state:

        try:
            user_id = get_user_id()
            curseur_attente()
            gsheets = get_or_create_user_gsheets(user_id, spreadsheet_id="1ytYrefEPzdJGy5w36ZAjW_QQTlvfZ17AH69JkiHQzZY")
            st.session_state.gsheets = gsheets
        except Exception as e:
            curseur_normal()
            print(f"Erreur à l'ouverture de la connexion avec la Google Sheet : {e}")
            st.stop()
    
    if "gsheets" in st.session_state:
        gs_set_client_for_worker(st.session_state.gsheets)

META_DICT = {
    "fn": None,
    "fp": None,
    "MARGE": None,
    "DUREE_REPAS": None,
    "DUREE_CAFE": None,
    "itineraire_app": None,
    "city_default": None,
    "traiter_pauses": None,
    "periode_a_programmer_debut": None,
    "periode_a_programmer_fin": None,
}

# 📥 Charge les infos persistées depuis la Google Sheet
def charger_contexte():

    def meta_getval(attr: str, meta_df: pd.DataFrame):
        val = meta_df.at[0, attr] if attr in meta_df.columns and len(meta_df) > 0 else None
        val = val if pd.notna(val) else None
        return val

    df = meta = ca= None
    if "gsheets" in st.session_state:
            
        gsheets = st.session_state.gsheets

        try:
            worksheet = gsheets["data"]
            df = get_as_dataframe(worksheet, evaluate_formulas=True)
            df.dropna(how="all")
        except Exception as e:
            print(f"Erreur au chargement du DataFrame depuis la Google Sheet : {e}")
        
        if not all(col in df.columns for col in COLONNES_ATTENDUES):
            print(f"Format de la Google Sheet invalide")
            df = pd.DataFrame(columns=COLONNES_ATTENDUES)

        try:
            worksheet = gsheets["meta"]
            meta_df = get_as_dataframe(worksheet, evaluate_formulas=True)
            meta = {k: meta_getval(k, meta_df) for k in META_DICT.keys()}
        except Exception as e:
            print(f"Erreur au chargement des métadonnées depuis la Google Sheet : {e}")
        
        try:
            worksheet = gsheets["adrs"]
            ca = get_as_dataframe(worksheet, evaluate_formulas=True)
        except Exception as e:
            print(f"Erreur au chargement du carnet d'adresses depuis la Google Sheet : {e}")
            ca = pd.DataFrame(columns=COLONNES_ATTENDUES_CARNET_ADRESSES)
    
    return df, meta, ca

# 📤 Sauvegarde l'ensemble des infos persistées dans la Google Sheet
def sauvegarder_contexte():
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets

            worksheet = gsheets["data"]
            worksheet.clear()
            df = ajouter_options_date(st.session_state.df)
            set_with_dataframe(worksheet, df)

            worksheet = gsheets["meta"]
            worksheet.clear()
            set_with_dataframe(worksheet, pd.DataFrame([get_meta()]))

            worksheet = gsheets["adrs"]
            worksheet.clear()
            set_with_dataframe(worksheet, st.session_state.ca)

        except Exception as e:
            print(f"Erreur gs_sauvegarder_contexte : {e}")

# 📤 Sauvegarde le DataFrame dans la Google Sheet
def sauvegarder_df():
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets
            worksheet = gsheets["data"]
            worksheet.clear()
            set_with_dataframe(worksheet, st.session_state.df)
        except Exception as e:
            print(f"Erreur gs_sauvegarder_df : {e}")

# Sauvegarde une ligne dans la Google Sheet
def sauvegarder_row(index_df):
    
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets
            worksheet = gsheets["data"]

            if "df" in st.session_state and st.session_state.df is not None:

                # row_df = DataFrame d'une seule ligne
                row_df = st.session_state.df.loc[[index_df]].copy()

                set_with_dataframe(
                    worksheet,                 # ta worksheet gspread
                    row_df,
                    row=int(index_df + 2),     # la ligne Google Sheet où écrire
                    include_column_header=False,
                    resize=False,
                )

        except Exception as e:
            print(f"Erreur gs_sauvegarder_row : {e}")

# 📤 Sauvegarde des params dans la Google Sheet
def sauvegarder_param(param):
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets

            worksheet = gsheets["meta"]
            if param == "MARGE":
                worksheet.update_acell("C2", minutes(st.session_state.MARGE))
            elif param == "DUREE_REPAS":
                worksheet.update_acell("D2", minutes(st.session_state.DUREE_REPAS))
            elif param == "DUREE_CAFE":
                worksheet.update_acell("E2", minutes(st.session_state.DUREE_CAFE))
            elif param == "itineraire_app":
                worksheet.update_acell("F2", st.session_state.itineraire_app)
            elif param == "city_default":
                worksheet.update_acell("G2", st.session_state.city_default)
            elif param == "traiter_pauses":
                worksheet.update_acell("H2", str(st.session_state.traiter_pauses))
            elif param == "periode_a_programmer_debut":
                worksheet.update_acell("I2", to_iso_date(st.session_state.periode_a_programmer_debut))
            elif param == "periode_a_programmer_fin":
                worksheet.update_acell("J2", to_iso_date(st.session_state.periode_a_programmer_fin))

        except Exception as e:
            print(f"Erreur gs_sauvegarder_param : {e}")

