#!/usr/bin/env python3
"""Read-only inventory of Codex desktop project state and optional SQLite records."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import sqlite3
import sys


ACCOUNT = 'electron-account-renderer-state-v1'
PROJECTS = 'local-projects'
ORDER = 'project-order'
ASSIGNMENTS = 'thread-project-assignments'
MEMBERSHIPS = 'thread-project-membership-host-ids'
MAPPING = 'app-server-project-id-by-legacy-project-id-by-host'
KEYS = (PROJECTS, ORDER, ASSIGNMENTS, MEMBERSHIPS, MAPPING)


def path_style(value):
    if PureWindowsPath(value).is_absolute():
        return 'windows'
    if PurePosixPath(value).is_absolute():
        return 'posix'
    return 'relative'


def root_info(value, system=None):
    result = {'path': value, 'exists': None, 'is_directory': None}
    if not isinstance(value, str) or not value:
        result['issue'] = 'Root path must be a nonempty string'
        return result
    style = path_style(value)
    result['path_style'] = style
    native_style = 'windows' if (system or platform.system()) == 'Windows' else 'posix'
    if style not in ('relative', native_style):
        result['check_status'] = 'foreign_path_not_checked'
        result['note'] = 'Path syntax belongs to another platform; existence is unknown, not missing'
        return result
    if style == 'relative':
        result['issue'] = 'Relative root path was not resolved or checked'
        return result
    path = Path(value)
    try:
        result.update(exists=path.exists(), is_directory=path.is_dir(), check_status='checked')
    except (OSError, ValueError) as error:
        result['issue'] = str(error)
    return result


def inspect_candidate(values, source, current_owner=False):
    result = {'source': source, 'is_current_owner_snapshot': current_owner,
              'status': 'absent', 'present_keys': [], 'issues': [],
              'project_count': None, 'projects': [], 'order_count': None,
              'assignment_count': None, 'assignment_counts_by_project': {},
              'membership_host_count': None, 'legacy_mapping_count': None,
              'legacy_server_mappings': []}
    if not isinstance(values, dict):
        result.update(status='incompatible', issues=['Expected a globals object'])
        return result
    present = [key for key in KEYS if key in values]
    result['present_keys'] = present
    if not present:
        return result
    result['status'] = 'compatible' if len(present) == len(KEYS) else 'partial'
    issues = result['issues']
    expected = {PROJECTS: dict, ORDER: list, ASSIGNMENTS: dict,
                MEMBERSHIPS: dict, MAPPING: dict}
    valid = {}
    for key in present:
        if not isinstance(values[key], expected[key]):
            issues.append(key + ': unsupported value type')
        else:
            valid[key] = values[key]
    projects = valid.get(PROJECTS)
    if projects is not None:
        result['project_count'] = len(projects)
        for identity, item in projects.items():
            if not isinstance(item, dict):
                issues.append(PROJECTS + '[' + repr(identity) + ']: expected object')
                continue
            name = item.get('name')
            if not isinstance(name, str) or not name:
                issues.append(PROJECTS + '[' + repr(identity) + ']: missing name')
            if item.get('id') != identity:
                issues.append(PROJECTS + '[' + repr(identity) + ']: id does not match key')
            roots = item.get('rootPaths')
            if not isinstance(roots, list):
                issues.append(PROJECTS + '[' + repr(identity) + ']: rootPaths is not a list')
                roots = []
            root_results = [root_info(root) for root in roots]
            if any('issue' in root for root in root_results):
                issues.append(PROJECTS + '[' + repr(identity) + ']: some roots could not be checked')
            result['projects'].append({'legacy_id': identity, 'name': name,
                                       'roots': root_results})
    if ORDER in valid:
        order = valid[ORDER]
        result['order_count'] = len(order)
        if any(not isinstance(item, str) for item in order):
            issues.append(ORDER + ': non-string project IDs')
        elif len(set(order)) != len(order):
            issues.append(ORDER + ': duplicate project IDs')
        elif projects is not None and any(item not in projects for item in order):
            issues.append(ORDER + ': references projects absent from this candidate')
    if ASSIGNMENTS in valid:
        assignments = valid[ASSIGNMENTS]
        result['assignment_count'] = len(assignments)
        counts = Counter()
        for item in assignments.values():
            if not isinstance(item, dict) or not isinstance(item.get('projectId'), str):
                issues.append(ASSIGNMENTS + ': unsupported assignment entry')
                continue
            if item.get('projectKind') != 'local':
                issues.append(ASSIGNMENTS + ': contains a non-local or unknown projectKind')
                continue
            counts[item['projectId']] += 1
        result['assignment_counts_by_project'] = dict(counts)
        if projects is not None and any(identity not in projects for identity in counts):
            issues.append(ASSIGNMENTS + ': references projects absent from this candidate')
    if MEMBERSHIPS in valid:
        result['membership_host_count'] = len(valid[MEMBERSHIPS])
        if any(not isinstance(value, str) for value in valid[MEMBERSHIPS].values()):
            issues.append(MEMBERSHIPS + ': non-string host IDs')
    if MAPPING in valid:
        mappings = []
        for host, entries in valid[MAPPING].items():
            if not isinstance(entries, dict):
                issues.append(MAPPING + ': expected a per-host mapping object')
                continue
            for legacy_id, server_id in entries.items():
                if not isinstance(server_id, str) or not server_id:
                    issues.append(MAPPING + ': invalid server project ID')
                    continue
                mappings.append({'host_key': host, 'legacy_id': legacy_id,
                                 'server_id': server_id,
                                 'is_candidate_project': legacy_id in (projects or {})})
        result['legacy_mapping_count'] = len(mappings)
        result['legacy_server_mappings'] = mappings
    result['issues'] = list(dict.fromkeys(issues))
    if issues:
        result['status'] = 'incompatible'
    return result


def inspect_state(filename):
    path = Path(filename).resolve()
    result = {'path': str(path), 'status': 'unverified', 'candidates': []}
    try:
        data = path.read_bytes()
        state = json.loads(data.decode('utf-8-sig'))
        if not isinstance(state, dict):
            raise ValueError('Top-level JSON must be an object')
    except (OSError, ValueError) as error:
        result['error'] = str(error)
        return result
    result['sha256'] = hashlib.sha256(data).hexdigest()
    result['candidates'].append(inspect_candidate(state, '$'))
    account = state.get(ACCOUNT)
    result['account_state_present'] = ACCOUNT in state
    if ACCOUNT not in state:
        result['status'] = 'inspected' if result['candidates'][0]['present_keys'] else 'unverified'
        result['account_note'] = 'No account-scoped state found; do not infer an owner'
        if result['status'] == 'unverified':
            result['error'] = 'No supported project or account-state fields found'
        return result
    if not isinstance(account, dict) or not isinstance(account.get('inactive', {}), dict):
        result['error'] = 'Unsupported account-state or inactive-snapshot schema'
        return result
    owner = account.get('owner')
    if owner is not None and not isinstance(owner, str):
        result['error'] = 'Unsupported owner type'
        return result
    result['owner_is_set'] = bool(owner)
    inactive = account.get('inactive', {})
    result['inactive_snapshot_count'] = len(inactive)
    for identity, snapshot in inactive.items():
        source = '$[' + json.dumps(ACCOUNT) + '].inactive[' + json.dumps(identity) + '].globals'
        values = snapshot.get('globals') if isinstance(snapshot, dict) else None
        result['candidates'].append(inspect_candidate(values, source, identity == owner))
    result['status'] = 'inspected'
    return result


def inspect_database(filename):
    path = Path(filename).resolve()
    result = {'path': str(path), 'status': 'unverified', 'issues': [],
              'project_count': None, 'project_root_count': None,
              'assigned_thread_count': None, 'projects': [], 'project_roots': [],
              'assignment_counts_by_project': {}}
    if not path.is_file():
        result['issues'].append('Database file does not exist; no file was created')
        return result
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            requirements = {'projects': {'id', 'name'}, 'project_roots': {'project_id', 'path'},
                            'threads': {'id', 'project_id'}}
            supported = {}
            for table, required in requirements.items():
                columns = {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')} if table in tables else set()
                supported[table] = required <= columns
                if not supported[table]:
                    result['issues'].append(table + ': missing required columns ' + ', '.join(sorted(required - columns)))
            if supported['projects']:
                result['projects'] = [{'server_id': row[0], 'name': row[1]} for row in connection.execute('SELECT id, name FROM projects ORDER BY id')]
                result['project_count'] = len(result['projects'])
            if supported['project_roots']:
                result['project_roots'] = [{'server_id': row[0], **root_info(row[1])} for row in connection.execute('SELECT project_id, path FROM project_roots ORDER BY project_id, path')]
                result['project_root_count'] = len(result['project_roots'])
            if supported['threads']:
                result['assignment_counts_by_project'] = dict(connection.execute('SELECT project_id, count(*) FROM threads WHERE project_id IS NOT NULL GROUP BY project_id'))
                result['assigned_thread_count'] = sum(result['assignment_counts_by_project'].values())
            result['status'] = 'verified' if all(supported.values()) else 'partial'
            result['supported_tables'] = supported
    except sqlite3.Error as error:
        result['status'] = 'unverified'
        result['issues'].append(str(error))
    return result


def environment_info(explicit_home=None, environ=None, user_home=None):
    environ = os.environ if environ is None else environ
    if explicit_home is not None:
        value, source = explicit_home, '--codex-home'
    elif environ.get('CODEX_HOME'):
        value, source = environ['CODEX_HOME'], 'CODEX_HOME'
    else:
        value = str(Path(user_home or Path.home()) / '.codex')
        source = 'user_home/.codex'
    native_style = 'windows' if platform.system() == 'Windows' else 'posix'
    style = path_style(value)
    foreign = style not in ('relative', native_style)
    resolved = value if foreign else str(Path(value).expanduser().resolve())
    return {'os': platform.system(), 'python': sys.version.split()[0],
            'interpreter': sys.executable, 'codex_home': resolved,
            'home_source': source, 'home_path_is_foreign': foreign}


def discover(environment, max_states=20, max_databases=20):
    result = {'mode': 'directory_inventory', 'recursive': False, 'status': 'unverified',
              'state_files': [], 'database_files': [], 'empty_files_skipped': [],
              'state_files_omitted': [], 'database_files_omitted': [],
              'candidate_order': 'current JSON first, then filenames; not a recovery-source ranking'}
    if environment['home_path_is_foreign']:
        result['error'] = 'Selected home has foreign path syntax; provide the actual local data directory'
        return result
    home = Path(environment['codex_home'])
    try:
        if not home.is_dir():
            result['error'] = 'Selected data directory does not exist; no directory was created'
            return result
        states, databases = [], []
        for item in home.iterdir():
            is_state = item.name.startswith('.codex-global-state.json')
            is_database = item.name.startswith('state') and item.name.endswith('.sqlite')
            if not (is_state or is_database) or not item.is_file():
                continue
            if item.stat().st_size == 0:
                result['empty_files_skipped'].append(str(item))
                continue
            (states if is_state else databases).append(item)
        states.sort(key=lambda p: (p.name != '.codex-global-state.json', p.name))
        databases.sort(key=lambda p: p.name)
        result['state_files'] = [str(p) for p in states[:max_states]]
        result['database_files'] = [str(p) for p in databases[:max_databases]]
        result['state_files_omitted'] = [str(p) for p in states[max_states:]]
        result['database_files_omitted'] = [str(p) for p in databases[max_databases:]]
        result['truncated'] = bool(result['state_files_omitted'] or result['database_files_omitted'])
        result['status'] = 'partial' if result['truncated'] else ('discovered' if states or databases else 'unverified')
        if not states and not databases:
            result['error'] = 'No nonempty supported filename candidates found; this does not prove projects were deleted'
    except OSError as error:
        result['error'] = str(error)
    return result


def positive_limit(value):
    number = int(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError('Use a limit from 1 to 1000')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', action='append', default=[], help='State JSON or backup; repeatable')
    parser.add_argument('--db', help='Existing SQLite database; opened in mode=ro')
    parser.add_argument('--codex-home', help='Data directory for automatic discovery; overrides CODEX_HOME and the current user home')
    parser.add_argument('--max-states', type=positive_limit, default=20, help='Automatic JSON candidate limit (default: 20); omitted files are reported')
    parser.add_argument('--max-databases', type=positive_limit, default=20, help='Automatic database candidate limit (default: 20); every selected database is schema-checked')
    args = parser.parse_args()
    environment = environment_info(args.codex_home)
    result = {'mode': 'read_only', 'environment': environment}
    state_files, db_files = args.state, [args.db] if args.db else []
    if not state_files and not db_files:
        discovery = discover(environment, args.max_states, args.max_databases)
        result['discovery'] = discovery
        state_files, db_files = discovery['state_files'], discovery['database_files']
    else:
        result['discovery'] = {'mode': 'explicit_files', 'status': 'not_requested',
                               'note': 'Only explicit files were inspected; automatic candidates were not added'}
    result['states'] = [inspect_state(path) for path in state_files]
    result['databases'] = [inspect_database(path) for path in db_files]
    if len(result['databases']) == 1:
        result['database'] = result['databases'][0]
    for state in result['states']:
        for candidate in state['candidates']:
            for mapping in candidate['legacy_server_mappings']:
                checks = []
                for database in result['databases']:
                    if database['project_count'] is not None:
                        names = {item['server_id']: item['name'] for item in database['projects']}
                        checks.append({'database': database['path'], 'server_project_exists': mapping['server_id'] in names,
                                       'server_project_name': names.get(mapping['server_id'])})
                mapping['database_checks'] = checks
                if len(result['databases']) == 1 and len(checks) == 1:
                    mapping.update({key: checks[0][key] for key in ('server_project_exists', 'server_project_name')})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = any(state['status'] == 'unverified' or any(candidate['status'] == 'incompatible' for candidate in state['candidates']) for state in result['states'])
    failed = failed or any(database['status'] != 'verified' for database in result['databases'])
    failed = failed or result['discovery']['status'] in ('unverified', 'partial')
    return 2 if failed else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
