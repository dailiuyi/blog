# Project preview sources

Assets retrieved 2026-09-23 from the owner's public repositories.

- `elma-demo.gif`: `dailiuyi/elma-gohan`, commit `bbc45f1e5972facb8beb790e9ebb94efbc08c587`, `docs/images/readme/product-demo.gif`.
- `elma-still.png`: first frame of that GIF, used to avoid autoplay. The full animation is loaded only after pressing Play.
- `elma-preview.jpg`: same repository and commit, `docs/images/readme/social-preview.jpg` (mini-program discovery card).
- `threebody.png`: `dailiuyi/ThreeBodySimulation`, commit `444c21154677bd01a3d483d051fc051398bd8b00`, `screenshots/2026-08-14/屏幕截图 2026-08-14 144744.png`.
- minialloc's text preview quotes `minimalloc.c` from `dailiuyi/minialloc`, commit `79afe6a16ed41e92116a896eff55f27f1c1f0e39`. It is labeled source code, not a running application screenshot.
- `card-pdf-studio/layout-diagram.svg`: 排版示意图，不是运行截图。按 `src/content/blog/tcg-card-pdf-layout.md` 记录的工具输出规格绘制（A4 210 × 297 mm、卡片 59 × 86 mm、8 mm 页边距、2 mm 间距、300 DPI，正好排成 3 × 3）。图中卡面为占位图形，不代表任何真实卡牌内容。
- `threebody.webp`、`elma-still.webp`: 由同目录同名 `.png` 压缩生成（原 PNG 保留用于溯源）。用仓库内 `node_modules/sharp`（sharp 0.35.4 / libvips 8.18.6，未新增依赖）执行：`sharp('threebody.png').resize({width:1600,withoutEnlargement:true}).webp({quality:80})` 与 `sharp('elma-still.png').webp({quality:80})`，输出 `threebody.webp` 1600×843、`elma-still.webp` 288×640（尺寸不变）。
