// SexBall measurement, then one camera check at a time. No image recognition.
let automaticRequestBusy = false;
function automaticCommand(script) {
    if (automaticRequestBusy) return;
    automaticRequestBusy = true;
    $('.automatic-action').prop('disabled', true);
    $('#automatic-error').text('');
    return $.ajax({
        url: printerUrl(printerIp, '/printer/gcode/script'),
        method: 'POST', contentType: 'application/json',
        data: JSON.stringify({script})
    }).fail(function(xhr) {
        $('#automatic-error').text(xhr.responseJSON?.error?.message || xhr.responseText || 'Command failed');
    }).always(function() {
        automaticRequestBusy = false;
        updatePage();
    });
}
function updateAutomaticCalibration(state, homed, activeTool) {
    if (!state?.automatic_xy) {
        $('#automatic-calibration').hide();
        return;
    }
    $('#automatic-calibration').show();
    const cameraReady = state.camera_position?.every(v => v !== null);
    const checking = state.xy_state === 'awaiting_confirmation';
    const pending = ['probing', 'awaiting_confirmation', 'measured', 'saving'].includes(state.xy_state);
    $('#automatic-start').prop('disabled', automaticRequestBusy || !homed || !cameraReady || pending);
    $('#automatic-capture').prop('disabled', automaticRequestBusy || !homed || activeTool !== 0 || pending);
    $('#automatic-camera-save').prop('disabled', automaticRequestBusy || !homed || !cameraReady || pending);
    $('#automatic-confirm').prop('disabled', automaticRequestBusy || !homed || !checking || activeTool !== state.check_tool);
    $('#automatic-abort').prop('disabled', automaticRequestBusy || !pending);
    const message = checking ? `Check physical T${state.check_tool} in the camera, then confirm. The last confirmation saves and restarts Klipper.` :
        `${state.xy_state || 'idle'}. ${cameraReady ? 'Camera reference ready.' : 'Align T0 in the camera and capture its position.'}`;
    $('#automatic-status').text(message);
    const body = $('#automatic-results').empty();
    Object.entries(state.xy_results || {}).forEach(([number, result]) => {
        const row = $('<tr>');
        $('<td>').text(`T${number}`).appendTo(row);
        result.offsets.forEach(value => $('<td>').text(value.toFixed(6)).appendTo(row));
        $('<td>').text((state.confirmed_tools || []).includes(Number(number)) ? 'Confirmed' : 'Measured').appendTo(row);
        body.append(row);
    });
}
$(document).ready(function() {
    $('#automatic-capture').on('click', () => automaticCommand('AXISCOPE_SET_CAMERA_POSITION CURRENT=1'));
    $('#automatic-camera-save').on('click', () => automaticCommand('AXISCOPE_SAVE_CAMERA_POSITION'));
    $('#automatic-start').on('click', () => automaticCommand('CALIBRATE_ALL_XY_OFFSETS CAMERA_CHECK=1 SAVE=1'));
    $('#automatic-confirm').on('click', () => automaticCommand('AXISCOPE_CONFIRM_TOOL'));
    $('#automatic-abort').on('click', () => automaticCommand('AXISCOPE_ABORT_CALIBRATION'));
});
