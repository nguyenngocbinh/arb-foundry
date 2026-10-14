from arb_foundry import __version__


def test_package_has_semantic_version() -> None:
    assert __version__ == "0.2.0"


def test_live_execution_is_not_exposed_by_foundation() -> None:
    import arb_foundry

    assert not hasattr(arb_foundry, "create_order")
    assert not hasattr(arb_foundry, "submit_order")
