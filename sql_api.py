##############
# API SQLite #
##############

import sqlite3
import streamlit as st
import pandas as pd
import json
import numpy as np
from pathlib import Path
from contextlib import contextmanager
from pandas.api.types import is_scalar

from app_const import WITH_GOOGLE_SHEET, MARGE, DUREE_REPAS, DUREE_CAFE
from app_utils import ajouter_options_date, get_options_date_from_uuid, minutes, to_iso_date, get_meta
import sync_worker as wk

DATA_DIR = Path.home() / "app_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app_avignon.db"

# Colonnes persistées
CORE_COLS_DICO = {
    "Date": "INTEGER",
    "Debut": "TEXT",
    "Fin": "TEXT",
    "Duree": "TEXT",
    "Activite": "TEXT",
    "Lieu": "TEXT",
    "Relache": "TEXT",
    "Reserve": "TEXT",
    "Priorite": "INTEGER",
    "Debut_dt": "TEXT",
    "Duree_dt": "TEXT",
    "Hyperlien": "TEXT",
    "__options_date": "TEXT",
    "__uuid": "TEXT PRIMARY KEY",
}

CORE_COLS = list(CORE_COLS_DICO.keys())

# Colonnes non persistées
VOLATILE_COLS = {
    # ajoute ici toute autre colonne strictement "df_display"
}

META_COLS_DICO = {
    "id": "INTEGER PRIMARY KEY DEFAULT 1",
    "fn": "TEXT",
    "fp": "TEXT",
    "MARGE": "INTEGER",
    "DUREE_REPAS": "INTEGER",
    "DUREE_CAFE": "INTEGER",
    "itineraire_app": "TEXT",
    "city_default": "TEXT",
    "traiter_pauses": "TEXT",
    "periode_a_programmer_debut": "TEXT",
    "periode_a_programmer_fin": "TEXT",
}

DEFAULT_META = {k: None for k in META_COLS_DICO if k != "id"}

META_COLS = list(META_COLS_DICO.keys())

@contextmanager
def _conn_rw():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON;")
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    ddl_cols = ",\n  ".join(f"{col} {sqltype}" for col, sqltype in CORE_COLS_DICO.items())
    meta_cols = ",\n  ".join(f"{col} {sqltype}" for col, sqltype in META_COLS_DICO.items())
    ddl = f"""
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS df_principal (
      {ddl_cols},
      extras_json TEXT NOT NULL DEFAULT '{{}}'
    );
    
    CREATE TABLE IF NOT EXISTS meta (
      {meta_cols},
      extras_json TEXT NOT NULL DEFAULT '{{}}'
    );

    CREATE TABLE IF NOT EXISTS carnet (
      id TEXT PRIMARY KEY,
      Nom TEXT,
      Adresse TEXT,
      extras_json TEXT NOT NULL DEFAULT '{{}}'
    );
    """
    with _conn_rw() as con:
        con.executescript(ddl)

def db_exists() -> bool:
    return DB_PATH.exists() and DB_PATH.stat().st_size > 0

def _strip_display_cols(df: pd.DataFrame) -> pd.DataFrame:
    # enlève les colonnes strictement d'affichage
    keep = [c for c in df.columns if c not in VOLATILE_COLS]
    return df[keep].copy()

def _split_core_extras(row: dict):
    core = {k: row.get(k) for k in CORE_COLS if k in row}
    extras = {k: v for k, v in row.items() if (k not in CORE_COLS and k not in VOLATILE_COLS)}
    return core, extras

def _merge_core_extras(df_core: pd.DataFrame) -> pd.DataFrame:
    if "extras_json" not in df_core.columns or df_core.empty:
        return df_core.drop(columns=[c for c in ("extras_json",) if c in df_core.columns], errors="ignore")
    extras_df = pd.json_normalize(
        df_core["extras_json"].apply(lambda s: json.loads(s) if isinstance(s, str) and s.startswith("{") else (s or {}))
    )
    out = pd.concat(
        [df_core.drop(columns=["extras_json"]).reset_index(drop=True),
         (extras_df if not extras_df.empty else pd.DataFrame(index=df_core.index)).reset_index(drop=True)],
        axis=1
    )
    return out

def _to_sql(v):
    """Convertit toute valeur pandas/numpy 'missing' en None, et numpy scalars en Python natifs."""
    # manquants pandas / numpy
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    # numpy scalars -> python
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        # garde None déjà traité plus haut
        return float(v)
    # pandas Timestamp/Timedelta -> string
    if hasattr(v, "isoformat"):  # Timestamp / datetime
        try:
            return v.isoformat()
        except Exception:
            pass
    if str(type(v)).endswith("Timedelta'>"):
        return str(v)
    return v

def _clean_jsonable(x):
    """Nettoie récursivement pour que json.dumps fonctionne (NaN/NA->None, numpy->python)."""
    # scalaires
    if is_scalar(x) or x is None:
        return _to_sql(x)
    # dict
    if isinstance(x, dict):
        return {str(k): _clean_jsonable(v) for k, v in x.items()}
    # list/tuple/set
    if isinstance(x, (list, tuple, set)):
        return [_clean_jsonable(v) for v in x]
    # fallback objet
    return _to_sql(x)

def charger_contexte():
    with _conn_rw() as con:
        df_core = pd.read_sql_query("SELECT * FROM df_principal", con)
        df = _merge_core_extras(df_core)
        meta_df = pd.read_sql_query("SELECT * FROM meta", con)
        carnet = pd.read_sql_query("SELECT * FROM carnet", con)
    
    if meta_df.empty:
        # ✅ renvoie un dict avec toutes les clés mais valeurs None
        meta = DEFAULT_META.copy()
    else:
        # on prend la première ligne (ou adapte si plusieurs types)
        row_dict = meta_df.iloc[0].to_dict()
        meta = {k: row_dict.get(k, v) for k, v in DEFAULT_META.items()}

    ca = carnet.drop(columns=["id", "extras_json"])
    
    return df, meta, ca

def sauvegarder_contexte(enqueue=True):
    df = _strip_display_cols(st.session_state.df)
    df = ajouter_options_date(df)
    if "__uuid" not in df.columns:
        raise ValueError("sql_sauvegarder_contexte: __uuid manquant dans df")

    # upsert pour df
    cols_sql = [c for c in CORE_COLS if c != "__uuid"] + ["extras_json"]
    set_sql  = ",".join([f"{c}=excluded.{c}" for c in cols_sql])
    sql_df = (
        f"INSERT INTO df_principal (__uuid,{','.join(cols_sql)}) "
        f"VALUES ({','.join(['?']*(len(cols_sql)+1))}) "
        f"ON CONFLICT(__uuid) DO UPDATE SET {set_sql}"
    )

    # préparer df
    rows_params = []
    for _, row in df.iterrows():
        core, extras = _split_core_extras(row.to_dict())
        extras_json = json.dumps(_clean_jsonable(extras), ensure_ascii=False)
        vals = [core.get("__uuid")] \
             + [_to_sql(core.get(c)) for c in CORE_COLS if c != "__uuid"] \
             + [extras_json]
        rows_params.append(vals)

    # préparer meta (upsert id=1, on écrase aussi les colonnes manquantes avec NULL)
    meta = get_meta()
    sql_meta = meta_vals = None
    if meta is not None:
        placeholders = ",".join(["?"] * len(META_COLS))
        set_clause   = ",".join([f"{c}=excluded.{c}" for c in META_COLS])
        vals = []
        for col in META_COLS:
            v = meta.get(col)
            if col == "payload_json" and isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        sql_meta  = f"INSERT INTO meta (id,{','.join(META_COLS)}) VALUES (1,{placeholders}) " \
                    f"ON CONFLICT(id) DO UPDATE SET {set_clause}"
        meta_vals = vals

    # preparer ca
    ca = st.session_state.ca

    with _conn_rw() as con:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")

        # 🔥 reset total de df_principal puis réécriture
        con.execute("DELETE FROM df_principal")
        if rows_params:
            con.executemany(sql_df, rows_params)

        # meta : upsert complet (toutes colonnes dans META_COLS sont fixées, y compris à NULL)
        if sql_meta is not None:
            con.execute(sql_meta, meta_vals)

        # ca : reset puis insert
        if ca is not None:
            con.execute("DELETE FROM carnet")
            if len(ca):
                cols = ca.columns.tolist()
                con.executemany(
                    f"INSERT INTO carnet ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                    ca.where(pd.notna(ca), None).itertuples(index=False, name=None)
                )
    if enqueue:
        wk.enqueue_save_full(df, meta, ca)

def sauvegarder_df():
    df = _strip_display_cols(st.session_state.df)
    df = ajouter_options_date(df)
    if "__uuid" not in df.columns:
        raise ValueError("sql_sauvegarder_df: __uuid manquant dans df")

    # upsert pour df_principal
    cols_sql = [c for c in CORE_COLS if c != "__uuid"] + ["extras_json"]
    set_sql  = ",".join([f"{c}=excluded.{c}" for c in cols_sql])
    sql_df = (
        f"INSERT INTO df_principal (__uuid,{','.join(cols_sql)}) "
        f"VALUES ({','.join(['?']*(len(cols_sql)+1))}) "
        f"ON CONFLICT(__uuid) DO UPDATE SET {set_sql}"
    )

    # préparer les valeurs
    rows_params = []
    for _, row in df.iterrows():
        core, extras = _split_core_extras(row.to_dict())
        extras_json = json.dumps(_clean_jsonable(extras), ensure_ascii=False)
        vals = [core.get("__uuid")] \
             + [_to_sql(core.get(c)) for c in CORE_COLS if c != "__uuid"] \
             + [extras_json]
        rows_params.append(vals)

    with _conn_rw() as con:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")

        # 🔥 reset total de df_principal puis réécriture
        con.execute("DELETE FROM df_principal")
        if rows_params:
            con.executemany(sql_df, rows_params)

    wk.enqueue_save_df(df)

def sauvegarder_row(index_df):
    s = st.session_state.df.loc[index_df]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[0]
    row = s.to_dict()
    if "__uuid" not in row:
        raise ValueError("sql_sauvegarder_row: __uuid manquant")

    # 🔹 Injecter __options_date depuis les df_display avant split core/extras
    opt = get_options_date_from_uuid(row["__uuid"])
    if opt is not None:
        row["__options_date"] = opt  # ira dans extras_json

    core, extras = _split_core_extras(row)
    extras_json = json.dumps(_clean_jsonable(extras), ensure_ascii=False)

    cols_sql = [c for c in CORE_COLS if c != "__uuid"] + ["extras_json"]
    set_sql  = ",".join([f"{c}=excluded.{c}" for c in cols_sql])
    sql_one  = (
        f"INSERT INTO df_principal (__uuid,{','.join(cols_sql)}) "
        f"VALUES ({','.join(['?']*(len(cols_sql)+1))}) "
        f"ON CONFLICT(__uuid) DO UPDATE SET {set_sql}"
    )
    vals = [core.get("__uuid")] \
         + [_to_sql(core.get(c)) for c in CORE_COLS if c != "__uuid"] \
         + [extras_json]

    with _conn_rw() as con:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")
        con.execute(sql_one, vals)

    wk.enqueue_save_row(row)

ALLOWED_META_COLS = set(META_COLS) 

def sauvegarder_param(param: str):
    try:
        if param not in ALLOWED_META_COLS:
            raise ValueError(f"Paramètre inconnu : {param}")

        if param == "MARGE":
            value = minutes(st.session_state.MARGE)
        elif param == "DUREE_REPAS":
            value = minutes(st.session_state.DUREE_REPAS)
        elif param == "DUREE_CAFE":
            value = minutes(st.session_state.DUREE_CAFE)
        elif param == "itineraire_app":
            value = st.session_state.itineraire_app
        elif param == "city_default":
            value = st.session_state.city_default
        elif param == "traiter_pauses":
            value = str(st.session_state.traiter_pauses)
        elif param == "periode_a_programmer_debut":
            value = to_iso_date(st.session_state.periode_a_programmer_debut)
        elif param == "periode_a_programmer_fin":
            value = to_iso_date(st.session_state.periode_a_programmer_fin)
        else:
            # si colonne autorisée mais non gérée plus haut
            value = st.session_state.get(param)

        sql = f"""
            INSERT INTO meta (id, {param})
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET {param} = excluded.{param}
        """
        with _conn_rw() as con:
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("BEGIN IMMEDIATE")
            con.execute(sql, (value,))

        wk.enqueue_save_param(param, value)

    except Exception as e:
        print(f"Erreur sqlite_sauvegarder_param : {e}")

