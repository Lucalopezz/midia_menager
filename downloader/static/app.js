const $ = (selector) => document.querySelector(selector);
const activeStatuses = new Set(['preparing', 'downloading', 'processing']);
const state = { jobs: [], filter: 'all', submitting: false, connected: false };
const rows = new Map();
let toastTimer;

function icon(name) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  element.append(use);
  element.setAttribute('aria-hidden', 'true');
  return element;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function bytes(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = value ? Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1) : 0;
  return `${(value / (1024 ** index)).toLocaleString('pt-BR', { maximumFractionDigits: index > 0 ? 1 : 0 })} ${units[index]}`;
}

function duration(seconds) {
  if (!Number.isFinite(seconds)) return 'Calculando tempo…';
  if (seconds < 60) return `${Math.ceil(seconds)} s restantes`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min restantes`;
  return `${Math.floor(seconds / 3600)} h ${Math.ceil((seconds % 3600) / 60)} min restantes`;
}

function toast(message, error = false) {
  const element = $('#toast');
  clearTimeout(toastTimer);
  element.classList.toggle('error', error);
  element.querySelector('span').textContent = message;
  element.hidden = false;
  toastTimer = setTimeout(() => { element.hidden = true; }, 4500);
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(12000), headers: { 'Content-Type': 'application/json', ...options.headers } });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Confira os links e tente novamente.');
  return data;
}

function connection(connected) {
  state.connected = connected;
  document.body.classList.toggle('connected', connected);
  document.body.classList.toggle('disconnected', !connected);
  $('#connection').lastChild.textContent = connected ? 'Servidor online' : 'Reconectando…';
  $('#sidebar-connection').textContent = connected ? 'Tudo funcionando' : 'Tentando reconectar…';
  updateForm();
}

function inputUrls() {
  return $('#urls').value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

function updateForm() {
  const count = inputUrls().length;
  $('#url-count').textContent = `${count} ${count === 1 ? 'link' : 'links'}`;
  $('#destination').textContent = `library/youtube/${$('#category').value.trim().replace(/\/$/, '')}`;
  $('#submit-button').disabled = !count || state.submitting || !state.connected;
  $('#submit-label').textContent = state.submitting ? 'Adicionando…' : 'Adicionar à fila';
}

function statusLabel(status) {
  return { queued: 'Na fila', preparing: 'Preparando', downloading: 'Baixando', processing: 'Finalizando', completed: 'Concluído', failed: 'Falhou' }[status] || status;
}

function buildRow(job) {
  const row = node('article', `job ${job.status}`);
  row.dataset.jobId = job.id;
  const top = node('div', 'job-top');
  const cover = node('div', 'job-cover');
  cover.append(icon(job.status === 'failed' ? 'alert' : job.status === 'completed' ? 'check' : 'play'));
  const info = node('div', 'job-info');
  let urlLabel = job.url;
  try { urlLabel = new URL(job.url).hostname.replace(/^www\./, ''); } catch { /* Validado no servidor. */ }
  const title = node('h3', 'job-title', job.title || job.url);
  title.title = job.title || job.url;
  const source = node('a', 'job-url', `${urlLabel} · ${job.category || 'youtube'}`);
  source.href = job.url;
  source.target = '_blank';
  source.rel = 'noreferrer';
  source.title = job.url;
  info.append(title, source);
  const status = node('span', 'status-pill', statusLabel(job.status));
  top.append(cover, info, status);
  row.append(top);

  if (activeStatuses.has(job.status)) {
    const progress = node('div', 'job-progress');
    const label = node('div', 'progress-label');
    const playlist = job.playlist_index ? ` · Vídeo ${job.playlist_index}${job.playlist_count ? ` de ${job.playlist_count}` : ''}` : '';
    const determinate = job.status === 'downloading' && Number.isFinite(job.percent);
    label.append(node('span', '', `${job.detail}${playlist}`), node('span', 'percent', determinate ? `${Math.floor(job.percent)}%` : ''));
    const track = node('div', `progress-track${determinate ? '' : ' indeterminate'}`);
    track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-label', `Progresso de ${job.title || job.url}`);
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    if (determinate) track.setAttribute('aria-valuenow', String(Math.round(job.percent)));
    const bar = node('span');
    if (determinate) bar.style.width = `${job.percent}%`;
    track.append(bar);
    progress.append(label, track);
    if (job.status === 'downloading') {
      const meta = node('div', 'job-meta');
      meta.append(node('span', '', `${bytes(job.downloaded_bytes)}${job.total_bytes ? ` / ${bytes(job.total_bytes)}` : ''}`));
      if (job.speed) meta.append(node('span', '', `${bytes(job.speed)}/s`));
      meta.append(node('span', '', duration(job.eta)));
      progress.append(meta);
    }
    row.append(progress);
  } else if (job.status === 'failed') {
    const actions = node('div', 'error-actions');
    const details = node('details', 'error-details');
    details.append(node('summary', '', 'Ver detalhes do erro'), node('p', '', job.error || 'Falha desconhecida. Tente novamente.'));
    const retry = node('button', 'retry-button', 'Tentar de novo');
    retry.type = 'button';
    retry.prepend(icon('refresh'));
    retry.addEventListener('click', async () => {
      retry.disabled = true;
      try {
        await api(`/api/jobs/${job.id}/retry`, { method: 'POST' });
        toast('Download adicionado à fila novamente.');
        await refresh();
      } catch (error) {
        toast(error.message, true);
        retry.disabled = false;
      }
    });
    actions.append(details, retry);
    row.append(actions);
  } else {
    row.append(node('p', 'job-detail', job.detail));
  }
  return row;
}

function renderJobs() {
  const list = $('#job-list');
  const visible = state.jobs.filter((job) => state.filter === 'all' || (state.filter === 'active' ? activeStatuses.has(job.status) || job.status === 'queued' : job.status === state.filter));
  const ids = new Set(visible.map((job) => job.id));
  for (const [id, entry] of rows) {
    if (!ids.has(id)) { entry.element.remove(); rows.delete(id); }
  }
  visible.forEach((job, index) => {
    const signature = JSON.stringify(job);
    let entry = rows.get(job.id);
    if (!entry || entry.signature !== signature) {
      const element = buildRow(job);
      if (entry) entry.element.replaceWith(element);
      entry = { element, signature };
      rows.set(job.id, entry);
    }
    if (list.children[index] !== entry.element) list.insertBefore(entry.element, list.children[index] || null);
  });
  $('#empty-state').hidden = visible.length > 0;
  const messages = {
    all: ['Sua biblioteca começa com um link.', 'Adicione seus vídeos favoritos e acompanhe cada download por aqui.'],
    active: ['Tudo tranquilo por aqui.', 'Nenhum download em andamento. Adicione novos links quando quiser.'],
    completed: ['O primeiro play está a caminho.', 'Seus downloads concluídos vão aparecer aqui.'],
    failed: ['Nenhuma falha por aqui.', 'Quando um download precisar de atenção, ele aparece nesta aba.'],
  };
  $('#empty-title').textContent = messages[state.filter][0];
  $('#empty-copy').textContent = messages[state.filter][1];
}

async function refresh() {
  try {
    const data = await api('/api/status');
    connection(true);
    state.jobs = data.jobs;
    $('#count-queued').textContent = data.counts.queued;
    $('#count-active').textContent = data.counts.active;
    $('#count-completed').textContent = data.counts.completed;
    $('#active-caption').textContent = data.counts.active ? 'A biblioteca está crescendo.' : 'Pronto para começar.';
    $('#queue-total').textContent = Object.values(data.counts).reduce((sum, count) => sum + count, 0);
    $('#failed-badge').hidden = !data.counts.failed;
    $('#failed-badge').textContent = data.counts.failed;
    $('#storage-free').textContent = bytes(data.storage.free);
    $('#storage-bar').style.width = `${100 * (1 - data.storage.free / data.storage.total)}%`;
    $('#history-note').textContent = data.counts.completed + data.counts.failed > 100 ? 'Últimos 100 finalizados' : '';
    renderJobs();
  } catch {
    connection(false);
  }
}

$('#download-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.submitting || !state.connected) return;
  const errorElement = $('#form-error');
  errorElement.hidden = true;
  const urls = inputUrls();
  try {
    if (urls.length > 100) throw new Error('Adicione até 100 links por vez.');
    for (const url of urls) {
      let parsed;
      try { parsed = new URL(url); } catch { throw new Error('Confira os links: use uma URL completa por linha.'); }
      if (!['http:', 'https:'].includes(parsed.protocol) || /\s/.test(url) || parsed.username || parsed.password) throw new Error('Use links que comecem com http:// ou https://, uma URL por linha.');
    }
    state.submitting = true;
    updateForm();
    const result = await api('/api/jobs', { method: 'POST', body: JSON.stringify({ urls, category: $('#category').value.trim() || null }) });
    $('#urls').value = '';
    selectFilter('all');
    toast(result.added ? `${result.added} ${result.added === 1 ? 'link adicionado' : 'links adicionados'} à fila.` : 'Estes links já estão na fila.');
    await refresh();
  } catch (error) {
    errorElement.textContent = error.message;
    errorElement.hidden = false;
  } finally {
    state.submitting = false;
    updateForm();
  }
});

function selectFilter(filter) {
  state.filter = filter;
  document.querySelectorAll('.tab').forEach((tab) => {
    const active = tab.dataset.filter === filter;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    if (active) $('#job-list').setAttribute('aria-labelledby', tab.id);
  });
  renderJobs();
}

document.querySelectorAll('.tab').forEach((tab, index, tabs) => {
  tab.addEventListener('click', () => selectFilter(tab.dataset.filter));
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    selectFilter(tabs[next].dataset.filter);
    tabs[next].focus();
  });
});

$('#urls').addEventListener('input', updateForm);
$('#category').addEventListener('input', updateForm);
$('#download-form').addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !$('#submit-button').disabled) {
    event.preventDefault();
    $('#download-form').requestSubmit();
  }
});

document.querySelectorAll('.jellyfin-link').forEach((link) => {
  const url = new URL(window.location.href);
  url.port = '8096';
  url.pathname = '/';
  url.search = '';
  url.hash = '';
  link.href = url.href;
});

async function poll() {
  await refresh();
  setTimeout(poll, document.hidden ? 5000 : 1000);
}
updateForm();
poll();
