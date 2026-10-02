"""Read-only previews of generated Modelica topology and numeric CSV signals.

The model preview is a connection diagram, not an OMEdit icon renderer or a
Modelica compiler. Unsupported declarations are reported instead of invented.
"""
import csv
import json
import math
import re


def candidates(path, suffix, requested, resolve, list_artifacts, memory_key):
    files = [f['name'] for f in list_artifacts(path) if f['name'].lower().endswith(suffix)]
    if requested:
        if requested not in files:
            raise ValueError('Select an available run artifact')
        return files, [resolve(path, requested)]
    try:
        memory = json.loads((path / 'memory.json').read_text())
        preferred = (memory.get('files') or {}).get(memory_key)
        if preferred:
            actual = resolve(path, preferred).relative_to(path).as_posix()
            if actual in files:
                files.remove(actual)
                files.insert(0, actual)
    except (ValueError, OSError, AttributeError):
        pass
    return files, [resolve(path, name) for name in files]


def clean_source(source):
    # Preserve strings while removing comments; comment markers can occur in text.
    return re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else ' ', source)


def statements(source):
    start = depth = 0
    quoted = escaped = False
    for i, c in enumerate(source):
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c in '({[':
            depth += 1
        elif c in ')}]':
            depth -= 1
        elif c == ';' and depth == 0:
            yield source[start:i].strip()
            start = i + 1


NUM = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
PAIR = rf'\{{\s*({NUM})\s*,\s*({NUM})\s*\}}'
REF = r"[A-Za-z_]\w*(?:\[[\w, :+*-]+\])?(?:\.[A-Za-z_]\w*(?:\[[\w, :+*-]+\])?)+"


def parse_model(source):
    source = clean_source(source)
    # Blank strings for structural keyword detection (including descriptions).
    structural = re.sub(r'"(?:\\.|[^"\\])*"', lambda m: ' ' * len(m[0]), source)
    model = re.search(r'\b(?:model|block)\s+(\w+)\b', structural)
    if not model:
        return {'name': '', 'nodes': [], 'edges': [], 'warnings': ['No top-level model or block found.']}
    tail = structural[model.end():]
    boundary = re.search(r'\b(?:initial\s+)?(?:equation|algorithm)\b', tail)
    stop = model.end() + boundary.start() if boundary else len(source)
    declarations = source[model.end():stop]
    declarations = re.sub(r'^\s*"(?:\\.|[^"\\])*"', '', declarations)
    nodes = []
    ignored = []
    skip = {'parameter', 'constant', 'Real', 'Integer', 'Boolean', 'String', 'annotation',
            'extends', 'import', 'end', 'type'}
    for statement in statements(declarations):
        statement = re.sub(r'^\s*(?:public|protected)\b', '', statement).strip()
        match = re.match(r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+([A-Za-z_]\w*)\s*(.*)', statement, re.S)
        if not match:
            continue
        kind, name, rest = match.groups()
        if kind in skip:
            continue
        if rest.lstrip().startswith(('[', ',')) or kind in {'replaceable', 'redeclare', 'inner', 'outer', 'model', 'block', 'package'}:
            ignored.append(name)
            continue
        placement = None
        transform = re.search(r'\btransformation\s*\(([^)]*)\)', rest, re.S)
        if transform:
            origin = re.search(r'\borigin\s*=\s*' + PAIR, transform[1])
            extent = re.search(r'\bextent\s*=\s*\{\s*' + PAIR + r'\s*,\s*' + PAIR, transform[1])
            if origin or extent:
                ox, oy = map(float, origin.groups()) if origin else (0, 0)
                if extent:
                    x1, y1, x2, y2 = map(float, extent.groups())
                    ox += (x1 + x2) / 2
                    oy += (y1 + y2) / 2
                if math.isfinite(ox) and math.isfinite(oy):
                    placement = [ox, -oy]
        params = rest.split('annotation', 1)[0].strip()
        nodes.append({'name': name, 'type': kind, 'parameters': params[:3000], 'position': placement})
    names = {n['name'] for n in nodes}
    edges = []
    # Ignore connect(...) in descriptions. Endpoint strings contain no quotes.
    for match in re.finditer(r'\bconnect\s*\(\s*(' + REF + r')\s*,\s*(' + REF + r')\s*\)', structural[stop:]):
        a, b = match.groups()
        left, right = a.split('.')[0], b.split('.')[0]
        if left not in names or right not in names:
            ignored.append(f'{a} → {b}')
            continue
        edges.append({'from': left, 'to': right, 'source': a, 'target': b,
                      'kind': 'hydraulic' if '.port' in a and '.port' in b else
                              'mechanical' if '.flange' in a else 'signal'})
    warnings = []
    if ignored:
        warnings.append(f'{len(ignored)} declarations or connections cannot be represented by this preview. Open the source for full details.')
    if re.search(r'\b(?:for|if)\b[^;]*\bconnect\s*\(', structural[stop:], re.S):
        warnings.append('Conditional or loop connections are shown as source topology, without evaluating equations.')
    if not nodes:
        warnings.append('No supported component declarations found; download the Modelica source to inspect it.')
    return {'name': model[1], 'nodes': nodes, 'edges': edges, 'warnings': warnings,
            'layout': 'placement' if nodes and all(n['position'] for n in nodes) else 'automatic'}


def model_data(path, requested, resolve, list_artifacts):
    files, paths = candidates(path, '.mo', requested, resolve, list_artifacts, 'mo_file')
    if not paths:
        return {'files': files, 'nodes': [], 'edges': [], 'warnings': []}
    p = paths[0]
    if p.stat().st_size > 4_000_000:
        return {'file': p.relative_to(path).as_posix(), 'files': files, 'nodes': [], 'edges': [],
                'warnings': ['Model exceeds the 4 MB preview limit. Download the source to inspect it.']}
    return {**parse_model(p.read_text(encoding='utf-8-sig', errors='replace')),
            'file': p.relative_to(path).as_posix(), 'files': files}


def signal_data(path, requested, variable, resolve, list_artifacts):
    files, paths = candidates(path, '.csv', requested, resolve, list_artifacts, 'csv_file')
    empty = {'files': files, 'variables': [], 'points': [], 'samples': 0}
    for p in paths:
        with p.open(encoding='utf-8-sig', errors='replace', newline='') as handle:
            reader = csv.reader(handle)
            names = [n.strip() for n in next(reader, [])]
            if 'time' not in names:
                continue
            time_index = names.index('time')
            # OMC exports may repeat time and append nrows=... to the header.
            indices = {n: i for i, n in enumerate(names) if n and n != 'time' and not n.startswith('nrows=')}
            numeric = set()
            pending = dict(indices)
            if not indices:
                continue
            selected = variable if variable in indices else 'worktable.v' if 'worktable.v' in indices else next(iter(indices))
            index = indices[selected]
            points, total, stride, last = [], 0, 1, None
            minimum = maximum = None
            for row in reader:
                # Discover sparse signals anywhere in the file, not only in its first rows.
                for name, col in list(pending.items()):
                    try:
                        if math.isfinite(float(row[col])):
                            numeric.add(name)
                            del pending[name]
                    except (IndexError, ValueError):
                        pass
                try:
                    t, value = float(row[time_index]), float(row[index])
                except (ValueError, IndexError):
                    continue
                if not (math.isfinite(t) and math.isfinite(value)):
                    continue
                minimum = value if minimum is None else min(minimum, value)
                maximum = value if maximum is None else max(maximum, value)
                last = [t, value]
                if total % stride == 0:
                    points.append(last)
                total += 1
                if len(points) > 4000:
                    points = points[::2]
                    stride *= 2
            variables = [n for n in indices if n in numeric]
            if not variables:
                continue
            if selected not in numeric:
                return signal_data(path, p.relative_to(path).as_posix(), variables[0], resolve, list_artifacts)
            if last is not None and (not points or points[-1] != last):
                points.append(last)
            return {'files': files, 'file': p.relative_to(path).as_posix(), 'variables': variables,
                    'variable': selected, 'points': points, 'samples': total,
                    'minimum': minimum, 'maximum': maximum, 'last': last[1] if last else None}
    return empty
