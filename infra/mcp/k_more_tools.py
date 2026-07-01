from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import kerosene_work_tools as base


ToolFn = Callable[[Path, dict[str, Any]], dict[str, Any]]
CHECKPOINT_ROOT_REL = Path("tmp/mcp/checkpoints")
LAST_CHANGE_REL = Path("tmp/mcp/last_change_manifest.json")


def _utc() -> str:
    return base.utc_now()


def _checkpoint_root(root: Path) -> Path:
    return base.resolve_target(root, CHECKPOINT_ROOT_REL.as_posix())


def _last_change_path(root: Path) -> Path:
    return base.resolve_target(root, LAST_CHANGE_REL.as_posix())


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
        raise base.WorkToolError("paths are required")
    if not isinstance(raw_paths, list):
        raise base.WorkToolError("paths must be a list")
    paths: list[str] = []
    for raw in raw_paths:
        if raw in (None, ""):
            continue
        target = base.resolve_target(root, str(raw))
        paths.append(base.rel(root, target))
    return sorted(set(paths))


def _patch_paths_from_list(patches: Any) -> list[str]:
    files: list[str] = []
    if isinstance(patches, list):
        for item in patches:
            patch = item.get("patch") if isinstance(item, dict) else item
            if isinstance(patch, str):
                files.extend(base.patch_paths(patch))
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
    source = base.resolve_target(root, source_rel)
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
        explicit_paths = base.changed_files(root)
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
        "git_status": base.git_out(root, ["status", "--short"], timeout_seconds=60),
        "entries": entries,
    }
    _write_json(checkpoint_dir / "manifest.json", manifest)
    _write_json(_last_change_path(root), {"checkpoint_id": checkpoint_id, "created_at": _utc(), "paths": paths, "reason": "checkpoint"})
    return {"checkpoint_id": checkpoint_id, "paths": paths, "entry_count": len(entries)}


def _latest_checkpoint(root: Path) -> str:
    root_dir = _checkpoint_root(root)
    if not root_dir.exists():
        raise base.WorkToolError("No checkpoints found")
    candidates = sorted([p.name for p in root_dir.iterdir() if p.is_dir()])
    if not candidates:
        raise base.WorkToolError("No checkpoints found")
    return candidates[-1]


def _manifest(root: Path, checkpoint_id: str | None) -> tuple[str, Path, dict[str, Any]]:
    cp_id = checkpoint_id or _latest_checkpoint(root)
    cp_dir = _checkpoint_root(root) / cp_id
    manifest = _read_json(cp_dir / "manifest.json", {})
    if not manifest:
        raise base.WorkToolError(f"Checkpoint not found: {cp_id}")
    return cp_id, cp_dir, manifest


def workspace_restore_session(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    cp_id, cp_dir, manifest = _manifest(root, args.get("checkpoint_id"))
    only_paths = set(_normalize_paths(root, args.get("paths"), allow_empty=True)) if args.get("paths") is not None else None
    dry_run = base.as_bool(args.get("dry_run"), False)
    restored: list[str] = []
    deleted: list[str] = []
    files_dir = cp_dir / "files"
    for entry in manifest.get("entries", []):
        path_rel = entry.get("path")
        if not isinstance(path_rel, str) or (only_paths is not None and path_rel not in only_paths):
            continue
        target = base.resolve_target(root, path_rel)
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
    after = base.git_out(root, ["status", "--short"], timeout_seconds=60)
    return {"checkpoint_id": cp_id, "before_status": before, "after_status": after, "changed_paths": _changed_since_status(before, after)}


def _record_last_change(root: Path, data: dict[str, Any]) -> None:
    data = {**data, "updated_at": _utc()}
    _write_json(_last_change_path(root), data)


def rollback_last_patch(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_json(_last_change_path(root), {})
    checkpoint_id = args.get("checkpoint_id") or manifest.get("checkpoint_id")
    if not checkpoint_id:
        raise base.WorkToolError("No last checkpoint is recorded")
    result = workspace_restore_session(root, {"checkpoint_id": checkpoint_id, "dry_run": base.as_bool(args.get("dry_run"), False)})
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
        raise base.WorkToolError("patch is required")
    pieces = []
    for index, piece in enumerate(_split_patch_by_file(patch), 1):
        files = base.patch_paths(piece)
        for file_path in files:
            base.resolve_target(root, file_path)
        pieces.append({"index": index, "files": files, "chars": len(piece), "patch": piece if base.as_bool(args.get("include_patches"), False) else None})
    return {"piece_count": len(pieces), "pieces": pieces}


def apply_patch_batch(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    patches = args.get("patches")
    if isinstance(args.get("patch"), str):
        patches = [{"patch": piece} for piece in _split_patch_by_file(str(args["patch"]))]
    if not isinstance(patches, list) or not patches:
        raise base.WorkToolError("patches or patch is required")
    dry_run = base.as_bool(args.get("dry_run"), False)
    rollback_on_failure = base.as_bool(args.get("rollback_on_failure"), True)
    files = _patch_paths_from_list(patches)
    checkpoint = workspace_checkpoint(root, {"paths": files, "label": args.get("label") or "patch-batch"}) if not dry_run else {"checkpoint_id": None, "paths": files}
    results = []
    try:
        for index, item in enumerate(patches, 1):
            patch = item.get("patch") if isinstance(item, dict) else item
            if not isinstance(patch, str) or not patch.strip():
                raise base.WorkToolError(f"invalid patch #{index}")
            result = base.apply_patch(root, {"patch": patch, "dry_run": dry_run, "timeout_seconds": args.get("timeout_seconds", 120)})
            results.append({"index": index, **result})
            if not result.get("applied") and not dry_run:
                raise base.WorkToolError(f"patch #{index} was not applied")
    except Exception as exc:
        restored = None
        if rollback_on_failure and checkpoint.get("checkpoint_id") and not dry_run:
            restored = workspace_restore_session(root, {"checkpoint_id": checkpoint["checkpoint_id"]})
        raise base.WorkToolError(f"apply_patch_batch failed: {exc}; rollback={bool(restored)}") from exc
    _record_last_change(root, {"type": "patch_batch", "checkpoint_id": checkpoint.get("checkpoint_id"), "paths": files, "results": results})
    return {"applied": not dry_run, "dry_run": dry_run, "checkpoint_id": checkpoint.get("checkpoint_id"), "files": files, "results": results}


def _group_edits(edits: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edit in edits:
        path = edit.get("path")
        if not isinstance(path, str) or not path:
            raise base.WorkToolError("each edit needs a path")
        grouped.setdefault(path, []).append({"old_text": edit.get("old_text"), "new_text": edit.get("new_text"), "replace_all": edit.get("replace_all", True)})
    return grouped


def _run_validation(root: Path, validation: Any, paths: list[str]) -> dict[str, Any]:
    value = str(validation or "changed_files").strip().lower()
    if value in {"none", "false", "skip"}:
        return {"status": "skipped"}
    if value in {"mcp", "tools"}:
        return base.validate_mcp(root, {})
    if value == "frontend":
        return base.validate_frontend(root, {})
    if value == "backend":
        return base.validate_backend(root, {})
    return base.validate_changed_files(root, {"paths": paths})


def resilient_apply_change(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    goal = str(args.get("goal") or "resilient change")
    patches = args.get("patches") or ([] if not isinstance(args.get("patch"), str) else [{"patch": args.get("patch")}])
    edits = args.get("edits") or []
    if not patches and not edits:
        raise base.WorkToolError("patch, patches or edits are required")
    dry_run = base.as_bool(args.get("dry_run"), False)
    rollback_on_failure = base.as_bool(args.get("rollback_on_failure"), True)
    paths = sorted(set(_patch_paths_from_list(patches) + _paths_from_edits(edits) + _normalize_paths(root, args.get("target_files"), allow_empty=True)))
    checkpoint = workspace_checkpoint(root, {"paths": paths, "label": f"resilient-{_safe_id(goal)}"}) if not dry_run else {"checkpoint_id": None, "paths": paths}
    steps: list[dict[str, Any]] = []
    try:
        if patches:
            patch_result = apply_patch_batch(root, {"patches": patches, "dry_run": dry_run, "rollback_on_failure": False, "label": goal})
            steps.append({"type": "patch_batch", "result": patch_result})
        if edits:
            if not isinstance(edits, list):
                raise base.WorkToolError("edits must be a list")
            for path, grouped in _group_edits(edits).items():
                result = base.multi_edit(root, {"path": path, "edits": grouped, "dry_run": dry_run})
                steps.append({"type": "multi_edit", "result": result})
        validation = _run_validation(root, args.get("validation", "changed_files"), paths) if not dry_run else {"status": "skipped", "dry_run": True}
        if validation.get("status") == "fail":
            raise base.WorkToolError("validation failed")
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
    dry_run = base.as_bool(args.get("dry_run"), True)
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        raise base.WorkToolError("old and new are required")
    results = []
    for path_rel in paths:
        if not path_rel.endswith(".dart"):
            continue
        path = base.resolve_existing(root, path_rel)
        before = base.read_text(path)
        after, count = _replace_identifier_outside_dart_strings(before, old, new)
        if count:
            if not dry_run:
                path.write_text(after, encoding="utf-8")
            results.append({"path": path_rel, "replacements": count, "diff": base.diff_text(before, after, path_rel, 10000)})
    return {"dry_run": dry_run, "old": old, "new": new, "changes": results}


TOOLS: dict[str, ToolFn] = {
    "workspace_checkpoint": workspace_checkpoint,
    "workspace_restore_session": workspace_restore_session,
    "workspace_changed_by_session": workspace_changed_by_session,
    "rollback_last_patch": rollback_last_patch,
    "patch_autosplit": patch_autosplit,
    "apply_patch_batch": apply_patch_batch,
    "resilient_apply_change": resilient_apply_change,
    "blocked_payload_diagnosis": blocked_payload_diagnosis,
    "dart_rename_symbol_safe": dart_rename_symbol_safe,
}


def can_handle(name: str) -> bool:
    return name in TOOLS


def tool_schema() -> list[dict[str, Any]]:
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    paths_schema = {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}}, "label": {"type": "string"}}, "additionalProperties": False}
    return [
        {"name": "workspace_checkpoint", "description": "Create a restorable checkpoint for explicit paths or current changed files.", "inputSchema": paths_schema},
        {"name": "workspace_restore_session", "description": "Restore files from a checkpoint. Use dry_run to preview.", "inputSchema": {"type": "object", "properties": {"checkpoint_id": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "dry_run": {"type": "boolean", "default": False}}, "additionalProperties": False}},
        {"name": "workspace_changed_by_session", "description": "Compare current git status with a checkpoint baseline.", "inputSchema": {"type": "object", "properties": {"checkpoint_id": {"type": "string"}}, "additionalProperties": False}},
        {"name": "rollback_last_patch", "description": "Restore the checkpoint recorded by the last resilient change or patch batch.", "inputSchema": {"type": "object", "properties": {"checkpoint_id": {"type": "string"}, "dry_run": {"type": "boolean", "default": False}}, "additionalProperties": False}},
        {"name": "patch_autosplit", "description": "Split a multi-file unified diff into per-file pieces for resilient application.", "inputSchema": {"type": "object", "properties": {"patch": {"type": "string"}, "include_patches": {"type": "boolean", "default": False}}, "required": ["patch"], "additionalProperties": False}},
        {"name": "apply_patch_batch", "description": "Apply multiple small patches with checkpoint and rollback-on-failure.", "inputSchema": {"type": "object", "properties": {"patch": {"type": "string"}, "patches": {"type": "array", "items": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"], "additionalProperties": False}}, "dry_run": {"type": "boolean", "default": False}, "rollback_on_failure": {"type": "boolean", "default": True}, "label": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 120}}, "additionalProperties": False}},
        {"name": "resilient_apply_change", "description": "Apply patches and exact edits with checkpoint, fallback-style small steps, validation and rollback.", "inputSchema": {"type": "object", "properties": {"goal": {"type": "string"}, "patch": {"type": "string"}, "patches": {"type": "array", "items": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"], "additionalProperties": False}}, "edits": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}, "replace_all": {"type": "boolean", "default": True}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False}}, "target_files": {"type": "array", "items": {"type": "string"}}, "validation": {"type": "string", "default": "changed_files"}, "dry_run": {"type": "boolean", "default": False}, "rollback_on_failure": {"type": "boolean", "default": True}}, "additionalProperties": False}},
        {"name": "blocked_payload_diagnosis", "description": "Diagnose blocked patch/payload failures and recommend safer MCP tools.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "payload": {"type": "string"}}, "additionalProperties": False}},
        {"name": "dart_rename_symbol_safe", "description": "Dart identifier rename across explicit files while skipping comments and string literals. Defaults to dry_run.", "inputSchema": {"type": "object", "properties": {"old": {"type": "string"}, "new": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "dry_run": {"type": "boolean", "default": True}}, "required": ["old", "new", "paths"], "additionalProperties": False}},
    ]


def call_tool(root: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise RuntimeError(name)
    return TOOLS[name](root.resolve(), args or {})
