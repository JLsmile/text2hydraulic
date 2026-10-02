#!/usr/bin/env python3
"""Check distributable assets and local documentation without external tools."""
import ast
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from package_demo import source_files


def main():
    errors = []
    try:
        files = source_files(ROOT)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    skill = ROOT / 'bundle/.claude/skills/text2hydraulic'
    for role in ['design', 'review', 'simulation']:
        if not (ROOT / f'bundle/.claude/agents/hydraulic-{role}.md').is_file():
            errors.append(f'Missing agent: {role}')
    if len(list((skill / 'scripts').glob('*.py'))) != 9:
        errors.append('Expected nine engineering scripts')
    for path in files:
        try:
            content = path.read_text(encoding='utf-8-sig')
            if path.suffix == '.py':
                ast.parse(content, filename=str(path))
            elif path.suffix == '.json':
                json.loads(content)
            elif path.suffix == '.md':
                # Ignore code examples; validate relative Markdown file links.
                content = re.sub(r'```.*?```', '', content, flags=re.S)
                for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
                    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith('#'):
                        continue
                    target = unquote(target.split('#', 1)[0].strip('<>'))
                    if target and not (path.parent / target).exists():
                        errors.append(f'{path.relative_to(ROOT)}: broken link {target}')
        except (ValueError, SyntaxError, UnicodeError) as exc:
            errors.append(f'{path.relative_to(ROOT)}: {exc}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'OK: {len(files)} source files; Python syntax, JSON, local Markdown file links, and workflow assets.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
