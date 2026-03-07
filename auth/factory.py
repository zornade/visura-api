from .base import BaseAuthProvider
from .cie import CIEAuthProvider
from .sielte import SielteAuthProvider


def get_provider(provider_name: str) -> BaseAuthProvider:
    """
    Factory function to get the appropriate authentication provider based on the provider_name.

    Args:
        provider_name (str): The name of the authentication provider (e.g., 'CIE', 'SIELTE').

    Returns:
        BaseAuthProvider: An instance of the requested authentication provider.

    Raises:
        ValueError: If the provider is not supported.
    """
    provider_upper = provider_name.upper()
    if provider_upper == "CIE":
        return CIEAuthProvider()
    elif provider_upper == "SIELTE":
        return SielteAuthProvider()
    else:
        raise ValueError(f"Unsupported authentication provider: {provider_name}. Use 'CIE' or 'SIELTE'.")
