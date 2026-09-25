import pytest


@pytest.fixture(autouse=True)
def _no_catalog_crawl(monkeypatch):
    """Saving a catalog opens supplier web pages for models and photos; tests stay offline."""
    monkeypatch.setenv("ROLODEX_CATALOG_COMPLETE", "0")
