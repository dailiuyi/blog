import bangumiData from '../data/bangumi-cache.json';

export interface AnimeWatchItem {
  id: number;
  title: string;
  jpTitle: string;
  type: 2 | 3;
  status: '在看' | '已看完';
  progress: number;
  progressText: string;
  totalEps: number;
  epStatus: number;
  rate: number;
  comment?: string;
  tags: string[];
  meta: string;
  image: string;
  source: string;
}

/**
 * Fisher-Yates 随机洗牌算法
 */
function shuffle<T>(array: T[]): T[] {
  const result = [...array];
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

/**
 * 获取随机动画展厅列表
 * 默认挑选 9 部（适合 3 联展厅滑动 3 组）：
 * 优先混入真实「在看」作品，同时随机搭配「看过」精选，兼顾追番动态与补番回忆。
 */
export function getBangumiWatchlist(count = 9): AnimeWatchItem[] {
  const items = (bangumiData.items || []) as AnimeWatchItem[];
  if (!items.length) return [];

  const doingList = items.filter(item => item.type === 3);
  const doneList = items.filter(item => item.type === 2);

  // 随机洗牌
  const shuffledDoing = shuffle(doingList);
  const shuffledDone = shuffle(doneList);

  // 如果在看比较多，取部分在看 + 部分看过
  const doingTarget = Math.min(shuffledDoing.length, Math.max(2, Math.floor(count * 0.4)));
  const doneTarget = count - doingTarget;

  const selectedDoing = shuffledDoing.slice(0, doingTarget);
  const selectedDone = shuffledDone.slice(0, doneTarget);

  // 合并后再做一次随机交错，使轮播时在看与看过错落有致
  const merged = shuffle([...selectedDoing, ...selectedDone]);

  // 如果凑不够 count，用剩余的补齐
  if (merged.length < count) {
    const remaining = shuffle(items.filter(item => !merged.some(m => m.id === item.id)));
    merged.push(...remaining.slice(0, count - merged.length));
  }

  return merged.slice(0, count);
}
