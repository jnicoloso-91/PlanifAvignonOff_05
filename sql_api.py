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
from app_utils import ajouter_options_date, get_options_date_from_uuid, minutes, to_iso_date, get_meta, get_user_id
import sync_worker as wk
import tracer

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
    "__uuid": "TEXT NOT NULL",
}

CORE_COLS = list(CORE_COLS_DICO.keys())

# Colonnes non persistées
VOLATILE_COLS = {
    # ajouter ici toute autre colonne strictement "df_display"
}

META_COLS_DICO = {
    "id": "INTEGER NOT NULL DEFAULT 1",
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

META_COLS = [c for c in META_COLS_DICO.keys() if c != "id"]  

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

def _has_composite_pk_expected(con) -> bool:
    # Vérifie que df_principal a bien (user_id, __uuid) et meta/carnet idem
    def pk_cols(table):
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall() if r[5] == 1]
    try:
        return (
            pk_cols("df_principal") == ["user_id", "__uuid"] and
            pk_cols("meta")         == ["user_id", "id"]      and
            pk_cols("carnet")       == ["user_id", "id"]
        )
    except Exception:
        return False

def ensure_schema():
    with _conn_rw() as con:
        ok = False
        try:
            ok = _has_composite_pk_expected(con)
        except Exception:
            ok = False
        if not ok:
            # Schéma ancien ou manquant → on repart propre
            con.executescript("""
                DROP TABLE IF EXISTS df_principal;
                DROP TABLE IF EXISTS meta;
                DROP TABLE IF EXISTS carnet;
            """)
    # crée les tables (ta nouvelle init_db)
    init_db()

def init_db():

    # DEBUG ONLY - A utiliser pour faire un reset DB
    # tracer.log("Début drop tables", types=["main"])
    # with sqlite3.connect(DB_PATH) as con:
    #     cur = con.cursor()
    #     # supprime les tables si elles existent
    #     cur.executescript("""
    #         DROP TABLE IF EXISTS df_principal;
    #         DROP TABLE IF EXISTS meta;
    #         DROP TABLE IF EXISTS carnet;
    #     """)
    #     con.commit()
    # tracer.log("Fin drop tables", types=["main"])
    # DEBUG ONLY - A utiliser pour faire un reset DB

    ddl_cols  = ",\n  ".join(f"{col} {sqltype}" for col, sqltype in CORE_COLS_DICO.items())
    meta_cols = ",\n  ".join(f"{col} {sqltype}" for col, sqltype in META_COLS_DICO.items())

    ddl = f"""
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS df_principal (
      {ddl_cols},
      extras_json TEXT NOT NULL DEFAULT '{{}}',
      user_id     TEXT NOT NULL,
      PRIMARY KEY (user_id, __uuid)
    );

    CREATE TABLE IF NOT EXISTS meta (
      {meta_cols},
      extras_json TEXT NOT NULL DEFAULT '{{}}',
      user_id     TEXT NOT NULL,
      PRIMARY KEY (user_id, id)
    );

    CREATE TABLE IF NOT EXISTS carnet (
      Nom         TEXT,
      Adresse     TEXT,
      Tel         TEXT,
      Web         TEXT,
      __uuid      TEXT,
      extras_json TEXT NOT NULL DEFAULT '{{}}',
      user_id     TEXT NOT NULL,
      PRIMARY KEY (user_id, __uuid)
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
    user_id = get_user_id()

    with _conn_rw() as con:
        # df_principal
        df_core = pd.read_sql(
            "SELECT * FROM df_principal WHERE user_id = :uid",
            con, params={"uid": user_id}
        )
        df = _merge_core_extras(df_core).drop(columns=["user_id"], errors="ignore")

        # meta : on ne garde qu'un enregistrement (le plus récent)
        meta_df = pd.read_sql(
            "SELECT * FROM meta WHERE user_id = :uid ORDER BY rowid DESC LIMIT 1",
            con, params={"uid": user_id}
        )

        # carnet
        carnet_df = pd.read_sql(
            "SELECT * FROM carnet WHERE user_id = :uid",
            con, params={"uid": user_id}
        )
        ca = carnet_df.drop(columns=["extras_json", "user_id"], errors="ignore")

    # META : défaut si vide, sinon on mappe sur DEFAULT_META
    if meta_df.empty:
        meta = DEFAULT_META.copy()
    else:
        row = meta_df.iloc[0].to_dict()
        meta = {k: row.get(k, v) for k, v in DEFAULT_META.items()}

    return df, meta, ca

def sauvegarder_contexte(enqueue=True):
    user_id = get_user_id()

    # --- prépa df_principal (inchangé côté métier) ---
    df = _strip_display_cols(st.session_state.df)
    df = ajouter_options_date(df)
    if "__uuid" not in df.columns:
        raise ValueError("sql_sauvegarder_contexte: __uuid manquant dans df")

    # upsert pour df (⚠️ conflit sur (user_id,__uuid))
    cols_sql_no_keys = [c for c in CORE_COLS if c != "__uuid"] + ["extras_json"]  # colonnes à mettre à jour
    set_sql = ",".join([f"{c}=excluded.{c}" for c in cols_sql_no_keys])

    sql_df = (
        f"INSERT INTO df_principal (user_id,__uuid,{','.join([c for c in CORE_COLS if c != '__uuid'])},extras_json) "
        f"VALUES ({','.join(['?']*(len(CORE_COLS) + 2))}) "  # +2 = user_id + extras_json
        f"ON CONFLICT(user_id,__uuid) DO UPDATE SET {set_sql}"
    )

    # préparer df → lignes (user_id, __uuid, CORE_COLS sauf __uuid..., extras_json)
    rows_params = []
    for _, row in df.iterrows():
        core, extras = _split_core_extras(row.to_dict())
        extras_json = json.dumps(_clean_jsonable(extras), ensure_ascii=False)
        vals = [user_id, core.get("__uuid")] \
             + [_to_sql(core.get(c)) for c in CORE_COLS if c != "__uuid"] \
             + [extras_json]
        rows_params.append(vals)

    # --- prépa meta (upsert "snapshot" id=1 par user) ---
    meta = get_meta()
    sql_meta = cols = ph = vals = None
    if meta is not None:
        cols = ",".join(["user_id", "id"] + META_COLS)
        ph   = ",".join(["?"] * (2 + len(META_COLS)))
        vals = [user_id, 1] + [meta.get(c) for c in META_COLS]

    # --- prépa carnet (DataFrame en session) ---
    carnet = st.session_state.ca
    # on s'assure que user_id est dans les colonnes à l'insert
    carnet_to_insert = None
    if isinstance(carnet, pd.DataFrame) and not carnet.empty:
        carnet_to_insert = carnet.copy()
        if "user_id" not in carnet_to_insert.columns:
            carnet_to_insert["user_id"] = user_id
        else:
            carnet_to_insert["user_id"] = user_id  # on force le bon scope

    with _conn_rw() as con:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")

        # 🔥 df_principal : reset du scope utilisateur puis réécriture
        con.execute("DELETE FROM df_principal WHERE user_id = ?", (user_id,))
        if rows_params:
            con.executemany(sql_df, rows_params)

        # 🔥 meta : reset du scope utilisateur puis upsert (id=1 par user)
        con.execute("DELETE FROM meta WHERE user_id = ?", (user_id,))
        if meta is not None:
            con.execute(f"INSERT INTO meta ({cols}) VALUES ({ph})", vals)

        # 🔥 carnet d'adresses : reset du scope utilisateur puis insert
        con.execute("DELETE FROM carnet WHERE user_id = ?", (user_id,))

        if isinstance(carnet, pd.DataFrame) and not carnet.empty:
            ca = carnet.copy()
            ca["user_id"] = user_id
            if "extras_json" not in ca.columns:
                ca["extras_json"] = "{}"

            # __uuid est requis par le schéma (NOT NULL + PK)
            if "__uuid" not in ca.columns:
                raise ValueError("carnet.__uuid manquant")

            carnet_sql = ca.where(pd.notna(ca), None)
            cols = carnet_sql.columns.tolist()
            con.executemany(
                f"INSERT INTO carnet ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                carnet_sql.itertuples(index=False, name=None)
            )

    if enqueue:
        wk.enqueue_save_full(df, meta, carnet)

def sauvegarder_df():
    user_id = get_user_id()  # ✅ Identifiant utilisateur courant

    df = _strip_display_cols(st.session_state.df)
    df = ajouter_options_date(df)

    if "__uuid" not in df.columns:
        raise ValueError("sql_sauvegarder_df: __uuid manquant dans df")

    # --- upsert pour df_principal ---
    cols_sql = [c for c in CORE_COLS if c != "__uuid"] + ["extras_json", "user_id"]
    set_sql  = ",".join([f"{c}=excluded.{c}" for c in cols_sql if c != "user_id"])  # ne jamais changer user_id
    sql_df = (
        f"INSERT INTO df_principal (__uuid,{','.join(cols_sql)}) "
        f"VALUES ({','.join(['?']*(len(cols_sql)+1))}) "
        f"ON CONFLICT(user_id,__uuid) DO UPDATE SET {set_sql}"
    )

    # --- préparer les valeurs ---
    rows_params = []
    for _, row in df.iterrows():
        core, extras = _split_core_extras(row.to_dict())
        extras_json = json.dumps(_clean_jsonable(extras), ensure_ascii=False)
        vals = [core.get("__uuid")] \
             + [_to_sql(core.get(c)) for c in CORE_COLS if c != "__uuid"] \
             + [extras_json, user_id]
        rows_params.append(vals)

    # --- exécution ---
    with _conn_rw() as con:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")

        # 🔥 suppression des lignes de cet utilisateur uniquement
        con.execute("DELETE FROM df_principal WHERE user_id = ?", (user_id,))

        if rows_params:
            con.executemany(sql_df, rows_params)

    # --- enqueue async ---
    wk.enqueue_save_df(df)

def sauvegarder_row(index_df):
    user_id = get_user_id()  # ✅ scope utilisateur

    s = st.session_state.df.loc[index_df]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[0]
    row = s.to_dict()
    if "__uuid" not in row:
        raise ValueError("sql_sauvegarder_row: __uuid manquant")

    # 🔹 injecter __options_date (va dans extras_json)
    opt = get_options_date_from_uuid(row["__uuid"])
    if opt is not None:
        row["__options_date"] = opt

    core, extras = _split_core_extras(row)
    extras_json = json.dumps(_clean_jsonable(extras), ensure_ascii=False)

    # upsert (clé: user_id + __uuid)
    cols_sql = [c for c in CORE_COLS if c != "__uuid"] + ["extras_json", "user_id"]
    set_sql  = ",".join([f"{c}=excluded.{c}" for c in cols_sql if c != "user_id"])  # ne pas modifier user_id

    sql_one = (
        f"INSERT INTO df_principal (__uuid,{','.join(cols_sql)}) "
        f"VALUES ({','.join(['?']*(len(cols_sql)+1))}) "
        f"ON CONFLICT(user_id,__uuid) DO UPDATE SET {set_sql}"
    )

    vals = [core.get("__uuid")] \
         + [_to_sql(core.get(c)) for c in CORE_COLS if c != "__uuid"] \
         + [extras_json, user_id]

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

        # --- valeur à persister ---
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
            value = st.session_state.get(param)

        user_id = get_user_id()  # ✅ scope utilisateur

        # --- UPSERT par (user_id, id=1) ---
        sql = f"""
            INSERT INTO meta (user_id, id, {param})
            VALUES (?, 1, ?)
            ON CONFLICT(user_id, id) DO UPDATE SET {param} = excluded.{param}
        """
        with _conn_rw() as con:
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("BEGIN IMMEDIATE")
            con.execute(sql, (user_id, value))

        wk.enqueue_save_param(param, value)

    except Exception as e:
        print(f"Erreur sqlite_sauvegarder_param : {e}")

def sauvegarder_ca():
    user_id = get_user_id()

    carnet = _strip_display_cols(st.session_state.get("ca"))
    if not isinstance(carnet, pd.DataFrame):
        wk.enqueue_save_ca(pd.DataFrame())
        return 0

    if "__uuid" not in carnet.columns:
        raise ValueError("carnet.__uuid manquant")

    ca = carnet.copy()
    ca["user_id"] = user_id
    if "extras_json" not in ca.columns:
        ca["extras_json"] = "{}"

    carnet_sql = ca.where(pd.notna(ca), None)
    cols = carnet_sql.columns.tolist()

    with _conn_rw() as con:
        try: con.execute("PRAGMA busy_timeout=30000")
        except: pass
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM carnet WHERE user_id = ?", (user_id,))
        if not carnet_sql.empty:
            con.executemany(
                f"INSERT INTO carnet ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                carnet_sql.itertuples(index=False, name=None)
            )

    wk.enqueue_save_ca(carnet_sql.copy())
