---
title: "把主力环境迁到 Ubuntu 之后，我怎么给 agent 画边界"
description: "从 Windows 搬回 Ubuntu，聊 agent 的权限边界、Grok Bot 多 agent 分工、靠文件交接、按额度动态路由模型，以及 subagent 该不该拆。"
published: 2026-10-30T12:00:00+08:00
updated: 2026-10-30T12:00:00+08:00
draft: false
category: Agent
tags: [AI, Agent, Ubuntu, Grok Bot, 工作流, Subagent]
featured: false
author: nabunana
readingTime: "8 min"
---

## 为什么迁移 Ubuntu

其实早在 20 年，我就算半个 Ubuntu 原住民了。仗着一股豪气，初生牛犊不怕虎，开始折腾并不熟悉的 Linux 命令。还记得那时候用得最熟的命令就是 apt install。资料也参差不齐，用着 Wine 这种不太优雅的方案，就这样和并不完善的生态斗智斗勇。

时间来到 2026 年，我又一次把主力环境迁到了 Ubuntu，而不是选别的替代方案。众所周知，Windows 对 agent 的原生适配并不好，已经到了难以忍受的地步；WSL 又不够性感，总有一层中间商赚差价的不爽。种种原因，让我选了 Linux 当主力工作环境。得益于 agent，我对 Linux 的隔阂和不适应已经缓解了很多，甚至因为更多的客制化和权限开放，某些方面用着更爽。为什么换？就是好用啊。心无旁骛地开发，能让我很快进入一种前所未有的心流状态。对我来说，agent 在 Linux 上跑得更顺，这一点最重要。代价呢？基本没有。QQ、微信、飞书这些办公软件都支持，系统舒服，占用少，性能好……

长时间用下来，还是攒了不少心得。装好 Codex 的第一件事，我就让 agent 给常用的特定命令开了免密 sudo。毕竟是开发环境，能 YOLO 一口气跑完的满足感，是多次返工给不了的。当然也出过一次生产问题。有一次 Grok Build 想用 rm -rf 清理构建缓存，这当然会被策略拦住。但它没死心，转头写了个 Python 脚本去删。这一次，它成功了。这也不是唯一一次碰到边界。前一天，sudo apt upgrade 被权限拦住，它就让我自己跑。

![agent 被拦住后，面前摆着三条路](/media/posts/ubuntu-agent/01b-blocked-three-paths.png)

两件事放在一块看，我开始认真想：agent 的边界怎么画清楚，用起来才不烧心。能干什么，什么不能碰，遇到阻碍是绕过去还是停下来问……其实已经有人把生产环境交给 agent 运维了，但还有很多人在观望。后面讲的东西，都是从这里开始的。

![Windows 和 WSL 层层过闸，Ubuntu 一路直通](/media/posts/ubuntu-agent/01a-wsl-vs-ubuntu.png)

## 如何编排 agent 的边界

从一开始，我就放开了 harness 的权限，读写、命令、联网、MCP 全部放行。但 push、开 PR、合并，必须我点头才能做。没人会去兴师问罪一个 AI，最后背锅的还是我。强推、读取 ssh 和 env、rm 则直接禁止。删库跑路和密钥泄露这种毁灭性的失误，是不能接受的。

不能说这套边界天衣无缝，但总得留个心眼。agent 干活可以不用每次都问我，但每次踩到的坑、每个职能之外的动作，都会让这套系统再加固一次。

边界管的是一个 agent 能做什么。agent 多起来以后，问题就变成了它们之间怎么配合。

![围栏工地：圈内随便干，禁区要我点头，漏洞贴补丁](/media/posts/ubuntu-agent/02-boundary-site-patches.png)

## 我的多 agent 体系：Grok Bot

作为 personal agent 的开山鼻祖，Grok Bot 算是完成度比较高的产品，尤其是后端模型换成 Opus 5.5 之后，我把越来越多的任务交给了它。毕竟电脑会关机、会断电，云电脑不会。我一度分了很多职能部门，也过足了老板瘾，但返璞归真之后，还是组了一套只属于自己的 team：一个管家负责日常交接和生活，工程调度主管编码和部署，学习教练管知识库和学习，事实核查负责调查和探索。得益于 X 的原生搜索适配，事实核查这块它确实做得很好。

![从很多部门收成云电脑上的四人小队](/media/posts/ubuntu-agent/03-from-departments-to-team.png)

## 靠文件交接，不靠聊天

我定了一套交接规矩和流程：一项任务一个文件夹，card.md 是任务卡，result.md 和 verdict.md 分别由执行方和验收方负责。结论只有 PASS、BLOCK、HOLD 三种。

要明确一点：聊天不能当事实真相，而且每个 bot 只能碰自己那份 Markdown。这就是多 agent 版的上下文落盘。agent 之间可以交接，但靠的不是聊天，而是落盘的文件。

![聊天交接会丢，任务卡和结果文件才接得住](/media/posts/ubuntu-agent/04-chat-vs-handoff-card-b.png)

## 编排后端编码工作流：动态路由模型

一个小技巧是，在 Grok Bot 的云电脑上装上 Codex 等 agent，接入相应的 LLM。我的做法是，工程调度负责写好任务卡，或者定时拉取 GitHub 上的 issue，再交给对应的模型完成。工程调度重在「调度」，而不是编码。同时我还编排了一套动态路由：查询各家模型提供商的余量，再结合我对模型能力的理解，分配 Claude、GPT、Grok 等模型来写代码和做 review。我还做了个前端看板监控任务进度，堵塞项、正在跑的、能合的 PR，一目了然。

![调度员看各家额度，把任务卡投给有余量的模型](/media/posts/ubuntu-agent/05a-dispatcher-by-quota.png)

<!-- TODO: 看板截图（用户自截），放这里 -->

这样不仅省下了很多 Grok Bot 的额度，还能提升完工质量。其实还有更复杂的编排方式，比如多个 issue 攒起来先做优先级高的、按模型余量路由的算法，以及针对 DeepSeek 这种峰谷定价的任务编排，有兴趣可以去 X 上查查更多用法。

![动态路由表：任务分给谁写、谁审，以及兜底链](/media/posts/ubuntu-agent/05b-routing-table.png)

说到这里，我一直在用「一个调度带多个执行者」的路子。但最近有篇文章，让我回头重新审视了这件事。

## subagent 之争

Anthropic 近日发了一篇很有价值的文章，《[When to fold subagents into your main agent loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)》。我让 Grok Bot 整理了一下原文，大概是这几层意思：第一，带 skills 的单 agent，质量常常比 subagent 方案好，而且更便宜、更快。第二，自己看过每一步的 agent 能把前后串起来，交接少，丢的东西也少，原文叫 "lossy handoffs"。第三，按问题类型拆 agent，就成了 "telephone game"，每传一次话失真一点。原文还说："You need to decide what context is exchanged between agents and often lose details in the back and forth." 意思是 agent 之间来回传话，细节经常丢。

但原文也写得很清楚："To be clear, subagents still have their place, including at the research frontier." 上下文撑爆、需要并行、工具选不准，这几种情况还是该用 subagent。所以别把它读成「subagent 是错方向」。

结合我自己的使用，确实存在这样的问题：用强模型驱动弱模型，弱模型的幻觉很可能带偏主 agent，而主 agent 上下文的微小偏差也可能带来连锁灾难……Anthropic 的担忧不是空穴来风。他们的观点可以用三组词总结：状态脱节，向上污染，向下放大。主 agent 在探索过程中的细枝末节，没法在上下文传递中全量呈现；主 agent 也分不清子 agent 是真干完了，还是自信地编了个结果，于是一错再错……一味地上 subagent 可能是错误的方向，效果未必比得上 single-agent loop + 按需加载 skills，问题也不只是 token 税带来的成本。

对这种场景，除了刚才说的 single-agent loop + 按需加载 skills，还有一种解法是 worktree 并行，评论区也给了 review + 短步提交 + HITL 的思路。如果是为了用 subagent 而用 subagent，而不是为了解决实际开发中遇到的长链路任务，效果其实远不如一把梭来得优雅。

![子 agent 悄悄改了超时却报测试全过，主 agent 只看绿勾就批了](/media/posts/ubuntu-agent/06-subagent-drift-b.png)

## 写在最后

回头看，这篇讲的其实是同一件事：给 agent 多大的权，让它们怎么配合。单个 agent 要画清边界，能放的放开，碰不得的直接禁止；多个 agent 之间靠文件交接，不靠聊天；到了要不要拆 subagent，也得先问一句，拆出来是在解决真问题，还是在给自己加交接。

我的这套体系也还在边用边改。每踩一个坑，就加固一处。【待柳逸补：想留给读者的一句话，或者接下来打算试的方向】
