# Psychosocial Safety Evaluator

Psychosocial Safety Evaluator is intended to become an open-source evaluation harness for testing psychosocial behavior in conversational AI. The first V1 evaluation construct will be relational sycophancy.

Current status: repository bootstrap only. The evaluation engine, relational sycophancy evaluation, and product functionality are not implemented. The sole test verifies that the package can be imported.

## Local development

Python 3.14 is required. From the repository root, on macOS/Linux:

```sh
python3.14 --version
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python --version
python -c "import psych_eval"
python -m pytest
```

`.env.example` documents the future OpenAI API key configuration. No key is needed for setup or tests, and this bootstrap does not load `.env` files. Never commit credentials.
