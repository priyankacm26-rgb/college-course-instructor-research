
"""Portal-type detection for the College Course & Instructor Research app."""

from urllib.parse import urlparse
import re


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).casefold()


def detect_portal(url: str, title: str = "", page_text: str = "") -> dict:
    """Identify supported college portals using URL and page content."""

    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").casefold()
    path = (parsed.path or "").casefold()
    text = _clean(f"{title} {page_text}")

    if parsed.scheme not in {"http", "https"} or not host:
        return {
            "portal_type": "unknown",
            "adapter": "manual",
            "confidence": 0.0,
            "reason": "A valid full http(s) schedule URL is required.",
        }

    # Glendale Community College
    if host == "psprd.glendale.edu":
        return {
            "portal_type": "glendale_peoplesoft",
            "adapter": "glendale",
            "confidence": 0.99,
            "reason": "Recognized Glendale Community College class search host.",
        }

    # Reedley College / SCCCD Self-Service
    if host == "selfservice.scccd.edu":
        if "/student/courses" in path or "reedley college" in text:
            return {
                "portal_type": "scccd_self_service",
                "adapter": "reedley",
                "confidence": 0.95,
                "reason": "Recognized SCCCD Self-Service course search.",
            }

    # Palomar College
    if host == "my.palomar.edu" or host.endswith(".palomar.edu") or host == "palomar.edu":
        return {
            "portal_type": "palomar",
            "adapter": "palomar",
            "confidence": 0.99,
            "reason": "Recognized Palomar College schedule domain.",
        }

    # Existing Banner support
    if host.endswith("uncw.edu") or "registration.uncw.edu" in host:
        return {
            "portal_type": "banner",
            "adapter": "banner",
            "confidence": 0.99,
            "reason": "Recognized UNC Wilmington registration host.",
        }

    # Existing CUNY support
    if host.endswith("cuny.edu") and "cfglobalsearchtool" in path:
        return {
            "portal_type": "cuny_global_search",
            "adapter": "cuny",
            "confidence": 0.99,
            "reason": "Recognized CUNY Global Search URL pattern.",
        }

    # Existing Banner content detection
    if any(marker in text for marker in (
        "banner self service",
        "student registration ssb",
        "browse classes",
    )):
        return {
            "portal_type": "banner",
            "adapter": "banner",
            "confidence": 0.85,
            "reason": "Banner/Student Registration indicators detected.",
        }

    # Existing CUNY content detection
    if "search for classes" in text and "select all institutions" in text:
        return {
            "portal_type": "cuny_global_search",
            "adapter": "cuny",
            "confidence": 0.90,
            "reason": "CUNY Global Search indicators detected.",
        }

    # Generic schedule page
    if any(marker in text for marker in (
        "class schedule",
        "course schedule",
        "schedule of classes",
    )):
        return {
            "portal_type": "html_or_custom",
            "adapter": "generic",
            "confidence": 0.55,
            "reason": "Schedule page detected; portal type is not confirmed.",
        }

    return {
        "portal_type": "unknown",
        "adapter": "manual",
        "confidence": 0.20,
        "reason": "No supported portal signature was recognized.",
    }
