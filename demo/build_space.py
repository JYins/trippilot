from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "space_bundle"
EXCLUDED_NAMES = {
    ".agents",
    ".codex",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "qdrant_data",
    OUTPUT_DIRECTORY.name,
}
TOP_LEVEL_EXCLUDED_NAMES = {"Dockerfile", ".dockerignore", "vercel.json"}


def make_space_readme(gradio_version: str) -> str:
    return f"""---
title: TripPilot Demo
sdk: gradio
sdk_version: {gradio_version}
app_file: app.py
---

# TripPilot Demo

TripPilot 是一个面向北京出行场景的座舱 Agent 原型。这个 Gradio demo 使用确定性
stub、录制的地图与天气数据，以及纯软件模拟的座舱状态，不连接真实车辆。
"""


def read_gradio_version(requirements_path: Path) -> str:
    if not requirements_path.is_file():
        raise FileNotFoundError(f"缺少 Gradio 依赖文件：{requirements_path}")

    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        requirement = line.strip()
        if not requirement.startswith("gradio"):
            continue
        match = re.fullmatch(r"gradio==(\d+\.\d+\.\d+)", requirement)
        if match is None:
            raise ValueError(
                "demo/requirements.txt 中的 Gradio 依赖格式错误，"
                "必须写成 gradio==X.Y.Z"
            )
        return match.group(1)

    raise ValueError("demo/requirements.txt 缺少 gradio==X.Y.Z 依赖")


def ignore_deployment_files(directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in EXCLUDED_NAMES:
            ignored.add(name)
        elif Path(directory).resolve() == REPOSITORY_ROOT and name in TOP_LEVEL_EXCLUDED_NAMES:
            ignored.add(name)
        elif name.startswith(".env"):
            ignored.add(name)
        elif name.endswith(".db"):
            ignored.add(name)
        elif name.startswith(":memory:"):
            ignored.add(name)
        elif name.endswith(".pyc"):
            ignored.add(name)
    return ignored


def build_space() -> Path:
    app_source = REPOSITORY_ROOT / "demo" / "gradio_app.py"
    requirements_source = REPOSITORY_ROOT / "requirements.txt"
    demo_requirements = REPOSITORY_ROOT / "demo" / "requirements.txt"
    for required_file in (app_source, requirements_source, demo_requirements):
        if not required_file.is_file():
            raise FileNotFoundError(f"缺少打包所需文件：{required_file}")
    gradio_version = read_gradio_version(demo_requirements)

    if OUTPUT_DIRECTORY.exists():
        if not OUTPUT_DIRECTORY.is_dir():
            raise RuntimeError(f"部署路径存在但不是目录：{OUTPUT_DIRECTORY}")
        shutil.rmtree(OUTPUT_DIRECTORY)

    shutil.copytree(
        REPOSITORY_ROOT,
        OUTPUT_DIRECTORY,
        ignore=ignore_deployment_files,
    )
    shutil.copy2(app_source, OUTPUT_DIRECTORY / "app.py")
    shutil.copy2(requirements_source, OUTPUT_DIRECTORY / "requirements.txt")
    space_readme = make_space_readme(gradio_version)
    (OUTPUT_DIRECTORY / "README.md").write_text(space_readme, encoding="utf-8")
    return OUTPUT_DIRECTORY


def main() -> int:
    try:
        output_directory = build_space()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"打包失败：{exc}", file=sys.stderr)
        return 1
    print(f"Space 部署包已生成：{output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
