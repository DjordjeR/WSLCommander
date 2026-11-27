"""Small display helpers for timestamps stored in settings."""


def relative_time(iso: str) -> str:
    """Human 'just now / 5 min ago / 2 h ago / yesterday / date' for an ISO
    timestamp; empty string when unknown (device seen before the app watched)."""
    if not iso:
        return ""
    from datetime import datetime

    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    delta = (datetime.now() - when).total_seconds()
    if delta < 0:
        delta = 0
    if delta < 45:
        return "just now"
    if delta < 3600:
        minutes = int(delta // 60)
        return f"{minutes} min ago"
    if delta < 7200:
        return "1 h ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    if delta < 172800:
        return "yesterday"
    return when.strftime("%Y-%m-%d")
