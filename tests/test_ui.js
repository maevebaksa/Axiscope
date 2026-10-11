const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const elements = new Map();
function $(selector) {
    if (selector === document) return {ready() {}};
    if (!elements.has(selector)) elements.set(selector, {visible: false, props: {}, textValue: '', children: []});
    const state = elements.get(selector);
    const item = {
        show() {state.visible = true; return item;},
        hide() {state.visible = false; return item;},
        prop(key, value) {state.props[key] = value; return item;},
        text(value) {state.textValue = value; return item;},
        empty() {state.children = []; return item;},
        append(child) {state.children.push(child); return item;},
        appendTo() {return item;},
        on() {return item;}
    };
    return item;
}
const document = {};
const context = {document, $, Number, Array, Object};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../js/automatic.js'), 'utf8'), context);
const update = context.updateAutomaticCalibration;
function state(selector) {return elements.get(selector);}
function disabled(selector) {return state(selector).props.disabled;}
function text() {return state('#automatic-status').textValue;}

update(null, false, -1);
assert.equal(state('#automatic-calibration').visible, true);
assert.equal(disabled('.automatic-action'), true);
assert.match(text(), /Waiting/);
update({probe_results: {}}, true, 0);
assert.equal(state('#automatic-calibration').visible, true);
assert.match(text(), /old Axiscope backend/);
update({automatic_xy: false}, true, 0);
assert.match(text(), /SexBall pin is missing/);
update(null, false, -1, 'Cannot read printer status');
assert.equal(text(), 'Cannot read printer status');

const ready = {automatic_xy: true, camera_position: [null, null, null], xy_state: 'idle', xy_results: {}};
update(ready, true, 0);
assert.equal(disabled('#automatic-capture'), false);
assert.equal(disabled('#automatic-start'), true);
update({...ready, camera_position: [100, 110, 10]}, true, 0);
assert.equal(disabled('#automatic-start'), false);
update({...ready, camera_position: [100, 110, 10]}, false, 0);
assert.equal(disabled('#automatic-start'), true);
assert.equal(disabled('#automatic-capture'), true);
update({...ready, camera_position: [100, 110, 10], xy_state: 'awaiting_confirmation', check_tool: 1}, true, 0);
assert.equal(disabled('#automatic-start'), true);
assert.equal(disabled('#automatic-confirm'), true);
update({...ready, camera_position: [100, 110, 10], xy_state: 'awaiting_confirmation', check_tool: 1}, true, 1);
assert.equal(disabled('#automatic-confirm'), false);
assert.equal(disabled('#automatic-abort'), false);

const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const panelOpening = html.match(/<div id="automatic-calibration"[^>]*>/)[0];
assert.ok(!panelOpening.includes('display:none'));
assert.ok(html.includes('automatic.js?v=sexball-ui-2'));
assert.ok(html.includes('index.js?v=sexball-ui-2'));
console.log('UI visibility, diagnostic messages, capability checks, homing/camera checks, and confirmation gating passed.');
