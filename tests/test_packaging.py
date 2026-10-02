"""Release archives must contain the runtime skill and exclude local state."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_demo import FOLDERS, TOP, source_files


class PackagingTests(unittest.TestCase):
    def test_release_contains_skill_and_all_roles(self):
        root = Path(__file__).resolve().parents[1]
        names = {p.relative_to(root).as_posix() for p in source_files(root)}
        self.assertIn('bundle/.claude/skills/text2hydraulic/SKILL.md', names)
        for role in ['design', 'review', 'simulation']:
            self.assertIn(f'bundle/.claude/agents/hydraulic-{role}.md', names)
        self.assertIn('docs/demo.md', names)

    def test_local_state_and_external_libraries_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in TOP:
                (root / name).write_text('source', encoding='utf-8')
            for name in FOLDERS:
                (root / name).mkdir(parents=True, exist_ok=True)
            excluded = ['config.json', 'runs/result.json', 'bundle/OpenHydraulics/package.mo',
                        'bundle/.claude/settings.local.json', 'docs/.env', 'docs/.env.json',
                        'tests/__pycache__/cache.pyc', 'docs/private.key']
            for name in excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('local', encoding='utf-8')
            names = {p.relative_to(root).as_posix() for p in source_files(root)}
            self.assertTrue(names.isdisjoint(excluded))


if __name__ == '__main__':
    unittest.main()
