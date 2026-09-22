'use strict';

// Run with Node in CI, or pass the script source to runSettingsTests in a V8 harness.
async function runSettingsTests(source) {
  const checks = [];
  function assert(value, message) { if (!value) throw Error(message); }
  function element() {
    return {
      children: [], textContent: '', hidden: false, disabled: false,
      replaceChildren(...rows) { this.children = rows; },
      append(...rows) { this.children.push(...rows); },
    };
  }
  async function check(name, test) {
    try { await test(); checks.push({ name, ok: true }); }
    catch (error) { checks.push({ name, ok: false, error: String(error.message || error) }); }
  }

  const ids = {
    profileRecipientList: element(), profileRecipientLibraries: element(),
    findPeople: element(), addUserDialog: { close() {} },
  };
  const document = { hidden: false, createElement: () => element() };
  const $ = id => ids[id];
  const calls = [];
  let refreshed = 0;
  async function post(path, payload) { calls.push({ path, payload }); return {}; }
  async function action(fn) { return fn(); }
  async function refresh() { refreshed++; }
  async function responseJson(path) {
    calls.push({ path });
    if (path.startsWith('/api/plex/profiles/libraries')) return {
      items: [{ id: '11', name: '音乐', status: 'added' },
              { id: '15', name: '经典音乐', status: 'available' }],
    };
    if (path === '/api/plex/profiles') return { active_profile_id: 'default', items: [] };
    throw Error('Unexpected request: ' + path);
  }
  const start = source.indexOf('function renderRecipients(');
  const end = source.indexOf('async function loadAvailablePeople()', start);
  assert(start >= 0 && end > start, 'Recipient rendering functions missing');
  const recipients = new Function('$', 'document', 'action', 'post', 'responseJson', 'refresh',
    source.slice(start, end) + ';return {renderRecipients,renderRecipientLibraries};')(
      $, document, action, post, responseJson, refresh);

  await check('existing accounts remain selectable for another library', async () => {
    recipients.renderRecipients([
      { id: '1', title: 'shudaizi', username: 'shudaizi', kind: 'owner',
        kind_label: '管理员', existing_profile_id: 'default' },
      { id: '42', title: 'shudai6', username: 'shudai6', kind: 'shared',
        kind_label: '共享朋友', existing_profile_id: 'shared-42' },
    ], 'default');
    assert(ids.profileRecipientList.children.length === 2, 'Existing accounts were hidden');
    assert(ids.profileRecipientList.children.every(row => !row.children[1].disabled),
      'Existing account cannot choose a second library');
  });

  await check('library rows block only the exact already-added or cleaning pair', async () => {
    recipients.renderRecipientLibraries(
      { id: '42', title: 'shudai6', kind: 'shared' }, 'default',
      { account: { id: '42', username: 'shudai6' }, libraries: [
        { id: '11', name: '音乐', status: 'added' },
        { id: '15', name: '经典音乐', status: 'available' },
        { id: '16', name: '其他音乐', status: 'cleanup' },
      ] });
    const rows = ids.profileRecipientLibraries.children.slice(1);
    assert(rows.length === 3, 'Expected all accessible libraries');
    assert(rows[0].children[1].textContent === '已添加' && rows[0].children[1].disabled,
      'Already-added pair can be added again');
    assert(rows[1].children[1].textContent === '添加' && !rows[1].children[1].disabled,
      'Second library is unavailable');
    assert(rows[2].children[1].textContent === '待清理' && rows[2].children[1].disabled,
      'Pending cleanup pair can be added again');
    await rows[1].children[1].onclick();
    assert(calls.some(call => call.path === '/api/plex/recipients/shared/import'
      && call.payload.library_id === '15'), 'Second shared library did not use import route');
  });

  await check('owner selects a second library through the owner profile route', async () => {
    recipients.renderRecipients([
      { id: '1', title: 'shudaizi', username: 'shudaizi', kind: 'owner',
        kind_label: '管理员', existing_profile_id: 'default' },
    ], 'default');
    await ids.profileRecipientList.children[0].children[1].onclick();
    const rows = ids.profileRecipientLibraries.children.slice(1);
    assert(calls.some(call => call.path === '/api/plex/profiles/libraries?profile_id=default'),
      'Owner libraries were not queried');
    await rows[1].children[1].onclick();
    assert(calls.some(call => call.path === '/api/plex/profiles/library'
      && call.payload.profile_id === 'default' && call.payload.library_id === '15'),
      'Owner second library did not create an independent profile');
  });

  await check('a completed removal updates the open page without reload', async () => {
    const first = source.indexOf('let profileStatusTimer=null');
    const last = source.indexOf('function render(s,saved)', first);
    assert(first >= 0 && last > first, 'Removal status polling is missing');
    const scheduled = [];
    const rendered = [];
    const auth = { profile: () => 'default', setProfile() {} };
    const view = new Function('document', 'PCHAuth', 'responseJson', 'refresh',
      'setTimeout', 'clearTimeout', 'renderManagedUsers',
      'let plexProfiles=[];let activeProfile="default";'
      + source.slice(first, last)
      + ';return {renderProfiles};')(
        document, auth, responseJson, refresh,
        callback => { scheduled.push(callback); return scheduled.length; }, () => {},
        () => rendered.push(true));
    view.renderProfiles({ active_profile_id: 'default', items: [
      { id: 'shared-42', enabled: false, removal: { status: 'running' } },
    ] });
    assert(scheduled.length > 0, 'No status refresh was scheduled');
    await scheduled.shift()();
    assert(rendered.length >= 2 && refreshed > 0,
      'Removal completion did not refresh the page');
  });
  await check('completed new-user generation clears its warning without reload', async () => {
    const first = source.indexOf('let profileStatusTimer=null');
    const last = source.indexOf('function render(s,saved)', first);
    const scheduled = [];
    const rendered = [];
    const before = refreshed;
    const auth = { profile: () => 'default', setProfile() {} };
    const finishedStatus = path => path === '/api/plex/profiles'
      ? Promise.resolve({ active_profile_id: 'default', items: [
        { id: 'new-profile', enabled: true },
      ] }) : responseJson(path);
    const view = new Function('document', 'PCHAuth', 'responseJson', 'refresh',
      'setTimeout', 'clearTimeout', 'renderManagedUsers',
      'let plexProfiles=[];let activeProfile="default";'
      + source.slice(first, last)
      + ';return {renderProfiles};')(
        document, auth, finishedStatus, refresh,
        callback => { scheduled.push(callback); return scheduled.length; }, () => {},
        () => rendered.push(true));
    view.renderProfiles({ active_profile_id: 'default', items: [
      { id: 'new-profile', enabled: true,
        preparation: { status: 'needs_attention', errors: { weekly: 'needs_attention' } } },
    ] });
    assert(scheduled.length > 0, 'No generation status refresh was scheduled');
    await scheduled.shift()();
    assert(rendered.length >= 2 && refreshed > before,
      'Completed generation did not refresh the page');
  });
  await check('durable generation warnings do not poll every three seconds', async () => {
    const first = source.indexOf('let profileStatusTimer=null');
    const last = source.indexOf('function render(s,saved)', first);
    const delayFor = (status, removalStatus) => {
      const delays = [];
      const auth = { profile: () => 'default', setProfile() {} };
      const view = new Function('document', 'PCHAuth', 'responseJson', 'refresh',
        'setTimeout', 'clearTimeout', 'renderManagedUsers',
        'let plexProfiles=[];let activeProfile="default";'
        + source.slice(first, last)
        + ';return {renderProfiles};')(
          document, auth, responseJson, refresh,
          (_callback, delay) => { delays.push(delay); return delays.length; }, () => {}, () => {});
      view.renderProfiles({ active_profile_id: 'default', items: [
        { id: 'new-profile', enabled: !removalStatus, preparation: { status },
          ...(removalStatus ? { removal: { status: removalStatus } } : {}) },
      ] });
      return delays[0];
    };
    assert(delayFor('running') <= 5000, 'Active generation is not checked promptly');
    assert(delayFor('needs_attention') >= 15000, 'Needs-attention state polls too often');
    assert(delayFor('waiting_for_data') >= 60000, 'Long wait polls too often');
    assert(delayFor('paused', 'needs_attention') >= 15000,
      'Paused generation keeps polling rapidly during a stalled removal');
  });
  return checks;
}

globalThis.runSettingsTests = runSettingsTests;
if (typeof require === 'function' && require.main === module) {
  const fs = require('fs');
  const path = require('path');
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'helper', 'static', 'settings.js'), 'utf8');
  runSettingsTests(source).then(results => {
    for (const result of results) process.stdout.write(
      (result.ok ? 'PASS ' : 'FAIL ') + result.name + (result.error ? ': ' + result.error : '') + '\n');
    if (results.some(result => !result.ok)) process.exitCode = 1;
  }).catch(error => { console.error(error); process.exitCode = 1; });
}
