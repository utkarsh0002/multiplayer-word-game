import re
from dataclasses import asdict, dataclass
from math import ceil
from time import monotonic, time
from unicodedata import category as unicode_category

from word_lists import WORD_LISTS


REVIEW_SECONDS = 15


def is_word_or_phrase(answer: str) -> bool:
    return all(
        part and part[0].isalpha()
        and all(char.isalpha() or unicode_category(char).startswith("M") for char in part)
        for part in re.split(r"[ '’-]", answer)
    )


@dataclass
class ReviewEntry:
    player_id: str
    category: str
    answer: str
    status: str = "pending"


def pending_entries(
    letter: str, categories: tuple[str, ...], answers: dict[str, dict[str, str]]
) -> dict[tuple[str, str], ReviewEntry]:
    entries = {}
    starting_letter = letter.casefold()
    for player_id, submitted in answers.items():
        for category in categories:
            answer = submitted[category]
            normalized = answer.casefold()
            if (
                normalized.startswith(starting_letter)
                and normalized not in WORD_LISTS[category]
                and is_word_or_phrase(answer)
            ):
                entries[player_id, category] = ReviewEntry(player_id, category, answer)
    return entries


@dataclass
class Review:
    entries: dict[tuple[str, str], ReviewEntry]
    deadline: int
    deadline_monotonic: float

    @classmethod
    def begin(cls, entries: dict[tuple[str, str], ReviewEntry]) -> "Review":
        return cls(entries, int(time() * 1000) + int(REVIEW_SECONDS * 1000), monotonic() + REVIEW_SECONDS)

    def seconds_remaining(self) -> float:
        return max(0.0, self.deadline_monotonic - monotonic())

    def has_pending(self) -> bool:
        return any(entry.status == "pending" for entry in self.entries.values())

    def reject_undecided(self) -> None:
        for entry in self.entries.values():
            if entry.status == "pending":
                entry.status = "timed_out"

    def approved_answers(self) -> set[tuple[str, str]]:
        return {key for key, entry in self.entries.items() if entry.status == "accepted"}

    def snapshot(self, *, scored: bool) -> dict:
        return {
            "deadline": self.deadline,
            "remaining_ms": 0 if scored else ceil(self.seconds_remaining() * 1000),
            "entries": [asdict(entry) for entry in self.entries.values()],
        }
