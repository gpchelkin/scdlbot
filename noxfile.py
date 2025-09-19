"""Nox sessions for running tests."""

import nox


@nox.session
def tests(session: nox.Session) -> None:
    """Run the test suite."""
    session.run("poetry", "install", "--with", "main,dev,docs", external=True)
    session.run("poetry", "run", "make", "test", external=True)

