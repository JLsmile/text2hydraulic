import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import icon_preview as prod


class IconPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        spec=importlib.util.spec_from_file_location('isolated_renderer',prod.__file__)
        self.renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.renderer)
        self.patch=patch.object(self.renderer,'ROOT',self.root);self.patch.start()
        self.run=self.root/'run';self.run.mkdir();self.model=self.run/'Circuit.mo'
        self.model.write_text('model Circuit Some.Pump pump; end Circuit;');os.utime(self.model,(time.time()-5,time.time()-5))
        for name in ['library.mo','exporter.py','visualization.py']:(self.root/name).write_text('test')
        self.script=self.root/'render_model.py'
        self.script.write_text('''import pathlib,sys,json
p=pathlib.Path(sys.argv[sys.argv.index('--output')+1])
(p/'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
(p/'diagram.png').write_bytes(b'PNG test fixture')
(p/'metadata.json').write_text(json.dumps({'width':640,'height':480,'components':1,'connections':0,'warnings':[]}))
''')
        self.env={'available':True,'python':sys.executable,'exporter':str(self.root/'exporter.py'),'omc':'/usr/bin/true','library':str(self.root/'library.mo')}
        self.envpatch=patch.object(self.renderer,'environment',return_value=self.env);self.envpatch.start()

    def tearDown(self):
        self.renderer.shutdown()
        if self.renderer._THREAD:self.renderer._THREAD.join(3)
        self.envpatch.stop();self.patch.stop();self.tmp.cleanup()

    def preview(self, **kw):
        return self.renderer.preview(self.run,'',{},server.artifact_path,server.artifacts,**kw)

    def done(self):
        until=time.monotonic()+5
        while time.monotonic()<until:
            data=self.preview()
            if data['status'] in {'ready','failed'}:return data
            time.sleep(.05)
        self.fail('Render did not finish')

    def test_async_render_cache_and_changed_model(self):
        queued=self.preview();self.assertIn(queued['status'],['queued','rendering'])
        result=self.done();self.assertEqual(result['status'],'ready')
        key=result['key'];file=self.renderer.cache_directory(self.run,key)/'diagram.svg';mtime=file.stat().st_mtime_ns
        self.assertEqual(self.preview()['key'],key);self.assertEqual(file.stat().st_mtime_ns,mtime)
        # Same file size, changed content: must invalidate cache.
        self.model.write_text('model Circuit Some.Tank tank; end Circuit;');os.utime(self.model,(time.time()-5,time.time()-5))
        self.assertNotEqual(self.preview()['key'],key)
        self.assertEqual(self.done()['status'],'ready')

    def test_failure_retained_and_explicit_retry(self):
        self.script.write_text('raise RuntimeError("test render failure")')
        data=self.done();self.assertEqual(data['status'],'failed')
        file=self.renderer.cache_directory(self.run,data['key'])/'render.log'
        self.assertIn('test render failure',file.read_text())
        self.assertEqual(self.preview()['status'],'failed')
        self.assertIn(self.preview(retry=True)['status'],['queued','rendering'])
        self.done()

    def test_missing_annotations_is_worker_error_not_fabricated_graph(self):
        self.script.write_text('raise RuntimeError("No placed components found")')
        result=self.done()
        self.assertEqual(result['status'],'failed')
        self.assertIn('Circuit.mo has no component Placement annotations',result['message'])
        self.assertIn('Select the final circuit model',result['message'])

    def test_cache_path_confinement(self):
        for key in ['../secret','x'*64,'a'*63]:
            with self.assertRaises(ValueError):self.renderer.cache_directory(self.run,key)
        (self.run/'model-previews').symlink_to(self.root)
        with self.assertRaises(ValueError):self.renderer.cache_directory(self.run,'a'*64)

    def test_file_still_being_written_waits(self):
        os.utime(self.model,None)
        self.assertEqual(self.preview()['status'],'waiting')
        self.assertFalse(self.renderer._JOBS)

    def test_source_selection_and_missing_environment(self):
        with self.assertRaises(ValueError):self.renderer.preview(self.run,'../Circuit.mo',{},server.artifact_path,server.artifacts)
        with patch.object(self.renderer,'environment',return_value={'available':False,'message':'Install dependencies'}):
            self.assertEqual(self.preview()['status'],'unavailable')
        self.model.unlink();self.assertEqual(self.preview()['status'],'empty')

if __name__=='__main__':unittest.main()
