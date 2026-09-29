"""Starts Quota Monitor in the background.

Run it once (double-click on Windows, or from a terminal on Linux). It starts the
tray application as a detached background process using the project's virtual
environment, then exits, so no terminal or console window has to stay open.
Output from the background process goes to data/tray.log.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHILD_FLAG = "QUOTA_MONITOR_BACKGROUND"


def venv_python() -> str:
    if sys.platform == "win32":
        candidate = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        fallback = Path(sys.executable).with_name("pythonw.exe")
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
        fallback = Path(sys.executable)
    return str(candidate if candidate.is_file() else fallback)


if os.environ.get(CHILD_FLAG) != "1":
    env = dict(os.environ, **{CHILD_FLAG: "1"})
    options = {"env": env, "cwd": str(ROOT), "stdin": subprocess.DEVNULL,
               "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True  # Survives the terminal being closed.
    subprocess.Popen([venv_python(), str(Path(__file__).resolve())], **options)
    sys.exit(0)

sys.path.insert(0, str(ROOT))
(ROOT / "data").mkdir(exist_ok=True)
sys.stdout = sys.stderr = open(ROOT / "data" / "tray.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115

from quota_monitor.__main__ import main  # noqa: E402 (path must be set first)

sys.exit(main(["--config", str(ROOT / "config.toml"), "tray"]))
