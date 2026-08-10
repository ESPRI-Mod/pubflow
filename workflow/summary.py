from dataclasses import dataclass


@dataclass
class PublicationSummary:
    campaign: str

    run_id: str

    total: int = 0

    success: int = 0

    failed: int = 0

    failures: list = None

    def __post_init__(self):
        if self.failures is None:
            self.failures = []

    def add_result(self, result):

        self.total += 1

        if result.status == "SUCCESS":
            self.success += 1

        else:
            self.failed += 1

            self.failures.append(
                result
            )
