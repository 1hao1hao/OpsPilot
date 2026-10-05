"""Read-only migration audit; never emits environment variable values."""

import json
import os
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
result = {"root": str(root), "scripts": [], "symlinks": [], "env_server_paths": []}
for path in (root / "scripts").rglob("*.sh"):
    data = path.read_bytes()
    result["scripts"].append({"path": str(path.relative_to(root)), "crlf": data.count(b"\r\n"), "lf": data.count(b"\n"), "executable": os.access(path, os.X_OK)})
for directory, dirs, files in os.walk(root, followlinks=False):
    dirs[:] = [name for name in dirs if name not in {".git", ".venv", ".external", "node_modules"} and not name.startswith(".venv.linux-backup-")]
    for name in dirs + files:
        path = Path(directory) / name
        if path.is_symlink():
            result["symlinks"].append({"path": str(path.relative_to(root)), "target": str(path.readlink()), "valid": path.exists()})
env = root / ".env"
result["env_exists"] = env.exists()
if env.exists():
    for number, line in enumerate(env.read_text(encoding="utf-8").splitlines(), 1):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if re.search(r"/(?:home|root|opt|srv|mnt|var|tmp|share)/", value):
            result["env_server_paths"].append({"line": number, "key": key.strip()})
git = Path("C:/Program Files/Git/cmd/git.exe")
if git.exists():
    tracked = subprocess.run([str(git), "ls-files", "--stage"], cwd=root, capture_output=True, text=True, check=True)
    result["git_symlinks"] = [line for line in tracked.stdout.splitlines() if line.startswith("120000")]
print(json.dumps(result, ensure_ascii=False, indent=2))
