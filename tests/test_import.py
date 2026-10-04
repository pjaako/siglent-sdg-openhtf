import siglent_sdg_openhtf


def test_import_exposes_version() -> None:
    assert isinstance(siglent_sdg_openhtf.__version__, str)
    assert siglent_sdg_openhtf.__version__


def test_version_comes_from_package_metadata() -> None:
    from importlib.metadata import version

    assert siglent_sdg_openhtf.__version__ == version("siglent-sdg-openhtf")
