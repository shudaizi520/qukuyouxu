'use strict';

const $ = id => document.getElementById(id);
const ACTIVE_JOB_POLL_MS = 3000;
const IDLE_POLL_MS = 45000;
let timer = null;
let busy = false;
let latest = null;
let diagnosticsLoaded = false;
let historyLoaded = false;

function note(text, error = false) {
  PCHUI.notify(text, { error });
}

function time(value) {
  return value
    ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
    : '—';
}

async function request(path) {
  return PCHAuth.request(path);
}

async function refresh() {
  const response = await request('/api/status');
  const summary = await response.json();
  render(summary);
  return summary;
}

function renderDailyDiagnostics(data) {
  const ready = !!data.algorithm_version;
  const view = data.display || {};
  const summary = view.summary || {};
  const buckets = view.buckets || {};
  const exclusions = view.exclusions || {};
  $('dailyDiagnostics').textContent = ready
    ? `候选 ${summary.candidate || 0} · 选入 ${summary.selected || 0}`
    : '尚未生成';
  $('dailyBuckets').textContent = ready
    ? `稳定喜好 ${buckets.stable || 0} · 近期口味 ${buckets.recent || 0} · `
      + `久未重听 ${buckets.rediscovery || 0} · 曲库探索 ${buckets.exploration || 0}`
    : '';
  $('dailySimilarity').textContent = ready && data.similarity_source
    ? `相近歌曲依据 · ${data.similarity_source}`
    : '';
  const details = $('dailyExclusionDetails');
  details.hidden = !ready;
  if (!ready) {
    details.open = false;
    const target = $('dailyExclusions');
    if (target) target.textContent = '';
    return;
  }
  $('dailyExclusions').textContent = `手动排除 ${exclusions.manual || 0} · `
    + `最近播放 ${exclusions.recent_plays || 0} · `
    + `听腻冷却 ${exclusions.fatigue || 0} · 防重复 ${exclusions.recent_daily || 0}`;
}

function renderEvents(events) {
  $('events').replaceChildren();
  const rows = (events || []).slice().reverse();
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '暂无记录';
    $('events').append(empty);
    return;
  }
  for (const event of rows.slice(0, 20)) {
    const row = document.createElement('div');
    row.className = 'event-row';
    const dot = document.createElement('span');
    dot.className = `event-dot ${((event.level || 'info') === 'error') ? 'error' : ''}`;
    const text = document.createElement('div');
    const message = document.createElement('strong');
    message.textContent = typeof event === 'string' ? event : (event.message || '运行记录');
    text.append(message);
    if (event.time) {
      const timestamp = document.createElement('small');
      timestamp.textContent = time(event.time);
      text.append(timestamp);
    }
    row.append(dot, text);
    $('events').append(row);
  }
}

function render(summary) {
  const c = summary.settings || {};
  const j = summary.job || {};
  const q = summary.qq_auth || {};
  const b = summary.behavior || {};
  const w = summary.webhook || {};
  const scheduler = summary.scheduler || {};
  const plexReady = !!(c.plex_url && c.token_present && c.section);

  $('plexHealth').textContent = summary.plex_connection?.server
    ? '正常' : (plexReady ? '已配置' : '未配置');
  $('plexDetail').textContent = summary.plex_connection?.server
    ? `${summary.plex_connection.server} · 音乐库 ${c.section}`
    : (plexReady ? '等待连接检查' : '请先连接 Plex');
  $('qqHealth').textContent = q.logged_in ? '已授权' : '未授权';
  $('qqDetail').textContent = q.logged_in ? '主题来源可用' : '主题扩充暂不可用';

  const schedulerLabels = {
    normal: '空闲', running: '执行中', retrying: '等待重试', error: '异常',
  };
  $('jobHealth').textContent = schedulerLabels[scheduler.state]
    || (j.running ? '执行中' : '空闲');
  if (scheduler.state === 'error') {
    $('jobDetail').textContent = scheduler.last_error || '调度器未正常运行';
  } else if (scheduler.state === 'retrying') {
    $('jobDetail').textContent = `下次重试 ${time(scheduler.next_retry_at)}`;
  } else {
    $('jobDetail').textContent = j.running ? (j.message || j.kind || '正在处理') : '无后台任务';
  }

  $('behaviorLearned').textContent = b.learned_tracks || 0;
  $('behaviorPreferred').textContent = b.preferred_tracks || 0;
  $('behaviorCooled').textContent = b.cooled_tracks || 0;
  $('behaviorActive').textContent = b.active_sessions || 0;
  const states = {disabled:'已关闭',not_connected:'未连接',verification_needed:'待验证',connected_waiting:'已连接',learning:'学习中'};
  const connection = $('behaviorConnection');
  connection.textContent = states[w.state] || states.not_connected;
  connection.dataset.state = w.state || 'not_connected';
  $('behaviorStatus').textContent = b.updated_at ? time(b.updated_at) : '暂无';
  $('managedCount').textContent = `${Object.keys(summary.managed || {}).length} 个`;
  $('dailyManaged').textContent = summary.daily_managed ? '已建立' : '未建立';
  $('lastRun').textContent = time(summary.last_run);
}

async function loadDiagnostics(force = false) {
  if (diagnosticsLoaded && !force) return;
  const response = await request('/api/daily/diagnostics');
  const payload = await response.json();
  renderDailyDiagnostics({ ...(payload.latest || {}), display: payload.display || {} });
  diagnosticsLoaded = true;
}

async function loadHistory(force = false) {
  if (historyLoaded && !force) return;
  const response = await request('/api/status/details');
  const payload = await response.json();
  renderEvents(payload.events || []);
  historyLoaded = true;
}

function stopPolling() {
  clearTimeout(timer);
  timer = null;
}

function pollingDelay() {
  return latest?.job?.running ? ACTIVE_JOB_POLL_MS : (latest?.status_refresh_ms || IDLE_POLL_MS);
}

function schedulePolling(delay = pollingDelay()) {
  stopPolling();
  if (!PCHAuth.status().authenticated || document.hidden) return;
  timer = setTimeout(pollOnce, delay);
}

async function pollOnce() {
  timer = null;
  if (!PCHAuth.status().authenticated || document.hidden) return;
  try { latest = await refresh(); } catch {}
  schedulePolling();
}

async function startPolling() {
  stopPolling();
  if (!PCHAuth.status().authenticated || document.hidden) return;
  try { latest = await refresh(); } catch (error) { note(error.message, true); }
  schedulePolling();
}

$('statusDetails').addEventListener('toggle', async () => {
  if (!$('statusDetails').open) return;
  try { await loadDiagnostics(); } catch (error) { note(error.message, true); }
});

$('eventHistory').addEventListener('toggle', async () => {
  if (!$('eventHistory').open) return;
  try { await loadHistory(); } catch (error) { note(error.message, true); }
});

$('refresh').onclick = async () => {
  if (busy) return;
  busy = true;
  diagnosticsLoaded = false;
  historyLoaded = false;
  try {
    latest = await refresh();
    const detailRequests = [];
    if ($('statusDetails').open) detailRequests.push(loadDiagnostics());
    if ($('eventHistory').open) detailRequests.push(loadHistory());
    await Promise.all(detailRequests);
    note('已刷新');
  } catch (error) {
    note(error.message, true);
  } finally {
    busy = false;
    schedulePolling();
  }
};

window.addEventListener('visibilitychange', () => {
  if (document.hidden) stopPolling();
  else startPolling();
});
window.addEventListener('pagehide', stopPolling);
window.addEventListener('pch-auth-ready', startPolling);
window.addEventListener('pch-auth-login', startPolling);
window.addEventListener('pch-auth-logout', stopPolling);
