"""Point d'entrée : `python -m memoryaicm …`, `python chemin/memoryaicm/__main__.py …` ou `python memoryaicm_neutral.zip …`."""

if __package__ in (None, ""):  # lancé par chemin de fichier : rendre le paquet importable
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from memoryaicm.cli import main
else:
    from .cli import main

main()
