# Wisland Agent Instructions

## Required startup

Before planning, reviewing, or editing this repository:

1. Read `PROJECT_STATE.md` completely.
2. Run `git status --short --branch`.
3. Run `git log -6 --oneline --decorate`.
4. Treat the current source and Git state as authoritative if they differ from the handoff file.

## Conversation continuity

- Do not re-plan or re-implement stages marked complete.
- Do not repeat full implementation prompts already recorded as delivered.
- When the user says a stage is complete, verify the relevant files/tests and update `PROJECT_STATE.md`; do not regenerate the same task specification.
- Report only new changes, remaining work, risks, and the next actionable step.

## Safety

- Preserve existing uncommitted changes.
- Do not use `git reset --hard`, `git checkout --`, or broad file replacement.
- Do not commit or push unless the user explicitly asks.
- Never write API keys, cookies, tokens, passwords, or mail verification codes into config, logs, tests, or documentation.

## Stage completion

Before completing a stage:

1. Run relevant tests.
2. Review the diff for unrelated changes and secrets.
3. Update `PROJECT_STATE.md` with:
   - stage status
   - commit hash if committed
   - tests performed
   - known risks
   - next stage
4. Report what actually changed and what remains.
