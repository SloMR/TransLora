"""Human-friendly duration formatting."""

def format_duration(seconds: float) -> str:
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {(total % 3600) // 60}m"
