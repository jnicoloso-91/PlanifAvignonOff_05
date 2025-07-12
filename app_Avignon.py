import streamlit as st
import pandas as pd
import datetime
import io

# Variables globales
BASE_DATE = datetime.date(2000, 1, 1)
MARGE = datetime.timedelta(minutes=30)
PAUSE_DEJ_DEBUT_MIN = datetime.time(11, 0)
PAUSE_DEJ_DEBUT_MAX = datetime.time(14, 0)
PAUSE_DIN_DEBUT_MIN = datetime.time(19, 0)
PAUSE_DIN_DEBUT_MAX = datetime.time(21, 0)
DUREE_REPAS = datetime.timedelta(hours=1)
DUREE_CAFE = datetime.timedelta(minutes=30)


# Ecris le titre de la page
def ecrire_titre():
    # st.title("Planification Avignon 2025",anchor="top")
    # Réduire l’espace en haut de la page
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 1rem;
            }
        </style>
        """,
        unsafe_allow_html=True
    )
    st.markdown("## Planification Avignon 2025")

# Nettoyage des données du tableau Excel importé
def nettoyer_donnees(df):
    try:
        # Nettoyage noms de colonnes : suppression espaces et normalisation accents
        df.columns = df.columns.str.strip().str.replace("\u202f", " ").str.normalize("NFKD").str.encode("ascii", errors="ignore").str.decode("utf-8")

        colonnes_attendues = ["Reserve", "Priorite", "Date", "Heure", "Duree", "Theatre", "Spectacle", "Relache", "Autres"]
        colonnes_attendues_avec_accents = ["Réservé", "Priorité", "Date", "Heure", "Durée", "Théâtre", "Spectacle", "Relâche", "Autres"]

        if not all(col in df.columns for col in colonnes_attendues):
            st.error("Le fichier ne contient pas toutes les colonnes attendues: " + ", ".join(colonnes_attendues_avec_accents))
            st.session_state["fichier_invalide"] = True
        else:
            # Suppression du flag "fichier_invalide" s'il existe
            if "fichier_invalide" in st.session_state:
                del st.session_state["fichier_invalide"] 

            # Nettoyage Heure : "10h00" -> datetime.time
            # df["Heure_dt"] = pd.to_datetime(
            #     df["Heure"].astype(str).str.replace("h", ":", regex=False).str.strip(),
            #     format="%H:%M",
            #     errors="coerce"
            # )
            # Nettoyage Heure : "10h00" -> datetime.time
            # Evite les NaN plantants et la date de base à utiliser partout dans le programme
            df["Heure_dt"] = pd.to_datetime(
                df["Heure"].apply(lambda h: f"{BASE_DATE.isoformat()} {h.strip()}" if pd.notna(h) else None), 
                format="%Y-%m-%d %Hh%M",
                errors="coerce"
            )

            # # Nettoyage Duree : "1h00" -> timedelta
            # df["Duree_dt"] = pd.to_timedelta(
            #     df["Duree"].astype(str).str.replace("h", ":", regex=False) + ":00",
            #     errors="coerce"
            # )
            # Nettoyage Duree : "1h00" -> timedelta
            # Evite les NaN plantants et corrige les formats partiels (ex: "1h" → "1h0m", "0h45" → "0h45m", "30m" → "0h30m")
            df["Duree_dt"] = pd.to_timedelta(
                df["Duree"]
                .fillna("")  
                .str.strip().str.lower()
                .str.replace(r"^(\d+)h$", r"\1h0m", regex=True) 
                .str.replace(r"^(\d+)h(\d+)$", r"\1h\2m", regex=True)
                .str.replace(r"^(\d+)m$", r"0h\1m", regex=True),
                errors="coerce"
            )

            # Convertit explicitement certaines colonnes pour éviter les erreurs de conversion pandas
            df["Reserve"] = df["Reserve"].astype("object").fillna("").astype(str)
            df["Autres"] = df["Autres"].astype("object").fillna("").astype(str)
            df["Relache"] = df["Relache"].astype("object").fillna("").astype(str)
            pd.set_option('future.no_silent_downcasting', True)
            df["Priorite"] = df["Priorite"].astype("object").fillna("").astype(str)

    except Exception as e:
        st.error(f"Erreur lors du décodage du fichier : {e}")
        st.session_state["fichier_invalide"] = True

# Renvoie le dataframe des activités planifiées
def get_activites_planifiees(df):
    return df[df["Date"].notna()].sort_values(by=["Date", "Heure_dt"])

# Affiche les activités planifiées dans un tableau
def afficher_activites_planifiees(planifies):
    df_affichage = planifies[["Date", "Heure", "Duree", "Spectacle", "Theatre", "Priorite", "Reserve", "Autres"]].rename(columns={
        "Reserve": "Réservé",
        "Priorite": "Priorité",
        "Duree": "Durée",
        "Theatre": "Théâtre",
        "Relache": "Relâche",
        "Autres": "Autres                  "
    })

    st.dataframe(df_affichage.fillna(""), hide_index=True,)

# Vérifie si une date de référence est compatible avec la valeur de la colonne Relache qui donne les jours de relache pour un spectacle donné
def est_hors_relache(relache_val, date_val):
    if pd.isna(relache_val):
        return True  # Aucune relache spécifiée
    relache_val_str = str(int(relache_val)).lower().strip() if isinstance(relache_val, (int, float)) else relache_val.lower().strip()
    date_val_str = str(int(date_val)).lower().strip() if isinstance(date_val, (int, float)) else date_val.lower().strip()
    if relache_val_str == date_val_str:
        return False
    if "pair" in relache_val_str and int(date_val) % 2 == 0:
        return False
    if "impair" in relache_val_str and int(date_val) % 2 != 0:
        return False
    return True

# Renvoie la liste des activités planifiées
def get_activites_supprimables(df, planifies):
    activites_planifies = []
    for _, row in planifies[planifies["Reserve"].astype(str).str.strip().str.lower() != "oui"].iterrows():
        # Heure de début
        heure_debut = row["Heure_dt"]
        # Heure de fin
        heure_fin = heure_debut + row["Duree_dt"] if pd.notnull(heure_debut) and pd.notnull(row["Duree_dt"]) else None
        # Format date
        date_str = str(int(row["Date"])) if pd.notnull(row["Date"]) else ""
        titre = f"{row['Spectacle']} ({row['Theatre']}) - P{formatter_cellule_int(row['Priorite'])}" if not pd.isna(row["Spectacle"]) else f"{row['Autres']}"
        # desc = f"{date_str} de {row['Heure'].strip()} à {heure_fin.strftime('%Hh%M')} ({row['Duree'].strip()}) - " + titre
        desc = f"{date_str} - {row['Heure'].strip()} - " + titre
        activites_planifies.append((desc, row.name))
    return activites_planifies

# Supprime une activité planifiée
def supprimer_activite(planifies, supprimables):
    # Choix d'une activité planifiée à supprimer
    choix_activite = st.selectbox("Choix d'une activité à supprimer", [p[0] for p in supprimables])
    # Récupération de l'index de l'activité choisie
    idx = dict((p[0], p[1]) for p in supprimables)[choix_activite]
    ligne_ref = planifies.loc[idx]
    # Suppression de l'activité choisie
    if st.button("Supprimer"):
        st.session_state.df.at[idx, "Date"] = None
        if est_pause(ligne_ref):
            st.session_state.df.at[idx, "Heure"] = None
            st.session_state.df.at[idx, "Duree"] = None
            st.session_state.df.at[idx, "Autres"] = None
        st.rerun()

# Création de la liste des créneaux avant/après pour chaque activité planifiée
def get_creneaux(df, planifies, traiter_pauses):

    def description_creneau(row, borne_min, borne_max, type_creneau):
        titre = row["Spectacle"] if not pd.isna(row["Spectacle"]) else row["Autres"]
        date_str = str(int(row["Date"])) if pd.notnull(row["Date"]) else ""
        return ((
            f"{date_str} - [{borne_min.strftime('%Hh%M')} - {borne_max.strftime('%Hh%M')}] - {type_creneau} - {titre}",
            (type_creneau, row.name)
        ))
    
    creneaux = []
    bornes = []

    for _, row in planifies.iterrows():

        # Heure de début de créneau
        heure_debut = row["Heure_dt"]
        # Heure de fin de créneau
        heure_fin = heure_debut + row["Duree_dt"] if pd.notnull(heure_debut) and pd.notnull(row["Duree_dt"]) else None

        # Ajout des creneaux avant l'activité considérée s'ils existent
        if pd.notnull(heure_debut):
            if get_activites_planifiables_avant(df, planifies, row, traiter_pauses):
                borne_min, borne_max = get_creneau_bounds_avant(planifies, row)
                if (borne_min, borne_max) not in bornes:
                    bornes.append((borne_min, borne_max))
                    creneaux.append(description_creneau(row, borne_min, borne_max, "Avant"))

        # Ajout des creneaux après l'activité considérée s'ils existent
        if pd.notnull(heure_fin):
            if get_activites_planifiables_apres(df, planifies, row, traiter_pauses):
                borne_min, borne_max = get_creneau_bounds_apres(planifies, row)
                if (borne_min, borne_max) not in bornes:
                    bornes.append((borne_min, borne_max))
                    creneaux.append(description_creneau(row, borne_min, borne_max, "Après"))

    return creneaux

# Renvoie les bornes du créneau existant avant une activité donnée par son descripteur ligne_ref
def get_creneau_bounds_avant(planifies, ligne_ref):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Heure_dt"]
    duree_ref = ligne_ref["Duree_dt"]
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None    

    # Chercher l'activité planifiée précédente sur le même jour
    planifies_jour_ref = planifies[planifies["Date"] == date_ref]
    planifies_jour_ref = planifies_jour_ref.sort_values(by="Heure_dt")
    prev = planifies_jour_ref[planifies_jour_ref["Heure_dt"] < debut_ref].tail(1)

    # Calculer l'heure de début minimum du créneau
    if not prev.empty:
        prev_fin = datetime.datetime.combine(BASE_DATE, prev["Heure_dt"].iloc[0].time()) + prev["Duree_dt"].iloc[0]
        debut_min = prev_fin
        if not est_pause_cafe(ligne_ref):
            debut_min = debut_min + MARGE
    else:
        debut_min = datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))

    # Calculer l'heure de fin max du créneau
    fin_max = datetime.datetime.combine(BASE_DATE, debut_ref.time())
    if not est_pause_cafe(ligne_ref):
        fin_max = fin_max - MARGE

    # Si le créneau est invalide, on ajuste
    if debut_min > fin_max:
        debut_min = fin_max  

    return debut_min, fin_max

# Renvoie les bornes du créneau existant après une activité donnée par son descripteur ligne_ref
def get_creneau_bounds_apres(planifies, ligne_ref):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Heure_dt"]
    duree_ref = ligne_ref["Duree_dt"]
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None    

    # Ajuster la date de référence si le jour a changé
    if fin_ref.day != debut_ref.day:
        date_ref = date_ref + fin_ref.day - debut_ref.day  

    # Chercher l'activité planifiée suivante sur le même jour de référence
    planifies_jour_ref = planifies[planifies["Date"] == date_ref]
    planifies_jour_ref = planifies_jour_ref.sort_values(by="Heure_dt")
    next = planifies_jour_ref[planifies_jour_ref["Heure_dt"] + planifies_jour_ref["Duree_dt"] > fin_ref].head(1)

    # Calculer l'heure de fin max du créneau
    if not next.empty:
        fin_max = datetime.datetime.combine(BASE_DATE, next["Heure_dt"].iloc[0].time())
        if not est_pause_cafe(ligne_ref):
            fin_max = fin_max - MARGE
    else:
        fin_max = datetime.datetime.combine(BASE_DATE, datetime.time(23, 59))

    # Calculer l'heure de début minimum du créneau
    debut_min = datetime.datetime.combine(BASE_DATE, fin_ref.time())
    if not est_pause_cafe(ligne_ref):
        debut_min = debut_min + MARGE

    # Si le créneau est invalide, on ajuste
    if debut_min > fin_max:
        fin_max = debut_min   

    return debut_min, fin_max

# Renvoie la liste des activités planifiables avant une activité donnée par son descripteur ligne_ref
def get_activites_planifiables_avant(df, planifies, ligne_ref, traiter_pauses=True):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Heure_dt"]
    duree_ref = ligne_ref["Duree_dt"]
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None

    debut_min, fin_max = get_creneau_bounds_avant(planifies, ligne_ref)

    proposables = []   
    for _, row in df[df["Date"].isna()].iterrows():
        if pd.isna(row["Heure_dt"]) or pd.isna(row["Duree_dt"]):
            continue
        h_debut = datetime.datetime.combine(BASE_DATE, row["Heure_dt"].time())
        h_fin = h_debut + row["Duree_dt"]
        # Le spectacle doit commencer après debut_min et finir avant fin_max
        if h_debut >= debut_min and h_fin <= fin_max and est_hors_relache(row["Relache"], date_ref):
            titre = f"{row['Spectacle']} ({row['Theatre']}) - P{formatter_cellule_int(row['Priorite'])}" if not pd.isna(row["Spectacle"]) else f"{row['Autres']}"
            # desc = f"{int(date_ref)} de {row['Heure'].strip()} à {h_fin.strftime('%Hh%M')} ({row['Duree'].strip()}) - " + titre
            desc = f"{int(date_ref)} - {row['Heure'].strip()} - " + titre
            proposables.append((h_debut, desc, row.name, "ActiviteExistante"))
    if traiter_pauses:
        ajouter_pauses(proposables, planifies, ligne_ref, "Avant")
    # Trier par h_debut décroissant
    proposables.sort(reverse=True, key=lambda x: x[0])
    return proposables

# Renvoie la liste des activités planifiables après une activité donnée par son descripteur ligne_ref
def get_activites_planifiables_apres(df, planifies, ligne_ref, traiter_pauses=True):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Heure_dt"]
    duree_ref = ligne_ref["Duree_dt"]
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None   

    debut_min, fin_max = get_creneau_bounds_apres(planifies, ligne_ref)

    proposables = []
    if fin_ref.day != debut_ref.day:
        return proposables  # Pas d'activités planifiables après si le jour a changé

    for _, row in df[df["Date"].isna()].iterrows():
        if pd.isna(row["Heure_dt"]) or pd.isna(row["Duree_dt"]):
            continue
        h_debut = datetime.datetime.combine(BASE_DATE, row["Heure_dt"].time())
        h_fin = h_debut + row["Duree_dt"]
        # Le spectacle doit commencer après debut_min et finir avant fin_max
        if h_debut >= debut_min and h_fin <= fin_max and est_hors_relache(row["Relache"], date_ref):
            titre = f"{row['Spectacle']} ({row['Theatre']}) - P{formatter_cellule_int(row['Priorite'])}" if not pd.isna(row["Spectacle"]) else f"{row['Autres']}"
            # desc = f"{int(date_ref)} de {row['Heure'].strip()} à {h_fin.strftime('%Hh%M')} ({row['Duree'].strip()}) - " + titre
            desc = f"{int(date_ref)} - {row['Heure'].strip()} - " + titre
            proposables.append((h_debut, desc, row.name, "ActiviteExistante"))
    if traiter_pauses:
        ajouter_pauses(proposables, planifies, ligne_ref, "Après")
    # Trier par h_debut croissant
    proposables.sort(key=lambda x: x[0])
    return proposables
    
# Vérifie si une pause d'un type donné est déjà présente pour un jour donné dans le dataframe des activités planiées
def pause_deja_existante(planifies, jour, type_pause):
    df_jour = planifies[planifies["Date"] == jour]
    return df_jour["Autres"].astype(str).str.contains(type_pause, case=False, na=False).any() 

# Ajoute les pauses possibles (déjeuner, dîner, café) à une liste d'activités planifiables pour une activité donnée par son descripteur ligne_ref
def ajouter_pauses(proposables, planifies, ligne_ref, type_creneau):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Heure_dt"]
    duree_ref = ligne_ref["Duree_dt"]
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None    

    def desc(h, duree, nom):
        # return f"{int(date_ref)} de {h.strftime('%Hh%M')} à {(h + duree).time().strftime('%Hh%M')} ({formatter_timedelta(duree)}) - {nom}"
        return f"{int(date_ref)} - {h.strftime('%Hh%M')} - {nom}"
    
    # Récupération des bornes du créneau
    if type_creneau == "Avant":
        debut_min, fin_max = get_creneau_bounds_avant(planifies, ligne_ref)
    elif type_creneau == "Après":
        debut_min, fin_max = get_creneau_bounds_apres(planifies, ligne_ref)
    else:
        raise ValueError("type_creneau doit être 'avant' ou 'apres'")

    # Pause déjeuner
    if not pause_deja_existante(planifies, date_ref, "déjeuner"):
        if type_creneau == "Avant":
            h_dej = fin_max - DUREE_REPAS
            if h_dej.time() >= PAUSE_DEJ_DEBUT_MIN:
                if h_dej.time() > PAUSE_DEJ_DEBUT_MAX:
                    h_dej = datetime.datetime.combine(BASE_DATE, PAUSE_DEJ_DEBUT_MAX)  
                if fin_max >= datetime.datetime.combine(BASE_DATE, PAUSE_DEJ_DEBUT_MIN) + DUREE_REPAS:
                    if h_dej >= debut_min and h_dej + DUREE_REPAS <= fin_max:
                        proposables.append((h_dej, desc(h_dej, DUREE_REPAS, "Pause déjeuner"), None, "PauseDejeuner"))
        elif type_creneau == "Après":
            h_dej = debut_min
            if h_dej.time() <= PAUSE_DEJ_DEBUT_MAX:
                if h_dej.time() < PAUSE_DEJ_DEBUT_MIN:
                    h_dej = datetime.datetime.combine(BASE_DATE, PAUSE_DEJ_DEBUT_MIN)  
                if debut_min <= datetime.datetime.combine(BASE_DATE, PAUSE_DEJ_DEBUT_MAX):
                    if h_dej >= debut_min and h_dej+ DUREE_REPAS <= fin_max:
                        proposables.append((h_dej, desc(h_dej, DUREE_REPAS, "Pause déjeuner"), None, "PauseDejeuner"))

    # Pause dîner
    if not pause_deja_existante(planifies, date_ref, "dîner"):
        if type_creneau == "Avant":
            h_din = fin_max - DUREE_REPAS
            if h_din.time() >= PAUSE_DIN_DEBUT_MIN:
                if h_din.time() > PAUSE_DIN_DEBUT_MAX:
                    h_din = datetime.datetime.combine(BASE_DATE, PAUSE_DIN_DEBUT_MAX)  
                if fin_max >= datetime.datetime.combine(BASE_DATE, PAUSE_DIN_DEBUT_MIN) + DUREE_REPAS:
                    if h_din >= debut_min and h_din + DUREE_REPAS <= fin_max:
                        proposables.append((h_din, desc(h_din, DUREE_REPAS, "Pause diner"), None, "PauseDiner"))
        elif type_creneau == "Après":
            h_din = debut_min
            if h_din.time() <= PAUSE_DIN_DEBUT_MAX:
                if h_din.time() < PAUSE_DIN_DEBUT_MIN:
                    h_din = datetime.datetime.combine(BASE_DATE, PAUSE_DIN_DEBUT_MIN)  
                if debut_min <= datetime.datetime.combine(BASE_DATE, PAUSE_DIN_DEBUT_MAX):
                    if h_din >= debut_min and h_din + DUREE_REPAS <= fin_max:
                        proposables.append((h_din, desc(h_din, DUREE_REPAS, "Pause diner"), None, "PauseDiner"))

    # Pause café
    if not est_pause(ligne_ref):
        theatre_ref = ligne_ref["Theatre"]
        theatre_prev = planifies.loc[ligne_ref.name - 1, "Theatre"] if ligne_ref.name > 0 else None
        if type_creneau == "Avant":
            h_cafe = fin_max - DUREE_CAFE + MARGE
            if theatre_ref == theatre_prev: 
                # Dans ce cas pas la peine de tenir compte de la marge avec le spectacle précédent incluse dans debut_min
                if h_cafe >= debut_min - MARGE: 
                    proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "PauseCafe"))
            else: 
                # Dans ce cas on tient compte de la marge avec le spectacle précédent incluse dans debut_min
                if h_cafe >= debut_min:
                    proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "PauseCafe"))
        elif type_creneau == "Après":
            h_cafe = debut_min - MARGE
            if theatre_ref == theatre_prev: 
                # Dans ce cas pas la peine de tenir compte de la marge avec le spectacle suivant incluse dans fin_max
                if h_cafe + DUREE_CAFE <= fin_max - MARGE: 
                    proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "PauseCafe"))
            else: 
                # Dans ce cas on tient compte de la marge avec le spectacle suivant incluse dans fin_max
                if h_cafe + DUREE_CAFE <= fin_max:
                    proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "PauseCafe"))
   
def est_pause(ligne_ref):
    val = str(ligne_ref["Autres"]).strip()
    valeurs = val.split()
    if not valeurs:
        return False
    return val.split()[0].lower() == "pause"

def est_pause_cafe(ligne_ref):
    val = str(ligne_ref["Autres"]).strip()
    valeurs = val.split()
    if not valeurs:
        return False
    if len(valeurs) < 2:
        return False
    return val.split()[0].lower() == "pause" and val.split()[1].lower() == "café"

def telecharger_Excel():
    if "df" in st.session_state:
        # Convertir le DataFrame en fichier Excel dans un buffer mémoire
        buffer = io.BytesIO()

        # Trier par Date (nombre entier) puis Heure
        df_sorted = st.session_state.df.sort_values(by=["Date", "Heure_dt"]).drop(columns=["Heure_dt", "Duree_dt"], errors='ignore')
        df_sorted = df_sorted.rename(columns={
            "Reserve": "Réservé",
            "Priorite": "Priorité",
            "Duree": "Durée",
            "Theatre": "Théâtre",
            "Relache": "Relâche",
            "Autres": "Autres    "
        })

        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_sorted.to_excel(writer, index=False)

        # Revenir au début du buffer pour le téléchargement
        buffer.seek(0)

        nom_fichier = st.session_state.get("file_uploader").name if "file_uploader" in st.session_state else "planification_avignon.xlsx"

        # Bouton de téléchargement
        return st.download_button(
            label="Télécharger Excel",
            data=buffer,
            file_name=nom_fichier,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        return False

def ajouter_activite(date_ref, proposables, choix_activite):

    type_activite = dict((p[1], p[3]) for p in proposables)[choix_activite]
    if st.button("Ajouter au planning"):
        if type_activite == "ActiviteExistante":
            # Pour les spectacles, on planifie la date et l'heure
            index = dict((p[1], p[2]) for p in proposables)[choix_activite]
            st.session_state.df.at[index, "Date"] = date_ref
        elif type_activite == "PauseDejeuner":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(st.session_state.df)  # Ajouter à la fin du DataFrame
            st.session_state.df.at[index, "Date"] = date_ref
            st.session_state.df.at[index, "Heure"] = (dict((p[1], p[0]) for p in proposables)[choix_activite]).time().strftime("%Hh%M")
            st.session_state.df.at[index, "Duree"] = formatter_timedelta(DUREE_REPAS)
            st.session_state.df.at[index, "Autres"] = "Pause déjeuner"
        elif type_activite == "PauseDiner":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(st.session_state.df)  # Ajouter à la fin du DataFrame
            st.session_state.df.at[index, "Date"] = date_ref
            st.session_state.df.at[index, "Heure"] = (dict((p[1], p[0]) for p in proposables)[choix_activite]).time().strftime("%Hh%M")
            st.session_state.df.at[index, "Duree"] = formatter_timedelta(DUREE_REPAS)
            st.session_state.df.at[index, "Autres"] = "Pause dîner"
        elif type_activite == "PauseCafe":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(st.session_state.df)  # Ajouter à la fin du DataFrame
            st.session_state.df.at[index, "Date"] = date_ref
            st.session_state.df.at[index, "Heure"] = (dict((p[1], p[0]) for p in proposables)[choix_activite]).time().strftime("%Hh%M")
            st.session_state.df.at[index, "Duree"] = formatter_timedelta(DUREE_CAFE)
            st.session_state.df.at[index, "Autres"] = "Pause café"
        st.rerun()

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
        return int(d)
    return d
    
# Callback de st.file_uploader pour charger le fichier Excel
def file_uploader_callback():
    fichier = st.session_state.get("file_uploader")
    if fichier is not None:
        try:
            st.session_state.df = pd.read_excel(fichier)
        except Exception as e:
            st.error(f"Erreur lors du chargement du fichier : {e}")
            st.stop()
    else:
        st.session_state.clear()

def main():
    # Affichage du titre
    ecrire_titre()

    # Chargement du fichier Excel contenant les spectacles à planifier
    uploaded_file = st.file_uploader(
        "Choix du fichier Excel contenant les spectacles à planifier", 
        type=["xlsx"], 
        key="file_uploader",
        on_change=file_uploader_callback)

    # Si le fichier est chargé dans st.session_state.df et valide, on le traite
    if "df" in st.session_state:

        # Accès au DataFrame
        df = st.session_state.df

        # Nettoyage des données
        nettoyer_donnees(df)

        if not "fichier_invalide" in st.session_state:
            # Affectation du flag de traitement des pauses
            traiter_pauses = st.checkbox("Tenir compte des pauses (déjeuner, dîner, café)", value=False)  

            # Affichage des activités planifiées
            st.markdown("##### Activités planifiées")
            planifies = get_activites_planifiees(df)
            afficher_activites_planifiees(planifies)

            # Gestion de la liste des activités planifiées
            supprimables = get_activites_supprimables(df, planifies)
            if supprimables:
                supprimer_activite(planifies, supprimables)

            # Planification de nouvelles activités
            st.markdown("##### Planification de nouvelles activités")
            if not planifies.empty:
                # Création des créneaux avant/après pour chaque spectacle planifié
                creneaux = get_creneaux(df, planifies, traiter_pauses)

                if creneaux:
                    # Choix d'un créneau à planifier
                    choix_creneau = st.selectbox("Choix du créneau à planifier", [c[0] for c in creneaux])
                    type_creneau, idx = dict(creneaux)[choix_creneau]

                    ligne_ref = planifies.loc[idx]
                    date_ref = ligne_ref["Date"]

                    # Choix d'une activité à planifier dans le creneau choisi
                    if type_creneau == "Avant":
                        proposables = get_activites_planifiables_avant(df, planifies, ligne_ref, traiter_pauses)

                    elif type_creneau == "Après":
                        proposables = get_activites_planifiables_apres(df, planifies, ligne_ref, traiter_pauses)

                    if proposables:
                        choix_activite = st.selectbox("Choix de l'activité à planifier dans le créneau sélectionné", [p[1] for p in proposables])
                        col1, col2 = st.columns(2)
                        with col1:
                            ajouter_activite(date_ref, proposables, choix_activite)
                        with col2:
                            telecharger_Excel()
                    else:
                        st.info("Aucune activité compatible avec ce créneau.")
                else:
                    st.info("Aucun créneau disponible.")
            

if __name__ == "__main__":
    main()
