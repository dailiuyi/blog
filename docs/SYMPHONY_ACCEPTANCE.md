# 自动验收与返工

Symphony 发现已入队 Issue；公共 Python 包 `symphony_acceptance` 管理编码、检查、独立验收和最终 PR。博客命令在 `config/symphony-blog.json`。页面视觉、最终验收和合并由人负责。

## 运行环境和安装

使用 Linux / WSL、Python 3.10+、Git、Codex app-server；博客另需 Node 24、Go 1.25、Bash。使用 `python3 scripts/install_symphony_acceptance.py` 安装按内容指纹命名的外部版本，确认没有活动任务后加 `--activate` 切换 current。不要从 Issue 的可写源码启动控制器，也不要在任务运行时替换版本。

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

Issue 的 workflow-plan 保留 title、acceptance、allowedPaths、checks 四个字段。acceptance 每个非空行是一条条件。注册会快照化计划；新计划需重新注册。

```sh
python3 -m symphony_acceptance --config <config.json> register --issue 123
python3 -m symphony_acceptance --config <config.json> enqueue --issue 123
python3 -m symphony_acceptance --config <config.json> status --issue 123
```

register 验证实际模型能力；enqueue 最后添加 ready 标签。DeepSeek 必须先取得本 Issue 的明确授权，再传 --deepseek-consent；更换计划后不自动沿用授权。

人工 rework 的修改要求会加入后续编码和逐项验收条件，并纳入验收报告的计划指纹；Issue 原始计划指纹继续用于检查未经登记的需求变更。允许路径和必需检查仍由已登记计划约束。

在登记 Issue 或 PR 中，由允许的账号单独评论 `/symphony rework 修改要求` 可重新发起。阻塞解除后使用 `/symphony resume`。普通讨论、代码块内命令、其他账号和机器人均不触发派发。同一评论只处理一次；人工重新发起得到新的三次修复额度，旧证据保留。

## 证据与交付

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
