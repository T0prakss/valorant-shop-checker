from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SessionData:
    access_token: str
    entitlements_token: str
    puuid: str
    shard: str
    region: str
    riot_cookies: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=datetime.utcnow)
