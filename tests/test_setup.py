"""Portable runtime discovery without real model calls or user config changes."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('setup_server', Path(__file__).resolve().parents[1] / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
DEFAULTS = server.read_json(server.ROOT / 'config.example.json')

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        (self.root / 'config.example.json').write_text(json.dumps(DEFAULTS))
        package = self.root / 'bundle/OpenHydraulics/package.mo'
        package.parent.mkdir(parents=True)
        package.write_text('package OpenHydraulics end OpenHydraulics;')
        skill = self.root / 'bundle/.claude/skills/text2hydraulic/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('test')
        self.patches = [patch.object(server, 'ROOT', self.root), patch.object(Path, 'home', return_value=self.home),
                        patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True),
                        patch.object(server, 'login_shell_paths', return_value={})]
        for p in self.patches:p.start()
        server.SETUP = {'status': 'pending', 'tools': {}}
        self.omc = self.make_bin(self.home / '.local/bin/omc')
        original = server.command_candidates
        isolated = patch.object(server, 'command_candidates', side_effect=lambda name, configured:
                                [p for p in original(name, configured) if str(p).startswith(str(self.root))])
        isolated.start()
        self.patches.append(isolated)

    def tearDown(self):
        server.ACTIVE.clear()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def make_bin(self, path, text='#!/bin/sh\nprintf "test version 1.0\\n"\n'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)
        return path

    def test_creates_config_and_finds_native_install_outside_path(self):
        cli = self.make_bin(self.home / '.local/bin/claude')
        result = server.auto_configure()
        cfg = server.read_json(self.root / 'config.json')
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(cfg['claude_command'], str(cli))
        self.assertEqual(cfg['omc_command'], str(self.omc))
        self.assertTrue(server.health()['ready'])
        self.assertEqual(server.auto_configure()['status'], 'checked')

    def test_repairs_old_machine_paths_and_preserves_user_settings(self):
        cli = self.make_bin(self.home / '.local/bin/claude')
        original = {'claude_command':'/old/computer/claude', 'omc_command':'/old/computer/omc',
                    'openhydraulics_package':'/old/computer/package.mo', 'model':'custom-model',
                    'timeout_seconds':321, 'allowed_tools':['Read'], 'custom_setting':42}
        (self.root / 'config.json').write_text(json.dumps(original))
        server.auto_configure()
        cfg = server.read_json(self.root / 'config.json')
        self.assertEqual(cfg['claude_command'], str(cli))
        self.assertEqual(cfg['openhydraulics_package'], 'bundle/OpenHydraulics/package.mo')
        for key in ['model','timeout_seconds','allowed_tools','custom_setting']:
            self.assertEqual(cfg[key],original[key])

    def test_nvm_node_is_available_to_version_check_and_child(self):
        folder = self.home / '.nvm/versions/node/v22.1.0/bin'
        cli = self.make_bin(folder / 'claude', '#!/usr/bin/env node\nignored\n')
        self.make_bin(folder / 'node')
        server.auto_configure()
        cfg = server.config()
        self.assertEqual(cfg['claude_command'], str(cli))
        self.assertTrue(server.probe_version(str(cli),cfg))
        self.assertEqual(server.runtime_env(cfg)['PATH'].split(os.pathsep)[0], str(folder))

    def test_permission_setting_is_saved_for_both_launch_paths(self):
        target=self.root/'config.json'
        target.write_text(json.dumps({'model':'keep-model','timeout_seconds':456,'custom':True}))
        result=server.save_permission_mode('bypassPermissions')
        saved=server.read_json(target)
        self.assertEqual(result['permission_mode'],'bypassPermissions')
        self.assertEqual(saved['terminal_permission_mode'],'bypassPermissions')
        self.assertEqual(saved['model'],'keep-model')
        self.assertEqual(saved['timeout_seconds'],456)
        self.assertTrue(saved['custom'])
        self.assertEqual(server.permission_args(saved),['--permission-mode','bypassPermissions'])
        self.assertEqual(server.permission_args(saved,interactive=True),['--permission-mode','bypassPermissions'])
        server.save_permission_mode('default')
        self.assertEqual(server.config()['permission_mode'],'default')
        self.assertEqual(server.health()['permission_mode'],'default')

    def test_permission_setting_rejects_invalid_mode_and_broken_json(self):
        target=self.root/'config.json'
        with self.assertRaises(ValueError):server.save_permission_mode('bad-mode')
        self.assertFalse(target.exists())
        target.write_text('{broken')
        with self.assertRaises(ValueError):server.save_permission_mode('bypassPermissions')
        self.assertEqual(target.read_text(),'{broken')

    def test_relative_executable_uses_demo_directory_for_child_path(self):
        env = server.runtime_env({'claude_command':'bin/claude', 'omc_command':'omc'})
        self.assertEqual(env['PATH'].split(os.pathsep)[0],str(self.root / 'bin'))

    def test_missing_tool_is_reported_without_guessing_a_path(self):
        with patch.object(server, 'command_candidates', side_effect=lambda name, _: [str(self.omc)] if name=='omc' else []):
            result = server.auto_configure()
        self.assertFalse(result['tools']['claude']['available'])
        self.assertIn('Install',result['tools']['claude']['message'])
        self.assertFalse(server.health()['ready'])

    def test_malformed_config_and_active_run_are_not_overwritten(self):
        target = self.root / 'config.json'
        target.write_text('{invalid')
        self.assertEqual(server.auto_configure()['status'],'error')
        self.assertEqual(target.read_text(),'{invalid')
        server.ACTIVE['test'] = object()
        self.assertEqual(server.auto_configure()['status'],'busy')
        self.assertEqual(target.read_text(),'{invalid')

    def test_preserves_valid_custom_executable_and_environment_override(self):
        custom = self.make_bin(self.home / 'custom/runtime')
        self.make_bin(self.home / '.local/bin/claude')
        target = self.root / 'config.json'
        target.write_text(json.dumps({'claude_command':str(custom)}))
        server.auto_configure()
        self.assertEqual(server.config()['claude_command'], str(custom))
        with patch.dict(os.environ, {'T2H_CLAUDE':'/missing/pinned/runtime'}):
            result = server.auto_configure()
        self.assertFalse(result['tools']['claude']['available'])
        self.assertIn('T2H_CLAUDE',result['tools']['claude']['message'])
        self.assertEqual(server.read_json(target)['claude_command'], str(custom))

if __name__ == '__main__':unittest.main(verbosity=2)
