###########################
# Utilitaires application #
###########################

import streamlit as st
import pandas as pd
import datetime
import re
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
import uuid
import math
import hashlib
import json
import numpy as np
import time
import streamlit.components.v1 as components
import unicodedata

from app_const import *

# Permet de mesurer le temps d'exécution d'une fonction avec le décorateur # @chrono
def chrono(func):
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        print(f"{func.__name__} exécutée en {end - start:.6f} s")
        return result
    return wrapper

# Normalise un texte pour faciliter les comparaisons :
# enlève tous ce qui n'est pas ascii, 
# décompose les caractères accentués (é -> e+)
# strip() + lower()
def normalize_text(txt: str) -> str:
    if not isinstance(txt, str):
        return ""
    # minuscules + sans accents + espaces compactés
    t = unicodedata.normalize("NFD", txt).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"\s+", " ", t.strip().lower())
    return t

# Cast en int sûr
def safe_int(val, default=None):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

# Indique si val est un float valide
def est_float_valide(val):
    return (isinstance(val, float) or isinstance(val, int) or  isinstance(val, np.float64) or isinstance(val, np.int64)) and not math.isnan(val)
    
def minutes(td: datetime.timedelta) -> int:
    return int(td.total_seconds() // 60)

def minutes_safe(x):
    if isinstance(x, datetime.timedelta):
        return int(x.total_seconds() // 60)
    try:
        # pandas Timedelta / numpy
        if hasattr(x, "total_seconds"):
            return int(x.total_seconds() // 60)
        return int(x)
    except Exception:
        return ""

# Renvoie val sous la forme "10h00" si datetime ou time, "" si None, str(val).strip() sinon
def heure_str(val):
    from datetime import datetime, time
    if isinstance(val, (datetime, time)):
        return val.strftime("%Hh%M")
    if pd.isna(val):
        return ""
    return str(val).strip()

# Renvoie un datetime basé sur BASE_DATE si h est datetime, time, str de la forme 10h00, 10:00 ou 10:00:00, None dans les autres cas
def heure_parse(h):
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

# Indique si une valeur à un format heure semblable à 10h00
def est_heure_valide(val):
    if pd.isna(val):
        return False
    try:
        return re.fullmatch(r"\d{1,2}h\d{2}", val.strip()) if val else False
    except Exception:
        return False
    
# Renvoie val sous la forme "1h00" si timedelta, "" si None, str(val).strip() sinon
def duree_str(val):
    from datetime import timedelta
    if pd.isna(val):
        return ""
    if isinstance(val, (timedelta, pd.Timedelta)):
        total_minutes = minutes(val)
        h = total_minutes // 60
        m = total_minutes % 60
        return f"{h}h{m:02d}"
    return str(val).strip()

# Renvoie un timedelta si h est timedelta, datetime, time, str de la forme 1h00, 1:00 ou 1:00:00, None dans les autres cas
def duree_parse(d):
    from datetime import datetime, time, timedelta

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

# Indique si une valeur à un format durée semblable à 1h00
def est_duree_valide(val):
    if pd.isna(val):
        return False
    try:
        return re.fullmatch(r"\d{1,2}h[0-5]\d", val.strip()) is not None if val else False
    except Exception:
        return False
    

def hhmm_to_min(s):
    """Accepte 'HHhMM', 'HH:MM', 'HH MM', 'HHMM', 'HhM', 'H:M'… -> minutes depuis minuit."""
    if s is None or (isinstance(s, float) and pd.isna(s)) or pd.isna(s):
        return None
    s = str(s).strip().replace(" ", "")
    s = s.replace("H", "h").replace("-", ":").replace("_", ":")
    m = (re.fullmatch(r"(\d{1,2})h(\d{1,2})", s)
         or re.fullmatch(r"(\d{1,2}):(\d{1,2})", s)
         or re.fullmatch(r"(\d{1,2})(\d{2})", s))  # 930 -> 9:30
    if not m:
        raise ValueError(f"Heure invalide: {s!r}. Attendu style '14h30'.")
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mm < 60):
        raise ValueError(f"Heure hors bornes: {s!r}")
    return h*60 + mm

# Calcule l'heure de fin à partir de l'heure de début et de la durée    
def calculer_fin(h, d, fin_actuelle=""):
    if isinstance(d, pd.Timedelta) and not pd.isna(h):
        total = h + d
        return f"{total.hour:02d}h{total.minute:02d}"
    else:
        return fin_actuelle if pd.notna(fin_actuelle) else ""

# Calcule l'heure de fin à partir d'une row
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

# Renvoie une date ISO (YYYY-MM-DD) pour une val datetime ou datetime.date, sinon renvoie chaine vide
def to_iso_date(val):
    if isinstance(val, datetime.datetime):
        return val.date().isoformat()
    elif isinstance(val, datetime.date):
        return val.isoformat()
    else:
        return ""

# Renvoie une bitmap encodée en format Base64 à partir d'un fichier
import base64
def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

# Ajoute les colonnes non présentes du df dans une row (hors colonnes de travail ["Debut_dt", "Duree_dt", "__uuid"])
def completer_ligne(ligne_partielle):
    colonnes_df_utiles = [col for col in st.session_state.df if col not in ["Debut_dt", "Duree_dt"]]
    colonnes_supplementaires = [col for col in ligne_partielle.keys() if col not in colonnes_df_utiles]
    colonnes_finales = colonnes_df_utiles + colonnes_supplementaires
    return {col: ligne_partielle.get(col, np.nan) for col in colonnes_finales}

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

# Renvoie le numero de ligne d'un df qui matche des valeurs
def trouver_ligne(df, valeurs):
    for i, row in df.iterrows():
        match = True
        for col, val in valeurs.items():
            if col in row and not pd.isna(row[col]):
                if row[col] != val:
                    match = False
                    break
        if match:
            return i, df.index.get_loc(i)
    return None, None

# Renvoie l'index de la ligne la plus proche dans un df_display d'aggrid
# Le df_display est supposé contenir dans la colonne __index l'index du df de base
def ligne_voisine_index(df_display, index_df):
    df_display_reset = df_display.reset_index(drop=True)
    if pd.notna(index_df):
        if len(df_display_reset) > 0:
            selected_row_pos = df_display_reset["__index"].eq(index_df).idxmax() 
            new_selected_row_pos = selected_row_pos + 1 if  selected_row_pos + 1 <= len(df_display) - 1 else max(selected_row_pos - 1, 0)
            return df_display_reset.iloc[new_selected_row_pos]["__index"] 
    else:
        return None

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

# Crée un hash des colonnes d'un df et de parametres.
def hash_df(df: pd.DataFrame, colonnes_a_garder: list=None, colonnes_a_enlever: list=None, params=None):
    
    if df is None:
        return None

    # Attention : convertir les colonnes de type datetime en string pour JSON
    if colonnes_a_garder is None:
        df_subset = df
    else:
        df_subset = df[colonnes_a_garder]
    
    if colonnes_a_enlever is not None:
        df_subset = df_subset.drop(colonnes_a_enlever, axis=1)

    df_subset = df_subset.astype(str)
    
    data = {
        "df": df_subset.to_dict("records"),
        "params": params
    }
    json_data = json.dumps(data, sort_keys=True)
    return hashlib.sha256(json_data.encode()).hexdigest()

# Normalise une val au format iso pour préparation hashage
def normalize(val):
    # Sérialisation stable pour le state (dates, timedeltas, NaN…)
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    if isinstance(val, datetime.timedelta):
        return int(val.total_seconds())
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    if pd.isna(val):
        return None
    return val

# Hashage d'une liste de variables d'état du st.session_state
def hash_state(keys: list) -> str:
    snapshot = {k: normalize(st.session_state.get(k)) for k in keys}
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()# Affiche un message d'erreur dans un dialog 

# Equivalent st.error dans une boite de dialogue modale
@st.dialog("Erreur")
def show_dialog_error(message):
    st.error(message)
    if st.button("Fermer"):
        st.rerun()

# A utiliser sur les colonnes de travail d'un df_display contenant des listes, series, 
# car les aggrid exigent des objets JSON serializable. Un JsCode faisant un JSON.parse()
# permet de dérialiser côté client pour exploitation de la colonne de travail 
# (voir la colonne __options_date cintenant les menus de jours de programmation possibles).
def safe_json_dump(val):
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)
    return "[]"

# Ajout d'un UUID à un df (utilisé pour le mode immutableData=True des AgGrid)
def add_persistent_uuid(df, idx=None):
    if idx is None:
        if "__uuid" not in df.columns:
            df["__uuid"] = [str(uuid.uuid4()) for _ in range(len(df))]
        else:
            df["__uuid"] = df["__uuid"].astype(str)
        return df
    else:
        df.at[idx, "__uuid"] = str(uuid.uuid4())

# Ajout d'une colonne Hyperlien au df
def add_hyperliens(df, lnk=None):
    if "Hyperlien" not in df.columns:
        if lnk is None or "Activite" not in df.columns:
            df["Hyperlien"] = pd.NA
        else:  
            df["Hyperlien"] = df["Activite"].map(lambda a: lnk.get(a, ""))
    return df

# Renvoie un hash sur les uuid pour faire une key qui ne change que si une ligne est supprimée / ajoutée
# Pas utilisé car l'aggrid sait se débrouiller de cette situation sans changer la key
def make_grid_key_suffix(df):
    ids_set = sorted(str(x) for x in df["__uuid"])   # tri pour neutraliser l’ordre
    sig = hashlib.sha1(json.dumps(ids_set).encode()).hexdigest()
    return sig

# renvoie l'uuid stocké dans la colonne __uuid d'un df à partir de l'index de ligne (idx)
def get_uuid(df, idx):
    if len(df) == 0:
        return None
    try:
        if idx in df.index:
            return str(df.loc[idx, "__uuid"])   # idx est un label d’index
        else:
            return None
    except KeyError:
        return None 

# renvoie l'index dans le df à partir de l'uuid stocké dans la colonne __uuid 
def get_index_from_uuid(df, uuid):
    """
    Retourne l'index du DataFrame dont la colonne '__uuid' vaut uuid_value.
    Renvoie None si aucun match.
    """
    if df is None or len(df) == 0 or "__uuid" not in df.columns:
        return None
    matches = df.index[df["__uuid"] == uuid]
    return matches[0] if len(matches) else None

# def ajouter_options_date(df_save: pd.DataFrame):
#     """
#     Copie la colonne __options_date issue des deux df_display
#     dans le DataFrame à sauvegarder en SQLite.
#     """
#     # 1️⃣  Récupère les deux df_display depuis session_state
#     df_prog  = st.session_state.activites_programmees_df_display
#     df_non_prog   = st.session_state.activites_non_programmees_df_display

#     # 2️⃣  Concatène uniquement les colonnes utiles
#     src = pd.concat([df_prog, df_non_prog], ignore_index=True)[["__uuid", "__options_date"]]

#     # 3️⃣  Aligne par __uuid
#     if "__uuid" not in df_save.columns:
#         raise ValueError("__uuid manquant dans le DataFrame à sauvegarder")

#     # left join pour récupérer les valeurs
#     df_save = df_save.merge(src, on="__uuid", how="left", suffixes=("", "_src"))

#     # si la colonne existait déjà, on écrase avec la version issue des df_display
#     if "__options_date_src" in df_save.columns:
#         df_save["__options_date"] = df_save["__options_date_src"]
#         df_save.drop(columns="__options_date_src", inplace=True)

#     return df_save

def ajouter_options_date(df_save: pd.DataFrame) -> pd.DataFrame:
    """
    Copie la colonne __options_date issue des deux df_display
    dans le DataFrame à sauvegarder en SQLite.

    - Si df_save est vide : renvoie une copie avec une colonne __options_date vide.
    - Aligne sur __uuid (converti en str pour éviter les conflits de type).
    """

    if df_save is None or not isinstance(df_save, pd.DataFrame):
        raise ValueError("df_save invalide")

    # --- Cas DataFrame vide : on renvoie une copie avec la colonne demandée ----
    if df_save.empty:
        out = df_save.copy()
        if "__options_date" not in out.columns:
            out["__options_date"] = pd.Series(dtype=object)
        return out

    if "__uuid" not in df_save.columns:
        raise ValueError("__uuid manquant dans le DataFrame à sauvegarder")

    # ---- Source : concat des df_display ----
    frames = []
    for name in ("activites_programmees_df_display", "activites_non_programmees_df_display"):
        d = st.session_state.get(name)
        if isinstance(d, pd.DataFrame) and not d.empty and "__uuid" in d.columns:
            tmp = d[["__uuid"]].copy()
            tmp["__options_date"] = d["__options_date"] if "__options_date" in d.columns else None
            frames.append(tmp)

    if not frames:
        # Rien à ajouter mais on s’assure que la colonne existe
        out = df_save.copy()
        if "__options_date" not in out.columns:
            out["__options_date"] = pd.Series(dtype=object)
        return out

    src = pd.concat(frames, ignore_index=True)
    src["__uuid"] = src["__uuid"].astype(str)

    # Dernière valeur par __uuid si doublons
    src = (src.dropna(subset=["__uuid"])
              .groupby("__uuid", as_index=False)
              .agg({"__options_date": "last"}))

    out = df_save.copy()
    out["__uuid"] = out["__uuid"].astype(str)

    merged = out.merge(src, on="__uuid", how="left", suffixes=("", "_src"))
    if "__options_date_src" in merged.columns:
        merged["__options_date"] = merged["__options_date_src"]
        merged.drop(columns="__options_date_src", inplace=True)

    return merged


def get_options_date_from_uuid(uuid: str) -> object | None:
    """
    Cherche __options_date pour un __uuid donné dans les deux df_display activites_programmees_df_display et activites_non_programmees_df_display.
    Retourne None si introuvable ou colonne absente.
    """
    for key in ("activites_programmees_df_display", "activites_non_programmees_df_display"):
        df = st.session_state.get(key)
        if isinstance(df, pd.DataFrame) and "__uuid" in df.columns:
            try:
                # sélection rapide
                sub = df.loc[df["__uuid"] == uuid]
                if not sub.empty and "__options_date" in sub.columns:
                    return sub["__options_date"].iloc[0]
            except Exception:
                pass
    return None

# renvoie le rowid correspondant à la sel_request sur une grille
def requested_rowid(grid_name):
    sel_request = st.session_state.get(f"{grid_name}_sel_request", None)
    if sel_request is not None:
        return sel_request["id"]
    return None

# renvoie le numéro de ligne correspondant à la sel_request sur une grille
def requested_rownum(grid_name):
    rownum = None  # par défaut
    sel_request = st.session_state.get(f"{grid_name}_sel_request", None)
    df_display = st.session_state.get(f"{grid_name}_df_display", None)
    if sel_request is not None and sel_request["id"] is not None and df_display is not None:
        matches = df_display[df_display["__index"].astype(str) == str(sel_request["id"])]
        if not matches.empty:
            rownum = df_display.index.get_loc(matches.index[0])
    return rownum

# Active le curseur "wait"
def curseur_attente():
    st.markdown(
        """
        <style>
        body {cursor: wait !important;}
        </style>
        """,
        unsafe_allow_html=True
    )

# Revenir au curseur normal
def curseur_normal():
    st.markdown(
        """
        <style>
        body {cursor: default !important;}
        </style>
        """,
        unsafe_allow_html=True
    )

# Renvoie les métadonnées du contexte dans un dico
def get_meta():
    return {
        "fn": st.session_state.fn,
        "fp": st.session_state.fp,
        "MARGE": minutes(st.session_state.MARGE),
        "DUREE_REPAS": minutes(st.session_state.DUREE_REPAS),
        "DUREE_CAFE": minutes(st.session_state.DUREE_CAFE),
        "itineraire_app": st.session_state.itineraire_app,
        "city_default": st.session_state.city_default,
        "traiter_pauses": str(st.session_state.traiter_pauses),
        "periode_a_programmer_debut": to_iso_date(st.session_state.periode_a_programmer_debut),
        "periode_a_programmer_fin": to_iso_date(st.session_state.periode_a_programmer_fin),
    }

# Injecte un CSS permettent de colorer les primary buttons selon les styles de PALETTE_COULEUR_PRIMARY_BUTTONS ("info", "error", etc.) 
def injecter_css_pour_primary_buttons(type_css):
    palette = PALETTE_COULEUR_PRIMARY_BUTTONS.get(type_css, PALETTE_COULEUR_PRIMARY_BUTTONS["info"])
    st.markdown(f"""
    <style>
    button[data-testid="stBaseButton-primary"]{{
    background-color: {palette["bg"]} !important;   /* fond info */
    color: #0b1220 !important;
    border: none !important;                /* supprime toutes les bordures */
    outline: none !important;
    box-shadow: none !important;
    text-align: left !important;
    width: 100% !important;
    padding: 0.9em 1em !important;
    border-radius: 0.5em !important;
    white-space: normal !important;
    line-height: 1.4 !important;
    cursor: pointer !important;
    }}
    </style>
    """, unsafe_allow_html=True)

# Affiche l'équivalent d'un st.info ou st.error avec un label
# Si key est fourni, un bouton clickable de type primary est utilisé 
# Ce bouton doit être stylé avec un CSS ciblant les boutons de type primary et injecté par l'appelant pour être coloré correctement
# Mais attention tous les boutons de type primary seront alors stylés de la même manière 
def st_info_avec_label(label, info_text, key=None, color="blue", afficher_label=True, label_separe=True):
    
    def st_info_error_ou_bouton(label, info_text, key, color):
        if key:
            return st.button(info_text, key=key, type="primary", use_container_width=True)
        else:
            if color.lower() == "red":
                st.error(info_text) 
            else:
                st.info(info_text
                               )
    if label_separe:
        if afficher_label:
            st.markdown(f"""
            <div style='
                font-size: 0.88rem;
                font-weight: normal;
                margin-bottom: 0.2rem;
            '>
                {label}
            </div>
            """, unsafe_allow_html=True)

        return st_info_error_ou_bouton(label, info_text, key, color)
    else:
        info_text = f"**{label}:** {info_text}" if afficher_label else info_text
        return st_info_error_ou_bouton(label, info_text, key, color)

