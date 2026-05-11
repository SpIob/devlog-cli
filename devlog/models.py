from dataclasses import dataclass, field
from typing import List


@dataclass
class Entry:
    id: str
    message: str
    created_at: str  # ISO 8601 UTC string
    tags: List[str] = field(default_factory=list)