"""Transport the exact canonical judge input; core owns result validation."""

from psych_eval.judge import JudgeConfig, JudgeError, JudgeInput, JudgeResult
from psych_eval.judge_payload import JUDGE_PROMPT_VERSION

from psych_eval_openai._transport import failure_detail, make_client, response_text, sampling_args


class OpenAIJudge:
    def __init__(self, client, config: JudgeConfig):
        self._client, self.config = client, config

    def assess(self, request: JudgeInput, *, config: JudgeConfig) -> str:
        if config != self.config:
            raise JudgeError('judge_input', 'OpenAI judge configuration must match its public provenance')
        try:
            response = self._client.responses.create(
                model=config.model, instructions=request.instructions,
                input=[{'role': 'user', 'content': request.model_dump_json()}],
                text={'format': {'type': 'json_schema', 'name': 'psych_eval_judge_result',
                                 'strict': True, 'schema': JudgeResult.model_json_schema()}},
                **sampling_args(config), store=False, truncation='disabled',
            )
            return response_text(response)
        except Exception as exc:
            raise JudgeError('judge_call', failure_detail(exc)) from None


def build_judge(*, config: JudgeConfig, options: dict):
    if config.provider != 'openai' or config.mode != 'live':
        raise ValueError('OpenAI judge requires live mode and provider=openai')
    if config.prompt_version not in (None, JUDGE_PROMPT_VERSION):
        raise ValueError('OpenAI judge requires canonical judge prompt 0.1')
    return OpenAIJudge(make_client(options), config)
