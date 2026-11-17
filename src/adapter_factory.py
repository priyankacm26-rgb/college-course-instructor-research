
"""Select a registered portal adapter using portal detection."""

from src.portal_detection.detector import detect_portal

from src.adapters.banner import BannerAdapter
from src.adapters.cuny import CUNYAdapter


ADAPTER_REGISTRY = {
    "banner": BannerAdapter,
    "cuny": CUNYAdapter,
}


# Optional college-specific adapters.
# They are loaded only when their files exist.
def _register_optional_adapters():
    optional_adapters = {
        "glendale": ("src.adapters.glendale", "GlendaleAdapter"),
    }

    import importlib

    for adapter_name, (module_name, class_name) in optional_adapters.items():
        try:
            module = importlib.import_module(module_name)
            adapter_class = getattr(module, class_name)
            ADAPTER_REGISTRY[adapter_name] = adapter_class
        except (ImportError, AttributeError):
            # Keep existing adapters working if optional adapters
            # have not yet been implemented.
            continue


_register_optional_adapters()


def get_adapter(schedule_url: str, title: str = "", page_text: str = ""):
    """Return (adapter_instance, detection_metadata)."""

    detection = detect_portal(
        schedule_url,
        title=title,
        page_text=page_text,
    )

    adapter_name = detection.get("adapter", "manual")
    confidence = float(detection.get("confidence", 0.0))

    if confidence < 0.80:
        raise ValueError(
            "Portal detection confidence is too low for automatic routing. "
            f"Detection result: {detection}"
        )

    if adapter_name not in ADAPTER_REGISTRY:
        raise ValueError(
            f"Portal identified as '{adapter_name}', but its adapter "
            "has not been implemented or registered yet. "
            f"Detection result: {detection}"
        )

    return ADAPTER_REGISTRY[adapter_name](), detection
