import hashlib

def compute_hash(text: str) -> str:
    """Compute whitespace-insensitive SHA256 hash of a text string."""
    if not text:
        return ""
    # Strip, lowercase keywords (optional), and reduce all whitespace to single spaces
    normalized = " ".join(text.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
