from .viking_client import VikingClient, VikingDocument, VikingError, VikingUnreachable


def viking_client_for(cfg, *, allow_stub: bool = True) -> VikingClient:
    """Build a VikingClient from a NodeConfig, threading stub_dir through.

    Single canonical entry point for callers that have a config in hand.
    """
    return VikingClient(
        cfg.viking.base_url,
        timeout_s=cfg.viking.timeout_s,
        allow_stub=allow_stub,
        stub_dir=cfg.viking.stub_dir,
    )


__all__ = ["VikingClient", "VikingDocument", "VikingError", "VikingUnreachable", "viking_client_for"]
