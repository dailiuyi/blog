const clamp = (value: number) => Math.min(1, Math.max(0, value));
const smooth = (value: number) => { const p = clamp(value); return p * p * (3 - 2 * p); };

/** Pure geometry: the untransformed block is the measurement and hit-test layer. */
export function rollPose(top: number, height: number, viewport: number, compact = false) {
  const progress = clamp((viewport - top) / Math.max(1, viewport + height));
  const enter = smooth((.3 - progress) / .3);
  const leave = smooth((progress - .7) / .3);
  return {
    angle: compact ? -6 * enter + 6 * leave : -12 * enter + 16 * leave,
    y: compact ? 4 * enter - 4 * leave : 8 * enter - 10 * leave,
    opacity: 1 - .1 * enter - .12 * leave,
    origin: enter > 0 ? '4%' : leave > 0 ? '96%' : '50%',
  };
}
