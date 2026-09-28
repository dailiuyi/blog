"""Human ready-label intake, with durable authorization and delivery receipts."""
from datetime import datetime, timezone

from .agents import AgentError
from .core import PipelineError, StateStore, extract_plan, plan_hash, validate_plan
from .github import APIError

READY = 'symphony:ready'


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed
    except (AttributeError, TypeError, ValueError) as exc:
        raise PipelineError('label_event_time_invalid') from exc


class IntakeStore(StateStore):
    """Reuse atomic writes/locks, but keep receipts out of the task-state glob."""
    def __init__(self, config):
        super().__init__(config['control_root'], config['repository'], 1)
        self.home = self.home.parent / 'label-intake'
        self.state_path = self.home / 'receipts.json'
        self.events_path = self.home / 'events.jsonl'


def ready_event(controller, issue):
    from .controller import labels_of
    if issue.get('state') != 'open' or 'pull_request' in issue or READY not in labels_of(issue):
        raise PipelineError('ready_label_required')
    events = controller.github.issue_events(issue['number'])
    # GitHub returns issue events in chronological order. Preserve that order,
    # including events sharing a second; IDs are identifiers, not timestamps.
    relevant = [e for e in events if e.get('event') in {'labeled', 'unlabeled'}
                and ((e.get('label') or {}).get('name') == READY
                     or (e.get('label') or {}).get('name', '').startswith(
                         ('symphony:model:', 'symphony:effort:')))]
    if not relevant or relevant[-1].get('event') != 'labeled' or (relevant[-1].get('label') or {}).get('name') != READY:
        raise PipelineError('add_ready_after_model_labels')
    event = relevant[-1]
    actor = event.get('actor') or {}
    if (actor.get('type') != 'User' or actor.get('login') not in controller.config.get('allowed_users', [])
            or event.get('performed_via_github_app')):
        raise PipelineError('ready_label_actor_not_authorized')
    if not isinstance(event.get('id'), int) or isinstance(event['id'], bool):
        raise PipelineError('label_event_id_invalid')
    edited = controller.github.issue_last_edited_at(issue['number'])
    if edited and timestamp(edited) >= timestamp(event.get('created_at')):
        raise PipelineError('issue_edited_reapply_ready')
    return event


def authorization(controller, issue, event):
    from .controller import route_for, labels_of
    plan = validate_plan(extract_plan(issue.get('body') or ''), controller.config)
    return {'source': 'github_ready_label', 'event_id': event['id'],
            'actor': event['actor']['login'], 'ready_at': event['created_at'],
            'plan_hash': plan_hash(plan), 'route': route_for(labels_of(issue), controller.config)}


def verify_registration(controller, issue, approved):
    current = authorization(controller, issue, ready_event(controller, issue))
    if current != approved:
        raise PipelineError('label_authorization_changed')


def scan(controller):
    """No inference here: validate, register and enqueue each new human intent."""
    if not controller.config.get('label_intake', False):
        return []
    store = IntakeStore(controller.config)
    outcomes = []
    with store.lock():
        receipt = store.load()
        if receipt is None:
            enabled_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            baseline = [e['id'] for issue in controller.github.issues_with_label(READY)
                        for e in controller.github.issue_events(issue['number'])
                        if e.get('event') == 'labeled' and (e.get('label') or {}).get('name') == READY]
            receipt = {'enabled_at': enabled_at, 'baseline_events': baseline, 'events': {}}
            store.save(receipt)  # Never retroactively authorize existing labels.

        def deliver(record):
            number = record['issue']
            if record['status'] == 'rejected':
                ready_events = [e for e in controller.github.issue_events(number)
                                if e.get('event') == 'labeled' and (e.get('label') or {}).get('name') == READY]
                # A delayed error delivery must not cancel a newer human request.
                if ready_events and ready_events[-1]['id'] == record['event_id']:
                    controller.github.set_labels(number, ['symphony:blocked'], [READY])
                controller.github.notify(number,
                    '自动入队未通过：`' + record['reason'] + '`。请修正计划/模型配置，'
                    '再移除并重新添加 `symphony:ready`；无需手动注册。',
                    'label-intake-' + str(record['event_id']))
            record['delivery_pending'] = False
            store.save(receipt)

        for record in receipt['events'].values():
            if record.get('delivery_pending'):
                try:
                    deliver(record)
                except APIError as exc:
                    outcomes.append({'issue': record['issue'], 'error': str(exc)})

        for listed in controller.github.issues_with_label(READY):
            number = listed['number']
            event = None
            try:
                task_store = StateStore(controller.config['control_root'], controller.config['repository'], number)
                state = task_store.load()
                # Active runs own their labels. In particular, a controller using
                # the owner's PAT can appear as that owner in GitHub events.
                if state and state['phase'] not in {'registered', 'ready', 'blocked', 'closed'}:
                    continue
                issue = controller.github.issue(number)
                events = controller.github.issue_events(number)
                ready_events = [e for e in events if e.get('event') == 'labeled'
                                and (e.get('label') or {}).get('name') == READY]
                if not ready_events:
                    continue
                event = ready_events[-1]
                if (timestamp(event.get('created_at')) < timestamp(receipt['enabled_at'])
                        or event['id'] in receipt.get('baseline_events', [])):
                    continue
                key = str(event['id'])
                record = receipt['events'].get(key)
                if record and record['status'] in {'queued', 'rejected', 'ignored'}:
                    continue
                if state and state['phase'] == 'closed':
                    raise PipelineError('closed_task_create_new_issue')
                approved = (record or {}).get('authorization')
                if approved is None:
                    # A manually registered task's existing ready event is not
                    # a new request. A later remove/re-add can start a new cycle.
                    if state and timestamp(event['created_at']) <= timestamp(state['created_at']):
                        continue
                    event = ready_event(controller, issue)
                    approved = authorization(controller, issue, event)
                    record = {'issue': number, 'event_id': event['id'], 'status': 'pending',
                              'authorization': approved}
                    receipt['events'][key] = record
                    store.save(receipt)
                verify_registration(controller, issue, approved)
                if state and state.get('label_authorization') == approved:
                    if state['phase'] != 'registered':
                        record['status'] = 'queued'  # Already dispatched before a crash.
                        store.save(receipt)
                        continue
                else:
                    controller.register(number, deepseek_consent=approved['route']['model'] == 'deepseek-flash',
                                        update_plan=bool(state), label_authorization=approved)
                controller.enqueue(number)
                record['status'] = 'queued'
                store.save(receipt)
                outcomes.append({'issue': number, 'label_event': event['id'], 'status': 'queued'})
            except (PipelineError, APIError, AgentError) as exc:
                if (getattr(exc, 'transient', False) or str(exc) in {'state_locked', 'task_active'}
                        or event is None):
                    outcomes.append({'issue': number, 'error': str(exc)})
                    continue
                record = {'issue': number, 'event_id': event['id'], 'status': 'rejected',
                          'reason': str(exc), 'delivery_pending': True}
                receipt['events'][str(event['id'])] = record
                store.save(receipt)
                try:
                    deliver(record)
                except APIError:
                    pass  # Durable delivery retries even after ready is removed.
                outcomes.append({'issue': number, 'error': str(exc)})
    return outcomes
