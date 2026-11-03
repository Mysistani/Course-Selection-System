"""Install declared UI dependencies only when the requirements hash changes."""
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
requirements = ROOT/'requirements-ui.txt'
digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
stamp = Path(sys.prefix)/'.scheduler-ui-dependencies'
if not stamp.exists() or stamp.read_text()!=digest:
    subprocess.run([sys.executable,'-m','pip','install','-r',str(requirements)],check=True)
    stamp.write_text(digest)
