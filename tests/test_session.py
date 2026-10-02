"""Interactive continuation uses an exact session ID and normal permission prompts."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch, Mock

spec=importlib.util.spec_from_file_location('session_server',Path(__file__).resolve().parents[1]/'server.py')
server=importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
SESSION='12345678-1234-4234-8234-123456789abc'

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"run with space's"
        self.path.mkdir()
        self.cli=Path(self.tmp.name)/'runtime tool'
        self.cli.write_text('#!/bin/sh\nexit 0\n');self.cli.chmod(0o755)
        server.save_json(self.path/'run.json',{'status':'failed','session_id':SESSION})
        self.cfg={'claude_command':str(self.cli),'omc_command':'omc','terminal_permission_mode':'default'}
        self.config_patch=patch.object(server,'config',return_value=self.cfg);self.config_patch.start()
    def tearDown(self):
        server.ACTIVE.clear();self.config_patch.stop();self.tmp.cleanup()
    def test_command_quotes_path_and_uses_interactive_permissions(self):
        command=server.resume_command(self.path,self.cfg)
        tokens=shlex.split(command)
        self.assertEqual(tokens[:4],['cd','--',str(self.path),'&&'])
        self.assertIn(str(self.cli),tokens)
        self.assertEqual(tokens[-4:],['--resume',SESSION,'--permission-mode','default'])
        self.assertNotIn('dontAsk',tokens)
        self.assertNotIn('--dangerously-skip-permissions',tokens)
    def test_background_inherits_global_mode_without_overriding_it(self):
        self.assertEqual(server.permission_args({'permission_mode':'inherit'}),[])
        self.assertEqual(server.permission_args({'permission_mode':'bypassPermissions'}),['--permission-mode','bypassPermissions'])
    def test_resume_uses_current_global_mode_instead_of_old_dontask(self):
        settings=Path(self.tmp.name)/'custom-settings'
        settings.mkdir()
        (settings/'settings.json').write_text(json.dumps({'permissions':{'defaultMode':'bypassPermissions'}}))
        with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(settings)}):
            inherited=server.permission_args({'permission_mode':'inherit'},interactive=True)
        self.assertEqual(inherited,['--permission-mode','bypassPermissions'])
        self.assertEqual(server.permission_args({'permission_mode':'bypassPermissions','terminal_permission_mode':'default'},interactive=True),['--permission-mode','default'])
    def test_invalid_mode_is_not_passed_to_cli(self):
        with self.assertRaisesRegex(ValueError,'Invalid permission mode'):
            server.permission_args({'permission_mode':'invalid'})
    def test_old_record_falls_back_to_raw_stream(self):
        server.save_json(self.path/'run.json',{'status':'failed'})
        (self.path/'claude.stdout.jsonl').write_text('invalid\n'+json.dumps({'type':'system','session_id':SESSION})+'\n')
        self.assertEqual(server.saved_session_id(self.path),SESSION)
    def test_missing_session_has_clear_error(self):
        server.save_json(self.path/'run.json',{'status':'failed'})
        with self.assertRaisesRegex(ValueError,'No saved session ID'):server.open_session(self.path)
    def test_running_session_cannot_be_opened_concurrently(self):
        server.ACTIVE[self.path.name]=object()
        with self.assertRaisesRegex(ValueError,'Stop the background'):server.open_session(self.path)
    def test_no_desktop_returns_copyable_command_without_launching(self):
        with patch.dict(os.environ,{'DISPLAY':'','WAYLAND_DISPLAY':''}),patch.object(server.subprocess,'Popen') as process:
            result=server.open_session(self.path)
        self.assertFalse(result['opened']);self.assertIn(SESSION,result['command']);process.assert_not_called()
    def test_desktop_launch_is_argv_based_and_resumes_exact_session(self):
        child=Mock();child.wait.return_value=0
        which=server.shutil.which
        def find(name,**kwargs):
            return '/usr/bin/gnome-terminal' if name=='gnome-terminal' else which(name,**kwargs)
        with patch.dict(os.environ,{'DISPLAY':':1'}),patch.object(server.shutil,'which',side_effect=find),patch.object(server.subprocess,'Popen',return_value=child) as process:
            result=server.open_session(self.path)
        self.assertTrue(result['opened'])
        args,kwargs=process.call_args
        self.assertEqual(args[0][:4],['/usr/bin/gnome-terminal','--','/bin/bash','-c'])
        self.assertIn(SESSION,args[0][-1]);self.assertIn('--permission-mode default',args[0][-1])
        self.assertFalse(kwargs.get('shell',False));self.assertEqual(kwargs['cwd'],self.path)

if __name__=='__main__':unittest.main(verbosity=2)
