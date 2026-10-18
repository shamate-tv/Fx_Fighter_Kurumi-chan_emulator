"""久留美模拟器 · 一键安装程序（单文件版）

双击 exe 即完成安装：
  1. 检查 Python 3.10+ 与 Node.js
  2. 把内置的项目文件复制到安装目录（默认 %USERPROFILE%\\KurumiSimulator）
  3. 创建 backend/.venv 并安装后端依赖
  4. 安装前端依赖
  5. 创建桌面快捷方式「久留美模拟器」
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
    if sys.platform == "win32":
        os.system("chcp 65001 >nul")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def pause(message: str = "\n按 Enter 退出...") -> None:
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
    for candidate in (["py", "-3"], ["python"], ["python3"]):
        output = capture(candidate + ["--version"])
        match = re.search(r"Python (\d+)\.(\d+)", output)
        if match and (int(match.group(1)), int(match.group(2))) >= (3, 10):
            return candidate
    return None


def find_npm() -> str | None:
    return shutil.which("npm") or shutil.which("npm.cmd")


def project_source() -> Path | None:
    """内置项目文件所在目录（PyInstaller 解压到 _MEIPASS/project）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent
    candidate = base / "project"
    if (candidate / "backend" / "requirements.txt").exists():
        return candidate
    return None


def ask_install_dir() -> Path:
    default = Path.home() / "KurumiSimulator"
    print(f"  安装目录（直接回车使用默认：{default}）")
    print("  > ", end="", flush=True)
    try:
        answer = re.sub(r"\ufeff", "", input().strip().strip('"'))
    except EOFError:
        answer = ""
    return Path(answer) if answer else default


def create_shortcut(target_bat: Path, workdir: Path) -> None:
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        return
    link = desktop / "久留美模拟器.lnk"
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{link}'); "
        f"$sc.TargetPath = '{target_bat}'; "
        f"$sc.WorkingDirectory = '{workdir}'; "
        "$sc.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            timeout=30,
        )
        print(f"  已创建桌面快捷方式：{link}")
    except Exception:
        pass


def main() -> int:
    print()
    print("  " + TITLE)
    print("  " + "=" * (len(TITLE) + 12))
    print()

    source = project_source()
    if source is None:
        print("  [错误] 安装包缺少内置项目文件，请重新下载完整的安装程序。")
        pause()
        return 1

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
    print()

    target = ask_install_dir()
    if target.exists() and (target / "backend" / "requirements.txt").exists():
        print()
        print(f"  检测到已有安装：{target}")
        print("  继续将更新代码并重装依赖（保留虚拟环境与 node_modules）。")
    else:
        print()
        print(f"  [1/4] 复制项目文件到 {target}")
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            destination = target / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    destination,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".venv", "node_modules", "__pycache__", "*.pyc"),
                )
            else:
                shutil.copy2(item, destination)

    # -------- 后端 --------
    venv_dir = target / "backend" / ".venv"
    venv_python = venv_dir / "Scripts" / "python.exe"
    if not venv_python.exists():
        print()
        print("  [2/4] 创建后端虚拟环境（backend/.venv）…")
        run(python + ["-m", "venv", str(venv_dir)])
    else:
        print()
        print("  [2/4] 后端虚拟环境已存在，跳过")

    print()
    print("  [3/4] 安装后端依赖（首次约 1-3 分钟）…")
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    run([str(venv_python), "-m", "pip", "install", "-r", str(target / "backend" / "requirements.txt")])

    # -------- 前端 --------
    print()
    if (target / "frontend" / "node_modules").exists():
        print("  [4/4] 前端依赖已存在，跳过")
    else:
        print("  [4/4] 安装前端依赖（首次约 2-5 分钟）…")
        cache_dir = target / "frontend" / ".npm-cache"
        run(
            [find_npm(), "install", "--cache", str(cache_dir), "--logs-dir", str(cache_dir / "logs")],
            cwd=target / "frontend",
        )

    # -------- 快捷方式 --------
    print()
    create_shortcut(target / "start.bat", target)

    print()
    print("  安装完成！")
    print(f"  安装目录：{target}")
    print("  双击桌面「久留美模拟器」或安装目录里的 start.bat 启动。")
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
