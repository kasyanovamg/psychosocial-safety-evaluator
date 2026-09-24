"""OpenAI-only transport helpers; no SDK objects cross the evaluator boundary."""

import os

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


def failure_detail(exc):
    """Allowlisted diagnostics only, never SDK text, headers, or request objects."""
    if isinstance(exc, openai.AuthenticationError):
        return 'OpenAI authentication failed'
    if isinstance(exc, openai.RateLimitError):
        return 'OpenAI rate limit exceeded'
    if isinstance(exc, openai.APITimeoutError):
        return 'OpenAI request timed out'
    if isinstance(exc, openai.APIConnectionError):
        return 'OpenAI connection failed'
    if isinstance(exc, openai.BadRequestError):
        return 'OpenAI rejected the request; check model, sampling, and structured-output support'
    return 'OpenAI API or response failure'


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
