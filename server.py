#!/usr/bin/env python3
"""Local, dependency-free Claude Code stream bridge. Python 3.10+, Linux."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import shlex
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
import uuid
import webbrowser
import zipfile

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / 'runs'
ACTIVE = {}
LOCK = threading.RLock()
SETUP_LOCK = threading.RLock()
SETUP = {'status': 'pending', 'message': 'Automatic environment setup has not run yet.', 'tools': {}}
FINAL = {'completed', 'failed', 'stopped', 'interrupted', 'timeout'}
EXTENSIONS = {'.mo', '.mos', '.mat', '.csv', '.log', '.md', '.txt', '.json', '.jsonl', '.png', '.jpg', '.jpeg', '.svg', '.pdf'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, fallback=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


def save_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def config():
    data = read_json(ROOT / 'config.example.json', {})
    data.update(read_json(ROOT / 'config.json', {}))
    for key, env in [('claude_command', 'T2H_CLAUDE'), ('omc_command', 'T2H_OMC'),
                     ('openhydraulics_package', 'T2H_OPENHYDRAULICS'), ('model', 'T2H_MODEL')]:
        if os.environ.get(env):
            data[key] = os.environ[env]
    return data


def executable(command):
    if not isinstance(command, str) or not command:
        return None
    expanded = os.path.expanduser(command)
    if '/' in expanded and not os.path.isabs(expanded):
        expanded = str(ROOT / expanded)
    return shutil.which(expanded)


def runtime_env(cfg):
    """Include the selected CLI's bin directory (not its symlink target).

    npm/nvm launchers use /usr/bin/env node and need their sibling node binary.
    """
    env = dict(os.environ)
    bins = []
    for key in ('claude_command', 'omc_command'):
        command = cfg.get(key, '')
        if command and '/' in command:
            path = Path(command).expanduser()
            if not path.is_absolute():
                path = ROOT / path
            bins.append(str(path.absolute().parent))
    bins.extend([env.get('PATH', ''), str(Path.home() / '.local/bin'), '/usr/local/bin', '/usr/bin', '/bin'])
    env['PATH'] = os.pathsep.join(b for b in bins if b)
    return env


def login_shell_paths():
    """Read only marked command paths; never expose shell startup output."""
    shell = os.environ.get('SHELL', '/bin/bash')
    if Path(shell).name not in {'bash', 'zsh', 'sh', 'dash'} or not os.path.isfile(shell):
        shell = '/bin/bash'
    script = ('printf "\\n__T2H_CLAUDE__%s\\n" "$(command -v claude)"; '
              'printf "__T2H_OMC__%s\\n" "$(command -v omc)"')
    process = subprocess.Popen([shell, '-lc', script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return {}
    found = {}
    for line in output.splitlines():
        for name, marker in [('claude', '__T2H_CLAUDE__'), ('omc', '__T2H_OMC__')]:
            if line.startswith(marker) and os.path.isabs(line[len(marker):]):
                found[name] = line[len(marker):]
    return found


def command_candidates(name, configured):
    candidates = [executable(configured), shutil.which(name)]
    home = Path.home()
    for directory in [home / '.local/bin', home / '.npm-global/bin', home / '.npm/bin',
                      home / 'bin', Path('/usr/local/bin'), Path('/usr/bin'), Path('/bin'),
                      Path('/snap/bin')]:
        candidates.append(str(directory / name))
    if name == 'claude':
        for pattern in ['.nvm/versions/node/*/bin/claude', '.local/share/fnm/node-versions/*/installation/bin/claude']:
            # Prefer newer installed versions, comparing numeric version parts.
            candidates.extend(str(p) for p in sorted(home.glob(pattern),
                              key=lambda p: tuple(map(int, re.findall(r'\d+', str(p.relative_to(home))))), reverse=True))
    return list(dict.fromkeys(p for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)))


def probe_version(path, cfg):
    env = runtime_env(cfg)
    env['PATH'] = str(Path(path).parent) + os.pathsep + env['PATH']
    process = subprocess.Popen([path, '--version'], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, errors='replace', start_new_session=True)
    try:
        output, _ = process.communicate(timeout=6)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return False
    return process.returncode == 0 and bool(output.strip())


def auto_configure():
    """Repair portable paths, preserving model, permissions and user options."""
    global SETUP
    with SETUP_LOCK, LOCK:
        if ACTIVE:
            return {'status': 'busy', 'message': 'Setup will resume when the active run finishes.', 'tools': {}}
        target = ROOT / 'config.json'
        try:
            existing = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
            if not isinstance(existing, dict):
                raise ValueError('Expected a JSON object')
        except (ValueError, OSError):
            SETUP = {'status': 'error', 'message': 'config.json could not be read. Repair its JSON syntax and retry; the file was not overwritten.', 'tools': {}}
            return SETUP
        cfg = config()
        saved = {**read_json(ROOT / 'config.example.json', {}), **existing}
        tools_found = {}
        updates = {}
        shell_paths = None
        for key, name, variable in [('claude_command', 'claude', 'T2H_CLAUDE'),
                                    ('omc_command', 'omc', 'T2H_OMC')]:
            candidates = command_candidates(name, cfg.get(key))
            # Explicit environment overrides must not silently select another tool.
            if os.environ.get(variable):
                candidates = [executable(cfg.get(key))]
            found = None
            for candidate in candidates[:4]:
                if candidate:
                    try:
                        if probe_version(candidate, cfg):
                            found = os.path.abspath(candidate)
                            break
                    except OSError:
                        continue
            if not found and not os.environ.get(variable):
                if shell_paths is None:
                    try:
                        shell_paths = login_shell_paths()
                    except OSError:
                        shell_paths = {}
                candidate = executable(shell_paths.get(name))
                if candidate and candidate not in candidates:
                    try:
                        if probe_version(candidate, cfg):
                            found = os.path.abspath(candidate)
                    except OSError:
                        pass
            tools_found[name] = {'available': bool(found), 'path': found,
                                 'message': 'Found and verified.' if found else
                                 (f'{variable} points to an unavailable executable.' if os.environ.get(variable) else
                                  'Not found or could not start. Install this dependency, then select Auto-detect again.')}
            if found:
                updates[key] = found
        if not package_path(cfg).is_file() and not os.environ.get('T2H_OPENHYDRAULICS'):
            if (ROOT / 'bundle/OpenHydraulics/package.mo').is_file():
                updates['openhydraulics_package'] = 'bundle/OpenHydraulics/package.mo'
        # Environment overrides are per-launch, not durable user preferences.
        for key, env_key in [('claude_command', 'T2H_CLAUDE'), ('omc_command', 'T2H_OMC')]:
            if not os.environ.get(env_key) and key in updates:
                saved[key] = updates[key]
        if 'openhydraulics_package' in updates:
            saved['openhydraulics_package'] = updates['openhydraulics_package']
        try:
            changed = not target.exists() or saved != existing
            if changed:
                save_json(target, saved)
            SETUP = {'status': 'saved' if changed else 'checked',
                     'message': 'Local paths were detected and saved to config.json.' if changed else
                                'Local configuration is up to date.', 'tools': tools_found}
        except OSError:
            SETUP = {'status': 'error', 'message': 'Unable to save config.json. Make the demo directory writable and retry.', 'tools': tools_found}
        return SETUP


def package_path(cfg):
    path = Path(cfg['openhydraulics_package']).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def health():
    cfg = config()
    checks = {'claude': bool(executable(cfg['claude_command'])),
              'openmodelica': bool(executable(cfg['omc_command'])),
              'openhydraulics': package_path(cfg).is_file(),
              'skill': (ROOT / 'bundle/.claude/skills/text2hydraulic/SKILL.md').is_file(),
              'timeout': bool(shutil.which('timeout', path=runtime_env(cfg)['PATH']))}
    with SETUP_LOCK:
        setup = dict(SETUP)
    for name, key in [('claude', 'claude'), ('omc', 'openmodelica')]:
        if name in setup.get('tools', {}):
            checks[key] = checks[key] and setup['tools'][name]['available']
    import icon_preview
    renderer = icon_preview.environment(cfg)
    return {'renderer': renderer, 'checks': checks, 'ready': all(checks.values()) and setup.get('status') != 'error', 'model': cfg['model'] or 'Local default model',
            'package': str(package_path(cfg)), 'platform': os.name, 'setup': setup,
            'permission_mode': cfg.get('permission_mode', 'inherit'),
            'terminal_permission_mode': cfg.get('terminal_permission_mode', 'inherit')}


def save_permission_mode(mode):
    if mode not in {'inherit', 'default', 'acceptEdits', 'plan', 'auto', 'dontAsk', 'bypassPermissions'}:
        raise ValueError('Invalid permission mode.')
    with SETUP_LOCK:
        target = ROOT / 'config.json'
        try:
            current = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
            if not isinstance(current, dict):
                raise ValueError('Expected a JSON object')
        except (ValueError, OSError) as exc:
            raise ValueError('Cannot read config.json. Repair the file before saving permissions.') from exc
        saved = {**read_json(ROOT / 'config.example.json', {}), **current}
        saved.update(permission_mode=mode, terminal_permission_mode=mode)
        save_json(target, saved)
    return {'permission_mode': mode, 'terminal_permission_mode': mode,
            'message': 'Permission settings saved. They apply to new runs and newly opened sessions.'}


def run_path(run_id):
    if not re.fullmatch(r'[0-9a-f]{12}', run_id):
        raise ValueError('Invalid run ID')
    path = RUNS / run_id
    if not (path / 'run.json').is_file():
        raise FileNotFoundError('Run not found')
    return path


def saved_session_id(path):
    """Support both newly saved IDs and original portable demo records."""
    value = read_json(path / 'run.json', {}).get('session_id')
    if value:
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            pass
    stream = path / 'claude.stdout.jsonl'
    if stream.is_file():
        with stream.open(encoding='utf-8', errors='replace') as handle:
            for _ in range(200):
                line = handle.readline(2_000_000)
                if not line:
                    break
                try:
                    data = json.loads(line)
                    return str(uuid.UUID(data['session_id']))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
    raise ValueError('No saved session ID was found. The runtime may have failed before creating a conversation.')


def permission_args(cfg, interactive=False):
    modes = {'default', 'acceptEdits', 'plan', 'auto', 'dontAsk', 'bypassPermissions'}
    mode = cfg.get('permission_mode', 'inherit')
    if interactive and cfg.get('terminal_permission_mode', 'inherit') != 'inherit':
        mode = cfg['terminal_permission_mode']
    if mode == 'inherit':
        if not interactive:
            # Let the CLI apply normal settings precedence for new runs.
            return []
        # Resuming can restore the old run's dontAsk mode. Prefer the user's
        # current default, rather than restoring the demo's former forced mode.
        settings_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))).expanduser()
        settings = read_json(settings_dir / 'settings.json', {})
        permissions = settings.get('permissions', {}) if isinstance(settings, dict) else {}
        mode = permissions.get('defaultMode', 'default') if isinstance(permissions, dict) else 'default'
    if mode not in modes:
        raise ValueError('Invalid permission mode. Use inherit, default, acceptEdits, plan, auto, dontAsk or bypassPermissions.')
    return ['--permission-mode', mode]


def resume_command(path, cfg):
    cli = executable(cfg.get('claude_command'))
    if not cli:
        raise ValueError('Design runtime not found. Run Auto-detect first.')
    session_id = saved_session_id(path)
    args = ['env', '-u', 'CLAUDECODE', 'PATH=' + runtime_env(cfg)['PATH'], cli,
            '--resume', session_id, *permission_args(cfg, interactive=True)]
    return 'cd -- ' + shlex.quote(str(path)) + ' && ' + shlex.join(args)


def open_session(path):
    with LOCK:
        if path.name in ACTIVE or read_json(path / 'run.json', {}).get('status') not in FINAL:
            raise ValueError('Stop the background run before continuing this session in a terminal.')
        cfg = config()
        command = resume_command(path, cfg)
        reply = {'opened': False, 'command': command,
                 'message': 'Run this command in a terminal on the computer hosting the demo.'}
        env = runtime_env(cfg)
        env.pop('CLAUDECODE', None)
        if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
            return reply
        # Keep a failed resume visible, for example when CLI history was removed.
        script = command + '\nresult=$?; if [ "$result" -ne 0 ]; then printf "\\nSession could not open. Press Enter to close."; read -r answer; fi'
        for terminal, flags in [('gnome-terminal', ['--']), ('konsole', ['--separate', '-e']),
                                ('xfce4-terminal', ['--disable-server', '-x']),
                                ('kitty', []), ('alacritty', ['-e']),
                                ('x-terminal-emulator', ['-e']), ('xterm', ['-e'])]:
            binary = shutil.which(terminal, path=env['PATH'])
            if not binary:
                continue
            try:
                with (path / 'terminal-launch.log').open('ab') as log:
                    child = subprocess.Popen([binary, *flags, '/bin/bash', '-c', script],
                                             cwd=path, env=env, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=log, start_new_session=True)
                try:
                    code = child.wait(timeout=0.5)
                    if code != 0:
                        continue
                except subprocess.TimeoutExpired:
                    threading.Thread(target=child.wait, daemon=True).start()
                return {**reply, 'opened': True,
                        'message': 'Terminal launch requested using your configured permission mode. Continue the conversation there.'}
            except OSError:
                continue
        return reply


def artifact_path(path, name):
    candidate = (path / name).resolve()
    relative = candidate.relative_to(path.resolve())
    if any(p.startswith('.') for p in relative.parts) or candidate.suffix.lower() not in EXTENSIONS:
        raise ValueError('File cannot be downloaded')
    if not candidate.is_file():
        raise FileNotFoundError('File not found')
    return candidate


def artifacts(path):
    output = []
    for base, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(base) / d).is_symlink()]
        for name in files:
            p = Path(base) / name
            if p.is_symlink() or name.startswith('.') or p.suffix.lower() not in EXTENSIONS:
                continue
            if p.name.endswith('_info.json') or p.name in {'events.jsonl', 'run.json'}:
                continue
            try:
                output.append({'name': p.relative_to(path).as_posix(), 'size': p.stat().st_size, 'modified': p.stat().st_mtime_ns})
            except FileNotFoundError:
                pass
    return sorted(output, key=lambda x: x['name'])


def snapshot(path):
    memory_path = path / 'memory.json'
    memory = read_json(memory_path, {}) if memory_path.is_file() and memory_path.stat().st_size < 8_000_000 else {}
    return {'memory': memory if isinstance(memory, dict) else {}, 'artifacts': artifacts(path)}


def response_data(path):
    memory = read_json(path / 'memory.json', {})
    name = (memory.get('files') or {}).get('csv_file')
    candidates = []
    if name:
        try:
            candidates.append(artifact_path(path, name))
        except (ValueError, FileNotFoundError):
            pass
    if not candidates:
        candidates = [path / f['name'] for f in artifacts(path) if f['name'].endswith('.csv')]
    for candidate in candidates:
        points = []
        with candidate.open(encoding='utf-8-sig', errors='replace', newline='') as handle:
            reader = csv.DictReader(handle)
            names = reader.fieldnames or []
            t = next((n for n in names if n.strip() == 'time'), None)
            s = next((n for n in names if n.strip() == 'worktable.s'), None)
            v = next((n for n in names if n.strip() == 'worktable.v'), None)
            if not (t and s and v):
                continue
            # Bound preview memory; downloadable CSV retains every sample.
            stride = 1
            total = 0
            for row in reader:
                try:
                    point = [float(row[t]), float(row[s]), float(row[v])]
                except (ValueError, TypeError, KeyError):
                    continue
                if all(math.isfinite(x) for x in point):
                    if total % stride == 0:
                        points.append(point)
                    total += 1
                    if len(points) > 4000:
                        points = points[::2]
                        stride *= 2
            return {'file': candidate.relative_to(path).as_posix(), 'points': points, 'samples': total}
    return {'points': [], 'samples': 0}


class Run:
    def __init__(self, prompt, cfg):
        self.id = uuid.uuid4().hex[:12]
        self.path = RUNS / self.id
        self.path.mkdir(parents=True)
        self.cfg = cfg
        self.lock = threading.RLock()
        self.process = None
        self.cancel = threading.Event()
        self.seq = 0
        self.result = None
        self.meta = {'id': self.id, 'prompt': prompt, 'created': stamp(), 'status': 'starting', 'model': cfg['model']}
        save_json(self.path / 'run.json', self.meta)

    def emit(self, kind, data):
        with self.lock:
            self.seq += 1
            event = {'id': self.seq, 'time': stamp(), 'kind': kind, 'data': data}
            with (self.path / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + '\n')

    def status(self, status, message):
        with self.lock:
            self.meta.update(status=status, message=message)
            if status in FINAL:
                self.meta['finished'] = stamp()
            save_json(self.path / 'run.json', self.meta)
            self.emit('status', dict(self.meta))

    def stop(self):
        self.cancel.set()
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            self.kill_group(process)

    @staticmethod
    def kill_group(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        def kill_remaining():
            time.sleep(2)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        threading.Thread(target=kill_remaining, daemon=True).start()

    def reader(self, stream, kind):
        raw_name = 'claude.stdout.jsonl' if kind == 'claude' else 'claude.stderr.log'
        with (self.path / raw_name).open('w', encoding='utf-8') as raw:
            for line in iter(stream.readline, ''):
                raw.write(line)
                raw.flush()
                if not line.strip():
                    continue
                try:
                    data = json.loads(line) if kind == 'claude' else line.rstrip()
                except ValueError:
                    data = line.rstrip()
                if isinstance(data, dict) and data.get('type') == 'system' and data.get('session_id'):
                    try:
                        session_id = str(uuid.UUID(data['session_id']))
                        with self.lock:
                            self.meta['session_id'] = session_id
                            save_json(self.path / 'run.json', self.meta)
                    except (ValueError, TypeError, AttributeError):
                        pass
                if isinstance(data, dict) and data.get('type') == 'result':
                    self.result = data
                    (self.path / 'final-report.md').write_text(data.get('result') or '\n'.join(data.get('errors', [])), encoding='utf-8')
                self.emit(kind, data)
        stream.close()

    def execute(self):
        try:
            shutil.copytree(ROOT / 'bundle/.claude', self.path / '.claude')
            template = self.path / '.claude/skills/text2hydraulic/reference/94_template.json'
            memory = read_json(template)
            memory['meta'].update(created=stamp(), openmodelica_omc_path=executable(self.cfg['omc_command']),
                                  openhydraulics_package_path=str(package_path(self.cfg)))
            memory['input'].update(raw=self.meta['prompt'], raw_hash_sha256=hashlib.sha256(self.meta['prompt'].encode()).hexdigest())
            memory['files']['work_dir'] = str(self.path)
            save_json(template, memory)
            save_json(self.path / 'memory.json', memory)
            (self.path / 'prompt.txt').write_text(self.meta['prompt'], encoding='utf-8')
            instructions = (
                'This is a text2hydraulic research demonstration. First read '
                '.claude/skills/text2hydraulic/SKILL.md and follow its workflow. '
                'An initialized memory.json is in the current directory. Preserve input.raw, '
                'its original hash and the environment paths in meta. Keep all task outputs '
                'in the current directory and memory.json at its root. Update meta.phase and '
                'actual results at every stage. Use English for progress notes, design descriptions, '
                'assumptions, issues and the final report, even when the input is in Chinese. '
                'Progress notes should briefly describe the current engineering operation, '
                'for example: Now let me also add an issues entry about the velocity discrepancy. '
                'Do not narrate loading or invoking skills, mention the runtime brand, or print '
                'file paths, shell commands, raw file contents or tool arguments in progress notes. '
                'Perform the necessary calculations, generate Modelica models and run real '
                'OpenModelica simulations. Do not invent results or equate process completion '
                'with engineering acceptance. Retain issues and available artifacts on failure. '
                'Do not delete artifacts, modify files outside the task directory, or install software. '
                'This demo requires a circuit visualization: after generating the .mo file, run the bundled '
                'generate_icon_placement_annotations.py and generate_oil_line_annotations.py scripts '
                'as described in the skill, and check icon overlap. Preserve the model on layout failure. '
                'Export time, worktable.s and worktable.v to CSV. Also export actual cylinder port '
                'pressures and pump flow variables when available in the simulation result; verify '
                'their exact names first. Never synthesize missing signals. Keep files.csv_file updated. '
                'The final English report must cover requirements, assumptions, circuit, sizing, '
                'actual simulation metrics, acceptance and artifacts. '
                '\nRuntime environment: ' + json.dumps(memory['meta'], ensure_ascii=False))
            command = [executable(self.cfg['claude_command']), '-p', '--output-format', 'stream-json',
                       '--verbose', '--include-partial-messages', *permission_args(self.cfg),
                       '--allowedTools', ','.join(self.cfg['allowed_tools']), '--append-system-prompt', instructions]
            if self.cfg.get('model'):
                command.extend(['--model', self.cfg['model']])
            env = runtime_env(self.cfg)
            env.pop('CLAUDECODE', None)
            env['PYTHONUNBUFFERED'] = '1'
            with self.lock:
                if self.cancel.is_set():
                    self.status('stopped', 'Run stopped')
                    return
                self.process = subprocess.Popen(command, cwd=self.path, env=env, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                text=True, encoding='utf-8', errors='replace', start_new_session=True, bufsize=1)
            readers = [threading.Thread(target=self.reader, args=(stream, kind), daemon=True)
                       for stream, kind in [(self.process.stdout, 'claude'), (self.process.stderr, 'stderr')]]
            for reader in readers:
                reader.start()
            self.process.stdin.write(self.meta['prompt'])
            self.process.stdin.close()
            self.status('running', 'Design workflow is running')
            started = time.monotonic()
            last = ''
            timed_out = False
            while self.process.poll() is None:
                if time.monotonic() - started > int(self.cfg['timeout_seconds']):
                    timed_out = True
                    self.stop()
                snap = snapshot(self.path)
                serialized = json.dumps(snap, sort_keys=True)
                if serialized != last:
                    self.emit('state', snap)
                    last = serialized
                time.sleep(0.5)
            for reader in readers:
                reader.join(timeout=5)
            self.emit('state', snapshot(self.path))
            self.meta['exit_code'] = self.process.returncode
            if timed_out:
                self.status('timeout', 'Run time limit reached; processes stopped and records retained')
            elif self.cancel.is_set():
                self.status('stopped', 'Run stopped; existing records and artifacts retained')
            elif self.process.returncode or not self.result or self.result.get('is_error'):
                self.status('failed', 'Run failed or returned an incomplete result; see saved records')
            elif self.result.get('permission_denials'):
                self.status('failed', 'An operation was denied; review records and local permissions before retrying')
            else:
                self.status('completed', 'Run finished; engineering acceptance is recorded in the design state')
        except Exception as exc:
            if self.process and self.process.poll() is None:
                self.stop()
            self.emit('stderr', str(exc))
            self.status('failed', str(exc))
        finally:
            with LOCK:
                ACTIVE.pop(self.id, None)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Text2Hydraulic/1.0'

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith('GET /api/health'):
            return
        super().log_message(fmt, *args)

    def valid_request(self):
        host = self.headers.get('Host', '')
        accepted = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        return host in accepted and (not origin or origin in {f'http://{h}' for h in accepted})

    def send_bytes(self, data, content_type='application/json; charset=utf-8', code=200, download=False):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment')
        self.end_headers()
        self.wfile.write(data)

    def json(self, data, code=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), code=code)

    def do_GET(self):
        if not self.valid_request():
            return self.json({'error': 'Only local same-origin access is allowed'}, 403)
        try:
            parsed = urlparse(self.path)
            parts = unquote(parsed.path).strip('/').split('/')
            query = parse_qs(parsed.query)
            if parsed.path == '/api/health':
                return self.json(health())
            if parsed.path == '/api/runs':
                records = [read_json(p) for p in RUNS.glob('*/run.json')]
                return self.json(sorted([r for r in records if r], key=lambda r: r['created'], reverse=True))
            if parts[:2] == ['api', 'runs'] and len(parts) >= 3:
                path = run_path(parts[2])
                action = parts[3] if len(parts) > 3 else ''
                if not action:
                    return self.json({'run': read_json(path / 'run.json'), **snapshot(path)})
                if action == 'events':
                    return self.events(path, query)
                if action == 'model':
                    import icon_preview
                    return self.json(icon_preview.preview(path, query.get('file', [''])[0], config(), artifact_path, artifacts, query.get('retry',['0'])[0]=='1'))
                if action == 'model-image':
                    import icon_preview
                    folder=icon_preview.cache_directory(path, query.get('key',[''])[0])
                    fmt=query.get('format',['svg'])[0]
                    if fmt not in {'svg','png'}:raise ValueError('Unsupported image format')
                    if (icon_preview.read_json(folder/'result.json',{}) or {}).get('status')!='ready':raise FileNotFoundError('Model image is not ready')
                    p=artifact_path(path,(folder/f'diagram.{fmt}').relative_to(path).as_posix())
                    return self.send_bytes(p.read_bytes(),'image/svg+xml' if fmt=='svg' else 'image/png')
                if action == 'signals':
                    from visualization import signal_data
                    return self.json(signal_data(path, query.get('file', [''])[0], query.get('variable', [''])[0], artifact_path, artifacts))
                if action == 'response':
                    return self.json(response_data(path))
                if action == 'file':
                    p = artifact_path(path, query.get('name', [''])[0])
                    return self.send_file(p)
                if action == 'export':
                    # Temporary zip avoids keeping large MAT exports in RAM.
                    import tempfile
                    with tempfile.TemporaryFile() as out:
                        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
                            for f in artifacts(path):
                                p = artifact_path(path, f['name'])
                                archive.write(p, f['name'])
                            for name in ['run.json', 'events.jsonl']:
                                if (path / name).is_file():
                                    archive.write(path / name, name)
                        out.seek(0, 2)
                        size = out.tell()
                        out.seek(0)
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header('Content-Disposition', f'attachment; filename="text2hydraulic-{parts[2]}.zip"')
                        self.send_header('Content-Length', str(size))
                        self.end_headers()
                        shutil.copyfileobj(out, self.wfile)
                    return
                raise FileNotFoundError('Endpoint not found')
            static = (ROOT / 'static' / ('index.html' if parsed.path == '/' else unquote(parsed.path).lstrip('/'))).resolve()
            static.relative_to((ROOT / 'static').resolve())
            if not static.is_file():
                raise FileNotFoundError('Page not found')
            self.send_bytes(static.read_bytes(), (mimetypes.guess_type(static)[0] or 'application/octet-stream') + '; charset=utf-8')
        except (ValueError, FileNotFoundError) as exc:
            self.json({'error': str(exc)}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.json({'error': str(exc)}, 500)

    def send_file(self, path):
        with path.open('rb') as handle:
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(os.fstat(handle.fileno()).st_size))
            self.send_header('Content-Disposition', 'attachment')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            shutil.copyfileobj(handle, self.wfile)

    def events(self, path, query):
        after = int(self.headers.get('Last-Event-ID') or query.get('after', ['0'])[0])
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        offset = 0
        last_ping = 0
        while True:
            file = path / 'events.jsonl'
            if file.exists():
                with file.open('r', encoding='utf-8') as handle:
                    handle.seek(offset)
                    while True:
                        line = handle.readline()
                        if not line or not line.endswith('\n'):
                            break
                        offset = handle.tell()
                        event = json.loads(line)
                        if event['id'] > after:
                            self.wfile.write(f"id: {event['id']}\ndata: {line}\n".encode())
                            after = event['id']
                    self.wfile.flush()
            meta = read_json(path / 'run.json', {})
            if meta.get('status') in FINAL:
                # Final status is written before its event; ensure the final event flushes.
                with LOCK:
                    active = path.name in ACTIVE
                if not active:
                    if file.exists() and file.stat().st_size > offset:
                        continue
                    self.wfile.write(b'event: end\ndata: {}\n\n')
                    self.wfile.flush()
                    return
            if time.monotonic() - last_ping > 10:
                self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
                last_ping = time.monotonic()
            time.sleep(0.25)

    def do_POST(self):
        if not self.valid_request() or self.headers.get('Content-Type') != 'application/json':
            return self.json({'error': 'Only local same-origin JSON requests are allowed'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 100_000:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            if self.path == '/api/environment/setup':
                setup = auto_configure()
                import icon_preview
                icon_preview.environment(config(), force=True)
                return self.json({**health(), 'setup': setup})
            if self.path == '/api/settings/permissions':
                return self.json(save_permission_mode(data.get('permission_mode')))
            if self.path == '/api/runs':
                prompt = data.get('prompt', '').strip()
                if not prompt or len(prompt) > 20000:
                    raise ValueError('Enter 1–20000 characters of operating requirements')
                if not health()['ready']:
                    return self.json({'error': 'Runtime environment is not ready; check environment settings'}, 409)
                with LOCK:
                    if ACTIVE:
                        return self.json({'error': 'A run is already active; wait or stop it first'}, 409)
                    run = Run(prompt, config())
                    ACTIVE[run.id] = run
                    threading.Thread(target=run.execute, daemon=True).start()
                return self.json(run.meta, 201)
            parts = self.path.strip('/').split('/')
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'terminal':
                return self.json(open_session(run_path(parts[2])))
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'stop':
                run_path(parts[2])
                with LOCK:
                    run = ACTIVE.get(parts[2])
                if run:
                    run.stop()
                return self.json({'stopping': bool(run)})
            self.json({'error': 'Endpoint not found'}, 404)
        except (ValueError, TypeError, AttributeError, FileNotFoundError) as exc:
            self.json({'error': str(exc)}, 400)
        except Exception as exc:
            self.json({'error': str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)
    import fcntl
    runtime_lock = (RUNS / '.server.lock').open('a')
    try:
        fcntl.flock(runtime_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.exit(1, 'A demo server is already using this directory. Stop it first.\n')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    auto_configure()
    for path in RUNS.glob('*/run.json'):
        meta = read_json(path, {})
        if meta.get('status') not in FINAL:
            meta.update(status='interrupted', message='Previous server stopped unexpectedly; records retained', finished=stamp())
            save_json(path, meta)
    url = f'http://127.0.0.1:{args.port}'
    print(f'\nText2Hydraulic Demo → {url}\nPress Ctrl+C to stop. Run data: {RUNS}\n', flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with LOCK:
            pending = list(ACTIVE.values())
        for run in pending:
            run.stop()
        deadline = time.monotonic() + 7
        while ACTIVE and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        import icon_preview
        icon_preview.shutdown()
        server.server_close()
        runtime_lock.close()


if __name__ == '__main__':
    main()
