"""Integration tests use a fake CLI; they never spend model credits."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import zipfile

spec = importlib.util.spec_from_file_location('server', Path(__file__).resolve().parents[1] / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

FAKE = '''#!/usr/bin/env python3
import json,sys,time,pathlib,subprocess,os
prompt=sys.stdin.read()
def emit(d): print(json.dumps(d),flush=True)
if prompt=='SLOW':
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    pathlib.Path('child.pid').write_text(str(child.pid))
    emit({'type':'system','subtype':'init'})
    time.sleep(60)
if prompt=='FAIL':
    print('model unavailable',file=sys.stderr,flush=True)
    sys.exit(3)
emit({'type':'system','subtype':'init','model':'test-only','session_id':'12345678-1234-4234-8234-123456789abc'})
emit({'type':'stream_event','event':{'type':'message_start','message':{'id':'msg1'}}})
emit({'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}})
emit({'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'测试输出'}}})
time.sleep(.15)
emit({'type':'assistant','message':{'id':'msg1','content':[{'type':'text','text':'测试输出'}]}})
p=pathlib.Path('memory.json'); m=json.loads(p.read_text());m['meta']['phase']='done';m['acceptance'].update(done_allowed=True,result_level_success=True,strict_reproducible_success=True);p.write_text(json.dumps(m))
pathlib.Path('response.csv').write_text('time,worktable.s,worktable.v\\n0,0,0\\n1,0.1,0.1\\n2,nan,1\\n')
pathlib.Path('model.mo').write_text('model Test end Test;')
emit({'type':'result','result':'# 测试报告\\n成功','is_error':False,'duration_ms':150,'permission_denials':[]})
'''

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(server, 'RUNS', self.root / 'runs')
        self.patch.start()
        server.RUNS.mkdir()
        self.cli = self.root / 'fake-claude'
        self.cli.write_text(FAKE)
        self.cli.chmod(0o755)
        self.cfg = server.config()
        self.cfg.update(claude_command=str(self.cli),timeout_seconds=10)

    def tearDown(self):
        for run in list(server.ACTIVE.values()):
            run.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def start(self,prompt):
        run=server.Run(prompt,self.cfg)
        with server.LOCK: server.ACTIVE[run.id]=run
        thread=threading.Thread(target=run.execute)
        thread.start()
        return run,thread

    def test_stream_persistence_and_result(self):
        run,thread=self.start('测试工况');thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(run.meta['status'],'completed')
        self.assertEqual(server.read_json(run.path/'run.json')['session_id'],'12345678-1234-4234-8234-123456789abc')
        events=[json.loads(line) for line in (run.path/'events.jsonl').read_text().splitlines()]
        self.assertEqual([e['id'] for e in events],list(range(1,len(events)+1)))
        self.assertTrue(any(e['kind']=='claude' and e['data'].get('type')=='stream_event' for e in events))
        self.assertEqual((run.path/'final-report.md').read_text(),'# 测试报告\n成功')
        self.assertEqual(server.response_data(run.path)['samples'],2)
        self.assertEqual(server.read_json(run.path/'memory.json')['input']['raw'],'测试工况')
        self.assertTrue((run.path/'claude.stdout.jsonl').is_file())
        self.assertNotIn('.claude',str(server.artifacts(run.path)))

    def test_failure_retains_stderr(self):
        run,thread=self.start('FAIL');thread.join(10)
        self.assertEqual(run.meta['status'],'failed')
        self.assertIn('model unavailable',(run.path/'claude.stderr.log').read_text())

    def test_stop_and_timeout(self):
        for timeout in [False,True]:
            self.cfg['timeout_seconds']=1 if timeout else 10
            run,thread=self.start('SLOW')
            deadline=time.monotonic()+5
            while not (run.path/'child.pid').exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue((run.path/'child.pid').exists())
            child=int((run.path/'child.pid').read_text())
            if not timeout:run.stop()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(run.meta['status'],'timeout' if timeout else 'stopped')
            proc=Path(f'/proc/{child}/stat')
            if proc.exists():self.assertEqual(proc.read_text().split()[2],'Z')

    def test_stop_before_spawn(self):
        run=server.Run('early',self.cfg);run.stop();run.execute()
        self.assertEqual(run.meta['status'],'stopped')
        self.assertIsNone(run.process)

    def test_download_confinement(self):
        run,thread=self.start('OK');thread.join(10)
        secret=self.root/'secret.json';secret.write_text('{}')
        (run.path/'leak.json').symlink_to(secret)
        for name in ['../secret.json','leak.json','.claude/skills/text2hydraulic/reference/94_template.json']:
            with self.assertRaises(ValueError):server.artifact_path(run.path,name)
        self.assertNotIn('leak.json',[a['name'] for a in server.artifacts(run.path)])

    def test_http_stream_reconnect_export_and_origin(self):
        run,thread=self.start('HTTP');thread.join(10)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        runner=threading.Thread(target=http.serve_forever,daemon=True);runner.start()
        base=f'http://127.0.0.1:{http.server_port}'
        try:
            def get(path,headers=None):
                with build_opener(ProxyHandler({})).open(Request(base+path,headers=headers or {}),timeout=5) as r:return r.read()
            import icon_preview
            with patch.object(icon_preview,'environment',return_value={'available':False,'message':'Test renderer unavailable'}):
                model=json.loads(get(f'/api/runs/{run.id}/model'))
            self.assertEqual(model['status'],'unavailable')
            self.assertEqual(model['file'],'model.mo')
            signals=json.loads(get(f'/api/runs/{run.id}/signals?variable=worktable.s'))
            self.assertEqual(signals['variable'],'worktable.s')
            self.assertEqual(signals['samples'],2)
            stream=get(f'/api/runs/{run.id}/events').decode()
            self.assertIn('event: end',stream)
            ids=[int(l[4:]) for l in stream.splitlines() if l.startswith('id: ')]
            after=ids[len(ids)//2]
            resumed=get(f'/api/runs/{run.id}/events',{'Last-Event-ID':str(after)}).decode()
            self.assertTrue(all(int(l[4:])>after for l in resumed.splitlines() if l.startswith('id: ')))
            archive=zipfile.ZipFile(io.BytesIO(get(f'/api/runs/{run.id}/export')))
            self.assertIn('events.jsonl',archive.namelist());self.assertIn('model.mo',archive.namelist())
            with self.assertRaises(HTTPError) as err:get('/api/health',{'Origin':'https://evil.example'})
            self.assertEqual(err.exception.code,403)
            with self.assertRaises(HTTPError) as err:get('/api/health',{'Host':'evil.example'})
            self.assertEqual(err.exception.code,403)
        finally:http.shutdown();http.server_close()

if __name__=='__main__':unittest.main(verbosity=2)
