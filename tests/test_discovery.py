import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'inspect_project_state.py'
spec = importlib.util.spec_from_file_location('inventory', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_state(root, count=1):
    projects = {f'p{i}': {'id': f'p{i}', 'name': f'Project {i}', 'rootPaths': [str(root)]} for i in range(count)}
    return {module.PROJECTS: projects, module.ORDER: list(projects), module.ASSIGNMENTS: {}, module.MEMBERSHIPS: {}, module.MAPPING: {}, 'not_project_data': 'secret-sentinel'}


def make_db(path, valid=True, count=1):
    connection = sqlite3.connect(path)
    if valid:
        connection.executescript('CREATE TABLE projects(id TEXT, name TEXT); CREATE TABLE project_roots(project_id TEXT, path TEXT); CREATE TABLE threads(id TEXT, project_id TEXT);')
        connection.executemany('INSERT INTO projects VALUES (?, ?)', [(f's{i}', f'Project {i}') for i in range(count)])
    else:
        connection.execute('CREATE TABLE unknown_schema(value TEXT)')
    connection.commit()
    connection.close()


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def state(self, name='.codex-global-state.json', count=1):
        path = self.base / name
        path.write_text(json.dumps(make_state(self.base, count)), encoding='utf-8')
        return path

    def run_cli(self, *args, env=None):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), *args], capture_output=True, encoding='utf-8', env=env)
        return result.returncode, json.loads(result.stdout)

    def test_home_priority(self):
        explicit, env_home, user_home = (self.base / name for name in ('explicit', 'environment', 'user'))
        env = {'CODEX_HOME': str(env_home)}
        self.assertEqual(module.environment_info(str(explicit), env, user_home)['codex_home'], str(explicit))
        self.assertEqual(module.environment_info(None, env, user_home)['codex_home'], str(env_home))
        fallback = module.environment_info(None, {}, user_home)
        self.assertEqual(fallback['codex_home'], str(user_home / '.codex'))
        self.assertEqual(fallback['home_source'], 'user_home/.codex')

    def test_environment_discovery(self):
        self.state(count=7)
        env = dict(os.environ, CODEX_HOME=str(self.base))
        code, report = self.run_cli(env=env)
        self.assertEqual(code, 0)
        self.assertEqual(report['environment']['home_source'], 'CODEX_HOME')
        self.assertEqual(report['states'][0]['candidates'][0]['project_count'], 7)

    def test_explicit_home_overrides_environment(self):
        self.state(count=2)
        env = dict(os.environ, CODEX_HOME=str(self.base / 'wrong'))
        code, report = self.run_cli('--codex-home', str(self.base), env=env)
        self.assertEqual(code, 0)
        self.assertEqual(report['environment']['home_source'], '--codex-home')

    def test_project_counts(self):
        for count in (0, 1, 7, 101):
            path = self.state(count=count)
            before = path.read_bytes()
            result = module.inspect_state(path)
            self.assertEqual(result['candidates'][0]['project_count'], count)
            self.assertEqual(result['candidates'][0]['status'], 'compatible')
            self.assertEqual(before, path.read_bytes())

    def test_multiple_databases_schema_checked(self):
        self.state()
        make_db(self.base / 'state_5.sqlite', count=2)
        make_db(self.base / 'state_100.sqlite', count=3)
        code, report = self.run_cli('--codex-home', str(self.base))
        self.assertEqual(code, 0)
        self.assertEqual(len(report['databases']), 2)
        self.assertEqual(sorted(db['project_count'] for db in report['databases']), [2, 3])
        self.assertNotIn('database', report)
        make_db(self.base / 'state_999.sqlite', valid=False)
        code, report = self.run_cli('--codex-home', str(self.base))
        self.assertEqual(code, 2)
        self.assertEqual(len(report['databases']), 3)
        self.assertIsNone(next(db for db in report['databases'] if db['path'].endswith('state_999.sqlite'))['project_count'])

    def test_bounded_nonrecursive_discovery(self):
        self.state()
        self.state('.codex-global-state.json.bak-a')
        self.state('.codex-global-state.json.bak-b')
        (self.base / '.codex-global-state.json.empty').write_bytes(b'')
        (self.base / 'auth.json').write_text('secret-sentinel', encoding='utf-8')
        nested = self.base / 'nested'
        nested.mkdir()
        (nested / '.codex-global-state.json').write_text('{}', encoding='utf-8')
        code, report = self.run_cli('--codex-home', str(self.base), '--max-states', '2')
        discovery = report['discovery']
        self.assertEqual(code, 2)
        self.assertTrue(discovery['truncated'])
        self.assertEqual(len(discovery['state_files']), 2)
        self.assertEqual(len(discovery['state_files_omitted']), 1)
        self.assertEqual(len(discovery['empty_files_skipped']), 1)
        self.assertNotIn('secret-sentinel', json.dumps(report))
        self.assertNotIn(str(nested), json.dumps(report))

    def test_foreign_paths_are_unknown(self):
        win = module.root_info(r'C:\Users\Someone\Work', system='Linux')
        posix = module.root_info('/Users/someone/work', system='Windows')
        for result in (win, posix):
            self.assertIsNone(result['exists'])
            self.assertEqual(result['check_status'], 'foreign_path_not_checked')
            self.assertNotIn('issue', result)
        self.assertTrue(module.root_info(str(self.base))['exists'])

    def test_missing_home_does_not_create_data(self):
        missing = self.base / 'missing'
        code, report = self.run_cli('--codex-home', str(missing))
        self.assertEqual(code, 2)
        self.assertEqual(report['discovery']['status'], 'unverified')
        self.assertFalse(missing.exists())

    def test_original_interface_unchanged_and_read_only(self):
        state = self.state(count=2)
        backup = self.state('.codex-global-state.json.bak', count=3)
        db = self.base / 'custom.sqlite'
        make_db(db, count=4)
        original = {path: path.read_bytes() for path in (state, backup, db)}
        code, report = self.run_cli('--state', str(state), '--state', str(backup), '--db', str(db))
        self.assertEqual(code, 0)
        self.assertEqual(report['database']['project_count'], 4)
        self.assertEqual(len(report['states']), 2)
        self.assertEqual(report['discovery']['mode'], 'explicit_files')
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    def test_unknown_state_schema_not_success(self):
        unknown = self.base / 'unknown.json'
        unknown.write_text('{"new_layout": []}', encoding='utf-8')
        code, report = self.run_cli('--state', str(unknown))
        self.assertEqual(code, 2)
        self.assertEqual(report['states'][0]['status'], 'unverified')

    def test_missing_database_not_created(self):
        absent = self.base / 'absent.sqlite'
        code, report = self.run_cli('--db', str(absent))
        self.assertEqual(code, 2)
        self.assertIsNone(report['database']['project_count'])
        self.assertFalse(absent.exists())

    def test_discovery_never_reads_auth_file(self):
        state = self.state()
        auth = self.base / 'auth.json'
        auth.write_text('PRIVATE-AUTH-SENTINEL', encoding='utf-8')
        original_reader = Path.read_bytes
        paths_read = []

        def guarded_read(path):
            self.assertNotEqual(path, auth, 'Discovery must not read auth.json')
            paths_read.append(path)
            return original_reader(path)

        with mock.patch.object(Path, 'read_bytes', guarded_read):
            inventory = module.discover(module.environment_info(str(self.base)))
            reports = [module.inspect_state(path) for path in inventory['state_files']]
        self.assertEqual(paths_read, [state])
        self.assertNotIn('PRIVATE-AUTH-SENTINEL', json.dumps(reports))


if __name__ == '__main__':
    unittest.main(verbosity=2)
