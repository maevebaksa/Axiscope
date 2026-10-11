
import os
import ast
import math
import glob
import re
import shutil
import tempfile
import time
import copy
from . import tools_calibrate
from . import toolchanger

class Axiscope:
    def __init__(self, config):
        self.printer       = config.get_printer()
        self.gcode         = self.printer.lookup_object('gcode')
        self.gcode_move = self.printer.load_object(config, 'gcode_move')

        self.x_pos         = config.getfloat('zswitch_x_pos', None)
        self.y_pos         = config.getfloat('zswitch_y_pos', None)
        self.z_pos         = config.getfloat('zswitch_z_pos', None)
        self.lift_z        = config.getfloat('lift_z', 1, above=0.)
        self.move_speed    = config.getfloat('move_speed', 60, above=0.)
        self.z_move_speed  = config.getfloat('z_move_speed', 10, above=0.)
        self.samples       = config.getint('samples', 10, minval=1)
        self.travel_speed = config.getfloat('travel_speed', self.move_speed, above=0.)
        self.spread = config.getfloat('spread', 7., above=0.)
        self.lower_z = config.getfloat('lower_z', 1., above=0.)
        self.lift_speed = config.getfloat('lift_speed', 4., above=0.)
        self.final_lift_z = config.getfloat('final_lift_z', 6., above=0.)
        self.max_z_travel = config.getfloat('max_z_travel', 10., above=0.)
        self.safe_z = config.getfloat('safe_z', None)
        self.max_offset = config.getfloat('max_offset', 5., above=0.)
        self.camera_position = [config.getfloat('camera_%s_pos' % a, None)
                                for a in 'xyz']
        if any(v is not None for v in self.camera_position) and not all(
                v is not None for v in self.camera_position):
            raise config.error('Set all camera_x_pos, camera_y_pos and camera_z_pos')
        self.xy_results = {}
        self.xy_state = 'idle'
        self.confirmed_tools = []
        self.check_tool = None
        self.xy_save = True
        self.xy_include_z = False

        self.pin              = config.get('pin'             , None)
        self.config_file_path = config.get('config_file_path', None)
        
        # Load gcode_macro module for template support
        self.gcode_macro = self.printer.load_object(config, 'gcode_macro')
        
        # Custom gcode macros
        self.start_gcode = self.gcode_macro.load_template(config, 'start_gcode', '')
        self.before_pickup_gcode = self.gcode_macro.load_template(config, 'before_pickup_gcode', '')
        self.after_pickup_gcode = self.gcode_macro.load_template(config, 'after_pickup_gcode', '')
        self.finish_gcode = self.gcode_macro.load_template(config, 'finish_gcode', '')

        self.has_cfg_data     = False
        self.probe_results = {}

        # Check for tools_calibrate conflict
        if config.has_section('tools_calibrate'):
            raise config.error(
                "Cannot use [axiscope] when [tools_calibrate] is also configured. "
                "Both modules conflict with each other. "
                "Please use only one: either [axiscope] or [tools_calibrate]."
            )

        #setup endstop in query_endstops if pin is set
        if self.pin is not None:
            self.probe_multi_axis = tools_calibrate.PrinterProbeMultiAxis(
                config,
                tools_calibrate.ProbeEndstopWrapper(config, 'x'),
                tools_calibrate.ProbeEndstopWrapper(config, 'y'),
                tools_calibrate.ProbeEndstopWrapper(config, 'z')
            )
            query_endstops = self.printer.load_object(config, 'query_endstops')
            query_endstops.register_endstop(self.probe_multi_axis.mcu_probe[-1].mcu_endstop, "Axiscope")
        else:
            self.probe_multi_axis = None

        self.toolchanger = self.printer.load_object(config, 'toolchanger')

        self.printer.register_event_handler("klippy:connect", self.handle_connect)

        #register gcode commands
        self.gcode.register_command('MOVE_TO_ZSWITCH', self.cmd_MOVE_TO_ZSWITCH, desc=self.cmd_MOVE_TO_ZSWITCH_help)
        self.gcode.register_command('PROBE_ZSWITCH',   self.cmd_PROBE_ZSWITCH, desc=self.cmd_PROBE_ZSWITCH_help)
        self.gcode.register_command('CALIBRATE_ALL_Z_OFFSETS',   self.cmd_CALIBRATE_ALL_Z_OFFSETS, desc=self.cmd_CALIBRATE_ALL_Z_OFFSETS_help)

        self.gcode.register_command('AXISCOPE_START_GCODE', self.cmd_AXISCOPE_START_GCODE, desc="Execute the Axiscope start G-code macro")
        self.gcode.register_command('AXISCOPE_BEFORE_PICKUP_GCODE', self.cmd_AXISCOPE_BEFORE_PICKUP_GCODE, desc="Execute the Axiscope before pickup G-code macro")
        self.gcode.register_command('AXISCOPE_AFTER_PICKUP_GCODE', self.cmd_AXISCOPE_AFTER_PICKUP_GCODE, desc="Execute the Axiscope after pickup G-code macro")
        self.gcode.register_command('AXISCOPE_FINISH_GCODE', self.cmd_AXISCOPE_FINISH_GCODE, desc="Execute the Axiscope finish G-code macro")
        self.gcode.register_command('AXISCOPE_SAVE_TOOL_OFFSET',          self.cmd_AXISCOPE_SAVE_TOOL_OFFSET,          desc=self.cmd_AXISCOPE_SAVE_TOOL_OFFSET_help)
        self.gcode.register_command('AXISCOPE_SAVE_MULTIPLE_TOOL_OFFSETS', self.cmd_AXISCOPE_SAVE_MULTIPLE_TOOL_OFFSETS, desc=self.cmd_AXISCOPE_SAVE_MULTIPLE_TOOL_OFFSETS_help)
        self.gcode.register_command('AXISCOPE_SET_ENDSTOP_POSITION', self.cmd_AXISCOPE_SET_ENDSTOP_POSITION, desc=self.cmd_AXISCOPE_SET_ENDSTOP_POSITION_help)
        for name in ('CALIBRATE_ALL_XY_OFFSETS', 'AXISCOPE_SET_CAMERA_POSITION',
                     'AXISCOPE_MOVE_TO_CAMERA', 'AXISCOPE_CONFIRM_TOOL',
                     'AXISCOPE_SAVE_CALIBRATION', 'AXISCOPE_ABORT_CALIBRATION',
                     'AXISCOPE_QUERY_PROBE', 'AXISCOPE_SAVE_CAMERA_POSITION',
                     'AXISCOPE_ADJUST_CAMERA'):
            self.gcode.register_command(name, getattr(self, 'cmd_' + name))

    def handle_connect(self):
        if self.config_file_path is not None:
            expanded_path = os.path.expanduser(self.config_file_path)
            self.config_file_path = expanded_path
            
            if os.path.exists(self.config_file_path):
                self.has_cfg_data = True
                self.gcode.respond_info("Axiscope config file found (%s)." % self.config_file_path)
                self.gcode.respond_info("--Axiscope Loaded--")
            else:
                self.gcode.respond_info("Could not find Axiscope config file (%s)" % self.config_file_path)
                self.gcode.respond_info("Note: You can use ~ for home directory, e.g., ~/printer_data/config/axiscope.offsets")

        else:
            self.gcode.respond_info("Axiscope automatic calibration uses native SAVE_CONFIG. "
                                    "config_file_path is only needed for legacy manual offset saving.")


    def get_status(self, eventtime):
        return {
            'probe_results':   copy.deepcopy(self.probe_results),
            'can_save_config': self.has_cfg_data is not False,
            'endstop_x':       self.x_pos,
            'endstop_y':       self.y_pos,
            'endstop_z':       self.z_pos,
            'automatic_xy': self.probe_multi_axis is not None,
            'xy_state': self.xy_state,
            'xy_results': copy.deepcopy(self.xy_results),
            'camera_position': list(self.camera_position),
            'safe_z': self.safe_z,
            'check_tool': self.check_tool,
            'check_position': (copy.copy(getattr(self, 'check_position', None))
                               if self.check_tool is not None else None),
            'confirmed_tools': list(self.confirmed_tools),
            'confirmation_ready': self._verification_error() is None,
            'confirmation_blocked': self._verification_error(),
            'camera_adjustment': True,
            'native_config_save': True,
        }
        
    def run_gcode(self, name, template, extra_context):
        """Run gcode with template expansion and context"""
        curtime = self.printer.get_reactor().monotonic()
        context = {
            **template.create_template_context(),
            'tool': self.toolchanger.active_tool.get_status(
                curtime) if self.toolchanger.active_tool else {},
            'toolchanger': self.toolchanger.get_status(curtime),
            'axiscope': self.get_status(curtime),
            **extra_context,
        }
        template.run_gcode_from_command(context)


    def is_homed(self):
        toolhead   = self.printer.lookup_object('toolhead')
        ctime      = self.printer.get_reactor().monotonic()
        homed_axes = toolhead.get_kinematics().get_status(ctime)['homed_axes']

        return all(x in homed_axes for x in 'xyz')


    def has_switch_pos(self):
        return all(x is not None for x in [self.x_pos, self.y_pos, self.z_pos])


    # Reuse the installed toolchanger's directional sampling implementation.
    # Axiscope owns the sole probe_multi_axis instance; no [tools_calibrate].
    def probe_xy(self, toolhead, top_pos, direction, gcmd, samples=None):
        axis, sign = tools_calibrate.direction_types[direction]
        start = list(top_pos)
        start[axis] -= sign * self.spread
        self._check_position(start, gcmd)
        self._check_position([top_pos[0], top_pos[1], top_pos[2] + self.lift_z], gcmd)
        self._check_position([start[0], start[1], top_pos[2] - self.lower_z], gcmd)
        if top_pos[2] + self.lift_z > self.safe_z:
            raise gcmd.error('Probe clearance exceeds safe_z')
        return tools_calibrate.ToolsCalibrate.probe_xy(
            self, toolhead, top_pos, direction, gcmd, samples=samples)
    calibrate_xy = tools_calibrate.ToolsCalibrate.calibrate_xy

    def _require_motion(self, gcmd):
        if not self.is_homed():
            raise gcmd.error('Must home XYZ first')
        eventtime = self.printer.get_reactor().monotonic()
        printing = self.printer.lookup_object('print_stats', None)
        if printing and printing.get_status(eventtime)['state'] in ('printing', 'paused'):
            raise gcmd.error('Calibration is unavailable during a print')
        if self.safe_z is None:
            raise gcmd.error('Configure safe_z above the SexBall, camera and transit obstacles')
        mesh = self.printer.lookup_object('bed_mesh', None)
        if mesh and mesh.get_status(eventtime).get('profile_name'):
            raise gcmd.error('Run BED_MESH_CLEAR before calibration/camera positioning')

    def _check_position(self, pos, gcmd):
        th = self.printer.lookup_object('toolhead')
        status = th.get_kinematics().get_status(self.printer.get_reactor().monotonic())
        for i, value in enumerate(pos):
            if value is not None and (not math.isfinite(value) or
                    not status['axis_minimum'][i] <= value <= status['axis_maximum'][i]):
                raise gcmd.error('Axiscope %s target outside machine limits: %s' %
                                 ('XYZ'[i], value))

    def _lift(self, gcmd):
        th = self.printer.lookup_object('toolhead')
        height = max(th.get_position()[2], self.safe_z)
        self._check_position([None, None, height], gcmd)
        th.manual_move([None, None, height], self.z_move_speed)
        th.wait_moves()
        # manual_move bypasses gcode_move's cached position. Toolchanger captures
        # that cache as its return height, so refresh it before SELECT_TOOL.
        self.gcode_move.reset_last_position()

    def _move_safely(self, pos, gcmd):
        self._require_motion(gcmd)
        self._check_position(pos, gcmd)
        self._check_clearance(pos, gcmd)
        self._lift(gcmd)
        th = self.printer.lookup_object('toolhead')
        th.manual_move([pos[0], pos[1], None], self.move_speed)
        th.manual_move([None, None, pos[2]], self.z_move_speed)
        th.wait_moves()
        self.gcode_move.reset_last_position()

    def _check_clearance(self, pos, gcmd, label='Target'):
        if pos[2] > self.safe_z:
            raise gcmd.error('%s Z %.3f exceeds safe_z %.3f. Set safe_z at or '
                             'above this height and verify it clears the entire '
                             'travel path, then restart Klipper' %
                             (label, pos[2], self.safe_z))

    def _select_tool(self, number, gcmd):
        self._lift(gcmd)
        self.cmd_AXISCOPE_BEFORE_PICKUP_GCODE(gcmd)
        th = self.printer.lookup_object('toolhead')
        th.wait_moves()
        self.gcode_move.reset_last_position()
        start_z = th.get_position()[2]
        previous = self.toolchanger.active_tool
        old_z_offset = self._offsets(previous)[2] if previous else None
        # Keep KTCE's Z return after it exits the dock. Suppressing every axis
        # leaves the carriage at dock height before the next lateral move.
        # XY stay under the pickup/dropoff macros' control until Axiscope moves.
        self.gcode.run_script_from_command('SELECT_TOOL T=%d RESTORE_AXIS=Z' % number)
        th.wait_moves()
        active = self.toolchanger.active_tool
        if active is None or active.tool_number != number:
            raise gcmd.error('Physical tool selection failed for T%d' % number)
        if old_z_offset is not None:
            expected_z = start_z + self._offsets(active)[2] - old_z_offset
            actual_z = th.get_position()[2]
            if abs(actual_z - expected_z) > 0.05:
                raise gcmd.error('T%d did not return to toolchange start Z: expected '
                                 '%.3f, got %.3f. No calibration XY move was made; '
                                 'check the dock-exit/Z-return macros' %
                                 (number, expected_z, actual_z))
        self.gcode_move.reset_last_position()
        self.cmd_AXISCOPE_AFTER_PICKUP_GCODE(gcmd)

    def _locate_sexball(self, gcmd):
        th = self.printer.lookup_object('toolhead')
        def down(samples=None):
            return self.probe_multi_axis.run_probe(
                'z-', gcmd, samples=samples, speed_ratio=0.5,
                max_distance=self.max_z_travel)
        if any(p.query_endstop(th.get_last_move_time())
               for p in self.probe_multi_axis.mcu_probe):
            raise gcmd.error('SexBall already triggered before probing; check wiring/polarity')
        top = down(samples=1)
        # All XY approaches/retracts are constrained by the machine limits.
        for i in (0, 1):
            for sign in (-1, 1):
                pos = list(top)
                pos[i] += sign * self.spread
                self._check_position(pos, gcmd)
        self._check_position([top[0], top[1], top[2] - self.lower_z], gcmd)
        self._check_position([top[0], top[1], top[2] + self.lift_z], gcmd)
        x, y = self.calibrate_xy(th, top, gcmd, samples=1)
        th.manual_move([None, None, top[2] + self.lift_z], self.lift_speed)
        th.manual_move([x, y, None], self.travel_speed)
        top = down()
        x, y = self.calibrate_xy(th, [x, y, top[2]], gcmd)
        self._lift(gcmd)
        self.gcode_move.reset_last_position()
        return [x, y, top[2]]

    def _tool(self, number):
        return self.toolchanger.lookup_tool(number)

    def _offsets(self, tool):
        return [float(getattr(tool, 'gcode_%s_offset' % a) or 0.) for a in 'xyz']

    def cmd_CALIBRATE_ALL_XY_OFFSETS(self, gcmd):
        self._require_motion(gcmd)
        if not self.probe_multi_axis or not self.has_switch_pos():
            raise gcmd.error('Configure the SexBall pin and zswitch XYZ first')
        if self.xy_state in ('probing', 'awaiting_confirmation', 'measured'):
            raise gcmd.error('Finish or abort the current calibration first')
        numbers = list(self.toolchanger.tool_numbers)
        if 0 not in numbers:
            raise gcmd.error('T0 must exist as the reference tool')
        camera_check = gcmd.get_int('CAMERA_CHECK', 1, minval=0, maxval=1)
        save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        include_z = gcmd.get_int('INCLUDE_Z', 0, minval=0, maxval=1)
        if camera_check and any(v is None for v in self.camera_position):
            raise gcmd.error('Capture a T0 camera position before calibration')
        for i in (0, 1):
            for sign in (-1, 1):
                position = [self.x_pos, self.y_pos, self.z_pos]
                position[i] += sign * self.spread
                self._check_position(position, gcmd)
        self._check_position([None, None, self.safe_z], gcmd)
        if self.z_pos > self.safe_z:
            raise gcmd.error('SexBall starting height exceeds safe_z')
        for number in numbers:
            tool = self._tool(number)
            if not tool or not all(hasattr(tool, 'gcode_%s_offset' % a) for a in 'xyz'):
                raise gcmd.error('Requires KTCE or DraftShift/viesturz klipper-toolchanger tools')
            if camera_check:
                camera = list(self.camera_position)
                camera[2] += self._offsets(tool)[2] - self._offsets(self._tool(0))[2]
                self._check_position(camera, gcmd)
                self._check_clearance(camera, gcmd, 'T%d camera' % number)
        self.xy_results = {}
        self.confirmed_tools = []
        self.check_tool = None
        self.xy_save = bool(save)
        self.xy_include_z = bool(include_z)
        self.xy_state = 'probing'
        try:
            self.cmd_AXISCOPE_START_GCODE(gcmd)
            self._require_motion(gcmd)
            baseline = self._offsets(self._tool(0))
            reference = None
            for number in [0] + [n for n in numbers if n != 0]:
                self._select_tool(number, gcmd)
                self._move_safely([self.x_pos, self.y_pos, self.z_pos], gcmd)
                location = self._locate_sexball(gcmd)
                if reference is None:
                    reference = location
                offsets = [baseline[i] + location[i] - reference[i] for i in range(3)]
                if any(not math.isfinite(v) or abs(v - baseline[i]) > self.max_offset
                       for i, v in enumerate(offsets)):
                    raise gcmd.error('T%d measured offset exceeds max_offset' % number)
                if not include_z:
                    offsets[2] = self._offsets(self._tool(number))[2]
                self.xy_results[str(number)] = {
                    'position': location, 'offsets': offsets,
                    'original_offsets': self._offsets(self._tool(number)),
                    'name': self._tool(number).name}
                gcmd.respond_info('T%d measured offsets: X=%.6f Y=%.6f Z=%.6f' %
                                  (number, *offsets))
            self._select_tool(0, gcmd)
            self.cmd_AXISCOPE_FINISH_GCODE(gcmd)
            self.xy_state = 'awaiting_confirmation' if camera_check else 'measured'
            if camera_check:
                self._show_camera(0, gcmd)
            elif save:
                self.cmd_AXISCOPE_SAVE_CALIBRATION(gcmd)
        except Exception:
            # Do not issue recovery moves after a probe/toolchange error.
            self.xy_state = 'failed'
            self.check_tool = None
            raise

    def cmd_AXISCOPE_SET_CAMERA_POSITION(self, gcmd):
        self._require_motion(gcmd)
        if self.xy_state in ('probing', 'awaiting_confirmation', 'measured'):
            raise gcmd.error('Abort calibration before changing the camera reference')
        if not self.toolchanger.active_tool or self.toolchanger.active_tool.tool_number != 0:
            raise gcmd.error('Capture the camera position with physical T0 selected')
        th = self.printer.lookup_object('toolhead')
        th.wait_moves()
        current = th.get_position()
        use_current = gcmd.get_int('CURRENT', 0, minval=0, maxval=1)
        pos = [gcmd.get_float(a.upper(), current[i] if use_current else None)
               for i, a in enumerate('xyz')]
        if any(v is None for v in pos):
            raise gcmd.error('Use CURRENT=1 or specify X, Y and Z')
        self._check_position(pos, gcmd)
        self._check_clearance(pos, gcmd, 'Camera')
        self.camera_position = pos
        gcmd.respond_info('T0 camera reference captured; calibration will save it. '
                          'AXISCOPE_SAVE_CAMERA_POSITION saves it separately')

    def _show_camera(self, number, gcmd):
        self._require_motion(gcmd)
        if any(v is None for v in self.camera_position):
            raise gcmd.error('Set camera position first')
        pending = self.xy_state in ('awaiting_confirmation', 'measured')
        if pending and str(number) not in self.xy_results:
            raise gcmd.error('No measurement for T%d' % number)
        tool = self._tool(number)
        if tool is None:
            raise gcmd.error('Unknown tool')
        baseline = (self.xy_results['0']['offsets'] if pending
                    else self._offsets(self._tool(0)))
        offsets = (self.xy_results[str(number)]['offsets'] if pending
                   else self._offsets(tool))
        # Raw carriage coordinates already include tool offsets. Add the full
        # measured delta, not the old offset a second time. No runtime mutation.
        pos = [self.camera_position[i] + offsets[i] - baseline[i] for i in range(3)]
        self._check_position(pos, gcmd)
        self._check_clearance(pos, gcmd, 'T%d camera' % number)
        self._select_tool(number, gcmd)
        self._move_safely(pos, gcmd)
        self.check_tool = number
        self.check_position = pos
        gcmd.respond_info('T%d at camera. Visually check alignment, then AXISCOPE_CONFIRM_TOOL' % number)

    def cmd_AXISCOPE_MOVE_TO_CAMERA(self, gcmd):
        self._show_camera(gcmd.get_int('T', 0, minval=0), gcmd)

    def _verification_error(self):
        if self.xy_state != 'awaiting_confirmation' or self.check_tool is None:
            return 'no_check'
        active = self.toolchanger.active_tool
        if active is None or active.tool_number != self.check_tool:
            return 'wrong_tool'
        current = self.printer.lookup_object('toolhead').get_position()
        if any(abs(current[i] - self.check_position[i]) > 0.05 for i in range(3)):
            return 'position_changed'
        return None

    def _require_verification(self, gcmd):
        self._require_motion(gcmd)
        error = self._verification_error()
        if error:
            raise gcmd.error({
                'no_check': 'No camera verification in progress',
                'wrong_tool': 'Return to the verification tool with AXISCOPE_MOVE_TO_CAMERA',
                'position_changed': 'Nozzle moved; return with AXISCOPE_MOVE_TO_CAMERA before confirming',
            }[error])

    def cmd_AXISCOPE_ADJUST_CAMERA(self, gcmd):
        self._require_verification(gcmd)
        number = self.check_tool
        if number == 0:
            raise gcmd.error('T0 is the reference; abort and recapture T0 instead of trimming it')
        if number in self.confirmed_tools:
            raise gcmd.error('Cannot adjust an already confirmed tool')
        delta = [gcmd.get_float(axis, 0.) for axis in ('X', 'Y')]
        if any(not math.isfinite(v) or abs(v) > 1. for v in delta):
            raise gcmd.error('Camera adjustment must be finite and at most 1 mm per axis')
        result = self.xy_results[str(number)]
        offsets = list(result['offsets'])
        position = list(self.check_position)
        baseline = self.xy_results['0']['offsets']
        for i in range(2):
            offsets[i] += delta[i]
            position[i] += delta[i]
            if abs(offsets[i] - baseline[i]) > self.max_offset:
                raise gcmd.error('Camera-adjusted offset exceeds max_offset')
        # Commit the pending correction only after the bounded camera move succeeds.
        # Runtime tool offsets and config remain untouched until final confirmation.
        self._move_safely(position, gcmd)
        result['offsets'] = offsets
        result['camera_adjustment'] = [offsets[i] - baseline[i] -
            (result['position'][i] - self.xy_results['0']['position'][i]) for i in range(2)]
        self.check_position = position
        gcmd.respond_info('T%d pending camera correction: X=%.6f Y=%.6f; visually recheck before confirming' %
                          (number, *result['camera_adjustment']))

    def cmd_AXISCOPE_CONFIRM_TOOL(self, gcmd):
        self._require_verification(gcmd)
        number = self.check_tool
        if number not in self.confirmed_tools:
            self.confirmed_tools.append(number)
        remaining = [int(n) for n in self.xy_results if int(n) not in self.confirmed_tools]
        if remaining:
            self._show_camera(remaining[0], gcmd)
        else:
            self.xy_state = 'measured'
            self.check_tool = None
            self._lift(gcmd)
            if self.xy_save:
                self.cmd_AXISCOPE_SAVE_CALIBRATION(gcmd)
            else:
                gcmd.respond_info('All tools confirmed. AXISCOPE_SAVE_CALIBRATION writes offsets')

    def cmd_AXISCOPE_SAVE_CALIBRATION(self, gcmd):
        self._require_motion(gcmd)
        if self.xy_state != 'measured' or not self.xy_results:
            raise gcmd.error('No complete calibration ready to save')
        # Reject stale results if another command changed a tool's offsets.
        for number, result in self.xy_results.items():
            if self._offsets(self._tool(int(number))) != result['original_offsets']:
                raise gcmd.error('Tool offsets changed during calibration; abort and rerun')
        self._lift(gcmd)
        updates = {}
        for number, result in self.xy_results.items():
            if int(number) == 0:
                continue
            updates[result['name']] = {
                'gcode_%s_offset' % a: '%.6f' % result['offsets'][i]
                for i, a in enumerate('xyz' if self.xy_include_z else 'xy')}
        if all(v is not None for v in self.camera_position):
            updates['axiscope'] = self._camera_updates()
        self.xy_state = 'saving'
        try:
            self._save_native(updates, gcmd)
        except Exception:
            self.xy_state = 'measured'
            raise
        self.xy_state = 'saved'

    def _camera_updates(self):
        return {'camera_%s_pos' % a: '%.6f' % self.camera_position[i]
                for i, a in enumerate('xyz')}

    def cmd_AXISCOPE_SAVE_CAMERA_POSITION(self, gcmd):
        self._require_motion(gcmd)
        if self.xy_state in ('probing', 'awaiting_confirmation', 'measured'):
            raise gcmd.error('Complete or abort calibration first')
        if any(v is None for v in self.camera_position):
            raise gcmd.error('Capture camera position first')
        self._lift(gcmd)
        self._save_native({'axiscope': self._camera_updates()}, gcmd)

    def _save_native(self, updates, gcmd):
        configfile = self.printer.lookup_object('configfile')
        # Klipper SAVE_CONFIG rejects duplicate options in included files.
        # Migrate only the calibrated options into the native autosave block.
        # Back up each touched include and roll it back if saving fails.
        root = self.printer.get_start_args()['config_file']
        edits = included_option_edits(root, updates)
        changed = []
        autosave = getattr(configfile, 'autosave', configfile)
        pending_snapshot = {name: copy.deepcopy(getattr(autosave, name))
                            for name in ('fileconfig', 'status_save_pending', 'save_config_pending')}
        try:
            for path, original, replacement in edits:
                with open(path) as f:
                    if f.read() != original:
                        raise gcmd.error('Config changed during save: %s' % path)
                backup = path + '.axiscope-backup-' + time.strftime('%Y%m%d-%H%M%S')
                if os.path.exists(backup):
                    raise gcmd.error('Backup already exists: %s' % backup)
                shutil.copy2(path, backup)
                atomic_replace_text(path, replacement)
                changed.append((path, original))
            for section, options in updates.items():
                for option, value in options.items():
                    configfile.set(section, option, value)
            self.gcode.run_script_from_command('SAVE_CONFIG')
        except Exception:
            for name, value in pending_snapshot.items():
                setattr(autosave, name, value)
            for path, original in reversed(changed):
                atomic_replace_text(path, original)
            raise

    def cmd_AXISCOPE_ABORT_CALIBRATION(self, gcmd):
        self.xy_results = {}
        self.confirmed_tools = []
        self.check_tool = None
        self.xy_state = 'idle'
        gcmd.respond_info('Calibration discarded; tool offsets were not changed')

    def cmd_AXISCOPE_QUERY_PROBE(self, gcmd):
        if self.probe_multi_axis is None:
            raise gcmd.error('Configure the SexBall pin first')
        th = self.printer.lookup_object('toolhead')
        states = [p.query_endstop(th.get_last_move_time())
                  for p in self.probe_multi_axis.mcu_probe]
        gcmd.respond_info('Axiscope SexBall: %s' % ('TRIGGERED' if any(states) else 'open'))

    def update_tool_offsets(self, cfg_data, tool_name, offsets):
        axis          = "xyz" if len(offsets) == 3 else "xy"
        section_name  = "[%s]" % tool_name
        section_start = None
        section_end   = None
        new_section   = None

        for i, line in enumerate(cfg_data):
            stripped_line = line.lstrip()
            if stripped_line.startswith(section_name):
                section_start = i+1
            
            elif section_start is not None:
                if stripped_line.startswith('['):
                    section_end = i-1
                    break

        for i, a in enumerate(axis):
            offset_name   = "gcode_%s_offset" % a
            offset_value  = offsets[i]
            offset_string = "%s: %.3f\n" % (offset_name, offset_value)

            if section_start is not None:
                if section_end is not None:
                    section_lines = cfg_data[section_start:section_end+1]
                else:
                    section_lines = cfg_data[section_start:]

                for line in section_lines:
                    stripped_line = line.lstrip()

                    if stripped_line.startswith(offset_name):
                        cfg_index = cfg_data.index(line)
                        cfg_data[cfg_index] = offset_string

            else:
                if new_section is not None:
                    new_section.append(offset_string)
                else:
                    new_section = ["\n", section_name+"\n", offset_string]

        if new_section is not None:
            new_section.append("\n")
            no_touch_index = None

            if self.config_file_path.endswith('printer.cfg'):
                for line in cfg_data:
                    if line.lstrip().startswith('#*#'):
                        no_touch_index = cfg_data.index(line)
                        break

            if no_touch_index is not None:
                cfg_data = cfg_data[:no_touch_index] + ["\n"] + new_section + cfg_data[no_touch_index:]

            else:
                cfg_data = cfg_data + ["\n"] + new_section
        
        return cfg_data

    cmd_MOVE_TO_ZSWITCH_help = "Move the toolhead over the Z switch"

    def cmd_MOVE_TO_ZSWITCH(self, gcmd):
        if not self.is_homed():
            gcmd.respond_info('Must home first.')
            return

        if not self.has_switch_pos():
            gcmd.respond_error('Z switch positions are not valid.')
            return

        gcmd.respond_info('Moving to Z Switch')

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()

        # Get current position
        current_pos = toolhead.get_position()

        # First move horizontally to the target X,Y at current Z height
        #toolhead.manual_move([self.x_pos, self.y_pos, current_pos[2]], self.move_speed)
        self.gcode_move.cmd_G1(self.gcode.create_gcode_command("G0", "G0", { 'X': self.x_pos, 'Y': self.y_pos, 'Z': current_pos[2], 'F': self.move_speed*60 }))

        # Then move vertically to the target Z height
        toolhead.manual_move([None, None, self.z_pos+self.lift_z], self.z_move_speed)


    cmd_PROBE_ZSWITCH_help = "Probe the Z switch to determine offset."

    def cmd_PROBE_ZSWITCH(self, gcmd):
        toolhead  = self.printer.lookup_object('toolhead')
        tool_no   = str(self.toolchanger.active_tool.tool_number)
        start_pos = toolhead.get_position()
        z_result  = self.probe_multi_axis.run_probe("z-", gcmd, speed_ratio=0.5, max_distance=10.0, samples=self.samples)[2]
        
        self.reactor = self.printer.get_reactor()
        measured_time = self.reactor.monotonic()

        if tool_no == "0":
            self.probe_results[tool_no] = {'z_trigger': z_result, 'z_offset': 0, 'last_run': measured_time}

        elif "0" in self.probe_results:
            z_offset = z_result - self.probe_results["0"]['z_trigger']

            self.probe_results[tool_no] = {
                'z_trigger': z_result, 
                'z_offset': z_offset,
                'last_run': measured_time
            }

        else:
            self.probe_results[tool_no] = {'z_trigger': z_result, 'z_offset': None, 'last_run': measured_time}


        toolhead.move(start_pos, self.z_move_speed)
        toolhead.set_position(start_pos)
        toolhead.wait_moves()

        return


    cmd_CALIBRATE_ALL_Z_OFFSETS_help = "Probe the Z switch for each tool to determine offset."

    def cmd_CALIBRATE_ALL_Z_OFFSETS(self, gcmd):
        
        if not self.is_homed():
            gcmd.respond_info('Must home first.')
            return
            
        # Run start_gcode at the beginning of calibration
        self.cmd_AXISCOPE_START_GCODE(gcmd)

        for tool_no in self.toolchanger.tool_numbers:
            # Run before_pickup_gcode before tool change
            self.cmd_AXISCOPE_BEFORE_PICKUP_GCODE(gcmd)
            self.gcode.run_script_from_command('T%i' % tool_no)
            # Run after_pickup_gcode after tool change
            self.cmd_AXISCOPE_AFTER_PICKUP_GCODE(gcmd)
            
            self.gcode.run_script_from_command('MOVE_TO_ZSWITCH')
            self.gcode.run_script_from_command('PROBE_ZSWITCH SAMPLES=%i' % self.samples)

        self.gcode.run_script_from_command('T0')

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()

        for tool_no in self.probe_results:
            if tool_no != "0":
                gcmd.respond_info('T%s gcode_z_offset: %.3f' % (tool_no, self.probe_results[tool_no]['z_offset']))
        
        # Run finish_gcode after calibration is complete
        self.cmd_AXISCOPE_FINISH_GCODE(gcmd)
    
    # Command handlers for custom macro G-code commands
    def cmd_AXISCOPE_START_GCODE(self, gcmd):
        """Execute the Axiscope start G-code macro"""
        if self.start_gcode:
            self.run_gcode('start_gcode', self.start_gcode, {})
        else:
            gcmd.respond_info("No start_gcode configured for Axiscope")

    def cmd_AXISCOPE_BEFORE_PICKUP_GCODE(self, gcmd):
        """Execute the Axiscope before pickup G-code macro"""
        if self.before_pickup_gcode:
            self.run_gcode('before_pickup_gcode', self.before_pickup_gcode, {})
        else:
            gcmd.respond_info("No before_pickup_gcode configured for Axiscope")

    def cmd_AXISCOPE_AFTER_PICKUP_GCODE(self, gcmd):
        """Execute the Axiscope after pickup G-code macro"""
        if self.after_pickup_gcode:
            self.run_gcode('after_pickup_gcode', self.after_pickup_gcode, {})
        else:
            gcmd.respond_info("No after_pickup_gcode configured for Axiscope")

    def cmd_AXISCOPE_FINISH_GCODE(self, gcmd):
        """Execute the Axiscope finish G-code macro"""
        if self.finish_gcode:
            self.run_gcode('finish_gcode', self.finish_gcode, {})
        else:
            gcmd.respond_info("No finish_gcode configured for Axiscope")

    cmd_AXISCOPE_SAVE_TOOL_OFFSET_help = "Save a tool offset to your axiscope config file."
    
    def cmd_AXISCOPE_SAVE_TOOL_OFFSET(self, gcmd):
        """
        This function saves the tool offsets for the specified tool.

        Usage
        -----
        `AXISCOPE_SAVE_TOOL_OFFSET TOOL_NAME=<tool_name> OFFSETS=<offsets>`

        Example
        -----
        ```
        AXISCOPE_SAVE_TOOL_OFFSET TOOL_NAME="tool T0" OFFSETS="[-0.01, 0.03, 0.01]"
        ```
        """
        if self.has_cfg_data is not False:
            with open(self.config_file_path, 'r') as f:
                cfg_data = f.readlines()

            tool_name = gcmd.get('TOOL_NAME')
            offsets   = ast.literal_eval(gcmd.get('OFFSETS'))

            out_data = self.update_tool_offsets(cfg_data, tool_name, offsets)
            gcmd.respond_info("Writing %s offsets." % tool_name)

            with open(self.config_file_path, 'w') as f:
                for line in out_data:
                    f.write(line)

                f.close()
                gcmd.respond_info("Offsets written successfully.")

        else:
            gcmd.respond_info("Axiscope needs a valid config location (config_file_path) to save tool offsets.")


    cmd_AXISCOPE_SAVE_MULTIPLE_TOOL_OFFSETS_help = "Save multiple tool offsets to your axiscope config file."

    def cmd_AXISCOPE_SAVE_MULTIPLE_TOOL_OFFSETS(self, gcmd):
        """
        This function saves the offsets for multiple tools'.

        Usage
        -----
        `AXISCOPE_SAVE_MULTIPLE_TOOL_OFFSETS TOOLS=<tools> OFFSETS=<offsets>`

        Example
        -----
        ```
        AXISCOPE_SAVE_MULTIPLE_TOOL_OFFSETS TOOLS="['tool T0', 'tool T1']" OFFSETS="[[-0.01, 0.03, 0.01], [0.02, 0.02, -0.06]]"
        ```
        """
        if self.has_cfg_data is not False:
            with open(self.config_file_path, 'r') as f:
                cfg_data = f.readlines()

            tool_names = gcmd.get('TOOLS')
            offsets    = ast.literal_eval(gcmd.get('OFFSETS'))
            out_data   = cfg_data

            for i, tool_name in enumerate(tool_names):
                out_data = self.update_tool_offsets(cfg_data, tool_name, offsets[i])

            gcmd.respond_info("Writing %s offsets." % tool_name)

            with open(self.config_file_path, 'w') as f:
                for line in out_data:
                    f.write(line)

                f.close()
                gcmd.respond_info("Offsets written successfully.")

        else:
            gcmd.respond_info("Axiscope needs a valid config location (config_file_path) to save tool offsets.")

    cmd_AXISCOPE_SET_ENDSTOP_POSITION_help = "Set kinematic position for X, Y, and/or Z axes"
    
    def cmd_AXISCOPE_SET_ENDSTOP_POSITION(self, gcmd):
        """
        Set axiscope endstop positions for specified axes. Can receive X, Y, and/or Z optionally.
        
        Usage
        -----
        AXISCOPE_SET_ENDSTOP_POSITION [X=<x_pos>] [Y=<y_pos>] [Z=<z_pos>] [CURRENT=<1|0>]
        
        Examples
        --------
        AXISCOPE_SET_ENDSTOP_POSITION X=150.0          # Set only X endstop position
        AXISCOPE_SET_ENDSTOP_POSITION Y=200.0          # Set only Y endstop position  
        AXISCOPE_SET_ENDSTOP_POSITION Z=0.0            # Set only Z endstop position
        AXISCOPE_SET_ENDSTOP_POSITION X=150.0 Y=200.0  # Set X and Y endstop positions
        AXISCOPE_SET_ENDSTOP_POSITION X=150.0 Y=200.0 Z=0.0  # Set all endstop positions
        AXISCOPE_SET_ENDSTOP_POSITION CURRENT=1        # Set all positions to current position
        AXISCOPE_SET_ENDSTOP_POSITION X=150.0 CURRENT=1  # Set X to 150.0, Y and Z to current
        """
        # Get current position
        toolhead = self.printer.lookup_object('toolhead')
        current_pos = toolhead.get_position()
        
        # Check if CURRENT parameter is set
        use_current = gcmd.get_int('CURRENT', 0)
        
        # Get optional parameters
        x_pos = gcmd.get_float('X', None)
        y_pos = gcmd.get_float('Y', None) 
        z_pos = gcmd.get_float('Z', None)
        
        # If CURRENT=1, use current position for unspecified axes
        if use_current:
            if x_pos is None:
                x_pos = current_pos[0]
            if y_pos is None:
                y_pos = current_pos[1]
            if z_pos is None:
                z_pos = current_pos[2]
        
        # Update axiscope's internal position variables
        set_axes = []
        if x_pos is not None:
            self.x_pos = x_pos
            set_axes.append(f"X={x_pos:.3f}")
        if y_pos is not None:
            self.y_pos = y_pos
            set_axes.append(f"Y={y_pos:.3f}")
        if z_pos is not None:
            self.z_pos = z_pos
            set_axes.append(f"Z={z_pos:.3f}")
            
        if set_axes:
            if use_current:
                gcmd.respond_info(f"Set axiscope endstop positions (using current): {' '.join(set_axes)}")
            else:
                gcmd.respond_info(f"Set axiscope endstop positions: {' '.join(set_axes)}")
        else:
            gcmd.respond_info("No axes specified. Use X=, Y=, Z=, and/or CURRENT=1 parameters.")


def atomic_replace_text(path, content):
    # Real paths preserve existing symlinks to custom config locations.
    path = os.path.realpath(path)
    fd, temp = tempfile.mkstemp(prefix='.axiscope-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, temp)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def included_option_edits(root, updates):
    """Find scalar offset/camera options in the actual include tree.

    Leave the main config to Klipper's own duplicate stripping and preserve
    every unrelated option, comment, macro and include. Do not invent tool
    section names or write an unreferenced offset file.
    """
    root_real = os.path.realpath(root)
    visited = set()
    edits = []
    def visit(filename, stack):
        path = os.path.realpath(filename)
        if path in stack:
            raise ValueError('Recursive config include: %s' % filename)
        if path in visited:
            return
        visited.add(path)
        with open(filename) as f:
            original = f.read()
        section = None
        lines = original.splitlines(True)
        output = []
        changed = False
        for line in lines:
            header = re.match(r'^\s*\[([^]]+)\]\s*(?:[#;].*)?$', line.strip())
            if header:
                section = header.group(1).strip()
                if section.startswith('include '):
                    pattern = os.path.join(os.path.dirname(filename), section[8:].strip())
                    matches = sorted(glob.glob(pattern))
                    if not matches and not glob.has_magic(pattern):
                        raise ValueError('Missing config include: %s' % pattern)
                    for child in matches:
                        visit(child, stack + [path])
            option = re.match(r'^\s*([A-Za-z0-9_]+)\s*[:=]', line)
            if path != root_real and option and option.group(1).lower() in updates.get(section, {}):
                line = '# Axiscope migrated to SAVE_CONFIG: ' + line
                changed = True
            output.append(line)
        if changed:
            edits.append((path, original, ''.join(output)))
    visit(os.path.abspath(root), [])
    return edits

def load_config(config):
    return Axiscope(config)
