"""OpenHTF plug for the Siglent SDG2042X arbitrary waveform generator."""

from .plug import Identity, SiglentSdgPlug
from .scpi import ProtocolError, SetupError

__version__ = "0.1.0"

__all__ = ["Identity", "ProtocolError", "SetupError", "SiglentSdgPlug", "__version__"]
