#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import difflib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable
import argparse
import sys
import time
from collections import Counter




class WorkToolError(RuntimeError):
    pass


MAX_CONTEXT_CHARS = int(os.environ.get("KEROSENE_WORK_TOOLS_MAX_CONTEXT_CHARS", "120000"))
SESSION_STATE_REL = Path(os.environ.get("KEROSENE_WORK_TOOLS_SESSION_STATE", "tmp/mcp/session_state.json"))
BLOCKED_NAMES = {".env", ".netrc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "wallet.dat"}
SAFE_ENV_EXAMPLES = {".env.dist", ".env.example", ".env.sample", ".env.template"}
BLOCKED_DIRS = {".git", ".gnupg", ".ssh", ".secrets", "secrets"}
BLOCKED_SUFFIXES = {".db", ".jks", ".key", ".keystore", ".mv.db", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3"}
BINARY_SUFFIXES = {".7z", ".apk", ".bin", ".class", ".dll", ".dylib", ".gif", ".gz", ".hprof", ".ico", ".jar", ".jpeg", ".jpg", ".keystore", ".mp3", ".mp4", ".otf", ".png", ".so", ".tar", ".ttf", ".webp", ".zip"}
EXCLUDED_DIRS = {".dart_tool", ".git", ".gradle", ".idea", ".mypy_cache", ".pytest_cache", ".qodana", ".venv", ".vscode", "__pycache__", "build", "coverage", "node_modules", "target", "venv"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# as_bool, clamp_int and is_relative_to are defined once in the canonical helper block below.


def rel(root: Path, path: Path) -> str:
    root = root.resolve()
    path = path.resolve(strict=False)
    if path == root:
        return "."
    return path.relative_to(root).as_posix()


def blocked_path(path: Path) -> bool:
    lower_parts = {part.lower() for part in path.parts}
    if lower_parts & BLOCKED_DIRS:
        return True
    name = path.name.lower()
    if name in SAFE_ENV_EXAMPLES:
        return False
    if name in BLOCKED_NAMES:
        return True
    if name.startswith(".env.") and name not in SAFE_ENV_EXAMPLES:
        return True
    return any(suffix.lower() in BLOCKED_SUFFIXES for suffix in path.suffixes)


def resolve_existing(root: Path, raw: Any) -> Path:
    value = "." if raw in (None, "") else str(raw)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        path = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise WorkToolError(f"Path does not exist: {value}") from exc
    if not is_relative_to(path, root):
        raise WorkToolError(f"Path is outside the project root: {value}")
    if blocked_path(path):
        raise WorkToolError(f"Refusing protected path: {rel(root, path)}")
    return path


def resolve_target(root: Path, raw: Any) -> Path:
    if raw in (None, ""):
        raise WorkToolError("Path is required")
    value = str(raw)
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        raise WorkToolError("Path must be relative to the project root")
    path = (root.resolve() / candidate).resolve(strict=False)
    if not is_relative_to(path, root):
        raise WorkToolError(f"Path is outside the project root: {value}")
    if blocked_path(path):
        raise WorkToolError(f"Refusing protected path: {rel(root, path)}")
    return path


def probably_binary(path: Path) -> bool:
    if any(suffix.lower() in BINARY_SUFFIXES for suffix in path.suffixes):
        return True
    try:
        return b"\x00" in path.read_bytes()[:4096]
    except OSError:
        return True


def read_text(path: Path) -> str:
    if path.is_dir():
        raise WorkToolError(f"Path is a directory: {path}")
    if probably_binary(path):
        raise WorkToolError(f"Refusing binary file: {path}")
    return path.read_text(encoding="utf-8")


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - 80)
    return text[:keep] + f"\n...[truncated {len(text) - keep} chars]"


def run(root: Path, command: list[str], *, cwd: Path | None = None, timeout_seconds: int = 120) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=str(cwd or root), env=os.environ.copy(), capture_output=True, text=True, timeout=timeout_seconds, check=False)
    return {"command": command, "cwd": rel(root, cwd or root), "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def git(root: Path, args: list[str], *, timeout_seconds: int = 120) -> dict[str, Any]:
    return run(root, ["git", *args], cwd=root, timeout_seconds=timeout_seconds)


def git_out(root: Path, args: list[str], *, timeout_seconds: int = 120) -> str:
    result = git(root, args, timeout_seconds=timeout_seconds)
    if result["returncode"] != 0:
        raise WorkToolError(result["stderr"] or result["stdout"] or "git command failed")
    return result["stdout"]


def diff_text(before: str, after: str, file_path: str, max_chars: int = 12000) -> str:
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=f"a/{file_path}", tofile=f"b/{file_path}"))
    return truncate(diff, max_chars)


def changed_files(root: Path) -> list[str]:
    out = git_out(root, ["status", "--short"], timeout_seconds=60)
    files: list[str] = []
    for line in out.splitlines():
        item = line[3:] if len(line) > 3 else line.strip()
        if " -> " in item:
            item = item.split(" -> ", 1)[1]
        if item.strip():
            files.append(item.strip())
    return files


def patch_paths(patch: str) -> list[str]:
    paths: set[str] = set()
    for line in patch.splitlines():
        if line.startswith(("+++ ", "--- ")):
            value = line[4:].strip().split("\t", 1)[0]
            if value != "/dev/null":
                paths.add(value[2:] if value.startswith(("a/", "b/")) else value)
        elif line.startswith("diff --git "):
            parts = line.split()
            for value in parts[2:4]:
                paths.add(value[2:] if value.startswith(("a/", "b/")) else value)
        elif line.startswith(("rename from ", "rename to ")):
            paths.add(line.split(" ", 2)[2].strip())
    return sorted(p for p in paths if p)


def apply_patch(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    patch = args.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        raise WorkToolError("patch must be a non-empty unified diff")
    dry_run = as_bool(args.get("dry_run"), False)
    timeout_seconds = clamp_int(args.get("timeout_seconds"), 120, 1, 1200)
    files = patch_paths(patch)
    for file_path in files:
        resolve_target(root, file_path)
    patch_dir = root / "tmp" / "mcp"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_file = patch_dir / "last_apply_patch.diff"
    patch_file.write_text(patch, encoding="utf-8")
    check = git(root, ["apply", "--check", "--whitespace=nowarn", str(patch_file)], timeout_seconds=timeout_seconds)
    if check["returncode"] != 0 or dry_run:
        return {"applied": False, "dry_run": dry_run, "files": files, "check": check}
    before = git_out(root, ["status", "--short"], timeout_seconds=60)
    applied = git(root, ["apply", "--whitespace=nowarn", str(patch_file)], timeout_seconds=timeout_seconds)
    after = git_out(root, ["status", "--short"], timeout_seconds=60)
    stat = git(root, ["diff", "--stat", "--", *files], timeout_seconds=60) if files else {"stdout": ""}
    return {"applied": applied["returncode"] == 0, "dry_run": False, "files": files, "apply": applied, "status_before": before, "status_after": after, "diffstat": stat.get("stdout", "")}


def multi_edit(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_existing(root, args.get("path"))
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise WorkToolError("edits must be a non-empty list")
    original = read_text(path)
    updated = original
    results = []
    for index, edit in enumerate(edits, 1):
        old = edit.get("old_text") if isinstance(edit, dict) else None
        new = edit.get("new_text") if isinstance(edit, dict) else None
        replace_all = as_bool(edit.get("replace_all") if isinstance(edit, dict) else None, True)
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise WorkToolError(f"invalid edit #{index}")
        count = updated.count(old)
        if count == 0:
            raise WorkToolError(f"edit #{index} found no occurrences")
        updated = updated.replace(old, new, -1 if replace_all else 1)
        results.append({"index": index, "occurrences": count, "replacements": count if replace_all else 1})
    file_path = rel(root, path)
    dry_run = as_bool(args.get("dry_run"), False)
    if not dry_run:
        path.write_text(updated, encoding="utf-8")
    return {"path": file_path, "dry_run": dry_run, "edits": results, "diff": diff_text(original, updated, file_path)}


def copy_tree(src: Path, dst: Path) -> None:
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            copy_tree(child, dst / child.name)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())


def copy_path(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    src = resolve_existing(root, args.get("source"))
    dst = resolve_target(root, args.get("destination"))
    overwrite = as_bool(args.get("overwrite"), False)
    dry_run = as_bool(args.get("dry_run"), False)
    if dst.exists() and not overwrite:
        raise WorkToolError(f"destination exists: {rel(root, dst)}")
    if dry_run:
        return {"copied": False, "dry_run": True, "source": rel(root, src), "destination": rel(root, dst)}
    if dst.exists():
        shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
    copy_tree(src, dst)
    return {"copied": True, "source": rel(root, src), "destination": rel(root, dst)}


def move_path(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    src = resolve_existing(root, args.get("source"))
    dst = resolve_target(root, args.get("destination"))
    overwrite = as_bool(args.get("overwrite"), False)
    dry_run = as_bool(args.get("dry_run"), False)
    if dst.exists() and not overwrite:
        raise WorkToolError(f"destination exists: {rel(root, dst)}")
    if dry_run:
        return {"moved": False, "dry_run": True, "source": rel(root, src), "destination": rel(root, dst)}
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
    shutil.move(str(src), str(dst))
    return {"moved": True, "source": rel(root, src), "destination": rel(root, dst)}


def mkdir(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_target(root, args.get("path"))
    dry_run = as_bool(args.get("dry_run"), False)
    if not dry_run:
        path.mkdir(parents=True, exist_ok=True)
    return {"created": not dry_run, "dry_run": dry_run, "path": rel(root, path)}


def delete_path_safe(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_existing(root, args.get("path"))
    dry_run = as_bool(args.get("dry_run"), True)
    recursive = as_bool(args.get("recursive"), False)
    max_entries = clamp_int(args.get("max_entries"), 200, 1, 10000)
    if path == root:
        raise WorkToolError("refusing to delete project root")
    entries = list(path.rglob("*")) if path.is_dir() else []
    if path.is_dir() and not recursive:
        raise WorkToolError("directory deletion requires recursive=true")
    if len(entries) > max_entries:
        raise WorkToolError(f"directory has {len(entries)} entries; max_entries={max_entries}")
    if dry_run:
        return {"deleted": False, "dry_run": True, "path": rel(root, path), "entries": len(entries)}
    shutil.rmtree(path) if path.is_dir() else path.unlink()
    return {"deleted": True, "dry_run": False, "path": rel(root, path), "entries": len(entries)}


def format_paths(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    raw_paths = args.get("paths") or changed_files(root)
    if not isinstance(raw_paths, list):
        raise WorkToolError("paths must be a list")
    paths = [rel(root, resolve_target(root, p)) for p in raw_paths if str(p).strip()]
    commands = []
    dart = [p for p in paths if p.endswith(".dart")]
    if dart and (root / "frontend").exists():
        rels = [str(Path(p).relative_to("frontend")) for p in dart if p.startswith("frontend/")]
        commands.append(run(root, ["dart", "format", *rels], cwd=root / "frontend", timeout_seconds=300))
    py = [p for p in paths if p.endswith(".py")]
    if py:
        commands.append(run(root, ["python3", "-m", "py_compile", *py], timeout_seconds=300))
    return {"paths": paths, "commands": commands}


def symbols(text: str) -> list[str]:
    found = []
    patterns = [r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", r"^\s*(?:public|private|protected)?\s*(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)"]
    compiled = [re.compile(p) for p in patterns]
    for line in text.splitlines():
        for pattern in compiled:
            match = pattern.match(line)
            if match:
                found.append(match.group(1))
                break
    return found


def file_info(root: Path, path: Path) -> dict[str, Any]:
    info = {"path": rel(root, path), "exists": path.exists()}
    if path.exists() and path.is_file() and not probably_binary(path):
        text = path.read_text(encoding="utf-8", errors="replace")
        info.update({"lines": text.count("\n") + (0 if text.endswith("\n") else 1), "bytes": len(text.encode("utf-8")), "imports": [line.strip() for line in text.splitlines() if line.strip().startswith(("import ", "export "))][:80], "symbols": symbols(text)[:120]})
    elif path.exists() and path.is_dir():
        info["children"] = sorted(child.name for child in path.iterdir() if not child.name.startswith("."))[:120]
    return info


def context_pack(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    topic = str(args.get("topic") or "Kerosene implementation context")
    max_chars = clamp_int(args.get("max_chars"), 60000, 4000, MAX_CONTEXT_CHARS)
    raw_paths = args.get("paths") or ["."]
    if not isinstance(raw_paths, list):
        raise WorkToolError("paths must be a list")
    paths = [resolve_existing(root, p) for p in raw_paths]
    scoped_paths = [rel(root, p) for p in paths]
    diff_scope = ["--", *scoped_paths] if scoped_paths != ["."] else []
    tree = []
    for base in paths[:6]:
        start = base if base.is_dir() else base.parent
        count = 0
        for current, dirs, files in os.walk(start):
            current_path = Path(current)
            depth = len(current_path.relative_to(start).parts)
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]
            if depth > 2:
                dirs[:] = []
                continue
            tree.append(("  " * depth) + (current_path.name + "/"))
            count += 1
            for name in sorted(files)[:40]:
                if count > 180:
                    break
                tree.append(("  " * (depth + 1)) + name)
                count += 1
            if count > 180:
                break
    pack = {"topic": topic, "root": str(root), "generated_at": utc_now(), "paths": scoped_paths, "tree": tree[:260], "overviews": [file_info(root, p) for p in paths], "git_status": truncate(git(root, ["status", "--short"], timeout_seconds=60).get("stdout", ""), 12000), "diffstat": truncate(git(root, ["diff", "--stat", *diff_scope], timeout_seconds=60).get("stdout", ""), 12000), "guidance": ["Prefer apply_patch or multi_edit over full rewrites.", "Run validate_changed_files after edits.", "Keep protected local material out of model context."]}
    text = json.dumps(pack, ensure_ascii=False, indent=2)
    if len(text) > max_chars:
        pack["truncated"] = True
        pack["text"] = truncate(text, max_chars)
    return pack


def git_status_compact(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    return {"branch": git_out(root, ["branch", "--show-current"], timeout_seconds=60).strip(), "changed_files": changed_files(root), "status": git_out(root, ["status", "--short"], timeout_seconds=60)}


def git_diff_summary(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    paths = args.get("paths") or []
    rels = [rel(root, resolve_target(root, p)) for p in paths] if isinstance(paths, list) else []
    scoped = ["--", *rels] if rels else []
    return {"paths": rels, "stat": git(root, ["diff", "--stat", *scoped], timeout_seconds=120).get("stdout", ""), "name_status": git(root, ["diff", "--name-status", *scoped], timeout_seconds=120).get("stdout", "")}


def git_diff_file(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = rel(root, resolve_target(root, args.get("path")))
    max_chars = clamp_int(args.get("max_chars"), 60000, 1000, 500000)
    result = git(root, ["diff", "--", path], timeout_seconds=120)
    return {"path": path, "diff": truncate(result.get("stdout", ""), max_chars), "returncode": result.get("returncode"), "stderr": result.get("stderr", "")}


def batch_read_files(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    raw_paths = args.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise WorkToolError("paths must be a non-empty list")
    max_chars_each = clamp_int(args.get("max_chars_each"), 30000, 1000, 200000)
    return {"files": [{"path": rel(root, p := resolve_existing(root, raw)), "text": truncate(read_text(p), max_chars_each)} for raw in raw_paths]}


def named_validation(root: Path, name: str, command: list[str], cwd: Path | None = None, timeout_seconds: int = 900) -> dict[str, Any]:
    result = run(root, command, cwd=cwd, timeout_seconds=timeout_seconds)
    return {"name": name, "status": "pass" if result["returncode"] == 0 else "fail", **result}


def validate_frontend(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    frontend = root / "frontend"
    commands = [named_validation(root, "flutter analyze", ["flutter", "analyze"], cwd=frontend, timeout_seconds=1200), named_validation(root, "cleanup guard", ["bash", "tool/check_frontend_cleanup_rules.sh"], cwd=frontend, timeout_seconds=300), named_validation(root, "frontend alignment audit", ["bash", "tool/audit_frontend_alignment.sh"], cwd=frontend, timeout_seconds=300), named_validation(root, "architecture guard", ["dart", "run", "tool/check_frontend_architecture_rules.dart"], cwd=frontend, timeout_seconds=600)]
    return {"status": "pass" if all(c["status"] == "pass" for c in commands) else "fail", "commands": commands}


def validate_backend(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    cwd = root / "backend" if (root / "backend" / "gradlew").exists() else root
    command = ["./gradlew", "test"]
    commands = [named_validation(root, "gradle test", command, cwd=cwd, timeout_seconds=1800)]
    return {"status": "pass" if all(c["status"] == "pass" for c in commands) else "fail", "commands": commands}


def validate_changed_files(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    selected = args.get("paths") or changed_files(root)
    if not isinstance(selected, list):
        raise WorkToolError("paths must be a list")
    paths = [rel(root, resolve_target(root, p)) for p in selected if str(p).strip()]
    commands = []
    dart = [p for p in paths if p.endswith(".dart")]
    if dart and (root / "frontend").exists():
        rels = [str(Path(p).relative_to("frontend")) for p in dart if p.startswith("frontend/")]
        commands.append(named_validation(root, "dart format changed", ["dart", "format", *rels], cwd=root / "frontend", timeout_seconds=300))
        commands.append(named_validation(root, "flutter analyze", ["flutter", "analyze"], cwd=root / "frontend", timeout_seconds=1200))
    py = [p for p in paths if p.endswith(".py")]
    if py:
        commands.append(named_validation(root, "python compile", ["python3", "-m", "py_compile", *py], timeout_seconds=300))
    if paths:
        commands.append(named_validation(root, "git diff check", ["git", "diff", "--check", "--", *paths], timeout_seconds=120))
    return {"status": "pass" if all(c["status"] == "pass" for c in commands) else "fail", "paths": paths, "commands": commands}


def session_file(root: Path) -> Path:
    return resolve_target(root, SESSION_STATE_REL.as_posix())


def read_session(root: Path) -> dict[str, Any]:
    path = session_file(root)
    if not path.exists():
        return {"active": False, "notes": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_session(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    path = session_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def session_start(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    return write_session(root, {"active": True, "task": str(args.get("task") or "Kerosene work session"), "created_at": utc_now(), "files_changed": changed_files(root), "notes": []})


def session_status(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    state = read_session(root)
    state["files_changed"] = changed_files(root)
    return state


def session_note(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    note = args.get("note")
    if not isinstance(note, str) or not note.strip():
        raise WorkToolError("note must be a non-empty string")
    state = read_session(root)
    state.setdefault("notes", []).append({"at": utc_now(), "note": note})
    return write_session(root, state)


def session_finish(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    state = read_session(root)
    state["active"] = False
    state["finished_at"] = utc_now()
    state["files_changed"] = changed_files(root)
    return write_session(root, state)


def grep_fallback(root: Path, query: str, path: str, max_count: int, fixed: bool = False) -> dict[str, Any]:
    matches: list[str] = []
    base = resolve_existing(root, path)
    flags = 0 if fixed else re.IGNORECASE
    pattern = None if fixed else re.compile(query, flags)
    needle = query if fixed else ""
    files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
    for file_path in files:
        if len(matches) >= max_count:
            break
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in file_path.relative_to(root).parts[:-1]):
            continue
        if blocked_path(file_path) or probably_binary(file_path):
            continue
        try:
            for line_no, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                hit = needle in line if fixed else bool(pattern and pattern.search(line))
                if hit:
                    matches.append(f"{rel(root, file_path)}:{line_no}:{line}")
                    if len(matches) >= max_count:
                        break
        except OSError:
            continue
    return {"command": ["python-grep-fallback", query, path], "cwd": ".", "returncode": 0 if matches else 1, "stdout": "\n".join(matches), "stderr": ""}


def run_rg(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not isinstance(query, str) or not query:
        raise WorkToolError("query is required")
    path = rel(root, resolve_existing(root, args.get("path") or "."))
    max_count = clamp_int(args.get("max_count", args.get("max_results")), 200, 1, 5000)
    if shutil.which("rg"):
        res = run(root, ["rg", "--line-number", "--max-columns", "500", "--max-count", str(max_count), query, path], timeout_seconds=300)
        stdout = res.get("stdout", "")
        lines = stdout.splitlines()
        global_max = 1000
        if len(lines) > global_max:
            res["stdout"] = "\n".join(lines[:global_max]) + f"\n... [truncated, total {len(lines)} matching lines]"
        return res
    return grep_fallback(root, query, path, max_count)


def find_references(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    symbol = args.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise WorkToolError("symbol is required")
    path = rel(root, resolve_existing(root, args.get("path") or "."))
    result = run(root, ["rg", "--line-number", "--fixed-strings", symbol, path], timeout_seconds=300) if shutil.which("rg") else grep_fallback(root, symbol, path, 5000, fixed=True)
    matches = []
    for line in result.get("stdout", "").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            matches.append({"path": parts[0], "line": int(parts[1]) if parts[1].isdigit() else parts[1], "text": parts[2]})
    return {"symbol": symbol, "matches": matches[:1000], "truncated": len(matches) > 1000}


def rename_symbol_safe(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    old = args.get("old")
    new = args.get("new")
    paths = args.get("paths") or []
    dry_run = as_bool(args.get("dry_run"), True)
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new or not isinstance(paths, list):
        raise WorkToolError("old, new and paths are required")
    pattern = re.compile(rf"\b{re.escape(old)}\b")
    changes = []
    for raw in paths:
        path = resolve_existing(root, raw)
        text = read_text(path)
        updated, count = pattern.subn(new, text)
        if count:
            file_path = rel(root, path)
            changes.append({"path": file_path, "replacements": count, "diff": diff_text(text, updated, file_path, 8000)})
            if not dry_run:
                path.write_text(updated, encoding="utf-8")
    return {"dry_run": dry_run, "old": old, "new": new, "changes": changes}


def dart_import_rewrite(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_existing(root, args.get("path"))
    package = str(args.get("package") or "kerosene")
    dry_run = as_bool(args.get("dry_run"), False)
    text = read_text(path)
    file_path = Path(rel(root, path))
    if not file_path.as_posix().startswith("frontend/lib/"):
        raise WorkToolError("expected a file under frontend/lib")
    current_dir = file_path.parent
    lib_root = (root / "frontend" / "lib").resolve(strict=False)
    def repl(match: re.Match[str]) -> str:
        quote = match.group(1)
        value = match.group(2)
        end_quote = match.group(3)
        if not value.startswith("."):
            return match.group(0)
        target = (root / current_dir / value).resolve(strict=False)
        if not is_relative_to(target, lib_root):
            return match.group(0)
        return f"import {quote}package:{package}/{target.relative_to(lib_root).as_posix()}{end_quote}"
    updated = re.sub(r"import\s+(['\"])([^'\"]+)(['\"])", repl, text)
    if not dry_run and updated != text:
        path.write_text(updated, encoding="utf-8")
    return {"path": rel(root, path), "dry_run": dry_run, "changed": updated != text, "diff": diff_text(text, updated, rel(root, path))}


def validate_mcp(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    files = [p for p in ["infra/mcp/kerosene_server.py", "infra/mcp/kerosene_work_tools.py", "infra/mcp/k_more_tools.py"] if (root / p).exists()]
    commands = [named_validation(root, "python compile", ["python3", "-m", "py_compile", *files], timeout_seconds=300)]
    if (root / "infra" / "mcp" / "kerosene-mcp").exists():
        commands.append(named_validation(root, "server help", ["infra/mcp/kerosene-mcp", "--help"], timeout_seconds=30))
    return {"status": "pass" if all(c["status"] == "pass" for c in commands) else "fail", "commands": commands}









ToolFn = Callable[[Path, dict[str, Any]], dict[str, Any]]
CHECKPOINT_ROOT_REL = Path("tmp/mcp/checkpoints")
LAST_CHANGE_REL = Path("tmp/mcp/last_change_manifest.json")


def _utc() -> str:
    return utc_now()


def _checkpoint_root(root: Path) -> Path:
    return resolve_target(root, CHECKPOINT_ROOT_REL.as_posix())


def _last_change_path(root: Path) -> Path:
    return resolve_target(root, LAST_CHANGE_REL.as_posix())


def _safe_id(value: Any | None = None) -> str:
    raw = str(value or _utc()).strip()
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")[:80] or "checkpoint"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_paths(root: Path, raw_paths: Any, *, allow_empty: bool = False) -> list[str]:
    if raw_paths in (None, ""):
        if allow_empty:
            return []
        raise WorkToolError("paths are required")
    if not isinstance(raw_paths, list):
        raise WorkToolError("paths must be a list")
    paths: list[str] = []
    for raw in raw_paths:
        if raw in (None, ""):
            continue
        target = resolve_target(root, str(raw))
        paths.append(rel(root, target))
    return sorted(set(paths))


def _patch_paths_from_list(patches: Any) -> list[str]:
    files: list[str] = []
    if isinstance(patches, list):
        for item in patches:
            patch = item.get("patch") if isinstance(item, dict) else item
            if isinstance(patch, str):
                files.extend(patch_paths(patch))
    return sorted(set(files))


def _paths_from_edits(edits: Any) -> list[str]:
    files: list[str] = []
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and edit.get("path"):
                files.append(str(edit["path"]))
    return sorted(set(files))


def _changed_since_status(before: str, after: str) -> list[str]:
    before_set = set(_status_files(before))
    after_set = set(_status_files(after))
    return sorted(after_set | before_set)


def _status_files(status: str) -> list[str]:
    files: list[str] = []
    for line in status.splitlines():
        item = line[3:] if len(line) > 3 else line.strip()
        if " -> " in item:
            item = item.split(" -> ", 1)[1]
        if item.strip():
            files.append(item.strip())
    return files


def _copy_current_file(root: Path, source_rel: str, destination: Path) -> dict[str, Any]:
    source = resolve_target(root, source_rel)
    entry = {"path": source_rel, "exists": source.exists(), "is_dir": source.is_dir() if source.exists() else False}
    if source.exists():
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return entry


def workspace_checkpoint(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    explicit_paths = args.get("paths")
    if explicit_paths is None:
        explicit_paths = changed_files(root)
    paths = _normalize_paths(root, explicit_paths, allow_empty=True)
    label = _safe_id(args.get("label") or args.get("name"))
    checkpoint_id = f"{_safe_id(_utc())}-{label}"
    checkpoint_dir = _checkpoint_root(root) / checkpoint_id
    files_dir = checkpoint_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=False)
    entries = []
    for item in paths:
        entries.append(_copy_current_file(root, item, files_dir / item))
    manifest = {
        "id": checkpoint_id,
        "label": label,
        "created_at": _utc(),
        "paths": paths,
        "git_status": git_out(root, ["status", "--short"], timeout_seconds=60),
        "entries": entries,
    }
    _write_json(checkpoint_dir / "manifest.json", manifest)
    _write_json(_last_change_path(root), {"checkpoint_id": checkpoint_id, "created_at": _utc(), "paths": paths, "reason": "checkpoint"})
    return {"checkpoint_id": checkpoint_id, "paths": paths, "entry_count": len(entries)}


def _latest_checkpoint(root: Path) -> str:
    root_dir = _checkpoint_root(root)
    if not root_dir.exists():
        raise WorkToolError("No checkpoints found")
    candidates = sorted([p.name for p in root_dir.iterdir() if p.is_dir()])
    if not candidates:
        raise WorkToolError("No checkpoints found")
    return candidates[-1]


def _manifest(root: Path, checkpoint_id: str | None) -> tuple[str, Path, dict[str, Any]]:
    cp_id = checkpoint_id or _latest_checkpoint(root)
    cp_dir = _checkpoint_root(root) / cp_id
    manifest = _read_json(cp_dir / "manifest.json", {})
    if not manifest:
        raise WorkToolError(f"Checkpoint not found: {cp_id}")
    return cp_id, cp_dir, manifest


def workspace_restore_session(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    cp_id, cp_dir, manifest = _manifest(root, args.get("checkpoint_id"))
    only_paths = set(_normalize_paths(root, args.get("paths"), allow_empty=True)) if args.get("paths") is not None else None
    dry_run = as_bool(args.get("dry_run"), False)
    restored: list[str] = []
    deleted: list[str] = []
    files_dir = cp_dir / "files"
    for entry in manifest.get("entries", []):
        path_rel = entry.get("path")
        if not isinstance(path_rel, str) or (only_paths is not None and path_rel not in only_paths):
            continue
        target = resolve_target(root, path_rel)
        snapshot = files_dir / path_rel
        existed = bool(entry.get("exists"))
        if dry_run:
            (restored if existed else deleted).append(path_rel)
            continue
        if existed:
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if snapshot.is_dir():
                shutil.copytree(snapshot, target)
            else:
                shutil.copy2(snapshot, target)
            restored.append(path_rel)
        else:
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            deleted.append(path_rel)
    return {"checkpoint_id": cp_id, "dry_run": dry_run, "restored": restored, "deleted": deleted}


def workspace_changed_by_session(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    cp_id, _cp_dir, manifest = _manifest(root, args.get("checkpoint_id"))
    before = manifest.get("git_status", "")
    after = git_out(root, ["status", "--short"], timeout_seconds=60)
    return {"checkpoint_id": cp_id, "before_status": before, "after_status": after, "changed_paths": _changed_since_status(before, after)}


def _record_last_change(root: Path, data: dict[str, Any]) -> None:
    data = {**data, "updated_at": _utc()}
    _write_json(_last_change_path(root), data)


def rollback_last_patch(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_json(_last_change_path(root), {})
    checkpoint_id = args.get("checkpoint_id") or manifest.get("checkpoint_id")
    if not checkpoint_id:
        raise WorkToolError("No last checkpoint is recorded")
    result = workspace_restore_session(root, {"checkpoint_id": checkpoint_id, "dry_run": as_bool(args.get("dry_run"), False)})
    result["rolled_back"] = not result.get("dry_run", False)
    return result


def _split_patch_by_file(patch: str) -> list[str]:
    lines = patch.splitlines(keepends=True)
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current:
            chunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(current)
    if len(chunks) <= 1:
        return [patch]
    return ["".join(chunk) for chunk in chunks if "+++ " in "".join(chunk) or "--- " in "".join(chunk)]


def patch_autosplit(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    patch = args.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        raise WorkToolError("patch is required")
    pieces = []
    for index, piece in enumerate(_split_patch_by_file(patch), 1):
        files = patch_paths(piece)
        for file_path in files:
            resolve_target(root, file_path)
        pieces.append({"index": index, "files": files, "chars": len(piece), "patch": piece if as_bool(args.get("include_patches"), False) else None})
    return {"piece_count": len(pieces), "pieces": pieces}


def apply_patch_batch(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    patches = args.get("patches")
    if isinstance(args.get("patch"), str):
        patches = [{"patch": piece} for piece in _split_patch_by_file(str(args["patch"]))]
    if not isinstance(patches, list) or not patches:
        raise WorkToolError("patches or patch is required")
    dry_run = as_bool(args.get("dry_run"), False)
    rollback_on_failure = as_bool(args.get("rollback_on_failure"), True)
    files = _patch_paths_from_list(patches)
    checkpoint = workspace_checkpoint(root, {"paths": files, "label": args.get("label") or "patch-batch"}) if not dry_run else {"checkpoint_id": None, "paths": files}
    results = []
    try:
        for index, item in enumerate(patches, 1):
            patch = item.get("patch") if isinstance(item, dict) else item
            if not isinstance(patch, str) or not patch.strip():
                raise WorkToolError(f"invalid patch #{index}")
            result = apply_patch(root, {"patch": patch, "dry_run": dry_run, "timeout_seconds": args.get("timeout_seconds", 120)})
            results.append({"index": index, **result})
            if not result.get("applied") and not dry_run:
                raise WorkToolError(f"patch #{index} was not applied")
    except Exception as exc:
        restored = None
        if rollback_on_failure and checkpoint.get("checkpoint_id") and not dry_run:
            restored = workspace_restore_session(root, {"checkpoint_id": checkpoint["checkpoint_id"]})
        raise WorkToolError(f"apply_patch_batch failed: {exc}; rollback={bool(restored)}") from exc
    _record_last_change(root, {"type": "patch_batch", "checkpoint_id": checkpoint.get("checkpoint_id"), "paths": files, "results": results})
    return {"applied": not dry_run, "dry_run": dry_run, "checkpoint_id": checkpoint.get("checkpoint_id"), "files": files, "results": results}


def _group_edits(edits: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edit in edits:
        path = edit.get("path")
        if not isinstance(path, str) or not path:
            raise WorkToolError("each edit needs a path")
        grouped.setdefault(path, []).append({"old_text": edit.get("old_text"), "new_text": edit.get("new_text"), "replace_all": edit.get("replace_all", True)})
    return grouped


def _run_validation(root: Path, validation: Any, paths: list[str]) -> dict[str, Any]:
    value = str(validation or "changed_files").strip().lower()
    if value in {"none", "false", "skip"}:
        return {"status": "skipped"}
    if value in {"mcp", "tools"}:
        return validate_mcp(root, {})
    if value == "frontend":
        return validate_frontend(root, {})
    if value == "backend":
        return validate_backend(root, {})
    return validate_changed_files(root, {"paths": paths})


def resilient_apply_change(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    goal = str(args.get("goal") or "resilient change")
    patches = args.get("patches") or ([] if not isinstance(args.get("patch"), str) else [{"patch": args.get("patch")}])
    edits = args.get("edits") or []
    if not patches and not edits:
        raise WorkToolError("patch, patches or edits are required")
    dry_run = as_bool(args.get("dry_run"), False)
    rollback_on_failure = as_bool(args.get("rollback_on_failure"), True)
    paths = sorted(set(_patch_paths_from_list(patches) + _paths_from_edits(edits) + _normalize_paths(root, args.get("target_files"), allow_empty=True)))
    checkpoint = workspace_checkpoint(root, {"paths": paths, "label": f"resilient-{_safe_id(goal)}"}) if not dry_run else {"checkpoint_id": None, "paths": paths}
    steps: list[dict[str, Any]] = []
    try:
        if patches:
            patch_result = apply_patch_batch(root, {"patches": patches, "dry_run": dry_run, "rollback_on_failure": False, "label": goal})
            steps.append({"type": "patch_batch", "result": patch_result})
        if edits:
            if not isinstance(edits, list):
                raise WorkToolError("edits must be a list")
            for path, grouped in _group_edits(edits).items():
                result = multi_edit(root, {"path": path, "edits": grouped, "dry_run": dry_run})
                steps.append({"type": "multi_edit", "result": result})
        validation = _run_validation(root, args.get("validation", "changed_files"), paths) if not dry_run else {"status": "skipped", "dry_run": True}
        if validation.get("status") == "fail":
            raise WorkToolError("validation failed")
    except Exception as exc:
        restored = None
        if rollback_on_failure and checkpoint.get("checkpoint_id") and not dry_run:
            restored = workspace_restore_session(root, {"checkpoint_id": checkpoint["checkpoint_id"]})
        return {"applied": False, "goal": goal, "checkpoint_id": checkpoint.get("checkpoint_id"), "error": str(exc), "rolled_back": bool(restored), "rollback": restored, "steps": steps}
    result = {"applied": not dry_run, "dry_run": dry_run, "goal": goal, "checkpoint_id": checkpoint.get("checkpoint_id"), "paths": paths, "steps": steps, "validation": validation}
    _record_last_change(root, {"type": "resilient_apply_change", "checkpoint_id": checkpoint.get("checkpoint_id"), "paths": paths, "goal": goal})
    return result


def blocked_payload_diagnosis(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    message = str(args.get("message") or "")
    payload = str(args.get("payload") or "")
    text = f"{message}\n{payload}".lower()
    findings = []
    if any(token in text for token in ["auth", "token", "password", "passkey", "biometric", "credential", "secret"]):
        findings.append("auth_or_secret_like_terms")
    if len(payload) > 20000:
        findings.append("large_payload")
    if payload.count("\n+++") + payload.count("\ndiff --git") > 1:
        findings.append("multi_file_patch")
    strategy = "Use resilient_apply_change with target_files and small exact edits."
    if "multi_file_patch" in findings:
        strategy = "Use patch_autosplit or apply_patch_batch with one patch per file."
    if "auth_or_secret_like_terms" in findings:
        strategy += " Avoid embedding secrets; describe intent and pass only file paths plus exact non-secret replacements."
    return {"findings": findings, "recommended_strategy": strategy, "safe_tools": ["context_pack", "batch_read_files", "resilient_apply_change", "apply_patch_batch", "rollback_last_patch"]}


def _replace_identifier_outside_dart_strings(text: str, old: str, new: str) -> tuple[str, int]:
    out: list[str] = []
    i = 0
    count = 0
    state = "code"
    quote = ""
    ident = re.compile(rf"\b{re.escape(old)}\b")
    while i < len(text):
        if state == "code":
            if text.startswith("//", i):
                end = text.find("\n", i)
                if end == -1:
                    out.append(text[i:])
                    break
                out.append(text[i:end])
                i = end
                continue
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                end = len(text) - 2 if end == -1 else end
                out.append(text[i:end + 2])
                i = end + 2
                continue
            if text[i] in {'"', "'"}:
                quote = text[i]
                state = "string"
                out.append(text[i])
                i += 1
                continue
            m = ident.match(text, i)
            if m:
                out.append(new)
                i = m.end()
                count += 1
                continue
            out.append(text[i])
            i += 1
        else:
            out.append(text[i])
            if text[i] == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if text[i] == quote:
                state = "code"
            i += 1
    return "".join(out), count


def dart_rename_symbol_safe(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    old = args.get("old")
    new = args.get("new")
    paths = _normalize_paths(root, args.get("paths"))
    dry_run = as_bool(args.get("dry_run"), True)
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        raise WorkToolError("old and new are required")
    results = []
    for path_rel in paths:
        if not path_rel.endswith(".dart"):
            continue
        path = resolve_existing(root, path_rel)
        before = read_text(path)
        after, count = _replace_identifier_outside_dart_strings(before, old, new)
        if count:
            if not dry_run:
                path.write_text(after, encoding="utf-8")
            results.append({"path": path_rel, "replacements": count, "diff": diff_text(before, after, path_rel, 10000)})
    return {"dry_run": dry_run, "old": old, "new": new, "changes": results}









SERVER_NAME = "kerosene-mcp"
SERVER_VERSION = "0.4.0"
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_ROOT = Path(os.environ.get("KEROSENE_MCP_ROOT") or str(PROJECT_ROOT))
CODEX_FLEET_SCRIPT = Path(
    os.environ.get("KEROSENE_MCP_CODEX_FLEET_SCRIPT") or str(PROJECT_ROOT / "AGENTS" / "codex-fleet-mcp")
)
AGY_FLEET_SCRIPT = Path(os.environ.get("KEROSENE_MCP_AGY_FLEET_SCRIPT") or str(PROJECT_ROOT / "AGENTS" / "agy-fleet-mcp"))
CODEX_FLEET_STATE_DIR = Path(os.environ.get("CODEX_FLEET_HOME", "/home/omega/.codex-fleet"))
CODEX_FLEET_WORKTREES_DIR = CODEX_FLEET_STATE_DIR / "worktrees"
NIGHTLY_QUEUE_REL = Path(os.environ.get("KEROSENE_MCP_NIGHTLY_QUEUE", "docs/AGENTS/NIGHTLY_ORCHESTRATION_QUEUE.md"))
NIGHTLY_STATE_REL = Path(os.environ.get("KEROSENE_MCP_NIGHTLY_STATE", "docs/AGENTS/NIGHTLY_ORCHESTRATION_STATE.md"))
DEFAULT_NIGHTLY_AGENT_ID = os.environ.get("KEROSENE_MCP_NIGHTLY_AGENT_ID", "codex2")
DEFAULT_NIGHTLY_MODEL = os.environ.get("KEROSENE_MCP_NIGHTLY_MODEL", "gpt-5.5")

MAX_READ_BYTES = int(os.environ.get("KEROSENE_MCP_MAX_READ_BYTES", "100000000"))
DEFAULT_READ_BYTES = int(os.environ.get("KEROSENE_MCP_DEFAULT_READ_BYTES", "262144"))
MAX_READ_LINES = int(os.environ.get("KEROSENE_MCP_MAX_READ_LINES", "20000"))
DEFAULT_READ_LINES = int(os.environ.get("KEROSENE_MCP_DEFAULT_READ_LINES", "1000"))
MAX_READ_LINE_CHARS = int(os.environ.get("KEROSENE_MCP_MAX_READ_LINE_CHARS", "20000"))
MAX_READ_LINES_CHARS = int(os.environ.get("KEROSENE_MCP_MAX_READ_LINES_CHARS", "4000000"))
MAX_SEARCH_RESULTS = int(os.environ.get("KEROSENE_MCP_MAX_SEARCH_RESULTS", "5000"))
DEFAULT_SEARCH_RESULTS = int(os.environ.get("KEROSENE_MCP_DEFAULT_SEARCH_RESULTS", "250"))
MAX_SEARCH_FILES = int(os.environ.get("KEROSENE_MCP_MAX_SEARCH_FILES", "200000"))
DEFAULT_SEARCH_FILES = int(os.environ.get("KEROSENE_MCP_DEFAULT_SEARCH_FILES", "50000"))
MAX_SEARCH_FILE_BYTES = int(os.environ.get("KEROSENE_MCP_MAX_SEARCH_FILE_BYTES", "100000000"))
DEFAULT_SEARCH_TIMEOUT_SECONDS = float(os.environ.get("KEROSENE_MCP_DEFAULT_SEARCH_TIMEOUT_SECONDS", "5"))
MAX_SEARCH_TIMEOUT_SECONDS = float(os.environ.get("KEROSENE_MCP_MAX_SEARCH_TIMEOUT_SECONDS", "5400"))
DEFAULT_SEARCH_RESPONSE_CHARS = int(os.environ.get("KEROSENE_MCP_DEFAULT_SEARCH_RESPONSE_CHARS", "750000"))
MAX_SEARCH_RESPONSE_CHARS = int(os.environ.get("KEROSENE_MCP_MAX_SEARCH_RESPONSE_CHARS", "2000000"))
MAX_TOOL_RESPONSE_CHARS = int(os.environ.get("KEROSENE_MCP_MAX_TOOL_RESPONSE_CHARS", "1500000"))
MAX_TREE_ENTRIES = int(os.environ.get("KEROSENE_MCP_MAX_TREE_ENTRIES", "50000"))
DEFAULT_TREE_ENTRIES = int(os.environ.get("KEROSENE_MCP_DEFAULT_TREE_ENTRIES", "5000"))
MAX_LIST_ENTRIES = int(os.environ.get("KEROSENE_MCP_MAX_LIST_ENTRIES", "50000"))
DEFAULT_LIST_ENTRIES = int(os.environ.get("KEROSENE_MCP_DEFAULT_LIST_ENTRIES", "2000"))
MAX_CONTEXT_LINES = int(os.environ.get("KEROSENE_MCP_MAX_CONTEXT_LINES", "25"))
DEFAULT_COMMAND_TIMEOUT_SECONDS = int(os.environ.get("KEROSENE_MCP_DEFAULT_COMMAND_TIMEOUT_SECONDS", "10"))
MAX_COMMAND_TIMEOUT_SECONDS = int(os.environ.get("KEROSENE_MCP_MAX_COMMAND_TIMEOUT_SECONDS", "14400"))
MAX_MCP_PROXY_TIMEOUT_SECONDS = int(os.environ.get("KEROSENE_MCP_MAX_PROXY_TIMEOUT_SECONDS", "172800"))

EXCLUDED_DIR_NAMES = {
    ".dart_tool",
    ".git",
    ".gradle",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".qodana",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "node_modules",
    "target",
    "venv",
}

SENSITIVE_EXACT_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.prod",
    ".env.staging",
    ".netrc",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "wallet.dat",
}

SAFE_ENV_EXAMPLE_NAMES = {
    ".env.dist",
    ".env.example",
    ".env.sample",
    ".env.template",
}

SENSITIVE_DIR_NAMES = {
    ".git",
    ".gnupg",
    ".secrets",
    ".ssh",
    "secrets",
}

SENSITIVE_SUFFIXES = {
    ".db",
    ".jks",
    ".key",
    ".keystore",
    ".mv.db",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}

SENSITIVE_ENV_MARKERS = {
    "ACCESS_TOKEN",
    "API_KEY",
    "AUTH_TOKEN",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
}

SCRUB_SHELL_ENV = os.environ.get("KEROSENE_MCP_SCRUB_SHELL_ENV", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

BINARY_SUFFIXES = {
    ".7z",
    ".apk",
    ".bin",
    ".class",
    ".dll",
    ".dylib",
    ".gif",
    ".gz",
    ".hprof",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".keystore",
    ".mp3",
    ".mp4",
    ".otf",
    ".png",
    ".so",
    ".tar",
    ".ttf",
    ".webp",
    ".zip",
}


class ReadOnlyMcpError(RuntimeError):
    pass


def utc_iso(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(timespec="seconds")


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ReadOnlyMcpError(f"Expected integer value, got {value!r}") from exc
    return max(minimum, min(maximum, parsed))


def clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ReadOnlyMcpError(f"Expected numeric value, got {value!r}") from exc
    return max(minimum, min(maximum, parsed))


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def is_relative_to(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def relative_path(root: Path, path: Path) -> str:
    if path == root:
        return "."
    return path.relative_to(root).as_posix()


def has_suffix(path: Path, suffixes: set[str]) -> bool:
    return any(suffix.lower() in suffixes for suffix in path.suffixes)


def is_sensitive_path(path: Path) -> bool:
    lower_parts = {part.lower() for part in path.parts}
    if lower_parts & SENSITIVE_DIR_NAMES:
        return True

    lower_name = path.name.lower()
    if lower_name in SAFE_ENV_EXAMPLE_NAMES:
        return False
    if lower_name in SENSITIVE_EXACT_NAMES:
        return True
    if lower_name.startswith(".env."):
        return True
    return has_suffix(path, SENSITIVE_SUFFIXES)


def shell_environment(root: Path) -> dict[str, str]:
    if not SCRUB_SHELL_ENV:
        env = os.environ.copy()
        env["KEROSENE_MCP_ROOT"] = str(root)
        return env

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper_key = key.upper()
        if any(marker in upper_key for marker in SENSITIVE_ENV_MARKERS):
            continue
        env[key] = value
    env["KEROSENE_MCP_ROOT"] = str(root)
    return env


def is_excluded_dir(path: Path) -> bool:
    return path.name in EXCLUDED_DIR_NAMES or (path / "pyvenv.cfg").exists()


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


def is_binary_suffix(path: Path) -> bool:
    return has_suffix(path, BINARY_SUFFIXES)


def is_probably_binary(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:4096]
    if b"\0" in sample:
        return True
    control = 0
    for byte in sample:
        if byte in (9, 10, 12, 13, 27):
            continue
        if byte < 32 or byte == 127:
            control += 1
    return control / len(sample) > 0.30


def decode_text(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), "utf-8-replacement"


def truncate_text(value: str, max_chars: int = 500) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 15] + "...<truncated>"


def compact_tool_value(value: Any, *, max_chars: int = MAX_TOOL_RESPONSE_CHARS) -> str:
    text = json.dumps(value, indent=2, sort_keys=True)
    if len(text) <= max_chars:
        return text

    if not isinstance(value, dict):
        return json.dumps(
            {
                "response_truncated": True,
                "original_response_chars": len(text),
                "error": "Tool response exceeded connector-safe size.",
            },
            indent=2,
            sort_keys=True,
        )

    compacted = dict(value)
    compacted["response_truncated"] = True
    compacted["original_response_chars"] = len(text)
    compacted["response_truncation_reason"] = "connector_safe_response_budget"

    for key in ("content", "stdout", "stderr", "tree"):
        current = compacted.get(key)
        if isinstance(current, str):
            compacted[key] = truncate_text(current, max(1000, max_chars // 2))

    for key in ("results", "lines", "entries"):
        current = compacted.get(key)
        if isinstance(current, list):
            compacted[f"original_{key}_count"] = len(current)
            compacted[key] = current
            while compacted[key]:
                candidate = json.dumps(compacted, indent=2, sort_keys=True)
                if len(candidate) <= max_chars:
                    return candidate
                keep = max(0, len(compacted[key]) // 2)
                compacted[key] = compacted[key][:keep]

    text = json.dumps(compacted, indent=2, sort_keys=True)
    if len(text) <= max_chars:
        return text

    summary = {
        "response_truncated": True,
        "original_response_chars": compacted.get("original_response_chars"),
        "response_truncation_reason": "connector_safe_response_budget",
        "available_keys": sorted(str(key) for key in value.keys()),
        "error": "Tool response exceeded connector-safe size. Retry with a narrower path, lower max_results, lower max_bytes, or read_file_lines.",
    }
    return json.dumps(summary, indent=2, sort_keys=True)


def resolve_root(root: Path) -> Path:
    resolved = root.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ReadOnlyMcpError(f"Project root is not a directory: {resolved}")
    return resolved


def resolve_existing_path(root: Path, user_path: Any) -> Path:
    raw = "." if user_path in (None, "") else str(user_path)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ReadOnlyMcpError(f"Path does not exist: {raw}") from exc
    except OSError as exc:
        raise ReadOnlyMcpError(f"Cannot resolve path {raw!r}: {exc}") from exc
    if not is_relative_to(resolved, root):
        raise ReadOnlyMcpError(f"Path is outside the Kerosene project root: {raw}")
    return resolved


def ensure_readable_file(root: Path, user_path: Any) -> Path:
    path = resolve_existing_path(root, user_path)
    if path.is_dir():
        raise ReadOnlyMcpError(f"Path is a directory, not a file: {relative_path(root, path)}")
    if is_sensitive_path(path):
        raise ReadOnlyMcpError(
            f"Refusing to read sensitive file: {relative_path(root, path)}. "
            "Use an explicit safer export if this content is required."
        )
    return path


def resolve_writable_path(root: Path, user_path: Any) -> Path:
    raw = "." if user_path in (None, "") else str(user_path)
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        raise ReadOnlyMcpError("Path must be relative to the project root")
    resolved_root = root.resolve(strict=True)
    path = (resolved_root / candidate).resolve(strict=False)
    if not is_relative_to(path, resolved_root):
        raise ReadOnlyMcpError(f"Path is outside the Kerosene project root: {raw}")
    if is_sensitive_path(path):
        raise ReadOnlyMcpError(
            f"Refusing to modify sensitive file: {relative_path(root, path)}. "
            "Use an explicit safer export if this content is required."
        )
    return path


def write_file(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_writable_path(root, args.get("path"))
    content = args.get("content")
    if not isinstance(content, str):
        raise ReadOnlyMcpError("Field 'content' must be a string")
    create_parents = as_bool(args.get("create_parents"), True)
    existed_before = path.exists()
    if path.exists() and path.is_dir():
        raise ReadOnlyMcpError(f"Path is a directory, not a file: {relative_path(root, path)}")
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "path": relative_path(root, path),
        "bytes_written": len(content.encode("utf-8")),
        "created": not existed_before,
    }


def replace_text_in_file(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = ensure_readable_file(root, args.get("path"))
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if not isinstance(old_text, str) or not old_text:
        raise ReadOnlyMcpError("Field 'old_text' must be a non-empty string")
    if not isinstance(new_text, str):
        raise ReadOnlyMcpError("Field 'new_text' must be a string")

    replace_all = as_bool(args.get("replace_all"), True)
    case_sensitive = as_bool(args.get("case_sensitive"), True)
    original = path.read_text(encoding="utf-8")

    if case_sensitive:
        occurrences = original.count(old_text)
        if occurrences == 0:
            raise ReadOnlyMcpError("No occurrences found")
        updated = original.replace(old_text, new_text, -1 if replace_all else 1)
        replaced = occurrences if replace_all else 1
    else:
        pattern = re.compile(re.escape(old_text), re.IGNORECASE)
        matches = list(pattern.finditer(original))
        if not matches:
            raise ReadOnlyMcpError("No occurrences found")
        if replace_all:
            updated, replaced = pattern.subn(new_text, original)
        else:
            updated = pattern.sub(new_text, original, count=1)
            replaced = 1

    path.write_text(updated, encoding="utf-8")
    return {
        "path": relative_path(root, path),
        "replacements": replaced,
        "bytes_written": len(updated.encode("utf-8")),
    }



def call_mcp_proxy(script: Path, tool_name: str, arguments: dict[str, Any], timeout_seconds: int = 1200) -> Any:
    if not script.exists():
        raise ReadOnlyMcpError(f"Proxy MCP script not found: {script}")
    payload = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}),
        ]
    ) + "\n"
    try:
        completed = subprocess.run(
            [str(script)],
            input=payload,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ReadOnlyMcpError(f"Proxy MCP call timed out after {timeout_seconds} seconds") from exc
    if completed.returncode != 0 and not completed.stdout.strip():
        raise ReadOnlyMcpError(completed.stderr.strip() or f"Proxy MCP exited with {completed.returncode}")
    last_result: Any = None
    for line in completed.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == 2:
            if "error" in message:
                error = message["error"]
                raise ReadOnlyMcpError(error.get("message") or "Proxy MCP tool call failed")
            result = message.get("result")
            content = result.get("content") if isinstance(result, dict) else None
            if (
                isinstance(content, list)
                and content
                and isinstance(content[0], dict)
                and isinstance(content[0].get("text"), str)
            ):
                try:
                    last_result = json.loads(content[0]["text"])
                except json.JSONDecodeError:
                    last_result = content[0]["text"]
            else:
                last_result = result
    if last_result is None:
        raise ReadOnlyMcpError("Proxy MCP did not return a tool result")
    return last_result


def proxy_timeout_seconds(tool_name: str, arguments: dict[str, Any]) -> int:
    timeout = 1200
    raw_timeout = arguments.get("timeout_seconds")
    try:
        requested = int(float(raw_timeout)) if raw_timeout is not None else None
    except (TypeError, ValueError):
        requested = None
    if requested:
        timeout = max(timeout, requested + 120)
        if tool_name.endswith("_quota_probe_all"):
            timeout = max(timeout, requested * 8 + 240)
    return min(timeout, MAX_MCP_PROXY_TIMEOUT_SECONDS)


def run_local_command(command: list[str], *, cwd: Path, timeout_seconds: int = 60) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
            "ok": completed.returncode == 0,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timed_out": True,
            "ok": False,
            "error": f"Command timed out after {timeout_seconds} seconds",
        }


def run_git(repo: Path, args: list[str], *, timeout_seconds: int = 60) -> dict[str, Any]:
    return run_local_command(["git", *args], cwd=repo, timeout_seconds=timeout_seconds)


def git_text(repo: Path, args: list[str], *, timeout_seconds: int = 60) -> str:
    result = run_git(repo, args, timeout_seconds=timeout_seconds)
    return result["stdout"].strip() if result.get("returncode") == 0 else ""


def ensure_git_repo(path: Path) -> Path:
    result = run_git(path, ["rev-parse", "--show-toplevel"])
    if result.get("returncode") != 0:
        raise ReadOnlyMcpError(result.get("stderr") or f"Not a git repository: {path}")
    return Path(str(result["stdout"]).strip()).resolve()


def allowed_orchestration_repo(root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ReadOnlyMcpError(f"Cannot resolve repository path: {candidate}") from exc
    allowed_roots = [root.resolve(strict=True), CODEX_FLEET_WORKTREES_DIR.expanduser().resolve(strict=False)]
    if not any(is_relative_to(resolved, allowed_root) for allowed_root in allowed_roots):
        raise ReadOnlyMcpError(f"Refusing repository path outside Kerosene orchestration roots: {resolved}")
    return ensure_git_repo(resolved)


def queue_path(root: Path) -> Path:
    return resolve_existing_path(root, NIGHTLY_QUEUE_REL.as_posix())


def state_path(root: Path) -> Path:
    return resolve_existing_path(root, NIGHTLY_STATE_REL.as_posix())


def parse_heading_block(content: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(?P<body>.*?)(?=^## |\Z)", content, re.M | re.S)
    return match.group("body").strip() if match else ""


def replace_heading_block(content: str, heading: str, body: str) -> str:
    replacement = f"## {heading}\n\n{body.strip()}\n\n"
    pattern = rf"^## {re.escape(heading)}\n.*?(?=^## |\Z)"
    if re.search(pattern, content, re.M | re.S):
        return re.sub(pattern, replacement, content, count=1, flags=re.M | re.S)
    return content.rstrip() + "\n\n" + replacement


def parse_nightly_state(root: Path) -> dict[str, str | None]:
    try:
        content = state_path(root).read_text(encoding="utf-8")
    except OSError:
        content = ""
    current = parse_heading_block(content, "Current task")
    next_block = parse_heading_block(content, "Next task")

    def field(name: str) -> str | None:
        match = re.search(rf"^{re.escape(name)}:\s*(.+?)\s*$", current, re.M)
        return match.group(1).strip() if match else None

    next_match = re.search(r"`([^`]+)`", next_block)
    return {
        "current_task": field("ID"),
        "agent_id": field("Agent"),
        "status": field("Status"),
        "next_task": next_match.group(1).strip() if next_match else None,
    }


def parse_nightly_queue(root: Path) -> list[dict[str, str]]:
    content = queue_path(root).read_text(encoding="utf-8")
    matches = list(re.finditer(r"^###\s+\d+\.\s+`([^`]+)`\s*$", content, re.M))
    tasks: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        mode_match = re.search(r"^Mode:\s*(.+?)\s*$", body, re.M)
        tasks.append(
            {
                "id": match.group(1).strip(),
                "body": body,
                "mode": mode_match.group(1).strip() if mode_match else "",
            }
        )
    return tasks


def completed_commit_subjects(root: Path) -> set[str]:
    output = git_text(root, ["log", "--format=%s", "-n", "300"])
    return {line.strip() for line in output.splitlines() if line.strip()}


def find_queue_task(root: Path, task_id: str) -> dict[str, str] | None:
    for task in parse_nightly_queue(root):
        if task["id"] == task_id:
            return task
    return None


def next_queue_task(root: Path, *, preferred_task_id: str | None = None) -> dict[str, str] | None:
    tasks = parse_nightly_queue(root)
    completed = completed_commit_subjects(root)
    if preferred_task_id:
        preferred = next((task for task in tasks if task["id"] == preferred_task_id), None)
        if preferred and preferred["id"] not in completed:
            return preferred
    state = parse_nightly_state(root)
    state_next = state.get("next_task")
    if state_next:
        preferred = next((task for task in tasks if task["id"] == state_next), None)
        if preferred and preferred["id"] not in completed:
            return preferred
    for task in tasks:
        if task["id"] not in completed:
            return task
    return None


def task_after(root: Path, completed_task_id: str) -> dict[str, str] | None:
    tasks = parse_nightly_queue(root)
    for index, task in enumerate(tasks):
        if task["id"] == completed_task_id:
            for later in tasks[index + 1 :]:
                if later["id"] not in completed_commit_subjects(root):
                    return later
            return None
    return next_queue_task(root)


def git_dirty_files(repo: Path) -> list[str]:
    tracked = git_text(repo, ["diff", "--name-only", "HEAD", "--"]).splitlines()
    untracked = git_text(repo, ["ls-files", "--others", "--exclude-standard"]).splitlines()
    seen: set[str] = set()
    files: list[str] = []
    for path in [*tracked, *untracked]:
        path = path.strip()
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


def unsafe_git_paths(repo: Path, paths: list[str]) -> list[str]:
    unsafe: list[str] = []
    for rel in paths:
        path = (repo / rel).resolve(strict=False)
        if ".git" in path.parts or is_sensitive_path(path):
            unsafe.append(rel)
    return unsafe


def stage_git_paths(repo: Path, paths: list[str]) -> dict[str, Any]:
    if not paths:
        return {"ok": True, "staged": []}
    for offset in range(0, len(paths), 50):
        batch = paths[offset : offset + 50]
        result = run_git(repo, ["add", "--", *batch])
        if result.get("returncode") != 0:
            return {"ok": False, "failed_batch": batch, "stdout": result["stdout"], "stderr": result["stderr"]}
    return {"ok": True, "staged": paths}


def commit_if_changed(repo: Path, message: str, paths: list[str] | None = None) -> dict[str, Any]:
    paths = paths if paths is not None else git_dirty_files(repo)
    if not paths:
        return {"ok": True, "committed": False, "reason": "no changes"}
    unsafe = unsafe_git_paths(repo, paths)
    if unsafe:
        return {"ok": False, "committed": False, "blocked_reason": "unsafe_paths", "unsafe_paths": unsafe}
    staged = stage_git_paths(repo, paths)
    if not staged.get("ok"):
        return {"ok": False, "committed": False, "blocked_reason": "git_add_failed", "stage": staged}
    result = run_git(repo, ["commit", "-m", message], timeout_seconds=120)
    if result.get("returncode") != 0:
        return {
            "ok": False,
            "committed": False,
            "blocked_reason": "git_commit_failed",
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }
    return {
        "ok": True,
        "committed": True,
        "commit": git_text(repo, ["rev-parse", "--short", "HEAD"]),
        "message": message,
        "paths": paths,
    }


def compact_agent_record(record: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "agent_id",
        "alive",
        "status",
        "last_status",
        "thread_id",
        "cwd",
        "run_as_user",
        "model",
        "event_count",
        "errors",
        "diagnostic",
        "stderr_tail",
    ]
    compact = {key: record.get(key) for key in keys if key in record}
    if isinstance(record.get("last_agent_message"), str):
        compact["last_agent_message"] = truncate_text(record["last_agent_message"], 2000)
    return compact


def fleet_status_snapshot(agent_id: str | None = None) -> dict[str, Any]:
    args = {"agent_id": agent_id} if agent_id else {}
    status = call_mcp_proxy(CODEX_FLEET_SCRIPT, "fleet_status", args, timeout_seconds=1200)
    if not isinstance(status, dict):
        raise ReadOnlyMcpError("fleet_status returned an unexpected payload")
    return status


def active_fleet_agents(status: dict[str, Any]) -> dict[str, Any]:
    agents = status.get("agents") if isinstance(status.get("agents"), dict) else {}
    return {agent_id: record for agent_id, record in agents.items() if isinstance(record, dict) and record.get("alive")}


def current_or_requested_agent(root: Path, args: dict[str, Any]) -> str | None:
    requested = args.get("agent_id")
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    state = parse_nightly_state(root)
    agent_id = state.get("agent_id")
    if agent_id and agent_id.lower() != "none":
        return agent_id
    return DEFAULT_NIGHTLY_AGENT_ID


def repo_from_args(root: Path, args: dict[str, Any]) -> Path:
    if isinstance(args.get("agent_id"), str) and args["agent_id"].strip():
        status = fleet_status_snapshot(args["agent_id"].strip())
        record = (status.get("agents") or {}).get(args["agent_id"].strip())
        if not isinstance(record, dict) or not record.get("cwd"):
            raise ReadOnlyMcpError(f"No cwd recorded for agent {args['agent_id']}")
        return allowed_orchestration_repo(root, Path(str(record["cwd"])))
    raw_path = args.get("path")
    if raw_path:
        candidate = Path(str(raw_path)).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        return allowed_orchestration_repo(root, candidate)
    return ensure_git_repo(root)


def kerosene_git_status(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    repo = repo_from_args(root, args)
    status = run_git(repo, ["status", "--short"])
    branch = git_text(repo, ["branch", "--show-current"])
    head = git_text(repo, ["rev-parse", "--short", "HEAD"])
    upstream = git_text(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    dirty_files = git_dirty_files(repo)
    return {
        "ok": status.get("returncode") == 0,
        "repo": str(repo),
        "branch": branch or None,
        "head": head or None,
        "upstream": upstream or None,
        "clean": not dirty_files,
        "dirty_file_count": len(dirty_files),
        "dirty_files": dirty_files,
        "status_short": status["stdout"],
        "stderr": status["stderr"],
    }


def kerosene_clean_worktree(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    repo = repo_from_args(root, args)
    status = kerosene_git_status(root, {**args, "path": str(repo)})
    if status["clean"]:
        return {"ok": True, "clean": True, "repo": str(repo), "action": "none"}
    diff_check = run_git(repo, ["diff", "--check"], timeout_seconds=120)
    return {
        "ok": False,
        "clean": False,
        "repo": str(repo),
        "action": "blocked",
        "blocked_reason": "dirty_worktree_requires_explicit_commit_or_human_review",
        "diff_check": {
            "ok": diff_check.get("returncode") == 0,
            "stdout": diff_check["stdout"],
            "stderr": diff_check["stderr"],
        },
        "status": status,
        "policy": "This tool does not discard or stash unknown work. Use kerosene_commit_agent_output for validated agent output.",
    }


def build_nightly_task_prompt(root: Path, task: dict[str, str]) -> str:
    return f"""You are a Codex implementation agent working inside the Kerosene repository.

Task: {task['id']}

Use the repository-local orchestration queue as the source of truth:
- {NIGHTLY_QUEUE_REL.as_posix()}
- {NIGHTLY_STATE_REL.as_posix()}

Implement only the task section below. Do not broaden scope. Do not run infra/scripts/local/control.sh start.
Do not commit; the Kerosene MCP orchestrator will validate and commit locally.
Do not use git add . If validation is blocked by sandbox, report the exact command and error.

Task section:
{task['body']}

Final report must include changed files, validation commands/results, and blockers or residual risks.
"""


def commit_state_update(root: Path, message: str = "fase-6/orchestration: update nightly state") -> dict[str, Any]:
    rel = NIGHTLY_STATE_REL.as_posix()
    status = run_git(root, ["status", "--short", "--", rel])
    if not status["stdout"].strip():
        return {"ok": True, "committed": False, "reason": "state unchanged"}
    return commit_if_changed(root, message, [rel])


def update_nightly_state(
    root: Path,
    *,
    current_task_id: str | None,
    agent_id: str | None,
    status_text: str,
    last_completed: str | None = None,
    next_task_id: str | None = None,
) -> dict[str, Any]:
    path = state_path(root)
    content = path.read_text(encoding="utf-8")
    current_body = "\n".join(
        [
            f"ID: {current_task_id or 'none'}",
            f"Agent: {agent_id or 'none'}",
            f"Status: {status_text}",
        ]
    )
    content = replace_heading_block(content, "Current task", current_body)
    next_body = f"`{next_task_id}`" if next_task_id else "None"
    content = replace_heading_block(content, "Next task", next_body)
    if last_completed:
        last_body = parse_heading_block(content, "Last completed work")
        bullet = f"- `{last_completed}`"
        if bullet not in last_body:
            last_body = (bullet + "\n" + last_body).strip()
            content = replace_heading_block(content, "Last completed work", last_body)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": relative_path(root, path)}


def kerosene_dispatch_next(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    mode = str(args.get("mode") or "nightly")
    if mode != "nightly":
        raise ReadOnlyMcpError("Only mode='nightly' is supported")
    if as_bool(args.get("require_clean_worktree"), True):
        root_status = kerosene_git_status(root, {})
        if not root_status["clean"]:
            return {"ok": False, "action": "blocked", "blocked_reason": "root_worktree_dirty", "git_status": root_status}

    status = fleet_status_snapshot()
    active = active_fleet_agents(status)
    if active:
        return {
            "ok": True,
            "action": "wait",
            "reason": "agent_already_running",
            "active_agents": {agent_id: compact_agent_record(record) for agent_id, record in active.items()},
        }

    task = next_queue_task(root, preferred_task_id=args.get("task_id") if isinstance(args.get("task_id"), str) else None)
    if not task:
        return {"ok": True, "action": "none", "reason": "no_pending_queue_task"}

    agent_id = str(args.get("agent_id") or DEFAULT_NIGHTLY_AGENT_ID)
    prompt = build_nightly_task_prompt(root, task)
    if as_bool(args.get("dry_run"), False):
        return {"ok": True, "action": "dry_run", "task_id": task["id"], "agent_id": agent_id}

    fleet_args = {
        "agent_id": agent_id,
        "model": str(args.get("model") or DEFAULT_NIGHTLY_MODEL),
        "task": prompt,
        "cwd": str(root),
        "sandbox": str(args.get("sandbox") or "workspace-write"),
        "approval_policy": str(args.get("approval_policy") or "never"),
        "create_worktree": as_bool(args.get("create_worktree"), True),
    }
    if isinstance(args.get("reasoning_effort"), str):
        fleet_args["reasoning_effort"] = args["reasoning_effort"]
    result = call_mcp_proxy(CODEX_FLEET_SCRIPT, "fleet_start_worker", fleet_args, timeout_seconds=1200)
    next_after = task_after(root, task["id"])
    update_nightly_state(
        root,
        current_task_id=task["id"],
        agent_id=agent_id,
        status_text="dispatched by kerosene_dispatch_next",
        next_task_id=next_after["id"] if next_after else None,
    )
    state_commit = commit_state_update(root, "fase-6/orchestration: dispatch nightly task")
    record = ((result or {}).get("agents") or {}).get(agent_id) if isinstance(result, dict) else None
    return {
        "ok": True,
        "action": "dispatched",
        "task_id": task["id"],
        "agent_id": agent_id,
        "state_commit": state_commit,
        "agent": compact_agent_record(record) if isinstance(record, dict) else None,
    }


def kerosene_collect_agent_result(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    agent_id = current_or_requested_agent(root, args)
    status = fleet_status_snapshot(agent_id)
    agents = status.get("agents") if isinstance(status.get("agents"), dict) else {}
    record = agents.get(agent_id) if agent_id else None
    if not isinstance(record, dict):
        return {"ok": False, "action": "blocked", "blocked_reason": "agent_not_found", "agent_id": agent_id}
    tail_lines_count = clamp_int(args.get("lines"), 80, 1, 500)
    tail = call_mcp_proxy(
        CODEX_FLEET_SCRIPT,
        "fleet_tail",
        {"agent_id": agent_id, "stream": "events", "lines": tail_lines_count},
        timeout_seconds=1200,
    )
    repo_status: dict[str, Any] | None = None
    cwd = record.get("cwd")
    if isinstance(cwd, str) and cwd:
        try:
            repo_status = kerosene_git_status(root, {"path": cwd})
        except ReadOnlyMcpError as exc:
            repo_status = {"ok": False, "error": str(exc), "repo": cwd}
    ready_to_commit = not bool(record.get("alive")) and bool(repo_status and not repo_status.get("clean"))
    return {
        "ok": True,
        "action": "agent_running" if record.get("alive") else "agent_finished",
        "ready_to_commit": ready_to_commit,
        "agent": compact_agent_record(record),
        "worktree_status": repo_status,
        "tail": {
            "agent_id": agent_id,
            "stream": "events",
            "line_count": len(tail.get("lines") or []) if isinstance(tail, dict) else None,
            "last_lines": (tail.get("lines") or [])[-10:] if isinstance(tail, dict) else [],
        },
    }


def kerosene_commit_agent_output(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    mode = str(args.get("mode") or "nightly")
    if mode != "nightly":
        raise ReadOnlyMcpError("Only mode='nightly' is supported")
    agent_id = current_or_requested_agent(root, args)
    status = fleet_status_snapshot(agent_id)
    record = ((status.get("agents") or {}).get(agent_id)) if agent_id else None
    if not isinstance(record, dict):
        return {"ok": False, "action": "blocked", "blocked_reason": "agent_not_found", "agent_id": agent_id}
    if record.get("alive"):
        return {"ok": True, "action": "wait", "reason": "agent_still_running", "agent": compact_agent_record(record)}

    cwd = record.get("cwd")
    repo = allowed_orchestration_repo(root, Path(str(cwd))) if isinstance(cwd, str) and cwd else root
    dirty_files = git_dirty_files(repo)
    if not dirty_files:
        return {"ok": True, "action": "none", "reason": "agent_worktree_clean", "repo": str(repo)}
    diff_check = run_git(repo, ["diff", "--check"], timeout_seconds=120)
    if diff_check.get("returncode") != 0:
        return {
            "ok": False,
            "action": "blocked",
            "blocked_reason": "diff_check_failed",
            "repo": str(repo),
            "stdout": diff_check["stdout"],
            "stderr": diff_check["stderr"],
        }

    state = parse_nightly_state(root)
    message = str(args.get("message") or state.get("current_task") or "").strip()
    if not message or message == "none":
        return {"ok": False, "action": "blocked", "blocked_reason": "missing_commit_message"}

    commit_result = commit_if_changed(repo, message, dirty_files)
    if not commit_result.get("ok"):
        return {"ok": False, "action": "blocked", "blocked_reason": "agent_commit_failed", "commit": commit_result}

    integrated: dict[str, Any] | None = None
    if repo != root and as_bool(args.get("integrate_to_root"), True):
        root_status = kerosene_git_status(root, {})
        if not root_status["clean"]:
            return {
                "ok": False,
                "action": "blocked",
                "blocked_reason": "root_dirty_before_cherry_pick",
                "agent_commit": commit_result,
                "root_status": root_status,
            }
        commit_hash = str(commit_result.get("commit") or "")
        cherry_pick = run_git(root, ["cherry-pick", commit_hash], timeout_seconds=120)
        if cherry_pick.get("returncode") != 0:
            abort = run_git(root, ["cherry-pick", "--abort"], timeout_seconds=120)
            return {
                "ok": False,
                "action": "blocked",
                "blocked_reason": "cherry_pick_failed",
                "agent_commit": commit_result,
                "stdout": cherry_pick["stdout"],
                "stderr": cherry_pick["stderr"],
                "abort": {"returncode": abort["returncode"], "stderr": abort["stderr"]},
            }
        integrated = {
            "ok": True,
            "commit": git_text(root, ["rev-parse", "--short", "HEAD"]),
            "stdout": cherry_pick["stdout"],
            "stderr": cherry_pick["stderr"],
        }

    next_task = task_after(root, message)
    next_after = task_after(root, next_task["id"]) if next_task else None
    update_nightly_state(
        root,
        current_task_id=next_task["id"] if next_task else None,
        agent_id=None,
        status_text="ready to dispatch next task" if next_task else "queue complete",
        last_completed=message,
        next_task_id=next_after["id"] if next_after else None,
    )
    state_commit = commit_state_update(root)
    return {
        "ok": True,
        "action": "committed",
        "agent_id": agent_id,
        "repo": str(repo),
        "agent_commit": commit_result,
        "integrated_to_root": integrated,
        "state_commit": state_commit,
        "next_task": next_task["id"] if next_task else None,
    }


def kerosene_cycle_once(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    mode = str(args.get("mode") or "nightly")
    if mode != "nightly":
        raise ReadOnlyMcpError("Only mode='nightly' is supported")
    steps: list[dict[str, Any]] = []

    root_status = kerosene_git_status(root, {})
    steps.append({"step": "kerosene_git_status", "clean": root_status["clean"], "dirty_file_count": root_status["dirty_file_count"]})
    if not root_status["clean"]:
        clean = kerosene_clean_worktree(root, {})
        steps.append({"step": "kerosene_clean_worktree", "result": clean})
        return {"ok": False, "action": "blocked", "blocked_reason": "root_worktree_dirty", "steps": steps}

    status = fleet_status_snapshot()
    active = active_fleet_agents(status)
    if active:
        collect = kerosene_collect_agent_result(root, {"agent_id": next(iter(active)), "lines": args.get("lines", 80)})
        steps.append({"step": "kerosene_collect_agent_result", "result": collect})
        return {"ok": True, "action": "wait", "steps": steps}

    collect = kerosene_collect_agent_result(root, {"agent_id": current_or_requested_agent(root, args), "lines": args.get("lines", 80)})
    steps.append({"step": "kerosene_collect_agent_result", "result": collect})
    if collect.get("ready_to_commit") and as_bool(args.get("commit_agent_output"), True):
        commit = kerosene_commit_agent_output(root, args)
        steps.append({"step": "kerosene_commit_agent_output", "result": commit})
        if not commit.get("ok"):
            return {"ok": False, "action": "blocked", "blocked_reason": "commit_agent_output_failed", "steps": steps}

    if as_bool(args.get("dispatch"), True):
        dispatch_args = dict(args)
        dispatch_args.setdefault("require_clean_worktree", True)
        dispatch = kerosene_dispatch_next(root, dispatch_args)
        steps.append({"step": "kerosene_dispatch_next", "result": dispatch})
        return {"ok": bool(dispatch.get("ok")), "action": dispatch.get("action"), "steps": steps}

    return {"ok": True, "action": "none", "steps": steps}


def kerosene_call_tool(root: Path, name: str, args: dict[str, Any]) -> Any:
    if name == "kerosene_cycle_once":
        return kerosene_cycle_once(root, args)
    if name == "kerosene_git_status":
        return kerosene_git_status(root, args)
    if name == "kerosene_clean_worktree":
        return kerosene_clean_worktree(root, args)
    if name == "kerosene_dispatch_next":
        return kerosene_dispatch_next(root, args)
    if name == "kerosene_collect_agent_result":
        return kerosene_collect_agent_result(root, args)
    if name == "kerosene_commit_agent_output":
        return kerosene_commit_agent_output(root, args)
    raise ReadOnlyMcpError(f"Unknown Kerosene orchestration tool: {name}")


def path_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def entry_info(root: Path, path: Path) -> dict[str, Any]:
    try:
        stat = path.lstat()
    except OSError as exc:
        return {
            "name": path.name,
            "path": relative_path(root, path) if is_relative_to(path, root) else str(path),
            "type": "unknown",
            "readable": False,
            "blocked_reason": str(exc),
        }

    info: dict[str, Any] = {
        "name": path.name,
        "path": relative_path(root, path) if is_relative_to(path, root) else path.name,
        "type": path_type(path),
        "size": stat.st_size,
        "modified": utc_iso(stat.st_mtime),
    }

    try:
        resolved = path.resolve(strict=True)
    except OSError:
        info["readable"] = False
        info["blocked_reason"] = "unresolvable_symlink_or_path"
        return info

    if not is_relative_to(resolved, root):
        info["readable"] = False
        info["blocked_reason"] = "outside_project_root"
    elif path.is_dir() and is_excluded_dir(path):
        info["readable"] = False
        info["blocked_reason"] = "excluded_directory"
    elif path.is_file() and is_sensitive_path(path):
        info["readable"] = False
        info["blocked_reason"] = "sensitive_path"
    elif path.is_file() and is_binary_suffix(path):
        info["readable"] = False
        info["blocked_reason"] = "binary_file"
    else:
        info["readable"] = True
    return info


def sorted_children(path: Path) -> list[Path]:
    return sorted(path.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower()))


def list_directory(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_existing_path(root, args.get("path"))
    if not path.is_dir():
        raise ReadOnlyMcpError(f"Path is not a directory: {relative_path(root, path)}")

    include_hidden = as_bool(args.get("include_hidden"), False)
    max_entries = clamp_int(args.get("max_entries"), DEFAULT_LIST_ENTRIES, 1, MAX_LIST_ENTRIES)
    entries: list[dict[str, Any]] = []
    skipped = {"hidden": 0, "excluded_directory": 0}
    truncated = False

    for child in sorted_children(path):
        if not include_hidden and is_hidden_name(child.name):
            skipped["hidden"] += 1
            continue
        if child.is_dir() and is_excluded_dir(child):
            skipped["excluded_directory"] += 1
            continue
        if len(entries) >= max_entries:
            truncated = True
            break
        entries.append(entry_info(root, child))

    return {
        "root": str(root),
        "path": relative_path(root, path),
        "entries": entries,
        "entry_count": len(entries),
        "truncated": truncated,
        "skipped": skipped,
    }


def read_file(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    if "path" not in args:
        raise ReadOnlyMcpError("read_file requires a path")
    path = ensure_readable_file(root, args["path"])
    size = path.stat().st_size
    offset = clamp_int(args.get("offset"), 0, 0, max(size, 0))
    max_bytes = clamp_int(args.get("max_bytes"), DEFAULT_READ_BYTES, 1, MAX_READ_BYTES)

    with path.open("rb") as handle:
        sample = handle.read(4096)
        if is_probably_binary(sample):
            raise ReadOnlyMcpError(f"Refusing to read binary file: {relative_path(root, path)}")
        handle.seek(offset)
        data = handle.read(max_bytes)

    text, encoding = decode_text(data)
    return {
        "path": relative_path(root, path),
        "size": size,
        "offset": offset,
        "bytes_read": len(data),
        "max_bytes": max_bytes,
        "truncated": offset + len(data) < size,
        "encoding": encoding,
        "content": text,
    }


def read_file_lines(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    if "path" not in args:
        raise ReadOnlyMcpError("read_file_lines requires a path")
    path = ensure_readable_file(root, args["path"])
    size = path.stat().st_size
    start_line = clamp_int(args.get("start_line"), 1, 1, 2_000_000_000)
    max_lines = clamp_int(args.get("max_lines"), DEFAULT_READ_LINES, 1, MAX_READ_LINES)
    max_chars = clamp_int(args.get("max_chars"), 1_000_000, 1_000, MAX_READ_LINES_CHARS)
    max_line_chars = clamp_int(args.get("max_line_chars"), MAX_READ_LINE_CHARS, 100, MAX_READ_LINE_CHARS)

    lines: list[dict[str, Any]] = []
    emitted_chars = 0
    last_line = start_line - 1
    truncated = False
    encoding = "utf-8"

    with path.open("rb") as handle:
        sample = handle.read(4096)
        if is_probably_binary(sample):
            raise ReadOnlyMcpError(f"Refusing to read binary file: {relative_path(root, path)}")
        handle.seek(0)
        for line_number, raw_line in enumerate(handle, start=1):
            if line_number < start_line:
                continue
            if len(lines) >= max_lines or emitted_chars >= max_chars:
                truncated = True
                break

            line_text, line_encoding = decode_text(raw_line)
            if line_encoding != "utf-8":
                encoding = line_encoding
            line_text = line_text.rstrip("\r\n")
            if len(line_text) > max_line_chars:
                line_text = truncate_text(line_text, max_line_chars)
            emitted_chars += len(line_text)
            lines.append({"line": line_number, "text": line_text})
            last_line = line_number

    return {
        "path": relative_path(root, path),
        "size": size,
        "start_line": start_line,
        "line_count": len(lines),
        "last_line": last_line if lines else None,
        "next_start_line": last_line + 1 if truncated else None,
        "max_lines": max_lines,
        "max_chars": max_chars,
        "truncated": truncated,
        "encoding": encoding,
        "lines": lines,
    }


def prune_dirnames(parent: Path, dirnames: list[str], include_hidden: bool, stats: Counter[str]) -> None:
    kept: list[str] = []
    for name in sorted(dirnames, key=str.lower):
        if is_excluded_dir(parent / name):
            stats["excluded_directories"] += 1
            continue
        if not include_hidden and is_hidden_name(name):
            stats["hidden_directories"] += 1
            continue
        kept.append(name)
    dirnames[:] = kept


def iter_text_files(
    root: Path,
    start: Path,
    *,
    include_hidden: bool,
    max_files: int,
    max_file_bytes: int,
    stats: Counter[str],
) -> Iterable[Path]:
    yielded = 0
    for dirpath, dirnames, filenames in os.walk(start):
        prune_dirnames(Path(dirpath), dirnames, include_hidden, stats)
        for filename in sorted(filenames, key=str.lower):
            if yielded >= max_files:
                stats["max_files_reached"] += 1
                return
            if not include_hidden and is_hidden_name(filename):
                stats["hidden_files"] += 1
                continue
            path = Path(dirpath) / filename
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                stats["unresolvable_files"] += 1
                continue
            if not is_relative_to(resolved, root):
                stats["outside_root_files"] += 1
                continue
            if is_sensitive_path(path):
                stats["sensitive_files"] += 1
                continue
            if is_binary_suffix(path):
                stats["binary_suffix_files"] += 1
                continue
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except OSError:
                stats["stat_errors"] += 1
                continue
            if size > max_file_bytes:
                stats["large_files"] += 1
                continue
            yielded += 1
            yield path


def search_text(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "")
    if not query:
        raise ReadOnlyMcpError("search_text requires a non-empty query")

    started_at = time.monotonic()
    start = resolve_existing_path(root, args.get("path"))
    if start.is_file():
        start = start.parent
    include_hidden = as_bool(args.get("include_hidden"), False)
    case_sensitive = as_bool(args.get("case_sensitive"), False)
    max_results = clamp_int(args.get("max_results"), DEFAULT_SEARCH_RESULTS, 1, MAX_SEARCH_RESULTS)
    max_files = clamp_int(args.get("max_files"), DEFAULT_SEARCH_FILES, 1, MAX_SEARCH_FILES)
    context_lines = clamp_int(args.get("context_lines"), 0, 0, MAX_CONTEXT_LINES)
    max_file_bytes = clamp_int(args.get("max_file_bytes"), MAX_SEARCH_FILE_BYTES, 1_000, MAX_SEARCH_FILE_BYTES)
    timeout_seconds = clamp_float(
        args.get("timeout_seconds"),
        DEFAULT_SEARCH_TIMEOUT_SECONDS,
        1.0,
        MAX_SEARCH_TIMEOUT_SECONDS,
    )
    max_response_chars = clamp_int(
        args.get("max_response_chars"),
        DEFAULT_SEARCH_RESPONSE_CHARS,
        10_000,
        MAX_SEARCH_RESPONSE_CHARS,
    )
    deadline = started_at + timeout_seconds

    needle = query if case_sensitive else query.lower()
    stats: Counter[str] = Counter()
    results: list[dict[str, Any]] = []
    emitted_result_chars = 0

    def response(*, truncated: bool, timed_out: bool = False, truncation_reason: str | None = None) -> dict[str, Any]:
        elapsed = time.monotonic() - started_at
        payload: dict[str, Any] = {
            "query": query,
            "path": relative_path(root, start),
            "case_sensitive": case_sensitive,
            "results": results,
            "result_count": len(results),
            "truncated": truncated,
            "timed_out": timed_out,
            "timeout_seconds": timeout_seconds,
            "elapsed_seconds": round(elapsed, 3),
            "max_response_chars": max_response_chars,
            "stats": dict(stats),
        }
        if truncation_reason:
            payload["truncation_reason"] = truncation_reason
        return payload

    for path in iter_text_files(
        root,
        start,
        include_hidden=include_hidden,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        stats=stats,
    ):
        if time.monotonic() >= deadline:
            stats["search_timeout_reached"] += 1
            return response(truncated=True, timed_out=True, truncation_reason="timeout")

        stats["scanned_files"] += 1
        try:
            data = path.read_bytes()
        except OSError:
            stats["read_errors"] += 1
            continue
        if time.monotonic() >= deadline:
            stats["search_timeout_reached"] += 1
            return response(truncated=True, timed_out=True, truncation_reason="timeout")
        if is_probably_binary(data):
            stats["binary_content_files"] += 1
            continue
        text, _encoding = decode_text(data)
        lines = text.splitlines()
        for line_number, line in enumerate(lines, start=1):
            if line_number % 256 == 0 and time.monotonic() >= deadline:
                stats["search_timeout_reached"] += 1
                return response(truncated=True, timed_out=True, truncation_reason="timeout")
            haystack = line if case_sensitive else line.lower()
            column = haystack.find(needle)
            if column < 0:
                continue
            item: dict[str, Any] = {
                "path": relative_path(root, path),
                "line": line_number,
                "column": column + 1,
                "text": truncate_text(line),
            }
            if context_lines:
                before_start = max(0, line_number - 1 - context_lines)
                after_end = min(len(lines), line_number + context_lines)
                item["before"] = [
                    {"line": index + 1, "text": truncate_text(lines[index])}
                    for index in range(before_start, line_number - 1)
                ]
                item["after"] = [
                    {"line": index + 1, "text": truncate_text(lines[index])}
                    for index in range(line_number, after_end)
                ]
            item_chars = len(json.dumps(item, ensure_ascii=False, sort_keys=True))
            if emitted_result_chars + item_chars > max_response_chars:
                stats["response_char_budget_reached"] += 1
                return response(truncated=True, truncation_reason="response_char_budget")
            results.append(item)
            emitted_result_chars += item_chars
            if len(results) >= max_results:
                return response(truncated=True, truncation_reason="max_results")

    return response(truncated=False)


def visible_tree_children(root: Path, path: Path, include_hidden: bool, stats: Counter[str]) -> list[Path]:
    children: list[Path] = []
    for child in sorted_children(path):
        if not include_hidden and is_hidden_name(child.name):
            stats["hidden_entries"] += 1
            continue
        try:
            resolved = child.resolve(strict=True)
        except OSError:
            stats["unresolvable_entries"] += 1
            continue
        if not is_relative_to(resolved, root):
            stats["outside_root_entries"] += 1
            continue
        if child.is_dir() and is_excluded_dir(child):
            stats["excluded_directories"] += 1
            continue
        children.append(child)
    return children


def get_project_tree(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    start = resolve_existing_path(root, args.get("path"))
    if not start.is_dir():
        raise ReadOnlyMcpError(f"Path is not a directory: {relative_path(root, start)}")

    include_hidden = as_bool(args.get("include_hidden"), False)
    include_files = as_bool(args.get("include_files"), True)
    max_depth = clamp_int(args.get("max_depth"), 4, 1, 16)
    max_entries = clamp_int(args.get("max_entries"), DEFAULT_TREE_ENTRIES, 1, MAX_TREE_ENTRIES)
    stats: Counter[str] = Counter()
    lines = [f"{relative_path(root, start)}/"]
    emitted = 0
    truncated = False

    def walk(path: Path, prefix: str, depth: int) -> None:
        nonlocal emitted, truncated
        if truncated or depth >= max_depth:
            return
        children = visible_tree_children(root, path, include_hidden, stats)
        if not include_files:
            children = [child for child in children if child.is_dir() and not child.is_symlink()]
        for index, child in enumerate(children):
            if emitted >= max_entries:
                lines.append(prefix + "`-- ...")
                truncated = True
                return
            is_last = index == len(children) - 1
            marker = "`-- " if is_last else "|-- "
            child_is_dir = child.is_dir() and not child.is_symlink()
            child_label = child.name + ("/" if child_is_dir else "")
            if child.is_file() and is_sensitive_path(child):
                child_label += " [blocked:sensitive]"
            elif child.is_file() and is_binary_suffix(child):
                child_label += " [binary]"
            elif child.is_symlink():
                child_label += " [symlink]"
            lines.append(prefix + marker + child_label)
            emitted += 1
            if child_is_dir:
                next_prefix = prefix + ("    " if is_last else "|   ")
                walk(child, next_prefix, depth + 1)

    walk(start, "", 0)
    return {
        "root": str(root),
        "path": relative_path(root, start),
        "tree": "\n".join(lines),
        "entry_count": emitted,
        "max_depth": max_depth,
        "truncated": truncated,
        "stats": dict(stats),
    }


def project_summary(root: Path, _args: dict[str, Any]) -> dict[str, Any]:
    stats: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    top_level_counts: Counter[str] = Counter()
    max_files = DEFAULT_SEARCH_FILES
    scanned = 0

    for path in iter_text_files(
        root,
        root,
        include_hidden=False,
        max_files=max_files,
        max_file_bytes=MAX_SEARCH_FILE_BYTES,
        stats=stats,
    ):
        scanned += 1
        rel = path.relative_to(root)
        top_level_counts[rel.parts[0] if rel.parts else "."] += 1
        extension_counts[path.suffix.lower() or "<none>"] += 1

    important_files = [
        "README.md",
        "docs/backend/PROJECT_CONSOLIDATED_SUMMARY.md",
        "docs/backend/API_REFERENCE.md",
        "docs/backend/api/WALLET.md",
        "backend/kerosene/build.gradle.kts",
        "frontend/pubspec.yaml",
        "backend/mpc-sidecar/go.mod",
    ]

    components: list[dict[str, str]] = []
    if (root / "backend/kerosene/build.gradle.kts").exists():
        components.append({"name": "backend/kerosene", "stack": "Java/Kotlin Gradle backend"})
    if (root / "frontend/pubspec.yaml").exists():
        components.append({"name": "frontend", "stack": "Flutter/Dart app"})
    if (root / "backend/mpc-sidecar/go.mod").exists():
        components.append({"name": "backend/mpc-sidecar", "stack": "Go MPC sidecar"})
    if (root / "infra").exists():
        components.append({"name": "infra", "stack": "Docker/Kubernetes/runtime infrastructure"})
    if (root / "docs").exists():
        components.append({"name": "docs", "stack": "Project documentation"})

    return {
        "root": str(root),
        "server": SERVER_NAME,
        "workspace_guards": [
            "all paths are resolved under the configured project root",
            "all non-sensitive project files are writable, including backend, frontend, docs, scripts, and infrastructure files",
            "sensitive files such as .env files, private keys, .git internals, local databases, and secrets directories are refused",
            "shell commands run from inside the project root and inherit the full server environment by default",
            "set KEROSENE_MCP_SCRUB_SHELL_ENV=1 to strip secret-like environment variables before shell execution",
            "binary and oversized files are skipped for search",
        ],
        "components": components,
        "important_files": [path for path in important_files if (root / path).exists()],
        "top_level_text_file_counts": dict(top_level_counts.most_common(20)),
        "extension_counts": dict(extension_counts.most_common(25)),
        "scanned_text_files": scanned,
        "skipped": dict(stats),
    }



class McpServer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def send(self, payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def result(self, request_id: Any, result: Any) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def error(self, request_id: Any, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            return
        try:
            if method == "initialize":
                params = message.get("params") or {}
                self.result(
                    request_id,
                    {
                        "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    },
                )
            elif method == "tools/list":
                self.result(request_id, {"tools": tool_schema()})
            elif method == "tools/call":
                params = message.get("params") or {}
                name = str(params.get("name") or "")
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    raise ReadOnlyMcpError("Tool arguments must be a JSON object")
                value = call_tool(self.root, name, args)
                self.result(
                    request_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": compact_tool_value(value),
                            }
                        ]
                    },
                )
            elif method in {"resources/list", "prompts/list"}:
                key = "resources" if method.startswith("resources/") else "prompts"
                self.result(request_id, {key: []})
            elif method == "ping":
                self.result(request_id, {})
            else:
                self.error(request_id, -32601, f"Method not found: {method}")
        except ReadOnlyMcpError as exc:
            self.result(
                request_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": str(exc)}],
                },
            )
        except Exception as exc:  # Keep the MCP process alive after unexpected tool failures.
            print(f"{SERVER_NAME}: unexpected error: {exc}", file=sys.stderr)
            self.result(
                request_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Unexpected error: {exc}"}],
                },
            )

    def serve(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"{SERVER_NAME}: invalid JSON-RPC message: {exc}", file=sys.stderr)
                continue
            if isinstance(message, dict):
                self.handle(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP server for the Kerosene project.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Project root exposed through MCP.")
    return parser.parse_args()




# ==========================================
# META TOOLS ROUTING PATCH (V2)
# ==========================================
def tool_schema():
    return [
        {
            "name": "Kerosene.Project",
            "description": "Navegar pelo projeto, listar diretorios, arvores de arquivos e resumos.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["tree", "list", "summary"]},
                    "path": {"type": "string", "description": "Caminho relativo (para tree ou list)"}
                },
                "required": ["action"]
            }
        },
        {
            "name": "Kerosene.Git",
            "description": "Ver status, limpar worktree ou commitar outputs de agentes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "clean", "dispatch_next", "collect", "commit"]},
                    "message": {"type": "string"}
                },
                "required": ["action"]
            }
        },
        {
            "name": "Kerosene.ReadCode",
            "description": "Ler um arquivo. Forneca start_line e end_line para trechos parciais.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "Kerosene.Search",
            "description": "Pesquisar texto ou regex em arquivos do projeto.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["code", "text", "regex"], "default": "code"},
                    "path": {"type": "string", "description": "Pasta/arquivo opcional"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "Kerosene.Edit",
            "description": "Modificar arquivos. Use 'resilient' ou 'patch' para refatoracoes seguras.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["write", "replace", "multi_edit", "patch", "resilient", "rollback"]},
                    "path": {"type": "string", "description": "Para write, replace, rollback"},
                    "content": {"type": "string", "description": "Para write"},
                    "old_text": {"type": "string", "description": "Para replace"},
                    "new_text": {"type": "string", "description": "Para replace"},
                    "patch": {"type": "string", "description": "Para action=patch"},
                    "edits": {"type": "array", "description": "Para action=multi_edit ou resilient"},
                    "instruction": {"type": "string", "description": "Para resilient"}
                },
                "required": ["action"]
            }
        },
        {
            "name": "Kerosene.Validate",
            "description": "Rodar validacoes pre-configuradas (frontend, backend).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "enum": ["frontend", "backend", "changed", "mcp"]}
                },
                "required": ["target"]
            }
        },
        {
            "name": "Kerosene.System",
            "description": "Mapear processos, portas e recursos do sistema com saida segura e compacta.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["summary", "processes", "ports", "resources"]},
                    "query": {"type": "string", "description": "Filtro opcional por nome, usuario ou comando"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 500, "default": 80},
                    "include_cmdline": {"type": "boolean", "default": true},
                    "listen_only": {"type": "boolean", "default": true}
                },
                "required": ["action"]
            }
        },
        {
            "name": "Fleet",
            "description": "Controlar frotas de agentes (Codex, Agy).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fleet": {"type": "string", "enum": ["codex", "agy"], "default": "codex"},
                    "action": {"type": "string", "enum": ["start", "stop", "resume", "status", "tail", "users", "quota", "usage", "preflight"]},
                    "agent_id": {"type": "string"},
                    "task": {"type": "string"}
                },
                "required": ["fleet", "action"]
            }
        }
    ]

# Monta o INTERNAL_TOOLS juntando tudo com apenas as ferramentas internas locais
def build_internal_tools() -> dict[str, Any]:
    return {
        "get_project_tree": get_project_tree,
        "list_directory": list_directory,
        "project_summary": project_summary,
        "kerosene_git_status": kerosene_git_status,
        "kerosene_clean_worktree": kerosene_clean_worktree,
        "kerosene_dispatch_next": kerosene_dispatch_next,
        "kerosene_collect_agent_result": kerosene_collect_agent_result,
        "kerosene_commit_agent_output": kerosene_commit_agent_output,
        "read_file": read_file,
        "read_file_lines": read_file_lines,
        "search_text": search_text,
        "search_code": search_text,
        "run_rg": run_rg,
        "write_file": write_file,
        "replace_text_in_file": replace_text_in_file,
        "multi_edit": multi_edit,
        "apply_patch": apply_patch,
        "resilient_apply_change": resilient_apply_change,
        "rollback_last_patch": rollback_last_patch,
        "validate_frontend": validate_frontend,
        "validate_backend": validate_backend,
        "validate_changed_files": validate_changed_files,
        "validate_mcp": validate_mcp,
        "system_summary": system_summary,
        "system_processes": system_processes,
        "system_ports": system_ports,
        "system_resources": system_resources,
    }

def _truncate_system_text(value: Any, max_chars: int = 500) -> Any:
    if value is None:
        return None
    text = str(value).replace("\x00", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 40)] + f"... [truncated {len(text) - max(0, max_chars - 40)} chars]"


def _redact_cmdline(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(?i)(--?(?:password|passwd|token|secret|api[-_]?key|authorization|bearer)(?:=|\s+))\S+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:password|passwd|token|secret|api[-_]?key|authorization|bearer)=)\S+", r"\1[redacted]", text)
    return text


def _read_proc_text(path: Path, max_chars: int = 8192) -> str:
    try:
        data = path.read_bytes()[:max_chars]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _proc_status_field(status_text: str, field: str) -> str | None:
    prefix = field + ":"
    for line in status_text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _uid_to_user(uid_text: str | None) -> str | None:
    if not uid_text:
        return None
    uid = uid_text.split()[0]
    try:
        import pwd
        return pwd.getpwuid(int(uid)).pw_name
    except Exception:
        return uid


def _proc_cmdline(pid: str, max_chars: int = 800) -> str:
    raw = _read_proc_text(Path("/proc") / pid / "cmdline", max_chars * 2)
    text = raw.replace("\x00", " ").strip()
    if not text:
        text = _read_proc_text(Path("/proc") / pid / "comm", max_chars).strip()
    return _truncate_system_text(_redact_cmdline(text), max_chars) or ""


def system_processes(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    max_results = clamp_int(args.get("max_results"), 80, 1, 500)
    include_cmdline = as_bool(args.get("include_cmdline"), True)
    query = str(args.get("query") or "").strip().lower()
    processes: list[dict[str, Any]] = []
    proc_root = Path("/proc")
    for proc_dir in proc_root.iterdir() if proc_root.exists() else []:
        pid = proc_dir.name
        if not pid.isdigit():
            continue
        status_text = _read_proc_text(proc_dir / "status")
        if not status_text:
            continue
        name = _proc_status_field(status_text, "Name") or ""
        uid_text = _proc_status_field(status_text, "Uid")
        user = _uid_to_user(uid_text)
        state = _proc_status_field(status_text, "State")
        ppid_text = _proc_status_field(status_text, "PPid")
        rss_text = _proc_status_field(status_text, "VmRSS")
        cmdline = _proc_cmdline(pid) if include_cmdline else ""
        haystack = " ".join(str(part or "") for part in (pid, name, user, state, cmdline)).lower()
        if query and query not in haystack:
            continue
        processes.append({
            "pid": int(pid),
            "ppid": int(ppid_text) if ppid_text and ppid_text.isdigit() else None,
            "user": user,
            "name": name,
            "state": state,
            "rss_kb": int(rss_text.split()[0]) if rss_text and rss_text.split()[0].isdigit() else None,
            "cmdline": cmdline if include_cmdline else None,
        })
    processes.sort(key=lambda item: item.get("pid") or 0)
    total = len(processes)
    return {
        "ok": True,
        "action": "processes",
        "process_count": total,
        "returned": min(total, max_results),
        "truncated": total > max_results,
        "processes": processes[:max_results],
    }


def system_resources(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    meminfo: dict[str, int] = {}
    for line in _read_proc_text(Path("/proc/meminfo"), 12000).splitlines():
        parts = line.replace(":", "").split()
        if len(parts) >= 2 and parts[1].isdigit():
            meminfo[parts[0]] = int(parts[1])
    loadavg = _read_proc_text(Path("/proc/loadavg"), 200).strip()
    uptime_parts = _read_proc_text(Path("/proc/uptime"), 100).split()
    disk = shutil.disk_usage(root)
    total_kb = meminfo.get("MemTotal")
    available_kb = meminfo.get("MemAvailable")
    return {
        "ok": True,
        "action": "resources",
        "cpu_count": os.cpu_count(),
        "loadavg": loadavg,
        "uptime_seconds": float(uptime_parts[0]) if uptime_parts else None,
        "memory": {
            "total_kb": total_kb,
            "available_kb": available_kb,
            "used_percent": round((1 - (available_kb / total_kb)) * 100, 2) if total_kb and available_kb is not None else None,
        },
        "project_disk": {
            "path": str(root),
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": round((disk.used / disk.total) * 100, 2) if disk.total else None,
        },
    }


def _decode_proc_ipv4(hex_value: str) -> str:
    try:
        return ".".join(str(part) for part in reversed(bytes.fromhex(hex_value)))
    except Exception:
        return hex_value


def _socket_inode_process_map(max_pids: int = 5000) -> dict[str, list[int]]:
    inode_map: dict[str, list[int]] = {}
    proc_root = Path("/proc")
    scanned = 0
    for proc_dir in proc_root.iterdir() if proc_root.exists() else []:
        pid = proc_dir.name
        if not pid.isdigit():
            continue
        scanned += 1
        if scanned > max_pids:
            break
        fd_dir = proc_dir / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds[:512]:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if match:
                inode_map.setdefault(match.group(1), []).append(int(pid))
    return inode_map


def system_ports(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    max_results = clamp_int(args.get("max_results"), 120, 1, 500)
    listen_only = as_bool(args.get("listen_only"), True)
    query = str(args.get("query") or "").strip().lower()
    inode_map = _socket_inode_process_map()
    tcp_states = {"0A": "LISTEN", "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK"}
    entries: list[dict[str, Any]] = []
    for rel_path, protocol in (("tcp", "tcp"), ("tcp6", "tcp6"), ("udp", "udp"), ("udp6", "udp6")):
        path = Path("/proc/net") / rel_path
        lines = _read_proc_text(path, 1_000_000).splitlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            local = parts[1]
            state_code = parts[3]
            inode = parts[9]
            if listen_only and protocol.startswith("tcp") and state_code != "0A":
                continue
            addr_hex, port_hex = local.split(":", 1)
            address = _decode_proc_ipv4(addr_hex) if protocol == "tcp" or protocol == "udp" else ("::" if set(addr_hex) == {"0"} else addr_hex)
            port = int(port_hex, 16)
            pids = sorted(set(inode_map.get(inode, [])))[:8]
            entry = {
                "protocol": protocol,
                "local_address": address,
                "port": port,
                "state": tcp_states.get(state_code, state_code),
                "inode": inode,
                "pids": pids,
            }
            haystack = json.dumps(entry, sort_keys=True).lower()
            if query and query not in haystack:
                continue
            entries.append(entry)
    entries.sort(key=lambda item: (str(item["protocol"]), int(item["port"]), str(item["local_address"])))
    total = len(entries)
    return {
        "ok": True,
        "action": "ports",
        "port_count": total,
        "returned": min(total, max_results),
        "truncated": total > max_results,
        "listen_only": listen_only,
        "ports": entries[:max_results],
    }


def system_summary(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    process_args = {**args, "max_results": min(clamp_int(args.get("max_results"), 20, 1, 100), 40), "include_cmdline": False}
    port_args = {**args, "max_results": min(clamp_int(args.get("max_results"), 40, 1, 120), 80), "listen_only": True}
    return {
        "ok": True,
        "action": "summary",
        "resources": system_resources(root, args),
        "processes": system_processes(root, process_args),
        "ports": system_ports(root, port_args),
    }


def _compact_fleet_text(value: Any, max_chars: int = 500) -> Any:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_chars:
        return value
    return text[: max(0, max_chars - 40)] + f"... [truncated {len(text) - max(0, max_chars - 40)} chars]"


def _compact_fleet_record(record: Any) -> Any:
    if not isinstance(record, dict):
        return record
    errors = record.get("errors") if isinstance(record.get("errors"), list) else []
    usage = record.get("usage") if isinstance(record.get("usage"), list) else []
    last_usage = usage[-1] if usage and isinstance(usage[-1], dict) else None
    return {
        "agent_id": record.get("agent_id"),
        "alive": record.get("alive"),
        "status": record.get("status"),
        "last_status": record.get("last_status"),
        "last_event_type": record.get("last_event_type"),
        "model": record.get("model"),
        "run_as_user": record.get("run_as_user"),
        "cwd": record.get("cwd"),
        "started_at": record.get("started_at"),
        "updated_at": record.get("updated_at"),
        "event_count": record.get("event_count"),
        "error_count": len(errors),
        "last_error": _compact_fleet_text(errors[-1].get("message") if errors and isinstance(errors[-1], dict) else None, 360),
        "last_task": _compact_fleet_text(record.get("last_task"), 420),
        "last_agent_message": _compact_fleet_text(record.get("last_agent_message"), 420),
        "last_usage": last_usage,
    }


def compact_fleet_response(value: Any, *, max_agents: int = 16) -> Any:
    if not isinstance(value, dict):
        return value
    compact = dict(value)
    agents = value.get("agents")
    if isinstance(agents, dict):
        items = sorted(agents.items())
        compact["agents"] = {agent_id: _compact_fleet_record(record) for agent_id, record in items[:max_agents]}
        compact["agent_count"] = len(items)
        compact["truncated_agents"] = len(items) > max_agents
    for key in ("stdout", "stderr", "last_task", "last_agent_message"):
        if key in compact:
            compact[key] = _compact_fleet_text(compact[key], 800)
    compact["compact"] = True
    return compact


INTERNAL_TOOLS = build_internal_tools()

def call_tool(root, name, args):
    if not isinstance(args, dict): args = {}
    
    if name == "Kerosene.Project":
        action = args.get("action")
        if action == "tree": return INTERNAL_TOOLS["get_project_tree"](root, args)
        if action == "list": return INTERNAL_TOOLS["list_directory"](root, args)
        if action == "summary": return INTERNAL_TOOLS["project_summary"](root, args)
        
    elif name == "Kerosene.Git":
        action = args.get("action")
        if action == "status": return INTERNAL_TOOLS["kerosene_git_status"](root, args)
        if action == "clean": return INTERNAL_TOOLS["kerosene_clean_worktree"](root, args)
        if action == "dispatch_next": return INTERNAL_TOOLS["kerosene_dispatch_next"](root, args)
        if action == "collect": return INTERNAL_TOOLS["kerosene_collect_agent_result"](root, args)
        if action == "commit": return INTERNAL_TOOLS["kerosene_commit_agent_output"](root, args)

    elif name == "Kerosene.ReadCode":
        if "start_line" in args or "end_line" in args:
            translated = dict(args)
            if "end_line" in args:
                start = int(args.get("start_line") or 1)
                end = int(args["end_line"])
                translated["max_lines"] = max(1, end - start + 1)
                translated.pop("end_line", None)
            return INTERNAL_TOOLS["read_file_lines"](root, translated)
        return INTERNAL_TOOLS["read_file"](root, args)

    elif name == "Kerosene.Search":
        return INTERNAL_TOOLS["run_rg"](root, {
            "query": args.get("query", ""),
            "path": args.get("path", "."),
            "max_count": args.get("max_results", 200)
        })

    elif name == "Kerosene.Edit":
        action = args.get("action")
        if action == "write": return INTERNAL_TOOLS["write_file"](root, args)
        if action == "replace": return INTERNAL_TOOLS["replace_text_in_file"](root, args)
        if action == "multi_edit": return INTERNAL_TOOLS["multi_edit"](root, args)
        if action == "patch": return INTERNAL_TOOLS["apply_patch"](root, args)
        if action == "resilient": return INTERNAL_TOOLS["resilient_apply_change"](root, args)
        if action == "rollback": return INTERNAL_TOOLS["rollback_last_patch"](root, args)

    elif name == "Kerosene.Validate":
        target = args.get("target")
        if target == "frontend": return INTERNAL_TOOLS["validate_frontend"](root, args)
        if target == "backend": return INTERNAL_TOOLS["validate_backend"](root, args)
        if target == "changed": return INTERNAL_TOOLS["validate_changed_files"](root, args)
        if target == "mcp": return INTERNAL_TOOLS["validate_mcp"](root, args)

    elif name == "Kerosene.System":
        action = args.get("action") or "summary"
        if action == "summary": return INTERNAL_TOOLS["system_summary"](root, args)
        if action == "processes": return INTERNAL_TOOLS["system_processes"](root, args)
        if action == "ports": return INTERNAL_TOOLS["system_ports"](root, args)
        if action == "resources": return INTERNAL_TOOLS["system_resources"](root, args)

    elif name == "Fleet":
        fleet = args.get("fleet", "codex")
        action = args.get("action")
        prefix = "agy" if fleet == "agy" else "fleet"
        
        tool_name = f"{prefix}_{action}"
        if action == "start": tool_name = f"{prefix}_start_worker"
        elif action == "stop": tool_name = f"{prefix}_stop_worker"
        elif action == "resume": tool_name = f"{prefix}_resume_worker"
        elif action == "users": tool_name = f"{prefix}_agent_users"
        elif action == "quota": tool_name = f"{prefix}_quota_probe_all"
        elif action == "usage": tool_name = f"{prefix}_usage_report"
        
        script = AGY_FLEET_SCRIPT if fleet == "agy" else CODEX_FLEET_SCRIPT
        result = call_mcp_proxy(script, tool_name, args, timeout_seconds=proxy_timeout_seconds(tool_name, args))
        return compact_fleet_response(result)

    # Fallback to direct internal tools call
    if name in INTERNAL_TOOLS:
        return INTERNAL_TOOLS[name](root, args)
    raise RuntimeError(f"Unknown tool: {name}")

# ==========================================


def main() -> None:
    args = parse_args()
    root = resolve_root(Path(args.root))
    McpServer(root).serve()

if __name__ == "__main__":
    main()
