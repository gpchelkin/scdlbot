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
3. Add a line to `CHANGELOG.md`, under the next version.

Keep it simple: the bot is meant to stay small and readable.

## Releases

1. Set the version in `pyproject.toml`, and give the version's section in `CHANGELOG.md` its date: `## 1.0.1 (2026-12-01)`.
2. Commit and push to master, then push a tag: `git tag v1.0.1 && git push origin v1.0.1`.

CI tests the tag, publishes the package to [PyPI](https://pypi.org/project/scdlbot/) and creates
[the GitHub release](https://github.com/gpchelkin/scdlbot/releases) with the version's section of the changelog.
[Read the Docs](https://scdlbot.readthedocs.io) rebuilds the docs from master and from tags by itself.
