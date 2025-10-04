#######################################################################
# Thread chargé de la sauvegarde dans la Google Sheet en temps masqué #
#######################################################################

import threading
import queue
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx, add_script_run_ctx
from gspread_dataframe import set_with_dataframe
import pandas as pd
import math

import tracer

# -----------------------------
#   Tâches de synchronisation
# -----------------------------
@dataclass
class GSTask:
    kind: str                      # 'save_full' | 'save_row' | 'save_param' | 'noop'
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


# --------------------------------------
#   Holder thread-safe du client GSheets
# --------------------------------------
_GSHEETS_REF: Optional[Any] = None
_GS_LOCK = threading.Lock()


def gs_set_client_for_worker(gs: Optional[Any]) -> None:
    """Définit (ou efface) le client Google Sheets accessible au worker."""
    global _GSHEETS_REF
    with _GS_LOCK:
        _GSHEETS_REF = gs


def _get_gsheets_client() -> Optional[Any]:
    with _GS_LOCK:
        return _GSHEETS_REF


def _gsheets_is_ready() -> bool:
    return _get_gsheets_client() is not None


# -------------------------------------------------
#   Fonctions de sauvegarde dans la Google Sheet
# -------------------------------------------------

def _ws(gs, name: str):
    # petit helper : récupère une worksheet par nom (ex: "data", "meta", "adrs")
    return gs[name]

def _to_cell(v):
    # vide si None ou NaN/NA
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return ""
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return v

def _gs_push_full(df, meta: dict, carnet):
    tracer.log("Début", types=["wk"])
    gs = _get_gsheets_client()
    if not gs or df is None:
        return
    
    # 1) DATA
    w = _ws(gs, "data")
    w.clear()
    set_with_dataframe(w, df)

    # 2) META : on écrit uniquement ce qui est fourni
    if meta is not None:
        wm = _ws(gs, "meta")
        # # mapping clé -> cellule
        # cell = {
        #     "fn": "A1",
        #     "fp": "A2",
        #     "MARGE": "A3",
        #     "DUREE_REPAS": "A4",
        #     "DUREE_CAFE": "A5",
        #     "itineraire_app": "A6",
        #     "city_default": "A7",
        #     "traiter_pauses": "A8",
        #     "periode_a_programmer_debut": "A9",
        #     "periode_a_programmer_fin": "A10",
        # }
        # for k, c in cell.items():
        #     if k in meta:
        #         wm.update_acell(c, _to_cell(meta[k]))
        meta = pd.DataFrame([{k: _to_cell(v) for k, v in meta.items()}])
        set_with_dataframe(wm, meta)

    # 3) CARNET D'ADRESSES
    if carnet is not None:
        wa = _ws(gs, "adrs")
        wa.clear()
        set_with_dataframe(wa, carnet)
    
    tracer.log("Fin", types=["wk"])

def _gs_push_df(df):
    """Écrit uniquement la feuille 'data' (efface puis réécrit tout le DF)."""
    tracer.log("Début", types=["wk"])
    gs = _get_gsheets_client()
    if not gs or df is None:
        return
    w = gs["data"]
    w.clear()
    set_with_dataframe(w, df)
    tracer.log("Fin", types=["wk"])

def _gs_push_row(row_df):
    """
    Met à jour UNE ligne dans la feuille 'data' en se basant sur la colonne '__uuid' du row_df.
    - Si l'uuid existe déjà -> update en place.
    - Sinon -> append en bas (sans réécrire l'entête).
    """
    tracer.log("Début", types=["wk"])
    gs = _get_gsheets_client()
    if not gs or row_df is None:
        return

    ws = gs["data"]
    headers = ws.row_values(1)

    # Si la feuille est vide -> on écrit header + ligne et on sort
    if not headers:
        set_with_dataframe(ws, row_df, include_column_header=True, resize=True)
        return

    # On s'assure d'avoir __uuid
    if "__uuid" not in row_df.columns:
        return
    uuid_str = str(row_df["__uuid"].iloc[0]).strip()
    if not uuid_str:
        return

    # Aligne l'ordre des colonnes du DF sur l'entête de la sheet
    row_df = row_df.reindex(columns=headers, fill_value="")

    # Trouve l'index de la colonne __uuid (1-based)
    try:
        uuid_col_idx = headers.index("__uuid") + 1
    except ValueError:
        # pas de colonne __uuid dans l'entête -> on ne sait pas cibler, on append
        last_row = len(ws.col_values(1))
        target_row = max(last_row + 1, 2)
        set_with_dataframe(ws, row_df, row=target_row, include_column_header=False, resize=False)
        return

    # Cherche la ligne où __uuid == uuid_str
    col_vals = ws.col_values(uuid_col_idx)  # inclut l'entête à la ligne 1
    row_idx = None
    for i, v in enumerate(col_vals[1:], start=2):  # lignes 2..n
        if str(v) == uuid_str:
            row_idx = i
            break

    if row_idx is None:
        # append en bas
        last_row = len(ws.col_values(1))
        target_row = max(last_row + 1, 2)
        set_with_dataframe(ws, row_df, row=target_row, include_column_header=False, resize=False)
    else:
        # update en place
        set_with_dataframe(ws, row_df, row=row_idx, include_column_header=False, resize=False)
    
    tracer.log("Fin", types=["wk"])

def _gs_push_param(key: str, value):
    tracer.log("Début", types=["wk"])
    gs = _get_gsheets_client()
    if not gs or not key:
        return
    wm = _ws(gs, "meta")
    cell = {
        "fn": "A2",
        "fp": "B2",
        "MARGE": "C2",
        "DUREE_REPAS": "D2",
        "DUREE_CAFE": "E2",
        "itineraire_app": "F2",
        "city_default": "G2",
        "traiter_pauses": "H2",
        "periode_a_programmer_debut": "I2",
        "periode_a_programmer_fin": "J2",
    }.get(key)
    if cell:
        wm.update_acell(cell, _to_cell(value))
    tracer.log("Fin", types=["wk"])

def _gs_push_ca(ca):
    """Écrit uniquement la feuille 'adrs' (efface puis réécrit tout le DF)."""
    tracer.log("Début", types=["wk"])
    gs = _get_gsheets_client()
    if not gs or ca is None:
        return
    w = gs["adrs"]
    w.clear()
    set_with_dataframe(w, ca)
    tracer.log("Fin", types=["wk"])

# ------------------------------------
#   Worker: boucle de synchronisation
# ------------------------------------
def _extract_uuid(obj):
    """Renvoie l'UUID (str) depuis un DataFrame(1 ligne) / Series / dict, sinon None."""
    import pandas as pd
    if obj is None:
        return None
    # DataFrame 1-ligne
    if isinstance(obj, pd.DataFrame):
        if "__uuid" in obj.columns and len(obj) > 0:
            v = obj["__uuid"].iloc[0]
            return None if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)
        return None
    # Series
    if isinstance(obj, pd.Series):
        v = obj.get("__uuid")
        if v is None:
            return None
        try:
            # v peut être un scalaire numpy/pandas
            import pandas as pd
            if pd.isna(v):
                return None
        except Exception:
            pass
        return str(v)
    # dict
    if isinstance(obj, dict):
        v = obj.get("__uuid")
        return None if (v is None or v == "") else str(v)
    return None

def _gsync_worker(q: "queue.Queue[GSTask]", stop_evt: threading.Event, status: Dict[str, Any]) -> None:
    """
    Boucle du worker (thread secondaire).
    - Ne lit PAS st.session_state.
    - Met à jour 'status' (dict) passé par référence.
    - S'auto-arrête si inactif trop longtemps (MAX_IDLE) et qu'il n'y a plus rien à faire.
    """
    if not isinstance(stop_evt, threading.Event):
        raise ValueError("stop_evt must be a threading.Event")

    MAX_IDLE = 600            # ⏳ auto-stop si aucun heartbeat UI depuis 10 min ET plus de travail
    COALESCE_WINDOW = 1.0     # regroupe les bursts pendant 1s
    BACKOFFS = [1, 2, 4, 8, 16]
    IDLE_SLICE = 0.05

    # init statut
    status.setdefault("stats", {"ok": 0, "err": 0, "skipped": 0})
    status.setdefault("last_seen", time.time())   # si l'UI ne l'a pas encore mis
    status["alive"] = True
    status["inflight"] = False
    status["last_err"] = None

    buf: List[GSTask] = []
    last_in = time.time()

    def run_task(task: GSTask) -> None:
        if not _gsheets_is_ready():
            status["stats"]["skipped"] += 1
            return

        status["inflight"] = True
        status["last_run"] = time.time()
        tries = 0
        last_err: Optional[Exception] = None

        while tries <= len(BACKOFFS):
            try:
                if task.kind == "save_full":
                    _gs_push_full(task.payload.get("df"),
                                  task.payload.get("meta") or {},
                                  task.payload.get("carnet"))
                elif task.kind == "save_df":
                    _gs_push_df(task.payload.get("df"))
                elif task.kind == "save_row":
                    _gs_push_row(task.payload.get("row_df"))
                elif task.kind == "save_param":
                    _gs_push_param(task.payload.get("key"), task.payload.get("value"))
                elif task.kind == "save_ca":
                    _gs_push_ca(task.payload.get("ca"))
                # 'noop' : rien à faire ici

                status["last_ok"] = time.time()
                status["last_err"] = None
                status["inflight"] = False
                status["stats"]["ok"] += 1
                return

            except Exception as e:
                last_err = e
                status["stats"]["err"] += 1
                if tries == len(BACKOFFS):
                    break
                time.sleep(BACKOFFS[tries])
                tries += 1

        status["inflight"] = False
        status["last_err"] = f"{type(last_err).__name__}: {last_err}"

    def flush_buffer() -> None:
        nonlocal buf
        if not buf:
            return

        # Coalescer
        last_full: Optional[GSTask] = None
        last_df:   Optional[GSTask] = None
        last_ca:   Optional[GSTask] = None
        rows_by_uuid: Dict[str, GSTask] = {}
        params_by_key: Dict[str, GSTask] = {}

        for t in buf:
            if t.kind == "save_row":
                uid = t.payload.get("__uuid") or _extract_uuid(t.payload.get("row_df")) or _extract_uuid(t.payload.get("row"))
                if uid:
                    rows_by_uuid[str(uid)] = t

            elif t.kind == "save_param":
                k = str(t.payload.get("key") or "")
                if k:
                    params_by_key[k] = t

            elif t.kind == "save_full":
                # on garde le plus récent
                if (last_full is None) or (t.ts >= last_full.ts):
                    last_full = t

            elif t.kind == "save_df":
                if (last_df is None) or (t.ts >= last_df.ts):
                    last_df = t

            elif t.kind == "save_ca":
                if (last_ca is None) or (t.ts >= last_ca.ts):
                    last_ca = t

            elif t.kind == "noop":
                # simple ping: marque OK, mais ne génère pas d'exécution
                status["last_ok"] = time.time()
                status["last_err"] = None
                status["stats"]["ok"] += 1

        # Ordre d'exécution :
        # 1) s'il y a un save_full -> on exécute UNIQUEMENT lui (il écrase tout)
        # 2) sinon s'il y a un save_df -> on exécute save_df puis params puis ca
        # 3) sinon -> rows puis params (derniers par clé) puis ca
        tasks_to_run: List[GSTask] = []
        if last_full:
            tasks_to_run.append(last_full)
        elif last_df:
            tasks_to_run.append(last_df)
            tasks_to_run.extend(params_by_key.values())
            if last_ca:
                        tasks_to_run.append(last_ca)            
        else:
            tasks_to_run.extend(rows_by_uuid.values())
            tasks_to_run.extend(params_by_key.values())
            if last_ca:
                        tasks_to_run.append(last_ca)

        buf = []
        for t in tasks_to_run:
            if stop_evt.is_set():
                return
            run_task(t)

    try:
        # Boucle principale
        while not stop_evt.is_set():
            try:
                task = q.get(timeout=0.2)
                buf.append(task)
                last_in = time.time()
            except queue.Empty:
                pass

            # Coalescence après une petite période d'inactivité sur la file
            if buf and (time.time() - last_in) > COALESCE_WINDOW:
                flush_buffer()

            # Statuts pour l'UI
            try:
                status["pending"] = q.qsize() + len(buf)
            except Exception:
                status["pending"] = len(buf)

            # 🔌 Auto-stop si plus de travail ET UI silencieuse depuis trop longtemps
            idle_for = time.time() - (status.get("last_seen") or 0)
            if status.get("pending", 0) == 0 and idle_for > MAX_IDLE:
                break

            time.sleep(IDLE_SLICE)

    except Exception as e:
        status["last_err"] = f"worker: {type(e).__name__}: {e}"
        time.sleep(0.5)

    # Arrêt -> flush final puis marquer 'alive' à False
    try:
        flush_buffer()
    except Exception:
        pass
    finally:
        status["alive"] = False
        status["inflight"] = False

# -----------------------------------------
#   Démarrage / arrêt du worker (1 par app)
# -----------------------------------------
def _ensure_sync_state() -> None:
    """Crée les clés côté session_state (pour UI/contrôle), sans les utiliser dans le thread."""
    st.session_state.setdefault("gsync_queue", None)
    st.session_state.setdefault("gsync_thread", None)
    st.session_state.setdefault("gsync_stop", None)
    st.session_state.setdefault("gsync_status", {
        "alive": False,
        "last_run": None,
        "last_ok": None,
        "last_err": None,
        "inflight": False,
        "pending": 0,
        "stats": {"ok": 0, "err": 0, "skipped": 0},
    })


# @st.cache_resource
# def start_worker() -> bool:
#     """
#     Démarre le worker de sync (une seule fois par conteneur).
#     Retourne True si le worker est actif (démarré ou déjà démarré).
#     """
#     _ensure_sync_state()

#     th = st.session_state.gsync_thread
#     if th and th.is_alive():
#         return True  # déjà démarré

#     # Construit les artefacts et passe-les au thread
#     q: "queue.Queue[GSTask]" = queue.Queue()
#     stop_evt = threading.Event()
#     status = {
#         "alive": True,
#         "last_run": None,
#         "last_ok": None,
#         "last_err": None,
#         "inflight": False,
#         "pending": 0,
#         "stats": {"ok": 0, "err": 0, "skipped": 0},
#     }

#     t = threading.Thread(
#         target=_gsync_worker,
#         args=(q, stop_evt, status),
#         name="gsync-worker",
#         daemon=True,
#     )
#     ctx = get_script_run_ctx()
#     if ctx:
#         add_script_run_ctx(t)   # supprime l’avertissement missing ScriptRunContext!
#     t.start()

#     # Références pour le thread principal (UI / contrôles / enqueue)
#     st.session_state.gsync_queue = q
#     st.session_state.gsync_stop = stop_evt
#     st.session_state.gsync_status = status
#     st.session_state.gsync_thread = t

#     return True

MAX_IDLE = 600  # 10 min

def _start_new_worker():
    """Crée un worker neuf et l'enregistre dans st.session_state."""

    # artefacts partagés
    q        = queue.Queue()
    stop_evt = threading.Event()
    status   = {
        "alive": True,
        "last_run": None,
        "last_ok": None,
        "last_err": None,
        "inflight": False,
        "pending": 0,
        "last_seen": time.time(),
        "stats": {"ok": 0, "err": 0, "skipped": 0},
    }

    # thread
    t = threading.Thread(
        target=_gsync_worker,            # <-- ta boucle actuelle (version patchée idle/flush)
        args=(q, stop_evt, status),
        name="gsync-worker",
        daemon=True,
    )
    # évite le warning "missing ScriptRunContext!"
    ctx = get_script_run_ctx()
    if ctx:
        add_script_run_ctx(t)

    t.start()

    # expose à l'UI
    st.session_state.gsync_queue  = q
    st.session_state.gsync_stop   = stop_evt
    st.session_state.gsync_status = status
    st.session_state.gsync_thread = t

    tracer.log("Started !", types=["wk"])

def ensure_worker_alive():
    """
    À appeler AU DÉBUT DE CHAQUE RUN (et avant tout enqueue).
    Relance si le worker est absent/mort/stoppé ; sinon ping heartbeat.
    """
    th     = st.session_state.get("gsync_thread")
    status = st.session_state.get("gsync_status")
    stop   = st.session_state.get("gsync_stop")

    dead = (
        th is None or not th.is_alive()
        or status is None or not status.get("alive", False)
        or (stop is not None and stop.is_set())
    )

    if dead:
        _start_new_worker()
    else:
        # heartbeat UI : la boucle worker lit last_seen pour l'auto-stop idle
        try:
            st.session_state.gsync_status["last_seen"] = time.time()
        except Exception:
            # si jamais status a été perdu/écrasé
            _start_new_worker()

def stop_worker() -> None:
    """Arrêt propre du worker (facultatif : bouton “Arrêter la sync”)."""
    stop_evt: Optional[threading.Event] = st.session_state.get("gsync_stop")
    th: Optional[threading.Thread] = st.session_state.get("gsync_thread")
    if stop_evt:
        stop_evt.set()
    if th:
        try:
            th.join(timeout=2)
        except Exception:
            pass


# --------------------------------------------
#   Helpers d’enqueue (appelés depuis l’UI)
# --------------------------------------------
def get_sync_status() -> Dict[str, Any]:
    """Retourne le dict de statut (pour affichage dans la sidebar, etc.)."""
    _ensure_sync_state()
    return st.session_state.gsync_status


def enqueue_save_full(df, meta: dict, carnet):
    ensure_worker_alive()
    q = st.session_state.get("gsync_queue")
    if q:
        # tracer.log("->", types=["wk"])
        q.put(GSTask(kind="save_full", payload={"df": df, "meta": meta, "carnet": carnet}))

def enqueue_save_df(df):
    ensure_worker_alive()
    q = st.session_state.get("gsync_queue")
    if q:
        # tracer.log("->", types=["wk"])
        q.put(GSTask(kind="save_df", payload={"df": df}))

def enqueue_save_row(row):
    import pandas as pd
    if isinstance(row, pd.Series):
        row_df = row.to_frame().T
    elif isinstance(row, dict):
        row_df = pd.DataFrame([row])
    else:
        row_df = row
    # garde-fou : il faut __uuid non vide
    uid = _extract_uuid(row_df)
    if not uid:
        return
    ensure_worker_alive()
    q = st.session_state.get("gsync_queue")
    if q:
        # tracer.log("->", types=["wk"])
        q.put(GSTask(kind="save_row", payload={"row_df": row_df}))
        
def enqueue_save_param(key: str, value):
    ensure_worker_alive()
    q = st.session_state.get("gsync_queue")
    if q:
        # tracer.log("->", types=["wk"])
        q.put(GSTask(kind="save_param", payload={"key": key, "value": value}))

def enqueue_save_ca(ca):
    ensure_worker_alive()
    q = st.session_state.get("gsync_queue")
    if q:
        # tracer.log("->", types=["wk"])
        q.put(GSTask(kind="save_ca", payload={"ca": ca}))

def enqueue_noop():
    ensure_worker_alive()
    q = st.session_state.get("gsync_queue")
    if not q:
        return False
    # tracer.log("->", types=["wk"])
    q.put(GSTask(kind="noop"))
    return True
