"""Explicit telemetry-only bridge into private Codex homes."""
from __future__ import annotations

import base64
import json
import os
import tomllib
from pathlib import Path

from .util import PipelineError, atomic_text


def _toml(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return '[' + ', '.join(_toml(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(f'{_toml(key)} = {_toml(item)}' for key, item in value.items()) + '}'
    raise PipelineError('Unsupported value in Codex OTEL configuration.')


def book_title(project: Path) -> str | None:
    try:
        book = json.loads((project / 'book.json').read_text(encoding='utf-8'))
        title = book.get('metadata', {}).get('title')
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(title, str):
        return None
    return ' '.join(title.split())[:250] or None


def install_otel(home: Path, options: dict, *, title: str | None = None) -> None:
    """Copy only the opted-in [otel] table, without prompts or agent settings.

    Missing source files are allowed for built-in profiles on machines without
    Codex user configuration. Refresh on every process start (including resume).
    Headers stay in the private config, never command-line/evidence arguments.
    """
    target = home / 'config.toml'
    marker = '# Intelitex telemetry-only configuration\n'
    source = options.get('otel_config')
    if not source:
        if not target.is_symlink() and target.is_file() and target.read_text(encoding='utf-8').startswith(marker):
            target.unlink()
        return
    try:
        data = tomllib.loads(Path(source).expanduser().read_text(encoding='utf-8'))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        raise PipelineError('Cannot read Codex OTEL configuration.') from exc
    otel = data.get('otel', {})
    if not isinstance(otel, dict):
        raise PipelineError('Codex OTEL configuration must be a table.')
    if target.is_symlink():
        raise PipelineError('Private Codex telemetry config cannot be a symbolic link.')
    # Never enable prompt export through inherited user preferences.
    otel = {**otel, 'log_user_prompt': False, 'log_agent_responses': False}
    # OTLP HTTP headers must be ASCII; the relay decodes the UTF-8 title.
    for name in ('exporter', 'metrics_exporter'):
        exporter = otel.get(name)
        if not isinstance(exporter, dict):
            continue
        http = exporter.get('otlp-http')
        if not isinstance(http, dict):
            continue
        headers = http.setdefault('headers', {})
        for key in list(headers):
            if key.lower() == 'x-intelitex-project':
                del headers[key]
        if title:
            encoded = base64.urlsafe_b64encode(' '.join(title.split())[:250].encode('utf-8')).decode('ascii')
            headers['X-Intelitex-Project'] = 'utf8-base64:' + encoded
    text = marker + '[otel]\n' + '\n'.join(f'{_toml(key)} = {_toml(value)}' for key, value in otel.items()) + '\n'
    atomic_text(target, text)
    os.chmod(target, 0o600)
