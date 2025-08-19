from datetime import datetime, timezone, timedelta

def now() -> datetime:
    """
    현재 시간을 UTC 기준으로 반환합니다.
    """
    return datetime.now(timezone.utc)

def get_seconds_until_next_day_utc() -> float:
    """
    현재 시간으로부터 다음 날 UTC 자정까지 남은 시간을 초 단위로 계산합니다.
    """
    now_utc = now()
    tomorrow_utc = now_utc.date() + timedelta(days=1)
    midnight_utc = datetime(tomorrow_utc.year, tomorrow_utc.month, tomorrow_utc.day, tzinfo=timezone.utc)
    seconds_remaining = (midnight_utc - now_utc).total_seconds()
    return seconds_remaining