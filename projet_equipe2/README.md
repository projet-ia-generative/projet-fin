# Assistant conversationnel — projet d'équipe

Cette application Web permet de discuter avec un modèle d'IA via l'API Ollama.
Elle est construite avec Django et conserve les conversations et leurs messages
dans une base de données SQLite locale. Les questions sont aussi confrontées aux
documents et aux données de `Base_de_connaissance/`; les extraits pertinents sont
envoyés au modèle avec la question pour qu'il puisse fonder sa réponse sur les
sources du projet.

## Fonctionnalités

- Créer et sélectionner des conversations.
- Envoyer un message au modèle Ollama et afficher sa réponse.
- Conserver l'historique des messages de chaque conversation.
- Supprimer une conversation et tous ses messages associés.
- Charger localement une clé d'API Ollama depuis `config.json`.
- Rechercher des passages utiles dans les fichiers PDF, Word, CSV et JSONL de
  la base de connaissances et les fournir au modèle avec leurs références;
  `QuestionsReponsesSources.docx` est exclu de cette recherche.

La recherche est lexicale (classement BM25), sans service externe d'indexation.
Les fichiers structurés sont lus localement; les trois PDF dont le nom correspond
le mieux aux termes de la question sont extraits afin d'éviter d'analyser tous
les PDF à chaque sujet. Les index sont conservés en mémoire et l'extraction PDF
est mise en cache pendant la vie du processus. Redémarrer Django pour prendre
en compte les modifications aux fichiers de la base. La recherche ne fait pas
d'OCR : un PDF composé seulement d'images ne fournira pas de texte exploitable.

La question et les extraits retrouvés sont envoyés à l'API distante Ollama
configurée par l'application. N'utilise pas cette fonction avec des données
confidentielles sans vérifier les règles de traitement des données applicables.

## Arborescence

```text
projet_equipe/
├── .gitignore
├── README.md
├── Lancer_appli.bat
├── manage.py
├── requirements.txt
├── config.json                     # Configuration locale, non versionnée
├── db.sqlite3                      # Base de données locale, non versionnée
├── .venv/                          # Environnement Python local, non versionné
├── assistant_project/
│   ├── __init__.py
│   ├── asgi.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── chat/
│   ├── __init__.py
│   ├── migrations/
│   │   ├── __init__.py
│   │   └── 0001_initial.py
│   ├── templates/
│   │   └── chat/
│   │       └── index.html
│   ├── knowledge_base.py
│   ├── models.py
│   ├── tests.py
│   ├── urls.py
│   └── views.py
└── Base_de_connaissance/
    ├── QuestionsReponsesSources.docx
    ├── Bd_aut2026/
    │   ├── Bd_demo_IA_v2026-06-29.csv
    │   ├── Bd_demo_IA_v2026-06-29.jsonl
    │   ├── Bd_famille_IA_v2026-06-29.csv
    │   ├── Bd_famille_IA_v2026-06-29.jsonl
    │   ├── Bd_revenu_IA_v2026-06-29.csv
    │   ├── Bd_revenu_IA_v2026-06-29.jsonl
    │   ├── Bd_revenu_rang_ra_IA_v2026-06-29.csv
    │   └── Bd_revenu_rang_ra_IA_v2026-06-29.jsonl
    └── Textes_aut2026/
        ├── CLPP_EnqueteFraudeEvictionLogement.pdf
        ├── CRV-CAMF_2016_HabitationAines.pdf
        ├── DSPMontreal_2015_LogementsSalubresAbordables.pdf
        ├── FRAPRU_2018_LogementPauvreteQuebec.pdf
        ├── Goyer_2020_LogementInegalitesSociales.pdf
        ├── Goyer_2024_DefendreDroitLogement.pdf
        ├── Goyer_2025_ExperienceDepossessionEviction.pdf
        ├── INSPQ_2014_HabitatPlusToit.pdf
        ├── INSPQ_2021_LogementInegaliteSocialesSanteCOVID.pdf
        ├── INSPQ_2022_PlanifierOffreResidentielle.pdf
        ├── INSPQ_2026_RoleDeterminantLogementSante.pdf
        ├── Leloup_2019_InsecuriteResidentielleMesureAD.pdf
        ├── OQI_2025_CoutsSanteManqueLogementsAdequats.pdf
        ├── OQI_2025_ManqueLogementsAdequatsCoutsProsperiteEconomique.pdf
        ├── OQI_2026_ManqueLogementsAdequatsViolenceDomestiqueDeveloppementEnfants.pdf
        ├── SimardJ_2019_PrecariteResidentielleLocatairesFaibleRevenu.pdf
        ├── SimardJ_2025_MaltraitancePersonnesVieillissantesParcLocatif.pdf
        └── SwopeHernandez_2019_HousingDeterminantHealthEquity.pdf
```

Les répertoires `__pycache__/` et leurs fichiers `.pyc` peuvent apparaître après
l'exécution de Python. Ce sont des caches générés automatiquement, ignorés par
Git et recréables; ils ne font pas partie des sources à maintenir.

## Rôle des fichiers et répertoires

### À la racine

| Fichier ou répertoire | Rôle |
| --- | --- |
| `.gitignore` | Exclut de Git l'environnement virtuel, les caches Python, la base de données locale, `config.json` et les fichiers temporaires de déploiement. |
| `README.md` | Présente le projet, son arborescence, les fichiers et les consignes d'exécution. |
| `Lancer_appli.bat` | Ouvre l'application dans le navigateur et démarre le serveur Django local avec l'interpréteur de `.venv`. |
| `manage.py` | Point d'entrée des commandes Django, par exemple pour lancer le serveur, appliquer les migrations ou exécuter les tests. |
| `requirements.txt` | Déclare les dépendances Python de l'application : Django, Requests et PyMuPDF pour extraire le texte des PDF. |
| `config.json` | Configuration locale contenant la clé `OLLAMA_API_KEY` utilisée pour authentifier les appels à l'API. Ce fichier est ignoré par Git; ne partage pas et ne versionne pas une vraie clé. |
| `db.sqlite3` | Base SQLite de développement où Django conserve les conversations, les messages et ses autres données locales. Le fichier est ignoré par Git. |
| `.venv/` | Environnement virtuel Python local avec les dépendances installées. Il est ignoré par Git et peut être recréé. |

### Projet Django — `assistant_project/`

| Fichier | Rôle |
| --- | --- |
| `__init__.py` | Marque `assistant_project` comme paquet Python. Même vide, il permet de conserver une importation de paquet compatible. |
| `asgi.py` | Expose l'application Django pour les serveurs compatibles ASGI. |
| `settings.py` | Configure Django : applications, middleware, modèles de page, base SQLite, langue, fuseau horaire et options du projet. |
| `urls.py` | Route `/admin/` vers l'administration Django et le chemin racine vers les routes de l'application `chat`. |
| `wsgi.py` | Expose l'application Django pour les serveurs compatibles WSGI. |

### Application de conversation — `chat/`

| Fichier ou répertoire | Rôle |
| --- | --- |
| `__init__.py` | Marque `chat` comme paquet Python. Ce fichier vide est conservé pour la découverte et l'importation du paquet. |
| `models.py` | Définit les modèles `Conversation` et `Message`, leurs champs, relations et ordres d'affichage. |
| `knowledge_base.py` | Extrait les textes PDF/Word et les données CSV/JSONL, évite les doublons CSV/JSONL, sélectionne des PDF d'après les termes de la question, découpe et classe les passages pertinents avec BM25. |
| `views.py` | Gère l'affichage du chat, la création et la suppression de conversations, la validation des messages et les échanges avec l'API Ollama. |
| `urls.py` | Associe les routes Web et API de l'application à leurs vues. |
| `tests.py` | Vérifie les comportements de suppression de conversation et les routes associées. |
| `templates/chat/index.html` | Contient l'interface du chat : historique latéral, affichage des messages, formulaire d'envoi et bouton de suppression. |
| `migrations/__init__.py` | Marque le dossier des migrations comme paquet Python afin que Django puisse les découvrir. |
| `migrations/0001_initial.py` | Crée initialement les tables des conversations et des messages. |


Les fichiers `__init__.py` servent
à la structure des paquets Python et aux migrations Django.

### Ressources — `Base_de_connaissance/`

| Fichier ou répertoire | Rôle |
| --- | --- |
| `QuestionsReponsesSources.docx` | Document de questions, réponses et sources associé au projet. Il est conservé dans le dossier, mais volontairement exclu du contexte envoyé au modèle. |
| `Bd_aut2026/` | Répertoire des jeux de données au format CSV et JSONL. |
| `Textes_aut2026/` | Répertoire des documents PDF de référence. |

#### Jeux de données — `Bd_aut2026/`

Les fichiers CSV et JSONL sont les jeux de données fournis dans deux formats
différents. Les sujets ci-dessous sont indiqués par leurs noms de fichiers.

| Fichier | Rôle |
| --- | --- |
| `Bd_demo_IA_v2026-06-29.csv` | Jeu de données de démonstration au format CSV. |
| `Bd_demo_IA_v2026-06-29.jsonl` | Jeu de données de démonstration au format JSON Lines. |
| `Bd_famille_IA_v2026-06-29.csv` | Jeu de données sur la famille au format CSV. |
| `Bd_famille_IA_v2026-06-29.jsonl` | Jeu de données sur la famille au format JSON Lines. |
| `Bd_revenu_IA_v2026-06-29.csv` | Jeu de données sur le revenu au format CSV. |
| `Bd_revenu_IA_v2026-06-29.jsonl` | Jeu de données sur le revenu au format JSON Lines. |
| `Bd_revenu_rang_ra_IA_v2026-06-29.csv` | Jeu de données sur le revenu et les rangs/régions administratives, au format CSV. |
| `Bd_revenu_rang_ra_IA_v2026-06-29.jsonl` | Même jeu de données au format JSON Lines. |

#### Documents de référence — `Textes_aut2026/`

Les thèmes ci-dessous sont résumés à partir des noms des fichiers; consulter les
PDF pour leur contenu détaillé.

| Fichier | Sujet indiqué par le nom |
| --- | --- |
| `CLPP_EnqueteFraudeEvictionLogement.pdf` | Enquête sur la fraude et l'éviction en logement. |
| `CRV-CAMF_2016_HabitationAines.pdf` | Habitation des personnes aînées. |
| `DSPMontreal_2015_LogementsSalubresAbordables.pdf` | Logements salubres et abordables. |
| `FRAPRU_2018_LogementPauvreteQuebec.pdf` | Logement et pauvreté au Québec. |
| `Goyer_2020_LogementInegalitesSociales.pdf` | Logement et inégalités sociales. |
| `Goyer_2024_DefendreDroitLogement.pdf` | Défense du droit au logement. |
| `Goyer_2025_ExperienceDepossessionEviction.pdf` | Expériences de dépossession et d'éviction. |
| `INSPQ_2014_HabitatPlusToit.pdf` | Habitat et logement. |
| `INSPQ_2021_LogementInegaliteSocialesSanteCOVID.pdf` | Logement, inégalités sociales, santé et COVID. |
| `INSPQ_2022_PlanifierOffreResidentielle.pdf` | Planification de l'offre résidentielle. |
| `INSPQ_2026_RoleDeterminantLogementSante.pdf` | Rôle du logement comme déterminant de la santé. |
| `Leloup_2019_InsecuriteResidentielleMesureAD.pdf` | Insécurité résidentielle et mesure AD. |
| `OQI_2025_CoutsSanteManqueLogementsAdequats.pdf` | Coûts de santé associés au manque de logements adéquats. |
| `OQI_2025_ManqueLogementsAdequatsCoutsProsperiteEconomique.pdf` | Manque de logements adéquats, coûts et prospérité économique. |
| `OQI_2026_ManqueLogementsAdequatsViolenceDomestiqueDeveloppementEnfants.pdf` | Logements inadéquats, violence domestique et développement des enfants. |
| `SimardJ_2019_PrecariteResidentielleLocatairesFaibleRevenu.pdf` | Précarité résidentielle des locataires à faible revenu. |
| `SimardJ_2025_MaltraitancePersonnesVieillissantesParcLocatif.pdf` | Maltraitance des personnes vieillissantes dans le parc locatif. |
| `SwopeHernandez_2019_HousingDeterminantHealthEquity.pdf` | Logement comme déterminant de l'équité en santé. |

Ces ressources sont conservées telles qu'elles ont été fournies; l'application
de chat ne les modifie pas et n'effectue actuellement pas de recherche dedans.

## Installation et démarrage

1. Installer Python et ouvrir PowerShell dans le dossier du projet.
2. Créer et activer un environnement virtuel si `.venv/` n'existe pas :

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Installer les dépendances (dont PyMuPDF pour l'extraction du texte des PDF) :

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Créer `config.json` à la racine du projet avec la clé d'API fournie par
   Ollama :

   ```json
   {
     "OLLAMA_API_KEY": "ta-cle-api"
   }
   ```

   Garde ce fichier local et ne publie pas la clé.

5. Préparer la base de données et lancer le serveur :

   ```powershell
   python manage.py migrate
   python manage.py runserver 127.0.0.1:8000
   ```

6. Ouvrir <http://127.0.0.1:8000/>. Sous Windows, `Lancer_appli.bat` peut aussi
   démarrer le serveur et ouvrir le navigateur; il utilise le chemin `.venv`
   dans le dossier du projet.

## Vérifications

Exécuter les tests de l'application et la vérification Django :

```powershell
python manage.py test chat
python manage.py check
```
