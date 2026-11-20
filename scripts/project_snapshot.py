#!/usr/bin/env python3
"""Export project-only snapshots and build review-only, non-destructive merge plans."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import ntpath
import posixpath
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys

ACCOUNT = 'electron-account-renderer-state-v1'
PROJECTS = 'local-projects'
ORDER = 'project-order'
ASSIGNMENTS = 'thread-project-assignments'
MEMBERSHIPS = 'thread-project-membership-host-ids'
MAPPING = 'app-server-project-id-by-legacy-project-id-by-host'
KEYS = (PROJECTS, ORDER, ASSIGNMENTS, MEMBERSHIPS, MAPPING)
FORMAT = 'codex-project-snapshot-v1'


def load(path):
    raw = Path(path).read_bytes()
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object: ' + str(path))
    return value, hashlib.sha256(raw).hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def project_fields(values, source=False):
    if not isinstance(values, dict):
        raise ValueError('Project globals must be an object')
    if source and PROJECTS not in values:
        raise ValueError('No recognized local-projects field; source schema is unverified')
    result = {key: copy.deepcopy(values.get(key, [] if key == ORDER else {})) for key in KEYS}
    for key in KEYS:
        if not isinstance(result[key], list if key == ORDER else dict):
            raise ValueError('Unsupported field type: ' + key)
    projects = result[PROJECTS]
    for identity, project in projects.items():
        allowed = {'id', 'name', 'rootPaths', 'createdAt', 'updatedAt'}
        if not isinstance(project, dict) or set(project) - allowed:
            raise ValueError('Unknown project record schema: ' + identity)
        if project.get('id') != identity or not isinstance(project.get('name'), str):
            raise ValueError('Project id/name mismatch: ' + identity)
        if not isinstance(project.get('rootPaths'), list) or any(not isinstance(p, str) or not (PureWindowsPath(p).is_absolute() or PurePosixPath(p).is_absolute()) for p in project['rootPaths']):
            raise ValueError('Project roots must be explicit absolute paths: ' + identity)
        for key in ('createdAt', 'updatedAt'):
            if key in project and not isinstance(project[key], (int, float)):
                raise ValueError('Unsupported project timestamp: ' + identity)
    order = result[ORDER]
    if any(not isinstance(p, str) or p not in projects for p in order) or len(order) != len(set(order)):
        raise ValueError('Invalid project-order references or duplicates')
    for identity, assignment in result[ASSIGNMENTS].items():
        if not isinstance(assignment, dict) or set(assignment) != {'projectId', 'projectKind'} or assignment.get('projectKind') != 'local' or assignment.get('projectId') not in projects:
            raise ValueError('Unsupported/dangling local assignment: ' + identity)
    if any(not isinstance(host, str) or not host for host in result[MEMBERSHIPS].values()):
        raise ValueError('Invalid membership host')
    for host, mappings in result[MAPPING].items():
        if not host or not isinstance(mappings, dict) or any(not isinstance(p, str) or not p for p in mappings.values()):
            raise ValueError('Unsupported legacy/server mapping schema')
    return result


def snapshot(state_path, source_owner=None):
    state, digest = load(state_path)
    if source_owner is None:
        values = state
    else:
        try:
            values = state[ACCOUNT]['inactive'][source_owner]['globals']
        except (KeyError, TypeError):
            raise ValueError('The explicitly selected inactive source does not exist') from None
    fields = project_fields(values, source=True)
    return {'format': FORMAT, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'source_scope': 'inactive' if source_owner is not None else 'top_level',
            'source_state_sha256': digest, 'project_count': len(fields[PROJECTS]),
            'assignment_count': len(fields[ASSIGNMENTS]), 'globals': fields,
            'scope': 'Project metadata only; no authentication, message bodies, project files or account owner included.'}


def windows_path(value):
    parsed = PureWindowsPath(value)
    return bool(re.match(r'^[A-Za-z]:[\\/]', value)) or (parsed.is_absolute() and parsed.drive.startswith('\\\\'))


def path_key(value):
    if windows_path(value):
        return 'windows', ntpath.normcase(ntpath.normpath(value)).replace('\\', '/')
    return 'posix', posixpath.normpath(value)


def parse_maps(entries):
    maps = {}
    for entry in entries:
        old, sep, new = entry.partition('=')
        if not sep or not old or not new or not (PureWindowsPath(old).is_absolute() or PurePosixPath(old).is_absolute()) or not (PureWindowsPath(new).is_absolute() or PurePosixPath(new).is_absolute()):
            raise ValueError('Each --path-map must be OLD=NEW with absolute paths')
        for value in (old, new):
            parts = PureWindowsPath(value).parts if windows_path(value) else PurePosixPath(value).parts
            if '..' in parts:
                raise ValueError('Parent traversal in --path-map is ambiguous; provide normalized absolute paths')
        key = path_key(old)
        if key in maps and path_key(maps[key][1]) != path_key(new):
            raise ValueError('Conflicting duplicate path-map source: ' + old)
        maps[key] = (old, new)
    return sorted(maps.values(), key=lambda item: len(path_key(item[0])[1]), reverse=True)


def remap_path(value, maps):
    style, normalized = path_key(value)
    for old, new in maps:
        old_style, old_key = path_key(old)
        if style != old_style:
            continue
        if normalized == old_key or normalized.startswith(old_key.rstrip('/') + '/'):
            original = ntpath.normpath(value) if style == 'windows' else posixpath.normpath(value)
            old_parts = PureWindowsPath(ntpath.normpath(old)).parts if style == 'windows' else PurePosixPath(posixpath.normpath(old)).parts
            parts = PureWindowsPath(original).parts if style == 'windows' else PurePosixPath(original).parts
            suffix = parts[len(old_parts):]
            cls = PureWindowsPath if windows_path(new) else PurePosixPath
            return str(cls(new).joinpath(*suffix))
    return value


def mapped_fields(fields, maps):
    result = copy.deepcopy(fields)
    changes = []
    for identity, project in result[PROJECTS].items():
        roots = [remap_path(root, maps) for root in project['rootPaths']]
        if roots != project['rootPaths']:
            changes.append({'field': PROJECTS + '.' + identity + '.rootPaths', 'before': project['rootPaths'], 'after': roots})
            project['rootPaths'] = roots
    mappings = {}
    for host_key, mapping in result[MAPPING].items():
        prefix, separator, path = host_key.partition(':')
        mapped_key = prefix + ':' + remap_path(path, maps) if separator and (PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()) else host_key
        if mapped_key != host_key:
            changes.append({'field': MAPPING, 'before': host_key, 'after': mapped_key})
        if mapped_key in mappings and mappings[mapped_key] != mapping:
            raise ValueError('Path mapping creates conflicting host keys: ' + mapped_key)
        mappings[mapped_key] = mapping
    result[MAPPING] = mappings
    return result, changes


def merge_orders(existing, incoming, conflicts):
    nodes = list(dict.fromkeys(existing + incoming))
    edges = {node: set() for node in nodes}
    for order in (existing, incoming):
        for left, right in zip(order, order[1:]):
            edges[left].add(right)
    result = []
    remaining = set(nodes)
    while remaining:
        blocked = {right for left in remaining for right in edges[left] if right in remaining}
        candidate = next((node for node in nodes if node in remaining and node not in blocked), None)
        if candidate is None:
            conflicts.append({'field': ORDER, 'reason': 'Incompatible relative order', 'existing': existing, 'incoming': incoming})
            return nodes
        result.append(candidate)
        remaining.remove(candidate)
    return result


def merge(existing, incoming, conflicts, scope):
    result = copy.deepcopy(existing)
    for key in (PROJECTS, ASSIGNMENTS, MEMBERSHIPS):
        for identity, value in incoming[key].items():
            if identity in result[key] and result[key][identity] != value:
                conflicts.append({'scope': scope, 'field': key, 'id': identity, 'reason': 'Existing and incoming values differ'})
            else:
                result[key][identity] = copy.deepcopy(value)
    for host, items in incoming[MAPPING].items():
        target = result[MAPPING].setdefault(host, {})
        for legacy, server in items.items():
            if legacy in target and target[legacy] != server:
                conflicts.append({'scope': scope, 'field': MAPPING, 'host_key': host, 'id': legacy, 'reason': 'Server ID differs'})
            else:
                target[legacy] = server
    result[ORDER] = merge_orders(existing[ORDER], incoming[ORDER], conflicts)
    return result


def plan(snapshot_path, state_path, target_owner, path_maps=()):
    if not target_owner:
        raise ValueError('An explicit nonempty --target-owner is required')
    saved, source_digest = load(snapshot_path)
    if saved.get('format') != FORMAT:
        raise ValueError('Unknown snapshot format')
    incoming = project_fields(saved.get('globals'), source=True)
    if not incoming[PROJECTS]:
        raise ValueError('An empty snapshot cannot be a recovery source')
    incoming, path_changes = mapped_fields(incoming, parse_maps(path_maps))
    state, state_digest = load(state_path)
    account = state.get(ACCOUNT)
    if not isinstance(account, dict) or not isinstance(account.get('inactive'), dict) or (account.get('owner') is not None and not isinstance(account.get('owner'), str)):
        raise ValueError('Unknown account-state schema; no plan generated')
    if target_owner not in account['inactive'] and account.get('owner') != target_owner:
        raise ValueError('Target owner is not present in the inspected state; do not invent an account partition')
    profile = account['inactive'].get(target_owner, {})
    if not isinstance(profile, dict):
        raise ValueError('Target profile is not an object')
    current = project_fields(profile.get('globals', {}))
    scopes = ['inactive_target']
    conflicts = []
    if account.get('owner') == target_owner:
        scopes.append('top_level')
        current = merge(current, project_fields(state), conflicts, 'top_level_vs_inactive')
    desired = merge(current, incoming, conflicts, 'snapshot_vs_target')
    diffs = []
    for scope in scopes:
        before = project_fields(state if scope == 'top_level' else profile.get('globals', {}))
        for key in KEYS:
            if before[key] != desired[key]:
                diffs.append({'scope': scope, 'field': key, 'before_count': len(before[key]), 'after_count': len(desired[key])})
    now = dt.datetime.now(dt.timezone.utc)
    return {'format': 'codex-project-plan-v1', 'created_at': now.isoformat(), 'expires_at': (now + dt.timedelta(hours=24)).isoformat(),
            'status': 'conflicts' if conflicts else ('no_change' if not diffs else 'review_required'),
            'executable': False, 'source_snapshot_sha256': source_digest, 'target_state_sha256': state_digest,
            'target_owner': target_owner, 'target_scopes': scopes, 'target_state_path': str(Path(state_path).resolve()),
            'summary': {'source_projects': len(incoming[PROJECTS]), 'existing_projects': len(current[PROJECTS]), 'proposed_projects': len(desired[PROJECTS]), 'proposed_assignments': len(desired[ASSIGNMENTS])},
            'path_changes': path_changes, 'diff': diffs, 'conflicts': conflicts,
            'desired_globals': None if conflicts else desired,
            'required_checks': ['This is a review-only metadata plan, never an executable patch.', 'Re-read the live file and regenerate on baseline, owner, schema or expiry changes.', 'Verify current app load/save behavior and closed state before any future writer.', 'Verify directory and thread existence plus legacy/server project mappings on this computer; path remapping does not migrate files, messages, IDs or login.', 'Preserve authentication, atoms, other profiles, unselected globals and archived state.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('snapshot', help='Export a new project-only snapshot')
    export.add_argument('--state', required=True)
    export.add_argument('--source-owner')
    export.add_argument('--out', required=True)
    review = commands.add_parser('plan', help='Create a review-only merge plan; never changes live state')
    review.add_argument('--snapshot', required=True)
    review.add_argument('--state', required=True)
    review.add_argument('--target-owner', required=True)
    review.add_argument('--path-map', action='append', default=[])
    review.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        value = snapshot(args.state, args.source_owner) if args.command == 'snapshot' else plan(args.snapshot, args.state, args.target_owner, args.path_map)
        write_new(args.out, value)
        print(json.dumps({'output': str(Path(args.out).resolve()), 'status': value.get('status', 'snapshot_created'), 'project_count': value.get('project_count'), 'summary': value.get('summary')}, ensure_ascii=False))
        return 2 if value.get('status') == 'conflicts' else 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({'status': 'not_completed', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
