import { rollPose } from './motion-math';

/** One event-driven director per formal page; no continuous idle animation loop. */
export function bindSiteMotion() {
  const abort = new AbortController();
  const { signal } = abort;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const compact = matchMedia('(max-width: 900px)');
  const fine = matchMedia('(hover: hover) and (pointer: fine)');
  const blocks = [...document.querySelectorAll<HTMLElement>('[data-motion-block]')];
  if (!blocks.length) return () => abort.abort();
  const visible = new Set<HTMLElement>();
  let frame = 0;
  let active: HTMLElement | null = null;
  let pointerX = 0;
  let pointerY = 0;
  let dirtyPointer = false;
  let settleTimer: ReturnType<typeof setTimeout> | undefined;
  const properties = ['--roll-angle', '--roll-y', '--roll-opacity', '--roll-origin', '--follow-x', '--follow-y', '--tilt-x', '--tilt-y', '--spot-x', '--spot-y'];

  const resetPointer = () => {
    active?.removeAttribute('data-motion-pointing');
    if (active) {
      for (const name of properties.slice(4)) active.style.removeProperty(name);
    }
    active = null;
    dirtyPointer = false;
  };
  const render = () => {
    frame = 0;
    if (document.hidden || reduce.matches) return;
    // Read all geometry before writing styles. No measurement includes a roll/hover transform.
    const poses = [...visible].filter(block => block.hasAttribute('data-motion-scroll')).map(block => {
      const rect = block.getBoundingClientRect();
      return { block, pose: rollPose(rect.top, rect.height, innerHeight, compact.matches) };
    });
    const pointerRect = active && dirtyPointer ? active.getBoundingClientRect() : null;
    for (const { block, pose } of poses) {
      block.style.setProperty('--roll-angle', `${pose.angle.toFixed(3)}deg`);
      block.style.setProperty('--roll-y', `${pose.y.toFixed(3)}px`);
      block.style.setProperty('--roll-opacity', pose.opacity.toFixed(4));
      block.style.setProperty('--roll-origin', pose.origin);
      block.setAttribute('data-motion-active', '');
    }
    if (active && pointerRect && !active.closest('.is-dragging')) {
      const x = Math.min(1, Math.max(0, (pointerX - pointerRect.left) / Math.max(1, pointerRect.width)));
      const y = Math.min(1, Math.max(0, (pointerY - pointerRect.top) / Math.max(1, pointerRect.height)));
      active.style.setProperty('--follow-x', `${((x - .5) * 8).toFixed(2)}px`);
      active.style.setProperty('--follow-y', `${((y - .5) * 8).toFixed(2)}px`);
      active.style.setProperty('--tilt-x', `${((.5 - y) * 4).toFixed(2)}deg`);
      active.style.setProperty('--tilt-y', `${((x - .5) * 4).toFixed(2)}deg`);
      active.style.setProperty('--spot-x', `${(x * 100).toFixed(1)}%`);
      active.style.setProperty('--spot-y', `${(y * 100).toFixed(1)}%`);
      dirtyPointer = false;
    }
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => {
      for (const block of blocks) block.removeAttribute('data-motion-active');
    }, 180);
  };
  const schedule = () => {
    if (!frame && !reduce.matches && !document.hidden) frame = requestAnimationFrame(render);
  };
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const block = entry.target as HTMLElement;
      if (entry.isIntersecting) visible.add(block);
      else visible.delete(block);
    }
    schedule();
  }, { rootMargin: '160px 0px' });
  const resize = new ResizeObserver(schedule);
  for (const block of blocks) {
    observer.observe(block);
    resize.observe(block);
  }
  resize.observe(document.body);
  const reset = () => {
    resetPointer();
    for (const block of blocks) {
      properties.forEach(name => block.style.removeProperty(name));
      block.removeAttribute('data-motion-active');
    }
    if (reduce.matches) {
      cancelAnimationFrame(frame);
      frame = 0;
    } else schedule();
  };
  document.addEventListener('pointermove', event => {
    if (!fine.matches || reduce.matches || event.pointerType === 'touch') return;
    const target = event.target instanceof Element ? event.target : null;
    const block = target?.closest<HTMLElement>('[data-motion-follow]') ?? null;
    if (block !== active) resetPointer();
    if (!block || event.buttons || block.closest('.is-dragging')) return;
    active = block;
    block.setAttribute('data-motion-pointing', '');
    pointerX = event.clientX;
    pointerY = event.clientY;
    dirtyPointer = true;
    schedule();
  }, { passive: true, signal });
  document.addEventListener('pointerout', event => {
    if (active && (!(event.relatedTarget instanceof Node) || !active.contains(event.relatedTarget))) resetPointer();
  }, { passive: true, signal });
  document.addEventListener('pointerdown', resetPointer, { passive: true, signal });
  window.addEventListener('blur', resetPointer, { signal });
  window.addEventListener('scroll', () => { resetPointer(); schedule(); }, { passive: true, signal });
  window.addEventListener('resize', schedule, { passive: true, signal });
  window.addEventListener('pageshow', schedule, { signal });
  document.addEventListener('load', schedule, { capture: true, signal });
  document.addEventListener('visibilitychange', () => {
    resetPointer();
    if (document.hidden) { cancelAnimationFrame(frame); frame = 0; }
    else schedule();
  }, { signal });
  for (const media of [reduce, fine, compact]) media.addEventListener('change', reset, { signal });
  document.fonts.ready.then(() => { if (!signal.aborted) schedule(); });
  schedule();
  return () => {
    abort.abort();
    observer.disconnect();
    resize.disconnect();
    cancelAnimationFrame(frame);
    clearTimeout(settleTimer);
    resetPointer();
  };
}
