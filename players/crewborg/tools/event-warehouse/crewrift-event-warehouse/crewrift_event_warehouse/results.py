from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class CrewriftResults(BaseModel):
    """Per-episode results.json, reduced to the fields the warehouse needs.

    Self-contained on purpose: the warehouse must not import the sportscaster
    package. Role lives here (``crew[]`` / ``imposter[]``), not in the request's
    PlayerIdentity, because role is assigned per episode rather than per policy.
    """

    scores: list[float]
    names: list[str] = Field(default_factory=list)
    win: list[bool] = Field(default_factory=list)
    tasks: list[int] = Field(default_factory=list)
    kills: list[int] = Field(default_factory=list)
    imposter: list[int] = Field(default_factory=list)
    crew: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def scores_are_present(self) -> "CrewriftResults":
        if len(self.scores) == 0:
            raise ValueError("scores must not be empty")
        return self

    def slot_count(self) -> int:
        return len(self.scores)

    def name_at(self, slot: int) -> str | None:
        if slot < len(self.names) and self.names[slot]:
            return self.names[slot]
        return None

    def role_at(self, slot: int) -> str:
        if slot < len(self.imposter) and int(self.imposter[slot]) == 1:
            return "imposter"
        if slot < len(self.crew) and int(self.crew[slot]) == 1:
            return "crew"
        return "unknown"

    def score_at(self, slot: int) -> float:
        return float(self.scores[slot]) if slot < len(self.scores) else 0.0

    def win_at(self, slot: int) -> bool:
        return slot < len(self.win) and bool(self.win[slot])

    def int_at(self, values: list[int], slot: int) -> int:
        return int(values[slot]) if slot < len(values) else 0
