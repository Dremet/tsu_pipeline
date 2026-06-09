import hashlib


def _md5(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def session_id(utc_start_time: str, host: int) -> str:
    return _md5(utc_start_time, str(host))


def hotlap_event_id(utc_start_time: str, host: int) -> str:
    return _md5(utc_start_time, str(host))


def participation_id(session_id: str, steam_id: int, vehicle_guid: str) -> str:
    return _md5(session_id, str(steam_id), vehicle_guid)


def bot_participation_id(session_id: str, player_array_index: int) -> str:
    """Stable key for a bot within a session: session + position in players array."""
    return _md5(session_id, "bot", str(player_array_index))


def lap_telemetry_id(participation_id: str, lap_number: int) -> str:
    return _md5(participation_id, str(lap_number))
