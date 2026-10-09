# 项目决策记录 (DECISIONS)

本文档记录博客项目中的长期设计、文风规范与工程决策，避免口头或单次会话中的意图丢失。

---

## D-019: 博客全站文案去 AI 味与文风基调规范 (oil-tone)

- **日期**：2026-10-08
- **状态**：已采纳并实施 (Accepted & Implemented)
- **参考依据**：[oil-oil/oil-tone 规范](https://github.com/oil-oil/oil-tone)

### 背景与问题
博客原有文案（首页、关于页、项目卡片等）存在较明显的 AI 自动生成痕迹：
1. **标签式排列与模板腔**：例如“关键词：Java、算法、Agent应用”、“不止是代码，还有生活和音乐”。
2. **生硬的四字/格言感标题**：如“把想法做成能用的东西”、“把过程写清楚”、“代码之外，也有日常”。
3. **宏大包装与空泛动词**：项目描述偏向功能套话与空洞升华，缺乏真实的开发动机与动手细节。
4. **口吻疏离**：自我介绍偏向公关式简介，缺乏真实开发者的生活感与随性感。

### 决策内容
1. **核心文风定位**：采用**「平实且带有一点生活随性感的开发者自白」**。
   - 设定身份：软件工程大三学生、平时主要写 Java / 算法、用 AI 结对写小工具、喜欢 Yorushika 和动画。
   - 叙述原则：直白讲清楚“为什么写这个工具”、“是怎么做的”、“踩了什么坑”，删除无材料支持的哲学升华和模板化标语。
   - 语言节奏：遵循 `oil-tone` 规范，保留符合自然语序的“的/了”等虚词，句子读起来朗朗上口，贴近真实口语化书面语。
2. **实施范围**：
   - [index.astro](file:///e:/blog/astro-blog/src/pages/index.astro)：首页 Hero 介绍、各板块章节标题、音乐与日常卡片文案。
   - [about.astro](file:///e:/blog/astro-blog/src/pages/about.astro)：关于我页面导语、个人经历展开（算法底层、后端工程、AI 协作方式）、底部交流与求职自白。
   - [projects.astro](file:///e:/blog/astro-blog/src/pages/projects.astro) & [selected-projects.ts](file:///e:/blog/astro-blog/src/data/selected-projects.ts)：ELMA、Three Body Lab、minialloc、Card PDF 等项目的标题、说明与开发细节。
   - [music.astro](file:///e:/blog/astro-blog/src/pages/music.astro) & [blog/index.astro](file:///e:/blog/astro-blog/src/pages/blog/index.astro)：音乐页与文章列表的导引文案。
   - [src/content/projects/*.md](file:///e:/blog/astro-blog/src/content/projects/)：各项目对应 Markdown 文档的概要与描述。


---

## D-020: 文章正文排版与代码块高质感美化 (Direction C)

- **日期**：2026-10-08
- **状态**：已采纳并实施 (Accepted & Implemented)

### 背景与问题
博客正文排版与代码块较为素淡单调：
1. 代码块缺乏容器层次与精致度，顶部栏仅有朴素的语言文字与复制按钮。
2. 用户期望 Mac 窗口风格的红黄绿三色微圆点（`.code-block-dots`）以增加界面工艺感。
3. Astro 5 / 7 Content Layer 会将 Markdown 产物缓存在 `node_modules/.astro/data-store.json`，若仅修改 rehype 插件而不清空该缓存，Astro build 会直接复用旧 HTML，导致插件改动不生效。

### 决策内容
1. **代码块视觉工艺**：
   - 在 rehype 编译管线 ([scripts/rehype-code-block.mjs](file:///e:/blog/astro-blog/scripts/rehype-code-block.mjs)) 中，向每个 `<div class="code-block-bar">` 静态注入 `<div class="code-block-dots"><span class="dot dot-red"></span><span class="dot dot-yellow"></span><span class="dot dot-green"></span></div>`。
   - 在客户端组件 ([src/components/common/CodeCopy.astro](file:///e:/blog/astro-blog/src/components/common/CodeCopy.astro)) 中配置深色模式（#ff5f56, #ffbd2e, #27c93f）圆点样式及尺寸约束（9px），并保留客户端动态检测兜底。
   - 增加微弱外发光阴影与内边框，提升暗黑风格代码块的质感与立体感。
2. **正文微交互升级**：
   - 顶部阅读进度条增加翡翠流光渐变（`reading-progress`）。
   - 日系信笺感 `blockquote` 与立体图片框线。
   - 搜索按钮增加 `<kbd>⌘K</kbd>` 快捷键标识。
3. **构建缓存清理规范**：
   - 若修改了 Markdown 预处理/rehype/remark 插件，需清除 `node_modules/.astro` 缓存以触发重新编译。

---

## D-021: 首页实时状态挂件与关于页卡片微交互 (Directions A & B)

- **日期**：2026-10-08
- **状态**：已采纳并实施 (Accepted & Implemented)

### 背景与问题
1. **首页首屏较为空旷单调**：大段平实简介下方直接排列按钮，缺乏“正在进行时”的生动生活感与呼吸感。
2. **关于页（`/about/`）交互单调**：
   - 个人经历采用下划线分割朴素列表，层次平坦；
   - 唱片组件未与全局音乐播放器状态联动，缺乏生动反馈。

### 决策内容
1. **方向 A：首页实时状态挂件 (Live Status Widget)**：
   - 在首页首屏自我介绍下方增加精致胶囊状态栏（`.live-status-pill`）。
   - 绿色呼吸灯小圆点（`.status-dot` + `.status-ping` 无限脉冲动画）。
   - 平实质感状态信息：“深圳 / 大三 · 编码与小工具 / ♫ 歌曲名 · 歌手”。
   - 联动全局播放器：当播放器切歌或播放时，动态展示当前正在播放的曲目信息。
2. **方向 B：关于页唱片联动与经历卡片微交互**：
   - 黑胶唱片联动：监听全局 `nabunana:player-state` 事件，播放时唱片持续自转并展示当前曲目，hover 时可预览旋转。
   - 经历卡片化重构：将原本的无底色下划线列表重构为三张精致微圆角卡片（`01 // 02 // 03` 胶片编号、微光边框、悬浮浮起动效、折叠内容平滑强调线）。
   - 项目缩略图与兴趣外链卡片增加微弱光泽阴影与缓动缩放。
---

## D-022: 首页四幕滚动叙事与五段文档流景深设计 (Scroll-Driven Storytelling)

- **日期**：2026-10-09
- **状态**：已采纳并实施 (Accepted & Implemented)
- **参考依据**：`HOME_SCROLL_STORYTELLING.md`, `DESIGN_LANGUAGE.md` §5/§8

### 背景与问题
1. 首页原型与长页面体验中，缺乏统一的叙事时间轴，视觉元素以零散的 Hover 或孤立淡入为主，无法传达连贯的意境。
2. Music 模块原本存在劫持滚轮的 `wheel preventDefault` 与独立的 AUTO CRUISE 巡航状态机，破坏了原生滚动体验。
3. 容器原有的 `overflow: clip` 剪断了 CSS `position: sticky`，导致固定视口演进无法生效。

### 决策内容
1. **单一滚动导演 (`home-scroll.ts`)**：
   - 使用单个 `requestAnimationFrame` 循环，仅在元素与视口相交且文档可见时运行；
   - 电影章通过 `p = clamp(-track.top / (track.offsetHeight - innerHeight), 0, 1)` 计算 `--p` 写入 CSS 自定义属性；反向滚动必须连续可逆；
   - 严禁引入 GSAP/Lenis/Canvas/scroll-snap 等重型库。
2. **四幕 Sticky 电影章架构**：
   - **Scene A (Threshold)**：`240vh` 轨，标题「写到天亮，也没关系。」，三拍副文槽交叉演进，`WindWaveMotif` 位移缩放提供微景深，指针视差仅在 `--p < 0.25` 允许；
   - **Scene B (Currently)**：`280vh` 轨，四拍（Yorushika / 葬送のフリーレン / Personal Blog / 技术文章与随笔）单焦点呈现，右侧索引高亮；
   - **Scene C (Music)**：`420vh` 轨，`#0a201d` 暗房，7 张真实专辑封面按 `index = --p * 6` 连续深度排布（禁止 wrap），移除 AUTO CRUISE 与滚轮劫持，水平拖拽与方向键直接映射至页面垂直滚动；
   - **Scene D (Watching)**：`240vh` 轨，三部作品海报单焦点，其余两张作为低对比侧影。
3. **五段文档流微景深 (`--enter`)**：
   - Writing (02)、Notes (04)、Projects (05)、Fragments (07)、About (08) 保持自然文档流；
   - 由导演计算 `--enter`（0.35→1，translateY 16px→0），提供进入视口时的温和景深与呼吸感，禁止整段弹入或全屏化。
4. **响应式与无障碍保障**：
   - `<=900px` 移动端：电影章折叠为内容高度（取消 sticky 加长时间轴），保持普通流排布，保留水平拖拽；
   - `prefers-reduced-motion`：track 高度塌回内容高度，`--p` 恒为 0，`--enter` 恒为 1，不启动 rAF 导演，所有内容静态可读。

### 成果回流：正式主页融合四大高质感特性 (Homepage Integration)
在保留正式首页平实口吻、精炼技术内容与 Live Status 挂件的前提下，将原型中的高质感模块解耦融入正式主页（`src/pages/index.astro`）：
1. **NOW 单焦点 4 拍状态看板**：在日常板块上方提供单焦点交互，左侧展示当前状态大字与说明，右侧 4 项索引支持悬停与点击切换。
2. **Watching 追番进度条卡片**：在日常板块增加 3 部番剧卡片（3:4 比例海报、百分比观看进度条、官方源链接与状态标签）。
3. **AFTER DARK 3D 黑胶画廊**：独立 `#music` 音乐暗房章节，7 封真实专辑 3D 深度堆叠，支持鼠标水平拖拽、方向键左右切歌、点击专辑直切开播。
4. **文档流微景深视差 (`--enter`)**：各主要章节进入视口时平滑浮现（`translateY: 22px -> 0`, `opacity: 0.35 -> 1`），在无障碍偏好下静止。

---

## D-023: 原生现代音乐播放器、浮动多行歌词视窗与沉浸式曲库大表重构

- **日期**：2026-10-09
- **状态**：已采纳并实施 (Accepted & Implemented)

### 背景与问题
1. **老旧第三方依赖与样式陈旧**：原播放器依赖外部库 `aplayer`，样式风格陈旧、体积臃肿且与全站现代化海风/暗夜玻璃质感格格不入。
2. **曲库交互维度受限**：原播放器采用折叠专辑树，选歌步骤繁琐，无法直观浏览全站 94 首歌曲的总表，缺乏按歌名/歌手/专辑即时搜索过滤的能力。
3. **歌词动效简陋**：原歌词仅有单行粗糙文字切换，缺乏多行景深层次感、双语排版对齐与平滑吸附居中动效，不支持点击歌词任意行即时跳转 Seek。
4. **切页状态中断与跨页持久化体验**：由于第三方 DOM 重构机制，切页时容易出现重载闪烁。

### 决策内容
1. **彻底移除第三方依赖，纯原生 HTML5 引擎**：
   - 彻底从 `package.json` 卸载 `aplayer` 及相关样式；
   - 采用单一原生 `<audio id="nabunana-audio-element">` 配合单例 `NabunanaPlayerController` 控制器，实现极简、高性能与秒级响应。
2. **常驻磨砂玻璃微胶囊（Floating Glass Capsule）**：
   - **折叠微态 (Mini Bar, 196×42px)**：方案 A 精炼规格，圆角 9999px 微胶囊，展示 32×32 呼吸旋转黑胶封面、歌名（11.5px）与歌手（9.5px）、播放暂停键与展开箭头，常态极致克制，绝不遮挡主页任何主体文字。
   - **展开卡片态 (Expanded Card, 336×68px)**：方案 A 紧凑规格，现代双排布局，左侧 44×44 圆角封面（带打开曲库快捷入口）；上排为歌名（12.5px 粗体）与歌手（9.5px），右侧为核心播放三键（上一首、青色微光播放键、下一首）；下排为时间指示（`00:00 / 04:00`，9px），右侧为功能按键组（循环模式、词、曲库抽屉、固定图钉 Pin、收起箭头）；顶部带横跨全宽的细腻可拖拽进度条。
3. **浮动多行歌词视窗（Apple Music 风格）**：
   - 浮动在播放器正上方（336×180px，精炼聚焦 3 行），采用 `#071917` 黑曜石深色背景与 32px 磨砂虚化，彻底隔离底层页面文字；与底部卡片宽度严丝合缝；
   - 顶部与底部带有精致渐隐虚化遮罩（`mask-image`）；
   - 当前句纯白加粗高亮（`15px`, `text-shadow: 0 0 14px rgba(100, 199, 189, 0.7)`），日中双语排版，译文清爽高亮（11.5px）；未激活行弱化景深（`opacity: 0.4`）；
   - **点击任意歌词行即时跳转 (Seek)**：
     - 支持点击歌词任意行精准跳转至对应时间点并平滑滚动高亮；
     - **HTTP Range 防冲刷机制**：查明当静态服务缺少 `Accept-Ranges: bytes`（如简单 HTTP 服务器）时，浏览器请求媒体并执行 `currentTime = seekTime` 会被重定向重载音频导致回退为 0 秒。工程上提供了支持 HTTP 206 Partial Content 的本地服务，并在播放器客户端增加了防御性 seek（未起播时先触发 `play()` 等元数据稳定后在微任务中执行精确 seek），确保本地与生产云端（Nginx / CDN）均能 100% 精准秒级跳转。
4. **半屏沉浸式全曲库选歌大表（Song Library Drawer）**：
   - 尺寸 `min(650px, calc(100vw - 36px)) × min(72vh, 640px)`，贴合现代化桌面音乐软件规格；
   - 顶部包含即时搜索框（支持歌名、歌手、专辑模糊搜索，实时更新计数与无结果兜底提示）；
   - 大表展示序号、封面、歌名、歌手、所属专辑；当前播放项展示跳动青色 EQ 3 条波形柱（播放跳动，暂停静止），点击任意行秒切开播。
5. **真正的跨页面持久化播放与全站事件联动**：
   - 依托 Astro `<ClientRouter />` 与 `transition:persist="nabunana-music-player"`，跨页面切换音频流完全零断流；
   - `localStorage` 全量持久化当前索引、秒级进度、音量、循环模式（列表循环/单曲循环/随机播放）、歌词开启状态、固定状态与展开状态；
   - 监听全站 `nabunana:play-track` 切歌事件，广播 `nabunana:player-state` 事件同步首页黑胶自转与全局状态胶囊。

