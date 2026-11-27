/* SpiceOwl end-to-end editorial walkthrough & on-demand click-to-play video controller. Zero external IO until clicked. */
const root = document.getElementById('product-guide');
const byId = id => document.getElementById(id);
const RELEASE_VIDEO_BASE = 'https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/';
const REPO_URL = 'https://github.com/spiceowl-dev/spiceowl';

const CH4_PATHS = {
  '04a': {
    file: '04a_agent_open_workspace.mp4',
    poster: './demo-poster-04a.webp',
    title: '04a · 一句话让 AI 打开远端电路',
    aria: '播放实操演示：一句话让 AI 打开远端电路 (0分45秒)',
    alt: '实操演示预览：一句话打开远端电路，彻底告别卡顿的 Virtuoso',
    howtoLabel: '具体操作方法（方式 A · AI 驱动）：',
    howtoText: '将对话权限切至「满血全权」→ 用自然语言说出电路关键词 → 观察 AI 检索远端 cds.lib 并将完整 Spectre 网表与 Maestro 配置自动填满右侧工作台。',
  },
  '04b': {
    file: '04b_manual_library_manager.mp4',
    poster: './demo-poster-04b.webp',
    title: '04b · 网页只读远程 Library Manager',
    aria: '播放实操演示：网页只读远程 Library Manager (0分42秒)',
    alt: '实操演示预览：想自己挑电路？网页里也有轻快只读的 Library Manager',
    howtoLabel: '具体操作方法（方式 B · 网页只读库管理）：',
    howtoText: '打开远程 Library Manager 自动解析工程内全部 cds.lib 电路库 → 查看标注的 schematic 与 Maestro 视图 → 一键「从 Schematic 导出并导入」。',
  },
};

function hydrateRepoLinks() {
  document.querySelectorAll('[data-github-link]').forEach(link => {
    link.href = REPO_URL;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  });
}

function announce(message) {
  const status = byId('demo-live-status');
  if (status) status.textContent = message;
}

function pauseOtherVideos(current) {
  root?.querySelectorAll('video').forEach(video => {
    if (video !== current && !video.paused) video.pause();
  });
}

function mountVideo(stage, autoplay = true) {
  if (!stage) return null;
  const existing = stage.querySelector('video');
  if (existing) {
    if (autoplay) {
      pauseOtherVideos(existing);
      const playPromise = existing.play();
      if (playPromise && typeof playPromise.catch === 'function') playPromise.catch(() => {});
    }
    return existing;
  }
  const { videoFile, videoPoster, videoTitle } = stage.dataset;
  const video = document.createElement('video');
  video.controls = true;
  video.playsInline = true;
  video.preload = 'metadata';
  video.poster = videoPoster || '';
  video.setAttribute('aria-label', videoTitle || 'SpiceOwl 操作演示视频');
  video.src = RELEASE_VIDEO_BASE + videoFile;
  video.addEventListener('play', () => pauseOtherVideos(video));
  stage.replaceChildren(video);
  announce('正在播放：' + (videoTitle || videoFile));
  if (autoplay) {
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === 'function') playPromise.catch(() => {});
  }
  return video;
}

function renderPoster(stage, meta) {
  stage.dataset.videoFile = meta.file;
  stage.dataset.videoPoster = meta.poster;
  stage.dataset.videoTitle = meta.title;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'so-poster-btn';
  btn.setAttribute('aria-label', meta.aria);

  const img = document.createElement('img');
  img.src = meta.poster;
  img.width = 1280;
  img.height = 650;
  img.alt = meta.alt;

  const fab = document.createElement('span');
  fab.className = 'so-play-fab';
  fab.setAttribute('aria-hidden', 'true');
  const tri = document.createElement('span');
  tri.textContent = '▶';
  fab.append(tri);

  btn.append(img, fab);
  btn.addEventListener('click', () => mountVideo(stage, true));
  stage.replaceChildren(btn);
}

function initFullFilmBanner() {
  const toggleBtn = byId('film-toggle-btn');
  const toggleLabel = byId('film-toggle-label');
  const drawer = byId('film-drawer');
  const stage = byId('film-stage');
  if (!toggleBtn || !drawer || !stage) return;

  const openFilm = (autoplay = true) => {
    drawer.hidden = false;
    toggleBtn.setAttribute('aria-expanded', 'true');
    if (toggleLabel) toggleLabel.textContent = '收起完整宣传片';
    mountVideo(stage, autoplay);
  };

  const closeFilm = () => {
    const video = stage.querySelector('video');
    if (video && !video.paused) video.pause();
    drawer.hidden = true;
    toggleBtn.setAttribute('aria-expanded', 'false');
    if (toggleLabel) toggleLabel.textContent = '展开播放完整宣传片 (10:45)';
  };

  toggleBtn.addEventListener('click', () => {
    if (drawer.hidden) openFilm(true);
    else closeFilm();
  });

  root.querySelectorAll('[data-open-film]').forEach(trigger => {
    trigger.addEventListener('click', event => {
      event.preventDefault();
      byId('so-videos')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      openFilm(true);
    });
  });
}

function initChapter04Tabs() {
  const tabs = [...root.querySelectorAll('[data-ch4-tab]')];
  const stage = byId('ch4-stage');
  const panel = byId('panel-04');
  const howto = byId('ch4-howto');
  if (!tabs.length || !stage) return null;

  const activate = (key, focusTab = false) => {
    const meta = CH4_PATHS[key];
    if (!meta) return;
    const activeVideo = stage.querySelector('video');
    if (activeVideo && !activeVideo.paused) activeVideo.pause();

    tabs.forEach(tab => {
      const selected = tab.dataset.ch4Tab === key;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && panel) panel.setAttribute('aria-labelledby', tab.id);
      if (selected && focusTab) tab.focus();
    });

    root.querySelectorAll('[data-ch4-note]').forEach(note => {
      note.classList.toggle('is-active', note.dataset.ch4Note === key);
    });

    renderPoster(stage, meta);
    if (howto) {
      const strong = document.createElement('strong');
      strong.textContent = meta.howtoLabel;
      howto.replaceChildren(strong, document.createTextNode(meta.howtoText));
    }
    announce('已切换电路打开演示：' + meta.title);
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab.dataset.ch4Tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        activate(tabs[next].dataset.ch4Tab, true);
      }
    });
  });

  return activate;
}

function initChapterFrames() {
  root.querySelectorAll('.so-chapter').forEach(chapter => {
    const stage = chapter.querySelector('.so-demo-stage');
    const posterBtn = stage?.querySelector('.so-poster-btn');
    const playChip = chapter.querySelector('[data-play-trigger]');

    posterBtn?.addEventListener('click', () => mountVideo(stage, true));
    playChip?.addEventListener('click', () => mountVideo(stage, true));
  });
}

function initTocSpy() {
  const tocLinks = [...root.querySelectorAll('[data-toc-link]')];
  const chapters = [...root.querySelectorAll('.so-chapter[id]')];
  if (!tocLinks.length || !chapters.length || typeof IntersectionObserver === 'undefined') return;

  const setCurrent = id => {
    tocLinks.forEach(link => {
      link.classList.toggle('is-current', link.dataset.tocLink === id);
    });
  };

  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(e => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
    if (visible[0]?.target?.id) setCurrent(visible[0].target.id);
  }, { rootMargin: '-18% 0px -58% 0px', threshold: [0.15, 0.4] });

  chapters.forEach(ch => observer.observe(ch));
}

function initHashNavigation(activateCh4) {
  const handleHash = (smooth = false) => {
    const id = location.hash.slice(1);
    root.querySelectorAll('.so-chapter.is-target').forEach(el => el.classList.remove('is-target'));
    if (!id) return;
    const behavior = smooth ? 'smooth' : 'auto';
    if (id === 'video-04b') {
      activateCh4?.('04b');
      const ch4 = byId('video-04a');
      ch4?.classList.add('is-target');
      ch4?.scrollIntoView({ behavior, block: 'center' });
      return;
    }
    if (id === 'video-04a') activateCh4?.('04a');
    const target = byId(id);
    if (target?.classList.contains('so-chapter')) {
      target.classList.add('is-target');
      target.scrollIntoView({ behavior, block: 'center' });
    }
  };
  handleHash(false);
  window.addEventListener('hashchange', () => handleHash(true));
}

hydrateRepoLinks();
if (root) {
  initFullFilmBanner();
  initChapterFrames();
  const activateCh4 = initChapter04Tabs();
  initTocSpy();
  initHashNavigation(activateCh4);
}
