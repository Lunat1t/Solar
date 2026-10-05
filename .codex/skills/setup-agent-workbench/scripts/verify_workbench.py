#!/usr/bin/env python3
"""Run deterministic integration checks for bundled Agent Workbench."""
import contextlib
import importlib.util
import io
import json
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

source = Path(__file__).resolve().parents[1] / 'assets/workbench/agent_workbench.py'
spec = importlib.util.spec_from_file_location('workbench', source)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'AGENTS.md').write_text('Existing project rules\n')
        (self.root / '.codex').mkdir()
        w.save(self.root / '.codex/hooks.json', {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'existing-policy'}]}]}})
        with contextlib.redirect_stdout(io.StringIO()):
            w.init(self.root)
        self.wb = self.root / '.agent-workbench'
    def tearDown(self):
        self.tmp.cleanup()
    def configured(self, code='print("verified")'):
        config = w.load(self.wb / 'config.json')
        config['checks'] = [{'name': 'test', 'argv': [sys.executable, '-c', code], 'timeout': 5}]
        w.save(self.wb / 'config.json', config)
        with contextlib.redirect_stdout(io.StringIO()):
            w.task(self.root, 'Fix behavior', ['Exact expected output'], ['src/**'])
    def verify(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return w.check(self.root)
    def test_init_preserves_existing_and_is_idempotent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            w.init(self.root)
        self.assertIn('Existing project rules', (self.root / 'AGENTS.md').read_text())
        self.assertEqual((self.root / 'AGENTS.md').read_text().count('agent-workbench:start'), 1)
        groups = w.load(self.root / '.codex/hooks.json')['hooks']['Stop']
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['hooks'][0]['command'], 'existing-policy')
    def test_empty_checks_fail(self):
        self.assertEqual(self.verify(), 1)
        self.assertFalse(w.fresh(self.root))
    def test_failing_command(self):
        self.configured('raise SystemExit(7)')
        self.assertEqual(self.verify(), 1)
        self.assertEqual(w.load(self.wb / 'verification.json')['results'][0]['exit_code'], 7)
    def test_missing_command(self):
        self.configured()
        config = w.load(self.wb / 'config.json')
        config['checks'][0]['argv'] = ['no-such-workbench-command']
        w.save(self.wb / 'config.json', config)
        self.assertEqual(self.verify(), 1)
        self.assertEqual(w.load(self.wb / 'verification.json')['results'][0]['exit_code'], 127)
    def test_source_edit_invalidates_checks(self):
        self.configured()
        self.assertEqual(self.verify(), 0)
        self.assertTrue(w.fresh(self.root))
        (self.root / 'src.py').write_text('broken = True\n')
        self.assertFalse(w.fresh(self.root))
    def test_config_edit_invalidates_checks(self):
        self.configured()
        self.verify()
        config = w.load(self.wb / 'config.json')
        config['checks'][0]['argv'][-1] = 'raise SystemExit(9)'
        w.save(self.wb / 'config.json', config)
        self.assertFalse(w.fresh(self.root))
    def test_new_task_invalidates_checks(self):
        self.configured()
        self.verify()
        with contextlib.redirect_stdout(io.StringIO()):
            w.task(self.root, 'Different task', ['Different result'], ['**'])
        self.assertFalse(w.fresh(self.root))
    def test_edits_during_check_fail_verification(self):
        self.configured('from pathlib import Path; Path("source.py").write_text("changed")')
        self.assertEqual(self.verify(), 1)
        self.assertFalse(w.load(self.wb / 'verification.json')['tree_unchanged_during_checks'])
    def test_stop_continues_only_once(self):
        self.configured()
        data = {'session_id': 'a', 'turn_id': '1', 'stop_hook_active': False}
        first = w.hook(self.root, 'Stop', data)
        second = w.hook(self.root, 'Stop', data)
        self.assertEqual(first.get('decision'), 'block')
        self.assertNotIn('decision', second)
    def test_stop_active_no_continuation(self):
        self.configured()
        self.assertNotIn('decision', w.hook(self.root, 'Stop', {'stop_hook_active': True}))
    def test_valid_check_no_stop_continuation(self):
        self.configured()
        self.verify()
        self.assertEqual(w.hook(self.root, 'Stop', {}), {})
    def test_repeated_failure_and_recovery(self):
        self.configured()
        data = {'tool_input': {'command': 'failing'}, 'tool_response': {'exit_code': 1}}
        self.assertEqual(w.hook(self.root, 'PostToolUse', data), {})
        self.assertIn('hookSpecificOutput', w.hook(self.root, 'PostToolUse', data))
        data['tool_response']['exit_code'] = 0
        self.assertEqual(w.hook(self.root, 'PostToolUse', data), {})
        data['tool_response']['exit_code'] = 1
        self.assertEqual(w.hook(self.root, 'PostToolUse', data), {})
    def test_research_mode_silent(self):
        self.configured()
        config = w.load(self.wb / 'config.json')
        config['research_mode'] = True
        w.save(self.wb / 'config.json', config)
        self.assertEqual(w.hook(self.root, 'Stop', {}), {})
        self.assertEqual(w.hook(self.root, 'SessionStart', {}), {})
    def test_finish_requires_evidence_and_stops_steering(self):
        self.configured()
        args = [sys.executable, str(source), '--root', str(self.root), 'finish', '--evidence', 'test actual-output passed']
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 2)
        self.verify()
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 0)
        self.assertEqual(w.load(self.wb / 'task.json')['status'], 'completed')
        self.assertEqual(w.hook(self.root, 'SessionStart', {}), {})
    def test_orchestration_repair_and_readonly_review(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.configured('from pathlib import Path; assert Path("fix.py").read_text() == "fixed"')
        bindir = self.root / '.agent-workbench/bin'
        bindir.mkdir()
        fake = bindir / 'codex'
        fake.write_text("#!/usr/bin/env python3\n" + "import sys,json\nfrom pathlib import Path\nprompt=sys.stdin.read()\nargs=sys.argv\nmode=args[args.index('--sandbox')+1]\np=Path('.agent-workbench/runs/calls.json')\ncalls=json.loads(p.read_text()) if p.exists() else []\ncalls.append(mode)\np.write_text(json.dumps(calls))\nif mode=='workspace-write': Path('fix.py').write_text('fixed' if len(calls)>1 else 'wrong')\nPath(args[args.index('-o')+1]).write_text('Review: examine acceptance evidence' if mode=='read-only' else 'Implemented')\nprint(json.dumps({'type':'turn.completed'}))\n")
        fake.chmod(0o755)
        with patch.dict(os.environ, {'PATH': str(bindir) + os.pathsep + os.environ['PATH']}):
            with contextlib.redirect_stdout(io.StringIO()):
                result = w.orchestrate(self.root)
        self.assertEqual(result, 0)
        self.assertEqual(w.load(self.wb / 'runs/calls.json'), ['workspace-write', 'workspace-write', 'read-only'])
        summaries = list((self.wb / 'runs').glob('agent-*/summary.json'))
        self.assertEqual(w.load(summaries[0])['status'], 'awaiting_review')
        self.assertEqual(w.load(self.wb / 'task.json')['status'], 'active')
    def test_timeout(self):
        code = w.execute([sys.executable, '-c', 'import time; time.sleep(30)'], self.root, .1, self.root / '.agent-workbench/out', self.root / '.agent-workbench/err')
        self.assertEqual(code, 124)
    def test_shell_string_rejected(self):
        with self.assertRaises(ValueError):
            w.validate_config({'checks': [{'argv': 'echo injection', 'name': 'bad'}]})
    def test_existing_git_hook_preserved(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('existing hook')
        r = subprocess.run([sys.executable, str(source), '--root', str(self.root), 'git-hook'], capture_output=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(hook.read_text(), 'existing hook')

if __name__ == '__main__':
    unittest.main(verbosity=2)
