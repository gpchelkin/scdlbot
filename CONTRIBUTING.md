# How to contribute

See [Run locally](README.md#run-locally) to set up the environment, then:

```bash
mise run fmt    # ruff: format and fix
mise run lint   # ruff: check
mise run test   # pytest
```

The same checks run in CI for every pull request.

Before submitting a pull request:

1. Add tests for new logic where it makes sense (see `tests/`).
2. Update `README.md` and `.env.sample` if you change behavior or settings.
3. Add a line to `CHANGELOG.rst`.

Keep it simple: the bot is meant to stay small and readable.
