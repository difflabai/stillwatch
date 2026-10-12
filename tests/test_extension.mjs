import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const emitted = [];
const sources = [];
class Notification {
    constructor(source, title, body) { Object.assign(this, {source, title, body}); }
    destroy(reason = 2) { this.reason = reason; this.source.notifications = this.source.notifications.filter(item => item !== this); }
}
class Tray { _showNotification() {} }
class InjectionManager {
    constructor() { this.restore = []; }
    overrideMethod(prototype, name, replacement) {
        const original = prototype[name];
        this.restore.push(() => { prototype[name] = original; });
        prototype[name] = replacement(original);
    }
    clear() { this.restore.reverse().forEach(restore => restore()); }
}
const Main = {messageTray: new Tray(), modalCount: 0, sessionMode: {isLocked: false}};
Main.messageTray.getSources = () => sources;
const context = {
    console, Main, InjectionManager, Extension: class {},
    MessageTray: {MessageTray: Tray, Notification, NotificationDestroyedReason: {DISMISSED: 2, EXPIRED: 1}},
    ModalDialog: {ModalDialog: class { close() {} destroy() {} }},
    Gio: {DBusExportedObject: {wrapJSObject() { return {export() {}, emit_signal(name, value) { emitted.push({name, payload: JSON.parse(value.values[0])}); }, unexport() {}}; }},
        DBus: {session: {signal_subscribe() { return 1; }, signal_unsubscribe() {}}},
        BusType: {SESSION: 1}, BusNameOwnerFlags: {NONE: 0}, DBusSignalFlags: {NONE: 0},
        bus_own_name() { return 1; }, bus_unown_name() {}},
    GLib: {Variant: class { constructor(signature, values) { this.values = values; }}},
    St: {}, Pango: {}, Clutter: {},
};
vm.createContext(context);
const source = fs.readFileSync('src/stillwatch/resources/extension/extension.js', 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export default class AttentionGate', 'globalThis.AttentionGate = class AttentionGate');
vm.runInContext(source, context);
const gate = new context.AttentionGate();
gate.enable();
function notification() {
    const src = {title: 'Slack', notifications: []};
    const item = new Notification(src, 'Example ping', 'Please review');
    src.notifications.push(item);
    sources.push(src);
    return item;
}
notification().destroy(2);
assert.equal(emitted.length, 1, 'Explicit native dismissal is observed before source removal');
assert.equal(emitted[0].name, 'NativeDismissed');
assert.equal(emitted[0].payload.title, 'Example ping');
notification().destroy(1);
assert.equal(emitted.length, 1, 'Native banner expiry does not acknowledge history');
const closing = notification();
closing.source._inDestruction = true;
closing.destroy(2);
assert.equal(emitted.length, 1, 'App/source shutdown does not acknowledge history');
const first = notification();
const second = notification();
const other = notification();
other.body = 'Different message';
gate._clearNative({app: 'com.slack.Slack', title: 'Example ping', body: 'Please review'});
assert.equal(first.reason, 2);
assert.equal(second.reason, 2);
assert.equal(other.reason, undefined, 'Different message must remain');
assert.equal(emitted.length, 1, 'Replicated handling must not create an echo dismissal');
gate.disable();
notification().destroy(2);
assert.equal(emitted.length, 1, 'Disabling restores the original native behavior');
console.log('Native dismissal hooks: six checks passed. GNOME session integration remains a manual check.');
