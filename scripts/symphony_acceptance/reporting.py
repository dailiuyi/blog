"""Human-facing review results; machine evidence remains in the state store."""


def render_summary(state, config):
    review = state.get('review') or {}
    browser = state.get('browser_review') or {}
    human = state.get('human_acceptance') or {}
    phase = state['phase']
    merged = phase == 'closed' and state.get('pr_merged')
    passed = phase == 'ready' and review.get('verdict') == 'pass'
    headline = ('已合并。' if merged else '自动审查通过，可以合并。' if passed
                else '暂不能合并，需要处理以下问题。' if phase == 'blocked'
                else '本次任务已结束。' if phase == 'closed' else '自动审查进行中。')
    lines = ['**' + headline + '**', '', '### 本次改动', '', state['plan']['title'], '',
             '### 检查结果', '']
    if review.get('summary'):
        lines.append(review['summary'])
        lines.append('')
    if not review and state.get('feedback') and phase in {'coding', 'reviewing'}:
        target = '编码 agent 修改源码' if phase == 'coding' else '浏览器补充证据并重新审查'
        lines.append(f"上一轮意见已交给{target}，已使用 {state.get('repairs', 0)}/{config.get('max_repairs', 3)} 次自动修复额度。")
        lines.append('')
    for key, label in [('checks', '提交前检查'), ('review_checks', '独立检出检查')]:
        checks = state.get(key) or {}
        if checks:
            names = '、'.join(item['name'] for item in checks.get('checks', []))
            status = '通过' if checks.get('status') == 'passed' else '未通过'
            lines.append(f'- {label}：{status}（{names}）。')
    if state.get('ci'):
        lines.append('- GitHub CI：' + ('通过。' if state['ci']['state'] == 'success' else '尚未通过。'))
    if browser:
        status = '通过' if browser.get('status') == 'passed' else '未通过'
        lines.append(f"- 浏览器验证：{status}，{len(browser.get('pages', []))} 组页面/视口；结果对应候选提交的本地构建。")
    else:
        lines.append('- 浏览器验证：本轮未执行；源码检查不等于页面与交互验证。')
    if human and human.get('head_sha') == state.get('head_sha'):
        lines.append(f"- 人工验收：{human['actor']} 已确认需求达到（{human['recorded_at']}）。")
    if state.get('agent_blocker') or state.get('reason'):
        lines += ['', '待处理：' + str(state.get('agent_blocker') or state['reason'])]
    for item in review.get('findings', []):
        lines += [f"- {'必须修复' if item['blocking'] else '建议'}：{item['evidence']} 修复方式：{item['required_fix']}"]
    if browser.get('pages'):
        lines += ['', '### 页面与交互', '']
        tested = list(dict.fromkeys(item['label'] for page in browser['pages'] for item in page.get('checks', [])))
        lines.append('实际验证：' + '、'.join(tested) + '。')
        for page in browser['pages']:
            label = f"{page['path']} · {page['width']}px"
            failures = [item for item in page.get('checks', []) if not item['passed']]
            results = '通过' if not failures else '；'.join(item['label'] + '未通过：' + item.get('error', '') for item in failures)
            lines.append(f'- {label}：{results}。')
        lines += ['', '<details>', '<summary>查看候选版本截图</summary>']
        for shot in browser.get('screenshots', []):
            if shot.get('url'):
                detail = ' · ' + shot['label'].replace('[', '').replace(']', '') if shot.get('label') else ''
                lines += ['', f"![{shot['path']} · {shot['width']}px{detail}]({shot['url']})"]
        lines += ['', '</details>']
    lines += ['', '<details>', '<summary>提交与详细审查证据</summary>', '',
              f"候选提交：`{state.get('head_sha', '尚未发布')}`；基线：`{state.get('base_sha', '尚未确定')}`。",
              f"审查模型：{config['reviewer']['model']} / {config['reviewer']['effort']}。"]
    for item in review.get('criteria', []):
        lines.append(f"- {'满足' if item['status'] == 'met' else '未确认' if item['status'] == 'blocked' else '未满足'}：{item['criterion']} — {item['evidence']}")
    if browser.get('manifest_url'):
        lines += ['', f"[浏览器检查记录与截图指纹]({browser['manifest_url']})"]
    lines += ['', '</details>']
    if not merged and phase == 'ready':
        lines += ['', '下一步：按本次审查结果决定是否合并；以上截图不代表线上部署。']
    elif phase == 'blocked':
        lines += ['', '修复后可评论 `/symphony rework 修改要求`；环境问题解除后可评论 `/symphony resume`。']
    return '\n'.join(lines)


def pr_description(state):
    return (state['plan']['title'] + f"\n\n关联需求：#{state['issue']}。\n\n"
            '自动审查结论、检查结果和候选版本截图见本 PR 的验收回复。')
