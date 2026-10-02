#!/usr/bin/env python3
"""Diagnose renderer dependencies; install only with an explicit --install."""
import argparse
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
CHECK="""
import importlib, json
missing={}
for name in ('OMPython','svgwrite','cairosvg'):
    try: importlib.import_module(name)
    except Exception as error: missing[name]=str(error)
print(json.dumps(missing))
"""


def probe(python):
    """Keep the actual import error so missing modules and Cairo are distinguishable."""
    try:
        result=subprocess.run([str(python),'-c',CHECK],capture_output=True,text=True,timeout=15)
        lines=(result.stderr or result.stdout).strip().splitlines()
        if result.returncode==0:
            missing=json.loads(result.stdout)
            return {'available':not missing,'missing':missing,'error':'; '.join(f'{name}: {error}' for name,error in missing.items())}
        return {'available':False,'error':'\n'.join(lines[-4:])[:1200] if result.returncode else ''}
    except (OSError,subprocess.TimeoutExpired,ValueError) as error:
        return {'available':False,'error':str(error)}


def usable(python):
    return probe(python)['available']


def configured_python():
    try:
        value=json.loads((ROOT/'config.json').read_text()).get('renderer_python','')
        if value:
            p=Path(value).expanduser()
            return ROOT/p if not p.is_absolute() else p
    except (OSError,ValueError,AttributeError):pass
    return None


def installation_commands(root=ROOT):
    # These strings are displayed only, never passed to a process.
    return '\n'.join([
        'cd -- '+shlex.quote(str(root)),
        'python3 setup_renderer.py --install',
    ])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true',help='Only check dependencies (default)')
    mode.add_argument('--install',action='store_true',help='Create a local renderer venv and install its dependencies')
    args=parser.parse_args()
    if args.install:
        python=ROOT/'.venv-renderer/bin/python'
        try:
            # Never clear an existing environment or install into system Python.
            if not python.is_file():
                subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv-renderer')],check=True)
            subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements-renderer.txt')],check=True)
        except (OSError,subprocess.CalledProcessError) as error:
            print('Renderer dependency installation failed: '+str(error),file=sys.stderr)
            print('If venv is unavailable on Ubuntu/Debian, install python3-venv first.',file=sys.stderr)
            return 1
    import icon_preview
    try:cfg=json.loads((ROOT/'config.json').read_text())
    except (OSError,ValueError):cfg={}
    for key, variable in [('omc_command', 'T2H_OMC'),
                          ('openhydraulics_package', 'T2H_OPENHYDRAULICS')]:
        if os.environ.get(variable):
            cfg[key] = os.environ[variable]
    result=icon_preview.environment(cfg,force=True)
    print(result['message'])
    if result.get('commands'):
        print('\nRun these commands yourself (nothing has been installed):\n')
        print(result['commands'])
        print('\nIf venv or Cairo is unavailable, install the corresponding system packages using your distribution package manager (Ubuntu/Debian: python3-venv and libcairo2).')
        print('After installation, select Auto-detect, then Render.')
    return 0 if result['available'] else 1


if __name__=='__main__':sys.exit(main())
