# 自动验收与返工

项目知识从 [文档入口](README.md) 开始；用户已确定的行为和边界见 [决策记录](DECISIONS.md)。本文件维护当前操作方法，不替代决策来源。

Symphony 发现已入队 Issue；公共 Python 包 `symphony_acceptance` 管理编码、检查、独立验收和最终 PR。博客命令在 `config/symphony-blog.json`。页面类修改包含实际浏览器检查与看图审查；人工验收记录单独保存，合并仍由用户授权。

## 运行环境和安装

使用 Linux / WSL、Python 3.10+、Git、Codex app-server；博客另需 Node 24、Go 1.25、Bash。使用 `python3 scripts/install_symphony_acceptance.py` 安装按内容指纹命名的外部版本，确认没有活动任务后加 `--activate` 切换 current。不要从 Issue 的可写源码启动控制器，也不要在任务运行时替换版本。

安装包同时保存 AGENTS、WORKFLOW、文档入口、决策记录和验收文档；这些文件也参与版本指纹。安装快照用于追溯该服务版本的规则，源码工作树仍是修改入口。文档保存、Git 提交、远端发布和运行安装分别核对；安装成功不会把未提交的文档发布到仓库，也不会改变已有 Issue 的计划。

当前运行时以 Codex CLI 0.154.0 验证。自定义权限通过 app-server 进程参数加载；仅在会话请求中传入同名配置，不能保证该版本实际加载权限表。升级 Codex 后应先验证只读会话、受保护目录以及编码工作区的 Git 元数据权限，再启用任务。

WSL 的 Codex 登录文件应保存在权限为 0600 的独立 CODEX_HOME 中，并把该目录加入 denied_read_paths。不要把 auth.json 符号链接到被拒绝访问的 Windows 目录：当前运行时创建沙盒时会解析链接，导致启动失败。主机已有登录文件和备份保持在编码工作区之外；不要把凭据加入安装包或仓库。

`WORKFLOW.md` 通过 codex.command 启动每任务工作进程。另需常驻评论监听器：

```sh
export PYTHONPATH="$HOME/.local/symphony-acceptance/current/scripts"
python3 -m symphony_acceptance --config "$HOME/.local/symphony-acceptance/current/config/symphony-blog.json" watch
```

GitHub 凭据只留在控制器侧；编码和验收子进程剥离凭据。检查命令通过 Codex acceptance-check 沙盒执行，只能写任务工作区和缓存，日志由外层进程保存。沙盒失效会阻塞，不降级为无沙盒执行。启动或切换前检查 ready 队列及运行中任务，保留原工作区。

安装后可通过 `scripts/start_symphony_acceptance.sh` 同时运行 runner 与监听器。设置 `SYMPHONY_BINARY` 为现有原生 Symphony 可执行文件路径，并通过标准输入传入 GitHub token；脚本不把 token 写入文件。Windows 的既有启动器可以把 `gh auth token` 输出直接管道传入 WSL 脚本。日志和 PID 文件位于外部安装目录。

Symphony 会从 codex.command 环境中移除 tracker 的 GitHub 凭据。启动脚本为可信控制器保留专用的 SYMPHONY_ACCEPTANCE_GITHUB_TOKEN 别名；编码、验收、检查和 Git 子进程均剥离该变量。缺少控制器凭据时直接报阻塞，不尝试匿名发布。

仓库配置的 `denied_read_paths` 禁止工具读取控制器记录和凭据目录；编码、验收会话均关闭工具网络访问。检查命令允许依赖下载，但同样拒绝读取这些目录。新的仓库配置应按所在主机更新路径。明确的临时网络错误最多重试两次，每次保留日志；源码变化会立即阻塞，重试不会消耗代码返工额度。

`agent_denied_read_paths` 可单独配置模型会话的额外拒绝目录，未设置时沿用 `denied_read_paths`。模型会话本身默认拒绝整个文件系统，只开放系统工具、Codex 可执行文件和当前工作区；`.git` 始终只读。博客在 WSL 中使用这一默认拒绝规则隔离 Windows 挂载目录，避免 Codex 0.154 对这些目录重复设置拒绝规则时发生 bwrap 挂载错误；检查命令继续使用完整的显式拒绝列表。

## 入队和人工操作

Issue 的 workflow-plan 保留 title、acceptance、allowedPaths、checks 四个字段。acceptance 每个非空行是一条条件。注册会快照化计划。

博客已启用 `label_intake: true`。日常使用只需：写好计划，选择模型/思考深度标签，最后添加 `symphony:ready`。监听器自动核对标签操作者、计划和模型，再登记授权、注册并入队，自动添加 `symphony:acceptance`，无需执行注册命令或再到聊天中确认。

允许账号（当前为 `dailiuyi`）选择 `symphony:model:deepseek-flash` 并最后添加 ready，表示授权将该 issue 的提示及执行所需工作区内容发送至 `https://api.deepseek.com`。授权记录包含账号、GitHub 事件 ID、时间、计划指纹和模型/深度。单独的模型标签不表示启动。控制器使用同一账号的 token 时，GitHub 无法区分手动点击和该账号下的工具操作；该账号及其凭据应由获准操作者控制，事件去重和任务状态防止控制器自己的标签操作形成循环。

首次启动记录持久化生效时间，不追认已有 ready 标签。要提交旧 issue，移除再添加 ready。必须先完成正文和模型标签，再加 ready；与正文修改处于同一秒的 ready 会保守拒绝，重新添加即可。模型或计划修改后需要新的 ready 事件；正在运行的任务不会被自动覆盖，等待其结束后再重新添加。重复扫描、重启和中途断线不会重复注册同一次请求。

失败会在 issue 写明原因、添加 blocked 并移除 ready；修正后重新添加 ready 即可。该策略不改变合并、部署和人工页面验收权限。手动回退命令仍可用于运维：

```sh
python3 -m symphony_acceptance --config <config.json> register --issue 123
python3 -m symphony_acceptance --config <config.json> enqueue --issue 123
python3 -m symphony_acceptance --config <config.json> status --issue 123
```

手动 register 验证实际模型能力；enqueue 最后添加 ready 标签。手动 DeepSeek 注册必须先取得本 Issue 的明确授权，再传 --deepseek-consent；更换计划后不自动沿用授权。自动标签入口会完成对应步骤。

人工 rework 的修改要求会加入后续编码和逐项验收条件，并纳入验收报告的计划指纹；Issue 原始计划指纹继续用于检查未经登记的需求变更。允许路径和必需检查仍由已登记计划约束。

在登记 Issue 或 PR 中，由允许的账号单独评论 `/symphony rework 修改要求` 可重新发起。阻塞解除后使用 `/symphony resume`。普通讨论、代码块内命令、其他账号和机器人均不触发派发。同一评论只处理一次；人工重新发起得到新的三次修复额度，旧证据保留。

## 自动反馈与复验循环

独立审查使用四种结论：`pass` 表示条件满足；`rework` 表示有证据的源码缺陷或未实现需求；`recapture` 表示仅缺少可补齐的浏览器截图或交互证据；`blocked` 表示环境、权限或必须由用户澄清的问题。不能以缺少截图为由要求编码 agent 越过文件权限。

`rework` 自动把完整 findings、证据和 required_fix 写入下一轮编码提示。编码完成后重新检查、发布候选提交、独立验收并核对 CI。`recapture` 把完整意见传入下一轮浏览器计划，在同一候选 SHA 上补拍并重新审查，不要求源码变化或新提交。两条路径共用三次修复额度；额度耗尽或源码返工没有变化时保留最新反馈并阻塞。仅建议性的意见不自动触发返工，普通 PR 评论仍不是调度指令。

浏览器计划的结构/属性校验失败也自动把具体错误返回原审查会话修正，计入同一三次额度；原始计划先保存再校验，便于追溯。属性白名单同时体现在输出 schema 和执行校验中，包含主题 data 属性及 aria-pressed。Windows 浏览器连接 WSL 临时预览端口时，仅连接拒绝/重置允许三次短重试；持续不可达仍报告失败，不修改全局网络或放宽访问范围。

同计划重新登记和人工恢复保留最新失败反馈（包括比旧审查通过结论更新的 CI 失败），证据按运行及轮次保存，旧报告不覆盖。截图计划每页最多24步，其中1至4个显式 `screenshot` 步骤必须覆盖1440、390、320px；按顺序执行主题切换后滚动到指定区域截图。博客配置要求 Summer 和 Night，控制器从截图时实际 DOM 主题元数据核对覆盖，不能由标签文字代替。初始首屏截图仍保留，但不能替代受影响区域及各要求主题的截图。显式截图失败时，带有步骤编号的失败记录与已有截图交给审查者纠正计划；未知、重复、绑定不符或缺少初始图仍拒绝。PR 回复显示补证据或源码返工状态及已使用额度。

## 证据与交付

面板通过控制器转发内部编码与验收会话的真实 tokenUsage；各会话按稳定标识去重后累加，结束时的重复统计不会再次计数。控制器阶段和最近模型活动用于心跳，心跳本身不表示模型正在生成文字。转发只包含阶段、角色、事件类型和用量，不转发工具参数、提示或源码。历史运行的本地 evidence 保留原统计，新版面板从升级后的运行开始统计。

独立验收检出使用完整 Git 克隆，并在开放给只读验收代理前校验基线、当前提交和完整差异。不得依赖代理联网按需读取 partial clone 的历史对象；此前的检出和证据保持不变。

PR 自动验收计划只写发布前可完成的源码、范围与检查条件。本地桌面/移动截图按准确提交保存，记录视口、路径、截图哈希与交互结果；人工视觉确认独立记录。线上验收在获准合并部署后核对 RELEASE、页面与健康接口，注明实际版本和时间。当前线上旧版本的观察必须标为基线或待部署，不能充当新版本验收，也不应成为发布前无法满足的循环条件。

编码结果使用结构化 JSON（`status: ready|blocked` 与非空 `summary`）。控制器同时发送协议的 outputSchema 和明确的文字格式要求；不会把 Markdown 修改说明推断成成功。`invalid_coder_result:not_json` 表示返回不是 JSON，`schema_mismatch` 表示字段或类型不符；原始返回仍保存在 evidence，修复后沿用原工作区恢复。

编码结果的 `ready` 只表示源码已就绪，可以进入控制器检查、截图和独立验收，不代表整条验收已通过。源码已经符合要求、唯一待办是控制器负责的检查或截图时，编码 agent 应返回 `ready` 并说明待补证据；`blocked` 仅表示批准范围内的源码工作无法继续或缺少用户必须澄清的信息。

外部控制目录按仓库和 Issue 隔离，保存原子更新状态、事件、命令日志、每轮验收报告。发布意图先于远端写入落盘；候选提交创建后，先持久化准确 SHA，再推进分支。超时后必须同时匹配已记录的 SHA、目标树和父提交，不强推、不重复创建 PR。缺少候选 SHA 的旧记录不会自动认领远端提交。

提交状态 symphony/acceptance 使用 pending、failure、error、success。通过报告包含准确 head/base SHA、计划指纹、验收模型和逐条证据。新提交或基线变化使旧结论失效。

草稿转正式要求独立检查、模型验收、当前提交的必要 CI 全部通过。自动通过不是人工批准，也不是已经部署。第一版不修改分支保护，仓库管理员仍能手动绕过状态。

交付前后均核对远端提交和计划，监听器继续检查已就绪 PR 的提交、基线及 CI 变化。GitHub 的转换草稿和提交状态写入不是原子操作；没有分支保护时，人工合并前仍应确认当前提交上的验收状态。监听器或 runner 任一退出会停止另一进程，避免留下没有恢复监听的编码服务。

## 其他仓库与验证

复制配置并设置仓库、外部状态目录、工作区、允许操作者和固定检查 argv。Issue 只能选择已有检查名，不能提供任意命令。公共实现不写死博客逻辑；第二套模拟配置验证可复用性。

```sh
PYTHONPATH=scripts python3 -m unittest discover -s tests -p 'test_acceptance*.py' -v
python3 -m unittest discover -s scripts -p 'test_symphony*.py'
node scripts/workflow_verify.mjs self-test
```

模拟测试验证返工、恢复、指纹和幂等性；真实隔离任务验证运行时权限、工具和 GitHub 状态。模拟通过、真实闭环、人工合并、线上部署分别报告。

## 审查回复与页面证据

PR 保留一条持续更新的验收回复，不再把相同全文另发为通知。回复先说明本次改动、检查结果和合并结论；逐条源码证据折叠展示。PR 正文采用稳定的需求概述，不保留 publishing 或尚未发布等过期状态。合并后同步更新审查回复，不把合并推断为浏览器验证或人工视觉批准。

当允许路径涉及页面、组件、布局、样式或内容时，审查模型先只读源码并生成有限操作的浏览器计划。可信控制器使用候选提交的独立构建，打开 1440、390、320px 页面，保存截图并执行文本、元素、属性、点击、键盘及锚点检查，再将实际结果和截图交给同一审查会话。浏览器缺失、证据不足或失败检查不能被源码推断覆盖。

当前主机使用已安装的 Windows Chrome 和 Codex 随附 Playwright，路径配置在 browser 中，不自动安装浏览器。预览只服务 dist 中的文件，隔离个人浏览器会话，禁止任意外站导航与统计写入；忽略的媒体文件可从配置的正式站点 /media/ 路径读取。截图不是线上部署证明。

截图和脱敏检查记录通过 GitHub Git API 保存到 codex/acceptance-evidence 专用分支，以证据提交 SHA 固定链接嵌入回复；不修改应用分支。并发更新使用快进重试，失败不强推。所有原始执行记录继续保存在主机控制目录。
