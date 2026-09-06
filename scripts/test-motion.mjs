import assert from 'node:assert/strict';
import test from 'node:test';
import { rollPose } from '../src/lib/motion-math.ts';

test('scrolling either direction returns exactly to the same pose', () => {
  const positions = [1200, 900, 650, 100, -200, -650];
  const forward = positions.map(top => rollPose(top, 300, 900));
  const backward = [...positions].reverse().map(top => rollPose(top, 300, 900)).reverse();
  assert.deepEqual(forward, backward);
});

test('content stays flat and fully legible in the middle of the viewport', () => {
  for (const height of [80, 300, 900, 1800]) {
    for (const compact of [false, true]) {
      const pose = rollPose((900 - height) / 2, height, 900, compact);
      assert.equal(pose.angle, 0);
      assert.equal(pose.y, 0);
      assert.equal(pose.opacity, 1);
    }
  }
});

test('all sizes respect desktop and touch movement and contrast budgets', () => {
  for (const compact of [false, true]) {
    for (const height of [0, 80, 300, 900, 2400]) {
      for (let top = -3000; top <= 2000; top += 5) {
        const pose = rollPose(top, height, 900, compact);
        assert.ok(pose.angle >= (compact ? -6 : -12) && pose.angle <= (compact ? 6 : 16));
        assert.ok(Math.abs(pose.y) <= (compact ? 4 : 10));
        assert.ok(pose.opacity >= .88 && pose.opacity <= 1);
      }
    }
  }
});

test('enter and exit remain continuous for rapid reversals and tall blocks', () => {
  for (const height of [80, 300, 1600]) {
    for (let top = -1800; top <= 1100; top += 1) {
      assert.ok(Math.abs(rollPose(top, height, 900).angle - rollPose(top + 1, height, 900).angle) < .1);
    }
  }
});
