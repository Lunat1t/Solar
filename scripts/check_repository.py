#!/usr/bin/env python3
"""Проверка согласованности поставляемого сэтапа Solar."""
from pathlib import Path
import json
import sys
import tomllib

root = Path(__file__).resolve().parents[1]
source = root / '.codex/skills/setup-agent-workbench/assets/workbench/agent_workbench.py'
installed = root / '.agent-workbench/agent_workbench.py'
assert source.read_bytes() == installed.read_bytes(), 'Обнови установленную копию Workbench из исходника'
config = json.loads((root / '.agent-workbench/config.json').read_text())
assert config['checks'], 'Не настроены проверки Solar'
hooks = json.loads((root / '.codex/hooks.json').read_text())['hooks']
for event in ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'Stop'):
    assert event in hooks, f'Отсутствует hook {event}'
for name in ('plan', 'implement', 'debug', 'review', 'research'):
    assert (root / f'.agent-workbench/roles/{name}.md').is_file()
for name in ('РУКОВОДСТВО.md', 'АРХИТЕКТУРА.md', 'ПРОВЕРКИ.md'):
    assert (root / 'docs' / name).is_file()
print('Состав сэтапа Solar согласован')

config = tomllib.loads((root / '.codex/config.toml').read_text())
assert config['agents']['enabled'] is True
assert config['agents']['max_concurrent_threads_per_session'] == 4
for name, sandbox in [('solar_explorer', 'read-only'), ('solar_worker', 'workspace-write'), ('solar_verifier', 'read-only'), ('solar_reviewer', 'read-only')]:
    agent = tomllib.loads((root / '.codex/agents' / (name + '.toml')).read_text())
    assert agent['name'] == name and agent['description'] and agent['developer_instructions']
    assert agent['sandbox_mode'] == sandbox
assert (root / 'docs/СТАНДАРТ.md').is_file()
print('Нативные профили и локальный стандарт представлены; live spawn требует отдельной проверки')
