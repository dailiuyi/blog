import { writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

const USER_ID = '578278';
const CACHE_FILE = path.join(process.cwd(), 'src', 'data', 'bangumi-cache.json');
const USER_AGENT = 'nabunana/blog/1.0 (contact@nabunana.com)';

async function fetchWithTimeout(url, timeoutMs = 6000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': USER_AGENT },
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return await res.json();
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

function normalizeItem(raw) {
  const subject = raw.subject || {};
  const isWatched = raw.type === 2;
  const totalEps = subject.eps || 0;
  const epStatus = raw.ep_status || 0;
  
  let progress = 100;
  let progressText = '100%';
  if (!isWatched) {
    if (totalEps > 0) {
      progress = Math.min(100, Math.round((epStatus / totalEps) * 100));
      progressText = epStatus > 0 ? `${epStatus}/${totalEps} 集 · ${progress}%` : `0/${totalEps} 集 · 0%`;
    } else {
      progress = 0;
      progressText = '在看';
    }
  }

  const tags = (subject.tags || [])
    .filter(t => t.name && !['TV', '日本', '2022', '2023', '2024', '2021', '2020'].includes(t.name))
    .slice(0, 2)
    .map(t => t.name);

  const title = subject.name_cn || subject.name || '未知动画';
  const jpTitle = subject.name || title;
  const meta = `${jpTitle}${tags.length > 0 ? ' / ' + tags.join(' · ') : ''}`;

  const image = subject.images?.large || subject.images?.common || subject.images?.medium || '/media/anime/fallback.webp';

  return {
    id: raw.subject_id || subject.id,
    title,
    jpTitle,
    type: raw.type,
    status: isWatched ? '已看完' : '在看',
    progress,
    progressText,
    totalEps,
    epStatus,
    rate: raw.rate || 0,
    comment: (raw.comment || '').trim(),
    tags,
    meta,
    image,
    source: `https://bgm.tv/subject/${raw.subject_id || subject.id}`,
    updatedAt: raw.updated_at || '',
  };
}

async function syncBangumi() {
  console.log(`[Bangumi] Syncing anime collections for user ${USER_ID}...`);
  try {
    const [doingRes, doneRes1, doneRes2] = await Promise.all([
      fetchWithTimeout(`https://api.bgm.tv/v0/users/${USER_ID}/collections?subject_type=2&type=3&limit=50`),
      fetchWithTimeout(`https://api.bgm.tv/v0/users/${USER_ID}/collections?subject_type=2&type=2&limit=50&offset=0`),
      fetchWithTimeout(`https://api.bgm.tv/v0/users/${USER_ID}/collections?subject_type=2&type=2&limit=50&offset=50`),
    ]);

    const doingItems = (doingRes.data || []).map(normalizeItem);
    const doneItems = [...(doneRes1.data || []), ...(doneRes2.data || [])].map(normalizeItem);
    const allItems = [...doingItems, ...doneItems];

    const payload = {
      updatedAt: new Date().toISOString(),
      user: USER_ID,
      total: allItems.length,
      doingCount: doingItems.length,
      doneCount: doneItems.length,
      items: allItems,
    };

    await writeFile(CACHE_FILE, JSON.stringify(payload, null, 2), 'utf8');
    console.log(`[Bangumi] Successfully synced ${allItems.length} records (${doingItems.length} 在看, ${doneItems.length} 看过) to ${CACHE_FILE}`);
  } catch (err) {
    console.warn(`[Bangumi] Sync failed: ${err.message}. Checking local cache...`);
    if (existsSync(CACHE_FILE)) {
      console.log(`[Bangumi] Reusing existing cache file ${CACHE_FILE}`);
    } else {
      console.warn(`[Bangumi] No cache found, creating fallback seed cache.`);
      const fallbackPayload = {
        updatedAt: new Date().toISOString(),
        user: USER_ID,
        total: 3,
        doingCount: 2,
        doneCount: 1,
        items: [
          {
            id: 29648,
            title: '葬送的芙莉莲',
            jpTitle: '葬送のフリーレン',
            type: 3,
            status: '在看',
            progress: 82,
            progressText: '23/28 集 · 82%',
            totalEps: 28,
            epStatus: 23,
            rate: 9,
            comment: '',
            tags: ['奇幻', '冒险'],
            meta: '葬送のフリーレン / 奇幻 · 冒险',
            image: '/media/anime/frieren.webp',
            source: 'https://bgm.tv/subject/399991',
            updatedAt: '',
          },
          {
            id: 364450,
            title: '孤独摇滚！',
            jpTitle: 'ぼっち・ざ・ろっく！',
            type: 2,
            status: '已看完',
            progress: 100,
            progressText: '100%',
            totalEps: 12,
            epStatus: 12,
            rate: 9,
            comment: '',
            tags: ['音乐', '日常'],
            meta: 'ぼっち・ざ・ろっく！ / 音乐 · 日常',
            image: '/media/anime/bocchi-the-rock.webp',
            source: 'https://bgm.tv/subject/328609',
            updatedAt: '',
          },
          {
            id: 302061,
            title: '86 —不存在的战区—',
            jpTitle: '86―エイティシックス―',
            type: 3,
            status: '在看',
            progress: 64,
            progressText: '15/23 集 · 64%',
            totalEps: 23,
            epStatus: 15,
            rate: 8,
            comment: '',
            tags: ['科幻', '剧情'],
            meta: '86―エイティシックス― / 科幻 · 剧情',
            image: '/media/anime/eighty-six.webp',
            source: 'https://bgm.tv/subject/302061',
            updatedAt: '',
          },
        ],
      };
      await writeFile(CACHE_FILE, JSON.stringify(fallbackPayload, null, 2), 'utf8');
    }
  }
}

syncBangumi();
