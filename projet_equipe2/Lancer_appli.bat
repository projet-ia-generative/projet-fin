@echo off
cd /d "%USERPROFILE%\OneDrive - Université du Québec à Chicoutimi\cours_et_projets\automne_2026\IA-generative\projet_equipe"
start "" http://127.0.0.1:8000/
".venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8000
pause
