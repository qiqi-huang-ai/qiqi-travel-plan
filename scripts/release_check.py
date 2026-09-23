#!/usr/bin/env python3
"""Run deterministic, network-free checks before packaging this Skill."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "SKILL.md",
    "README.md",
    "LICENSE",
    "agents/openai.yaml",
    "assets/schemas/trip.schema.json",
    "docs/USAGE.md",
    "docs/images/travel-dossier-hero.png",
    "docs/examples/trip.json",
    "docs/examples/outputs/2026-10-10-example-2d_v1.md",
    "docs/examples/outputs/2026-10-10-example-2d_v1.html",
)
IGNORED_LINK_PREFIXES = ("http://", "https://", "mailto:")
FORBIDDEN_PARTS = {"__pycache__", ".DS_Store"}


def fail(message: str) -> None:
    raise ValueError(message)


def check_required() -> None:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        fail("缺少发布必需文件：" + "、".join(missing))


def check_hygiene() -> None:
    bad = []
    for path in ROOT.rglob("*"):
        if any(part in FORBIDDEN_PARTS for part in path.parts) or path.suffix in {".pyc", ".pyo"}:
            bad.append(path.relative_to(ROOT).as_posix())
    if bad:
        fail("发布包包含生成文件：" + "、".join(sorted(bad)))


def check_markdown_links() -> None:
    missing = []
    for path in ROOT.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = raw.split("#", 1)[0]
            if not target or target.startswith(IGNORED_LINK_PREFIXES):
                continue
            if not (path.parent / target).resolve().exists():
                missing.append(f"{path.relative_to(ROOT)} -> {raw}")
    if missing:
        fail("Markdown 链接失效：" + "；".join(missing))


def check_source() -> None:
    for path in ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path), feature_version=(3, 10))
        compile(source, str(path), "exec")
    references = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".yaml", ".yml"}:
            continue
        if ("bys" + "-travel-plan") in path.read_text(encoding="utf-8"):
            references.append(path.relative_to(ROOT).as_posix())
    if references:
        fail("公开包不应保留参考 Skill 路径：" + "、".join(references))


def run_tests() -> None:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_*.py"],
        cwd=ROOT,
        env=env,
    )
    if result.returncode:
        fail("行为测试失败")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        check_required()
        check_hygiene()
        check_markdown_links()
        check_source()
        run_tests()
    except (OSError, UnicodeError, ValueError) as error:
        print(f"release check failed: {error}", file=sys.stderr)
        return 1
    print("release check passed: files, hygiene, links, syntax, tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
