"""Run isolated regression checks. No cloud account or real model required."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
for path in sorted(root.glob('test_*.py')):
    print(f'\n>>> {path.name}', flush=True)
    subprocess.run([sys.executable, str(path)], check=True, cwd=root.parent)
print('\nAll isolated regression checks passed.')
