---
title: "做一个能按真实尺寸打印的卡牌 PDF 工具"
description: "卡牌图片放进 A4 不难，麻烦的是毫米、DPI、图片比例和桌面程序里的几个小坑。"
published: 2026-08-20T12:20:00+08:00
category: Projects
tags: [Python, Pillow, PDF, Tkinter, Desktop]
featured: true
readingTime: "9 min"
---

我想把几张 TCG 卡图排到 A4 上直接打印。用图片编辑软件手动拼当然能做，但换一批图又要重新对齐，卡牌类型变了，尺寸也得再算一遍。

于是写了个 Python 小工具：选图片，填卡片宽高、页面边距和间距，生成一份 A4 PDF。界面用 Tkinter，图片和 PDF 交给 Pillow。

代码不多，容易出错的是打印尺寸。屏幕上看着合适，不代表纸上就是 59×86mm。

## 毫米是输入，像素只在生成图片时出现

页面和卡片都用毫米记录。合成时按 300 DPI 转成像素：

```text
pixel = millimeter / 25.4 × 300
```

A4 是 210×297mm，转出来大约 2480×3508 像素。保存 PDF 时仍然声明 300 DPI，打印软件才能把像素还原成原来的物理尺寸。

如果只把卡图缩到一个“差不多”的像素宽度，屏幕预览往往看不出问题，打印后才会发现整页偏大或偏小。所以尺寸计算从头到尾以毫米为准，像素只出现在渲染边界。

一页能放几张，用下面两行就能算：

```python
cols = max(1, int((available_width + gap) / (card_width + gap)))
rows = max(1, int((available_height + gap) / (card_height + gap)))
```

`available_width` 和 `available_height` 已经扣掉两侧边距。公式里给可用区域补一个 `gap`，是为了抵消最后一张卡后面并不存在的间距。

游戏王卡片使用 59×86mm，在默认 8mm 边距、2mm 间距下正好排成 3×3。网格算完后还会整体居中，不从左上角直接开始贴。这样换成一页 6 张的卡型时，留白不会全挤到右下角。

![A4 上 3×3 张 59×86 mm 的卡](/media/posts/tcg-a4.jpg)

## 原图比例不对时留白

扫描图、截图和网上保存的图片不一定符合标准卡牌比例。直接拉伸到卡片框，文字和人物都会变形。

工具用 contain：取横向和纵向缩放比例中较小的那个，等比缩放后居中。比例对不上时留一点白边，不裁内容，也不拉伸。

```python
scale = min(box_w / image.width, box_h / image.height)
new_w = round(image.width * scale)
new_h = round(image.height * scale)
```

透明 PNG 会先铺到白色背景上再转 RGB。否则透明区域在某些 PDF 处理流程里会变黑。

![拉伸变形对比 contain 等比留白](/media/posts/tcg-contain.jpg)

## 一张坏图只跳过一张

批量导入时总会碰到损坏文件，或者扩展名看起来是图片，实际内容无法解码。构建函数逐张打开，失败的文件记进 `skipped`。只要还有有效图片，PDF 就照常生成，完成后再告诉用户跳过了哪些文件。全部无效才终止。

排版函数没有依赖 Tkinter 控件。`layout_cols_rows()` 负责算网格，图片读取和等比缩放各自独立，`build_pdf()` 只接收路径和尺寸参数。命令行入口直接调用同一个 `build_pdf()`，出问题时可以绕过界面复现。

桌面界面状态多，图片处理又慢，把计算留成普通函数，测试和排错都省事。

## Tkinter 主线程只更新界面

300 DPI 的 A4 页面不小。一次处理几十张图，如果解码、缩放和写 PDF 全放在主线程，窗口会一直显示未响应。

`build_pdf()` 在后台线程运行，结果写入 `queue.Queue`。主线程用 `root.after()` 定时取结果，再更新状态栏和提示框。后台线程不直接操作 Tk 控件。

缩略图还有个很隐蔽的问题。`ImageTk.PhotoImage` 如果没有保留 Python 引用，即使 Label 还在用它，也可能被垃圾回收，界面上的图片会突然消失。程序专门保存了一份 `PhotoImage` 引用表，这类修补看起来不起眼，漏掉后却很像随机 UI 故障。

## 打包后不能继续依赖脚本目录

PyInstaller 单文件程序运行时会把资源解压到临时目录。源码模式下，配置放在脚本旁边没问题；打包后如果仍以 `__file__` 为基准，配置和默认 PDF 可能跑到临时路径。

```python
if getattr(sys, "frozen", False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))
```

打包版本使用 exe 所在目录，源码版本使用脚本目录。配置、默认输出和命令行报告都从这个位置往下找。

现在这个工具就做排版和导出，没有自动识别卡边、裁切扫描白边，也不碰打印机驱动设置。图片本身带白边时，可以手动微调卡片尺寸。
