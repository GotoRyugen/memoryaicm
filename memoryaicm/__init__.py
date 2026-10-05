"""memoryaicm — mémoire tri-couche pour agents LLM.

Invariants (voir README) :
    Log ⊇ Index ⊇ Adapter        vérité → projection → cache compilé
    oubli = désindexer            jamais détruire (journal append-only)
    ∀ écriture ∈ Log              datée · attribuée · rejouable · chaînée
    adhésion 100 % ∈ gardes       jamais dans le modèle seul
"""

from .config import Settings
from .model import Fact, Kind, Src, Event
from .log import Journal
from .index import Index
from .agent import MemoryAgent

__all__ = ["Settings", "Fact", "Kind", "Src", "Event", "Journal", "Index", "MemoryAgent"]
__version__ = "0.6.0"
