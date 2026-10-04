import siglent_sdg_openhtf


def test_import_exposes_version() -> None:
    assert isinstance(siglent_sdg_openhtf.__version__, str)
    assert siglent_sdg_openhtf.__version__
