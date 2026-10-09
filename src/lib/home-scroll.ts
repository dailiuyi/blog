/**
 * Single scroll director for homepage scrollytelling scenes.
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
  if (scenes.length === 0) return () => {};

  // If prefers-reduced-motion, freeze --p to 0 and exit director
  if (reducedMotion.matches) {
    scenes.forEach((scene) => {
      scene.style.setProperty('--p', '0');
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

  const sceneItems: SceneItem[] = scenes.map((scene) => {
    const track = scene.querySelector<HTMLElement>('.scene-track') || scene;
    return {
      scene,
      track,
      p: -1,
      isIntersecting: false,
    };
  });

  let rafId = 0;
  let isTicking = false;

  const update = () => {
    isTicking = false;
    if (document.hidden) return;

    const innerH = window.innerHeight;
    let anyIntersecting = false;

    for (const item of sceneItems) {
      if (!item.isIntersecting) continue;
      anyIntersecting = true;
      const rect = item.track.getBoundingClientRect();
      const scrollable = item.track.offsetHeight - innerH;
      const p = scrollable > 0 ? clamp(-rect.top / scrollable, 0, 1) : 0;

      if (Math.abs(p - item.p) > 0.0005) {
        item.p = p;
        item.scene.style.setProperty('--p', p.toFixed(4));
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
      }
      if (sceneItems.some((s) => s.isIntersecting)) {
        scheduleUpdate();
      }
    },
    {
      rootMargin: '100px 0px 100px 0px',
    }
  );

  sceneItems.forEach((item) => observer.observe(item.track));

  const onScrollOrResize = () => scheduleUpdate();

  window.addEventListener('scroll', onScrollOrResize, { passive: true, signal });
  window.addEventListener('resize', onScrollOrResize, { passive: true, signal });

  const onVisibilityChange = () => {
    if (!document.hidden && sceneItems.some((s) => s.isIntersecting)) {
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
