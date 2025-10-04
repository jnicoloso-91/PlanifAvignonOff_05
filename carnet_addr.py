#####################
# Carnet d'adresses #
#####################

import streamlit as st
import pandas as pd
from difflib import SequenceMatcher
import re
from urllib.parse import quote_plus

from app_utils import normalize_text, add_persistent_uuid
from app_const import COLONNES_ATTENDUES_CARNET_ADRESSES

@st.cache_data(show_spinner=False)
def prepare_carnet(carnet_df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute une colonne normalisée (une seule fois, puis cache)."""
    df = carnet_df.copy()
    if "Nom" in df.columns:
        df["_Nom_norm"] = df["Nom"].astype(str).map(normalize_text)
    else:
        df["_Nom_norm"] = ""
    return df

def resolve_address_fast(lieu: str, carnet_df: pd.DataFrame | None, city_default="Avignon"):
    """
    1) Cherche dans le carnet par égalité puis 'contains' (normalisé, sans accents).
    2) Si rien -> renvoie 'lieu, <city>'.
    Retourne (addr_humaine, addr_enc).
    """
    lieu = lieu if isinstance(lieu, str) else ""
    lieu = lieu.strip()
    key = normalize_text(lieu)

    addr = ""
    if carnet_df is not None and {"Nom","Adresse"}.issubset(carnet_df.columns):
        df = prepare_carnet(carnet_df)

        # match exact (rapide)
        hit = df.loc[df["_Nom_norm"].eq(key)]
        if hit.empty and key:
            # contains (vectorisé)
            hit = df.loc[df["_Nom_norm"].str.contains(re.escape(key), na=False)]

        if not hit.empty:
            val = hit.iloc[0]["Adresse"]
            if pd.notna(val):
                addr = str(val).strip()

    if not addr:
        # fallback toujours: lieu + ville
        addr = f"{lieu}, {city_default}" if lieu else city_default

    return addr, quote_plus(addr)

def resolve_address(lieu: str, carnet_df: pd.DataFrame | None = None, default_city="Avignon"):
    """
    Retourne (addr_humaine, addr_enc) en essayant d'abord le carnet (Nom -> Adresse)
    avec recherche accent-insensible, partielle, et fuzzy.
    Si pas trouvé, ajoute toujours ", <city>" au lieu.
    """
    def _best_match_row(carnet_df: pd.DataFrame, key_norm: str):
        """
        Retourne l'index de la meilleure ligne matchée dans carnet_df
        selon l'ordre: égalité stricte > contains > fuzzy.
        Renvoie None si aucun candidat crédible.
        """
        if carnet_df.empty:
            return None

        # Prépare colonne normalisée
        if "_Nom_norm" not in carnet_df.columns:
            carnet_df["_Nom_norm"] = carnet_df["Nom"].astype(str).apply(normalize_text)

        noms = carnet_df["_Nom_norm"]

        # 1) égalité stricte
        exact = carnet_df.index[noms == key_norm]
        if len(exact):
            return exact[0]

        # 2) contains (key dans nom)
        contains_idx = [i for i, n in noms.items() if key_norm in n]
        if contains_idx:
            # si plusieurs, prend le plus proche via ratio fuzzy
            if len(contains_idx) == 1:
                return contains_idx[0]
            best = max(contains_idx, key=lambda i: SequenceMatcher(None, key_norm, noms[i]).ratio())
            return best

        # 3) fuzzy global (utile si fautes de frappe)
        # on prend les candidats avec ratio >= 0.75 et choisit le meilleur
        scored = [(i, SequenceMatcher(None, key_norm, n).ratio()) for i, n in noms.items()]
        scored = [x for x in scored if x[1] >= 0.75]
        if scored:
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[0][0]

        return None

    lieu = lieu if isinstance(lieu, str) else ""
    saisie = lieu.strip()
    key = normalize_text(saisie)

    addr = ""

    if carnet_df is not None and {"Nom", "Adresse"}.issubset(carnet_df.columns):
        try:
            row_idx = _best_match_row(carnet_df, key)
            if row_idx is not None:
                val = carnet_df.loc[row_idx, "Adresse"]
                if pd.notna(val):
                    addr = str(val).strip()
        except Exception:
            pass  # pas de blocage si carnet mal formé

    # Fallback : toujours ajouter la ville si rien trouvé
    if not addr:
        if saisie:
            addr = f"{saisie}, {default_city}"
        else:
            addr = default_city

    addr_enc = quote_plus(addr) if addr else ""
    return addr, addr_enc

def nettoyer_ca(ca: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie le carnet d'adresses :
      - ajoute un UUID persistant
      - conserve uniquement les colonnes obligatoires
      - crée les colonnes manquantes si besoin
    """
    # Ajoute l'UUID si besoin
    ca = add_persistent_uuid(ca)

    cols_obligatoires = COLONNES_ATTENDUES_CARNET_ADRESSES + ["__uuid"]

    # Supprimer les colonnes non obligatoires
    cols_a_supprimer = [col for col in ca.columns if col not in cols_obligatoires]
    ca = ca.drop(columns=cols_a_supprimer, errors="ignore")

    # Ajouter les colonnes manquantes
    for col in COLONNES_ATTENDUES_CARNET_ADRESSES:
        if col not in ca.columns:
            ca[col] = pd.NA

    # Ajoute l'UUID si besoin
    ca = add_persistent_uuid(ca)

    # Réordonner les colonnes
    ca = ca[cols_obligatoires]

    return ca             
       


