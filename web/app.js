'use strict';

/* --------------------------------------------------------------------------
   光筛 LumaCull — front-end controller
   Keyboard contract: ←/→ browse · ↓ trash · ↑ undo
   -------------------------------------------------------------------------- */

const $ = (id) => document.getElementById(id);
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class ApiError extends Error {
  constructor(message, code = 'error', status = 0) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

const state = {
  token: '',
  ready: false,
  view: 'welcome',
  session: null,
  folder: '',
  images: [],
  index: 0,
  undoCount: 0,
  busy: false,
  modalOpen: false,
  infoOpen: true,
  meta: null,
  metaSeq: 0,
  photoSeq: 0,
  activeLayer: 0,
  zoom: 1,
  panX: 0,
  panY: 0,
  drag: null,
  prefetchTimer: 0,
  cache: new Map(),
  lastFocus: null,
};

const el = {};

function cacheDom() {
  el.app = $('app');
  el.boot = $('boot');
  el.bootStatus = $('bootStatus');
  el.bootLine = document.querySelector('.boot-line');
  el.brand = $('brand');
  el.folderChip = $('folderChip');
  el.folderName = $('folderName');
  el.folderPath = $('folderPath');
  el.topMeta = $('topMeta');
  el.undoBtn = $('undoBtn');
  el.undoCount = $('undoCount');
  el.deleteBtn = $('deleteBtn');
  el.rescanBtn = $('rescanBtn');
  el.infoBtn = $('infoBtn');
  el.helpBtn = $('helpBtn');

  el.welcome = $('welcome');
  el.welcomeOpen = $('welcomeOpen');
  el.welcomeHelp = $('welcomeHelp');
  el.recentBlock = $('recentBlock');
  el.recentList = $('recentList');

  el.workspace = $('workspace');
  el.stage = $('stage');
  el.canvas = $('canvas');
  el.photoFrame = $('photoFrame');
  el.photoLayerA = $('photoLayerA');
  el.photoLayerB = $('photoLayerB');
  el.photoLoading = $('photoLoading');
  el.imageError = $('imageError');
  el.imageErrorDetail = $('imageErrorDetail');
  el.prevBtn = $('prevBtn');
  el.nextBtn = $('nextBtn');
  el.frameIndex = $('frameIndex');
  el.frameTotal = $('frameTotal');
  el.stageFilename = $('stageFilename');
  el.stageSub = $('stageSub');
  el.formatBadge = $('formatBadge');
  el.stageFileMeta = $('stageFileMeta');
  el.zoomOut = $('zoomOut');
  el.zoomIn = $('zoomIn');
  el.zoomLabel = $('zoomLabel');
  el.zoomFit = $('zoomFit');
  el.stageHint = $('stageHint');

  el.filmstrip = $('filmstrip');
  el.filmstripEmpty = $('filmstripEmpty');
  el.filmCount = $('filmCount');

  el.finished = $('finished');
  el.finishedTitle = $('finishedTitle');
  el.finishedEyebrow = $('finishedEyebrow');
  el.finishedIconUse = $('finishedIconUse');
  el.finishedCopy = $('finishedCopy');
  el.finishedUndo = $('finishedUndo');
  el.finishedOpen = $('finishedOpen');
  el.finishedHint = document.querySelector('.finished-hint');

  el.inspector = $('inspector');
  el.infoState = $('infoState');
  el.infoName = $('infoName');
  el.infoBadges = $('infoBadges');
  el.exifSection = $('exifSection');
  el.specGrid = document.querySelector('#exifSection .spec-grid');
  el.infoCamera = $('infoCamera');
  el.infoLens = $('infoLens');
  el.infoAperture = $('infoAperture');
  el.infoShutter = $('infoShutter');
  el.infoIso = $('infoIso');
  el.infoFocal = $('infoFocal');
  el.infoTaken = $('infoTaken');
  el.noExif = $('noExif');
  el.infoDimensions = $('infoDimensions');
  el.infoSize = $('infoSize');
  el.infoFormat = $('infoFormat');
  el.infoModified = $('infoModified');
  el.revealBtn = $('revealBtn');
  el.allShortcutsBtn = $('allShortcutsBtn');

  el.helpModal = $('helpModal');
  el.helpClose = $('helpClose');
  el.helpDone = $('helpDone');
  el.toastLayer = $('toastLayer');
  el.live = $('live');

  el.layers = [el.photoLayerA, el.photoLayerB];
}

/* ------------------------------------------------------------------ utils */

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

function badge(text) {
  const node = document.createElement('span');
  node.className = 'info-badge';
  node.textContent = text;
  return node;
}

function pad2(value) {
  return String(value).padStart(2, '0');
}

function basename(path) {
  if (!path) return '';
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function aspectLabel(width, height) {
  if (!width || !height) return null;
  const ratio = width / height;
  const known = [
    [1.5, '3:2'],
    [4 / 3, '4:3'],
    [16 / 9, '16:9'],
    [5 / 4, '5:4'],
    [1, '1:1'],
    [2 / 3, '2:3'],
    [3 / 4, '3:4'],
    [9 / 16, '9:16'],
  ];
  for (const [value, label] of known) {
    if (Math.abs(ratio - value) < 0.02) return label;
  }
  return `${ratio.toFixed(2)}:1`;
}

function readStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* Storage is a convenience, never a requirement. */
  }
}

function announce(message) {
  if (!el.live) return;
  el.live.textContent = '';
  window.setTimeout(() => {
    el.live.textContent = message;
  }, 20);
}

function pulse(node) {
  if (!node) return;
  node.classList.remove('pulse-once');
  void node.offsetWidth;
  node.classList.add('pulse-once');
  window.setTimeout(() => node.classList.remove('pulse-once'), 450);
}

/* -------------------------------------------------------------------- api */

async function api(path, { method = 'GET', body } = {}) {
  const headers = {
    'X-Luma-Token': state.token,
    Accept: 'application/json',
  };
  const init = { method, headers, cache: 'no-store' };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError('无法连接光筛服务，请重新启动应用。', 'network', 0);
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok || !payload || payload.ok === false) {
    throw new ApiError(
      payload?.error || `请求失败（${response.status}）`,
      payload?.code || 'http',
      response.status,
    );
  }
  return payload;
}

function thumbUrl(item) {
  const token = encodeURIComponent(state.token);
  return `/media/thumb/${encodeURIComponent(item.id)}?k=${token}&m=${item.m}`;
}

function previewUrl(item) {
  const token = encodeURIComponent(state.token);
  return `/media/preview/${encodeURIComponent(item.id)}?k=${token}&m=${item.m}`;
}

/* ------------------------------------------------------------------ boot */

function showBoot(message) {
  el.bootStatus.classList.remove('is-error');
  el.bootStatus.textContent = message;
  el.bootLine.hidden = false;
  el.boot.classList.remove('is-done');
}

function bootError(message) {
  el.bootStatus.textContent = message;
  el.bootStatus.classList.add('is-error');
  el.bootLine.hidden = true;
  el.boot.classList.remove('is-done');
}

function hideBoot() {
  el.boot.classList.add('is-done');
}

/* ----------------------------------------------------------------- views */

function setView(view) {
  state.view = view;
  el.app.dataset.view = view;
  el.welcome.hidden = view !== 'welcome';
  el.workspace.hidden = view !== 'gallery';
  el.finished.hidden = view !== 'finished';
}

function showWelcome() {
  state.session = null;
  state.folder = '';
  state.images = [];
  state.index = 0;
  state.undoCount = 0;
  state.meta = null;
  setView('welcome');
  updateChrome();
  document.title = '光筛 LumaCull · 摄影师选片台';
}

function showFinished() {
  setView('finished');
  resetZoom();
  const undone = state.undoCount > 0;
  const hasFolder = Boolean(state.session);
  const isComplete = hasFolder && state.images.length === 0 && undone;
  el.finishedEyebrow.textContent = isComplete ? 'SESSION COMPLETE' : 'AWAITING PHOTOS';
  el.finishedIconUse.setAttribute('href', isComplete ? '#i-check' : '#i-image');
  if (!hasFolder) {
    el.finishedTitle.textContent = '这里还没有照片。';
    el.finishedCopy.textContent =
      '选择一组照片开始筛选。支持 JPEG、PNG、WebP、TIFF 与 HEIC。';
  } else if (isComplete) {
    el.finishedTitle.textContent = '这一组已经筛完了。';
    el.finishedCopy.textContent =
      '留下的照片都还在原文件夹里。删除的照片保存在应用回收站，随时可以撤回。';
  } else {
    el.finishedTitle.textContent = '这个文件夹里还没有照片。';
    el.finishedCopy.textContent =
      '把 JPEG、PNG、WebP、TIFF 或 HEIC 照片放进这个文件夹，然后按 R 重新扫描。';
  }
  el.finishedUndo.hidden = !undone;
  el.finishedUndo.disabled = state.busy || !undone;
  el.finishedHint.textContent = undone
    ? '也可以按 ↑ 撤回最近一张删除的照片。关闭窗口不会清空回收站。'
    : '照片只在本机读取 · 不会上传任何内容';
  updateChrome();
  announce(el.finishedTitle.textContent);
}

/* ------------------------------------------------------------------ data */

function applySession(session, options = {}) {
  const sameFolder =
    Boolean(state.folder) &&
    Boolean(session.folder) &&
    state.folder.toLocaleLowerCase() === session.folder.toLocaleLowerCase();
  const previousIndex = state.index;
  const previousId = state.images[state.index]?.id;

  state.session = session;
  state.folder = session.folder || '';
  state.folderName = session.folderName || basename(session.folder);
  state.images = Array.isArray(session.images) ? session.images : [];
  state.undoCount = Number(session.undoCount || 0);

  updateChrome();

  if (!state.images.length) {
    showFinished();
    return;
  }

  setView('gallery');
  renderFilmstrip();

  let target = 0;
  if (options.selectId) {
    const found = state.images.findIndex((item) => item.id === options.selectId);
    if (found >= 0) target = found;
    else if (Number.isInteger(options.index)) target = options.index;
  } else if (Number.isInteger(options.index)) {
    target = options.index;
  } else if (sameFolder && previousId) {
    const found = state.images.findIndex((item) => item.id === previousId);
    if (found >= 0) target = found;
    else target = Math.min(previousIndex, state.images.length - 1);
  } else {
    const remembered = readStorage(`lumacull:last:${state.folder}`);
    const found = remembered
      ? state.images.findIndex((item) => item.id === remembered)
      : -1;
    target = found >= 0 ? found : 0;
  }

  select(clamp(target, 0, state.images.length - 1), {
    force: true,
    animate: options.animate !== false,
    scrollBehavior: options.scrollBehavior || 'auto',
  });
}

function current() {
  return state.images[state.index] || null;
}

function select(index, options = {}) {
  if (!state.images.length) return;
  const next = clamp(Number(index) || 0, 0, state.images.length - 1);
  if (!options.force && next === state.index && state.meta) {
    updateActiveThumb(options.scrollBehavior || 'smooth');
    return;
  }
  const item = state.images[next];
  if (!item) return;

  state.index = next;
  state.meta = null;
  resetZoom();
  updateStage(item, next);
  updateActiveThumb(options.scrollBehavior || 'smooth');
  updateChrome();
  saveLastViewed(item);
  loadPhoto(item, options.animate !== false);
  loadMeta(item);
  prefetchNeighbours(item);
  document.title = `${item.name} · 光筛 LumaCull`;
}

function saveLastViewed(item) {
  if (!state.folder || !item) return;
  writeStorage(`lumacull:last:${state.folder}`, item.id);
}

function updateStage(item, index) {
  el.frameIndex.textContent = pad2(index + 1);
  el.frameTotal.textContent = pad2(state.images.length);
  el.stageFilename.textContent = item.name;
  el.stageFilename.title = item.name;
  el.stageSub.textContent = '正在读取照片信息…';
  el.formatBadge.textContent = (item.ext || 'IMG').toUpperCase();
  el.stageFileMeta.textContent = '—';
  el.imageError.hidden = true;
  el.stage.classList.remove('is-restoring');
}

function updateActiveThumb(behavior = 'smooth', immediate = false) {
  const nodes = el.filmstrip.querySelectorAll('.thumb');
  let activeNode = null;
  nodes.forEach((node) => {
    const isActive = Number(node.dataset.index) === state.index;
    node.classList.toggle('is-active', isActive);
    node.setAttribute('aria-selected', isActive ? 'true' : 'false');
    if (isActive) activeNode = node;
  });
  if (activeNode) {
    const scroll = () => {
      const strip = el.filmstrip;
      const targetLeft =
        activeNode.offsetLeft - (strip.clientWidth - activeNode.offsetWidth) / 2;
      const max = Math.max(0, strip.scrollWidth - strip.clientWidth);
      strip.scrollTo({
        left: clamp(targetLeft, 0, max),
        behavior: immediate ? 'auto' : behavior,
      });
    };
    if (immediate) scroll();
    else window.requestAnimationFrame(scroll);
  }
}

function renderFilmstrip() {
  const fragment = document.createDocumentFragment();
  state.images.forEach((item, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'thumb';
    button.dataset.index = String(index);
    button.dataset.id = item.id;
    button.setAttribute('role', 'option');
    button.setAttribute('aria-selected', 'false');
    button.setAttribute('aria-label', `第 ${index + 1} 张，${item.name}`);
    button.title = item.name;

    const image = document.createElement('img');
    image.alt = '';
    image.decoding = 'async';
    image.loading = Math.abs(index - state.index) <= 3 ? 'eager' : 'lazy';
    image.src = thumbUrl(item);
    image.draggable = false;
    image.addEventListener(
      'error',
      () => {
        image.style.visibility = 'hidden';
      },
      { once: true },
    );

    const number = document.createElement('span');
    number.className = 'thumb-index';
    number.textContent = pad2(index + 1);

    button.append(image, number);
    fragment.appendChild(button);
  });

  el.filmstrip.replaceChildren(fragment);
  el.filmCount.textContent = String(state.images.length);
  el.filmstripEmpty.hidden = state.images.length > 0;
  updateActiveThumb('auto', true);
}

/* -------------------------------------------------------------- top chrome */

function updateChrome() {
  const hasSession = Boolean(state.session && state.folder);
  const count = state.images.length;
  const isGallery = state.view === 'gallery' && count > 0;

  el.folderName.textContent = hasSession ? state.folderName : '选择照片文件夹';
  el.folderPath.textContent = hasSession ? state.folder : '把一整组照片交给光筛';
  el.folderChip.title = hasSession ? state.folder : '打开照片文件夹';
  el.topMeta.textContent = hasSession ? `${count} FRAMES` : '';

  el.undoCount.textContent = String(state.undoCount);
  el.undoCount.hidden = state.undoCount < 1;
  el.undoBtn.disabled = state.busy || !hasSession || state.undoCount < 1;
  el.deleteBtn.disabled = state.busy || !isGallery;
  el.rescanBtn.disabled = state.busy || !hasSession;
  el.infoBtn.disabled = !hasSession || count < 1;
  el.infoBtn.setAttribute('aria-pressed', state.infoOpen ? 'true' : 'false');
  el.finishedUndo.disabled = state.busy || state.undoCount < 1;
  el.finishedUndo.hidden = state.undoCount < 1;
  el.prevBtn.disabled = state.busy || !isGallery || state.index <= 0;
  el.nextBtn.disabled =
    state.busy || !isGallery || state.index >= state.images.length - 1;
  el.revealBtn.disabled = state.busy || !state.meta;
  el.app.classList.toggle('info-closed', !state.infoOpen);
}

function setBusy(value) {
  state.busy = Boolean(value);
  el.app.classList.toggle('is-busy', state.busy);
  updateChrome();
}

/* ---------------------------------------------------------------- loading */

function resetZoom() {
  state.zoom = 1;
  state.panX = 0;
  state.panY = 0;
  applyZoom();
}

function applyZoom() {
  if (!el.photoFrame) return;
  el.photoFrame.style.transform = `translate3d(${state.panX}px, ${state.panY}px, 0) scale(${state.zoom})`;
  el.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  el.canvas.classList.toggle('is-zoomed', state.zoom > 1.001);
  el.stageHint.textContent =
    state.zoom > 1.001 ? '拖动查看细节 · 0 复位' : '滚轮缩放 · 双击放大';
}

function clampPan() {
  const rect = el.canvas.getBoundingClientRect();
  const maxX = Math.max(0, (rect.width * (state.zoom - 1)) / 2);
  const maxY = Math.max(0, (rect.height * (state.zoom - 1)) / 2);
  state.panX = clamp(state.panX, -maxX, maxX);
  state.panY = clamp(state.panY, -maxY, maxY);
}

function zoomAt(nextZoom, clientX, clientY) {
  if (!current()) return;
  const previous = state.zoom;
  const next = clamp(nextZoom, 1, 4);
  if (next <= 1.001) {
    resetZoom();
    return;
  }
  const rect = el.canvas.getBoundingClientRect();
  const anchorX = (clientX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2;
  const anchorY = (clientY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2;
  const factor = next / previous;
  state.panX = anchorX - (anchorX - state.panX) * factor;
  state.panY = anchorY - (anchorY - state.panY) * factor;
  state.zoom = next;
  clampPan();
  applyZoom();
}

function loadPhoto(item, animate = true) {
  const sequence = ++state.photoSeq;
  window.clearTimeout(state.prefetchTimer);
  el.imageError.hidden = true;

  const active = el.layers[state.activeLayer];
  const hasVisible = el.layers.some((layer) => layer.classList.contains('is-visible'));
  if (!hasVisible) {
    el.photoLoading.hidden = false;
  } else {
    el.photoLoading.hidden = true;
  }

  const incoming = el.layers[1 - state.activeLayer];
  incoming.classList.remove('is-visible');
  incoming.onload = null;
  incoming.onerror = null;
  incoming.alt = item.name;

  let settled = false;
  const finish = () => {
    if (settled || sequence !== state.photoSeq) return;
    settled = true;
    const outgoing = el.layers[state.activeLayer];
    state.activeLayer = 1 - state.activeLayer;
    incoming.classList.add('is-visible');
    el.photoLoading.hidden = true;
    if (!state.meta?.error) el.imageError.hidden = true;
    window.setTimeout(() => {
      if (sequence !== state.photoSeq) return;
      outgoing.classList.remove('is-visible');
    }, animate ? 250 : 0);
  };

  const fail = () => {
    if (settled || sequence !== state.photoSeq) return;
    settled = true;
    el.photoLoading.hidden = true;
    el.layers.forEach((layer) => layer.classList.remove('is-visible'));
    showImageError('文件可能已被移动，或这个格式暂时无法解码。');
  };

  incoming.onload = finish;
  incoming.onerror = fail;

  incoming.src = previewUrl(item);
  if (incoming.complete && incoming.naturalWidth > 0) {
    finish();
  }
}

function showImageError(message) {
  el.imageError.hidden = false;
  el.imageErrorDetail.textContent = message || '文件可能已损坏或格式暂不支持';
}

function prefetchNeighbours(item) {
  window.clearTimeout(state.prefetchTimer);
  const index = state.index;
  state.prefetchTimer = window.setTimeout(() => {
    for (const offset of [-1, 1, 2]) {
      const neighbour = state.images[index + offset];
      if (!neighbour) continue;
      const image = new Image();
      image.decoding = 'async';
      image.src = previewUrl(neighbour);
    }
  }, 130);
}

/* ------------------------------------------------------------------- meta */

function resetMetaFields(name) {
  el.infoName.textContent = name || '—';
  el.infoBadges.replaceChildren();
  el.infoCamera.textContent = '—';
  el.infoLens.textContent = '';
  el.infoAperture.textContent = '—';
  el.infoShutter.textContent = '—';
  el.infoIso.textContent = '—';
  el.infoFocal.textContent = '—';
  el.infoTaken.textContent = '—';
  el.infoDimensions.textContent = '—';
  el.infoSize.textContent = '—';
  el.infoFormat.textContent = '—';
  el.infoModified.textContent = '—';
  el.noExif.hidden = true;
  el.specGrid.hidden = false;
  el.infoState.textContent = 'READING';
  el.infoBadges.append(badge('读取中'));
}

function loadMeta(item) {
  const sequence = ++state.metaSeq;
  const key = `${item.id}:${item.m}`;
  resetMetaFields(item.name);
  el.inspector.classList.add('is-loading');

  if (state.cache.has(key)) {
    const meta = state.cache.get(key);
    renderMeta(meta);
    el.inspector.classList.remove('is-loading');
    return;
  }

  api(`/api/meta/${encodeURIComponent(item.id)}`)
    .then((data) => {
      if (sequence !== state.metaSeq) return;
      state.cache.set(key, data.meta);
      if (state.cache.size > 240) {
        const oldest = state.cache.keys().next().value;
        state.cache.delete(oldest);
      }
      renderMeta(data.meta);
    })
    .catch((error) => {
      if (sequence !== state.metaSeq) return;
      el.infoState.textContent = 'ERROR';
      el.infoBadges.replaceChildren(badge('无法读取'));
      el.stageSub.textContent = '无法读取照片信息';
      el.noExif.hidden = false;
      el.noExif.textContent = error.message;
      showImageError(error.message);
    })
    .finally(() => {
      if (sequence === state.metaSeq) {
        el.inspector.classList.remove('is-loading');
      }
    });
}

function renderMeta(meta) {
  state.meta = meta;
  el.infoName.textContent = meta.name;
  el.infoName.title = meta.name;
  el.infoState.textContent = meta.error ? 'ERROR' : meta.exif ? 'EXIF' : 'FILE';

  const badges = [];
  if (meta.format) badges.push(badge(meta.format));
  if (meta.megapixels) badges.push(badge(`${meta.megapixels} MP`));
  const ratio = aspectLabel(meta.width, meta.height);
  if (ratio) badges.push(badge(ratio));
  el.infoBadges.replaceChildren(...badges);

  const exif = meta.exif || {};
  if (meta.error) {
    el.infoCamera.textContent = '照片信息不可用';
    el.infoLens.textContent = '';
  } else if (!meta.exif) {
    el.infoCamera.textContent = '这张照片没有记录 EXIF 信息';
    el.infoLens.textContent = '';
  } else {
    el.infoCamera.textContent = exif.camera || '未记录相机型号';
    el.infoLens.textContent = exif.lens || '';
  }
  el.infoAperture.textContent = exif.aperture || '—';
  el.infoShutter.textContent = exif.shutter || '—';
  el.infoIso.textContent = exif.iso || '—';
  el.infoFocal.textContent = exif.focal || '—';
  el.infoTaken.textContent = exif.takenAt || '—';
  el.noExif.hidden = true;
  el.specGrid.hidden = !meta.exif;

  el.infoDimensions.textContent =
    meta.width && meta.height ? `${meta.width} × ${meta.height}` : '—';
  el.infoSize.textContent = meta.sizeLabel || '—';
  el.infoFormat.textContent = meta.format || '—';
  el.infoModified.textContent = meta.modified || '—';

  if (meta.error) {
    el.stageSub.textContent = '无法读取这张照片';
    el.stageFileMeta.textContent = meta.format || '—';
    el.infoBadges.replaceChildren(badge('无法读取'));
    showImageError(meta.error);
  } else {
    el.formatBadge.textContent = (meta.format || '').toUpperCase() || 'IMG';
    const parts = [exif.camera, exif.focal, exif.aperture].filter(Boolean);
    el.stageSub.textContent = parts.length
      ? parts.join(' · ')
      : `${meta.width} × ${meta.height}`;
    el.stageFileMeta.textContent = `${meta.width} × ${meta.height} · ${meta.sizeLabel}`;
    el.imageError.hidden = true;
  }
  updateChrome();
}

/* ------------------------------------------------------------------ toast */

function toast(message, options = {}) {
  const {
    tone = 'neutral',
    actionLabel = '',
    onAction = null,
    duration = 3600,
    key = '',
  } = options;
  if (key) {
    el.toastLayer
      .querySelectorAll(`[data-toast-key="${key}"]`)
      .forEach((existing) => existing.remove());
  }
  const node = document.createElement('div');
  node.className = `toast toast--${tone}`;
  if (key) node.dataset.toastKey = key;
  const dot = document.createElement('span');
  dot.className = 'toast-dot';
  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = message;
  node.append(dot, text);

  let closed = false;
  let timer = 0;
  const close = () => {
    if (closed) return;
    closed = true;
    window.clearTimeout(timer);
    node.classList.remove('is-visible');
    node.classList.add('is-leaving');
    window.setTimeout(() => node.remove(), 220);
  };

  if (actionLabel && onAction) {
    const action = document.createElement('button');
    action.type = 'button';
    action.className = 'toast-action';
    action.textContent = actionLabel;
    action.addEventListener('click', () => {
      close();
      onAction();
    });
    node.appendChild(action);
  }

  el.toastLayer.appendChild(node);
  while (el.toastLayer.children.length > 3) {
    el.toastLayer.firstElementChild?.remove();
  }
  window.requestAnimationFrame(() => node.classList.add('is-visible'));
  timer = window.setTimeout(close, duration);
  node.addEventListener('mouseenter', () => window.clearTimeout(timer));
  node.addEventListener('mouseleave', () => {
    timer = window.setTimeout(close, 1600);
  });
  announce(message);
  return node;
}

/* --------------------------------------------------------------- start-up */

async function init() {
  cacheDom();
  bindEvents();
  state.infoOpen = readStorage('lumacull:info') !== '0';
  el.app.classList.toggle('info-closed', !state.infoOpen);

  const params = new URLSearchParams(window.location.search);
  state.token = params.get('k') || '';
  if (!state.token) {
    bootError('访问令牌缺失。请关闭此页，从「光筛 LumaCull」应用重新打开。');
    return;
  }

  startHeartbeat();

  try {
    const data = await api('/api/state');
    renderRecent(data.recent || []);
    if (data.session) applySession(data.session, { scrollBehavior: 'auto' });
    else showWelcome();
    state.ready = true;
    window.setTimeout(hideBoot, 180);
  } catch (error) {
    bootError(
      error instanceof ApiError && error.status === 403
        ? '访问令牌已失效。请关闭此页，重新启动「光筛 LumaCull」。'
        : error.message || '光筛启动失败，请重新打开应用。',
    );
  }
}

function startHeartbeat() {
  const beat = () => {
    fetch('/api/heartbeat', {
      method: 'POST',
      headers: { 'X-Luma-Token': state.token },
      cache: 'no-store',
      keepalive: true,
    }).catch(() => {});
  };
  beat();
  window.setInterval(beat, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) beat();
  });
  window.addEventListener(
    'pagehide',
    () => {
      try {
        navigator.sendBeacon(`/api/bye?k=${encodeURIComponent(state.token)}`);
      } catch {
        /* closing is best effort */
      }
    },
    { once: true },
  );
}

function renderRecent(entries) {
  el.recentList.replaceChildren();
  if (!Array.isArray(entries) || !entries.length) {
    el.recentBlock.hidden = true;
    return;
  }
  el.recentBlock.hidden = false;
  for (const entry of entries.slice(0, 4)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'recent-item';
    button.title = entry.path || entry.name || '';
    button.append(icon('folder'));

    const text = document.createElement('span');
    text.className = 'recent-item-text';

    const name = document.createElement('span');
    name.className = 'recent-item-name';
    name.textContent = entry.name || basename(entry.path);

    const path = document.createElement('span');
    path.className = 'recent-item-path';
    path.textContent = entry.path || '';
    text.append(name, path);

    const count = document.createElement('span');
    count.className = 'recent-item-count';
    count.textContent = `${Number(entry.count || 0)} 张`;

    button.append(text, count);
    button.addEventListener('click', () => openFolder(entry.path));
    el.recentList.appendChild(button);
  }
}

/* ---------------------------------------------------------- open / rescan */

async function openFolderUI() {
  if (state.busy) return;
  if (
    window.pywebview &&
    window.pywebview.api &&
    typeof window.pywebview.api.select_folder === 'function'
  ) {
    try {
      const path = await window.pywebview.api.select_folder(state.folder || '');
      if (path) await openFolder(path);
      return;
    } catch (error) {
      console.warn('Native folder picker failed, falling back.', error);
    }
  }
  await pickFolderFallback();
}

async function pickFolderFallback() {
  setBusy(true);
  showBoot('请选择一个照片文件夹…');
  try {
    const data = await api('/api/pick-folder', { method: 'POST' });
    if (data.cancelled) {
      hideBoot();
      return;
    }
    hideBoot();
    applySession(data.session, { scrollBehavior: 'auto' });
    toast(`已打开「${data.session.folderName}」 · ${data.session.count} 张照片`, {
      tone: 'success',
      key: 'status',
    });
  } catch (error) {
    hideBoot();
    toast(error.message || '打开文件夹失败。', { tone: 'danger' });
  } finally {
    setBusy(false);
  }
}

async function openFolder(path) {
  if (!path || state.busy) return;
  setBusy(true);
  showBoot(`正在扫描「${basename(path)}」…`);
  try {
    const data = await api('/api/open-folder', {
      method: 'POST',
      body: { path },
    });
    applySession(data.session, { scrollBehavior: 'auto' });
    hideBoot();
    toast(
      data.session.count
        ? `已打开「${data.session.folderName}」 · ${data.session.count} 张照片`
        : `已打开「${data.session.folderName}」 · 文件夹为空`,
      { tone: data.session.count ? 'success' : 'neutral', key: 'status' },
    );
  } catch (error) {
    hideBoot();
    toast(error.message || '打开文件夹失败。', { tone: 'danger' });
  } finally {
    setBusy(false);
  }
}

async function rescan() {
  if (!state.session || state.busy) return;
  const oldId = current()?.id;
  const oldIndex = state.index;
  setBusy(true);
  showBoot('正在重新扫描照片…');
  try {
    const data = await api('/api/rescan', { method: 'POST' });
    applySession(data.session, {
      selectId: oldId,
      index: oldIndex,
      animate: false,
      scrollBehavior: 'auto',
    });
    hideBoot();
    toast(`已重新扫描 · ${data.session.count} 张照片`, { tone: 'success', key: 'status' });
  } catch (error) {
    hideBoot();
    toast(error.message || '重新扫描失败。', { tone: 'danger' });
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------ delete/undo */

async function deleteCurrent() {
  if (state.busy || state.view !== 'gallery') return;
  const item = current();
  if (!item) return;
  const oldIndex = state.index;

  setBusy(true);
  el.stage.classList.add('is-leaving');

  try {
    const [data] = await Promise.all([
      api('/api/delete', { method: 'POST', body: { id: item.id } }),
      wait(165),
    ]);
    el.stage.classList.remove('is-leaving');
    const nextIndex = Math.min(oldIndex, data.session.count - 1);
    applySession(data.session, {
      index: nextIndex,
      animate: true,
      scrollBehavior: 'smooth',
    });
    toast(`已移入回收站 · ${item.name}`, {
      tone: 'danger',
      actionLabel: '撤回',
      onAction: undoLast,
      duration: 4200,
      key: 'action',
    });
  } catch (error) {
    el.stage.classList.remove('is-leaving');
    toast(error.message || '删除失败。', { tone: 'danger' });
  } finally {
    setBusy(false);
  }
}

async function undoLast() {
  if (state.busy || !state.session) return;
  if (state.undoCount < 1) {
    toast('还没有可以撤回的删除。', { duration: 2200 });
    pulse(el.undoBtn);
    return;
  }

  setBusy(true);
  try {
    const data = await api('/api/undo', { method: 'POST' });
    applySession(data.session, {
      selectId: data.restored.id,
      animate: false,
      scrollBehavior: 'smooth',
    });
    el.stage.classList.remove('is-restoring');
    void el.stage.offsetWidth;
    el.stage.classList.add('is-restoring');
    window.setTimeout(() => el.stage.classList.remove('is-restoring'), 520);
    toast(`已恢复 · ${data.restored.name}`, { tone: 'success', key: 'action' });
  } catch (error) {
    toast(error.message || '撤回失败。', { tone: 'danger' });
    if (error.code === 'undo_empty') {
      state.undoCount = Math.max(0, state.undoCount - 1);
      updateChrome();
    }
  } finally {
    setBusy(false);
  }
}

/* ---------------------------------------------------------------- navigate */

function navigate(delta) {
  if (state.busy || state.view !== 'gallery') return;
  const next = state.index + delta;
  if (next < 0 || next >= state.images.length) {
    pulse(delta < 0 ? el.prevBtn : el.nextBtn);
    return;
  }
  select(next, { scrollBehavior: 'smooth' });
}

/* ------------------------------------------------------------------- info */

function toggleInfo() {
  if (!state.session || !state.images.length) return;
  state.infoOpen = !state.infoOpen;
  writeStorage('lumacull:info', state.infoOpen ? '1' : '0');
  el.app.classList.toggle('info-closed', !state.infoOpen);
  updateChrome();
  window.requestAnimationFrame(() => {
    clampPan();
    applyZoom();
  });
}

async function revealCurrent() {
  if (!state.meta || state.busy) return;
  try {
    await api('/api/reveal', {
      method: 'POST',
      body: { id: state.meta.id },
    });
  } catch (error) {
    toast(error.message || '无法打开资源管理器。', { tone: 'danger' });
  }
}

/* ------------------------------------------------------------------ modal */

function openHelp() {
  if (state.modalOpen) return;
  state.lastFocus = document.activeElement;
  state.modalOpen = true;
  el.helpModal.hidden = false;
  window.requestAnimationFrame(() => el.helpModal.classList.add('is-open'));
  window.setTimeout(() => el.helpClose.focus(), 30);
}

function closeHelp() {
  if (!state.modalOpen) return;
  state.modalOpen = false;
  el.helpModal.classList.remove('is-open');
  window.setTimeout(() => {
    if (!state.modalOpen) el.helpModal.hidden = true;
    if (state.lastFocus && typeof state.lastFocus.focus === 'function') {
      state.lastFocus.focus();
    }
  }, 190);
}

function trapModalFocus(event) {
  if (event.key !== 'Tab' || !state.modalOpen) return;
  const focusable = el.helpModal.querySelectorAll(
    'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/* --------------------------------------------------------------- keyboard */

function onKeyDown(event) {
  if (!state.ready) return;

  if (state.modalOpen) {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeHelp();
    } else if (event.key === '?' || event.key === '/') {
      event.preventDefault();
      closeHelp();
    } else {
      trapModalFocus(event);
    }
    return;
  }

  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const tag = document.activeElement?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

  switch (event.key) {
    case 'ArrowLeft':
      event.preventDefault();
      navigate(-1);
      break;
    case 'ArrowRight':
      event.preventDefault();
      navigate(1);
      break;
    case 'ArrowDown':
      event.preventDefault();
      if (!event.repeat) deleteCurrent();
      break;
    case 'ArrowUp':
      event.preventDefault();
      if (!event.repeat) undoLast();
      break;
    case 'o':
    case 'O':
      event.preventDefault();
      openFolderUI();
      break;
    case 'r':
    case 'R':
      event.preventDefault();
      rescan();
      break;
    case 'i':
    case 'I':
      event.preventDefault();
      toggleInfo();
      break;
    case '?':
    case '/':
      event.preventDefault();
      openHelp();
      break;
    case '+':
    case '=':
      event.preventDefault();
      zoomAt(state.zoom * 1.2);
      break;
    case '-':
    case '_':
      event.preventDefault();
      zoomAt(state.zoom / 1.2);
      break;
    case '0':
      event.preventDefault();
      resetZoom();
      break;
    case 'Escape':
      if (state.zoom > 1.001) resetZoom();
      break;
    default:
      break;
  }
}

/* ------------------------------------------------------------------ events */

function bindEvents() {
  el.welcomeOpen.addEventListener('click', openFolderUI);
  el.folderChip.addEventListener('click', openFolderUI);
  el.welcomeHelp.addEventListener('click', openHelp);
  el.helpBtn.addEventListener('click', openHelp);
  el.helpClose.addEventListener('click', closeHelp);
  el.helpDone.addEventListener('click', closeHelp);
  el.helpModal.addEventListener('click', (event) => {
    if (event.target === el.helpModal) closeHelp();
  });

  el.undoBtn.addEventListener('click', undoLast);
  el.deleteBtn.addEventListener('click', deleteCurrent);
  el.rescanBtn.addEventListener('click', rescan);
  el.infoBtn.addEventListener('click', toggleInfo);
  el.prevBtn.addEventListener('click', () => navigate(-1));
  el.nextBtn.addEventListener('click', () => navigate(1));
  el.finishedOpen.addEventListener('click', openFolderUI);
  el.finishedUndo.addEventListener('click', undoLast);
  el.revealBtn.addEventListener('click', revealCurrent);
  el.allShortcutsBtn.addEventListener('click', openHelp);
  el.allShortcutsBtn.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openHelp();
    }
  });

  el.filmstrip.addEventListener('click', (event) => {
    const thumb = event.target.closest('.thumb');
    if (!thumb) return;
    const index = Number(thumb.dataset.index);
    if (!Number.isNaN(index)) select(index, { scrollBehavior: 'smooth' });
  });

  el.zoomIn.addEventListener('click', () => zoomAt(state.zoom * 1.2));
  el.zoomOut.addEventListener('click', () => zoomAt(state.zoom / 1.2));
  el.zoomFit.addEventListener('click', resetZoom);
  el.zoomLabel.addEventListener('click', resetZoom);

  el.canvas.addEventListener(
    'wheel',
    (event) => {
      if (!current() || state.busy) return;
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.0016);
      zoomAt(state.zoom * factor, event.clientX, event.clientY);
    },
    { passive: false },
  );

  el.canvas.addEventListener('dblclick', (event) => {
    if (!current() || event.target.closest('button')) return;
    if (state.zoom > 1.001) resetZoom();
    else zoomAt(2.2, event.clientX, event.clientY);
  });

  el.canvas.addEventListener('pointerdown', (event) => {
    if (state.zoom <= 1.001 || event.button !== 0 || !current()) return;
    state.drag = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: state.panX,
      panY: state.panY,
    };
    el.canvas.classList.add('is-dragging');
    el.canvas.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });

  el.canvas.addEventListener('pointermove', (event) => {
    if (!state.drag || event.pointerId !== state.drag.id) return;
    state.panX = state.drag.panX + (event.clientX - state.drag.x);
    state.panY = state.drag.panY + (event.clientY - state.drag.y);
    clampPan();
    applyZoom();
  });

  const endDrag = (event) => {
    if (!state.drag || event.pointerId !== state.drag.id) return;
    state.drag = null;
    el.canvas.classList.remove('is-dragging');
    try {
      el.canvas.releasePointerCapture?.(event.pointerId);
    } catch {
      /* pointer may already be released */
    }
  };
  el.canvas.addEventListener('pointerup', endDrag);
  el.canvas.addEventListener('pointercancel', endDrag);

  el.brand.addEventListener('click', () => {
    if (state.view === 'gallery') {
      toast('正在筛选这一组照片 · 按 O 可以换一组', { duration: 2000 });
    }
  });

  document.addEventListener('keydown', onKeyDown);
  window.addEventListener('resize', () => {
    clampPan();
    applyZoom();
  });
  window.addEventListener('pywebviewready', () => {
    document.body.dataset.native = '1';
  });
}

/* -------------------------------------------------------------------- run */

document.addEventListener('DOMContentLoaded', init);
