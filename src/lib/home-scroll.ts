/**
 * Single scroll director for homepage scrollytelling scenes and document-flow depth.
 * Governed by HOME_SCROLL_STORYTELLING.md
 */

export interface HomeScrollOptions {
  root?: HTMLElement;
  signal?: AbortSignal;
}

export function initHomeScroll(options: HomeScrollOptions = {}): () => void {
  const root = options.root ?? document.querySelector<HTMLElement>('[data-acg-root]');
  if (!root) return () => {};

  const signal = options.signal;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

  const scenes = Array.from(root.querySelectorAll<HTMLElement>('.home-scene[data-scene]'));
  const depthElements = Array.from(root.querySelectorAll<HTMLElement>('[data-editorial-depth], .about'));
  if (scenes.length === 0 && depthElements.length === 0) return () => {};

  // If prefers-reduced-motion, freeze --p to 0 and --enter to 1, then exit director
  if (reducedMotion.matches) {
    scenes.forEach((scene) => {
      scene.style.setProperty('--p', '0');
    });
    depthElements.forEach((el) => {
      el.style.setProperty('--enter', '1');
    });
    return () => {};
  }

  const clamp = (val: number, min = 0, max = 1) => Math.min(max, Math.max(min, val));

  interface SceneItem {
    scene: HTMLElement;
    track: HTMLElement;
    p: number;
    isIntersecting: boolean;
  }

  interface DepthItem {
    el: HTMLElement;
    enter: number;
    isIntersecting: boolean;
  }

  const sceneItems: SceneItem[] = scenes.map((scene) => {
    const track = scene.querySelector<HTMLElement>('.scene-track') || scene;
    return {
      scene,
      track,
      p: -1,
      isIntersecting: false,
    };
  });

  const depthItems: DepthItem[] = depthElements.map((el) => ({
    el,
    enter: -1,
    isIntersecting: false,
  }));

  let rafId = 0;
  let isTicking = false;

  const update = () => {
    isTicking = false;
    if (document.hidden) return;

    const innerH = window.innerHeight;
    let anyIntersecting = false;

    // 1. Sticky scenes --p
    for (const item of sceneItems) {
      if (!item.isIntersecting) continue;
      anyIntersecting = true;
      const rect = item.track.getBoundingClientRect();
      const scrollable = item.track.offsetHeight - innerH;
      const p = scrollable > 0 ? clamp(-rect.top / scrollable, 0, 1) : 0;

      if (Math.abs(p - item.p) > 0.0005) {
        item.p = p;
        item.scene.style.setProperty('--p', p.toFixed(4));
        item.scene.dispatchEvent(new CustomEvent('scene-progress', { detail: { p } }));
      }
    }

    // 2. Document flow --enter
    for (const item of depthItems) {
      if (!item.isIntersecting) continue;
      anyIntersecting = true;
      const rect = item.el.getBoundingClientRect();
      const startY = innerH * 0.85;
      const endY = innerH * 0.65;
      const enter = clamp((startY - rect.top) / (startY - endY), 0, 1);

      if (Math.abs(enter - item.enter) > 0.005) {
        item.enter = enter;
        item.el.style.setProperty('--enter', enter.toFixed(3));
      }
    }

    if (anyIntersecting && !document.hidden) {
      rafId = requestAnimationFrame(update);
      isTicking = true;
    }
  };

  const scheduleUpdate = () => {
    if (!isTicking && !document.hidden) {
      isTicking = true;
      rafId = requestAnimationFrame(update);
    }
  };

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const item = sceneItems.find((s) => s.track === entry.target);
        if (item) {
          item.isIntersecting = entry.isIntersecting;
        }
        const depth = depthItems.find((d) => d.el === entry.target);
        if (depth) {
          depth.isIntersecting = entry.isIntersecting;
        }
      }
      if (sceneItems.some((s) => s.isIntersecting) || depthItems.some((d) => d.isIntersecting)) {
        scheduleUpdate();
      }
    },
    {
      rootMargin: '100px 0px 100px 0px',
    }
  );

  sceneItems.forEach((item) => observer.observe(item.track));
  depthItems.forEach((item) => observer.observe(item.el));

  const onScrollOrResize = () => scheduleUpdate();

  window.addEventListener('scroll', onScrollOrResize, { passive: true, signal });
  window.addEventListener('resize', onScrollOrResize, { passive: true, signal });

  const onVisibilityChange = () => {
    if (!document.hidden && (sceneItems.some((s) => s.isIntersecting) || depthItems.some((d) => d.isIntersecting))) {
      scheduleUpdate();
    } else if (document.hidden && rafId) {
      cancelAnimationFrame(rafId);
      isTicking = false;
    }
  };
  document.addEventListener('visibilitychange', onVisibilityChange, { signal });

  const cleanup = () => {
    observer.disconnect();
    if (rafId) cancelAnimationFrame(rafId);
    isTicking = false;
  };

  signal?.addEventListener('abort', cleanup);

  // Initial update
  scheduleUpdate();

  return cleanup;
}

export function scrollSceneTo(scene: HTMLElement, targetP: number, smooth = true): void {
  const track = scene.querySelector<HTMLElement>('.scene-track') || scene;
  const scrollable = track.offsetHeight - window.innerHeight;
  if (scrollable <= 0) return;
  const trackTop = track.getBoundingClientRect().top + window.scrollY;
  const targetScrollY = trackTop + Math.max(0, Math.min(1, targetP)) * scrollable;
  window.scrollTo({
    top: targetScrollY,
    behavior: smooth ? 'smooth' : 'auto',
  });
}
