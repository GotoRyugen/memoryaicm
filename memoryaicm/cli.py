"""CLI : python -m memoryaicm <commande>

  init                         crée le dossier de données, affiche le backend
  chat [--session S]           conversation (commandes : oublie X · sommeil · /index · /journal Q · /revue · /quit)
  say "texte" [--session S]    un seul tour
  ingest FICHIER [--source S]  contenu externe = donnée (quarantaine si instruction)
  forget "x"                   désindexe (événement, cascade, vue rédigée, adaptateur recompilé)
  recall FACT_ID               réactive un fait désindexé
  sleep                        consolidation + compilation + tests → promote | rollback
  review [FACT_ID --approve|--reject]   file de validation des faits sensibles
  show index|log|adapter|status [--all]
  search "mots"                plein texte sur tout le journal (y compris le désindexé)
  rebuild                      reconstruit l'index depuis le journal (Log ⊇ Index)
  verify                       vérifie la chaîne de hachage
  bench                        lance les suites (rappel, injection, lignage, journal) sans promouvoir
  serve [--port N]             service local JSON (127.0.0.1) + sommeil automatique sur inactivité
  mcp [--selftest]             add-on Claude : serveur MCP sur stdio (Claude Desktop, Cowork, Claude Code)
  hook prompt                  hook Claude Code UserPromptSubmit : mémorise le message, imprime les notes (contexte auto)
  model approve|check|list|revoke [CHEMIN]   approbation SHA-256 des modèles locaux (règle Echo-Core)
  install [--write] [--hook]   configuration Claude Desktop / Claude Code, quel que soit le lanceur (paquet, venv ou archive zip)
  tools [--format mcp|openai|anthropic|gemini|ollama|markdown]   schémas des outils mémoire pour n'importe quel LLM
  protocol [--text]            consigne système universelle (avec le protocole texte pour un modèle sans outils)
  agent [--provider openai|anthropic|text] [--base-url U] [--model M] [--say "…"]
                               n'importe quel LLM pilote la mémoire par ses outils (boucle d'outils générique)
  export [--out FICHIER]       RGPD · droit d'accès et portabilité : tout le journal en JSONL revérifiable
  backup --out FICHIER         sauvegarde chiffrée du journal (AES-256-GCM, clé propre au dossier : vault.key)
  restore FICHIER [--key K]    restaure une sauvegarde dans un dossier mémoire vide, vérifie la chaîne, reconstruit l'index
  erase --yes                  RGPD · droit à l'effacement : clé détruite, fichiers écrasés et supprimés, pierre tombale

Autonomie : au démarrage, chaîne vérifiée et index reconstruit si besoin ; après N tours (25) le système
dort seul ; à l'ouverture d'une session, s'il n'a pas dormi depuis 6 h, il dort d'abord.
Option globale : --home CHEMIN (ou MEMORYAICM_HOME),
                 --backend auto|stub|anthropic|openai|llamacpp|ollama|callable:module:fn|callable:http://…
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import model as M
from .agent import MemoryAgent, PolicyRefused
from .bench import run_all
from .config import Settings, load_dotenv
from .log import Journal


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows : console cp1252 par défaut
        except Exception:
            pass


def _settings(args) -> Settings:
    load_dotenv()
    s = Settings()
    if args.home:
        s.root = Path(args.home)
    if args.backend:
        s.backend = args.backend
    return s


def _agent(args) -> MemoryAgent:
    return MemoryAgent(_settings(args))


def _print_turn(res) -> None:
    print(f"assistant> {res.answer}")
    if res.sleep and res.level not in ("sleep",):
        print(f"   [sommeil automatique : {res.sleep.summary()}]")
    meta = [f"niveau={res.level}"]
    if res.level in ("model", "abstain"):
        meta.append(f"accord={res.agreement:.2f}")
    if res.used:
        meta.append("notes=" + ",".join(f.id for f in res.used))
    if res.guard and res.guard.fixes:
        meta.append("garde=" + "; ".join(res.guard.fixes))
    if res.guard and not res.guard.ok:
        meta.append("BLOQUÉ=" + "; ".join(res.guard.reasons))
    print(f"   [{' · '.join(meta)}]")
    print(f"   [mémoire : {res.write.summary()}]")


def cmd_init(args) -> None:
    a = _agent(args)
    print(json.dumps(a.status(), ensure_ascii=False, indent=2))
    print(f"données : {a.s.root.resolve()}")


def cmd_chat(args) -> None:
    a = _agent(args)
    sid = a.start_session(args.session)
    print(f"session {sid} · backend {a.backend.name} · tape /quit pour sortir")
    while True:
        try:
            line = input("vous> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line:
            continue
        if line in ("/quit", "/q", "/exit"):
            break
        if line == "/index":
            _show_index(a, show_all=False); continue
        if line.startswith("/journal"):
            q = line[len("/journal"):].strip()
            _show_log(a, q); continue
        if line == "/revue":
            _show_reviews(a); continue
        if line.startswith("/externe "):
            p = Path(line[len("/externe "):].strip())
            content = p.read_text(encoding="utf-8") if p.exists() else line[len("/externe "):]
            quarantined = a.ingest_external(sid, p.name if p.exists() else "texte", content)
            print(f"   [externe ingéré comme donnée{' — QUARANTAINE : instruction détectée' if quarantined else ''}]")
            continue
        if line == "/status":
            print(json.dumps(a.status(), ensure_ascii=False, indent=2)); continue
        _print_turn(a.turn(line, sid))


def cmd_say(args) -> None:
    a = _agent(args)
    sid = args.session or a.start_session()
    _print_turn(a.turn(args.text, sid))


def cmd_ingest(args) -> None:
    a = _agent(args)
    sid = args.session or a.start_session()
    p = Path(args.file)
    content = p.read_text(encoding="utf-8")
    q = a.ingest_external(sid, args.source or p.name, content)
    print("ingéré comme donnée" + (" — QUARANTAINE : instruction mémoire détectée, rien n'est écrit" if q else ""))


def cmd_forget(args) -> None:
    a = _agent(args)
    try:
        print(a.forget(args.query, session=args.session or "").summary())
    except PolicyRefused as e:
        print(e); sys.exit(3)


def cmd_recall(args) -> None:
    a = _agent(args)
    a.reactivate(args.fact_id)
    print("réactivé")


def cmd_sleep(args) -> None:
    a = _agent(args)
    try:
        rep = a.sleep()
    except PolicyRefused as e:
        print(e); sys.exit(3)
    print(rep.summary())
    for s in rep.tests.get("suites", []):
        print(f"  - {s['name']}: {'OK' if s['pass'] else 'ÉCHEC'}")


def cmd_review(args) -> None:
    a = _agent(args)
    if args.fact_id:
        a.review(args.fact_id, approve=bool(args.approve) and not args.reject)
        print("validé" if args.approve and not args.reject else "rejeté")
    else:
        _show_reviews(a)


def cmd_show(args) -> None:
    a = _agent(args)
    if args.what == "index":
        _show_index(a, show_all=args.all)
    elif args.what == "log":
        _show_log(a, "")
    elif args.what == "adapter":
        v = a.adapter.current()
        print(f"versions : {a.adapter.versions()} · promue : {v}")
        if v is not None:
            print(a.adapter.profile())
    else:
        print(json.dumps(a.status(), ensure_ascii=False, indent=2))


def cmd_search(args) -> None:
    a = _agent(args)
    for ev in a.journal.search(args.query):
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ev.ts))
        print(f"{ev.seq:5d} {ts} {ev.actor:9s} {ev.type:18s} {json.dumps(ev.payload, ensure_ascii=False)[:140]}")


def cmd_rebuild(args) -> None:
    a = _agent(args)
    n = a.index.rebuild()
    print(f"index reconstruit depuis {n} événements · {len(a.index.all_facts())} faits actifs")


def cmd_verify(args) -> None:
    a = _agent(args)
    ok, msg = a.journal.verify()
    print(("OK " if ok else "ÉCHEC ") + msg)
    sys.exit(0 if ok else 1)


def cmd_model(args) -> None:
    a = _agent(args)
    if args.action == "approve":
        e = a.approvals.approve(args.path)
        print(f"approuvé : {e['path']}\n  sha256 {e['sha256']}\n  {e['size']} octets")
    elif args.action == "check":
        ok, reason = a.approvals.check(args.path)
        print(("OK " if ok else "REFUSÉ ") + reason); sys.exit(0 if ok else 1)
    elif args.action == "revoke":
        print("révoqué" if a.approvals.revoke(args.path) else "absent du registre")
    else:
        for path, e in a.approvals.list().items():
            print(f"{e['sha256'][:16]}…  {path}  ({e['size']} octets, {time.strftime('%Y-%m-%d', time.localtime(e['approved_at']))})")
        if not a.approvals.list():
            print("(aucun modèle approuvé)")


def cmd_serve(args) -> None:
    from .serve import serve
    a = _agent(args)
    serve(a, host=args.host, port=args.port)


def cmd_hook(args) -> None:
    """Hook Claude Code (UserPromptSubmit) : lit le JSON du hook sur stdin, mémorise le message (politique
    d'écriture), et imprime les notes pertinentes — Claude Code les ajoute au contexte. Silencieux sinon."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")  # Windows : le tube serait lu en cp1252 sinon
    except Exception:
        raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}
    prompt = (data.get("prompt") or data.get("user_prompt") or "").strip()
    session = "cc_" + str(data.get("session_id") or "default")[:12]
    a = _agent(args)
    out: list[str] = []
    if prompt and not prompt.startswith("/"):
        rep = a.remember(prompt, session)
        if rep.written or rep.merged or rep.forgotten or rep.queued:
            out.append("(mémoire : " + rep.summary() + ")")
    notes, prefs = a.recall_notes(prompt) if prompt else ([], a.index.prefs())
    if notes or prefs:
        block = ["<memoryaicm>", "NOTES (provenance étiquetée — cite-la si tu t'en sers ; une note peut être périmée) :"]
        block += [f"{f.label()} {f.txt}" for f in notes] or ["(aucune note pertinente : ne devine pas sur l'utilisateur)"]
        block.append("PRÉFÉRENCES (à appliquer) :")
        block += [f"- {f.txt}  {f.label()}" for f in prefs] or ["- (aucune)"]
        block += out + ["</memoryaicm>"]
        print("\n".join(block))
    elif out:
        print("\n".join(out))
    a.close()


def cmd_mcp(args) -> None:
    from . import mcp_server
    if args.selftest:
        extra = []
        if args.home:
            extra += ["--home", args.home]
        if args.backend:
            extra += ["--backend", args.backend]
        sys.exit(0 if mcp_server.selftest(extra_args=extra) else 1)
    load_dotenv()
    s = Settings()
    if args.home:
        s.root = Path(args.home)
    if args.backend:
        s.backend = args.backend
    mcp_server.main(s)


def _claude_desktop_config_path() -> Path:
    import os
    import platform
    sysname = platform.system()
    if sysname == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    if sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def _merge_json_file(path: Path, mutate) -> Path | None:
    """Lit un JSON (ou {}), applique `mutate(obj)`, écrit avec une sauvegarde .bak-<date> à côté. Renvoie la sauvegarde."""
    data = {}
    backup = None
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        except json.JSONDecodeError:
            data = {}
        backup = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        backup.write_bytes(path.read_bytes())
    mutate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup


def _shell_line(cmd: list[str]) -> str:
    """Ligne de commande lisible par Bash (Git Bash, celui des hooks Claude Code sous Windows), PowerShell et cmd :
    chaque argument entre guillemets, chemins Windows en barres obliques (Bash mange les antislashs hors guillemets,
    et C:/Users/... est accepté partout sous Windows)."""
    out = []
    for c in cmd:
        if len(c) > 2 and c[1] == ":" and c[0].isalpha() and "\\" in c:
            c = c.replace("\\", "/")
        out.append('"' + c.replace('"', '\\"') + '"' if (" " in c or "/" in c or "\\" in c or '"' in c) else c)
    return " ".join(out)


def cmd_install(args) -> None:
    """Configuration de l'add-on Claude, portable : affiche (ou écrit avec --write) l'entrée MCP de Claude Desktop,
    et le hook UserPromptSubmit de Claude Code (--hook). Fonctionne depuis le paquet, un venv ou l'archive zip."""
    from .mcp_server import launcher
    load_dotenv()
    home = Path(args.home) if args.home else Settings().root
    home = home.resolve()
    base = launcher()
    mcp_cmd = [*base, "--home", str(home), "mcp"]
    hook_cmd = [*base, "--home", str(home), "hook", "prompt"]
    entry = {"command": mcp_cmd[0], "args": mcp_cmd[1:]}
    cfg_path = _claude_desktop_config_path()
    print("# Claude Desktop / Cowork —", cfg_path)
    print(json.dumps({"mcpServers": {"memoryaicm": entry}}, ensure_ascii=False, indent=2))
    quoted = _shell_line(mcp_cmd)
    print("\n# Claude Code (serveur MCP, portée utilisateur) :")
    print(f"claude mcp add --scope user memoryaicm -- {quoted}")
    hook_line = _shell_line(hook_cmd)
    print("\n# Claude Code (hook UserPromptSubmit, ~/.claude/settings.json) :")
    print(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": hook_line, "timeout": 20}]}]}}, ensure_ascii=False, indent=2))
    if not (args.write or args.hook):
        print("\n(ajoute --write pour écrire la configuration Claude Desktop, --hook pour le hook Claude Code)")
        return
    if args.write:
        def mut(d):
            d.setdefault("mcpServers", {})["memoryaicm"] = entry
        bak = _merge_json_file(cfg_path, mut)
        print(f"\nécrit : {cfg_path}" + (f" (sauvegarde {bak.name})" if bak else "") + " — redémarre Claude Desktop")
    if args.hook:
        settings_path = Path.home() / ".claude" / "settings.json"

        def mut_hook(d):
            hooks = d.setdefault("hooks", {})
            lst = [h for h in hooks.get("UserPromptSubmit", []) if "memoryaicm" not in json.dumps(h)]
            lst.append({"hooks": [{"type": "command", "command": hook_line, "timeout": 20}]})
            hooks["UserPromptSubmit"] = lst
        bak = _merge_json_file(settings_path, mut_hook)
        print(f"écrit : {settings_path}" + (f" (sauvegarde {bak.name})" if bak else ""))
    home.mkdir(parents=True, exist_ok=True)


def cmd_tools(args) -> None:
    from .tools import schemas
    out = schemas(args.format)
    print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=2))


def cmd_protocol(args) -> None:
    from .tools import system_prompt
    print(system_prompt(text_protocol=bool(args.text)))


def cmd_agent(args) -> None:
    """Boucle d'outils : le LLM (n'importe lequel) génère, appelle les outils mémoire, la politique décide."""
    from .toolloop import make_loop
    a = _agent(args)
    kw = {"session": args.session}
    if args.provider in ("openai", "anthropic"):
        if args.base_url:
            kw["base_url"] = args.base_url
        if args.model:
            kw["model"] = args.model
        if args.api_key:
            kw["api_key"] = args.api_key
    else:  # text : le modèle de chat est le backend configuré (Ollama, llamacpp, openai, callable…) sans appel d'outils natif
        backend = a.backend
        if not hasattr(backend, "_call"):
            print(f"le backend « {backend.name} » ne sait pas converser : configure ollama, llamacpp, openai, anthropic ou callable:…")
            sys.exit(2)
        kw["chat"] = lambda system, messages: backend._call(system, messages)
    loop = make_loop(a, args.provider, **kw)

    def show(answer: str) -> None:
        for name, arguments, result in loop.calls:
            print(f"   [outil {name} {json.dumps(arguments, ensure_ascii=False)[:100]} → {result.splitlines()[0][:100] if result else ''}]")
        print(f"assistant> {answer}")

    if args.say:
        show(loop.ask(args.say))
        loop.close(); return
    print(f"agent · fournisseur {args.provider} · backend mémoire {a.backend.name} · /quit pour sortir")
    while True:
        try:
            line = input("vous> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line:
            continue
        if line in ("/quit", "/q", "/exit"):
            break
        try:
            show(loop.ask(line))
        except Exception as e:
            print(f"   [erreur : {e}]")
    loop.close()


def cmd_export(args) -> None:
    from . import privacy
    s = _settings(args)
    out = Path(args.out) if args.out else Path(f"memoryaicm-export-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    j = Journal(s.journal_path)
    try:
        r = privacy.export_jsonl(j, out)
    finally:
        j.close()
    print(f"export : {r['events']} événements → {r['path']} ({r['bytes']} octets)")


def cmd_backup(args) -> None:
    from . import privacy
    r = privacy.backup(_settings(args), Path(args.out))
    print(f"sauvegarde chiffrée : {r['path']} ({r['bytes']} octets) — clé : {r['key']} (à garder hors de la sauvegarde)")


def cmd_restore(args) -> None:
    from . import privacy
    s = _settings(args)
    key = bytes.fromhex(args.key) if args.key else None
    r = privacy.restore(s, Path(args.file), key=key)
    print(f"restauré : {r['events']} événements — {r['verify']}")
    a = MemoryAgent(s)
    try:
        a.index.rebuild()
        print(f"index reconstruit : {len(a.index.all_facts(on_only=True))} faits actifs")
    finally:
        a.close()


def cmd_erase(args) -> None:
    from . import privacy
    s = _settings(args)
    try:
        r = privacy.erase(s, confirm=args.yes)
    except privacy.PrivacyError as e:
        print(f"refusé : {e}")
        sys.exit(2)
    if not r["existed"]:
        print(f"rien à effacer : {r['root']} n'existe pas")
        return
    print(f"effacé : {r['files']} fichiers, {r['bytes']} octets écrasés — {r['root']} — pierre tombale ERASED ({r['erased_at']})")
    if r["locked"]:
        print("fichiers verrouillés (fermer Claude Desktop / le serveur, puis relancer erase --yes) :")
        for f in r["locked"]:
            print("  " + f)
        sys.exit(1)


def cmd_bench(args) -> None:
    a = _agent(args)
    ok, rep = run_all(a.journal, a.index, a.backend, a.s)
    for s in rep["suites"]:
        print(f"{s['name']:10s} {'OK' if s['pass'] else 'ÉCHEC'}  {json.dumps({k: v for k, v in s.items() if k not in ('name', 'pass', 'checks')}, ensure_ascii=False)}")
    sys.exit(0 if ok else 1)


def _show_index(a: MemoryAgent, show_all: bool) -> None:
    facts = a.index.all_facts(on_only=not show_all)
    if not facts:
        print("(index vide)"); return
    now = time.time()
    for f in facts:
        act = a.index.activation(f.id, now)
        flag = "on " if f.on else "off"
        print(f"{flag} {f.id} {f.kind.value:4s} v{f.ver} imp={f.imp:.2f} act={act:6.2f} [{f.subject}] {f.txt}"
              + (f"  deps={f.deps}" if f.deps else "") + (f"  ({a.index.get(f.id)['off_reason']})" if not f.on else ""))


def _show_log(a: MemoryAgent, query: str) -> None:
    evs = a.journal.search(query) if query else list(a.journal.replay())[-40:]
    for ev in evs:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.ts))
        print(f"{ev.seq:5d} {ts} {ev.actor:9s} {ev.type:18s} {json.dumps(ev.payload, ensure_ascii=False)[:120]}")


def _show_reviews(a: MemoryAgent) -> None:
    pend = a.index.pending_reviews()
    if not pend:
        print("(aucun fait en attente)"); return
    for f in pend:
        print(f"{f.id} [{f.subject}] {f.txt}   → review {f.id} --approve | --reject")


def main(argv: list[str] | None = None) -> None:
    _utf8_console()
    p = argparse.ArgumentParser(prog="memoryaicm", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", help="dossier de données (défaut : ./data ou MEMORYAICM_HOME)")
    p.add_argument("--backend", help="backend LLM : auto|stub|anthropic|openai|llamacpp|ollama|callable:module:fn|callable:http://…")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    c = sub.add_parser("chat"); c.add_argument("--session"); c.set_defaults(fn=cmd_chat)
    c = sub.add_parser("say"); c.add_argument("text"); c.add_argument("--session"); c.set_defaults(fn=cmd_say)
    c = sub.add_parser("ingest"); c.add_argument("file"); c.add_argument("--source"); c.add_argument("--session"); c.set_defaults(fn=cmd_ingest)
    c = sub.add_parser("forget"); c.add_argument("query"); c.add_argument("--session"); c.set_defaults(fn=cmd_forget)
    c = sub.add_parser("recall"); c.add_argument("fact_id"); c.set_defaults(fn=cmd_recall)
    sub.add_parser("sleep").set_defaults(fn=cmd_sleep)
    c = sub.add_parser("review"); c.add_argument("fact_id", nargs="?"); c.add_argument("--approve", action="store_true"); c.add_argument("--reject", action="store_true"); c.set_defaults(fn=cmd_review)
    c = sub.add_parser("show"); c.add_argument("what", choices=["index", "log", "adapter", "status"]); c.add_argument("--all", action="store_true"); c.set_defaults(fn=cmd_show)
    c = sub.add_parser("search"); c.add_argument("query"); c.set_defaults(fn=cmd_search)
    sub.add_parser("rebuild").set_defaults(fn=cmd_rebuild)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sub.add_parser("bench").set_defaults(fn=cmd_bench)
    c = sub.add_parser("export", help="RGPD : droit d'accès et portabilité (JSONL revérifiable)"); c.add_argument("--out"); c.set_defaults(fn=cmd_export)
    c = sub.add_parser("backup", help="sauvegarde chiffrée du journal (AES-256-GCM)"); c.add_argument("--out", required=True); c.set_defaults(fn=cmd_backup)
    c = sub.add_parser("restore", help="restaure une sauvegarde chiffrée dans un dossier vide"); c.add_argument("file"); c.add_argument("--key", help="clé en hexadécimal si vault.key n'est pas dans le dossier cible"); c.set_defaults(fn=cmd_restore)
    c = sub.add_parser("erase", help="RGPD : droit à l'effacement (clé détruite, fichiers écrasés)"); c.add_argument("--yes", action="store_true", help="confirme l'effacement définitif"); c.set_defaults(fn=cmd_erase)
    c = sub.add_parser("serve"); c.add_argument("--host"); c.add_argument("--port", type=int); c.set_defaults(fn=cmd_serve)
    c = sub.add_parser("mcp"); c.add_argument("--selftest", action="store_true"); c.set_defaults(fn=cmd_mcp)
    c = sub.add_parser("hook"); c.add_argument("what", choices=["prompt"]); c.set_defaults(fn=cmd_hook)
    c = sub.add_parser("model"); c.add_argument("action", choices=["approve", "check", "list", "revoke"]); c.add_argument("path", nargs="?"); c.set_defaults(fn=cmd_model)
    c = sub.add_parser("install"); c.add_argument("--write", action="store_true", help="écrit l'entrée MCP dans claude_desktop_config.json (sauvegarde à côté)")
    c.add_argument("--hook", action="store_true", help="écrit le hook UserPromptSubmit dans ~/.claude/settings.json"); c.set_defaults(fn=cmd_install)
    c = sub.add_parser("tools"); c.add_argument("--format", default="mcp", choices=["mcp", "openai", "anthropic", "gemini", "ollama", "markdown"]); c.set_defaults(fn=cmd_tools)
    c = sub.add_parser("protocol"); c.add_argument("--text", action="store_true", help="ajoute le protocole texte (modèle sans appel d'outils)"); c.set_defaults(fn=cmd_protocol)
    c = sub.add_parser("agent"); c.add_argument("--provider", default="openai", choices=["openai", "anthropic", "text"])
    c.add_argument("--base-url"); c.add_argument("--model"); c.add_argument("--api-key"); c.add_argument("--session"); c.add_argument("--say")
    c.set_defaults(fn=cmd_agent)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
