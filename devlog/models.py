from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Entry:
    id: str
    message: str
    created_at: str  # ISO 8601 UTC string
    tags: List[str] = field(default_factory=list)
    updated_at: Optional[str] = None  # ISO 8601 UTC string, or None