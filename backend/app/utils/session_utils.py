from datetime import datetime


def is_within_session(now: datetime, start: str, end: str) -> bool:
    start_t = datetime.strptime(start, "%H:%M").time()
    end_t = datetime.strptime(end, "%H:%M").time()
    return start_t <= now.time() <= end_t
