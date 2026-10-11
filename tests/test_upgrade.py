import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('moonraker_axiscope', ROOT / 'scripts/moonraker_axiscope.py')
manager = importlib.util.module_from_spec(spec); spec.loader.exec_module(manager)

class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.config = self.root / 'moonraker.conf'
        self.install = self.root / 'existing-axiscope'; self.install.mkdir()
        self.origin = 'https://github.com/maevebaksa/Axiscope.git'
    def tearDown(self): self.tmp.cleanup()
    def plan(self): return manager.update_plan(self.config, str(self.install), self.origin, 'main')
    def test_replace_existing_block_and_preserve_other_services(self):
        self.config.write_text('[server]\nhost: 0.0.0.0\n\n[update_manager axiscope]\n'
            'type: git_repo\norigin: https://github.com/nic335/Axiscope.git\npath: %s\n'
            'managed_services:\n  axiscope\n\n[update_manager klipper]\npath: ~/klipper\n' % self.install)
        before = self.config.read_text(); edits = self.plan(); backups = manager.apply(edits)
        after = self.config.read_text()
        self.assertIn('[server]\nhost: 0.0.0.0', after)
        self.assertIn('[update_manager klipper]\npath: ~/klipper', after)
        self.assertNotIn('nic335', after); self.assertIn(self.origin, after)
        self.assertEqual(after.count('[update_manager axiscope]'), 1)
        self.assertEqual(backups[0].read_text(), before)
        self.assertEqual(self.plan(), [])  # Idempotent.
        self.assertEqual(manager.locate(self.config, '/not/the/path'), str(self.install))
    def test_existing_block_in_nested_include_is_replaced_in_place(self):
        nested = self.root / 'conf'; nested.mkdir()
        child = nested / 'services.conf'; child.write_text('[update_manager axiscope]\npath: %s\norigin: upstream\n' % self.install)
        self.config.write_text('[include conf/*.conf]\n[server]\nhost: localhost\n')
        edits = self.plan()
        self.assertEqual(edits[0][0], child)
        self.assertEqual(manager.locate(self.config, '/unused'), str(self.install))
        manager.apply(edits)
        self.assertNotIn('[update_manager axiscope]', self.config.read_text())
        self.assertEqual(self.plan(), [])
    def test_duplicate_blocks_become_one(self):
        child = self.root / 'more.conf'; child.write_text('[update_manager Axiscope]\norigin: duplicate\n[authorization]\nforce_logins: True\n')
        self.config.write_text('[include more.conf]\n[update_manager axiscope]\norigin: old\n')
        manager.apply(self.plan())
        alltext = self.config.read_text() + child.read_text()
        self.assertEqual(alltext.lower().count('[update_manager axiscope]'), 1)
        self.assertIn('force_logins: True', alltext)
    def test_no_existing_block_appends_once(self):
        self.config.write_text('[server]\nhost: localhost\n')
        manager.apply(self.plan())
        self.assertIn('[update_manager axiscope]', self.config.read_text())
        self.assertEqual(self.plan(), [])
    def test_multiple_paths_require_override(self):
        self.config.write_text('[update_manager axiscope]\npath: /first\n[update_manager axiscope]\npath: /second\n')
        with self.assertRaisesRegex(ValueError, 'Multiple'): manager.locate(self.config, '/default')
        self.assertEqual(manager.locate(self.config, '/default', str(self.install)), str(self.install))
    def test_symlink_config_keeps_identity(self):
        target = self.root / 'actual.conf'; target.write_text('[server]\nhost: localhost\n')
        self.config.symlink_to(target)
        manager.apply(self.plan())
        self.assertTrue(self.config.is_symlink())
        self.assertIn('[update_manager axiscope]', target.read_text())
    def test_recursive_and_missing_includes(self):
        self.config.write_text('[include moonraker.conf]\n')
        with self.assertRaisesRegex(ValueError, 'Recursive'): self.plan()
        self.config.write_text('[include absent.conf]\n')
        with self.assertRaisesRegex(ValueError, 'Missing'): self.plan()
    def test_concurrent_edit_is_not_overwritten(self):
        self.config.write_text('[server]\nhost: localhost\n'); edits = self.plan()
        self.config.write_text('[server]\nhost: changed\n')
        with self.assertRaisesRegex(ValueError, 'changed'): manager.apply(edits)
        self.assertIn('host: changed', self.config.read_text())
    def test_manifest_restore(self):
        original = '[server]\nhost: localhost\n'; self.config.write_text(original)
        manifest = self.root / 'restore.json'
        subprocess.run(['python3', str(ROOT / 'scripts/moonraker_axiscope.py'), 'update', '--config', str(self.config),
                        '--install-dir', str(self.install), '--manifest', str(manifest)], check=True, capture_output=True)
        subprocess.run(['python3', str(ROOT / 'scripts/moonraker_axiscope.py'), 'restore', '--config', str(self.config),
                        '--manifest', str(manifest)], check=True, capture_output=True)
        self.assertEqual(self.config.read_text(), original)

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.source = self.root / 'fork'; self.source.mkdir()
        for relative in ('upgrade.sh', '.gitignore', 'app.py', 'requirements.txt', 'scripts/moonraker_axiscope.py', 'klippy/extras/axiscope.py'):
            destination = self.source / relative; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        for command in (['git', 'init', '-b', 'main', str(self.source)],
                        ['git', '-C', str(self.source), 'add', '.'],
                        ['git', '-C', str(self.source), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'Test fork']):
            subprocess.run(command, check=True, capture_output=True)
        self.install = self.root / 'custom-axiscope'; self.install.mkdir()
        (self.install / 'app.py').write_text('old app')
        (self.install / 'user-note.txt').write_text('preserve me in backup')
        environment = self.install / 'axiscope-env/bin'; environment.mkdir(parents=True)
        python = environment / 'python3'
        python.write_text('#!/bin/bash\nif [[ "${AXISCOPE_TEST_FAIL_PIP:-0}" == 1 && "$1" == "-m" ]]; then exit 31; fi\nexit 0\n')
        python.chmod(0o755)
        self.klipper = self.root / 'klipper'; extras = self.klipper / 'klippy/extras'; extras.mkdir(parents=True)
        (extras / 'tools_calibrate.py').write_text('installed backend')
        (extras / 'axiscope.py').write_text('old klipper module')
        configdir = self.root / 'printer_data/config'; configdir.mkdir(parents=True)
        self.config = configdir / 'moonraker.conf'
        self.config.write_text('[server]\nhost: localhost\n[update_manager axiscope]\npath: %s\norigin: upstream\n' % self.install)
        self.backup = self.root / 'backups'
    def tearDown(self): self.tmp.cleanup()
    def run_upgrade(self, *args, env=None):
        return subprocess.run(['bash', str(ROOT / 'upgrade.sh'), '--repo-url', str(self.source), '--moonraker-conf', str(self.config),
            '--klipper-dir', str(self.klipper), '--backup-root', str(self.backup), '--no-services', *args],
            text=True, capture_output=True, env=env)
    def test_detect_swap_backup_manager_and_clean_git_checkout(self):
        result = self.run_upgrade(); self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.install / '.git').is_dir())
        self.assertEqual((self.klipper / 'klippy/extras/axiscope.py').resolve(), self.install / 'klippy/extras/axiscope.py')
        old = list(self.backup.glob('*/previous-install/user-note.txt'))
        self.assertEqual(old[0].read_text(), 'preserve me in backup')
        self.assertNotIn('origin: upstream', self.config.read_text())
        self.assertIn('managed_services: axiscope klipper', self.config.read_text())
        status = subprocess.check_output(['git', '-C', str(self.install), 'status', '--porcelain'], text=True)
        self.assertEqual(status, '')
        second = self.run_upgrade(); self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.config.read_text().count('[update_manager axiscope]'), 1)
    def test_dependency_failure_restores_existing_install_and_link(self):
        before = self.config.read_text(); env = os.environ.copy(); env['AXISCOPE_TEST_FAIL_PIP'] = '1'
        result = self.run_upgrade(env=env); self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.install / 'app.py').read_text(), 'old app')
        self.assertEqual((self.klipper / 'klippy/extras/axiscope.py').read_text(), 'old klipper module')
        self.assertEqual(self.config.read_text(), before)
    def test_dry_run_does_not_swap_or_write_config(self):
        before = self.config.read_text(); result = self.run_upgrade('--dry-run')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.install / 'app.py').read_text(), 'old app')
        self.assertEqual(self.config.read_text(), before)
        self.assertFalse(self.backup.exists())
    def test_failed_clone_does_not_touch_install(self):
        shutil.rmtree(self.source)
        result = self.run_upgrade(); self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.install / 'app.py').read_text(), 'old app')
        self.assertFalse(self.backup.exists())

if __name__ == '__main__': unittest.main()
