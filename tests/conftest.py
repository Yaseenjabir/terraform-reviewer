import pytest


@pytest.fixture(autouse=True)
def _not_in_github_actions(monkeypatch):
    # The CLI writes step outputs when GITHUB_OUTPUT is set, as it is when the tests run in CI.
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
