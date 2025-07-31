import streamlit as st
import pandas as pd
import datetime
import io
import re
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font
import requests
from bs4 import BeautifulSoup
from collections import deque
import pandas.api.types as ptypes
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from google.oauth2.service_account import Credentials
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode
from io import BytesIO
import uuid
import math

# Variables globales
BASE_DATE = datetime.date(2000, 1, 1)
MARGE = datetime.timedelta(minutes=30)
PAUSE_DEJ_DEBUT_MIN = datetime.time(11, 0)
PAUSE_DEJ_DEBUT_MAX = datetime.time(14, 0)
PAUSE_DIN_DEBUT_MIN = datetime.time(19, 0)
PAUSE_DIN_DEBUT_MAX = datetime.time(21, 0)
DUREE_REPAS = datetime.timedelta(hours=1)
DUREE_CAFE = datetime.timedelta(minutes=30)
MAX_HISTORIQUE = 20

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

# Palette de couleurs
PALETTE_COULEURS_JOURS = {
    1: "#fce5cd",   2: "#fff2cc",   3: "#d9ead3",   4: "#cfe2f3",   5: "#ead1dc",
    6: "#f4cccc",   7: "#fff2cc",   8: "#d0e0e3",   9: "#f9cb9c",  10: "#d9d2e9",
    11: "#c9daf8",  12: "#d0e0e3",  13: "#f6b26b",  14: "#ffe599",  15: "#b6d7a8",
    16: "#a2c4c9",  17: "#b4a7d6",  18: "#a4c2f4",  19: "#d5a6bd",  20: "#e6b8af",
    21: "#fce5cd",  22: "#fff2cc",  23: "#d9ead3",  24: "#cfe2f3",  25: "#ead1dc",
    26: "#f4cccc",  27: "#d9d2e9",  28: "#b6d7a8",  29: "#d5a6bd",  30: "#f6b26b",
    31: "#d0e0e3"
}


######################
# User Sheet Manager #
######################

def get_user_id():
    params = st.query_params
    user_id_from_url = params.get("user_id", [None])[0]

    if user_id_from_url:
        st.session_state["user_id"] = user_id_from_url

    if "user_id" not in st.session_state:
        afficher_titre("Bienvenue sur le planificateur Avignon Off 👋")
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
    
def get_gsheet_client():
    try:
        creds_dict = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds)
    except Exception as e:
            st.error(f"Erreur de connexion à Google Sheets : {e}")
            return None
    
def get_or_create_user_gsheets(user_id, spreadsheet_id):
    gsheets = None
    client = get_gsheet_client()
    if client is not None:    
        try:
            sh = client.open_by_key(spreadsheet_id)
        except Exception as e:
            st.error(f"Impossible d'ouvrir la Google Sheet : {e}")
            st.stop()    

        sheet_names = [f"data_{user_id}", f"links_{user_id}", f"meta_{user_id}"] # Utilisation nominale en mode multiuser avec hébergement streamlit share
        # sheet_names = [f"data", f"links", f"meta"] # pour debugger en local 
        gsheets = {}

        for name in sheet_names:
            try:
                ws = sh.worksheet(name)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=name, rows=1000, cols=20)
            gsheets[name.split("_")[0]] = ws  # 'data', 'links', 'meta'

    return gsheets

####################
# API Google Sheet #
####################

# 📥 Charge les infos persistées depuis la Google Sheet
def load_from_gsheet():
    if "gsheets" not in st.session_state:
        try:
            user_id = get_user_id()
            gsheets = get_or_create_user_gsheets(user_id, spreadsheet_id="1ytYrefEPzdJGy5w36ZAjW_QQTlvfZ17AH69JkiHQzZY")
            st.session_state.gsheets = gsheets
            worksheet = gsheets["data"]
            df = get_as_dataframe(worksheet, evaluate_formulas=True)
            df.dropna(how="all")
            if len(df) > 0:

                worksheet = gsheets["links"]
                rows = worksheet.get_all_values()
                lnk = {}
                if len(rows) > 1:
                    data_rows = rows[1:]
                    lnk = {row[0]: row[1] for row in data_rows if len(row) >= 2}

                worksheet = gsheets["meta"]
                fn  = worksheet.acell("A1").value
                fp  = worksheet.acell("A2").value
                wb = download_excel_from_dropbox(fp)

                initialisation_environnement(df, wb, fn, lnk)
        except Exception as e:
            pass

# 📤 Sauvegarde le DataFrame dans la Google Sheet
def save_df_to_gsheet(df: pd.DataFrame):
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets
            worksheet = gsheets["data"]
            worksheet.clear()
            set_with_dataframe(worksheet, df)
        except Exception as e:
            pass

# 📤 Sauvegarde les hyperliens dans la Google Sheet
def save_lnk_to_gsheet(lnk):
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets
            worksheet = gsheets["links"]
            worksheet.clear()
            rows = [[k, v] for k, v in lnk.items()]
            worksheet.update(range_name="A1", values=[["Clé", "Valeur"]] + rows)
        except Exception as e:
            pass

# 📤 Sauvegarde l'ensemble des infos persistées dans la Google Sheet
def save_to_gsheet(df: pd.DataFrame, fichier_excel, lnk):
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets

            worksheet = gsheets["data"]
            worksheet.clear()
            set_with_dataframe(worksheet, df)

            worksheet = gsheets["links"]
            worksheet.clear()
            rows = [[k, v] for k, v in lnk.items()]
            worksheet.update(range_name="A1", values=[["Clé", "Valeur"]] + rows)

            worksheet = gsheets["meta"]
            worksheet.update_acell("A1", fichier_excel.name)
            fp = upload_excel_to_dropbox(fichier_excel.getvalue(), fichier_excel.name)
            worksheet.update_acell("A2", fp)
            return fp
        except Exception as e:
            pass
    else:
        return ""

# Sauvegarde une ligne dans la Google Sheet
def save_one_row_to_gsheet(df, index_df):
    
    def convert_cell_value(x):
        if pd.isna(x):
            return ""
        elif isinstance(x, (pd.Timedelta, datetime.timedelta)):
            # Convertir en durée lisible : "1:30:00" ou minutes
            return str(x)
        elif isinstance(x, (pd.Timestamp, datetime.datetime)):
            return x.strftime("%Y-%m-%d %H:%M:%S")
        elif hasattr(x, "item"):
            return x.item()  # Pour np.int64, np.float64, etc.
        else:
            return x
    
    if "gsheets" in st.session_state and st.session_state.gsheets is not None:
        try:
            gsheets = st.session_state.gsheets
            worksheet = gsheets["data"]
            valeurs = df.drop(columns=["Debut_dt", "Duree_dt"]).loc[index_df].map(convert_cell_value).tolist()
            ligne_sheet = index_df + 2  # +1 pour index, +1 pour en-tête
            worksheet.update(range_name=f"A{ligne_sheet}", values=[valeurs])
        except Exception as e:
            pass

####################
# API Google Drive #
####################

# Sauvegarde sur le google drive le fichier Excel de l'utilisateur (nécessite un drive partagé payant sur Google Space -> Non utilisé, remplacé par DropBox)
# Cette sauvegarde permet de garder une trace de la mise en page du fichier utilisateur
# from googleapiclient.http import MediaIoBaseUpload
# from googleapiclient.discovery import build

# Id du drive partagé utilisé pour enregistrer une copie du fichier Excel utilisateur (nécessite un drive partagé payant sur Google Space -> Non utilisé, remplacé par DropBox)
# Cette sauvegarde permet de garder une trace de la mise en page du fichier utilisateur
# SHARED_DRIVE_ID = "xxxx" 

# def upload_excel_to_drive(file_bytes, filename, mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
#     try:
#         creds = get_gcp_credentials()
#         drive_service = build("drive", "v3", credentials=creds)

#         file_metadata = {"name": filename, "parents": [SHARED_DRIVE_ID]}
#         media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype=mime_type, resumable=True)
#         uploaded = drive_service.files().create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
#         return uploaded["id"]
#     except Exception as e:
#         return None

# from googleapiclient.http import MediaIoBaseDownload

# Renvoie le fichier Excel de l'utilisateur sauvegardé sur le google drive (nécessite un drive partagé payant sur Google Space -> Non utilisé, remplacé par DropBox)
# Cette sauvegarde permet de garder une trace de la mise en page du fichier utilisateur
# def download_excel_from_drive(file_id):
#     try:
#         creds = get_gcp_credentials()
#         service = build('drive', 'v3', credentials=creds)
#         request = service.files().get_media(fileId=file_id)
#         fh = BytesIO()
#         downloader = MediaIoBaseDownload(fh, request)
#         done = False
#         while not done:
#             _, done = downloader.next_chunk()
#         fh.seek(0)
#         return load_workbook(BytesIO(fh.read()))
#     except Exception as e:
#         return Workbook()

###############
# API DropBox #
###############

import dropbox
from io import BytesIO

# Retourne les credentials pour les API Dropbox
def get_dropbox_client():
    access_token = st.secrets["dropbox"]["access_token"]
    return dropbox.Dropbox(access_token)

# Sauvegarde sur Dropbox le fichier Excel de l'utilisateur 
# Cette sauvegarde permet de garder une trace de la mise en page du fichier utilisateur
def upload_excel_to_dropbox(file_bytes, filename, dropbox_path="/uploads/"):
    dbx = get_dropbox_client()
    full_path = f"{dropbox_path}{filename}"

    try:
        dbx.files_upload(file_bytes, full_path, mode=dropbox.files.WriteMode("overwrite"))
        # st.success(f"✅ Fichier '{filename}' uploadé dans Dropbox à {full_path}")
        return full_path
    except Exception as e:
        # st.error(f"❌ Erreur d’upload : {e}")
        return ""

# Renvoie le fichier Excel de l'utilisateur sauvegardé sur DropBox
# Cette sauvegarde permet de garder une trace de la mise en page du fichier utilisateur
def download_excel_from_dropbox(file_path):
    dbx = get_dropbox_client()
    try:
        metadata, res = dbx.files_download(file_path)
        file_bytes = BytesIO(res.content)
        return load_workbook(file_bytes)
    except Exception as e:
        # st.error(f"❌ Erreur lors du téléchargement depuis Dropbox : {e}")
        return Workbook()

#######################
# Gestion Undo / Redo #
#######################

# Initialise les listes d'undo redo
def undo_redo_init(verify=True):
    if "historique_undo" not in st.session_state or "historique_redo" not in st.session_state or not verify:
        st.session_state.historique_undo = deque(maxlen=MAX_HISTORIQUE)
        st.session_state.historique_redo = deque(maxlen=MAX_HISTORIQUE)

# Sauvegarde du contexte courant
def undo_redo_save():
    snapshot = {
        "df": st.session_state.df.copy(deep=True),
        "liens": st.session_state.liens_activites.copy(),
        "activites_planifiees_selected_row": st.session_state.activites_planifiees_selected_row,
        "activites_non_planifiees_selected_row": st.session_state.activites_non_planifiees_selected_row
    }
    st.session_state.historique_undo.append(snapshot)
    st.session_state.historique_redo.clear()

# Undo
def undo_redo_undo():
    if st.session_state.historique_undo:
        current = {
            "df": st.session_state.df.copy(deep=True),
            "liens": st.session_state.liens_activites.copy(),
            "activites_planifiees_selected_row": st.session_state.activites_planifiees_selected_row,
            "activites_non_planifiees_selected_row": st.session_state.activites_non_planifiees_selected_row
        }
        st.session_state.historique_redo.append(current)
        
        snapshot = st.session_state.historique_undo.pop()
        st.session_state.df = snapshot["df"]
        st.session_state.liens_activites = snapshot["liens"]
        st.session_state.activites_planifiees_selected_row = snapshot["activites_planifiees_selected_row"]
        st.session_state.activites_non_planifiees_selected_row = snapshot["activites_non_planifiees_selected_row"]
        forcer_reaffichage_activites_planifiees()
        forcer_reaffichage_activites_non_planifiees()
        forcer_reaffichage_df("creneaux_disponibles")
        save_df_to_gsheet(st.session_state.df)
        st.rerun()

# Redo
def undo_redo_redo():
    if st.session_state.historique_redo:
        current = {
            "df": st.session_state.df.copy(deep=True),
            "liens": st.session_state.liens_activites.copy(),
            "activites_planifiees_selected_row": st.session_state.activites_planifiees_selected_row,
            "activites_non_planifiees_selected_row": st.session_state.activites_non_planifiees_selected_row
        }
        st.session_state.historique_undo.append(current)
        
        snapshot = st.session_state.historique_redo.pop()
        st.session_state.df = snapshot["df"]
        st.session_state.liens_activites = snapshot["liens"]
        st.session_state.activites_planifiees_selected_row = snapshot["activites_planifiees_selected_row"]
        st.session_state.activites_non_planifiees_selected_row = snapshot["activites_non_planifiees_selected_row"]
        forcer_reaffichage_activites_planifiees()
        forcer_reaffichage_activites_non_planifiees()
        forcer_reaffichage_df("creneaux_disponibles")
        save_df_to_gsheet(st.session_state.df)
        st.rerun()

#########################
# Fonctions utilitaires #
#########################

# Renvoie val sous la forme "10h00" si datetime ou time, "" si None, str(val).strip() sinon
def heure_str(val):
    from datetime import datetime, time
    if isinstance(val, (datetime, time)):
        return val.strftime("%Hh%M")
    if pd.isna(val):
        return ""
    return str(val).strip()

# Renvoie un datetime basé sur BASE_DATE si h est datetime, time, str de la forme 10h00, 10:00 ou 10:00:00, None dans les autres cas
def parse_heure(h):
    from datetime import datetime, time

    if pd.isna(h) or str(h).strip() == "":
        return datetime.combine(BASE_DATE, time(0, 0))  # Heure nulle par défaut        if isinstance(h, time):
    
    if isinstance(h, datetime):
        return datetime.combine(BASE_DATE, h.time())
    
    h_str = str(h).strip()

    # Format 10h00
    if re.match(r"^\d{1,2}h\d{2}$", h_str):
        try:
            return datetime.strptime(f"{BASE_DATE.isoformat()} {h_str}", "%Y-%m-%d %Hh%M")
        except ValueError:
            return None

    # Format 10:00 ou 10:00:00
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", h_str):
        try:
            t = datetime.strptime(h_str, "%H:%M").time()
            return datetime.combine(BASE_DATE, t)
        except ValueError:
            try:
                t = datetime.strptime(h_str, "%H:%M:%S").time()
                return datetime.combine(BASE_DATE, t)
            except ValueError:
                return None

    return None

# Renvoie val sous la forme "1h00" si timedelta, "" si None, str(val).strip() sinon
def duree_str(val):
    from datetime import datetime, time
    if isinstance(val, pd.Timedelta):
        total_minutes = int(val.total_seconds() // 60)
        h = total_minutes // 60
        m = total_minutes % 60
        return f"{h}h{m:02d}"
    if pd.isna(val):
        return ""
    return str(val).strip()

# Renvoie un timedelta si h est timedelta, datetime, time, str de la forme 1h00, 1:00 ou 1:00:00, None dans les autres cas
def parse_duree(d):
    from datetime import datetime, time

    if pd.isna(d) or str(d).strip() == "":
        return pd.Timedelta(0)

    # Si c'est déjà un timedelta
    if isinstance(d, pd.Timedelta):
        return d

    # Si c'est un datetime.time
    if isinstance(d, time):
        return pd.Timedelta(hours=d.hour, minutes=d.minute, seconds=d.second)

    # Si c'est un datetime.datetime
    if isinstance(d, datetime):
        t = d.time()
        return pd.Timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)

    d_str = str(d).strip().lower()

    # Format "1h30"
    if re.match(r"^\d{1,2}h\d{2}$", d_str):
        h, m = map(int, d_str.replace("h", " ").split())
        return pd.Timedelta(hours=h, minutes=m)

    # Format "1:30" ou "1:30:00"
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", d_str):
        try:
            parts = list(map(int, d_str.split(":")))
            if len(parts) == 2:
                h, m = parts
                return pd.Timedelta(hours=h, minutes=m)
            elif len(parts) == 3:
                h, m, s = parts
                return pd.Timedelta(hours=h, minutes=m, seconds=s)
        except ValueError:
            return None

    return None

# Calcule l'heure de fin à partir de l'heure de début et de la durée    
def calculer_fin(h, d, fin_actuelle=""):
    if isinstance(d, pd.Timedelta) and not pd.isna(h):
        total = h + d
        return f"{total.hour:02d}h{total.minute:02d}"
    else:
        return fin_actuelle if pd.notna(fin_actuelle) else ""

# Calcule l'heure de fin à partir de l'heure de début et de la durée    
def calculer_fin_row(row):
    h = row.get("Debut_dt")
    d = row.get("Duree_dt")
    fin_actuelle = row.get("Fin")
    return calculer_fin(h, d, fin_actuelle)

# Formatte un objet timedelta en une chaîne de caractères "XhYY"
def formatter_timedelta(d):
    if isinstance(d, datetime.timedelta):
        total_seconds = int(d.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h{minutes:02d}"
    return d
    
# Formatte le contenu d'une cellule entiere
def formatter_cellule_int(d):
    if isinstance(d, int) or isinstance(d, float):
        if isinstance(d, float) and math.isnan(d):
            return d
        return int(d)
    return d
    
# Indique si une valeur à un format heure semblable à 10h00
def est_format_heure(val):
    return re.fullmatch(r"\d{1,2}h\d{2}", val.strip()) if val else False

# Indique si une valeur à un format durée semblable à 1h00
def est_format_duree(val):
    return re.fullmatch(r"\d{1,2}h[0-5]\d", val.strip()) is not None if val else False

# Renvoie une bitmap encodée en format Base64 à partir d'un fichier
import base64
def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def completer_ligne(ligne_partielle):
    colonnes_df_utiles = [col for col in st.session_state.df if col not in ["Debut_dt", "Duree_dt"]]
    colonnes_supplementaires = [col for col in ligne_partielle.keys() if col not in colonnes_df_utiles]
    colonnes_finales = colonnes_df_utiles + colonnes_supplementaires
    return {col: ligne_partielle.get(col, None) for col in colonnes_finales}

# Selectbox avec items non editables (contrairement à st.selectbox())
def selectbox_aggrid(label, options, key="aggrid_selectbox", height=100):
    df = pd.DataFrame({"Choix": [options[0]]})
    
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_column(
        "Choix",
        editable=True,
        cellEditor="agSelectCellEditor",
        cellEditorParams={"values": options},
        singleClickEdit=True,
        minWidth=120  # 🔧 largeur minimale lisible
    )
    gb.configure_grid_options(domLayout='autoHeight')
    gb.configure_grid_options(onGridReady="""
        function(params) {
            setTimeout(function() {
                params.api.sizeColumnsToFit();
            }, 100);
        }
    """)
    gridOptions = gb.build()

    st.markdown(f"{label}")
    response = AgGrid(
        df,
        gridOptions=gridOptions,
        height=height,
        key=key,
        update_mode=GridUpdateMode.MODEL_CHANGED,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True
    )

    try:
        return response["data"]["Choix"].iloc[0]  # ✅ corrige le warning
    except:
        return None  # En cas de suppression accidentelle

# Force le reaffichage d'un dataframe
def forcer_reaffichage_df(key):
    session_state_reset_counter = key + "_reset_counter"
    if session_state_reset_counter in st.session_state:
        st.session_state[session_state_reset_counter] += 1 
    session_state_forcer_reaffichage = key + "_forcer_reaffichage"
    if session_state_forcer_reaffichage in st.session_state:
        st.session_state[session_state_forcer_reaffichage] = True

# Affichage d'un dataframe
def afficher_df(label, df, hide=[], key="affichage_df", colorisation=False):

    nb_lignes = len(df)
    ligne_px = 30  # hauteur approximative d’une ligne dans AgGrid
    max_height = 150
    height = min(nb_lignes * ligne_px + 50, max_height)

    # Initialisation du compteur qui permet de savoir si l'on doit forcer le réaffichage de l'aggrid après une suppression de ligne 
    session_state_reset_counter = key + "_reset_counter"
    if session_state_reset_counter not in st.session_state:
        st.session_state[session_state_reset_counter] = 0
    
    # Initialisation du flag permettant de savoir si l'on est en mode réaffichage complet de l'aggrid
    session_state_forcer_reaffichage = key + "_forcer_reaffichage"
    if session_state_forcer_reaffichage not in st.session_state:
        st.session_state[session_state_forcer_reaffichage] = False
   
    
    gb = GridOptionsBuilder.from_dataframe(df)

    #Colonnes cachées
    for col in hide:
        if col in df.columns:
            gb.configure_column(col, hide=True)

    # Colorisation
    if colorisation:
        if "Date" in df.columns:
            df["__jour"] = df["Date"].apply(lambda x: int(str(int(float(x)))[-2:]) if pd.notna(x) else None)
            gb.configure_column("__jour", hide=True)
            gb.configure_grid_options(getRowStyle=JsCode(f"""
            function(params) {{
                const jour = params.data.__jour;
                const couleurs = {PALETTE_COULEURS_JOURS};
                if (jour && couleurs[jour]) {{
                    return {{ 'backgroundColor': couleurs[jour] }};
                }}
                return null;
            }}
            """))

    # Configuration de la sélection
    pre_selected_row = 0  # par défaut
    session_state_selected_row = key + "_selected_row"
    if session_state_selected_row in st.session_state and st.session_state[session_state_selected_row] is not None:
        selected_row_courante = st.session_state[session_state_selected_row]
        position = trouver_position_ligne(df, selected_row_courante.to_dict())
        pre_selected_row = position if position is not None else pre_selected_row
    gb.configure_selection(selection_mode="single", use_checkbox=False, pre_selected_rows=[pre_selected_row])

    # Retaillage auto des largeurs de colonnes
    gb.configure_grid_options(onGridReady=JsCode(f"""
        function(params) {{
            params.api.sizeColumnsToFit();
            params.api.ensureIndexVisible({pre_selected_row}, 'middle');
            params.api.getDisplayedRowAtIndex({pre_selected_row}).setSelected(true);
        }}
    """))

    grid_options = gb.build()
    grid_options["suppressMovableColumns"] = True

    st.markdown(f"{label}")
    response = AgGrid(
        df,
        gridOptions=grid_options,
        height=height,
        key=f"{key} {st.session_state[session_state_reset_counter]}",
        update_mode=GridUpdateMode.MODEL_CHANGED | GridUpdateMode.SELECTION_CHANGED,
        allow_unsafe_jscode=True,
        # fit_columns_on_grid_load=False
    )

    selected_rows = response["selected_rows"]
    if st.session_state[session_state_forcer_reaffichage] == True:
        row = df.iloc[pre_selected_row]
    else:
        if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty:
            row = selected_rows.iloc[0] 
        elif isinstance(selected_rows, list) and len(selected_rows) > 0:
            row = selected_rows[0]
        else: 
            row = df.iloc[pre_selected_row]
    st.session_state[session_state_forcer_reaffichage] = False

    st.session_state[session_state_selected_row] = row

    return row

# Renvoie le numero de ligne d'un df qui matche des valeurs
def trouver_position_ligne(df, valeurs):
    for i, row in df.iterrows():
        match = True
        for col, val in valeurs.items():
            if col in row and not pd.isna(row[col]):
                if row[col] != val:
                    match = False
                    break
        if match:
            return df.index.get_loc(i)
    return None

# Renvoie l'index de la ligne la plus proche dans un df_display d'aggrid
# Le df_display est supposé contenir dans la colonne __index l'index du df de base
def ligne_voisine_index(df_display, index_df):
    df_display_reset = df_display.reset_index(drop=True)
    selected_row_pos = df_display_reset["__index"].eq(index_df).idxmax()
    new_selected_row_pos = selected_row_pos + 1 if  selected_row_pos + 1 <= len(df_display) - 1 else max(selected_row_pos - 1, 0)
    return df_display_reset.iloc[new_selected_row_pos]["__index"]

# Selectbox avec items non editables (contrairement à st.selectbox())
def aggrid_single_selection_list(label, choices, key="aggrid_select", hauteur=200):
    # Garde-fou : le label doit être une chaîne
    if not isinstance(label, str):
        raise ValueError(f"Le paramètre `label` doit être une chaîne, reçu : {type(label)}")

    # Transformation si liste de listes
    if choices and isinstance(choices[0], (list, tuple)):
        choices = [" | ".join(map(str, ligne)) for ligne in choices]

    df = pd.DataFrame({label: choices})

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_selection(selection_mode="single", use_checkbox=False)
    gb.configure_grid_options(
        domLayout='normal',
        headerHeight=0,
        suppressRowHoverHighlight=True,
        suppressCellFocus=True,
    )
    gb.configure_column(label, header_name="", wrapText=True, autoHeight=True, minWidth=200, flex=1)

    st.markdown("""
        <style>
        .ag-root-wrapper,
        .ag-theme-streamlit,
        .ag-header,
        .ag-cell:focus,
        .ag-cell,
        .ag-row-hover {
            border: none !important;
            outline: none !important;
            box-shadow: none !important;
            background-color: transparent !important;
        }
        .ag-header { display: none !important; }
        </style>
    """, unsafe_allow_html=True)

    response = AgGrid(
        df,
        gridOptions=gb.build(),
        key=key,
        height=hauteur,
        fit_columns_on_grid_load=False,
        allow_unsafe_jscode=True,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        enable_enterprise_modules=False
    )

    selected = response.get("selected_rows")
    if selected:
        valeur = selected[0][label]
        index_selection = selected[0].get("_selectedRowNodeInfo", {}).get("nodeRowIndex", None)
        return valeur
    else:
        return choices[0]

##########################
# Fonctions applicatives #
##########################

# Renvoie un descripteur d'activité à partir d'une date et d'une ligne du df
def get_descripteur_activite(date, row):
    titre = f"{date} - [{row['Debut'].strip()} - {row['Fin'].strip()}] - {row['Activite']}"
    if not (pd.isna(row["Lieu"]) or str(row["Lieu"]).strip() == ""):
        titre = titre + f"( {row['Lieu']}) - P{formatter_cellule_int(row['Priorite'])}"
    return titre

# Affiche le titre de la page de l'application
def afficher_titre(title):
    # Réduire l’espace en haut de la page
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 2rem;
            }
        </style>
        """, unsafe_allow_html=True
    )

    # Titre de la page
    st.markdown(f"## {title}")

# Affiche l'aide de l'application
def afficher_aide():
    
    with st.expander("ℹ️ À propos"):
        st.markdown("""
        <div style='font-size: 14px;'>
        <p style="margin-bottom: 0.2em">Cette application offre les fonctionnalités suivantes:</p>
        <ul style="margin-top: 0em; margin-bottom: 2em">
        <li>Choix de la période à planifier</li>
        <li>Chargement d'un fichier Excel contenant les spectacles à planifier</li>
        <li>Affichage des activités planifiées (i.e. celles dont le champ Date est renseigné)</li>
        <li>Affichage des activités non planifiées (i.e. celles dont le champ Date n'est pas renseigné)</li>
        <li>Gestion de la planification des activités en respectant les règles ci-dessous</li>
        <li>Affectation d'une activité à un créneau disponible</li>
        <li>Prise en compte optionnelle des pauses (déjeuner, dîner, café)</li>
        <li>Recherche d'un spectacle dans le programme du Off par click sur une activité</li>
        <li>Sauvegarde du ficher Excel modifié</li>
        </ul>
        
        <p style="margin-bottom: 0.2em">Conditions adoptées pour la planification des activités:</p>
        <ul style="margin-top: 0em; margin-bottom: 2em">
        <li>30 minutes de marge entre activités</li>
        <li>1 heure par pause repas</li>
        <li>1/2 heure par pause café sans marge avec l'activité précédente ou suivante</li>
        <li>Respect des relâches pour les spectacles</li>
        </ul>
      
        <p style="margin-bottom: 0.2em">Le fichier Excel d'entrée doit contenir les colonnes suivantes:</p>
        <ul style="margin-top: 0em; margin-bottom: 2em">
        <li>Date : Date de l'activité (entier)</li>
        <li>Début : Heure de début de l'activité (format HHhMM)</li>
        <li>Fin : Heure de fin de l'activité (format HHhMM)</li>
        <li>Durée : Durée de l'activité (format HHhMM ou HHh)</li>
        <li>Activité : Nom de l'activité (nom de spectacle, pause, visite, ...)</li>
        <li>Lieu : Lieu de l'activité</li>
        <li>Relâche : Jours de relâche pour l'activité (liste d'entiers, peut être vide)</li>
        <li>Réservé : Indique si l'activité est réservée (Oui/Non, vide interpété comme Non)</li>
        </ul>

        <p style="margin-bottom: 0.2em">📥Un modèle Excel est disponible <a href="https://github.com/jnicoloso-91/PlanifAvignon-05/raw/main/Mod%C3%A8le%20Excel.xlsx" download>
        ici
        </a></p>
        <p>ℹ️ Si le téléchargement ne démarre pas, faites un clic droit → "Enregistrer le lien sous...".</p>

        </div>
        """, unsafe_allow_html=True)  

# 1️⃣ Tentative de récupération des dates du festival depuis le site officiel (recherche simple)
def fetch_off_festival_dates():
    url = "https://www.festivaloffavignon.com/"
    r = requests.get(url, timeout=5)
    soup = BeautifulSoup(r.text, "html.parser")
    # Recherche dans le texte "du 5 au 26 juillet 2025"
    text = soup.get_text()
    match = re.search(r"du\s+(\d{1,2})\s+juillet\s+au\s+(\d{1,2})\s+juillet\s+2025", text, re.IGNORECASE)
    if match:
        d1, d2 = map(int, match.groups())
        base_year = 2025
        base_month = 7
        return datetime.date(base_year, base_month, d1), datetime.date(base_year, base_month, d2)
    return None, None

# Choix de la période à planifier
def choix_periode_a_planifier(df):

    if "nouveau_fichier" not in st.session_state:
        st.session_state.nouveau_fichier = True
    
    # Initialisation de la periode si nouveau fichier
    if st.session_state.nouveau_fichier == True:
        # Reset du flag déclenché par callback upload
        st.session_state.nouveau_fichier = False

        # Initialisation des variables de début et de fin de période à planifier
        periode_a_planifier_debut = None
        periode_a_planifier_fin = None

        # Garde uniquement les valeurs non nulles et convertibles de la colonne Date du df
        dates_valides = df["Date"].dropna().apply(lambda x: int(float(x)) if str(x).strip() != "" else None)
        dates_valides = dates_valides.dropna().astype(int)

        if not dates_valides.empty:
            # Conversion en datetime
            base_date = datetime.date(datetime.date.today().year, 7, 1)
            dates_datetime = dates_valides.apply(lambda j: datetime.datetime.combine(base_date, datetime.datetime.min.time()) + datetime.timedelta(days=j - 1))

            if not dates_datetime.empty:
                periode_a_planifier_debut = dates_datetime.min()
                periode_a_planifier_fin = dates_datetime.max()

        if periode_a_planifier_debut is None or periode_a_planifier_fin is None:
            if "festival_debut" not in st.session_state or "festival_fin" not in st.session_state:
                debut, fin = fetch_off_festival_dates()
                if debut and fin:
                    st.session_state.festival_debut = debut
                    st.session_state.festival_fin = fin
                else:
                    # Valeurs de secours (manuelles)
                    st.session_state.festival_debut = datetime.date(2025, 7, 5)
                    st.session_state.festival_fin = datetime.date(2025, 7, 26)
            periode_a_planifier_debut = st.session_state.festival_debut
            periode_a_planifier_fin = st.session_state.festival_fin
        
        st.session_state.periode_a_planifier_debut = periode_a_planifier_debut
        st.session_state.periode_a_planifier_fin = periode_a_planifier_fin

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.periode_a_planifier_debut = st.date_input("Début de la période à planifier", value=st.session_state.periode_a_planifier_debut, format="DD/MM/YYYY")
    with col2:
        st.session_state.periode_a_planifier_fin = st.date_input("Fin de la période à planifier", value=st.session_state.periode_a_planifier_fin, format="DD/MM/YYYY")

# Met à jour les données calculées
def recalculer_donnees(df):
    df["Debut_dt"] = df["Debut"].apply(parse_heure)
    df["Duree_dt"] = df["Duree"].apply(parse_duree)
    df["Fin"] = df.apply(calculer_fin_row, axis=1)            

# Nettoie les données du tableau Excel importé
def nettoyer_donnees(df):
    try:
        # Nettoyage noms de colonnes : suppression espaces et accents
        df.columns = df.columns.str.strip().str.replace("\u202f", " ").str.normalize("NFKD").str.encode("ascii", errors="ignore").str.decode("utf-8")

        colonnes_attendues = ["Date", "Debut", "Fin", "Duree", "Activite", "Lieu", "Relache", "Reserve", "Priorite", "Commentaire"]
        colonnes_attendues_avec_accents = ["Date", "Début", "Fin", "Durée", "Activité", "Lieu", "Relâche", "Réservé", "Priorité", "Commentaire"]

        if not all(col in df.columns for col in colonnes_attendues):
            st.error("Le fichier n'est pas au format Excel ou ne contient pas toutes les colonnes attendues: " + ", ".join(colonnes_attendues_avec_accents) + ".")
        elif (len(df) == 0):
            st.error("Le fichier est vide")
        else:

            # Suppression des lignes presque vides i.e. ne contenant que des NaN ou des ""
            df = df[~df.apply(lambda row: all(pd.isna(x) or str(x).strip() == "" for x in row), axis=1)].reset_index(drop=True)

            # Nettoyage Heure 
            df["Debut"] = df["Debut"].apply(heure_str)

            # Nettoyage Duree 
            df["Duree"] = df["Duree"].apply(duree_str)

            # Force les types corrects après lecture pour éviter les erreurs de conversion pandas
            colonnes_cibles = {
                "Debut": "string",
                "Fin": "string",
                "Duree": "string",
                "Activite": "string",
                "Lieu": "string"
            }
            for col, dtype in colonnes_cibles.items():
                df[col] = df[col].astype(dtype) 

            # Convertit explicitement certaines colonnes pour éviter les erreurs de conversion pandas
            df["Relache"] = df["Relache"].astype("object").fillna("").astype(str)
            df["Priorite"] = pd.to_numeric(df["Priorite"], errors="coerce").astype("Int64")
            # df["Priorite"] = df["Priorite"].astype("object").fillna("").astype(str)
            # pd.set_option('future.no_silent_downcasting', True)

            del st.session_state["fichier_invalide"]
            
    except Exception as e:
        st.error(f"Erreur lors du décodage du fichier : {e}")

# Renvoie les hyperliens de la colonne Activité 
def get_liens_activites(wb):
    liens_activites = {}
    try:
        ws = wb.worksheets[0]
        for cell in ws[1]:
            if cell.value and str(cell.value).strip().lower() in ["activité"]:
                col_excel_index = cell.column
        for row in ws.iter_rows(min_row=2, min_col=col_excel_index, max_col=col_excel_index):
            cell = row[0]
            if cell.hyperlink:
                liens_activites[cell.value] = cell.hyperlink.target
            else:
                # Construire l'URL de recherche par défaut
                if cell.value is not None:
                    url = f"https://www.festivaloffavignon.com/resultats-recherche?recherche={cell.value.replace(' ', '+')}"
                    liens_activites[cell.value] = url  # L'enregistrer dans la session
        return liens_activites
    except:
        return liens_activites

# Vérifie la cohérence des informations du dataframe et affiche le résultat dans un expander
def verifier_coherence(df):
    erreurs = []

    def est_entier(x):
        try:
            return not pd.isna(x) and str(x).strip() != "" and int(float(x)) == float(x)
        except Exception:
            return False
        
    # 1. 🔁 Doublons
    df_valid = df[df["Activite"].notna() & (df["Activite"].astype(str).str.strip() != "")]

    # Création d'une colonne temporaire pour la comparaison
    df_valid = df[df["Activite"].notna() & (df["Activite"].astype(str).str.strip() != "")]
    df_valid = df_valid.copy()  # pour éviter SettingWithCopyWarning
    df_valid["_spectacle_clean"] = df_valid["Activite"].astype(str).str.strip().str.lower()
    doublons = df_valid[df_valid.duplicated(subset=["_spectacle_clean"], keep=False)]

    if not doublons.empty:
        bloc = ["🟠 Doublons de spectacle :"]
        for _, row in doublons.iterrows():
            try:
                date_str = str(int(float(row["Date"]))) if pd.notna(row["Date"]) else "Vide"
            except (ValueError, TypeError):
                date_str = "Vide"
            heure_str = str(row["Debut"]).strip() if pd.notna(row["Debut"]) else "Vide"
            duree_str = str(row["Duree"]).strip() if pd.notna(row["Duree"]) else "Vide"
            bloc.append(f"{date_str} - {heure_str} - {row['Activite']} ({duree_str})")
        erreurs.append("\n".join(bloc))
        
    # 2. ⛔ Chevauchements
    chevauchements = []
    df_sorted = df.sort_values(by=["Date", "Debut_dt"])
    for i in range(1, len(df_sorted)):
        r1 = df_sorted.iloc[i - 1]
        r2 = df_sorted.iloc[i]
        if r1.isna().all() or r2.isna().all():
            continue
        if r1["Date"] == r2["Date"]:
            fin1 = r1["Debut_dt"] + r1["Duree_dt"]
            debut2 = r2["Debut_dt"]
            if debut2 < fin1:
                chevauchements.append((r1, r2))
    if chevauchements:
        bloc = ["🔴 Chevauchements:"]
        for r1, r2 in chevauchements:
            bloc.append(
                f"{r1['Activite']} ({r1['Debut']} / {r1['Duree']}) chevauche {r2['Activite']} ({r2['Debut']} / {r2['Duree']}) le {r1['Date']}"
            )
        erreurs.append("\n".join(bloc))

    # 3. 🕒 Erreurs de format
    bloc_format = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Debut", "Duree"]):
            continue
        if row.isna().all():
            continue

        # Date : uniquement si non NaN
        if pd.notna(row["Date"]) and not est_entier(row["Date"]):
            bloc_format.append(f"Date invalide à la ligne {idx + 2} : {row['Date']}")

        # Ne tester Heure/Duree que si Spectacle ou Autres est renseigné
        if str(row["Activite"]).strip() != "":
            if not re.match(r"^\d{1,2}h\d{2}$", str(row["Debut"]).strip()):
                bloc_format.append(f"Heure invalide à la ligne {idx + 2} : {row['Debut']}")
            if not re.match(r"^\d{1,2}h\d{2}$", str(row["Duree"]).strip()):
                bloc_format.append(f"Durée invalide à la ligne {idx + 2} : {row['Duree']}")
        
        # Test de la colonne Relache
        if not est_relache_valide(row["Relache"]):
            bloc_format.append(f"Relache invalide à la ligne {idx + 2} : {row['Relache']}")

    # 4. 📆 Spectacles un jour de relâche (Date == Relache)
    bloc_relache = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Debut", "Duree"]):
            continue
        if row.isna().all():
            continue

        if (
            est_entier(row["Date"]) and
            est_entier(row["Relache"]) and
            int(float(row["Date"])) == int(float(row["Relache"])) and
            str(row["Activite"]).strip() != ""
        ):
            bloc_relache.append(
                f"{row['Activite']} prévu le jour de relâche ({int(row['Date'])}) à la ligne {idx + 2}"
            )
    if bloc_relache:
        erreurs.append("🛑 Spectacles programmés un jour de relâche:\n" + "\n".join(bloc_relache))

    # 5. 🕳️ Heures non renseignées
    bloc_heure_vide = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Debut", "Duree"]):
            continue
        if row.isna().all():
            continue

        if str(row["Activite"]).strip() != "":
            if pd.isna(row["Debut"]) or str(row["Debut"]).strip() == "":
                bloc_heure_vide.append(f"Heure vide à la ligne {idx + 2}")
    if bloc_heure_vide:
        erreurs.append("⚠️ Heures non renseignées:\n" + "\n".join(bloc_heure_vide))

    # 6. 🕓 Heures au format invalide
    bloc_heure_invalide = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Debut", "Duree"]):
            continue
        if row.isna().all():
            continue

        if str(row["Activite"]).strip() != "":
            h = row["Debut"]
            if pd.notna(h) and str(h).strip() != "":
                h_str = str(h).strip().lower()
                is_time_like = isinstance(h, (datetime.datetime, datetime.time))
                valid_format = bool(re.match(r"^\d{1,2}h\d{2}$", h_str) or re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", h_str))
                if not is_time_like and not valid_format:
                    bloc_heure_invalide.append(f"Heure invalide à la ligne {idx + 2} : {h}")
    if bloc_heure_invalide:
        erreurs.append("⛔ Heures mal formatées:\n" + "\n".join(bloc_heure_invalide))

    # 7. 🕳️ Durées non renseignées ou nulles
    bloc_duree_nulle = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Debut", "Duree"]):
            continue
        if row.isna().all():
            continue

        if isinstance(row["Duree_dt"], pd.Timedelta) and row["Duree_dt"] == pd.Timedelta(0):
            if pd.isna(row["Duree"]) or str(row["Duree"]).strip() == "":
                msg = f"Durée vide à la ligne {idx + 2}"
            else:
                msg = f"Durée égale à 0 à la ligne {idx + 2} : {row['Duree']}"
            bloc_duree_nulle.append(msg)
    if bloc_duree_nulle:
        erreurs.append("⚠️ Durées nulles ou vides:\n" + "\n".join(bloc_duree_nulle))

    # 8. ⏱️ Durées au format invalide
    bloc_duree_invalide = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Debut", "Duree"]):
            continue
        if row.isna().all():
            continue

        if str(row["Activite"]).strip() != "":
            d = row["Duree"]
            if pd.notna(d) and str(d).strip() != "":
                d_str = str(d).strip().lower()
                is_timedelta = isinstance(d, pd.Timedelta)
                valid_format = bool(re.match(r"^\d{1,2}h\d{2}$", d_str) or re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", d_str))
                if not is_timedelta and not valid_format:
                    bloc_duree_invalide.append(f"Durée invalide à la ligne {idx + 2} : {d}")
    if bloc_duree_invalide:
        erreurs.append("⛔ Durées mal formatées:\n" + "\n".join(bloc_duree_invalide))

    contenu = "<div style='font-size: 14px;'>"
    for bloc in erreurs:
        lignes = bloc.split("\n")
        if lignes[0].startswith(("🟠", "🔴", "⚠️", "🛑", "⛔")):
            contenu += f"<p><strong>{lignes[0]}</strong></p><ul>"
            for ligne in lignes[1:]:
                contenu += f"<li>{ligne}</li>"
            contenu += "</ul>"
        else:
            contenu += f"<p>{bloc}</p>"
    contenu += "</div>"

    with st.expander("🔍 Vérification du fichier"):
        st.markdown(contenu, unsafe_allow_html=True)

# Renvoie le dataframe des activités planifiées
def get_activites_planifiees(df):
    return df[df["Date"].notna()].sort_values(by=["Date", "Debut_dt"])

# Renvoie le dataframe des activités non planifiées
def get_activites_non_planifiees(df):
    return df[df["Date"].isna() & df["Activite"].notna() & df["Debut"].notna() & df["Fin"].notna()]

# Affiche le bouton de recharche sur le net
def afficher_bouton_recherche_net(nom_activite):                   
    # Initialiser le dictionnaire si nécessaire
    if "liens_activites" not in st.session_state:
        st.session_state["liens_activites"] = {}

    liens = st.session_state["liens_activites"]

    # Vérifier si un lien existe déjà
    if nom_activite in liens:
        url = liens[nom_activite]
    else:
        # Construire l'URL de recherche
        url = f"https://www.festivaloffavignon.com/resultats-recherche?recherche={nom_activite.replace(' ', '+')}"
        liens[nom_activite] = url  # L'enregistrer dans la session

    st.link_button("🔍", url)
    # st.markdown(f"[🔍 Rechercher sur le net]({url})", unsafe_allow_html=True)

# Indique si une activité donnée par son descripteur dans le df est réservée
def est_reserve(ligne_df):
    return str(ligne_df["Reserve"]).strip().lower() == "oui"

# Renvoie les lignes modifées entre df1 et df2, l'index de df2 est supposé se trouver dans la colonne __index de df1
def get_lignes_modifiees(df1, df2):
    lignes_modifiees = set()
    for i, row in df1.iterrows():
        idx = row["__index"]
        for col in df1.drop(columns=["__index"]).columns:
            if idx in df2.index:
                val_avant = df2.at[idx, col]
                val_apres = row[col]
                if pd.isna(val_avant) and pd.isna(val_apres):
                    continue
                if val_avant != val_apres:
                    if col == "Début":
                        if not est_format_heure(val_apres):
                            st.error("Format invalide (attendu : 10h00)")
                            df1.at[i, col] = val_avant
                    if col == "Durée":
                        if not est_format_duree(val_apres):
                            st.error("Format invalide (attendu : 10h00)")
                            df1.at[i, col] = val_avant
                    lignes_modifiees.add((i, idx))
    return lignes_modifiees

# Affiche les activités planifiées dans un tableau
def afficher_activites_planifiees(df):
    st.markdown("##### Activités planifiées")

    # Constitution du df à afficher
    planifies = get_activites_planifiees(df).sort_values(by=["Date", "Debut_dt"], ascending=[True, True])
    df_display = planifies.rename(columns=RENOMMAGE_COLONNES)
    df_display["__jour"] = df_display["Date"].apply(lambda x: int(str(int(float(x)))[-2:]) if pd.notna(x) else None)
    df_display["__index"] = df_display.index
    df_display.drop(columns=["Debut_dt", "Duree_dt"], inplace=True)


    # Initialisation du compteur qui permet de savoir si l'on doit forcer le réaffichage de l'aggrid après une suppression de ligne 
    if "aggrid_activites_planifiees_reset_counter" not in st.session_state:
        st.session_state.aggrid_activites_planifiees_reset_counter = 0

    # Initialisation du flag permettant de savoir si l'on est en mode réaffichage complet de l'aggrid
    if "aggrid_activites_planifiees_forcer_reaffichage" not in st.session_state:
        st.session_state.aggrid_activites_planifiees_forcer_reaffichage = False
   
    # Initialisation du flag permettant de savoir si l'on doit gérer les modifications de cellules
    if "aggrid_activites_planifiees_gerer_modification_cellule" not in st.session_state:
        st.session_state.aggrid_activites_planifiees_gerer_modification_cellule = True
   
    # Initialisation de la variable d'état contenant l'index de ligne sélectionnée courant
    if "aggrid_activites_planifiees_idx_row_courant" not in st.session_state:
        st.session_state.aggrid_activites_planifiees_idx_row_courant = None
   
    # # Initialisation de la variable d'état contenant l'index de la colonne sélectionnée de l'éditeur d'activités planifiées
    # if "editeur_activites_planifiees_index_colonne_courante" not in st.session_state:
    #     st.session_state.editeur_activites_planifiees_index_colonne_courante = 0

    # # Initialisation du flag permettant de savoir si l'on doit utiliser l'index de colonne sélectionnée pour paramétrer la selectbox concernée de l'éditeur d'activités planifiées
    # if "editeur_activites_planifiees_utiliser_index_colonne_courante" not in st.session_state:
    #     st.session_state.editeur_activites_planifiees_utiliser_index_colonne_courante = False

    # Enregistrement dans st.session_state d'une copy du df à afficher
    st.session_state.df_display_activites_planifiees = df_display.copy()

    # Configuration
    gb = GridOptionsBuilder.from_dataframe(df_display)

    # squage des colonnes de travail
    gb.configure_column("__index", hide=True)
    gb.configure_column("__jour", hide=True)

    # Colonnes editables
    editable_cols = {col: True for col in df_display.columns if col != "__index" and col != "__jour"}
    editable_cols["Date"] = False  
    editable_cols["Début"] = False  
    editable_cols["Fin"] = False  
    editable_cols["Durée"] = False  
    for col, editable in editable_cols.items():
        gb.configure_column(col, editable=editable)

    # Colorisation
    gb.configure_grid_options(getRowStyle=JsCode(f"""
    function(params) {{
        const jour = params.data.__jour;
        const couleurs = {PALETTE_COULEURS_JOURS};
        if (jour && couleurs[jour]) {{
            return {{ 'backgroundColor': couleurs[jour] }};
        }}
        return null;
    }}
    """))

    # Retaillage largeur colonnes
    gb.configure_default_column(resizable=True)

    # Configuration de la sélection
    pre_selected_row = 0  # par défaut
    if "activites_planifiees_selected_row" in st.session_state:
        valeur_index = st.session_state["activites_planifiees_selected_row"]
        matches = df_display[df_display["__index"].astype(str) == str(valeur_index)]
        if not matches.empty:
            pre_selected_row = df_display.index.get_loc(matches.index[0])
    gb.configure_selection(selection_mode="single", use_checkbox=False, pre_selected_rows=[pre_selected_row])
    
    js_code = JsCode(f"""
            function(params) {{
                params.api.sizeColumnsToFit();
                params.api.ensureIndexVisible({pre_selected_row}, 'middle');
                params.api.getDisplayedRowAtIndex({pre_selected_row}).setSelected(true);
            }}
        """)
    # js_code = JsCode(f"""
    #         function(params) {{
    #             params.api.ensureIndexVisible({pre_selected_row}, 'middle');
    #             params.api.getDisplayedRowAtIndex({pre_selected_row}).setSelected(true);

    #             // Auto-size all columns to fit content
    #             let allColumnIds = [];
    #             params.columnApi.getAllColumns().forEach(function(column) {{
    #                 allColumnIds.push(column.colId);
    #             }});
    #             params.columnApi.autoSizeColumns(allColumnIds);
    #         }}
    #     """)
    gb.configure_grid_options(onGridReady=js_code)

    grid_options = gb.build()
    grid_options["suppressMovableColumns"] = True

    # Affichage
    response = AgGrid(
        df_display,
        gridOptions=grid_options,
        allow_unsafe_jscode=True,
        height=250,
        update_mode=GridUpdateMode.MODEL_CHANGED | GridUpdateMode.SELECTION_CHANGED,
        key=f"Activités planifiées {st.session_state.aggrid_activites_planifiees_reset_counter}",  # clé stable mais changeante après suppression de ligne pour forcer le reaffichage
    )

    # Affectation de la ligne sélectionnée courante
    selected_rows = response["selected_rows"]
    if st.session_state.aggrid_activites_planifiees_forcer_reaffichage == True:
        row = df_display.iloc[pre_selected_row]
    else:
        if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty:
            row = selected_rows.iloc[0] 
        elif isinstance(selected_rows, list) and len(selected_rows) > 0:
            row = selected_rows[0]
        else: 
            row = df_display.iloc[pre_selected_row]
    st.session_state.aggrid_activites_planifiees_forcer_reaffichage = False

    # Gestion des modifications de cellules
    if st.session_state.aggrid_activites_planifiees_gerer_modification_cellule == True:
        df_modifie = pd.DataFrame(response["data"])
        lignes_modifiees = get_lignes_modifiees(df_modifie, st.session_state.df_display_activites_planifiees)
        if lignes_modifiees:
            undo_redo_save()
            for i, idx in lignes_modifiees:
                for col in df_modifie.drop(columns=["__index", "__jour"]).columns:
                    if col not in ["Date", "Début", "Fin", "Durée"]:
                        col_df = RENOMMAGE_COLONNES_INVERSE[col] if col in RENOMMAGE_COLONNES_INVERSE else col
                        if df.at[idx, col_df] != df_modifie.at[i, col]:
                            # st.session_state.editeur_activites_planifiees_utiliser_index_colonne_courante = True
                            affecter_valeur(df,idx, col_df, df_modifie.at[i, col], forcer_reaffichage=["Debut"])
    st.session_state.aggrid_activites_planifiees_gerer_modification_cellule = True

    # 🟡 Traitement du clic
    if row is not None:
        index_df = row["__index"]
        if index_df != st.session_state.aggrid_activites_planifiees_idx_row_courant:
            st.session_state.editeur_activite_courante_idx = index_df
        st.session_state.aggrid_activites_planifiees_idx_row_courant = index_df

        # Enregistrement de la sélection courante pour gestion de la sélection
        st.session_state.activites_planifiees_selected_row = index_df

        nom_activite = str(row["Activité"]).strip() 

        if nom_activite:

            with st.expander("Contrôles"):
                st.markdown(f"🎯 Activité sélectionnée : **{nom_activite}**")

                # col1, col2, _ = st.columns([0.5, 0.5, 4])
                # with col1:
                #     if not est_pause_str(nom_activite):
                #         afficher_bouton_recherche_net(nom_activite)
                # with col2:
                #     if not est_reserve(st.session_state.df.loc[index_df]):
                #         if st.button("🗑️", key="SupprimerActivitePlanifiee"):
                #             undo_redo_save()
                #             df_display_reset = df_display.reset_index(drop=True)
                #             selected_row_pos = df_display_reset["__index"].eq(index_df).idxmax()
                #             new_selected_row_pos = selected_row_pos + 1 if  selected_row_pos + 1 <= len(df_display) - 1 else max(selected_row_pos - 1, 0)
                #             st.session_state.activites_planifiees_selected_row = df_display_reset.iloc[new_selected_row_pos]["__index"]
                #             supprimer_activite_planifiee(df, index_df)
                #             forcer_reaffichage_activites_planifiees()
                #             save_one_row_to_gsheet(df, index_df)
                #             st.rerun()

                # Bouton Chercher, Supprimer, Ajouter au planning 
                col1, col2, col3 = st.columns([0.5,0.5,4])
                with col1:
                    if not est_pause_str(nom_activite):
                        afficher_bouton_recherche_net(nom_activite)
                with col2:
                    if not est_reserve(df.loc[index_df]):
                        if st.button("🗑️", key="SupprimerActivitePlanifiee"):
                            undo_redo_save()
                            st.session_state.activites_planifiees_selected_row = ligne_voisine_index(df_display, index_df)
                            supprimer_activite(df, index_df)
                            forcer_reaffichage_activites_planifiees()
                            save_one_row_to_gsheet(df, index_df)
                            st.rerun()
                with col3:
                    col11, col12 = st.columns([0.5,4])
                    with col12:
                        if not est_reserve(df.loc[index_df]):
                            # Déterminer les jours disponibles 
                            jour_escape = "Le ??" # escape pour déplanifier l'activité
                            jours_possibles = [jour_escape]
                            jours_possibles_autres = get_jours_possibles(df, get_activites_planifiees(df), index_df)
                            if jours_possibles:
                                jours_possibles += jours_possibles_autres
                                jours_label = [jours_possibles[0]] + [f"Le {int(jour):02d}" for jour in jours_possibles[1:]]
                                jour_selection = st.selectbox("Choix jour", jours_label, label_visibility = "collapsed", key = "ChoixJourReplanifActivitePlanifiee")
                    with col11:
                        # Bouton pour confirmer
                        if not est_reserve(st.session_state.df.loc[index_df]):
                            if jours_possibles:
                                if st.button("🗓️", key="ReplanifierActivitéPlanifiee"):
                                    if jour_selection == jour_escape:
                                        undo_redo_save()
                                        st.session_state.activites_planifiees_selected_row = ligne_voisine_index(df_display, index_df)
                                        st.session_state.activites_non_planifiees_selected_row = index_df
                                        supprimer_activite_planifiee(df, index_df)
                                        forcer_reaffichage_activites_planifiees()
                                        forcer_reaffichage_activites_non_planifiees()
                                        forcer_reaffichage_df("creneaux_disponibles")
                                        save_one_row_to_gsheet(df, index_df)
                                        st.rerun()
                                    else:
                                        jour_choisi = int(jour_selection.split()[-1])
                                        undo_redo_save()
                                        df.at[index_df, "Date"] = jour_choisi
                                        forcer_reaffichage_activites_planifiees()
                                        save_one_row_to_gsheet(df, index_df)
                                        st.rerun()

            # Formulaire d'édition de la ligne sélectionnée
            # with st.expander("Edition de la ligne sélectionnée"):
            #     colonnes_editables = [col for col in df_display.columns if col not in ["__jour", "__index", "Date", "Début", "Fin", "Durée"]]
                
            #     # Ajout de l'hyperlien aux informations éditables s'il existe
            #     if st.session_state.liens_activites is not None:
            #         liens_activites = st.session_state.liens_activites
            #         lien = liens_activites.get(row["Activité"])
            #         if lien:
            #             colonnes_editables.append("Lien de recherche")

            #     # colonne = st.radio("⚙️ Colonne à éditer", colonnes_editables, key="selectbox_editeur_activites_planifiees", label_visibility="collapsed")
            #     index_colonne_courante = st.session_state.editeur_activites_planifiees_index_colonne_courante
            #     if st.session_state.editeur_activites_planifiees_utiliser_index_colonne_courante == True:
            #         colonne = st.selectbox("🛠️ Colonne à éditer", colonnes_editables, index=index_colonne_courante, key=f"selectbox_editeur_activites_planifiees")
            #     else:
            #         colonne = st.selectbox("🛠️ Colonne à éditer", colonnes_editables, key=f"selectbox_editeur_activites_planifiees")
            #     st.session_state.editeur_activites_planifiees_utiliser_index_colonne_courante = False
            #     st.session_state.editeur_activites_planifiees_index_colonne_courante = colonnes_editables.index(colonne)
            #     if colonne != "Lien de recherche":
            #         valeur_courante = row[colonne]
            #         if pd.isna(valeur_courante):
            #             valeur_courante = ""
            #     else:
            #         valeur_courante = lien
            #     nouvelle_valeur = st.text_input(f"✏️ Edition", valeur_courante) 
            #     if st.button("✅ Valider", key="validation_editeur_activites_planifiees"):
            #         if colonne == "Lien de recherche":
            #             undo_redo_save()
            #             liens_activites[row["Activité"]] = nouvelle_valeur
            #             save_lnk_to_gsheet(liens_activites)
            #             st.rerun()
            #         else:
            #             colonne_df = RENOMMAGE_COLONNES_INVERSE[colonne] if colonne in RENOMMAGE_COLONNES_INVERSE else colonne
            #             affecter_valeur(df, index_df, colonne_df, nouvelle_valeur, forcer_reaffichage=["All"])
                                
# Affiche les activités non planifiées dans un tableau
def afficher_activites_non_planifiees(df):
    st.markdown("##### Activités non planifiées")

    # Constitution du df à afficher
    non_planifies = get_activites_non_planifiees(df).sort_values(by=["Debut_dt"], ascending=[True])
    df_display = non_planifies.rename(columns=RENOMMAGE_COLONNES)
    df_display["__index"] = df_display.index
    df_display.drop(columns=["Date", "Debut_dt", "Duree_dt"], inplace=True)

    # Initialisation du compteur qui permet de savoir si l'on doit forcer le réaffichage de l'aggrid après une suppression de ligne 
    if "aggrid_activites_non_planifiees_reset_counter" not in st.session_state:
        st.session_state.aggrid_activites_non_planifiees_reset_counter = 0
    
    # Initialisation du flag permettant de savoir si l'on est en mode réaffichage complet de l'aggrid
    if "aggrid_activites_non_planifiees_forcer_reaffichage" not in st.session_state:
        st.session_state.aggrid_activites_non_planifiees_forcer_reaffichage = False
   
    # Initialisation du flag permettant de savoir si l'on doit gérer les modifications de cellules
    if "aggrid_activites_non_planifiees_gerer_modification_cellule" not in st.session_state:
        st.session_state.aggrid_activites_non_planifiees_gerer_modification_cellule = True
   
    # Initialisation de la variable d'état contenant l'index de ligne sélectionnée courant
    if "aggrid_activites_non_planifiees_idx_row_courant" not in st.session_state:
        st.session_state.aggrid_activites_non_planifiees_idx_row_courant = None
   
    # # Initialisation de la variable d'état contenant l'index de la colonne sélectionnée de l'éditeur d'activités non planifiées
    # if "editeur_activites_non_planifiees_index_colonne_courante" not in st.session_state:
    #     st.session_state.editeur_activites_non_planifiees_index_colonne_courante = 0

    # # Initialisation du flag permettant de savoir si l'on doit utiliser l'index de colonne sélectionnée pour paramétrer la selectbox concernée de l'éditeur d'activités non planifiées
    # if "editeur_activites_non_planifiees_utiliser_index_colonne_courante" not in st.session_state:
    #     st.session_state.editeur_activites_non_planifiees_utiliser_index_colonne_courante = False

    # Enregistrement dans st.session_state d'une copy du df à afficher
    st.session_state.df_display_activites_non_planifiees = df_display.copy()

    # Configuration
    gb = GridOptionsBuilder.from_dataframe(df_display)

    # Masquage des colonnes de travail
    gb.configure_column("__index", hide=True)

    # Colonnes editables
    editable_cols = {col: True for col in df_display.columns if col != "__index"}
    # editable_cols["Date"] = False  
    editable_cols["Fin"] = False  
    for col, editable in editable_cols.items():
        gb.configure_column(col, editable=editable)

    # Retaillage largeur colonnes
    gb.configure_default_column(resizable=True)

    # Configuration de la sélection
    pre_selected_row = 0  # par défaut
    if "activites_non_planifiees_selected_row" in st.session_state:
        valeur_index = st.session_state["activites_non_planifiees_selected_row"]
        matches = df_display[df_display["__index"].astype(str) == str(valeur_index)]
        if not matches.empty:
            pre_selected_row = df_display.index.get_loc(matches.index[0])
    gb.configure_selection(selection_mode="single", use_checkbox=False, pre_selected_rows=[pre_selected_row])
    
    js_code = JsCode(f"""
            function(params) {{
                params.api.sizeColumnsToFit();
                params.api.ensureIndexVisible({pre_selected_row}, 'middle');
                params.api.getDisplayedRowAtIndex({pre_selected_row}).setSelected(true);
            }}
        """)
    # js_code = JsCode(f"""
    #         function(params) {{
    #             params.api.ensureIndexVisible({pre_selected_row}, 'middle');
    #             params.api.getDisplayedRowAtIndex({pre_selected_row}).setSelected(true);

    #             // Auto-size all columns to fit content
    #             let allColumnIds = [];
    #             params.columnApi.getAllColumns().forEach(function(column) {{
    #                 allColumnIds.push(column.colId);
    #             }});
    #             params.columnApi.autoSizeColumns(allColumnIds);
    #         }}
    #     """)
    gb.configure_grid_options(onGridReady=js_code)

    grid_options = gb.build()
    grid_options["suppressMovableColumns"] = True

    # Affichage
    response = AgGrid(
        df_display,
        gridOptions=grid_options,
        allow_unsafe_jscode=True,
        height=250,
        update_mode=GridUpdateMode.MODEL_CHANGED | GridUpdateMode.SELECTION_CHANGED,
        key=f"Activités non planifiées {st.session_state.aggrid_activites_non_planifiees_reset_counter}",  # clé stable mais changeante après suppression de ligne ou modification de cellule pour forcer le reaffichage
    )

    # Affectation de la ligne sélectionnée courante
    selected_rows = response["selected_rows"]
    if st.session_state.aggrid_activites_non_planifiees_forcer_reaffichage == True:
        row = df_display.iloc[pre_selected_row]
    else:
        if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty:
            row = selected_rows.iloc[0] 
        elif isinstance(selected_rows, list) and len(selected_rows) > 0:
            row = selected_rows[0]
        else: 
            row = df_display.iloc[pre_selected_row]
    st.session_state.aggrid_activites_non_planifiees_forcer_reaffichage = False

    # Gestion des modifications de cellules
    if st.session_state.aggrid_activites_non_planifiees_gerer_modification_cellule == True:
        df_modifie = pd.DataFrame(response["data"])
        lignes_modifiees = get_lignes_modifiees(df_modifie, st.session_state.df_display_activites_non_planifiees)
        if lignes_modifiees:
            undo_redo_save()
            for i, idx in lignes_modifiees:
                for col in df_modifie.drop(columns=["__index"]).columns:
                    if col not in ["Date", "Fin"]:
                        col_df = RENOMMAGE_COLONNES_INVERSE[col] if col in RENOMMAGE_COLONNES_INVERSE else col
                        if df.at[idx, col_df] != df_modifie.at[i, col]:
                            # st.session_state.editeur_activites_non_planifiees_utiliser_index_colonne_courante = True
                            affecter_valeur(df,idx, col_df, df_modifie.at[i, col], forcer_reaffichage=["Debut", "Duree"])
    st.session_state.aggrid_activites_non_planifiees_gerer_modification_cellule = True

    # 🟡 Traitement du clic
    if row is not None:
        index_df = row["__index"]
        if index_df != st.session_state.aggrid_activites_non_planifiees_idx_row_courant:
            st.session_state.editeur_activite_courante_idx = index_df
        st.session_state.aggrid_activites_non_planifiees_idx_row_courant = index_df

        # Enregistrement de la sélection courante pour gestion de la sélection
        st.session_state.activites_non_planifiees_selected_row = index_df

        with st.expander("Contrôles"):

            nom_activite = str(row["Activité"]).strip() 

            if nom_activite:
                st.markdown(f"🎯 Activité sélectionnée : **{nom_activite}**")

                # Bouton Chercher, Supprimer, Ajouter au planning 
                col1, col2, col3 = st.columns([0.5,0.5,4])
                with col1:
                    if not est_pause_str(nom_activite):
                        afficher_bouton_recherche_net(nom_activite)
                with col2:
                    if st.button("🗑️", key="SupprimerActiviteNonPlanifiee"):
                        undo_redo_save()
                        st.session_state.activites_planifiees_selected_row = ligne_voisine_index(df_display, index_df)
                        supprimer_activite(df, index_df)
                        forcer_reaffichage_activites_non_planifiees()
                        forcer_reaffichage_df("activites_planifiables_dans_creneau_selectionne")
                        save_one_row_to_gsheet(df, index_df)
                        st.rerun()
                with col3:
                    col11, col12 = st.columns([0.5,4])
                    with col12:
                        # Déterminer les jours disponibles 
                        jours_possibles = get_jours_possibles(df, get_activites_planifiees(df), index_df)
                        if jours_possibles:
                            jours_label = [f"Le {int(jour):02d}" for jour in jours_possibles]
                            jour_selection = st.selectbox("Choix jour", jours_label, label_visibility = "collapsed", key = "ChoixJourPlanifActiviteNonPlanifiee")
                    with col11:
                        # Bouton pour confirmer
                        if jours_possibles:
                            if st.button("🗓️", key="AjouterAuxActivitésPlanifiees"):
                                jour_choisi = int(jour_selection.split()[-1])
                                undo_redo_save()
                                st.session_state.activites_non_planifiees_selected_row = ligne_voisine_index(df_display, index_df)
                                st.session_state.activites_planifiees_selected_row = index_df
                                df.at[index_df, "Date"] = jour_choisi
                                forcer_reaffichage_activites_planifiees()
                                forcer_reaffichage_activites_non_planifiees()
                                forcer_reaffichage_df("creneaux_disponibles")
                                save_one_row_to_gsheet(df, index_df)
                                st.rerun()

            # Formulaire d'édition
            # with st.expander("Edition de la ligne sélectionnée"):
            #     colonnes_editables = [col for col in df_display.columns if col not in ["__jour", "__index", "Date", "Fin"]]
                
            #     # Ajout de l'hyperlien aux infos éditables s'il existe
            #     if st.session_state.liens_activites is not None:
            #         liens_activites = st.session_state.liens_activites
            #         lien = liens_activites.get(row["Activité"])
            #         if lien:
            #             colonnes_editables.append("Lien de recherche")

            #     # colonne = st.radio("⚙️ Colonne à éditer", colonnes_editables, key="selectbox_editeur_activites_non_planifiees", label_visibility="collapsed")
            #     index_colonne_courante = st.session_state.editeur_activites_non_planifiees_index_colonne_courante
            #     if st.session_state.editeur_activites_non_planifiees_utiliser_index_colonne_courante == True:
            #         colonne = st.selectbox("🛠️ Colonne à éditer", colonnes_editables, index=index_colonne_courante, key=f"selectbox_editeur_activites_non_planifiees")
            #     else:
            #         colonne = st.selectbox("🛠️ Colonne à éditer", colonnes_editables, key=f"selectbox_editeur_activites_non_planifiees")
            #     st.session_state.editeur_activites_non_planifiees_utiliser_index_colonne_courante = False
            #     st.session_state.editeur_activites_non_planifiees_index_colonne_courante = colonnes_editables.index(colonne)
            #     if colonne != "Lien de recherche":
            #         valeur_courante = row[colonne]
            #         if pd.isna(valeur_courante):
            #             valeur_courante = ""
            #     else:
            #         valeur_courante = lien
            #     nouvelle_valeur = st.text_input(f"✏️ Edition", valeur_courante)
            #     if st.button("✅ Valider", key="validation_editeur_activites_non_planifiees"):
            #         if colonne == "Lien de recherche":
            #             undo_redo_save()
            #             liens_activites[row["Activité"]] = nouvelle_valeur
            #             save_lnk_to_gsheet(liens_activites)
            #             st.rerun()
            #         else:
            #             colonne_df = RENOMMAGE_COLONNES_INVERSE[colonne] if colonne in RENOMMAGE_COLONNES_INVERSE else colonne
            #             affecter_valeur(df, index_df, colonne_df, nouvelle_valeur, forcer_reaffichage=["All"])

            ajouter_activite(df)

# Affichage de l'éditeur d'activité
def afficher_editeur_activite(df):
    st.markdown("##### Editeur d'activité")
    with st.expander("Editeur d'activité"):
        # activites = df["Activite"].dropna().astype(str).str.strip()
        # activites = activites[activites != ""].unique().tolist()
        # activites.sort()

        # # Affichage dans une selectbox
        # if "editeur_activite_courante_idx" not in st.session_state:
        #     st.session_state.editeur_activite_courante_idx = df.index[0]
        # selectbox_index = 0
        # activite_selectionnee_courante = df.loc[st.session_state.editeur_activite_courante_idx, "Activite"].strip() if st.session_state.editeur_activite_courante_idx in df.index else None
        # if activite_selectionnee_courante is not None:
        #     selectbox_index = activites.index(activite_selectionnee_courante) if activite_selectionnee_courante in activites else 0
        # activite_selectionnee = st.selectbox("⚙️ Activité", activites, index=selectbox_index)
        # row = df[df["Activite"].astype(str).str.strip() == activite_selectionnee].iloc[0]
        # index_df = row.name  # index réel de la ligne dans df

        # Construction des libellés d'activités à afficher dansd la selectbox
        libelles = df.apply(
            lambda row: f"{'??' if pd.isna(row['Date']) else int(row['Date'])} - "
                        f"[{row['Debut']}-{row['Fin']}] - "
                        f"{str(row['Activite']).strip()}",
            axis=1
        )
        libelles_list = libelles.tolist()

        # Détermination de la préselection dans la selectbox 
        if "editeur_activite_courante_idx" not in st.session_state:
            st.session_state.editeur_activite_courante_idx = df.index[0]
        selectbox_index = 0
        idx = st.session_state.editeur_activite_courante_idx
        if idx in df.index:
            row = df.loc[idx]
            libelle_activite_selectionnee_courante = f"{'??' if pd.isna(row['Date']) else int(row['Date'])} - " \
                    f"[{row['Debut']}-{row['Fin']}] - " \
                    f"{str(row['Activite']).strip()}"
            selectbox_index = libelles_list.index(libelle_activite_selectionnee_courante) if libelle_activite_selectionnee_courante in libelles_list else 0

        # Affichage de la selectbox et déduction de la ligne sélectionnée dansle df
        selection = st.selectbox("⚙️ Activité", libelles_list, index=selectbox_index)
        index_selectionne = libelles[libelles == selection].index[0]
        row = df.loc[index_selectionne]
        index_df = row.name  # index réel de la ligne dans df

        if pd.notna(row["Date"]) and str(row["Duree"]).strip() != "":
            colonnes_editables = [col for col in df.columns if col not in ["Date", "Debut", "Fin", "Duree", "Debut_dt", "Duree_dt"]]
        else:
            colonnes_editables = [col for col in df.columns if col not in ["Date", "Fin", "Debut_dt", "Duree_dt"]]

        # Ajout de l'hyperlien aux infos éditables s'il existe
        if st.session_state.liens_activites is not None:
            liens_activites = st.session_state.liens_activites
            lien = liens_activites.get(row["Activite"])
            if lien:
                colonnes_editables.append("Lien de recherche")

        colonnes_editables_avec_accents = [RENOMMAGE_COLONNES.get(col, col) for col in colonnes_editables]
        
        if "editeur_activites_index_colonne_courante" not in st.session_state:
            st.session_state.editeur_activites_index_colonne_courante = 0

        # index_colonne_courante = st.session_state.editeur_activites_index_colonne_courante
        # colonne = st.selectbox("🛠️ Choix de la colonne à éditer", colonnes_editables, index=index_colonne_courante, key="selectbox_editeur_activites_choix_colonne")
        # colonne = st.radio("⚙️ Choix de la colonne à éditer", colonnes_editables, key="selectbox_editeur_activites_planifiees_choix_colonne", label_visibility="collapsed")
        
        colonne = st.selectbox("⚙️ Colonne", colonnes_editables_avec_accents, key="selectbox_editeur_activites_choix_colonne")
        st.session_state.editeur_activites_index_colonne_courante = colonnes_editables_avec_accents.index(colonne)
        colonne_df = RENOMMAGE_COLONNES_INVERSE[colonne] if colonne in RENOMMAGE_COLONNES_INVERSE else colonne

        if colonne_df != "Lien de recherche":
            valeur_courante = row[colonne_df]
        else:
            valeur_courante = lien

        nouvelle_valeur = st.text_input(f"✏️ Valeur", "" if pd.isna(valeur_courante) else str(valeur_courante)) 
        if st.button("✅ Valider", key="validation_editeur_activites"):
            if colonne_df == "Lien de recherche":
                undo_redo_save()
                liens_activites[row["Activite"]] = nouvelle_valeur
                save_lnk_to_gsheet(liens_activites)
                st.rerun()
            else:
                affecter_valeur(df, index_df, colonne_df, nouvelle_valeur)

# Affecte une nouvelle valeur à une cellule du df donnée par son index et sa colonne
def affecter_valeur(df, index, colonne, nouvelle_valeur, forcer_reaffichage=["All"], inhiber_gestion_modification_cellule=True):
    valeur_courante = df.at[index, colonne]
    erreur = None
    if colonne == "Debut" and not est_format_heure(nouvelle_valeur):
        erreur = "⛔ Format attendu : HHhMM (ex : 10h00)"
    elif colonne == "Duree" and not est_format_duree(nouvelle_valeur):
        erreur = "⛔ Format attendu : HhMM (ex : 1h00 ou 0h30)"
    elif colonne == "Relache" and not est_relache_valide(nouvelle_valeur):
        erreur = "⛔ Format attendu : 1, 10, pair, impair"
    elif colonne == "Reserve" and not est_reserve_valide(nouvelle_valeur):
        erreur = "⛔ Format attendu : Oui, Non"
    elif ptypes.is_numeric_dtype(df[colonne]):
        try:
            if "." not in nouvelle_valeur and "," not in nouvelle_valeur and "e" not in nouvelle_valeur.lower():
                nouvelle_valeur = int(nouvelle_valeur)
            else:
                nouvelle_valeur = float(nouvelle_valeur)
        except:
            erreur = "⛔ Format numérique attendu"

    if erreur:
        st.error(erreur)
    elif nouvelle_valeur != valeur_courante:
        try:
            df.at[index, colonne] = nouvelle_valeur
        except Exception as e:
            st.error(f"⛔ {e}")
        else:
            df.at[index, colonne] = valeur_courante
            undo_redo_save()
            df.at[index, colonne] = nouvelle_valeur
            if inhiber_gestion_modification_cellule:
                st.session_state.aggrid_activites_planifiees_gerer_modification_cellule = False
                st.session_state.aggrid_activites_non_planifiees_gerer_modification_cellule = False
            if colonne in forcer_reaffichage or forcer_reaffichage[0].lower() == "all":
                forcer_reaffichage_activites_planifiees()
                forcer_reaffichage_activites_non_planifiees()
                forcer_reaffichage_df("creneaux_disponibles")
            save_one_row_to_gsheet(df, index)
            st.rerun()

# Vérifie qu'une valeur est bien Oui Non
def est_reserve_valide(val):
    return str(val).strip().lower() in ["oui", "non"]

# Vérifie qu'une valeur contient bien NaN ou "" ou quelque chose du type "1", "1,10", "1, 10", "1, pair", "12, impair"
def est_relache_valide(val):

    # Cas val vide ou NaN
    if pd.isna(val) or str(val).strip() == "":
        return True

    val_str = str(val).strip().lower()

    # Autorise : chiffres ou mots-clés (pair, impair) séparés par virgules
    # Exemples valides : "1", "1, 10", "1, impair", "2, pair"
    # Regex : liste d'éléments séparés par des virgules, chaque élément est un entier ou 'pair'/'impair'
    motif = r"^\s*(\d+|pair|impair)(\s*,\s*(\d+|pair|impair))*\s*$"

    return re.fullmatch(motif, val_str) is not None

# Vérifie si une date de référence est compatible avec la valeur de la colonne Relache qui donne les jours de relache pour un spectacle donné
def est_hors_relache(relache_val, date_val):
    if pd.isna(relache_val) or pd.isna(date_val):
        return True  # Aucune relâche spécifiée ou date absente

    if not est_relache_valide(relache_val):
        return True
    
    try:
        date_int = int(float(date_val))
    except (ValueError, TypeError):
        return True  # Si la date n'est pas exploitable, on la considère planifiable

    # Normaliser le champ Relache en chaîne
    if isinstance(relache_val, (int, float)):
        relache_str = str(int(relache_val))
    else:
        relache_str = str(relache_val).strip().lower()

    # Cas particulier : pair / impair
    if "pair" in relache_str and date_int % 2 == 0:
        return False
    if "impair" in relache_str and date_int % 2 != 0:
        return False

    # Cas général : liste explicite de jours (ex : "20,21")
    try:
        jours = [int(float(x.strip())) for x in relache_str.split(",")]
        if date_int in jours:
            return False
    except ValueError:
        pass  # ignorer s'il ne s'agit pas d'une liste de jours

    return True

# Suppression d'une activité d'un df
def supprimer_activite(df, idx):
    df.loc[idx] = pd.NA

# Suppression d'une activité planifiée d'un df
def supprimer_activite_planifiee(df, idx):
    if est_pause(df.loc[idx]):
        df.loc[idx] = pd.NA
    else:
        df.at[idx, "Date"] = None

# Création de la liste des créneaux avant/après pour chaque activité planifiée
def get_creneaux(df, planifies, traiter_pauses):

    def description_creneau(row, borne_min, borne_max, avant, apres, type_creneau):
        titre = row["Activite"] if not pd.isna(row["Activite"]) else ""
        date_str = str(int(row["Date"])) if pd.notnull(row["Date"]) else ""
        return {
            "Date": date_str,
            "Debut": borne_min.strftime('%Hh%M'),
            "Fin": borne_max.strftime('%Hh%M'),
            "Activité avant": avant,
            "Activité après": apres,
            "__type_creneau": type_creneau,
            "__index": row.name
        }
    
    creneaux = []
    bornes = []

    for _, row in planifies.iterrows():

        # Heure de début d'activité
        heure_debut = row["Debut_dt"]
        # Heure de fin d'activité
        heure_fin = heure_debut + row["Duree_dt"] if pd.notnull(heure_debut) and pd.notnull(row["Duree_dt"]) else None

        # Ajout des creneaux avant l'activité considérée s'ils existent
        if pd.notnull(heure_debut):
            if get_activites_planifiables_avant(df, planifies, row, traiter_pauses):
                borne_min, borne_max, pred = get_creneau_bounds_avant(planifies, row)
                if (borne_min, borne_max) not in bornes:
                    bornes.append((borne_min, borne_max))
                    creneaux.append(description_creneau(row, borne_min, borne_max, pred["Activite"] if pred is not None else "", row["Activite"], "Avant"))

        # Ajout des creneaux après l'activité considérée s'ils existent
        if pd.notnull(heure_fin):
            if get_activites_planifiables_apres(df, planifies, row, traiter_pauses):
                borne_min, borne_max, next = get_creneau_bounds_apres(planifies, row)
                if (borne_min, borne_max) not in bornes:
                    bornes.append((borne_min, borne_max))
                    creneaux.append(description_creneau(row, borne_min, borne_max, row["Activite"], next["Activite"] if next is not None else "", "Après"))

    return pd.DataFrame(creneaux).sort_values(by=["Date", "Debut"], ascending=[True, True])

# Renvoie les bornes du créneau existant avant une activité donnée par son descripteur ligne_ref
def get_creneau_bounds_avant(planifies, ligne_ref):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Debut_dt"] if pd.notnull(ligne_ref["Debut_dt"]) else datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))
    duree_ref = ligne_ref["Duree_dt"] if pd.notnull(ligne_ref["Duree_dt"]) else datetime.timedelta(0)
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None    

    # Chercher l'activité planifiée précédente sur le même jour
    planifies_jour_ref = planifies[planifies["Date"] == date_ref]
    planifies_jour_ref = planifies_jour_ref.sort_values(by="Debut_dt")
    prev = planifies_jour_ref[planifies_jour_ref["Debut_dt"] < debut_ref].tail(1)

    # Calculer l'heure de début minimum du créneau
    if not prev.empty:
        prev_fin = datetime.datetime.combine(BASE_DATE, prev["Debut_dt"].iloc[0].time()) + prev["Duree_dt"].iloc[0]
        debut_min = prev_fin
    else:
        debut_min = datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))

    # Calculer l'heure de fin max du créneau
    fin_max = datetime.datetime.combine(BASE_DATE, debut_ref.time())

    return debut_min, fin_max, prev.iloc[0] if not prev.empty else None

# Renvoie les bornes du créneau existant après une activité donnée par son descripteur ligne_ref
def get_creneau_bounds_apres(planifies, ligne_ref):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Debut_dt"] if pd.notnull(ligne_ref["Debut_dt"]) else datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))
    duree_ref = ligne_ref["Duree_dt"] if pd.notnull(ligne_ref["Duree_dt"]) else datetime.timedelta(0)
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else debut_ref    


    # Ajuster la date de référence si le jour a changé
    if fin_ref.day != debut_ref.day:
        date_ref = date_ref + fin_ref.day - debut_ref.day  

    # Chercher l'activité planifiée suivante sur le même jour de référence
    planifies_jour_ref = planifies[planifies["Date"] == date_ref]
    planifies_jour_ref = planifies_jour_ref.sort_values(by="Debut_dt")
    next = planifies_jour_ref[planifies_jour_ref["Debut_dt"] + planifies_jour_ref["Duree_dt"] > fin_ref].head(1)

    # Calculer l'heure de fin max du créneau
    if not next.empty:
        fin_max = datetime.datetime.combine(BASE_DATE, next["Debut_dt"].iloc[0].time())
    else:
        fin_max = datetime.datetime.combine(BASE_DATE, datetime.time(23, 59))

    # Calculer l'heure de début minimum du créneau
    debut_min = datetime.datetime.combine(BASE_DATE, fin_ref.time())

    return debut_min, fin_max, next.iloc[0] if not next.empty else None

# Renvoie la liste des activités planifiables avant une activité donnée par son descripteur ligne_ref
def get_activites_planifiables_avant(df, planifies, ligne_ref, traiter_pauses=True):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Debut_dt"] if pd.notnull(ligne_ref["Debut_dt"]) else datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))
    duree_ref = ligne_ref["Duree_dt"] if pd.notnull(ligne_ref["Duree_dt"]) else datetime.timedelta(0)
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None

    proposables = [] 

    debut_min, fin_max, _ = get_creneau_bounds_avant(planifies, ligne_ref)
    if debut_min >= fin_max:
        return proposables  # Pas d'activités planifiables avant si le créneau est invalide

    for _, row in df[df["Date"].isna()].iterrows():
        if pd.isna(row["Debut_dt"]) or pd.isna(row["Duree_dt"]):
            continue
        h_debut = datetime.datetime.combine(BASE_DATE, row["Debut_dt"].time())
        h_fin = h_debut + row["Duree_dt"]
        # Le spectacle doit commencer après debut_min et finir avant fin_max
        if h_debut >= debut_min + MARGE and h_fin <= fin_max - MARGE and est_hors_relache(row["Relache"], date_ref):
            nouvelle_ligne = row.drop(labels=["Date", "Debut_dt", "Duree_dt"]).to_dict()
            nouvelle_ligne["__type_activite"] = "ActiviteExistante"
            nouvelle_ligne["__index"] = row.name
            proposables.append(nouvelle_ligne)
    if traiter_pauses:
        ajouter_pauses(proposables, planifies, ligne_ref, "Avant")
    return proposables

# Renvoie la liste des activités planifiables après une activité donnée par son descripteur ligne_ref
def get_activites_planifiables_apres(df, planifies, ligne_ref, traiter_pauses=True):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Debut_dt"] if pd.notnull(ligne_ref["Debut_dt"]) else datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))
    duree_ref = ligne_ref["Duree_dt"] if pd.notnull(ligne_ref["Duree_dt"]) else datetime.timedelta(0)
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None   

    proposables = []

    debut_min, fin_max, _ = get_creneau_bounds_apres(planifies, ligne_ref)
    if debut_min >= fin_max:
        return proposables  # Pas d'activités planifiables avant si le créneau est invalide

    if fin_ref.day != debut_ref.day:
        return proposables  # Pas d'activités planifiables après si le jour a changé

    for _, row in df[df["Date"].isna()].iterrows():
        if pd.isna(row["Debut_dt"]) or pd.isna(row["Duree_dt"]):
            continue
        h_debut = datetime.datetime.combine(BASE_DATE, row["Debut_dt"].time())
        h_fin = h_debut + row["Duree_dt"]
        # Le spectacle doit commencer après debut_min et finir avant fin_max
        if h_debut >= debut_min + MARGE and h_fin <= fin_max - MARGE and est_hors_relache(row["Relache"], date_ref):
            nouvelle_ligne = row.drop(labels=["Date", "Debut_dt", "Duree_dt"]).to_dict()
            nouvelle_ligne["__type_activite"] = "ActiviteExistante"
            nouvelle_ligne["__index"] = row.name
            proposables.append(nouvelle_ligne)
    if traiter_pauses:
        ajouter_pauses(proposables, planifies, ligne_ref, "Après")
    return proposables
    
# Vérifie si une pause d'un type donné est déjà présente pour un jour donné dans le dataframe des activités planiées
def pause_deja_existante(planifies, jour, type_pause):
    activites_planifies_du_jour = planifies[planifies["Date"] == jour]
    return activites_planifies_du_jour["Activite"].astype(str).str.contains(type_pause, case=False, na=False).any() 

# Ajoute les pauses possibles (déjeuner, dîner, café) à une liste d'activités planifiables pour une activité donnée par son descripteur ligne_ref
def ajouter_pauses(proposables, planifies, ligne_ref, type_creneau):

    # Pause repas
    def ajouter_pause_repas(proposables, date_ref, debut_min, fin_max, pause_debut_min, pause_debut_max, type_repas):
        if not pause_deja_existante(planifies, date_ref, type_repas):
            if type_creneau == "Avant":
                h_dej = min(max(fin_max - DUREE_REPAS - MARGE, 
                    datetime.datetime.combine(BASE_DATE, pause_debut_min)), 
                    datetime.datetime.combine(BASE_DATE, pause_debut_max))
                if h_dej - MARGE >= debut_min and h_dej + MARGE <= fin_max:
                    proposables.append((h_dej, desc(h_dej, DUREE_REPAS, f"Pause {type_repas}"), None, type_repas))
            elif type_creneau == "Après":
                h_dej = min(max(debut_min + MARGE, 
                    datetime.datetime.combine(BASE_DATE, pause_debut_min)), 
                    datetime.datetime.combine(BASE_DATE, pause_debut_max))
                if h_dej - MARGE >= debut_min and h_dej + MARGE <= fin_max:
                    nouvelle_ligne = completer_ligne({
                        "Debut": h_dej.strftime('%Hh%M'),
                        "Fin": (h_dej + DUREE_REPAS).strftime('%Hh%M'),
                        "Duree": duree_str(DUREE_REPAS),
                        "Activite": f"Pause {type_repas}",
                        "__type_activite": type_repas
                    })
                    proposables.append(nouvelle_ligne)
    
    def ajouter_pause_cafe(proposables, debut_min, fin_max):
        if not est_pause(ligne_ref):
            Lieu_ref = ligne_ref["Lieu"]
            if type_creneau == "Avant":
                i = planifies.index.get_loc(ligne_ref.name)  
                Lieu_ref_prev = planifies.iloc[i - 1]["Lieu"] if i > 0 else None
                h_cafe = fin_max - DUREE_CAFE
                if not pd.isna(Lieu_ref_prev) and Lieu_ref == Lieu_ref_prev: 
                    # Dans ce cas pas la peine de tenir compte de la marge avec le spectacle précédent 
                    if h_cafe >= debut_min: 
                        proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "café"))
                else: 
                    # Dans ce cas on tient compte de la marge avec le spectacle précédent sauf si debut_min = 0h00
                    marge_cafe = MARGE if debut_min != datetime.datetime.combine(BASE_DATE, datetime.time(0, 0)) else datetime.timedelta(minutes=0) 
                    if h_cafe >= debut_min + marge_cafe:
                        nouvelle_ligne = completer_ligne({
                            "Debut": h_cafe.strftime('%Hh%M'),
                            "Fin": (h_cafe + DUREE_CAFE).strftime('%Hh%M'),
                            "Duree": duree_str(DUREE_CAFE),
                            "Activite": "Pause café",
                            "__type_activite": "café"
                        })
                        proposables.append(nouvelle_ligne)
            elif type_creneau == "Après":
                i = planifies.index.get_loc(ligne_ref.name)  
                Lieu_ref_suiv = planifies.iloc[i + 1]["Lieu"] if i < len(planifies) - 1 else None
                h_cafe = debut_min
                if not pd.isna(Lieu_ref_suiv) and Lieu_ref == Lieu_ref_suiv: 
                    # Dans ce cas pas la peine de tenir compte de la marge avec le spectacle suivant 
                    if h_cafe + DUREE_CAFE <= fin_max: 
                        nouvelle_ligne = completer_ligne({
                            "Debut": h_cafe.strftime('%Hh%M'),
                            "Fin": (h_cafe + DUREE_CAFE).strftime('%Hh%M'),
                            "Duree": duree_str(DUREE_CAFE),
                            "Activite": "Pause café",
                            "__type_activite": "café"
                        })
                        proposables.append(nouvelle_ligne)
                else: 
                    # Dans ce cas on tient compte de la marge avec le spectacle suivant sauf si fin_max = 23h59
                    marge_cafe = MARGE if fin_max != datetime.datetime.combine(BASE_DATE, datetime.time(23, 59)) else datetime.timedelta(minutes=0)
                    if h_cafe + DUREE_CAFE <= fin_max - marge_cafe:
                        nouvelle_ligne = completer_ligne({
                            "Debut": h_cafe.strftime('%Hh%M'),
                            "Fin": (h_cafe + DUREE_CAFE).strftime('%Hh%M'),
                            "Duree": duree_str(DUREE_CAFE),
                            "Activite": "Pause café",
                            "__type_activite": "café"
                        })
                        proposables.append(nouvelle_ligne)

    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Debut_dt"] if pd.notnull(ligne_ref["Debut_dt"]) else datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))
    duree_ref = ligne_ref["Duree_dt"] if pd.notnull(ligne_ref["Duree_dt"]) else datetime.timedelta(0)
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None    

    def desc(h, duree, nom):
        # return f"{int(date_ref)} de {h.strftime('%Hh%M')} à {(h + duree).time().strftime('%Hh%M')} ({formatter_timedelta(duree)}) - {nom}"
        return f"{int(date_ref)} - {h.strftime('%Hh%M')} - {nom}"
    
    # Récupération des bornes du créneau
    if type_creneau == "Avant":
        debut_min, fin_max, _ = get_creneau_bounds_avant(planifies, ligne_ref)
    elif type_creneau == "Après":
        debut_min, fin_max, _ = get_creneau_bounds_apres(planifies, ligne_ref)
    else:
        raise ValueError("type_creneau doit être 'Avant' ou 'Après'")

    # Pause déjeuner
    ajouter_pause_repas(proposables, date_ref, debut_min, fin_max, PAUSE_DEJ_DEBUT_MIN, PAUSE_DEJ_DEBUT_MAX, "déjeuner")

    # Pause dîner
    ajouter_pause_repas(proposables, date_ref, debut_min, fin_max, PAUSE_DIN_DEBUT_MIN, PAUSE_DIN_DEBUT_MAX, "dîner")

    # Pause café
    ajouter_pause_cafe(proposables, debut_min, fin_max)

def est_pause_str(val):
    valeurs = val.split()
    if not valeurs:
        return False
    return val.split()[0].lower() == "pause"

def est_pause(ligne_ref):
    val = str(ligne_ref["Activite"]).strip()
    return est_pause_str(val)

def est_pause_cafe(ligne_ref):
    if not est_pause(ligne_ref):
        return False
    val = str(ligne_ref["Activite"]).strip()
    valeurs = val.split()
    if not valeurs:
        return False
    if len(valeurs) < 2:
        return False
    return val.split()[0].lower() == "pause" and val.split()[1].lower() == "café"

def sauvegarder_fichier():
    if "df" in st.session_state:

        # Récupération de la worksheet à traiter
        wb = st.session_state.wb
        ws = wb.worksheets[0]
        liens_activites = st.session_state.liens_activites

        # Effacer le contenu de la feuille Excel existante
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column):
            for cell in row:
                cell.value = None  # on garde le style, on efface juste la valeur
                cell.hyperlink = None

        # Réinjecter les données du df dans la feuille Excel
        from copy import copy

        col_activite = None
        for cell in ws[1]:
            if cell.value and str(cell.value).strip().lower() in ["activité"]:
                col_activite = cell.column
        source_font = ws.cell(row=1, column=1).font

        # Réindexer proprement pour éviter les trous
        df_sorted = st.session_state.df.copy()
        df_sorted = df_sorted.sort_values(by=["Date", "Debut_dt"])
        df_sorted = df_sorted.reset_index(drop=True)
        df_sorted = df_sorted.drop(columns=["Debut_dt", "Duree_dt"], errors='ignore')

        # Réécriture sans saut de ligne
        for i, (_, row) in enumerate(df_sorted.iterrows()):
            row_idx = i + 2  # ligne Excel (1-indexée + entête)
            for col_idx, value in enumerate(row, start=1):
                cell = ws.cell(row=row_idx, column=col_idx)

                if pd.isna(value):
                    cell.value = None
                else:
                    try:
                        # Conserve les entiers réels, sinon cast en string
                        v = int(value)
                        if str(v) == str(value).strip():
                            cell.value = v
                        else:
                            cell.value = value
                    except (ValueError, TypeError):
                        cell.value = value

                    # Ajout d'hyperlien pour la colonne Activite
                    if col_activite is not None:
                        if col_idx == col_activite and liens_activites is not None:
                            lien = liens_activites.get(value)
                            if lien:
                                cell.hyperlink = lien
                                cell.font = Font(color="0000EE", underline="single")
                            else:
                                cell.hyperlink = None
                                cell.font = copy(source_font)   

        # Sauvegarde dans un buffer mémoire
        buffer = io.BytesIO()
        wb.save(buffer)

        # Revenir au début du buffer pour le téléchargement
        buffer.seek(0)

        nom_fichier = st.session_state.fn if "fn" in st.session_state else "planification_avignon.xlsx"

        # Bouton de téléchargement
        return st.download_button(
            label="💾",
            data=buffer,
            file_name=nom_fichier,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        return False

# Ajoute une activité non planifée
def ajouter_activite_non_planifiee(df):
    with st.expander("Ajout d'une nouvelle activité non planifiée"):
        with st.form("ajout_activite"):
            # Ligne 1 : Début - Fin
            col1, col2 = st.columns(2)
            with col1:
                debut = st.text_input("Début (ex : 10h00)")
            with col2:
                duree = st.text_input("Durée (ex : 1h00)")

            # Ligne 2 : Nom - Théâtre
            col3, col4 = st.columns(2)
            with col3:
                nom = st.text_input("Nom de l'activité")
            with col4:
                lieu = st.text_input("Lieu")

            # Ligne 3 : Relâche - Priorité
            col5, col6 = st.columns(2)
            with col5:
                relache = st.text_input("Jours de relâche (ex : 5, 10, pair, impair)")
            with col6:
                priorite = st.number_input("Priorité", min_value=1, max_value=10, step=1, value=1)
            
            # Ligne 4 : Réservé
            col7, col8 = st.columns(2)
            with col7:
                reserve = st.selectbox("Réservé", ["Non", "Oui"])

            bouton_ajouter = st.form_submit_button("✅ Ajouter")

        if bouton_ajouter:
            erreurs = []

            # Vérif format
            if not est_format_heure(debut):
                erreurs.append("⛔ Format début invalide (attendu : 10h00)")
            if not est_format_duree(duree):
                erreurs.append("⛔ Format durée invalide (attendu : 1h00)")
            if not nom.strip():
                erreurs.append("⛔ Nom activité obligatoire")
            if not est_relache_valide(relache):
                erreurs.append("⛔ Format relache invalide (attendu : 1, 10, pair, impair)")

            # Vérif doublon
            existe = False
            if not erreurs:
                debut_dt = debut.strip()
                duree_dt = duree.strip()
                nom_clean = nom.strip().lower()
                existe = df[
                    (df["Debut"].astype(str).str.strip() == debut_dt) &
                    (df["Duree"].astype(str).str.strip() == duree_dt) &
                    (df["Activite"].astype(str).str.strip().str.lower() == nom_clean)
                ]
                if not existe.empty:
                    erreurs.append("⚠️ Une activité identique existe déjà dans la liste.")

            if erreurs:
                st.warning("\n".join(erreurs))
            else:
                nouvelle_ligne = {
                    "Debut": debut.strip(),
                    "Duree": duree.strip(),
                    "Activite": nom.strip(),
                    "Lieu": lieu.strip(),
                    "Relache": relache.strip(),
                    "Priorite": priorite,
                    "Reserve": reserve,
                }
                ligne_df = pd.DataFrame([nouvelle_ligne])
                undo_redo_save()
                df = pd.concat([df, ligne_df], ignore_index=True)
                st.success("🎉 Activité ajoutée !")
                save_df_to_gsheet(df)
                st.rerun()
        

# Ajoute une acivité planifiée au df
def ajouter_activite_planifiee(df, date_ref, activite):

    type_activite = activite["__type_activite"]
    if st.button("🗓️", key="AjouterAuPlanningParCréneau"):
        undo_redo_save()
        if type_activite == "ActiviteExistante":
            # Pour les spectacles, on planifie la date et l'heure
            index = activite["__index"]
            df.at[index, "Date"] = date_ref
        elif type_activite == "déjeuner":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(df)  # Ajouter à la fin du DataFrame
            df.at[index, "Date"] = date_ref
            df.at[index, "Debut"] = activite["Debut"]
            df.at[index, "Duree"] = formatter_timedelta(DUREE_REPAS)
            df.at[index, "Activite"] = "Pause déjeuner"
        elif type_activite == "dîner":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(df)  # Ajouter à la fin du DataFrame
            df.at[index, "Date"] = date_ref
            df.at[index, "Debut"] = activite["Debut"]
            df.at[index, "Duree"] = formatter_timedelta(DUREE_REPAS)
            df.at[index, "Activite"] = "Pause dîner"
        elif type_activite == "café":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(df)  # Ajouter à la fin du DataFrame
            df.at[index, "Date"] = date_ref
            df.at[index, "Debut"] = activite["Debut"]
            df.at[index, "Duree"] = formatter_timedelta(DUREE_CAFE)
            df.at[index, "Activite"] = "Pause café"

        st.session_state.activites_planifiees_selected_row = index
        forcer_reaffichage_activites_planifiees()
        forcer_reaffichage_df("creneaux_disponibles")
        # st.session_state.activites_non_planifiees_selected_row = ligne_voisine_index(st.session_state.df_display_activites_non_planifiees, index)
        # forcer_reaffichage_activites_non_planifiees()

        save_one_row_to_gsheet(df, index)
        st.rerun()

# Renvoie les jours possibles pour planifier une activité donnée par son idx
def get_jours_possibles(df, planifies, idx_activite):
    jours_possibles = []

    # Retour si index non valide
    if idx_activite not in df.index:
        return jours_possibles

    # Récupérer la durée de l'activité à considérer
    ligne_a_considerer = df.loc[idx_activite]
    debut = ligne_a_considerer["Debut_dt"]
    fin = ligne_a_considerer["Debut_dt"] + ligne_a_considerer["Duree_dt"]

    if planifies is not None:
        for jour in range(st.session_state.periode_a_planifier_debut.day, st.session_state.periode_a_planifier_fin.day + 1):
            
            if not est_hors_relache(ligne_a_considerer["Relache"], jour):
                continue

            activites_planifies_du_jour = planifies[planifies["Date"] == jour].sort_values("Debut_dt")

            if not activites_planifies_du_jour.empty:
                # Créneau entre minuit et première activité du jour
                premiere_activite_du_jour = activites_planifies_du_jour.iloc[0]
                borne_inf = datetime.datetime.combine(BASE_DATE, datetime.time.min)  # 00h00
                borne_sup = premiere_activite_du_jour["Debut_dt"]
                if debut > borne_inf + MARGE and fin < borne_sup - MARGE:
                    jours_possibles.append(jour)
                    continue  # on prend le premier créneau dispo du jour

                # Ensuite, créneaux entre chaque activité planifiée
                for _, ligne in activites_planifies_du_jour.iterrows():
                    borne_inf, borne_sup, _ = get_creneau_bounds_apres(activites_planifies_du_jour, ligne)
                    if debut > borne_inf + MARGE and fin < borne_sup - MARGE:
                        jours_possibles.append(jour)
                        break  # jour validé, on passe au suivant
            else: # jour libre
                jours_possibles.append(jour)

    return jours_possibles

# Planifie une activité choisie en fonction des jours possibles
def planifier_activite_par_choix_activite(df):
    st.markdown("##### Planification d'une nouvelle activité")

    # Filtrer les activités non planifiées
    planifies = get_activites_planifiees(df)
    non_planifiees = get_activites_non_planifiees(df)

    # Liste d'options formatées
    options_activites = []
    for idx, row in non_planifiees.iterrows():
        if get_jours_possibles(df, planifies, idx):
            label = f"[{row["Debut"]} - {row["Fin"]}] - {str(row["Activite"]).strip()}"
            options_activites.append((label, idx))

    # Afficher la selectbox des activités
    activite_selectionee = st.selectbox("Choix de l'activité à planifier :", options_activites, format_func=lambda x: x[0])
    if activite_selectionee:
        idx_choisi = activite_selectionee[1]

        # Déterminer les jours disponibles 
        jours_possibles = get_jours_possibles(df, planifies, idx_choisi)
        jours_label = [f"{int(jour):02d}" for jour in jours_possibles]

        jour_selection = st.selectbox("Choix du jour :", jours_label)

        # Bouton pour confirmer
        if jour_selection:
            if st.button("🗓️", key="AjouterAuPlanningParChoixActivite"):
                jour_choisi = int(jour_selection.split()[-1])

                # On peut maintenant modifier le df
                df.at[idx_choisi, "Date"] = jour_choisi
                st.rerun()

# Planifie une activité en fonction des créneaux possibles
def planifier_activite_par_choix_creneau(df):
    planifies = get_activites_planifiees(df)
    if not planifies.empty:
        st.markdown("##### Planification des créneaux disponibles")

        # Affectation du flag de traitement des pauses
        traiter_pauses = st.checkbox("Tenir compte des pauses", value=False)  
        if "traiter_pauses" in st.session_state and traiter_pauses != st.session_state.traiter_pauses:
            forcer_reaffichage_df("creneaux_disponibles")
        st.session_state.traiter_pauses = traiter_pauses

        # Création des créneaux avant/après pour chaque spectacle planifié
        creneaux = get_creneaux(df, planifies, traiter_pauses)

        if not creneaux.empty:
            choix_creneau_pred = st.session_state["creneaux_disponibles_selected_row"] if "creneaux_disponibles_selected_row" in st.session_state else None
            choix_creneau = afficher_df("Créneaux disponibles", creneaux, hide=["__type_creneau", "__index"], key="creneaux_disponibles")
            if choix_creneau is not None:
                if choix_creneau_pred is not None and choix_creneau_pred.to_dict() != choix_creneau.to_dict():
                    forcer_reaffichage_df("activites_planifiables_dans_creneau_selectionne")
                type_creneau = choix_creneau["__type_creneau"]
                idx = choix_creneau["__index"]

                ligne_ref = planifies.loc[idx]
                date_ref = ligne_ref["Date"]

                # Choix d'une activité à planifier dans le creneau choisi
                if type_creneau == "Avant":
                    proposables = get_activites_planifiables_avant(df, planifies, ligne_ref, traiter_pauses)

                elif type_creneau == "Après":
                    proposables = get_activites_planifiables_apres(df, planifies, ligne_ref, traiter_pauses)

                if proposables:
                    proposables = pd.DataFrame(proposables).sort_values(by=["Debut"], ascending=[True])
                    label = f"Activités planifiables sur le créneau du {int(date_ref)} entre [{choix_creneau["Debut"]}-{choix_creneau["Fin"]}]"
                    choix_activite = afficher_df(label, proposables, hide=["__type_activite", "__index"], key="activites_planifiables_dans_creneau_selectionne")
                    if choix_activite is not None:
                        ajouter_activite_planifiee(df, date_ref, choix_activite)


# Force le reaffichage de l'agrid des activités planifiées
def forcer_reaffichage_activites_planifiees():
    if "aggrid_activites_planifiees_reset_counter" in st.session_state:
        st.session_state.aggrid_activites_planifiees_reset_counter +=1 
    if "aggrid_activites_planifiees_forcer_reaffichage" in st.session_state:
        st.session_state.aggrid_activites_planifiees_forcer_reaffichage = True

# Force le reaffichage de l'agrid des activités non planifiées
def forcer_reaffichage_activites_non_planifiees():
    if "aggrid_activites_non_planifiees_reset_counter" in st.session_state:
        st.session_state.aggrid_activites_non_planifiees_reset_counter += 1 
    if "aggrid_activites_non_planifiees_forcer_reaffichage" in st.session_state:
        st.session_state.aggrid_activites_non_planifiees_forcer_reaffichage = True

# Réinitialisation de l'environnement après chargement fichier
def initialisation_environnement(df, wb, fn, lnk):
    st.session_state.df = df
    st.session_state.wb = wb
    st.session_state.fn = fn
    st.session_state.liens_activites = lnk
    st.session_state.nouveau_fichier = True
    undo_redo_init(verify=False)
    forcer_reaffichage_activites_planifiees()
    forcer_reaffichage_activites_non_planifiees()
    forcer_reaffichage_df("creneaux_disponibles")

# Charge le fichier Excel contenant les activités à planifier
def charger_fichier():
    # Callback de st.file_uploader pour charger le fichier Excel
    def file_uploader_callback():
        st.session_state.fichier_invalide = True
        fd = st.session_state.get("file_uploader")
        if fd is not None:
            try:
                df = pd.read_excel(fd)
                wb = load_workbook(fd)
                lnk = get_liens_activites(wb)
                nettoyer_donnees(df)
                if "fichier_invalide" not in st.session_state:
                    initialisation_environnement(df, wb, fd.name, lnk)
                    save_to_gsheet(df, fd, lnk)
            except Exception as e:
                st.error(f"Erreur lors du chargement du fichier : {e}")
                st.session_state.fichier_invalide = True

    # Chargement du fichier Excel contenant les activités à planifier
    uploaded_file = st.file_uploader(
        "Choix du fichier Excel contenant les activités à planifier", 
        type=["xlsx"], 
        key="file_uploader",
        on_change=file_uploader_callback)

# Essai de boutons html (à creuser -> permettrait d'avoir des boutons horizontaux avec grisés sur mobile)
def boutons_html():
    # Images
    undo_icon = image_to_base64("undo_actif.png")
    undo_disabled_icon = image_to_base64("undo_inactif.png")
    redo_icon = image_to_base64("undo_actif.png")
    redo_disabled_icon = image_to_base64("undo_inactif.png")

    # États
    undo_enabled = True
    redo_enabled = False

    # Lire le paramètre ?btn=undo ou ?btn=redo
    params = st.query_params
    clicked_btn = params.get("btn", None)

    # Action déclenchée
    if clicked_btn == "undo":
        st.success("Undo cliqué ✅")
        undo_redo_undo()

    elif clicked_btn == "redo":
        st.success("Redo cliqué ✅")
        undo_redo_redo()

    # Affichage des boutons côte à côte (même taille, même style)
    html = f"""
    <div style="display: flex; gap: 1em; align-items: center;">
    <a href="?btn=undo">
        <button style="background:none;border:none;padding:0;cursor:{'pointer' if undo_enabled else 'default'};" {'disabled' if not undo_enabled else ''}>
        <img src="data:image/png;base64,{undo_icon if undo_enabled else undo_disabled_icon}" width="32">
        </button>
    </a>
    <a href="?btn=redo">
        <button style="background:none;border:none;padding:0;cursor:{'pointer' if redo_enabled else 'default'};" {'disabled' if not redo_enabled else ''}>
        <img src="data:image/png;base64,{redo_icon if redo_enabled else redo_disabled_icon}" width="32">
        </button>
    </a>
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)
        
def ajouter_activite(df):
    import numpy as np

    def get_nom_nouvelle_activite(df):
        st.session_state.compteur_activite += 1
        noms_existants = df["Activite"].dropna().astype(str).str.strip().tolist()
        while True:
            nom_candidat = f"Activité {st.session_state.compteur_activite}"
            if nom_candidat not in noms_existants:
                return nom_candidat
            
    # Initialiser le DataFrame dans session_state si absent
    if "compteur_activite" not in st.session_state:
        st.session_state.compteur_activite = 0

    # Bouton Ajouter
    if st.button("➕"):

        undo_redo_save()
        new_idx = len(df)
        df.at[new_idx, "Debut"] = "09h00"
        df.at[new_idx, "Duree"] = "1h00"
        df.at[new_idx, "Activite"] = get_nom_nouvelle_activite(df)
        st.session_state.activites_non_planifiees_selected_row = new_idx
        forcer_reaffichage_activites_non_planifiees()
        forcer_reaffichage_df("activites_planifiables_dans_creneau_selectionne")
        save_one_row_to_gsheet(df, new_idx)
        st.rerun()

# Renvoie True si l'appli tourne sur mobile  
def mode_mobile():    
    from streamlit_js_eval import streamlit_js_eval, get_geolocation
    if "mode_mobile" not in st.session_state:
        _mode_mobile = False
        user_agent = streamlit_js_eval(js_expressions="navigator.userAgent", key="ua")
        if user_agent: # Renvoie toujours None...
            if "Mobile" in user_agent or "Android" in user_agent or "iPhone" in user_agent:
                _mode_mobile = True
        st.session_state.mode_mobile = _mode_mobile
    return True # st.session_state.mode_mobile

# Affichage des choix généraux
def afficher_infos_generales(df):
    with st.expander("Informations générales"):
        # Vérification de cohérence des informations du df
        verifier_coherence(df) 

        # Choix de la période à planifier
        choix_periode_a_planifier(df)

# Affichage des contrôles principaux
def afficher_controles_principaux(df):
    with st.expander("Contrôles principaux"):
        col1, col2, col3 = st.columns([0.5, 0.5, 4])
        with col1:
            if st.button("↩️", 
                disabled=not st.session_state.historique_undo, 
                key="undo_btn") and st.session_state.historique_undo:
                undo_redo_undo()
        with col2:
            if st.button("↪️", 
                disabled=not st.session_state.historique_redo, 
                key="redo_btn") and st.session_state.historique_redo:
                undo_redo_redo()
        with col3:
            sauvegarder_fichier()


def main():
    # Initialisation du cache Google Sheet permettant de gérer la persistence du df et assurer une restitution automatique des datas en cas de rupture de connexion streamlit
    load_from_gsheet()

    # Affichage du titre
    afficher_titre("Planificateur Avignon Off")

    # Affiche de l'aide
    afficher_aide()

    # chargement du fichier Excel
    charger_fichier()

    # Si le fichier est chargé dans st.session_state.df et valide, on le traite
    if "df" in st.session_state and isinstance(st.session_state.df, pd.DataFrame):

        # Accès au DataFrame après nettoyage
        df = st.session_state.df

        if "fichier_invalide" not in st.session_state:

            # Met à jour les données calculées
            recalculer_donnees(df)

            # Affichage des choix généraux
            afficher_infos_generales(df)

            # Affichage des contrôles principaux
            afficher_controles_principaux(df)

            # Affichage des activités planifiées
            afficher_activites_planifiees(df)

            # Affichage des activités non planifiées
            afficher_activites_non_planifiees(df)

            # Affichage de l'éditeur d'activité
            afficher_editeur_activite(df)

            # Planification d'une nouvelle activité par créneau
            planifier_activite_par_choix_creneau(df)            

if __name__ == "__main__":
    main()
