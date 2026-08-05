# AI evaluation suite

Prompt regression tests (goal.txt D14). These hit a **real** provider, cost
money, and are excluded from the default test run:

```bash
pytest -m ai_eval          # run them
pytest -m "not ai_eval"    # everything else (this is what CI does per PR)
```

## Why this exists

A prompt edit is a code change with no compiler and no type checker. The only
way to know that "make the concierge friendlier" did not also make it start
inventing checkout times is to re-run a fixed question set and compare.

## What lands here in Phase 2

- `questions.yaml` — ≥30 real guest questions with expected facts and the
  knowledge-base source each answer must cite
- `test_concierge.py` — asserts: correct fact, citation present, refusal when
  the answer is absent from context, and answer language matches the question
- `test_injection.py` — prompt-injection corpus: uploaded documents and guest
  messages that try to override the system prompt must be treated as data
- `test_leakage.py` — asserts one guest can never retrieve another's data
- `scoring.py` — writes the score to `PromptVersion.eval_score`; a drop blocks
  activation of that prompt version

Exit criterion for Phase 2: ≥90% correct on the question set.
