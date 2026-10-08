# Homepage Scroll-Driven Storytelling

工作目录：`E:\blog\astro-blog`  
作用范围：正式首页 `/` 与共用组件的 `/prototype/acg/`（都走 `AcgHome.astro`）  
状态：Ready for Codex  
本文件就是交给 Codex 的规格 + 操作方法。不要另发明一套产品。

---

## Context

当前首页 `AcgHome.astro` 是 **9 段编辑流**：Hero（约一屏）→ Currently → Writing → Music → Notes → Projects → Watching → Fragments → About → Footer。动效只有：

- Hero 里 `WindWaveMotif` 的 8–20s 循环波形 + 桌面 6×4px 指针视差
- Music 段 **3D 唱片廊 AUTO CRUISE**，并且 `wheel` 里 `preventDefault` 把竖直滚轮抢走
- 列表 hover `padding-left` / 封面 `scale(1.02)`
- 没有任何 `scroll progress` 驱动的画面演化；唯一全站滚动进度条在文章页

产品页原型 `ProductHome.astro` 只有「左栏 `position: sticky` + 右栏列表往下滚」，不是 Apple / 小米那种 **滚动距离 = 时间轴**。本计划把正式首页改成后者，但 **不推翻** `DESIGN_LANGUAGE.md`：清澈、克制、文学、夏日、海、风；签名仍是 `WindWaveMotif`；容器 1180px；细线 + 衬线大标题；访客只有 Summer / Night。

已拍板（2026-08-24）：

1. **4 幕 sticky 电影章**：Hero、Currently、Music、Watching。Writing / Notes / Projects / Fragments / About 保持文档流，只用 scroll progress 做轻景深与交接。
2. **Music**：取消竖直滚轮抢占；页面滚动进度连续切 7 张封面；保留拖拽与方向键；进入此章停止 AUTO CRUISE。
3. **章节口号可重写**；数据、排序、真实专辑 / 海报 / 文章不得虚构。

明确不在范围内：文学空间光、播放器 IA、导航 IA、文章页、音乐页、product/dev/minimal 原型、GSAP / Lenis / Canvas / `scroll-snap`。

---

## 当前结构（改之前必须看懂）

| DOM 顺序 | 段 | 现有视觉主体 | 本计划角色 |
| --- | --- | --- | --- |
| Header | `SiteHeader` 72px，**不是 sticky** | 导航 | 保持文档流，滚走 |
| 1 | Hero | 大标题 + `WindWaveMotif` | **Scene A** sticky |
| 2 | Currently `#currently` | 2×2 文字状态卡 | **Scene B** sticky |
| 3 | Writing `#writing` | 5 条 editorial list | 文档流 + 轻景深 |
| 4 | Music `#music` | 7 张 3D 唱片廊（抢滚轮） | **Scene C** sticky |
| 5 | Notes `#notes` | 短笔记 | 文档流 + 轻景深 |
| 6 | Projects `#projects` | 项目列表 | 文档流 + 轻景深 |
| 7 | Watching `#watching` | 3 张 3:4 海报 | **Scene D** sticky |
| 8 | Fragments `#fragments` | 摄影占位 | 文档流 + 轻景深 |
| 9 | About `#about` | 中心短文 | 文档流 + 轻景深 |
| Footer | 波形 + 版权 | 落地 | 静态 |

故意 **不重排 DOM**。Writing 夹在 Currently 与 Music 之间、Notes/Projects 夹在 Music 与 Watching 之间，是呼吸段，避免整页变成一支广告片。

编号保留内容身份：Currently 01、Writing 02、Music 03、Notes 04、Projects 05、Watching 06、Fragments 07、About 08。Hero 去掉现在的装饰 `01 / 09`，改为场景内 kicker。电影章用 `data-scene`，不用另一套 01–04 去覆盖内容编号。

---

## 推荐做法

一个导演、四幕舞台、其余是书页。不要做「很多动画组件的集合」。

### 1. 单一导演

在 `AcgHome.astro` 的现有 `<script>` 里扩展（可抽到 `src/lib/home-scroll.ts`，但只允许这一个模块）。**禁止**每段一个 IntersectionObserver 淡入组件。

每个电影章 markup：

```html
<section class="home-scene" data-scene="threshold" id="top">
  <div class="scene-track">
    <div class="scene-pin">
      <div class="scene-stage">…现有内容演化…</div>
    </div>
  </div>
</section>
```

- `.scene-track`：外层提供滚动距离（见各幕 vh）
- `.scene-pin`：`position: sticky; top: 0; height: 100svh;` 固定视口
- 进度公式（唯一）：

```
p = clamp( -track.getBoundingClientRect().top / (track.offsetHeight - innerHeight) , 0, 1 )
```

导演每帧只做一件事：给当前相交的 `[data-scene]` 写 `--p`（0–1）。CSS 用 `@property --p` 消费。反向滚动必须原路返回，禁止 `once` 类 fade-in。

`requestAnimationFrame` 仅在任一 scene track 与视口相交时运转；`document.hidden` 时暂停（与环境光同一纪律）。`astro:page-load` 用现有 AbortController 模式重绑。

不要用 `scroll-snap`、不要 `scrollIntoView` 强迫整屏、不要劫持 `html { scroll-behavior }` 做缓动滚动（保留现有 smooth 仅给锚点）。

### 2. 运动预算（克制、可反向、有景深）

全部从 `DESIGN_LANGUAGE.md` §8 收紧，Codex 不得加码：

| 属性 | 上限 | 说明 |
| --- | --- | --- |
| 滚动驱动的 `translate` | 文案 ≤ 32px，Motif ≤ 24px，次级卡 ≤ 48px | 禁止元素飞入飞出 |
| `scale` | 0.94–1.04 | 主体默认为 1 |
| `rotateY` | Music 邻封 ≤ 12° | 现唱片廊 18° 要降 |
| `opacity` | 0–1，相邻 beat 重叠约 0.12 | 交叉溶解，不要硬切 |
| 滚动视差 | 整幕最多 24px | 禁止高频层叠 |
| Hover | 仍 180–320ms | **滚动本身零 duration**，`p` 就是时间 |
| Blur | 默认 0；若用 ≤ 4px | 禁止毛玻璃主体 |
| Bounce / spring / 弹性 overshoot | 禁止 | |
| 滤镜 disco（brightness 闪、hue 转） | 禁止 | |

一幕一个主焦点。次级元素只允许更低 opacity / 更小 scale，不允许同时讲两个故事。

### 3. 四幕演出（口号已锁定，Codex 按此写，不要再创作长文）

#### Scene A · Threshold（Hero）

- **主焦点**：衬线标题「写到天亮，也没关系。」
- **景深层**：右侧 `WindWaveMotif`（气氛，不是第二个主角）。随 `--p` 向后走：`translateY(calc(var(--p) * 20px)) scale(calc(1 - var(--p) * 0.04))`，opacity 1 → 0.42。
- **Track**：`240vh`
- **Beats**（同一条副文槽交叉，不要堆三行）：

| `--p` | 可见文案 |
| --- | --- |
| 0.00–0.22 | kicker `夏 / SUMMER · 2026`（Night 仍走现有 `data-environment-season`）；h1 满强度；副文 `写作、代码、音乐，和一点夏天的碎片。` |
| 0.22–0.55 | kicker 收为 `00 / THRESHOLD`；副文换成 `先把房间打开。` |
| 0.55–0.82 | 副文换成 `往下走。最近的四件事会自己站出来。`；CTA（读一些文字 / 从这里开始）从 0.55 起淡出 |
| 0.82–1.00 | 标题上移并淡到 ~0.12；Motif 继续后退；为 Scene B 让出焦点 |

身份条 `nabunana · Developer · Writer · Music Listener` 在 0–0.50 可见，之后淡出。h1 **不得改字**。

指针视差保留现有 6×4px，且 **只在 `--p < 0.25`** 生效，避免与滚动视差叠成 8px 抖动。

#### Scene B · Currently

- **主焦点**：当前这一条状态（一次只亮一张，不是 2×2 同时喊）
- **Track**：`280vh`（四拍，约 70vh/拍）
- 布局（desktop）：左/中一张大状态（标题 + 一句）；右侧三条静音索引，当前项 accent 编号点亮
- 四拍数据 **沿用现有**，只改章节口号：

| 拍 | 索引 | 大字 | 一句 |
| --- | --- | --- | --- |
| 0 | 今聴いている | Yorushika | 最近的音乐片段，没有伪装成播放器。 |
| 1 | 今見ている | 葬送のフリーレン | 把旅途、时间和告别慢慢看完。 |
| 2 | 制作中 | Personal Blog | Astro、内容系统与属于自己的视觉语言。 |
| 3 | 読んでいる | 技术文章与随笔 | 在工程实践之外，留一点给文字。 |

章节头：kicker `01 / 最近のこと`，h2 `NOW`，场景句 `同一时间里，只把一件事放亮。`（这句在整幕低对比常驻，不要每拍换口号）。

交接：`--p` 0–0.08 从 Scene A 接过来；0.92–1 整台淡到 Writing 文档流。

#### Scene C · Music

- **主焦点**：当前专辑封面（1:1，真实封面）
- **Track**：`420vh`（7 张，进度连续，不是 7 次切换）
- 背景：沿用现有深夜海绿 `#0a201d`，铺满 pin，让这一幕成为首页里唯一的暗房电影章
- **进度映射**（唯一）：`index = --p * (7 - 1)`。距离公式可复用现唱片廊的 `depth / x / y / z / rotateY / opacity`，但：
  - **禁止 wrap / 无限循环**
  - `p=0` 第一张 `月を歩いている`，`p=1` 最后一张 `盗作`
  - `rotateY` 上限 12°，`z` 深度比现在浅（邻封可见即可）
  - 当前封 `opacity 1 scale 1`；`|distance| > 1.2` 的封 `opacity ≤ 0.18` 且 `pointer-events: none`

章节头：kicker `03 / 音楽`，h2 `AFTER DARK`，场景句 `封面随滚动换气。点下去，从第一首开始。` 当前专辑名 / 艺术家 / 年 / 曲数显示在封面下，随 index 交叉。

**滚轮**：删除 `albumRail` 上 `wheel preventDefault`。竖直滚动永远属于页面。

**拖拽 / 方向键**：不再维护第二套 `position` 状态机。水平拖拽与 ←/→ **把页面滚到该张对应的 track 位置**（`scrollTo` 该 album 的 `p = i / 6`）。松手不要惯性巡航。

**AUTO CRUISE**：首页 Music 章 **删除**。`gallery-note` 文案改为：`SCROLL TO BROWSE · 拖动或方向键对齐封面。点击从第一首开始。`

点击封面仍 `nabunana:play-track` + `data-play-index={albumStartIndexes}`，逻辑不改。

播放器左下固定、日夜控件右下固定：pin 内容底部留出 ≥ 80px，避免封底贴上播放器。

#### Scene D · Watching

- **主焦点**：当前一张 3:4 官方海报
- **Track**：`240vh`（3 张）
- desktop：当前海报居中（或略偏右），另外两张作为低对比侧影 `opacity 0.16–0.28 / scale 0.96`，不要三列同等大小
- 进度：`index = --p * 2`，同样连续、可反向
- 三部作品数据、本地 webp、官网外链、进度条、Finished/Watching **原样保留**
- 章节头：kicker `06 / 今見ている`，h2 `WATCHING`，场景句 `一次只看一张。`

进度条宽度仍是数据里的 82 / 100 / 64，不要用滚动去「播放」这部番。

### 4. 文档流五段（不是电影章）

Writing / Notes / Projects / Fragments / About：

- **不要** sticky、不要加长 vh、不要每条 list 单独飞入
- 用同一导演给进入视口的 `section` 写 `--enter`（0–1，view 区间大约「顶边进入 15% → 35%」；滚出反向）
- 只动两件事：整段 `opacity` 0.35→1，`translateY` 16px→0
- 段标题保持现有 editorial-list / 细线语言
- Writing 口号可轻写：h2 仍 `LATEST WRITING`，kicker 仍 `02 / 文章`，链「全部文章 ↗」保留。不要把五篇文章做成 sticky 单焦点
- Fragments 仍是占位，禁止用版权插画填满；可让三块随 `--enter` 有 8px 以内错层，不要 Ken Burns
- About 保持居中短文；h2 可改为：`一个喜欢写代码、听音乐，也想认真保存生活的人。`（现有句，不新写长文）

Footer 不做 scroll 动画。底部 `WindWaveMotif compact` 保持静态循环（reduced-motion 下静止）。

### 5. 交接

相邻两幕不要同时满不透明。规则：

- 幕尾 `--p ∈ [0.88, 1]`：主焦点 opacity → 0.12–0.2，不要 display:none
- 下一幕头 `--p ∈ [0, 0.12]`：主焦点 0.12 → 1
- Music 暗房的背景色画在 `.scene-pin` 上，随该 pin sticky 进入 / 离开，不要给 `body` 做滚动换色
- 不改 `body[data-lighting-register]`。首页整页保持 `threshold` + `display`。滚动 **不得** 改环境光 token、不得切 dawn/pine/river

### 6. Header / 锚点 / 层叠

- `SiteHeader` 保持现在的文档流 72px，**不改成 sticky**。电影章 pin `top: 0; height: 100svh`。首屏 = 导航 + Hero pin 的上半；滚过 72px 后 Hero 铺满视口
- 锚点 `#currently` `#writing` `#music` `#watching` 必须落到对应 `section` 顶部（track 顶端），不要落到 pin 中间导致 `--p` 从 0.5 起跳
- 现有 `html { scroll-behavior: smooth }` 只服务这些锚点
- z-index：环境光 0；主内容 1；播放器 / 日夜控件保持现有（控件 `z-index: 75`）。Scene pin 不得盖住播放器与 Summer/Night
- `body { isolation: isolate }` 与 `overflow: clip`：`.acg-home` 现有 `overflow: clip` 会 **剪断 sticky**。必须改为仅横向 clip 或不 clip，sticky 才能钉住。这是实施第一件物理事实。

### 7. 移动端（≤900px，与现有断点对齐）

电影章 **取消 sticky 加长**：

- `.scene-track` / `.scene-pin` 变成普通块，高度 auto
- `--p` 可仍按视口交写，但只允许极轻 opacity（例如 0.7–1），没有 240vh 时间轴
- Currently 回到单列 4 张静态卡
- Music 回到 **可拖拽的单封面主导廊**（保留水平拖拽 + 方向键；**仍然不要**竖直 wheel 抢占；不要 AUTO CRUISE）
- Watching 回到现有 2 列（≤430px 1 列）
- 触控区域 ≥ 44px

### 8. `prefers-reduced-motion`

- 所有 `.scene-track` 高度变为内容高度（无 240/280/420vh）
- `--p` 冻结为 0（每幕静止第一拍）
- 不跑 rAF 导演
- Motif 波形按现有规则停；指针视差停
- Music 显示第一张封面静态，键盘仍可换封面（瞬时，无过渡）
- 文档流 `--enter` 恒为 1
- 内容必须不滚动画就能全部读到

### 9. 技术选型

允许：原生 CSS `@property --p`、`position: sticky`、少量 rAF、现有 ClientRouter 重绑。

禁止：GSAP、ScrollTrigger、Lenis、locomotive-scroll、AOS、`scroll-snap`、Canvas、WebGL、视频、粒子、第三套主题。

不要上纯 CSS `animation-timeline: view()` 作为 **唯一** 实现：Music 的 7 封面映射、拖拽→`scrollTo`、ClientRouter 重绑、reduced-motion 折叠高度，用一个 JS 导演更可控。若顺手给文档流 `--enter` 加 CSS scroll-driven 作为增强可以，但导演必须是权威源，避免两套进度打架。

---

## 关键文件

| 文件 | 动作 |
| --- | --- |
| `src/components/acg/AcgHome.astro` | 主战场：markup 拆 track/pin、重写 Music 驱动、四幕 CSS、导演脚本 |
| `src/lib/home-scroll.ts` | 可选抽出：`sceneProgress`、rAF 绑定、music `p → scrollTo`。不要搞框架 |
| `src/components/acg/WindWaveMotif.astro` | 只消费父级 `--p` 做位移/缩放；不要重画 SVG、不要改月相逻辑 |
| `DESIGN_LANGUAGE.md` | 更新 §5 首页节奏（4 幕 + 5 段文档流）；§8 增加 scroll-linked 预算与「禁止 scroll-snap / 一次性 fade-in」 |
| `src/pages/index.astro` | 原则上不改；仍 `lightingIntensity="display" lightingRegister="threshold"` |
| `src/pages/prototype/acg.astro` | 不改；共享 AcgHome 即自动带上 |

不要碰：`EnvironmentLighting.astro`、`lighting.css`、`MusicPlayer.astro`、`src/data/music.ts` 曲目数据、`SiteHeader.astro` IA、文章页进度条。

复用：

- `musicAlbums` / `albumStartIndexes` / `nabunana:play-track`
- 现唱片廊的 distance → transform 数学（去 wrap、降 rotate、改驱动源）
- `AbortController` + `astro:page-load` 重绑模式
- `matchMedia('(prefers-reduced-motion: reduce)')`
- Watching 三张本地 webp + 官网 href

---

## PR 切分（顺序提交）

### PR1 — `home: add scroll director and threshold scene`

- 修 `.acg-home { overflow: clip }` 与 sticky 冲突
- 引入 `.home-scene / .scene-track / .scene-pin / --p`
- 只把 Hero 变成 Scene A，Currently 及以后先保持原样（可暂时接在后面）
- 导演 + reduced-motion 折叠 + 隐藏文档时暂停
- 验收：滚上滚下标题/副文/Motif 连续可逆；无 snap

### PR2 — `home: currently and watching sticky chapters`

- Scene B 单焦点四拍
- Scene D 单焦点三海报
- Writing / Notes / Projects 仍夹在中间不 sticky
- 验收：Currently 四拍可逆；Watching 三张可逆；一次只有一个主焦点

### PR3 — `home: scroll-driven album covers`

- 删除 AUTO CRUISE 与竖直 wheel 劫持
- `index = p * 6` 连续深度对齐 7 封
- 拖拽 / 方向键 → `scrollTo` 对应 p
- 点击仍播放；`#music` 落到 track 顶（p=0，第一张）
- 验收：滚到最后是盗作；滚回第一张是月を歩いている；滚轮始终滚页面

### PR4 — `home: editorial depth, copy, design language`

- 五段文档流 `--enter`
- 锁定文案按本计划写入
- `DESIGN_LANGUAGE.md` §5 / §8
- ≤900px 取消 sticky 加长
- `npm run check` && `npm run build`

每个 PR 一个 commit，标题用上面的英文。不要把四幕和光影重构混在一个 diff。

---

## Codex 操作方法

```powershell
cd E:\blog\astro-blog
```

1. Agent / 可写文件模式。`@` 本计划（若已拷到仓库则 `@` 那份）以及 `DESIGN_LANGUAGE.md`、`src/components/acg/AcgHome.astro`。
2. 按 PR1→4 **顺序**做，不要并行改 `AcgHome.astro`。
3. 每 PR：`npm run check`。PR4 后再 `npm run build`。
4. 浏览器走：`/` 滚到底再滚回来 → `#music` 锚点 → 点一张封面确认播放器 → 切 Night → 缩到 430px → 打开系统「减少动态效果」。
5. 下一会话若中断，第一句：`继续首页 scrollytelling。先 git log --oneline -8，从下一个未做的 PR 接着写，不要重写已落地的 --p 导演。`

### 不要做的事

- 不要 `scroll-snap`、整屏 PPT、元素从左右飞入
- 不要 IntersectionObserver `once: true` 冒充叙事
- 不要保留 Music 竖直 `preventDefault`
- 不要 AUTO CRUISE 和滚动进度两套状态机并存
- 不要改 `nabunana:environment-v1`、不要滚动换 lighting register
- 不要给访客加章节进度圆点分页器（那是 PPT）
- 不要引入 GSAP / Lenis / 粒子 / 视频 / 动漫人物 Hero
- 不要动播放器、音乐页、文章页、空间光
- 不要把 Writing/Projects 改成 sticky 单条高亮
- 不要发明未确认的歌手、照片、番剧

---

## 总提示词（整段复制给 Codex）

```
你在 E:\blog\astro-blog 实现首页滚动叙事。规格就是这份计划，按写死的数字与文案做，不要再设计第二套。

产品：Apple/小米式 scroll-driven storytelling。滚动距离连续驱动画面；必须可反向。不是 fade-in 集合，不是 scroll-snap PPT。

范围：只改正式首页与共用的 AcgHome（/ 和 /prototype/acg/）。保留设计语言：克制、衬线标题、细线、WindWaveMotif 作气氛、容器 1180px、Summer/Night。不要推翻 IA。

结构（不重排 DOM）：
- Scene A Threshold = Hero，track 240vh，主焦点是标题「写到天亮，也没关系。」Motif 只是景深（p: translateY 20px / scale -0.04 / opacity 1→0.42）。副文槽三拍交叉：①写作、代码、音乐，和一点夏天的碎片。②先把房间打开。③往下走。最近的四件事会自己站出来。h1 不许改字。
- Scene B Currently = 280vh，一次只亮 4 条状态里的 1 条（Yorushika / フリーレン / Personal Blog / 技术文章与随笔）。口号 h2=NOW，句=同一时间里，只把一件事放亮。
- 然后 Writing 仍是普通 editorial list。
- Scene C Music = 420vh 暗房 #0a201d。index=p*(7-1)，连续深度对齐 7 张真实封面，禁止 wrap。删除 AUTO CRUISE 和 wheel preventDefault。拖拽/方向键 scrollTo 对应 p。点击仍 nabunana:play-track。h2=AFTER DARK。
- Notes / Projects 普通文档流。
- Scene D Watching = 240vh，一次一张 3:4 海报，另外两张侧影。数据与官网链接不改。h2=WATCHING，句=一次只看一张。
- Fragments / About / Footer 不 sticky。文档流五段只用 --enter：opacity 0.35→1、translateY 16px→0，可反向。不要每条 list 飞入。

导演：唯一 rAF 写 --p。公式 p = clamp(-track.top / (track.offsetHeight - innerHeight), 0, 1)。相交才跑；document.hidden 暂停；astro:page-load AbortController 重绑。

运动上限：文案 translate≤32px，Motif≤24px，scale 0.94–1.04，Music rotateY≤12°，无 bounce、无 snap、无高频视差。滚动零 duration。

物理：.acg-home 的 overflow:clip 会剪断 sticky，必须改掉。Header 不 sticky。pin top:0 height:100svh。锚点落到 track 顶。pin 不得盖住播放器与日夜控件（底留≥80px）。不改 lighting register。

≤900px：取消 sticky 加长，Music 只保留水平拖拽+键盘、不要 cruise、不要抢竖直滚轮。
prefers-reduced-motion：track 高度塌回内容，p 冻结 0，不跑导演，内容不靠动画才能读完。

PR 顺序提交：
1 home: add scroll director and threshold scene
2 home: currently and watching sticky chapters
3 home: scroll-driven album covers
4 home: editorial depth, copy, design language

每 PR 跑 npm run check。禁止 GSAP/Lenis/Canvas/scroll-snap。禁止发明内容。做完在 / 上滚到底再滚回、点封面播放、切 Night、缩 430px、开减少动态效果。
```

---

## 验收（Codex 必须亲自滚一遍）

1. `/` 从顶滚到底再滚回：四幕画面连续可逆，没有卡在终态的 fade-in。
2. 任何时刻一幕只有一个主焦点（标题 / 一张状态 / 一张封面 / 一张海报）。
3. Writing、Notes、Projects 没有被加长成 sticky 电影章。
4. Music：竖直滚轮始终滚页面；7 封随 p 连续；尽头是盗作；滚回是月を歩いている；点封面出声。
5. `#music` 落在第一张封面，不是第 4 张。
6. Night 下暗房仍是 `#0a201d`，标题对比足够；Summer/Night 控件能点到。
7. 430px：无超长空白 sticky 轨；四状态与三海报可读。
8. 减少动态效果：无加长轨、无巡航、文案全在，无需「演完」才出现。
9. `ClientRouter` 去 `/blog/` 再回 `/`：导演仍工作，播放器不重挂。
10. `npm run check` 与 `npm run build` 通过。
11. 首页没有章节圆点、没有诗、没有第三套主题、没有 lighting 随滚动换房。

---

## 验证

桌面 1440：走完验收 1–6、9。  
移动 430：验收 7。  
系统 reduced-motion：验收 8。  
无浏览器工具时：`npm run build` + 读 computed CSS 确认 `.scene-pin { position: sticky }` 且 `.acg-home` 不再 `overflow: clip`；Music 脚本无 `preventDefault` on wheel。
