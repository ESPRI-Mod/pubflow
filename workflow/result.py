from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PublicationResult:
    dataset_id: str
    status: str
    exit_code: int = 0
    error_message: Optional[str] = None
    log_file: Optional[str] = None
