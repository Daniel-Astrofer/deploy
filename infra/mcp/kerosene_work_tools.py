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
from typing import Any, Callable


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


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkToolError(f"Expected integer value, got {value!r}") from exc
    return max(minimum, min(maximum, parsed))


def is_relative_to(path: Path, root: Path) -> bool:
    root = root.resolve()
    path = path.resolve(strict=False)
    return path == root or root in path.parents


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
        return run(root, ["rg", "--line-number", "--max-count", str(max_count), query, path], timeout_seconds=300)
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


def passthrough_validation(name: str, fn: Callable[[Path, dict[str, Any]], dict[str, Any]]) -> Callable[[Path, dict[str, Any]], dict[str, Any]]:
    return fn


TOOLS: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {
    "apply_patch": apply_patch,
    "multi_edit": multi_edit,
    "move_path": move_path,
    "copy_path": copy_path,
    "mkdir": mkdir,
    "delete_path_safe": delete_path_safe,
    "format_paths": format_paths,
    "context_pack": context_pack,
    "git_status_compact": git_status_compact,
    "git_diff_summary": git_diff_summary,
    "git_diff_file": git_diff_file,
    "git_changed_files": lambda root, args: {"changed_files": changed_files(root)},
    "batch_read_files": batch_read_files,
    "validate_frontend": validate_frontend,
    "validate_backend": validate_backend,
    "validate_mcp": validate_mcp,
    "validate_changed_files": validate_changed_files,
    "session_start": session_start,
    "session_status": session_status,
    "session_note": session_note,
    "session_finish": session_finish,
    "run_rg": run_rg,
    "run_dart_format": format_paths,
    "run_flutter_analyze": lambda root, args: named_validation(root, "flutter analyze", ["flutter", "analyze"], cwd=root / "frontend", timeout_seconds=1200),
    "run_flutter_test": lambda root, args: named_validation(root, "flutter test", ["flutter", "test"], cwd=root / "frontend", timeout_seconds=1800),
    "run_gradle_test": validate_backend,
    "run_git_diff_check": lambda root, args: named_validation(root, "git diff check", ["git", "diff", "--check", "--", *(args.get("paths") or changed_files(root))], timeout_seconds=120),
    "find_references": find_references,
    "rename_symbol_safe": rename_symbol_safe,
    "dart_import_rewrite": dart_import_rewrite,
    "extract_symbol": lambda root, args: {"supported": False, "message": "Use context_pack, find_references, multi_edit and apply_patch for controlled extraction."},
}


def path_pair_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": False}}, "required": ["source", "destination"], "additionalProperties": False}


def paths_schema(required: bool = False) -> dict[str, Any]:
    schema = {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": False}
    if required:
        schema["required"] = ["paths"]
    return schema


def tool_schema() -> list[dict[str, Any]]:
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    return [
        {"name": "apply_patch", "description": "Apply a unified diff under the project root after a git-apply check. Supports dry_run.", "inputSchema": {"type": "object", "properties": {"patch": {"type": "string"}, "dry_run": {"type": "boolean", "default": False}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1200, "default": 120}}, "required": ["patch"], "additionalProperties": False}},
        {"name": "multi_edit", "description": "Apply multiple exact replacements to one file and return a compact diff.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}, "replace_all": {"type": "boolean", "default": True}}, "required": ["old_text", "new_text"], "additionalProperties": False}}, "dry_run": {"type": "boolean", "default": False}}, "required": ["path", "edits"], "additionalProperties": False}},
        {"name": "move_path", "description": "Move a non-protected file or directory under the project root.", "inputSchema": path_pair_schema()},
        {"name": "copy_path", "description": "Copy a non-protected file or directory under the project root.", "inputSchema": path_pair_schema()},
        {"name": "mkdir", "description": "Create a directory under the project root.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean", "default": False}}, "required": ["path"], "additionalProperties": False}},
        {"name": "delete_path_safe", "description": "Delete a non-protected path. Defaults to dry_run; directories require recursive=true.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}, "recursive": {"type": "boolean", "default": False}, "max_entries": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 200}}, "required": ["path"], "additionalProperties": False}},
        {"name": "format_paths", "description": "Run format/compile checks for supplied or changed paths.", "inputSchema": paths_schema()},
        {"name": "context_pack", "description": "Build compact implementation context: tree, imports, symbols, git status and diffstat.", "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "max_chars": {"type": "integer", "minimum": 4000, "maximum": MAX_CONTEXT_CHARS, "default": 60000}}, "additionalProperties": False}},
        {"name": "git_status_compact", "description": "Return branch, short status and changed files.", "inputSchema": empty},
        {"name": "git_diff_summary", "description": "Return diff stat and name-status, optionally scoped to paths.", "inputSchema": paths_schema()},
        {"name": "git_diff_file", "description": "Return bounded git diff for one file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 1000, "maximum": 500000, "default": 60000}}, "required": ["path"], "additionalProperties": False}},
        {"name": "git_changed_files", "description": "Return changed files from git status --short.", "inputSchema": empty},
        {"name": "batch_read_files", "description": "Read multiple text files with per-file truncation.", "inputSchema": {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}}, "max_chars_each": {"type": "integer", "minimum": 1000, "maximum": 200000, "default": 30000}}, "required": ["paths"], "additionalProperties": False}},
        {"name": "validate_frontend", "description": "Run frontend validation bundle.", "inputSchema": empty},
        {"name": "validate_backend", "description": "Run backend validation bundle.", "inputSchema": empty},
        {"name": "validate_mcp", "description": "Compile-check server tooling.", "inputSchema": empty},
        {"name": "validate_changed_files", "description": "Validate changed files with targeted checks.", "inputSchema": paths_schema()},
        {"name": "session_start", "description": "Start a local work session state file under tmp/mcp.", "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}}, "additionalProperties": False}},
        {"name": "session_status", "description": "Read local work session state.", "inputSchema": empty},
        {"name": "session_note", "description": "Append a note to local session state.", "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"], "additionalProperties": False}},
        {"name": "session_finish", "description": "Finish local work session state.", "inputSchema": empty},
        {"name": "run_rg", "description": "Run bounded ripgrep.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string", "default": "."}, "max_count": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 200}}, "required": ["query"], "additionalProperties": False}},
        {"name": "run_dart_format", "description": "Run dart format wrapper.", "inputSchema": paths_schema()},
        {"name": "run_flutter_analyze", "description": "Run flutter analyze wrapper.", "inputSchema": empty},
        {"name": "run_flutter_test", "description": "Run flutter test wrapper.", "inputSchema": empty},
        {"name": "run_gradle_test", "description": "Run Gradle test wrapper.", "inputSchema": empty},
        {"name": "run_git_diff_check", "description": "Run git diff --check wrapper.", "inputSchema": paths_schema()},
        {"name": "find_references", "description": "Find literal references to a symbol.", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "path": {"type": "string", "default": "."}}, "required": ["symbol"], "additionalProperties": False}},
        {"name": "rename_symbol_safe", "description": "Identifier-boundary rename across explicit files. Defaults to dry_run.", "inputSchema": {"type": "object", "properties": {"old": {"type": "string"}, "new": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "dry_run": {"type": "boolean", "default": True}}, "required": ["old", "new", "paths"], "additionalProperties": False}},
        {"name": "dart_import_rewrite", "description": "Rewrite relative Dart imports in one frontend/lib file into package imports when safe.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "package": {"type": "string", "default": "kerosene"}, "dry_run": {"type": "boolean", "default": False}}, "required": ["path"], "additionalProperties": False}},
        {"name": "extract_symbol", "description": "Dry-run guidance placeholder for controlled extraction workflows.", "inputSchema": empty},
    ]


def can_handle(name: str) -> bool:
    return name in TOOLS


def call_tool(root: Path, name: str, args: dict[str, Any]) -> Any:
    if name not in TOOLS:
        raise WorkToolError(f"Unknown work tool: {name}")
    return TOOLS[name](root.resolve(), args)

_BASE_TOOL_SCHEMA = tool_schema
try:
    import k_more_tools as _more_tools
except Exception as _more_tools_error:  # pragma: no cover
    _MORE_TOOLS_LOAD_ERROR = _more_tools_error
else:
    TOOLS.update(_more_tools.TOOLS)

    def tool_schema() -> list[dict[str, Any]]:  # type: ignore[no-redef]
        return [*_BASE_TOOL_SCHEMA(), *_more_tools.tool_schema()]

    def call_tool(root: Path, name: str, args: dict[str, Any]) -> Any:  # type: ignore[no-redef]
        if name in _more_tools.TOOLS:
            return _more_tools.call_tool(root.resolve(), name, args)
        if name not in TOOLS:
            raise WorkToolError(f"Unknown work tool: {name}")
        return TOOLS[name](root.resolve(), args)
