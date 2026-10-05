#!/usr/bin/env python3
"""Solar: запуск переносимого Agent Workbench из этого репозитория."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent / '.codex/skills/setup-agent-workbench/assets/workbench/agent_workbench.py'), run_name='__main__')
