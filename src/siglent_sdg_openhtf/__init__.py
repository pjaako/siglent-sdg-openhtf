"""OpenHTF plug for the Siglent SDG2042X arbitrary waveform generator."""

from importlib.metadata import PackageNotFoundError, version

from .plug import Identity, SiglentSdgPlug
from .scpi import ProtocolError, SetupError

try:
    __version__ = version("siglent-sdg-openhtf")  # pyproject.toml is the single source of truth
except PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = "0.0.0"

__all__ = ["Identity", "ProtocolError", "SetupError", "SiglentSdgPlug", "__version__"]
