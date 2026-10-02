"""Regression checks for the old generate_icons / OMPython 4 API mismatch."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_model import install_exporter_queries


class ExporterCompatibilityTests(unittest.TestCase):
    def exporter(self, session):
        def legacy_query(*args, **kwargs):
            session.clearOMParserResult()
            raise AssertionError('The legacy query helper must not run')
        exporter = SimpleNamespace(omc=session, ask_omc=legacy_query)
        install_exporter_queries(exporter)
        return exporter

    def test_current_session_needs_no_legacy_parser_state(self):
        session = SimpleNamespace(sendExpression=Mock(side_effect=[('Base.Pump', 'Base.Fluid'), False]))
        exporter = self.exporter(session)
        self.assertEqual(exporter.ask_omc('getInheritanceCount', 'Hydraulics.Pump'), 2)
        self.assertEqual(exporter.ask_omc('getNthInheritedClass', 'Hydraulics.Pump, 1'), 'Base.Pump')
        self.assertIs(exporter.ask_omc('isConnector', 'Hydraulics.Pump'), False)
        session.sendExpression.assert_any_call('getInheritedClasses(Hydraulics.Pump)', parsed=True)
        self.assertEqual(session.sendExpression.call_count, 2)

    def test_annotations_stay_raw_and_cached_separately(self):
        raw = '{Placement(true,0,0,-10,-10,10,10,0,-,-,-,-,-,-,-)}'
        session = SimpleNamespace(sendExpression=Mock(side_effect=[raw, ('parsed',)]))
        exporter = self.exporter(session)
        self.assertEqual(exporter.ask_omc('getComponentAnnotations', 'Circuit', parsed=False), raw)
        self.assertEqual(exporter.ask_omc('getComponentAnnotations', 'Circuit', parsed=False), raw)
        self.assertEqual(exporter.ask_omc('getComponentAnnotations', 'Circuit'), ('parsed',))
        self.assertEqual(session.sendExpression.call_count, 2)
        session.sendExpression.assert_any_call('getComponentAnnotations(Circuit)', parsed=False)

    def test_query_errors_retain_expression_and_original_cause(self):
        error = ValueError('class not found')
        session = SimpleNamespace(sendExpression=Mock(side_effect=error))
        exporter = self.exporter(session)
        with self.assertRaisesRegex(RuntimeError, r'getIconAnnotation\(Missing\)') as raised:
            exporter.ask_omc('getIconAnnotation', 'Missing', parsed=False)
        self.assertIs(raised.exception.__cause__, error)


if __name__ == '__main__':
    unittest.main()
