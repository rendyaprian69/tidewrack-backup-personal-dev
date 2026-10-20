#!/usr/bin/env python3
"""Validate Tidewrack dialogue graphs without needing the Godot engine.

Graph checks, for every data/dialogue/*.json file:
  - it parses as a JSON object of node_id -> node
  - each node is either a linear node ("next": id|null) or a choice node
    ("choices": [{text, next, set_flag?}]) — not neither, not both
  - every referenced target id exists
  - at least one terminal (next == null) is reachable from an entry point
  - reports orphan nodes (unreachable from any known entry) as warnings

Entry points are discovered from scripts/ (every `dialogue_start` literal,
every start id passed to `_add_interactable(...)` or `DialogueManager.start(...)`)
plus the fallback set below, so a new scene cannot silently orphan its graph.

Flag report (the "flag report" named in the production risk log):
  - every flag set by a dialogue node or choice is listed in the canonical
    story-flag table in docs/narrative-bible.md                     -> ERROR
  - every flag the game code reads via get_flag(...) is set somewhere
    (by dialogue or by code); a read-only flag is a dead gate        -> ERROR
  - every flag set anywhere is read somewhere                        -> warning
  - every canonical flag in the bible is set by at least one graph   -> warning
  - the full flag table (where set, where read, canonical or not) is printed

Exit code 0 = all valid, 1 = at least one error.

Usage:  python3 tests/validate_dialogue.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIALOGUE_DIR = ROOT / "data" / "dialogue"
SCRIPTS_DIR = ROOT / "scripts"
BIBLE = ROOT / "docs" / "narrative-bible.md"

# Fallback entry-point ids, used in addition to those discovered in scripts/.
KNOWN_ENTRIES = {"logbook", "radio", "door", "start"}

# Journal graphs (scripts/journal.gd) reuse the dialogue format and live under
# data/dialogue/ with "entry_*" node ids; they are held to the same rules.
JOURNAL_ENTRY_PREFIX = "entry_"


# --- helpers -----------------------------------------------------------------

def is_journal_graph(graph: dict) -> bool:
    return any(str(k).startswith(JOURNAL_ENTRY_PREFIX) for k in graph)


def gd_files() -> list[Path]:
    return sorted(SCRIPTS_DIR.rglob("*.gd")) if SCRIPTS_DIR.is_dir() else []


def discover_entries() -> set[str]:
    """Collect every dialogue start id the game code can pass to DialogueManager."""
    entries = set(KNOWN_ENTRIES)
    for path in gd_files():
        text = path.read_text()
        entries.update(re.findall(r'dialogue_start\s*=\s*"(\w+)"', text))
        entries.update(re.findall(r'DialogueManager\.start\(\s*[^,()]+,\s*"(\w+)"', text))
        # _add_interactable(label, prompt, start_id, pos, color): the third string.
        for call in re.finditer(r"_add_interactable\(((?:[^()]|\([^()]*\))*)\)", text):
            strings = re.findall(r'"([^"]*)"', call.group(1))
            if len(strings) >= 3:
                entries.add(strings[2])
    return entries


def canonical_flags() -> set[str] | None:
    """Flag names from the 'Story flags (canonical)' table in the narrative bible.

    Returns None when the table cannot be found, so the caller can warn rather
    than fail every flag.
    """
    if not BIBLE.is_file():
        return None
    text = BIBLE.read_text()
    m = re.search(r"^##\s+Story flags.*?$", text, re.M)
    if not m:
        return None
    section = text[m.end():]
    nxt = re.search(r"^##\s", section, re.M)
    if nxt:
        section = section[: nxt.start()]
    flags = set(re.findall(r"^\|\s*`([A-Za-z0-9_]+)`\s*\|", section, re.M))
    return flags or None


def code_flag_refs() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Flags read (get_flag) and written (set_flag) by GDScript, with locations."""
    reads: dict[str, list[str]] = {}
    writes: dict[str, list[str]] = {}
    for path in gd_files():
        rel = path.relative_to(ROOT)
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for name in re.findall(r'get_flag\(\s*"(\w+)"', line):
                reads.setdefault(name, []).append(f"{rel}:{lineno}")
            for name in re.findall(r'set_flag\(\s*"(\w+)"', line):
                writes.setdefault(name, []).append(f"{rel}:{lineno}")
    return reads, writes


def dialogue_flag_sets(graphs: dict[str, dict]) -> dict[str, list[str]]:
    """Flags set by dialogue nodes and choices, with file:node locations."""
    sets: dict[str, list[str]] = {}
    for fname, graph in graphs.items():
        for nid, node in graph.items():
            if not isinstance(node, dict):
                continue
            for name in (node.get("set_flag") or {}):
                sets.setdefault(str(name), []).append(f"{fname}:{nid}")
            for i, choice in enumerate(node.get("choices") or []):
                if isinstance(choice, dict):
                    for name in (choice.get("set_flag") or {}):
                        sets.setdefault(str(name), []).append(f"{fname}:{nid}#{i}")
    return sets


# --- graph validation ----------------------------------------------------------

def reachable(graph: dict, entries: set[str]) -> set[str]:
    seen: set[str] = set()
    stack = [e for e in entries if e in graph]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        node = graph[nid]
        targets: list[str] = []
        if isinstance(node.get("choices"), list):
            targets += [c.get("next") for c in node["choices"] if isinstance(c, dict)]
        if "next" in node:
            targets.append(node.get("next"))
        for t in targets:
            if isinstance(t, str) and t in graph:
                stack.append(t)
    return seen


def validate_file(path: Path, known_entries: set[str]) -> tuple[list[str], list[str], dict | None]:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        graph = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"{path.name}: invalid JSON — {exc}"], warnings, None

    if not isinstance(graph, dict):
        return [f"{path.name}: top level must be an object of node_id -> node"], warnings, None

    has_terminal = False
    for nid, node in graph.items():
        if not isinstance(node, dict):
            errors.append(f"{path.name}:{nid}: node must be an object")
            continue

        has_choices = isinstance(node.get("choices"), list) and len(node["choices"]) > 0
        has_next = "next" in node

        if not has_choices and not has_next:
            errors.append(f"{path.name}:{nid}: node has neither 'next' nor 'choices'")
        if has_choices and has_next:
            errors.append(f"{path.name}:{nid}: node has both 'next' and 'choices' (ambiguous)")

        if has_next and node.get("next") is None:
            has_terminal = True

        if has_next and isinstance(node.get("next"), str) and node["next"] not in graph:
            errors.append(f"{path.name}:{nid}: 'next' points to missing node '{node['next']}'")

        if "set_flag" in node and not isinstance(node["set_flag"], dict):
            errors.append(f"{path.name}:{nid}: 'set_flag' must be an object of flag -> value")

        if has_choices:
            for i, choice in enumerate(node["choices"]):
                if not isinstance(choice, dict) or "text" not in choice:
                    errors.append(f"{path.name}:{nid}: choice #{i} needs a 'text' field")
                    continue
                target = choice.get("next")
                if target is not None and (not isinstance(target, str) or target not in graph):
                    errors.append(f"{path.name}:{nid}: choice #{i} 'next' -> missing node '{target}'")
                if target is None:
                    has_terminal = True
                if "set_flag" in choice and not isinstance(choice["set_flag"], dict):
                    errors.append(f"{path.name}:{nid}: choice #{i} 'set_flag' must be an object")

    if not has_terminal:
        errors.append(f"{path.name}: no terminal node (a node with next == null) — the graph never ends")

    entries = known_entries & set(graph.keys())
    if is_journal_graph(graph):
        entries |= {k for k in graph if str(k).startswith(JOURNAL_ENTRY_PREFIX)}
    if not entries:
        warnings.append(f"{path.name}: no known entry point present (known: {sorted(known_entries)})")
    else:
        seen = reachable(graph, entries)
        for orphan in sorted(set(graph) - seen):
            warnings.append(f"{path.name}:{orphan}: unreachable from entry points {sorted(entries)}")

    return errors, warnings, graph


# --- flag report -----------------------------------------------------------------

def flag_report(graphs: dict[str, dict]) -> tuple[list[str], list[str], list[str]]:
    """Cross-check flags between dialogue data, game code and the narrative bible."""
    errors: list[str] = []
    warnings: list[str] = []
    lines: list[str] = []

    dialogue_sets = dialogue_flag_sets(graphs)
    code_reads, code_writes = code_flag_refs()
    canon = canonical_flags()
    if canon is None:
        warnings.append(f"flags: no 'Story flags (canonical)' table found in {BIBLE.relative_to(ROOT)}; canonical check skipped")
        canon = set()

    all_set = set(dialogue_sets) | set(code_writes)
    all_flags = sorted(all_set | set(code_reads) | canon)

    for name in sorted(dialogue_sets):
        if canon and name not in canon:
            errors.append(f"flags: '{name}' is set by {', '.join(dialogue_sets[name])} but is not in the canonical table in narrative-bible.md")
    for name in sorted(code_reads):
        if name not in all_set:
            errors.append(f"flags: '{name}' is read at {', '.join(code_reads[name])} but nothing sets it — a dead gate")
    for name in sorted(all_set):
        if name not in code_reads:
            warnings.append(f"flags: '{name}' is set ({', '.join(dialogue_sets.get(name, []) + code_writes.get(name, []))}) but never read by any script")
    for name in sorted(canon):
        if name not in dialogue_sets:
            warnings.append(f"flags: canonical flag '{name}' is not set by any dialogue graph")

    width = max((len(f) for f in all_flags), default=4)
    lines.append(f"  {'flag':<{width}}  canon  set by                                   read by")
    for name in all_flags:
        setters = dialogue_sets.get(name, []) + code_writes.get(name, [])
        readers = code_reads.get(name, [])
        canon_mark = "yes" if name in canon else ("no " if canon else " ? ")
        lines.append(f"  {name:<{width}}  {canon_mark}    {', '.join(setters) or '—':<40} {', '.join(readers) or '—'}")
    return errors, warnings, lines


# --- main --------------------------------------------------------------------------

def main() -> int:
    files = sorted(DIALOGUE_DIR.glob("*.json"))
    if not files:
        print(f"No dialogue files found in {DIALOGUE_DIR}")
        return 1

    known_entries = discover_entries()
    all_errors: list[str] = []
    all_warnings: list[str] = []
    graphs: dict[str, dict] = {}
    for path in files:
        errs, warns, graph = validate_file(path, known_entries)
        all_errors += errs
        all_warnings += warns
        status = "OK" if not errs else "FAIL"
        count = f"{len(graph)} nodes" if graph is not None else "unparsed"
        kind = " [journal]" if graph is not None and is_journal_graph(graph) else ""
        print(f"[{status}] {path.relative_to(ROOT)}  ({count}){kind}")
        if graph is not None:
            graphs[path.name] = graph

    ferrs, fwarns, table = flag_report(graphs)
    all_errors += ferrs
    all_warnings += fwarns

    print()
    print("Flag report")
    for line in table:
        print(line)

    print()
    for w in all_warnings:
        print(f"  warning: {w}")
    for e in all_errors:
        print(f"  ERROR:   {e}")

    print()
    if all_errors:
        print(f"✗ {len(all_errors)} error(s), {len(all_warnings)} warning(s)")
        return 1
    print(f"✓ all dialogue graphs valid ({len(all_warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
