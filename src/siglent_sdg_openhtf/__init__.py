"""OpenHTF plug for the Siglent SDG2042X arbitrary waveform generator."""

from .scpi import ProtocolError, SetupError

# Later tasks add the public exports: SiglentSdgPlug, Identity.

__version__ = "0.1.0"

__all__ = ["ProtocolError", "SetupError", "__version__"]
