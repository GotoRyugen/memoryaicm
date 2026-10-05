@echo off
rem Lance le banc mémoire phase B (llama3). Le script Python ferme lui-même toute
rem ancienne instance (par ligne de commande, jamais Echos qui est pythonw.exe).
start "" /min python -u "%~dp0banc_locomo_phaseB.py"
