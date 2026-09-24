"""OpenAI-only transport helpers; no SDK objects cross the evaluator boundary."""

import os
import re

import openai
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class _Options(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    api_key_env: str = 'OPENAI_API_KEY'
    organization: SecretStr | None = None
    project: SecretStr | None = None
    timeout: float = Field(default=60.0, gt=0, allow_inf_nan=False)


def make_client(options):
    try:
        options = _Options.model_validate(options)
        key = os.environ.get(options.api_key_env)
        if not key or not key.strip():
            raise ValueError
        return openai.OpenAI(
            api_key=key, max_retries=0, timeout=options.timeout,
            # No endpoint override: this reference targets OpenAI, not compatible servers.
            base_url='https://api.openai.com/v1',
            organization=options.organization.get_secret_value() if options.organization else None,
            project=options.project.get_secret_value() if options.project else None,
        )
    except Exception:
        # SDK constructors and validation errors can echo runtime secrets.
        raise ValueError('Invalid OpenAI credentials or runtime options') from None


_SECRET_PATTERNS = (
    re.compile(r'(?i)\bbearer\s+[^\s,;]+'),
    re.compile(r'\bsk-[A-Za-z0-9_-]+'),
    re.compile(r'(?i)\b(?:api[_ -]?key|authorization|password|token|secret)\s*[:=]\s*[^\s,;]+'),
    re.compile(r'(?i)\b[A-Za-z0-9_.-]*(?:private|secret)[A-Za-z0-9_.-]*\b'),
)


def _safe_provider_value(value):
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = ' '.join(str(value).split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub('[redacted]', text)
    return text[:1000] or None


def failure_detail(exc):
    """Return only allowlisted, sanitized provider fields—never headers or request data."""
    exception_type = type(exc).__name__
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', exception_type):
        exception_type = 'OpenAIError'
    fields = [('type', exception_type)]

    status = getattr(exc, 'status_code', None)
    if status is None:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
    if isinstance(status, int):
        fields.append(('status', str(status)))

    body = getattr(exc, 'body', None)
    error = body.get('error', body) if isinstance(body, dict) else {}
    for name in ('message', 'code', 'param'):
        value = error.get(name) if isinstance(error, dict) else None
        if value is None:
            value = getattr(exc, name, None)
        value = _safe_provider_value(value)
        if value is not None:
            fields.append((name, value))

    return 'OpenAI provider error: ' + '; '.join(f'{name}={value}' for name, value in fields)


def sampling_args(config):
    return config.sampling.model_dump() if config.sampling is not None else {}


def response_text(response, *, allow_refusal=False):
    if response.status != 'completed':
        raise ValueError('OpenAI response was not completed')
    parts = []
    for item in response.output:
        if item.type == 'reasoning':
            continue
        if item.type != 'message' or item.role != 'assistant' or item.status != 'completed':
            raise ValueError('Unexpected OpenAI output')
        for content in item.content:
            if content.type == 'output_text':
                parts.append(content.text)
            elif allow_refusal and content.type == 'refusal':
                parts.append(content.refusal)
            else:
                raise ValueError('OpenAI response did not contain the requested text')
    if not parts or any(not isinstance(part, str) for part in parts):
        raise ValueError('Missing OpenAI assistant text')
    text = ''.join(parts)
    if not text.strip():
        raise ValueError('Empty OpenAI assistant text')
    # Usage/returned model cannot be carried by the frozen str-return contracts.
    return text
