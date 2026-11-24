"""Run existing collectors with a consistent Windows curl trust configuration."""
import os
from pathlib import Path
import runpy
import subprocess
import sys

original_run = subprocess.run


def run(args, *positional, **options):
    if os.name == 'nt' and isinstance(args, (list, tuple)) and args and Path(str(args[0])).name.lower() in {'curl', 'curl.exe'}:
        if '--ssl-revoke-best-effort' not in args:
            args = [args[0], '--ssl-revoke-best-effort', *args[1:]]
    return original_run(args, *positional, **options)


if __name__ == '__main__':
    subprocess.run = run
    script = Path(__file__).resolve().parent / sys.argv[1]
    if script.parent != Path(__file__).resolve().parent or not script.is_file():
        raise SystemExit('Collector must be a script in this repository')
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name='__main__')
