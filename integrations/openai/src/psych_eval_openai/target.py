"""One stateless OpenAI Responses call per canonical assistant turn."""

from psych_eval.runner import TargetMessage
from psych_eval.transcripts import TargetConfig

from psych_eval_openai._transport import failure_detail, make_client, response_text, sampling_args


class OpenAITarget:
    def __init__(self, client, config: TargetConfig):
        self._client, self.config = client, config

    def respond(self, messages: tuple[TargetMessage, ...], *, config: TargetConfig) -> str:
        if config != self.config:
            raise ValueError('OpenAI target configuration must match its public provenance')
        inputs = [{'role': 'system', 'content': config.system_prompt}]
        inputs.extend({'role': message.role, 'content': message.content} for message in messages)
        try:
            response = self._client.responses.create(
                model=config.model, input=inputs, **sampling_args(config),
                store=False, truncation='disabled',
            )
            return response_text(response, allow_refusal=True)
        except Exception as exc:
            raise RuntimeError(failure_detail(exc)) from None


def build_target(*, config: TargetConfig, options: dict):
    if config.provider != 'openai':
        raise ValueError('OpenAI target requires provider=openai')
    client = make_client(options)
    return lambda scenario: OpenAITarget(client, config)
