#!/usr/bin/env python3
"""Package only portable program assets, never config, credentials or run data."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json
ROOT = Path(__file__).resolve().parent
TOP = ['start.sh', 'server.py', 'visualization.py', 'icon_preview.py', 'render_model.py', 'setup_renderer.py', 'requirements-renderer.txt', 'config.example.json', 'README.md', 'README.zh-CN.md', 'THIRD_PARTY.md', 'package_demo.py', '.gitignore', '.gitattributes']
FOLDERS = ['static', 'bundle/.claude', 'tests', 'docs', 'examples', 'scripts', '.github/workflows']
SUFFIXES = {'.py', '.md', '.json', '.html', '.css', '.js', '.cjs', '.svg', '.txt', '.yml'}


def source_files(root=ROOT):
    """Allow only distributable source assets; never follow symlinks."""
    files = []
    for name in TOP:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing or unsafe required source: {name}')
        files.append(path)
    for folder in FOLDERS:
        base = root / folder
        if not base.is_dir():
            raise ValueError(f'Missing source directory: {folder}')
        for path in base.rglob('*'):
            relative = path.relative_to(root)
            if any((root / Path(*relative.parts[:i])).is_symlink()
                   for i in range(1, len(relative.parts) + 1)):
                continue
            if not path.is_file() or '__pycache__' in relative.parts:
                continue
            if path.suffix not in SUFFIXES or path.name.startswith('.env'):
                continue
            if path.name in {'config.json', 'settings.json', 'settings.local.json'}:
                continue
            files.append(path)
    return sorted(set(files))

def main():
    files = source_files()
    out = ROOT / 'dist/text2hydraulic-demo-linux.zip'
    out.parent.mkdir(exist_ok=True)
    manifest = {}
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(ROOT).as_posix()
            archive.write(path, f'text2hydraulic/{relative}')
            manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        archive.writestr('text2hydraulic/SHA256SUMS.json', json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f'{out}\n{len(files)} files, {out.stat().st_size / 1024 / 1024:.2f} MiB')

if __name__ == '__main__':
    main()
