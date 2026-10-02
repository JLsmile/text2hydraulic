import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import setup_renderer
import icon_preview


class RendererDiagnosticsTests(unittest.TestCase):
    def test_check_uses_simulator_environment_overrides(self):
        with patch.object(sys, 'argv', ['setup_renderer.py', '--check']), \
             patch.dict(setup_renderer.os.environ, {'T2H_OMC': '/custom/omc',
                        'T2H_OPENHYDRAULICS': '/custom/OpenHydraulics/package.mo'}), \
             patch.object(icon_preview, 'environment', return_value={'available': True, 'message': 'ready'}) as environment:
            self.assertEqual(setup_renderer.main(), 0)
        cfg = environment.call_args.args[0]
        self.assertEqual(cfg['omc_command'], '/custom/omc')
        self.assertEqual(cfg['openhydraulics_package'], '/custom/OpenHydraulics/package.mo')

    def test_invalid_probe_output_is_reported(self):
        with patch.object(setup_renderer.subprocess,'run',return_value=CompletedProcess([],0,'invalid json','')):
            self.assertFalse(setup_renderer.probe('/fake/python')['available'])

    def test_check_never_installs(self):
        with patch.object(sys,'argv',['setup_renderer.py','--check']), patch.object(icon_preview,'environment',return_value={'available':True,'message':'ready'}), patch.object(setup_renderer.subprocess,'run') as run:
            self.assertEqual(setup_renderer.main(),0)
            run.assert_not_called()

    def test_install_failure_returns_nonzero(self):
        with patch.object(sys,'argv',['setup_renderer.py','--install']), patch.object(setup_renderer.subprocess,'run',side_effect=OSError('installation unavailable')):
            self.assertEqual(setup_renderer.main(),1)

    def test_import_error_is_retained(self):
        with patch.object(setup_renderer.subprocess,'run',return_value=CompletedProcess([],1,'',"ModuleNotFoundError: No module named 'svgwrite'")):
            result=setup_renderer.probe('/fake/python')
        self.assertFalse(result['available'])
        self.assertIn('svgwrite',result['error'])

    def test_library_loader_error_is_retained(self):
        with patch.object(setup_renderer.subprocess,'run',return_value=CompletedProcess([],1,'','OSError: no library called cairo was found')):
            self.assertIn('cairo',setup_renderer.probe('/fake/python')['error'])

    def test_missing_python_does_not_blame_available_omc(self):
        with patch.object(setup_renderer,'probe',return_value={'available':False,'error':'Missing svgwrite'}), patch.object(icon_preview.shutil,'which',return_value='/usr/bin/omc'):
            result=icon_preview.environment({},force=True)
        self.assertFalse(result['available'])
        self.assertIn('Missing svgwrite',result['message'])
        self.assertNotIn('compiler (omc) was not found',result['message'])
        icon_preview._PROBES.clear()


if __name__=='__main__':unittest.main()
