import streamlit as st
import pandas as pd
import datetime
import io
import re
from openpyxl import load_workbook
from openpyxl.styles import Font

# Variables globales
BASE_DATE = datetime.date(2000, 1, 1)
MARGE = datetime.timedelta(minutes=30)
PAUSE_DEJ_DEBUT_MIN = datetime.time(11, 0)
PAUSE_DEJ_DEBUT_MAX = datetime.time(14, 0)
PAUSE_DIN_DEBUT_MIN = datetime.time(19, 0)
PAUSE_DIN_DEBUT_MAX = datetime.time(21, 0)
DUREE_REPAS = datetime.timedelta(hours=1)
DUREE_CAFE = datetime.timedelta(minutes=30)

# Retourne les lignes d'un df pour lesquelles une colonne donnée est vide (None ou "")
def lignes_vides(df, col):
    return df[df[col].isna() | (df[col].astype(str).str.strip() == "")]

# retourne le titre d'une activité
def get_descripteur_activite(date, row):
    titre = f"{date} - {row['Heure'].strip()} - {row['Activite']}"
    if not (pd.isna(row["Theatre"]) or str(row["Theatre"]).strip() == ""):
        titre = titre + f"( {row['Theatre']}) - P{formatter_cellule_int(row['Priorite'])}"
    return titre

# Affiche le titre de la page
def afficher_titre():
    # Réduire l’espace en haut de la page
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 0rem;
            }
        </style>
        """, unsafe_allow_html=True
    )

    # Titre de la page
    st.markdown("## Planificateur Avignon Off")

# Affichage de l'aide
def afficher_aide():
    
    # st.sidebar.markdown("### Aide")
    # st.sidebar.markdown("""
    # - **Chargement du fichier** : Sélectionnez un fichier Excel contenant les spectacles à planifier.
    # - **Affichage des activités planifiées** : Consultez les activités déjà planifiées.
    # - **Suppression d'une activité** : Sélectionnez une activité planifiée pour la supprimer (si elle n'est pas réservée).
    # - **Sélection d'un créneau** : Choisissez un créneau avant ou après une activité planifiée.
    # - **Sélection d'une activité à planifier** : Choisissez une activité à planifier dans le créneau sélectionné.
    # - **Renvoi du fichier modifié** : Renvoyer le fichier Excel modifié.
    # - **Prise en compte des pauses** : Optionnellement, tenez compte des pauses (déjeuner, dîner, café) lors de la planification des activités.
    # """, )

    with st.expander("ℹ️ À propos"):
        st.markdown("""
        <div style='font-size: 14px;'>
        <p style="margin-bottom: 0.2em">Cette application offre les fonctionnalités suivantes:</p>
        <ul style="margin-top: 0em; margin-bottom: 2em">
        <li>Chargement d'un fichier Excel contenant les spectacles à planifier</li>
        <li>Affichage des activités planifiées (i.e. celles dont le champ Date est renseigné)</li>
        <li>Suppression d'une activité planifiée (si non réservée)</li>
        <li>Sélection d'un créneau avant ou après une activité planifiée</li>
        <li>Sélection d'une activité à planifier dans le créneau sélectionné</li>
        <li>Sauvegarde du ficher Excel modifié</li>
        <li>Prise en compte optionnelle des pauses (déjeuner, dîner, café)</li>
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
        <li>Heure : Heure de début de l'activité (format HHhMM)</li>
        <li>Durée : Durée de l'activité (format HHhMM ou HHh)</li>
        <li>Activité : Nom du spectacle ou de l'activité s'il s'agit d'une autre activité (pause, visite, ...)</li>
        <li>Théâtre : Nom du théâtre où se déroule l'activité</li>
        <li>Relâche : Jours de relâche pour le spectacle (liste d'entiers, peut être vide)</li>
        <li>Réservé : Indique si l'activité est réservée (Oui/Non, vide interpété comme Non)</li>
        </ul>

        <p style="margin-bottom: 0.2em">📥Un modèle Excel est disponible <a href="https://github.com/jnicoloso-91/PlanifAvignon-05/raw/main/Mod%C3%A8le%20Excel.xlsx" download>
        ici
        </a></p>
        <p>ℹ️ Si le téléchargement ne démarre pas, faites un clic droit → "Enregistrer le lien sous...".</p>

        </div>
        """, unsafe_allow_html=True)   

# Nettoyage des données du tableau Excel importé
def nettoyer_donnees(df):
    
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
    
    try:
        # Nettoyage noms de colonnes : suppression espaces et normalisation accents
        df.columns = df.columns.str.strip().str.replace("\u202f", " ").str.normalize("NFKD").str.encode("ascii", errors="ignore").str.decode("utf-8")

        colonnes_attendues = ["Reserve", "Priorite", "Date", "Heure", "Duree", "Theatre", "Relache"]
        colonnes_attendues_avec_accents = ["Réservé", "Priorité", "Date", "Heure", "Durée", "Théâtre", "Activité", "Relâche"]

        if not all(col in df.columns for col in colonnes_attendues) and ("Activite" in df.columns or "Spectacle" in df.columns):
            st.error("Le fichier ne contient pas toutes les colonnes attendues: " + ", ".join(colonnes_attendues_avec_accents))
            st.session_state["fichier_invalide"] = True
        else:
            # Suppression du flag "fichier_invalide" s'il existe
            if "fichier_invalide" in st.session_state:
                del st.session_state["fichier_invalide"] 

            # Changement de la colonne Spectacle en Activite
            if "Spectacle" in df.columns:
                df.rename(columns={"Spectacle": "Activite"}, inplace=True)
            
            # Suppression des lignes presque vides i.e. ne contenant que des NaN ou des ""
            df = df[~df.apply(lambda row: all(pd.isna(x) or str(x).strip() == "" for x in row), axis=1)].reset_index(drop=True)

            # Nettoyage Heure : "10h00" -> datetime.time
            df["Heure"] = df["Heure"].apply(heure_str)
            df["Heure_dt"] = df["Heure"].apply(parse_heure)

            # Nettoyage Duree : "1h00" -> timedelta
            df["Duree"] = df["Duree"].apply(duree_str)
            df["Duree_dt"] = df["Duree"].apply(parse_duree)

            # Convertit explicitement certaines colonnes pour éviter les erreurs de conversion pandas
            df["Reserve"] = df["Reserve"].astype("object").fillna("").astype(str)
            df["Relache"] = df["Relache"].astype("object").fillna("").astype(str)
            pd.set_option('future.no_silent_downcasting', True)
            df["Priorite"] = pd.to_numeric(df["Priorite"], errors="coerce").astype("Int64")
            df["Priorite"] = df["Priorite"].astype("object").fillna("").astype(str)
            
    except Exception as e:
        st.error(f"Erreur lors du décodage du fichier : {e}")
        st.session_state["fichier_invalide"] = True
    
    return df

# Renvoie les hyperliens de la colonne Spectacle 
def get_liens_spectacles():
    liens_spectacles = {}
    ws = st.session_state.wb.worksheets[0]
    for cell in ws[1]:
        if cell.value and str(cell.value).strip().lower() in ["activité", "spectacle"]:
            col_excel_index = cell.column
    for row in ws.iter_rows(min_row=2, min_col=col_excel_index, max_col=col_excel_index):
        cell = row[0]
        if cell.hyperlink:
            liens_spectacles[cell.value] = cell.hyperlink.target
    return liens_spectacles

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
            heure_str = str(row["Heure"]).strip() if pd.notna(row["Heure"]) else "Vide"
            duree_str = str(row["Duree"]).strip() if pd.notna(row["Duree"]) else "Vide"
            bloc.append(f"{date_str} - {heure_str} - {row['Activite']} ({duree_str})")
        erreurs.append("\n".join(bloc))
        
    # 2. ⛔ Chevauchements
    chevauchements = []
    df_sorted = df.sort_values(by=["Date", "Heure_dt"])
    for i in range(1, len(df_sorted)):
        r1 = df_sorted.iloc[i - 1]
        r2 = df_sorted.iloc[i]
        if r1.isna().all() or r2.isna().all():
            continue
        if r1["Date"] == r2["Date"]:
            fin1 = r1["Heure_dt"] + r1["Duree_dt"]
            debut2 = r2["Heure_dt"]
            if debut2 < fin1:
                chevauchements.append((r1, r2))
    if chevauchements:
        bloc = ["🔴 Chevauchements:"]
        for r1, r2 in chevauchements:
            bloc.append(
                f"{r1['Activite']} ({r1['Heure']} / {r1['Duree']}) chevauche {r2['Activite']} ({r2['Heure']} / {r2['Duree']}) le {r1['Date']}"
            )
        erreurs.append("\n".join(bloc))

    # 3. 🕒 Erreurs de format
    bloc_format = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Heure", "Duree"]):
            continue
        if row.isna().all():
            continue

        # Date : uniquement si non NaN
        if pd.notna(row["Date"]) and not est_entier(row["Date"]):
            bloc_format.append(f"Date invalide à la ligne {idx + 2} : {row['Date']}")

        # Ne tester Heure/Duree que si Spectacle ou Autres est renseigné
        if str(row["Activite"]).strip() != "":
            if not re.match(r"^\d{1,2}h\d{2}$", str(row["Heure"]).strip()):
                bloc_format.append(f"Heure invalide à la ligne {idx + 2} : {row['Heure']}")
            if not re.match(r"^\d{1,2}h\d{2}$", str(row["Duree"]).strip()):
                bloc_format.append(f"Durée invalide à la ligne {idx + 2} : {row['Duree']}")

    # 4. 📆 Spectacles un jour de relâche (Date == Relache)
    bloc_relache = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Heure", "Duree"]):
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
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Heure", "Duree"]):
            continue
        if row.isna().all():
            continue

        if str(row["Activite"]).strip() != "":
            if pd.isna(row["Heure"]) or str(row["Heure"]).strip() == "":
                bloc_heure_vide.append(f"Heure vide à la ligne {idx + 2}")
    if bloc_heure_vide:
        erreurs.append("⚠️ Heures non renseignées:\n" + "\n".join(bloc_heure_vide))

    # 6. 🕓 Heures au format invalide
    bloc_heure_invalide = []
    for idx, row in df.iterrows():
        # ignorer si rien n'est planifié
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Heure", "Duree"]):
            continue
        if row.isna().all():
            continue

        if str(row["Activite"]).strip() != "":
            h = row["Heure"]
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
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Heure", "Duree"]):
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
        if all(pd.isna(row[col]) or str(row[col]).strip() == "" for col in ["Activite", "Heure", "Duree"]):
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
    return df[df["Date"].notna()].sort_values(by=["Date", "Heure_dt"])

# Affiche les activités planifiées dans un tableau
def afficher_activites_planifiees(planifies):
    df_affichage = planifies[["Date", "Heure", "Duree", "Activite", "Theatre", "Priorite", "Reserve"]].rename(columns={
        "Reserve": "Réservé",
        "Priorite": "Priorité",
        "Duree": "Durée",
        "Theatre": "Théâtre",
        "Relache": "Relâche",
        "Activite": "Activité",
    })

    st.dataframe(df_affichage.fillna(""), hide_index=True,)

# Vérifie si une date de référence est compatible avec la valeur de la colonne Relache qui donne les jours de relache pour un spectacle donné
def est_hors_relache(relache_val, date_val):
    if pd.isna(relache_val) or pd.isna(date_val):
        return True  # Aucune relâche spécifiée ou date absente

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

# Renvoie la liste des activités planifiées
def get_activites_supprimables(df, planifies):
    activites_planifies = []
    for _, row in planifies[planifies["Reserve"].astype(str).str.strip().str.lower() != "oui"].iterrows():
        desc = get_descripteur_activite(str(int(row["Date"])) if pd.notnull(row["Date"]) else "", row)
        activites_planifies.append((desc, row.name))
    return activites_planifies

# Supprime une activité planifiée
def supprimer_activite(planifies, supprimables):

    with st.expander("Supprimer une activité planifiée", expanded=False):

        choix_activite = st.selectbox("⚠️ Seules les activités non réservées sont proposées", [p[0] for p in supprimables])
        # Récupération de l'index de l'activité choisie
        idx = dict((p[0], p[1]) for p in supprimables)[choix_activite]
        ligne_ref = planifies.loc[idx]
        # Suppression de l'activité choisie
        if st.button("Supprimer"):
            st.session_state.df.at[idx, "Date"] = None
            if est_pause(ligne_ref):
                st.session_state.df.at[idx, "Heure"] = None
                st.session_state.df.at[idx, "Duree"] = None
            st.rerun()

# Création de la liste des créneaux avant/après pour chaque activité planifiée
def get_creneaux(df, planifies, traiter_pauses):

    def description_creneau(row, borne_min, borne_max, type_creneau):
        titre = row["Activite"] if not pd.isna(row["Activite"]) else ""
        date_str = str(int(row["Date"])) if pd.notnull(row["Date"]) else ""
        return ((
            f"{date_str} - [{borne_min.strftime('%Hh%M')} - {borne_max.strftime('%Hh%M')}] - {type_creneau} - {titre}",
            (type_creneau, row.name)
        ))
    
    creneaux = []
    bornes = []

    for _, row in planifies.iterrows():

        # Heure de début d'activité
        heure_debut = row["Heure_dt"]
        # Heure de fin d'activité
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
    else:
        debut_min = datetime.datetime.combine(BASE_DATE, datetime.time(0, 0))

    # Calculer l'heure de fin max du créneau
    fin_max = datetime.datetime.combine(BASE_DATE, debut_ref.time())

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
    else:
        fin_max = datetime.datetime.combine(BASE_DATE, datetime.time(23, 59))

    # Calculer l'heure de début minimum du créneau
    debut_min = datetime.datetime.combine(BASE_DATE, fin_ref.time())

    return debut_min, fin_max

# Renvoie la liste des activités planifiables avant une activité donnée par son descripteur ligne_ref
def get_activites_planifiables_avant(df, planifies, ligne_ref, traiter_pauses=True):
    date_ref = ligne_ref["Date"]
    debut_ref = ligne_ref["Heure_dt"]
    duree_ref = ligne_ref["Duree_dt"]
    fin_ref = debut_ref + duree_ref if pd.notnull(debut_ref) and pd.notnull(duree_ref) else None

    proposables = [] 

    debut_min, fin_max = get_creneau_bounds_avant(planifies, ligne_ref)
    if debut_min >= fin_max:
        return proposables  # Pas d'activités planifiables avant si le créneau est invalide

    for _, row in df[df["Date"].isna()].iterrows():
        if pd.isna(row["Heure_dt"]) or pd.isna(row["Duree_dt"]):
            continue
        h_debut = datetime.datetime.combine(BASE_DATE, row["Heure_dt"].time())
        h_fin = h_debut + row["Duree_dt"]
        # Le spectacle doit commencer après debut_min et finir avant fin_max
        if h_debut >= debut_min + MARGE and h_fin <= fin_max - MARGE and est_hors_relache(row["Relache"], date_ref):
            desc = get_descripteur_activite(int(date_ref), row)
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

    proposables = []

    debut_min, fin_max = get_creneau_bounds_apres(planifies, ligne_ref)
    if debut_min >= fin_max:
        return proposables  # Pas d'activités planifiables avant si le créneau est invalide

    if fin_ref.day != debut_ref.day:
        return proposables  # Pas d'activités planifiables après si le jour a changé

    for _, row in df[df["Date"].isna()].iterrows():
        if pd.isna(row["Heure_dt"]) or pd.isna(row["Duree_dt"]):
            continue
        h_debut = datetime.datetime.combine(BASE_DATE, row["Heure_dt"].time())
        h_fin = h_debut + row["Duree_dt"]
        # Le spectacle doit commencer après debut_min et finir avant fin_max
        if h_debut >= debut_min + MARGE and h_fin <= fin_max - MARGE and est_hors_relache(row["Relache"], date_ref):
            desc = get_descripteur_activite(int(date_ref), row)
            proposables.append((h_debut, desc, row.name, "ActiviteExistante"))
    if traiter_pauses:
        ajouter_pauses(proposables, planifies, ligne_ref, "Après")
    # Trier par h_debut croissant
    proposables.sort(key=lambda x: x[0])
    return proposables
    
# Vérifie si une pause d'un type donné est déjà présente pour un jour donné dans le dataframe des activités planiées
def pause_deja_existante(planifies, jour, type_pause):
    df_jour = planifies[
        (planifies["Date"] == jour) & 
        (planifies["Theatre"].isna() | planifies["Theatre"].astype(str).str.strip() == "")
    ]
    return df_jour["Activite"].astype(str).str.contains(type_pause, case=False, na=False).any() 

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
                    proposables.append((h_dej, desc(h_dej, DUREE_REPAS, f"Pause {type_repas}"), None, type_repas))
    
    def ajouter_pause_cafe(proposables, debut_min, fin_max):
        if not est_pause(ligne_ref):
            theatre_ref = ligne_ref["Theatre"]
            if type_creneau == "Avant":
                i = planifies.index.get_loc(ligne_ref.name)  
                theatre_prev = planifies.iloc[i - 1]["Theatre"] if i > 0 else None
                h_cafe = fin_max - DUREE_CAFE
                if theatre_ref == theatre_prev: 
                    # Dans ce cas pas la peine de tenir compte de la marge avec le spectacle précédent 
                    if h_cafe >= debut_min: 
                        proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "café"))
                else: 
                    # Dans ce cas on tient compte de la marge avec le spectacle précédent sauf si debut_min = 0h00
                    marge_cafe = MARGE if debut_min != datetime.datetime.combine(BASE_DATE, datetime.time(0, 0)) else datetime.timedelta(minutes=0) 
                    if h_cafe >= debut_min + marge_cafe:
                        proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "café"))
            elif type_creneau == "Après":
                i = planifies.index.get_loc(ligne_ref.name)  
                theatre_suiv = planifies.iloc[i + 1]["Theatre"] if i < len(planifies) - 1 else None
                h_cafe = debut_min
                if theatre_ref == theatre_suiv: 
                    # Dans ce cas pas la peine de tenir compte de la marge avec le spectacle suivant 
                    if h_cafe + DUREE_CAFE <= fin_max: 
                        proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "café"))
                else: 
                    # Dans ce cas on tient compte de la marge avec le spectacle suivant sauf si fin_max = 23h59
                    marge_cafe = MARGE if fin_max != datetime.datetime.combine(BASE_DATE, datetime.time(23, 59)) else datetime.timedelta(minutes=0)
                    if h_cafe + DUREE_CAFE <= fin_max - marge_cafe:
                        proposables.append((h_cafe, desc(h_cafe, DUREE_CAFE, "Pause café"), None, "café"))

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
        raise ValueError("type_creneau doit être 'Avant' ou 'Après'")

    # Pause déjeuner
    ajouter_pause_repas(proposables, date_ref, debut_min, fin_max, PAUSE_DEJ_DEBUT_MIN, PAUSE_DEJ_DEBUT_MAX, "déjeuner")

    # Pause dîner
    ajouter_pause_repas(proposables, date_ref, debut_min, fin_max, PAUSE_DIN_DEBUT_MIN, PAUSE_DIN_DEBUT_MAX, "dîner")

    # Pause café
    ajouter_pause_cafe(proposables, debut_min, fin_max)
   
def est_pause(ligne_ref):
    val = str(ligne_ref["Activite"]).strip()
    valeurs = val.split()
    if not valeurs:
        return False
    return (val.split()[0].lower() == "pause" and 
        (pd.isna(ligne_ref["Theatre"]) or str(ligne_ref["Theatre"]).strip() == "")
    )

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

def sauvegarder_excel():
    if "df" in st.session_state:

        # Trier par Date (nombre entier) puis Heure
        # df_sorted = st.session_state.df.sort_values(by=["Date", "Heure_dt"]).reset_index(drop=True).drop(columns=["Heure_dt", "Duree_dt"], errors='ignore')

        # df_sorted = df_sorted.rename(columns={
        #     "Reserve": "Réservé",
        #     "Priorite": "Priorité",
        #     "Duree": "Durée",
        #     "Theatre": "Théâtre",
        #     "Relache": "Relâche",
        #     "Activite": "Activité",
        # })

        # Récupération de la worksheet à traiter
        wb = st.session_state.wb
        ws = wb.worksheets[0]
        liens_spectacles = st.session_state.liens_spectacles

        # Effacer le contenu de la feuille Excel existante
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column):
            for cell in row:
                cell.value = None  # on garde le style, on efface juste la valeur
                cell.hyperlink = None

        # Réinjecter les données du df dans la feuille Excel
        from copy import copy
        for cell in ws[1]:
            if cell.value and str(cell.value).strip().lower() in ["activité", "spectacle"]:
                col_spectacle = cell.column
        source_font = ws.cell(row=1, column=1).font

        # for row_idx, (_, row) in enumerate(df_sorted.iterrows()):
        #     for col_idx, value in enumerate(row, start=1):
        #         if pd.isna(value):
        #                 ws.cell(row=row_idx + 2, column=col_idx, value=None)
        #         else:
        #             try:
        #                 # Tente la conversion en entier (ne garde que les entiers stricts, pas les float)
        #                 v = int(value)
        #                 if str(v) == str(value).strip():
        #                     ws.cell(row=row_idx + 2, column=col_idx, value=v)
        #                 else:
        #                     ws.cell(row=row_idx + 2, column=col_idx, value=value)
        #             except (ValueError, TypeError):
        #                 ws.cell(row=row_idx + 2, column=col_idx, value=value)                
        #                 # +2 car openpyxl est 1-indexé et on saute la ligne d’en-tête
        #                 # Ajoute le lien s’il s’agit de la colonne Spectacle
        #                 if col_idx == col_spectacle and liens_spectacles is not None:
        #                     lien = liens_spectacles.get(value)
        #                     if lien:
        #                         ws.cell(row=row_idx + 2, column=col_idx).hyperlink = lien
        #                         ws.cell(row=row_idx + 2, column=col_idx).font = Font(color="0000EE", underline="single")
        #                     else:
        #                         ws.cell(row=row_idx + 2, column=col_idx).hyperlink = None
        #                         ws.cell(row=row_idx + 2, column=col_idx).font = copy(source_font)
        # Réindexer proprement pour éviter les trous
        df_sorted = st.session_state.df.copy()
        df_sorted = df_sorted.sort_values(by=["Date", "Heure_dt"])
        df_sorted = df_sorted.reset_index(drop=True)
        df_sorted = df_sorted.drop(columns=["Heure_dt", "Duree_dt"], errors='ignore')

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

                    # Ajout d'hyperlien pour la colonne Spectacle
                    if col_idx == col_spectacle and liens_spectacles is not None:
                        lien = liens_spectacles.get(value)
                        if lien:
                            cell.hyperlink = lien
                            cell.font = Font(color="0000EE", underline="single")
                            print(f"{row_idx} - {cell.value} - {lien}")
                        else:
                            cell.hyperlink = None
                            cell.font = copy(source_font)   

        # Sauvegarde dans un buffer mémoire
        buffer = io.BytesIO()
        wb.save(buffer)

        # Revenir au début du buffer pour le téléchargement
        buffer.seek(0)

        nom_fichier = st.session_state.get("file_uploader").name if "file_uploader" in st.session_state else "planification_avignon.xlsx"

        # Bouton de téléchargement
        return st.download_button(
            label="Sauvegarder Excel",
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
        elif type_activite == "déjeuner":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(st.session_state.df)  # Ajouter à la fin du DataFrame
            st.session_state.df.at[index, "Date"] = date_ref
            st.session_state.df.at[index, "Heure"] = (dict((p[1], p[0]) for p in proposables)[choix_activite]).time().strftime("%Hh%M")
            st.session_state.df.at[index, "Duree"] = formatter_timedelta(DUREE_REPAS)
            st.session_state.df.at[index, "Activite"] = "Pause déjeuner"
        elif type_activite == "dîner":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(st.session_state.df)  # Ajouter à la fin du DataFrame
            st.session_state.df.at[index, "Date"] = date_ref
            st.session_state.df.at[index, "Heure"] = (dict((p[1], p[0]) for p in proposables)[choix_activite]).time().strftime("%Hh%M")
            st.session_state.df.at[index, "Duree"] = formatter_timedelta(DUREE_REPAS)
            st.session_state.df.at[index, "Activite"] = "Pause dîner"
        elif type_activite == "café":
            # Pour les pauses, on ne planifie pas d'heure spécifique
            index = len(st.session_state.df)  # Ajouter à la fin du DataFrame
            st.session_state.df.at[index, "Date"] = date_ref
            st.session_state.df.at[index, "Heure"] = (dict((p[1], p[0]) for p in proposables)[choix_activite]).time().strftime("%Hh%M")
            st.session_state.df.at[index, "Duree"] = formatter_timedelta(DUREE_CAFE)
            st.session_state.df.at[index, "Activite"] = "Pause café"
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
            st.session_state.wb = load_workbook(fichier)
            st.session_state.liens_spectacles = get_liens_spectacles()
        except Exception as e:
            st.error(f"Erreur lors du chargement du fichier : {e}")
            st.stop()
    else:
        st.session_state.clear()

def main():
    # Affichage du titre
    afficher_titre()

    afficher_aide()

    # Chargement du fichier Excel contenant les spectacles à planifier
    uploaded_file = st.file_uploader(
        "Choix du fichier Excel contenant les spectacles à planifier", 
        type=["xlsx"], 
        key="file_uploader",
        on_change=file_uploader_callback)
    
    # Si le fichier est chargé dans st.session_state.df et valide, on le traite
    if "df" in st.session_state:

        # Nettoyage des données
        st.session_state.df = nettoyer_donnees(st.session_state.df)

        # Accès au DataFrame après nettoyage
        df = st.session_state.df

        if not "fichier_invalide" in st.session_state:
            # Vérification de cohérence des informations du df
            verifier_coherence(df) 

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
                            sauvegarder_excel()
                    else:
                        st.info("Aucune activité compatible avec ce créneau.")
                else:
                    st.info("Aucun créneau disponible.")
            

if __name__ == "__main__":
    main()
