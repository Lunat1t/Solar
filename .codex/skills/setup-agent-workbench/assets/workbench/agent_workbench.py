#!/usr/bin/env python3
"""Agent Workbench 1.0: portable project setup and deterministic development gates.
Python 3.11+, Git; optional Codex CLI. Standard library only. Linux/macOS.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import uuid

VERSION = '1.0.0'
EXCLUDED = {'.git', '.agent-workbench', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'target', 'dist', 'build'}
RULES = '''\n<!-- agent-workbench:start -->
## Agent Workbench
Read `.agent-workbench/task.json` for the current goal, allowed scope and acceptance criteria.
Read `.agent-workbench/config.json` for verification commands; load relevant role guidance only.
Run `python3 .agent-workbench/agent_workbench.py check` after meaningful edits and before claiming completion.
Checks passing is evidence for the checks themselves, not proof of all acceptance criteria.
Reproduce failures before fixing. After two repeated failures revise the hypothesis rather than blindly retry.
Keep changes within the task scope. Treat scope matches as guidance; hooks are not a security sandbox.
For complex changes record the plan and next step in `.agent-workbench/checkpoint.md`.
Use the existing agent runtime to execute tools. Use isolated worktrees for independent writers.
Use a separate read-only reviewer for substantial changes when available. Supply the task, diff and test evidence,
not the implementer's diagnosis. Record unresolved findings; never automatically approve or merge.
Before reporting done, provide acceptance evidence and check for regression risks.
For research, freeze baseline/model/budgets and keep development assistance outside benchmark agents.
<!-- agent-workbench:end -->
'''
ROLES = {
 'plan': 'Start with the observable outcome. State acceptance criteria, constraints, affected files, smallest useful steps, and verification commands. Do not invent product scope. Record checkpoint before long work.',
 'implement': 'Read the task and local conventions. Reproduce the problem. Make a coherent change within scope. Use configured checks. Preserve unrelated user edits. Keep checkpoint and evidence current.',
 'debug': 'Collect reproduction, actual error, and relevant code. Write falsifiable competing hypotheses. Test the cheapest discriminating observation. Fix the supported cause and verify the original reproduction.',
 'review': 'Try to falsify correctness. Inspect the diff and surrounding code. Find concrete behavior regressions, missing checks and violated acceptance criteria. Cite file and reproduction. Do not pad with speculative findings. Separate observed defects from uncertainty.',
 'research': 'Record hypothesis, control, treatment, split, model, budgets, success metric and rejection condition before running. Keep held-out tasks inaccessible to learners. Log failed runs and overhead. Observational failure labels are hypotheses, not causal truth.',
}


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.replace(tmp, path)


def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=check)


def resolve(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Project directory does not exist')
    found = git(root, 'rev-parse', '--show-toplevel', check=False)
    return Path(found.stdout.strip()).resolve() if found.returncode == 0 else root


def files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(names):
            p = Path(base) / name
            if p.is_symlink() or p.suffix in {'.pyc', '.pyo'}:
                continue
            yield p


def fingerprint(root):
    h = hashlib.sha256()
    for p in files(root):
        h.update(str(p.relative_to(root)).encode())
        h.update(b'\0')
        with p.open('rb') as f:
            while block := f.read(1024 * 1024):
                h.update(block)
    return h.hexdigest()


def detect(root):
    checks = []
    if (root / 'package.json').exists():
        scripts = load(root / 'package.json').get('scripts', {})
        pm = 'pnpm' if (root / 'pnpm-lock.yaml').exists() else 'yarn' if (root / 'yarn.lock').exists() else 'npm'
        for name in ('lint', 'typecheck', 'test', 'build'):
            if name in scripts:
                checks.append({'name': name, 'argv': [pm, 'run', name], 'timeout': 180})
    if (root / 'pyproject.toml').exists() or (root / 'requirements.txt').exists():
        import tomllib
        data = tomllib.loads((root / 'pyproject.toml').read_text()) if (root / 'pyproject.toml').exists() else {}
        deps = json.dumps(data) + ((root / 'requirements.txt').read_text() if (root / 'requirements.txt').exists() else '')
        if 'ruff' in deps:
            checks.append({'name': 'lint', 'argv': ['ruff', 'check', '.'], 'timeout': 60})
        if 'mypy' in deps:
            checks.append({'name': 'typecheck', 'argv': ['mypy', '.'], 'timeout': 120})
        if list(root.glob('tests/**/test_*.py')) or list(root.glob('test_*.py')):
            if 'pytest' in deps or (root / 'pytest.ini').exists():
                checks.append({'name': 'test', 'argv': [sys.executable, '-m', 'pytest', '-q'], 'timeout': 180})
            else:
                checks.append({'name': 'test', 'argv': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests' if (root / 'tests').exists() else '.', '-v'], 'timeout': 180})
    if (root / 'Cargo.toml').exists():
        checks += [{'name': 'test', 'argv': ['cargo', 'test', '--locked'], 'timeout': 300}]
    if (root / 'go.mod').exists():
        checks += [{'name': 'test', 'argv': ['go', 'test', './...'], 'timeout': 180}]
    return {'version': VERSION, 'checks': checks, 'agent_timeout': 900, 'max_repair_rounds': 2, 'max_stop_continuations': 1, 'model': None, 'research_mode': False}


def init(root):
    root.mkdir(parents=True, exist_ok=True)
    wb = root / '.agent-workbench'
    wb.mkdir(exist_ok=True)
    config = wb / 'config.json'
    if not config.exists():
        save(config, detect(root))
    source = Path(__file__).resolve()
    target = wb / 'agent_workbench.py'
    if target.exists() and target.resolve() != source and target.read_bytes() != source.read_bytes():
        raise ValueError('Existing workbench differs; preserve it and review upgrade manually')
    if target.resolve() != source:
        target.write_bytes(source.read_bytes())
    agents = root / 'AGENTS.md'
    old = agents.read_text() if agents.exists() else '# Project instructions\n'
    if '<!-- agent-workbench:start -->' not in old:
        agents.write_text(old + RULES)
    ignore = root / '.gitignore'
    old = ignore.read_text() if ignore.exists() else ''
    entries = ['.agent-workbench/runs/', '.agent-workbench/hooks-state/', '.agent-workbench/task.json', '.agent-workbench/checkpoint.md', '.agent-workbench/verification.json']
    missing = [x for x in entries if x not in old.splitlines()]
    if missing:
        ignore.write_text(old.rstrip() + '\n' + '\n'.join(missing) + '\n')
    for role, text in ROLES.items():
        p = wb / 'roles' / (role + '.md')
        p.parent.mkdir(exist_ok=True)
        if not p.exists():
            p.write_text(text + '\n')
    hookfile = root / '.codex' / 'hooks.json'
    hookdata = load(hookfile, {'hooks': {}})
    hookdata.setdefault('hooks', {})
    for event in ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'Stop'):
        cmd = 'python3 "$(git rev-parse --show-toplevel)/.agent-workbench/agent_workbench.py" hook ' + event
        groups = hookdata['hooks'].setdefault(event, [])
        if not any(h.get('command') == cmd for group in groups for h in group.get('hooks', [])):
            groups.append({'hooks': [{'type': 'command', 'command': cmd, 'timeout': 15, 'statusMessage': 'Agent Workbench: ' + event}]})
    save(hookfile, hookdata)
    print(json.dumps({'installed': str(root), 'checks': load(config)['checks'], 'next': 'Inspect commands in .agent-workbench/config.json; trust project and review /hooks in Codex; hooks require a Git repo.'}, ensure_ascii=False))


def validate_config(config):
    for item in config['checks']:
        if not isinstance(item.get('argv'), list) or not item['argv'] or not all(isinstance(x, str) for x in item['argv']):
            raise ValueError('Checks must use nonempty argv arrays, not shell strings')
        if not 0 < item.get('timeout', 180) <= 3600:
            raise ValueError('Check timeout must be within 1..3600 seconds')


def execute(argv, cwd, timeout, out, err, input_text=None):
    """Capture logs; terminate the entire process group on timeout or interrupt."""
    with out.open('w') as stdout, err.open('w') as stderr:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            p.communicate(input_text.encode() if input_text is not None else None, timeout=timeout)
            return p.returncode
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return 124


def check(root):
    wb = root / '.agent-workbench'
    config = load(wb / 'config.json')
    validate_config(config)
    run = wb / 'runs' / ('check-' + uuid.uuid4().hex[:12])
    run.mkdir(parents=True)
    before = fingerprint(root)
    results = []
    for i, item in enumerate(config['checks']):
        start = time.monotonic()
        try:
            code = execute(item['argv'], root, item.get('timeout', 180), run / f'{i}.stdout', run / f'{i}.stderr')
        except OSError as exc:
            (run / f'{i}.stderr').write_text(str(exc))
            code = 127
        results.append({'name': item['name'], 'argv': item['argv'], 'exit_code': code, 'seconds': round(time.monotonic() - start, 3), 'stdout': str(run / f'{i}.stdout'), 'stderr': str(run / f'{i}.stderr')})
    after = fingerprint(root)
    task = load(wb / 'task.json', {})
    result = {'task_id': task.get('id'), 'passed': bool(results) and all(x['exit_code'] == 0 for x in results) and before == after, 'fingerprint': after, 'config_hash': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(), 'tree_unchanged_during_checks': before == after, 'results': results, 'created_at': time.time()}
    save(wb / 'verification.json', result)
    save(run / 'summary.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


def fresh(root):
    wb = root / '.agent-workbench'
    result = load(wb / 'verification.json', {})
    config = load(wb / 'config.json', {})
    task = load(wb / 'task.json', {})
    return bool(result.get('passed') and result.get('task_id') == task.get('id') and result.get('fingerprint') == fingerprint(root) and result.get('config_hash') == hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())


def task(root, goal, acceptance, scope):
    if not goal.strip() or not acceptance:
        raise ValueError('Task requires a goal and acceptance criteria')
    value = {'id': uuid.uuid4().hex[:12], 'goal': goal, 'acceptance': acceptance, 'scope': scope or ['**'], 'status': 'active', 'created_at': time.time()}
    save(root / '.agent-workbench' / 'task.json', value)
    (root / '.agent-workbench' / 'checkpoint.md').write_text('Goal: ' + goal + '\nNext: inspect repository and reproduce the problem.\n')
    print(json.dumps(value, ensure_ascii=False))


def hook(root, event, data):
    wb = root / '.agent-workbench'
    config = load(wb / 'config.json', {})
    taskdata = load(wb / 'task.json', {})
    if not taskdata or taskdata.get('status') != 'active' or config.get('research_mode'):
        return {}
    # Avoid persisting user prompt text, command text, tool output or credentials.
    sid = hashlib.sha256(str(data.get('session_id', 'unknown')).encode()).hexdigest()[:16]
    statefile = wb / 'hooks-state' / (sid + '.json')
    import fcntl
    statefile.parent.mkdir(parents=True, exist_ok=True)
    with statefile.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(statefile, {})
        if state.get('task_id') != taskdata['id']:
            state = {'task_id': taskdata['id'], 'failures': {}, 'continuations': {}}
        result = {}
        if event in ('SessionStart', 'UserPromptSubmit'):
            context = 'Current task: ' + json.dumps(taskdata, ensure_ascii=False) + '\nUse configured checks before completion. Checkpoint: .agent-workbench/checkpoint.md.'
            result = {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': context[:6000]}}
        elif event == 'PostToolUse':
            response = data.get('tool_response', {})
            # Handle structured direct and MCP text-wrapped exec results conservatively.
            if isinstance(response, dict) and 'content' in response:
                for block in response.get('content', []):
                    if block.get('type') == 'text':
                        try:
                            candidate = json.loads(block['text'])
                            if isinstance(candidate, dict) and 'exit_code' in candidate:
                                response = candidate
                                break
                        except (ValueError, KeyError):
                            pass
            code = response.get('exit_code') if isinstance(response, dict) else None
            if isinstance(code, int) and code != 0:
                key = hashlib.sha256(json.dumps(data.get('tool_input', {}), sort_keys=True).encode()).hexdigest()
                state['failures'][key] = state['failures'].get(key, 0) + 1
                if state['failures'][key] == 2:
                    result = {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': 'The same tool invocation failed twice. Revisit the cause using the debug role; collect a discriminating observation before another retry.'}}
            elif code == 0:
                key = hashlib.sha256(json.dumps(data.get('tool_input', {}), sort_keys=True).encode()).hexdigest()
                state['failures'].pop(key, None)
        elif event == 'Stop' and not fresh(root):
            turn = str(data.get('turn_id', 'unknown'))
            count = state['continuations'].get(turn, 0)
            limit = config.get('max_stop_continuations', 1)
            if count < limit and not data.get('stop_hook_active', False):
                state['continuations'][turn] = count + 1
                result = {'decision': 'block', 'reason': 'Verification is missing, failed or stale. Run python3 .agent-workbench/agent_workbench.py check, address supported failures within task scope, and report any remaining blocker. Do not loop or expand the task.'}
            else:
                result = {'systemMessage': 'Workbench verification remains incomplete. Report the blocker; do not claim verified completion.'}
        save(statefile, state)
        return result


def status(root):
    wb = root / '.agent-workbench'
    config = load(wb / 'config.json', {})
    print(json.dumps({'version': VERSION, 'task': load(wb / 'task.json'), 'verification_fresh': fresh(root), 'checks': config.get('checks', []), 'codex_available': __import__('shutil').which('codex') is not None, 'hook_activation': 'Requires trusted project and reviewed /hooks; this script cannot confirm runtime activation.'}, ensure_ascii=False))


def run_agent(root, prompt, readonly, directory, config):
    argv = ['codex', 'exec', '--json', '--sandbox', 'read-only' if readonly else 'workspace-write', '-o', str(directory / 'last-message.md')]
    if config.get('model'):
        argv += ['--model', config['model']]
    argv += ['-']
    code = execute(argv, root, config.get('agent_timeout', 900), directory / 'events.jsonl', directory / 'stderr.log', prompt)
    save(directory / 'process.json', {'exit_code': code, 'readonly': readonly, 'argv': argv, 'created_at': time.time()})
    return code


def orchestrate(root):
    wb = root / '.agent-workbench'
    taskdata = load(wb / 'task.json')
    config = load(wb / 'config.json')
    if not taskdata or taskdata.get('status') != 'active':
        raise ValueError('Create an active task first')
    if not config['checks']:
        raise ValueError('Configure at least one real verification command before orchestration')
    if not __import__('shutil').which('codex'):
        raise ValueError('Codex CLI is not installed here. Run interactively with the skill or install CLI on your development host.')
    if git(root, 'rev-parse', '--is-inside-work-tree', check=False).returncode:
        raise ValueError('CLI orchestration requires a Git repository')
    if not 0 <= config.get('max_repair_rounds', 2) <= 5:
        raise ValueError('max_repair_rounds must be 0..5')
    base = wb / 'runs' / ('agent-' + uuid.uuid4().hex[:12])
    base.mkdir(parents=True)
    spec = json.dumps(taskdata, ensure_ascii=False)
    for roundnum in range(config.get('max_repair_rounds', 2) + 1):
        directory = base / ('implement-' + str(roundnum))
        directory.mkdir()
        prompt = 'Use the implement role in .agent-workbench/roles. Complete this task within scope:\n' + spec
        if roundnum:
            prompt += '\nRead .agent-workbench/verification.json for the failed commands and their logs. Diagnose before fixing.'
        code = run_agent(root, prompt, False, directory, config)
        if code:
            print('Agent process failed; evidence: ' + str(directory))
            return code
        if check(root) == 0:
            review = base / 'review'
            review.mkdir()
            prompt = 'Use .agent-workbench/roles/review.md. Read the actual working diff, surrounding code, task and verification evidence. Try to falsify correctness. Do not edit the repository. Task:\n' + spec
            code = run_agent(root, prompt, True, review, config)
            save(base / 'summary.json', {'checks_fresh': fresh(root), 'review_process_exit': code, 'review': str(review / 'last-message.md'), 'status': 'awaiting_review', 'task_id': taskdata['id']})
            print('Checks passed. Inspect independent review at ' + str(review / 'last-message.md') + '. Acceptance and merge remain separate decisions.')
            return code
    print('Repair budget exhausted. Inspect ' + str(base) + ' and report the blocker.')
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('init')
    sub.add_parser('check')
    sub.add_parser('status')
    sub.add_parser('run')
    p = sub.add_parser('finish')
    p.add_argument('--evidence', action='append', required=True)
    p = sub.add_parser('task')
    p.add_argument('--goal', required=True)
    p.add_argument('--accept', action='append', required=True)
    p.add_argument('--scope', action='append')
    p = sub.add_parser('hook')
    p.add_argument('event', choices=['SessionStart', 'UserPromptSubmit', 'PostToolUse', 'Stop'])
    p = sub.add_parser('worktree')
    p.add_argument('--branch', required=True)
    p.add_argument('--path', required=True)
    p = sub.add_parser('git-hook')
    args = parser.parse_args()
    root = resolve(args.root)
    if args.action == 'init':
        init(root)
    elif args.action == 'check':
        return check(root)
    elif args.action == 'status':
        status(root)
    elif args.action == 'task':
        task(root, args.goal, args.accept, args.scope)
    elif args.action == 'run':
        return orchestrate(root)
    elif args.action == 'finish':
        if not fresh(root):
            raise ValueError('Current verification must pass before completion')
        current = load(root / '.agent-workbench/task.json')
        if not current or len(args.evidence) != len(current['acceptance']) or any(not x.strip() for x in args.evidence):
            raise ValueError('Supply one nonempty evidence item for each acceptance criterion, in order')
        current['status'] = 'completed'
        current['acceptance_evidence'] = args.evidence
        current['completed_at'] = time.time()
        save(root / '.agent-workbench/task.json', current)
        record = root / '.agent-workbench/runs' / ('task-' + current['id'])
        save(record / 'acceptance.json', current)
        print('Task completed with supplied acceptance evidence. This records evidence; it does not independently judge it.')
    elif args.action == 'hook':
        print(json.dumps(hook(root, args.event, json.load(sys.stdin)), ensure_ascii=False))
    elif args.action == 'worktree':
        if args.branch.startswith('-') or args.path.startswith('-'):
            raise ValueError('Invalid branch or path')
        destination = Path(args.path).resolve()
        git(root, 'worktree', 'add', '-b', args.branch, str(destination))
        init(destination)
    elif args.action == 'git-hook':
        hookpath = Path(git(root, 'rev-parse', '--git-path', 'hooks/pre-commit').stdout.strip())
        if not hookpath.is_absolute():
            hookpath = root / hookpath
        content = '#!/bin/sh\nexec python3 "$(git rev-parse --show-toplevel)/.agent-workbench/agent_workbench.py" check\n'
        if hookpath.exists() and hookpath.read_text() != content:
            raise ValueError('Existing pre-commit hook preserved. Integrate the check command into your existing hook manager.')
        hookpath.parent.mkdir(parents=True, exist_ok=True)
        hookpath.write_text(content)
        hookpath.chmod(0o755)
        print('Git pre-commit installed. It checks the working tree; inspect staged diff separately for partial commits.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print('Workbench error: ' + str(exc), file=sys.stderr)
        sys.exit(2)
