"""Small safety primitives shared by Travel Plan Pro commands.

This module deliberately owns only stable, cross-command rules: deterministic
serialization, path containment, atomic writes, snapshot immutability, and the
budget ceiling. Domain validation remains in ``validate_trip``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path


_CREDENTIAL_NAME = re.compile(r"(?i)(?:^|_)(?:api_?key|access_?token|token|secret|password|passwd)$")
_CREDENTIAL_VALUE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_.-]{12,}"
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9_.=-]{16,}", re.I)
_ID_NUMBER = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def configure_console() -> None:
    """Prefer UTF-8 diagnostics when the current Python stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def canonical_hash(data: object) -> str:
    """Return the digest for JSON data with an unambiguous canonical encoding."""
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inside(base: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> str:
    """Resolve *candidate* and reject it unless it remains inside *base*."""
    root = Path(base).resolve()
    target = Path(candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("路径越出行程目录") from error
    return str(target)


def atomic_text(path: str | os.PathLike[str], content: str) -> None:
    """Replace a text artifact atomically, leaving no half-written final file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".travel-plan-", suffix=".writing", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: str | os.PathLike[str], data: object) -> None:
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def artifact_path(base: str, path: str, source: str, kind: str) -> str:
    """Accept only a correctly named report path that cannot overwrite source/history."""
    target = inside(base, path)
    root = Path(base).resolve()
    relative = Path(target).resolve().relative_to(root).as_posix().lower()
    source_path = Path(source).resolve()
    if Path(target).resolve() == source_path or relative == "trip.json" or relative == "versions" or relative.startswith("versions/"):
        raise ValueError("产物路径不能覆盖行程数据或历史快照")
    prefix = {"audit": "audit.", "manifest": "manifest."}.get(kind)
    if prefix is None or not Path(target).name.lower().startswith(prefix) or Path(target).suffix.lower() != ".json":
        raise ValueError(f"{kind} 文件名须以 {prefix or kind + '.'} 开头、以 .json 结尾")
    return target


def secret_hits(data: object) -> list[str]:
    """Return field paths that look like secrets or identity credentials, never values."""
    hits: list[str] = []

    def visit(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}"
                if _CREDENTIAL_NAME.search(str(key)) and value not in (None, "", False):
                    hits.append(child + ".[凭据字段]")
                visit(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{path}[{index}]")
        elif isinstance(node, str) and (_CREDENTIAL_VALUE.search(node) or _BEARER.search(node) or _ID_NUMBER.search(node)):
            hits.append(path)

    visit(data, "$")
    return hits


def preflight(trip: dict, semantic: bool = False) -> None:
    """Validate before any write. Error messages intentionally omit user values."""
    if secret_hits(trip):
        raise ValueError("数据含疑似凭据，拒绝写入")
    import validate_trip as validator

    schema = json.loads(Path(validator.SCHEMA_PATH).read_text(encoding="utf-8"))
    checker = validator.MiniSchema(schema)
    if not checker.check(trip, schema, "$"):
        raise ValueError(f"数据契约校验失败（{len(checker.errors)} 处），请运行 validate_trip 查看字段位置")
    try:
        canonical_hash(trip)
    except (TypeError, ValueError) as error:
        raise ValueError("数据包含非有限数值") from error
    if semantic:
        report = validator.Audit(trip)
        validator.semantic_checks(trip, report)
        if report.summary()["blocking"]:
            raise ValueError(f"行程有 {report.summary()['blocking']} 个阻断问题，请先运行 validate_trip 修复")


def checkpoint(base: str, trip: dict) -> str:
    """Create an immutable versioned snapshot, or verify an identical existing one."""
    preflight(trip)
    versions = Path(inside(base, Path(base) / "versions"))
    version = trip["plan_version"]
    snapshot = Path(inside(base, versions / f"trip.v{version}.json"))
    if snapshot.exists():
        existing = json.loads(snapshot.read_text(encoding="utf-8"))
        if canonical_hash(existing) != canonical_hash(trip):
            raise ValueError("同版本快照已有不同内容，请先 save 升版本；不能覆盖历史快照")
        return str(snapshot)
    versions.mkdir(parents=True, exist_ok=True)
    atomic_json(snapshot, trip)
    return str(snapshot)


def budget_limit(trip: dict) -> float | int | None:
    """Compute a hard ceiling only when its currency and participant basis are explicit."""
    budget = trip["itinerary"]["budget"]
    request = trip["request"]["budget"]
    if budget.get("hard_limit") is not None:
        return budget["hard_limit"]
    if request.get("currency") != budget.get("currency"):
        return None
    maximum = request.get("amount_max")
    if maximum is None:
        return None
    if request.get("mode") == "total":
        return maximum
    people = request.get("budget_persons")
    if request.get("mode") == "per_person" and isinstance(people, int) and people > 0:
        return people * maximum
    return None


def budget_verdict(lo: float, hi: float, paid: float, unknown: bool, limit: float | None) -> str:
    if limit is None:
        return "预算口径或上限待确认"
    if paid + lo > limit:
        return "不成立"
    if paid + hi > limit:
        return "下界成立、上界超出"
    return "已知费用未超限，含未知费用时尚不能确认" if unknown else "成立"
