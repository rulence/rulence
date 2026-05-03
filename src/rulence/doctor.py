from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .policies import default_policy_dir
from .sessions import default_session_dir


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def run_doctor(project_root: str | Path | None = None) -> list[DoctorCheck]:
    root = Path(project_root).expanduser() if project_root else Path.cwd()
    checks = [
        DoctorCheck("python", "pass", sys.version.split()[0]),
        _command_check("node", ["node", "--version"]),
        _command_check("npm", ["npm", "--version"]),
        DoctorCheck("policy_dir", "pass" if default_policy_dir().exists() else "warn", str(default_policy_dir())),
        DoctorCheck("session_dir", "pass" if default_session_dir().exists() else "warn", str(default_session_dir())),
        DoctorCheck("npm_package", "pass" if (root / "package.json").exists() else "warn", str(root / "package.json")),
        DoctorCheck("node_modules", "pass" if (root / "node_modules").exists() else "warn", str(root / "node_modules")),
    ]
    return checks


def _command_check(name: str, command: list[str]) -> DoctorCheck:
    if not shutil.which(command[0]):
        return DoctorCheck(name, "warn", f"{command[0]} not found")
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=5)
    except Exception as exc:
        return DoctorCheck(name, "warn", str(exc))
    return DoctorCheck(name, "pass", result.stdout.strip())
