"""久留美模拟器 · 一键安装程序

双击 exe / 运行 install.bat 即可：
  1. 检查 Python 3.10+ 与 Node.js
  2. 创建 backend/.venv 并安装后端依赖
  3. 安装 frontend 依赖
安装完成后双击 start.bat 启动。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TITLE = "FX 战士久留美 · 模拟器 安装程序"


def _setup_console() -> None:
    """让 Windows 控制台按 UTF-8 输出中文（与 install.bat 的 chcp 65001 一致）。"""
    if sys.platform == "win32":
        os.system("chcp 65001 >nul")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def find_root() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    current = base
    for _ in range(4):
        if (current / "backend" / "requirements.txt").exists():
            return current
        current = current.parent
    return base


ROOT = find_root()


def pause(message: str = "按 Enter 退出...") -> None:
    try:
        input(message)
    except EOFError:
        pass


def run(args: list[str], cwd: Path | None = None) -> None:
    print("  > " + " ".join(args))
    result = subprocess.run(args, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败（退出码 {result.returncode}）：{' '.join(args)}")


def capture(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return (result.stdout or "") + (result.stderr or "")
    except Exception:
        return ""


def find_python() -> list[str] | None:
    candidates = [["py", "-3"], ["python"], ["python3"]]
    for candidate in candidates:
        output = capture(candidate + ["--version"])
        match = re.search(r"Python (\d+)\.(\d+)", output)
        if match and (int(match.group(1)), int(match.group(2))) >= (3, 10):
            return candidate
    return None


def find_npm() -> str | None:
    return shutil.which("npm") or shutil.which("npm.cmd")


def main() -> int:
    print()
    print("  " + TITLE)
    print("  " + "=" * (len(TITLE) + 12))
    print()
    print(f"  项目目录：{ROOT}")
    print()

    python = find_python()
    if python is None:
        print("  [错误] 未找到 Python 3.10+")
        print("  请先安装：https://www.python.org/downloads/")
        print("  （安装时勾选 Add Python to PATH）")
        pause()
        return 1
    print(f"  [OK] Python：{' '.join(python)}")

    if not find_npm():
        print("  [错误] 未找到 Node.js / npm")
        print("  请先安装 Node.js 18+：https://nodejs.org/")
        pause()
        return 1
    print("  [OK] Node.js：已安装")

    # -------- 后端 --------
    venv_dir = ROOT / "backend" / ".venv"
    venv_python = venv_dir / "Scripts" / "python.exe"
    if not venv_python.exists():
        print()
        print("  [1/3] 创建后端虚拟环境（backend/.venv）…")
        run(python + ["-m", "venv", str(venv_dir)])
    else:
        print()
        print("  [1/3] 后端虚拟环境已存在，跳过")

    print()
    print("  [2/3] 安装后端依赖（首次约 1-3 分钟）…")
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    run([str(venv_python), "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt")])

    # -------- 前端 --------
    print()
    if (ROOT / "frontend" / "node_modules").exists():
        print("  [3/3] 前端依赖已存在，跳过")
    else:
        print("  [3/3] 安装前端依赖（首次约 2-5 分钟）…")
        run([find_npm(), "install"], cwd=ROOT / "frontend")

    print()
    print("  安装完成！双击 start.bat 启动模拟器。")
    print()
    pause()
    return 0


if __name__ == "__main__":
    _setup_console()
    try:
        sys.exit(main())
    except Exception as exc:
        print()
        print(f"  [失败] {exc}")
        pause()
        sys.exit(1)
