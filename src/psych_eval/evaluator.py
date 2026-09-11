"""Judge orchestration and deterministic V1 aggregation, with no target calls."""

from uuid import UUID, uuid4

from psych_eval.evaluations import Evaluation, overall_severity
from psych_eval.judge import (
    Judge, JudgeConfig, JudgeError, JudgeInput, SavedJudgeInput, assemble_judge_input,
    request_transcript, validate_judge_result,
)
from psych_eval.judge_payload import JUDGE_PROMPT_VERSION
from psych_eval.scenarios import EvaluatorScenarioView
from psych_eval.transcripts import Transcript


def evaluate_transcript(
    transcript: Transcript, scenario: EvaluatorScenarioView,
    judge: Judge, config: JudgeConfig,
) -> Evaluation:
    """Judge an existing transcript once, preserving the exact request/response.

    Technical errors raise JudgeError with their stage. Semantic cannot_assess
    produces an evaluation with null severity. No implicit retries or fallback.
    """
    try:
        if not isinstance(scenario, EvaluatorScenarioView):
            raise TypeError("evaluate_transcript requires an explicit EvaluatorScenarioView")
        transcript = Transcript.model_validate(transcript.model_dump())
        scenario = EvaluatorScenarioView.model_validate(scenario.model_dump())
        config = JudgeConfig.model_validate(config.model_dump())
        request = assemble_judge_input(transcript, scenario)
        if config.prompt_version not in (None, JUDGE_PROMPT_VERSION):
            raise ValueError("judge config prompt_version must match canonical 0.1")
    except (ValueError, TypeError, AttributeError) as exc:
        raise JudgeError("judge_input", str(exc)) from exc

    try:
        # Give the judge its own copy so mutable lists cannot alter saved inputs.
        raw_response = judge.assess(request.model_copy(deep=True), config=config)
    except JudgeError:
        raise
    except Exception as exc:
        raise JudgeError("judge_call", f"{type(exc).__name__}: {exc}") from exc
    return evaluation_from_response(request, raw_response, config, evaluation_id=uuid4(),
                                    transcript_snapshot=transcript)


def evaluation_from_response(
    request: SavedJudgeInput, raw_response: str, config: JudgeConfig, *, evaluation_id: UUID,
    transcript_snapshot: Transcript | None = None,
) -> Evaluation:
    """Pure normalization of saved judge IO; never calls either model."""
    transcript = request_transcript(request, transcript_snapshot)
    result = validate_judge_result(raw_response, transcript)
    severity = overall_severity(result)
    return Evaluation(
        **result.model_dump(), schema_version="1.0", evaluation_id=evaluation_id,
        transcript_run_id=transcript.run_id, scenario_id=transcript.scenario_id,
        scenario_version=transcript.scenario_version,
        execution_status=transcript.execution_status, overall_severity=severity,
        judge=config, rubric_version=request.rubric_version, evaluator_version="1.0",
        judge_input=request, raw_judge_response=raw_response, transcript_snapshot=transcript_snapshot,
    )
