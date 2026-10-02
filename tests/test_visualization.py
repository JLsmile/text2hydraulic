import sys
import json
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visualization as viz
import server


class VisualizationTests(unittest.TestCase):
    def test_source_topology_and_placement(self):
        source = '''model Test "description with connect(fake.x, hidden.y); // text"
        OpenHydraulics.Components.MotorsPumps.ConstantDisplacementPump pump(Dconst=6e-6)
          annotation(Placement(transformation(extent={{-10,-10},{10,10}},origin={40,20})));
        OpenHydraulics.Components.Volumes.CircuitTank tank;
        parameter Real pressure=1e7;
        equation
        // connect(fake.x, hidden.y);
        connect(pump.portT, tank.port_a) annotation(Line(points={{40,10},{0,0}}));
        end Test;'''
        data = viz.parse_model(source)
        self.assertEqual([n['name'] for n in data['nodes']], ['pump', 'tank'])
        self.assertEqual(data['nodes'][0]['position'], [40, -20])
        self.assertEqual(len(data['edges']), 1)
        self.assertEqual(data['edges'][0]['source'], 'pump.portT')
        self.assertEqual(data['edges'][0]['kind'], 'hydraulic')
        self.assertEqual(data['layout'], 'automatic')

    def test_unknown_endpoints_not_fabricated(self):
        data = viz.parse_model('model X Some.Type a; equation connect(a.port, missing.port); end X;')
        self.assertEqual(data['edges'], [])
        self.assertTrue(data['warnings'])

    def test_preview_of_incomplete_file(self):
        self.assertEqual(viz.parse_model('model X Some.Type pump(')['nodes'], [])
        self.assertTrue(viz.parse_model('package P end P;')['warnings'])

    def test_csv_selection_duplicate_time_nonfinite_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'response.csv').write_text('time,"time",worktable.v,cylinder.port_a.p,note,nrows=4\n0,0,0,100,hello\n1,1,0.1,200,world\n2,2,nan,300,text\n3,3,0.2,inf,text\n')
            data = viz.signal_data(root, '', 'cylinder.port_a.p', server.artifact_path, server.artifacts)
            self.assertEqual(data['variables'], ['worktable.v', 'cylinder.port_a.p'])
            self.assertEqual(data['samples'], 3)
            self.assertEqual(data['maximum'], 300)
            self.assertEqual(data['points'][-1], [2,300])
            data = viz.signal_data(root, '', 'worktable.v', server.artifact_path, server.artifacts)
            self.assertEqual(data['samples'], 3)
            self.assertEqual(data['last'], .2)

    def test_model_and_csv_file_selection_confinement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'one.mo').write_text('model One Some.Type a; end One;')
            (root/'two.mo').write_text('model Two Some.Type b; end Two;')
            (root/'memory.json').write_text(json.dumps({'files': {'mo_file': 'two.mo'}}))
            self.assertEqual(viz.model_data(root,'',server.artifact_path,server.artifacts)['name'],'Two')
            self.assertEqual(viz.model_data(root,'one.mo',server.artifact_path,server.artifacts)['name'],'One')
            for bad in ['../secret.mo','.claude/skill.mo','missing.mo']:
                with self.assertRaises(ValueError):
                    viz.model_data(root,bad,server.artifact_path,server.artifacts)

    def test_decimation_retains_last_sample_and_full_extrema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'data.csv').write_text('time,force\n'+''.join(f'{i},{100000 if i==9 else i}\n' for i in range(20001)))
            data=viz.signal_data(root,'','force',server.artifact_path,server.artifacts)
            self.assertLessEqual(len(data['points']),4001)
            self.assertEqual(data['maximum'],100000)
            self.assertEqual(data['samples'],20001)
            self.assertEqual(data['points'][-1],[20000,20000])

    def test_sparse_signal_after_initial_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'sparse.csv').write_text('time,note,late_signal\n'+''.join(f'{i},text,\n' for i in range(300))+'300,text,42\n')
            data=viz.signal_data(root,'','',server.artifact_path,server.artifacts)
            self.assertEqual(data['variables'],['late_signal'])
            self.assertEqual(data['points'],[[300,42]])

    def test_missing_csv_and_invalid_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(viz.signal_data(root,'','',server.artifact_path,server.artifacts)['points'],[])
            (root/'invalid.csv').write_text('time,pressure\n0,nan\n1,inf\n')
            self.assertEqual(viz.signal_data(root,'','',server.artifact_path,server.artifacts)['variables'],[])

if __name__ == '__main__':
    unittest.main()
