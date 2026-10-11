"""Run with AXISCOPE_TOOLCHANGER=/path/to/ktce-or-ktc/klipper/extras.
Optional KLIPPER_CONFIGFILE=/path/to/klippy/configfile.py tests the real saver.
No printer, motors or camera are used.
"""
import configparser
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = Path(os.environ['AXISCOPE_TOOLCHANGER'])
pkg = types.ModuleType('axiscope_test'); pkg.__path__ = [str(ROOT / 'klippy/extras')]
sys.modules[pkg.__name__] = pkg
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
load('axiscope_test.tools_calibrate', BACKEND / 'tools_calibrate.py')
BackendToolchanger = load('axiscope_test.toolchanger', BACKEND / 'toolchanger.py')
mod = load('axiscope_test.axiscope', ROOT / 'klippy/extras/axiscope.py')
BackendTool = load('axiscope_test.tool', BACKEND / 'tool.py').Tool

class Command:
    def __init__(self, **args): self.args = args; self.messages = []
    def get_int(self, name, default=None, **limits): return int(self.args.get(name, default))
    def get_float(self, name, default=None, **limits):
        value = self.args.get(name, default)
        return float(value) if value is not None else None
    def get(self, name, default=None): return self.args.get(name, default)
    def error(self, msg): return RuntimeError(msg)
    def respond_info(self, msg): self.messages.append(msg)

class Head:
    def __init__(self): self.pos = [100., 100., 30., 0.]; self.moves = []; self.homed = 'xyz'
    def get_position(self): return list(self.pos)
    def wait_moves(self): pass
    def get_last_move_time(self): return 0.
    def get_kinematics(self): return self
    def get_status(self, time):
        return {'homed_axes': self.homed, 'axis_minimum': [0, 0, 0], 'axis_maximum': [400, 400, 400]}
    def manual_move(self, pos, speed):
        self.moves.append((list(pos), speed))
        for i, v in enumerate(pos):
            if v is not None: self.pos[i] = v

class Tool(BackendTool):
    def __init__(self, n):
        self.tool_number = n; self.name = 'tool physical_%d' % n
        self.gcode_x_offset, self.gcode_y_offset, self.gcode_z_offset = ([.5, -.2, .1] if n == 0 else [4., 3., .8])

class Probe:
    def __init__(self, printer):
        self.printer = printer; self.calls = []; self.triggered = False
        self.mcu_probe = [self, self, self]
    def query_endstop(self, time): return self.triggered
    def run_probe(self, direction, cmd, **kw):
        self.calls.append((direction, kw))
        th = self.printer.head; n = self.printer.tc.active_tool.tool_number
        center = [200 + n * .3, 210 - n * .4, 5 + n * .2]
        if direction == 'z-': th.pos[2] = center[2]
        else:
            i = 'xy'.index(direction[0]); sign = 1 if direction[1] == '+' else -1
            th.pos[i] = center[i] - sign * 2.
        return th.get_position()[:3]

class Gcode:
    def __init__(self, printer): self.printer = printer; self.scripts = []; self.fail_save = False; self.restarted = False; self.commands = {}
    def run_script_from_command(self, script):
        self.scripts.append(script)
        if script.startswith('SELECT_TOOL'):
            n = int(script.split('T=')[1].split()[0])
            previous = self.printer.tc.active_tool
            active = self.printer.tc.lookup_tool(n)
            if 'RESTORE_AXIS=Z' in script and previous is not None:
                self.printer.head.pos[2] += active.gcode_z_offset - previous.gcode_z_offset
            self.printer.tc.active_tool = active
        if script == 'SAVE_CONFIG':
            if self.fail_save: raise RuntimeError('injected save failure')
            self.printer.config.autosave.cmd_SAVE_CONFIG(Command())
    def register_command(self, name, cb, **kw): self.commands[name] = cb
    def request_restart(self, reason): self.restarted = True

class Printer:
    def __init__(self, root):
        self.root = root; self.head = Head(); self.tools = [Tool(0), Tool(2), Tool(1)]
        self.tc = types.SimpleNamespace(tool_numbers=[0, 2, 1], active_tool=self.tools[0], lookup_tool=lambda n: next((t for t in self.tools if t.tool_number == n), None))
        self.gcode = Gcode(self); self.config = None
        self.print_state = 'standby'; self.mesh_profile = ''
    def lookup_object(self, name, default=None):
        return {'toolhead': self.head, 'gcode': self.gcode, 'configfile': self.config,
                'print_stats': types.SimpleNamespace(get_status=lambda t: {'state': self.print_state}),
                'bed_mesh': types.SimpleNamespace(get_status=lambda t: {'profile_name': self.mesh_profile})}.get(name, default)
    def get_reactor(self): return types.SimpleNamespace(monotonic=lambda: 1.)
    def get_start_args(self): return {'config_file': str(self.root / 'printer.cfg')}
    command_error = RuntimeError

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.root.joinpath('printer.cfg').write_text('[include tools/*.cfg]\n[axiscope]\nsafe_z: 30\n')
        self.root.joinpath('tools').mkdir()
        self.include = self.root / 'tools' / 'all.cfg'
        self.include.write_text('[tool physical_0]\ngcode_x_offset: 0.5\ngcode_y_offset: -0.2\ngcode_z_offset: 0.1\n'
            '[tool physical_1]\ngcode_x_offset: 4\ngcode_y_offset: 3\ngcode_z_offset: 0.8\n'
            '[tool physical_2]\ngcode_x_offset: 4\ngcode_y_offset: 3\ngcode_z_offset: 0.8\n'
            '[gcode_macro EXAMPLE]\ngcode:\n  G1 X20\n')
        self.printer = Printer(self.root)
        if os.environ.get('KLIPPER_CONFIGFILE'):
            cf = load('real_klipper_configfile', Path(os.environ['KLIPPER_CONFIGFILE']))
            self.printer.config = cf.PrinterConfig(self.printer)
            self.printer.config.autosave.load_main_config()
        else:
            autosave = types.SimpleNamespace(fileconfig=configparser.RawConfigParser(), status_save_pending={}, save_config_pending=False,
                cmd_SAVE_CONFIG=lambda cmd: self.printer.gcode.request_restart('restart'))
            def set_value(section, option, value):
                if not autosave.fileconfig.has_section(section): autosave.fileconfig.add_section(section)
                autosave.fileconfig.set(section, option, value); autosave.save_config_pending = True
            self.printer.config = types.SimpleNamespace(autosave=autosave, set=set_value)
        a = self.a = mod.Axiscope.__new__(mod.Axiscope)
        a.printer = self.printer; a.gcode = self.printer.gcode; a.toolchanger = self.printer.tc
        a.gcode_move = types.SimpleNamespace(reset_last_position=lambda: None)
        a.probe_multi_axis = Probe(self.printer)
        a.x_pos, a.y_pos, a.z_pos = 200., 210., 9.
        a.safe_z = 30.; a.spread = 7.; a.lift_z = 1.; a.lower_z = 1.; a.final_lift_z = 6.
        a.travel_speed = 20.; a.lift_speed = 4.; a.move_speed = 20.; a.z_move_speed = 4.
        a.max_z_travel = 10.; a.max_offset = 5.
        a.camera_position = [100., 110., 10.]; a.xy_results = {}; a.xy_state = 'idle'
        a.confirmed_tools = []; a.check_tool = None; a.xy_include_z = False; a.xy_save = True
        a.probe_results = {}; a.has_cfg_data = False
        a.cmd_AXISCOPE_START_GCODE = lambda cmd: None
        a.cmd_AXISCOPE_FINISH_GCODE = lambda cmd: None
        a.cmd_AXISCOPE_BEFORE_PICKUP_GCODE = lambda cmd: None
        a.cmd_AXISCOPE_AFTER_PICKUP_GCODE = lambda cmd: None
    def tearDown(self): self.tmp.cleanup()
    def calibrate(self, **args): self.a.cmd_CALIBRATE_ALL_XY_OFFSETS(Command(**args))
    def test_full_measure_check_save(self):
        self.calibrate()
        self.assertEqual(self.a.xy_state, 'awaiting_confirmation')
        self.assertFalse(self.printer.gcode.restarted)
        self.assertEqual(self.a.check_tool, 0)
        r = self.a.xy_results['2']['offsets']
        for actual, expected in zip(r, [1.1, -1., .8]): self.assertAlmostEqual(actual, expected)
        self.assertEqual(len(self.a.probe_multi_axis.calls), 30)
        self.assertTrue(all(c[1]['max_distance'] == 10. for c in self.a.probe_multi_axis.calls if c[0] == 'z-'))
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.assertEqual(self.a.check_tool, 2)
        for actual, expected in zip(self.printer.head.pos[:3], [100.6, 109.2, 10.7]): self.assertAlmostEqual(actual, expected)
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.assertEqual(self.a.check_tool, 1)
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.assertTrue(self.printer.gcode.restarted)
        self.assertEqual(self.a.xy_state, 'saved')
        text = self.include.read_text()
        self.assertIn('gcode_z_offset: 0.8', text)
        self.assertIn('gcode:\n  G1 X20', text)
        self.assertIn('# Axiscope migrated to SAVE_CONFIG: gcode_x_offset: 4', text)
        self.assertIn('gcode_x_offset: 0.5', text)
        self.assertEqual(len(list(self.include.parent.glob('*.axiscope-backup-*'))), 1)
        self.assertEqual(self.printer.tools[1].gcode_x_offset, 4.)  # Runtime untouched until restart.
        if os.environ.get('KLIPPER_CONFIGFILE'):
            cf = load('reload_configfile', Path(os.environ['KLIPPER_CONFIGFILE']))
            reloaded = cf.PrinterConfig(self.printer).autosave.load_main_config()[0]
            self.assertAlmostEqual(reloaded.getfloat('tool physical_2', 'gcode_x_offset'), 1.1)
            self.assertAlmostEqual(reloaded.getfloat('tool physical_2', 'gcode_z_offset'), .8)
            self.assertAlmostEqual(reloaded.getfloat('axiscope', 'camera_y_pos'), 110.)
    def test_include_z(self):
        self.calibrate(SAVE=0, CAMERA_CHECK=0, INCLUDE_Z=1)
        self.assertAlmostEqual(self.a.xy_results['2']['offsets'][2], .5)
    def test_save_failure_rolls_back(self):
        self.calibrate(SAVE=0, CAMERA_CHECK=0)
        original = self.include.read_text()
        self.printer.gcode.fail_save = True
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            self.a.cmd_AXISCOPE_SAVE_CALIBRATION(Command())
        self.assertEqual(self.include.read_text(), original)
        self.assertEqual(self.a.xy_state, 'measured')
        self.assertEqual(self.printer.config.autosave.fileconfig.sections(), [])
        self.assertFalse(self.printer.config.autosave.save_config_pending)
    def test_bad_probe_stops_without_save(self):
        self.a.probe_multi_axis.triggered = True
        with self.assertRaisesRegex(RuntimeError, 'already triggered'): self.calibrate()
        self.assertEqual(self.a.xy_state, 'failed')
        self.assertEqual(self.a.probe_multi_axis.calls, [])
        self.assertNotIn('SAVE_CONFIG', self.printer.gcode.scripts)
    def test_bad_bounds_before_moving(self):
        self.a.y_pos = 398.
        with self.assertRaisesRegex(RuntimeError, 'limits'): self.calibrate()
        self.assertEqual(self.printer.head.moves, [])
    def test_unhomed_and_printing_and_mesh(self):
        self.printer.head.homed = 'xy'
        with self.assertRaisesRegex(RuntimeError, 'home'): self.calibrate()
        self.printer.head.homed = 'xyz'; self.printer.print_state = 'paused'
        with self.assertRaisesRegex(RuntimeError, 'print'): self.calibrate()
        self.printer.print_state = 'standby'; self.printer.mesh_profile = 'default'
        with self.assertRaisesRegex(RuntimeError, 'BED_MESH_CLEAR'): self.calibrate()
    def test_confirmation_requires_correct_tool_and_position(self):
        self.calibrate(SAVE=0)
        self.printer.tc.active_tool = self.printer.tools[1]
        with self.assertRaisesRegex(RuntimeError, 'verification tool'): self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.printer.tc.active_tool = self.printer.tools[0]; self.printer.head.pos[0] += 1
        with self.assertRaisesRegex(RuntimeError, 'Nozzle moved'): self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.assertEqual(self.a.confirmed_tools, [])
    def test_status_restores_pending_check_without_mutating_previous_snapshot(self):
        self.calibrate(SAVE=0)
        before = self.a.get_status(0.)
        self.assertTrue(before['confirmation_ready'])
        self.assertEqual(before['check_tool'], 0)
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        after = self.a.get_status(1.)
        self.assertEqual(after['confirmed_tools'], [0])
        self.assertEqual(before['confirmed_tools'], [])
        self.assertTrue(after['confirmation_ready'])
        self.printer.head.pos[0] += 0.4
        moved = self.a.get_status(2.)
        self.assertFalse(moved['confirmation_ready'])
        self.assertEqual(moved['confirmation_blocked'], 'position_changed')
        self.a.cmd_AXISCOPE_MOVE_TO_CAMERA(Command(T=2))
        self.assertTrue(self.a.get_status(3.)['confirmation_ready'])

    def test_camera_trim_changes_only_pending_tool_xy_until_final_save(self):
        self.calibrate()
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        before = self.a.get_status(0.)
        original = self.include.read_text()
        old_z = self.a.xy_results['2']['offsets'][2]
        self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=.4, Y=-.05))
        result = self.a.xy_results['2']
        self.assertAlmostEqual(result['offsets'][0], 1.5)
        self.assertAlmostEqual(result['offsets'][1], -1.05)
        self.assertEqual(result['offsets'][2], old_z)
        self.assertAlmostEqual(self.printer.head.pos[0], 101.)
        self.assertAlmostEqual(self.printer.head.pos[1], 109.15)
        self.assertEqual(self.include.read_text(), original)
        self.assertEqual(self.a.confirmed_tools, [0])
        self.assertEqual(self.printer.tools[1].gcode_x_offset, 4.)
        self.assertAlmostEqual(before['xy_results']['2']['offsets'][0], 1.1)
        self.a.cmd_AXISCOPE_MOVE_TO_CAMERA(Command(T=2))
        self.assertAlmostEqual(self.printer.head.pos[0], 101.)
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.assertEqual(self.printer.config.autosave.fileconfig.get('tool physical_2', 'gcode_x_offset'), '1.500000')
        self.assertTrue(self.printer.gcode.restarted)

    def test_camera_trim_requires_current_unconfirmed_nonreference_tool(self):
        self.calibrate(SAVE=0)
        with self.assertRaisesRegex(RuntimeError, 'T0 is the reference'):
            self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=.05))
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        self.printer.head.pos[0] += .4
        with self.assertRaisesRegex(RuntimeError, 'Nozzle moved'):
            self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=.05))
        self.a.cmd_AXISCOPE_MOVE_TO_CAMERA(Command(T=2))
        self.printer.tc.active_tool = self.printer.tools[0]
        with self.assertRaisesRegex(RuntimeError, 'verification tool'):
            self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=.05))
        self.printer.tc.active_tool = self.printer.tools[1]
        self.a.confirmed_tools.append(2)
        with self.assertRaisesRegex(RuntimeError, 'already confirmed'):
            self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=.05))

    def test_camera_trim_bounds_and_failed_move_leave_results_unchanged(self):
        self.calibrate(SAVE=0)
        self.a.cmd_AXISCOPE_CONFIRM_TOOL(Command())
        original = list(self.a.xy_results['2']['offsets'])
        moves = len(self.printer.head.moves)
        for value in [float('nan'), float('inf'), 1.1, -1.1]:
            with self.assertRaisesRegex(RuntimeError, 'at most 1 mm'):
                self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=value))
        self.a.max_offset = .8
        with self.assertRaisesRegex(RuntimeError, 'max_offset'):
            self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=.4))
        self.assertEqual(len(self.printer.head.moves), moves)
        self.a.max_offset = 5.
        self.a._move_safely = lambda pos, cmd: (_ for _ in ()).throw(RuntimeError('move failed'))
        with self.assertRaisesRegex(RuntimeError, 'move failed'):
            self.a.cmd_AXISCOPE_ADJUST_CAMERA(Command(X=.4))
        self.assertEqual(self.a.xy_results['2']['offsets'], original)

    def test_stale_offsets_cannot_save(self):
        self.calibrate(SAVE=0, CAMERA_CHECK=0)
        self.printer.tools[1].gcode_x_offset = 2.
        with self.assertRaisesRegex(RuntimeError, 'offsets changed'): self.a.cmd_AXISCOPE_SAVE_CALIBRATION(Command())
    def test_abort_leaves_config_untouched(self):
        before = self.include.read_text(); self.calibrate(SAVE=0)
        self.a.cmd_AXISCOPE_ABORT_CALIBRATION(Command())
        self.assertEqual(self.a.xy_results, {}); self.assertEqual(self.include.read_text(), before)
    def test_nonfinite_and_large_measurement(self):
        self.a.max_offset = .1
        with self.assertRaisesRegex(RuntimeError, 'max_offset'): self.calibrate()
        self.assertNotIn('SAVE_CONFIG', self.printer.gcode.scripts)
    def test_camera_capture_requires_t0(self):
        self.printer.tc.active_tool = self.printer.tools[1]
        with self.assertRaisesRegex(RuntimeError, 'physical T0'): self.a.cmd_AXISCOPE_SET_CAMERA_POSITION(Command(CURRENT=1))
        self.printer.tc.active_tool = self.printer.tools[0]
        self.a.cmd_AXISCOPE_SET_CAMERA_POSITION(Command(CURRENT=1))
        self.assertEqual(self.a.camera_position, [100., 100., 30.])
    def test_toolchange_returns_z_using_real_backend_before_calibration_xy(self):
        # Exercise Toolchanger.select_tool/_save_state/_restore_state_and_transform,
        # not the simple tool-selection stub used by the probe geometry tests.
        if not hasattr(BackendToolchanger, 'ToolGcodeTransform'):
            self.skipTest('This docking regression exercises KTCE ToolGcodeTransform')
        head = self.printer.head
        tc = BackendToolchanger.Toolchanger.__new__(BackendToolchanger.Toolchanger)
        tc.active_tool = self.printer.tools[0]
        tc.status = BackendToolchanger.STATUS_READY
        tc.next_change_id = 0; tc.current_change_id = -1
        tc.has_detection = False; tc.verify_tool_pickup = False
        tc.params = {}; tc.last_change_gcode_offset = None
        tc.gcode_transform = BackendToolchanger.ToolGcodeTransform()
        tc.gcode_transform.tool = tc.active_tool
        tc.gcode_transform.next_transform = head
        tc.ensure_homed = lambda cmd: None
        head.move = head.manual_move
        head.pos = [200., 210., 9., 0.]
        origin = [10., 20., 3., 0.]

        class GcodeMove:
            def __init__(inner): inner.reset_last_position()
            def reset_last_position(inner): inner.last = tc.gcode_transform.get_position()
            def get_status(inner):
                return {'gcode_position': [inner.last[i] - origin[i] for i in range(4)],
                        'homing_origin': origin}
            def cmd_G1(inner, cmd):
                pos = list(inner.last)
                for i, axis in enumerate('XYZ'):
                    if axis in cmd.args: pos[i] = float(cmd.args[axis]) + origin[i]
                tc.gcode_transform.move(pos, 20.)
                inner.reset_last_position()

        class Gcode:
            def __init__(inner): inner.scripts = []; inner.saved = []
            def create_gcode_command(inner, name, text, params): return Command(**params)
            def respond_info(inner, message): pass
            def error(inner, message): return RuntimeError(message)
            def run_script_from_command(inner, script):
                inner.scripts.append(script)
                if script.startswith('SELECT_TOOL'):
                    number = int(script.split('T=')[1].split()[0])
                    restore = script.split('RESTORE_AXIS=')[1]
                    tc.select_tool(Command(), self.printer.tc.lookup_tool(number), restore)
                elif script.startswith('SAVE_GCODE_STATE'):
                    inner.saved.append(tc.gcode_move.get_status())
                elif script.startswith('RESTORE_GCODE_STATE'):
                    tc.gcode_move.reset_last_position()

        def configure(tool): tc.active_tool = tool
        def run_macro(name, template, context):
            if name == 'tool.dropoff_gcode':
                head.manual_move([50., -5., 300.], 20.)
            if name == 'tool.pickup_gcode':
                head.manual_move([70., -5., 300.], 20.)
                # KTCE pickup macro exits the dock in Y before returning Z.
                head.manual_move([None, 120., None], 20.)
                if 'Z' in context['restore_position']:
                    head.manual_move([None, None, context['restore_position']['Z']], 20.)
        tc._configure_toolhead_for_tool = configure
        tc.run_gcode = run_macro
        for tool in self.printer.tools:
            tool.before_change_gcode = tool.dropoff_gcode = tool.pickup_gcode = tool.after_change_gcode = None
        tc.gcode_move = GcodeMove(); tc.gcode = Gcode()
        self.a.toolchanger = tc; self.a.gcode_move = tc.gcode_move; self.a.gcode = tc.gcode
        self.a._select_tool(1, Command())
        self.assertIn('SELECT_TOOL T=1 RESTORE_AXIS=Z', tc.gcode.scripts)
        # Snapshot must use the new lift height, despite the initial cached Z9.
        self.assertAlmostEqual(tc.gcode.saved[0]['gcode_position'][2], 30. - .1 - 3.)
        self.assertAlmostEqual(head.pos[2], 30.7)
        self.assertEqual(head.pos[1], 120.)
        head.moves = []
        self.a._move_safely([200., 210., 9.], Command())
        self.assertEqual(head.moves[0][0], [None, None, 30.7])
        self.assertEqual(head.moves[1][0], [200., 210., None])
        self.assertEqual(head.moves[2][0], [None, None, 9.])

    def test_incomplete_z_return_aborts_before_any_calibration_xy(self):
        def select_without_z_return(script):
            self.printer.tc.active_tool = self.printer.tools[2]
            self.printer.head.pos = [70., 120., 300., 0.]
        self.a.gcode.run_script_from_command = select_without_z_return
        with self.assertRaisesRegex(RuntimeError, 'did not return to toolchange start Z'):
            self.a._select_tool(1, Command())
        self.assertTrue(all(move[0][0] is None and move[0][1] is None
                            for move in self.printer.head.moves))
    def test_camera_height_error_reports_actual_heights_before_motion(self):
        self.a.safe_z = 20.
        with self.assertRaisesRegex(RuntimeError, 'Camera Z 30.000 exceeds safe_z 20.000'):
            self.a.cmd_AXISCOPE_SET_CAMERA_POSITION(Command(CURRENT=1))
        self.a.camera_position[2] = 30.
        before = list(self.printer.head.pos)
        with self.assertRaisesRegex(RuntimeError, 'T0 camera Z 30.000 exceeds safe_z 20.000'):
            self.calibrate()
        self.assertEqual(self.printer.head.pos, before)
        self.assertEqual(self.a.probe_multi_axis.calls, [])
    def test_camera_move_checks_tool_height_before_tool_selection(self):
        self.a.camera_position[2] = 30.
        before = list(self.printer.head.pos)
        active = self.printer.tc.active_tool
        with self.assertRaisesRegex(RuntimeError, 'camera Z 30.700 exceeds safe_z 30.000'):
            self.a.cmd_AXISCOPE_MOVE_TO_CAMERA(Command(T=2))
        self.assertIs(self.printer.tc.active_tool, active)
        self.assertEqual(self.printer.head.pos, before)
    def test_symlink_and_nested_include_preserved(self):
        target = self.root / 'source.cfg'; target.write_text(self.include.read_text())
        self.include.unlink(); self.include.symlink_to(target)
        edits = mod.included_option_edits(self.root / 'printer.cfg', {'tool physical_2': {'gcode_x_offset': '1'}})
        self.assertEqual(len(edits), 1)
        mod.atomic_replace_text(edits[0][0], edits[0][2])
        self.assertTrue(self.include.is_symlink())
        self.assertIn('# Axiscope migrated', target.read_text())
    def test_missing_and_recursive_includes_fail_without_edits(self):
        self.root.joinpath('printer.cfg').write_text('[include absent.cfg]\n')
        with self.assertRaisesRegex(ValueError, 'Missing'): mod.included_option_edits(self.root / 'printer.cfg', {})
        self.root.joinpath('printer.cfg').write_text('[include printer.cfg]\n')
        with self.assertRaisesRegex(ValueError, 'Recursive'): mod.included_option_edits(self.root / 'printer.cfg', {})

if __name__ == '__main__': unittest.main()
