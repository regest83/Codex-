import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'project_snapshot.py'
spec = importlib.util.spec_from_file_location('snapshot_tool', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fields(count):
    projects = {f'p{i}': {'id': f'p{i}', 'name': f'Project {i}', 'rootPaths': [f'C:/Users/A/Projects/P{i}']} for i in range(count)}
    return {mod.PROJECTS: projects, mod.ORDER: list(projects), mod.ASSIGNMENTS: {f't{i}': {'projectId': f'p{i}', 'projectKind': 'local'} for i in range(count)}, mod.MEMBERSHIPS: {f't{i}': 'local' for i in range(count)}, mod.MAPPING: {'local:C:/Users/A/.codex': {f'p{i}': f's{i}' for i in range(count)}}}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.src = self.root / 'source.json'
        self.dst = self.root / 'target.json'
        self.snap = self.root / 'snapshot.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), *args], capture_output=True, encoding='utf-8')

    def prepare(self, n=2, current=None, owner='target'):
        source = fields(n)
        source['auth'] = 'SECRET-SENTINEL'
        source[mod.ACCOUNT] = {'owner': 'source-owner-never-exported', 'inactive': {}}
        self.write(self.src, source)
        exported = mod.snapshot(self.src)
        self.write(self.snap, exported)
        state = copy.deepcopy(current or fields(0))
        state[mod.ACCOUNT] = {'owner': owner, 'inactive': {'target': {'atoms': {'preserve': True}, 'globals': copy.deepcopy(current or fields(0))}, 'other': {'atoms': {'keep': True}, 'globals': {}}}}
        state['projectless-thread-ids'] = ['running-new-chat']
        self.write(self.dst, state)
        return exported, state

    def test_export_has_only_project_metadata(self):
        exported, state = self.prepare()
        text = json.dumps(exported)
        self.assertNotIn('SECRET-SENTINEL', text)
        self.assertNotIn('source-owner-never-exported', text)
        self.assertEqual(set(exported['globals']), set(mod.KEYS))

    def test_preserves_five_new_projects(self):
        self.prepare(2, fields(7))
        baseline = self.dst.read_bytes()
        result = mod.plan(self.snap, self.dst, 'target')
        self.assertEqual(result['status'], 'no_change')
        self.assertEqual(len(result['desired_globals'][mod.PROJECTS]), 7)
        self.assertEqual(self.dst.read_bytes(), baseline)

    def test_arbitrary_hundred_projects(self):
        self.prepare(100)
        result = mod.plan(self.snap, self.dst, 'target')
        self.assertEqual(result['status'], 'review_required')
        self.assertEqual(len(result['desired_globals'][mod.PROJECTS]), 100)
        self.assertFalse(result['executable'])

    def test_empty_not_a_recovery_source(self):
        exported, _ = self.prepare(0)
        self.assertEqual(exported['project_count'], 0)
        with self.assertRaisesRegex(ValueError, 'empty snapshot'):
            mod.plan(self.snap, self.dst, 'target')

    def test_changed_project_and_assignment_conflicts(self):
        target = fields(2)
        target[mod.PROJECTS]['p0']['name'] = 'Edited current name'
        target[mod.ASSIGNMENTS]['t1']['projectId'] = 'p0'
        self.prepare(2, target)
        result = mod.plan(self.snap, self.dst, 'target')
        self.assertEqual(result['status'], 'conflicts')
        self.assertIsNone(result['desired_globals'])
        self.assertEqual({c['field'] for c in result['conflicts']}, {mod.PROJECTS, mod.ASSIGNMENTS})

    def test_order_conflict(self):
        target = fields(2)
        target[mod.ORDER].reverse()
        self.prepare(2, target)
        result = mod.plan(self.snap, self.dst, 'target')
        self.assertEqual(result['status'], 'conflicts')
        self.assertIn(mod.ORDER, {c['field'] for c in result['conflicts']})

    def test_null_and_different_owner_only_target_inactive(self):
        for owner in (None, 'other'):
            self.prepare(2, owner=owner)
            result = mod.plan(self.snap, self.dst, 'target')
            self.assertEqual(result['target_scopes'], ['inactive_target'])
        with self.assertRaisesRegex(ValueError, 'not present'):
            mod.plan(self.snap, self.dst, 'made-up-owner')

    def test_explicit_inactive_source(self):
        self.write(self.src, {mod.ACCOUNT: {'owner': 'current', 'inactive': {'old': {'atoms': {'secret': 1}, 'globals': fields(3)}}}})
        exported = mod.snapshot(self.src, 'old')
        self.assertEqual(exported['project_count'], 3)
        self.assertNotIn('secret', json.dumps(exported))
        with self.assertRaises(ValueError):
            mod.snapshot(self.src, 'missing')

    def test_longest_boundary_path_map_and_host_key(self):
        self.prepare()
        result = mod.plan(self.snap, self.dst, 'target', ['C:/Users/A=/Users/B', 'C:/Users/A/Projects=/Volumes/Projects'])
        desired = result['desired_globals']
        self.assertEqual(desired[mod.PROJECTS]['p0']['rootPaths'], ['/Volumes/Projects/P0'])
        self.assertIn('local:/Users/B/.codex', desired[mod.MAPPING])
        self.assertEqual(desired[mod.PROJECTS]['p0']['name'], 'Project 0')
        maps = mod.parse_maps(['C:/Users/A=/Users/B'])
        self.assertEqual(mod.remap_path('C:/Users/Alice/data', maps), 'C:/Users/Alice/data')
        self.assertEqual(mod.remap_path('C:/Users/A/Foo', maps), '/Users/B/Foo')
        with self.assertRaises(ValueError):
            mod.parse_maps(['C:/Users/A=/Users/B', 'c:/users/a=/Users/C'])

    def test_schema_and_output_protection(self):
        self.prepare()
        before = self.src.read_bytes()
        with self.assertRaises(FileExistsError):
            mod.write_new(self.src, {'overwrite': True})
        self.assertEqual(self.src.read_bytes(), before)
        value = fields(1)
        value[mod.PROJECTS]['p0']['new_schema_secret'] = 'unsafe'
        self.write(self.src, value)
        with self.assertRaises(ValueError):
            mod.snapshot(self.src)
        self.write(self.dst, {'unknown-version': {}})
        with self.assertRaises(ValueError):
            mod.plan(self.snap, self.dst, 'target')

    def test_unc_slashes_and_parent_mapping(self):
        maps = mod.parse_maps(['//Server/Share/work=D:/Recovered'])
        self.assertTrue(mod.windows_path('//Server/Share/work'))
        self.assertEqual(mod.remap_path(r'\\Server\Share\work\MixedCase', maps), r'D:\Recovered\MixedCase')
        self.assertEqual(mod.remap_path('//server/share/work/MixedCase', maps), r'D:\Recovered\MixedCase')
        self.assertEqual(mod.remap_path('//server/share/worker/file', maps), '//server/share/worker/file')
        for entry in ('C:/work/sub/..=D:/dest', 'C:/work=D:/dest/sub/..', '/work/sub/..=/dest'):
            with self.assertRaisesRegex(ValueError, 'Parent traversal'):
                mod.parse_maps([entry])
        maps = mod.parse_maps(['C:/work=D:/dest'])
        self.assertEqual(mod.remap_path('C:/work/sub/../a', maps), r'D:\dest\a')

    def test_posix_parent_segments_respect_mapping_boundary(self):
        maps = mod.parse_maps(['/work=/new/base'])
        self.assertEqual(mod.remap_path('/work/../outside', maps), '/work/../outside')
        self.assertEqual(mod.remap_path('/work/sub/../a', maps), '/new/base/a')

    def test_compatible_order_is_stable(self):
        conflicts = []
        result = mod.merge_orders(['p2', 'new'], ['p1', 'p2'], conflicts)
        self.assertEqual(result, ['p1', 'p2', 'new'])
        self.assertEqual(conflicts, [])

    def test_identical_metadata_without_order_is_no_change(self):
        _, state = self.prepare(2, fields(2))
        source = json.loads(self.src.read_text(encoding='utf-8'))
        source.pop(mod.ORDER)
        self.write(self.src, source)
        self.write(self.snap, mod.snapshot(self.src))
        state.pop(mod.ORDER)
        state[mod.ACCOUNT]['inactive']['target']['globals'].pop(mod.ORDER)
        self.write(self.dst, state)
        original = self.dst.read_bytes()
        result = mod.plan(self.snap, self.dst, 'target')
        self.assertEqual(result['status'], 'no_change')
        self.assertEqual(result['diff'], [])
        self.assertEqual(result['desired_globals'][mod.ORDER], [])
        self.assertEqual(self.dst.read_bytes(), original)

    def test_cli_paths_with_spaces_and_non_ascii(self):
        folder = self.root / 'fixture folder 测试'
        folder.mkdir()
        self.src = folder / 'source state.json'
        self.dst = folder / 'target state.json'
        self.snap = folder / 'prepared snapshot.json'
        self.prepare(3)
        originals = {path: path.read_bytes() for path in (self.src, self.dst)}
        output_snapshot = folder / 'exported snapshot.json'
        exported = self.run_cli('snapshot', '--state', str(self.src), '--out', str(output_snapshot))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertEqual(json.loads(exported.stdout)['status'], 'snapshot_created')
        output_plan = folder / 'recovery plan.json'
        planned = self.run_cli('plan', '--snapshot', str(output_snapshot), '--state', str(self.dst), '--target-owner', 'target', '--out', str(output_plan))
        self.assertEqual(planned.returncode, 0, planned.stderr)
        result = json.loads(output_plan.read_text(encoding='utf-8'))
        self.assertEqual(result['summary']['proposed_projects'], 3)
        self.assertFalse(result['executable'])
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_cli_conflicts_return_exit_two_without_mutation(self):
        target = fields(2)
        target[mod.PROJECTS]['p0']['name'] = 'Current user edit'
        self.prepare(2, target)
        original = self.dst.read_bytes()
        output = self.root / 'conflict plan.json'
        completed = self.run_cli('plan', '--snapshot', str(self.snap), '--state', str(self.dst), '--target-owner', 'target', '--out', str(output))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['status'], 'conflicts')
        result = json.loads(output.read_text(encoding='utf-8'))
        self.assertIsNone(result['desired_globals'])
        self.assertFalse(result['executable'])
        self.assertEqual(self.dst.read_bytes(), original)

    def test_cli_refuses_existing_output_even_live_state(self):
        self.prepare(2)
        originals = {path: path.read_bytes() for path in (self.src, self.dst, self.snap)}
        snapshot = self.run_cli('snapshot', '--state', str(self.src), '--out', str(self.snap))
        self.assertEqual(snapshot.returncode, 2)
        self.assertEqual(json.loads(snapshot.stderr)['status'], 'not_completed')
        plan = self.run_cli('plan', '--snapshot', str(self.snap), '--state', str(self.dst), '--target-owner', 'target', '--out', str(self.dst))
        self.assertEqual(plan.returncode, 2)
        self.assertEqual(json.loads(plan.stderr)['status'], 'not_completed')
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
