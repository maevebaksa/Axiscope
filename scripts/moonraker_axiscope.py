#!/usr/bin/env python3
"""Find and replace Axiscope's update-manager block in Moonraker includes."""
import argparse
import glob
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

HEADER = re.compile(r'^\s*\[([^]]+)\]\s*(?:[#;].*)?$')
TARGET = 'update_manager axiscope'

def config_tree(root):
    files = []; visited = set()
    def visit(filename, stack):
        filename = Path(filename).absolute(); real = filename.resolve()
        if real in stack:
            raise ValueError('Recursive Moonraker include: %s' % filename)
        if real in visited: return
        visited.add(real)
        text = filename.read_text()
        files.append((filename, text))
        for line in text.splitlines():
            match = HEADER.match(line)
            if not match: continue
            section = match.group(1).strip()
            if section.lower().startswith('include '):
                pattern = os.path.join(str(filename.parent), section[8:].strip())
                matches = sorted(glob.glob(pattern))
                if not matches and not glob.has_magic(pattern):
                    raise ValueError('Missing Moonraker include: %s' % pattern)
                for child in matches: visit(child, stack + [real])
    visit(root, [])
    return files

def sections(text):
    lines = text.splitlines(True); starts = []
    for index, line in enumerate(lines):
        match = HEADER.match(line.rstrip('\r\n'))
        if match: starts.append((index, match.group(1).strip().lower()))
    return [(start, starts[i + 1][0] if i + 1 < len(starts) else len(lines), name)
            for i, (start, name) in enumerate(starts)]

def expanded_path(value):
    # Config values are data, never shell expressions.
    value = value.strip()
    if '\n' in value or '\r' in value or not value:
        raise ValueError('Invalid installation path')
    return str(Path(os.path.expandvars(os.path.expanduser(value))).absolute().resolve())

def locate(root, default, override=None):
    files = config_tree(root)
    if override: return expanded_path(override)
    paths = set()
    for filename, text in files:
        lines = text.splitlines(True)
        for start, end, name in sections(text):
            if name == TARGET:
                for line in lines[start + 1:end]:
                    match = re.match(r'^\s*path\s*[:=]\s*(.*?)\s*(?:[#;].*)?$', line, re.I)
                    if match: paths.add(expanded_path(match.group(1)))
    if len(paths) > 1:
        raise ValueError('Multiple Axiscope paths found; specify --install-dir')
    if paths:
        path = next(iter(paths))
        if Path(path).exists() or not Path(default).exists(): return path
    return expanded_path(default)

def manager_block(install_dir, origin, branch):
    return ('[update_manager axiscope]\n'
            'type: git_repo\n'
            'channel: dev\n'
            'path: %s\n'
            'origin: %s\n'
            'primary_branch: %s\n'
            'virtualenv: %s/axiscope-env\n'
            'requirements: requirements.txt\n'
            'is_system_service: True\n'
            'managed_services: axiscope klipper\n\n') % (install_dir, origin, branch, install_dir)

def update_plan(root, install_dir, origin, branch):
    files = config_tree(root); block = manager_block(install_dir, origin, branch)
    found = False; edits = []
    for filename, text in files:
        lines = text.splitlines(True); out = []; cursor = 0
        for start, end, name in sections(text):
            if name != TARGET: continue
            out.extend(lines[cursor:start])
            if not found:
                out.append(block); found = True
            # Remove duplicate Axiscope sections, preserving other sections.
            cursor = end
        out.extend(lines[cursor:])
        new = ''.join(out)
        if new != text: edits.append((filename.resolve(), text, new))
    if not found:
        filename, text = files[0]
        edits.append((filename.resolve(), text, text.rstrip() + '\n\n' + block))
    return edits

def atomic_write(path, text):
    fd, name = tempfile.mkstemp(prefix='.axiscope-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        shutil.copymode(path, name)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def apply(edits):
    modified = []; backups = []
    try:
        for path, old, new in edits:
            if path.read_text() != old:
                raise ValueError('Moonraker configuration changed during upgrade: %s' % path)
            backup = Path(str(path) + '.axiscope-backup-%s-%d' % (time.strftime('%Y%m%d-%H%M%S'), os.getpid()))
            if backup.exists(): raise ValueError('Backup already exists: %s' % backup)
            shutil.copy2(path, backup); backups.append(backup)
            atomic_write(path, new); modified.append((path, old))
    except Exception:
        for path, old in reversed(modified): atomic_write(path, old)
        raise
    return backups

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['locate', 'update', 'check', 'restore'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--install-dir')
    parser.add_argument('--manifest')
    parser.add_argument('--default-dir', default=str(Path.home() / 'axiscope'))
    parser.add_argument('--origin', default='https://github.com/maevebaksa/Axiscope.git')
    parser.add_argument('--branch', default='main')
    args = parser.parse_args()
    if args.action == 'restore':
        if not args.manifest: raise ValueError('Restore requires --manifest')
        edits = json.loads(Path(args.manifest).read_text())
        for item in reversed(edits):
            path = Path(item['path']); current = path.read_text()
            if current == item['old']: continue
            if current != item['new']:
                raise ValueError('Cannot restore externally changed config: %s' % path)
            atomic_write(path, item['old'])
        print('Previous Moonraker Axiscope configuration restored')
        return
    install = locate(args.config, args.default_dir, args.install_dir)
    if args.action == 'locate': print(install); return
    edits = update_plan(args.config, install, args.origin, args.branch)
    if args.action == 'check':
        print('Installation: %s' % install)
        for path, old, new in edits: print('Replace Axiscope update-manager block in: %s' % path)
        if not edits: print('Axiscope update-manager block is already current')
    else:
        if args.manifest:
            path = Path(args.manifest)
            with path.open('w') as stream:
                json.dump([{'path': str(p), 'old': old, 'new': new} for p, old, new in edits], stream)
            path.chmod(0o600)
        for backup in apply(edits): print('Moonraker backup: %s' % backup)
        print('Axiscope update-manager block now tracks %s (%s)' % (args.origin, args.branch))

if __name__ == '__main__': main()
