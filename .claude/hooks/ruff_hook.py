"""PostToolUse hook: ruff-format an edited .py file, then report lint errors (exit 2) back to Claude."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUFF = ROOT / ".venv" / "Scripts" / "ruff.exe"

payload = json.load(sys.stdin)
file_path = (payload.get("tool_input") or {}).get("file_path") or ""
path = Path(file_path)
if path.suffix != ".py" or not path.is_file() or not RUFF.is_file():
    sys.exit(0)

subprocess.run([str(RUFF), "format", "--quiet", str(path)], cwd=ROOT)
check = subprocess.run(
    [str(RUFF), "check", "--output-format", "concise", str(path)], cwd=ROOT, capture_output=True, text=True
)
if check.returncode != 0:
    sys.stderr.write(check.stdout + check.stderr)
    sys.exit(2)
