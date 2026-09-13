"""External adapters; evaluator modules consume only Target and Judge contracts.

Construct targets and judges independently and pass them to execute_suite (or
run_scenario/evaluate_transcript). The existing target_factory callable is the
factory boundary; no global registration is needed. SDK clients, credentials,
and provider-only options belong on adapter instances, never artifact configs.
Adapters must translate SDK errors to credential-free exceptions before returning
control to the evaluator, which persists technical failure details.
"""
