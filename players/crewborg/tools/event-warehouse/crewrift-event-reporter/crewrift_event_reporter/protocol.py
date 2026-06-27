from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Wire protocol shared with the metta reporter-runner backend.
# Mirrors metta `app_backend/reporter_runner/protocol.py` (PR #15877):
# the worker now sends presigned artifact refs per episode instead of a
# single relayed bundle zip. There is NO backwards-compat path — this
# reporter image must be deployed in lockstep with a backend that speaks
# this `episodes` shape.

EpisodeStatus = Literal["success", "failed"]
ArtifactEncoding = Literal["identity", "zlib"]
BundleToken = Literal["results", "replay", "error_info", "game_logs", "player_logs", "player_artifact"]


class PlayerIdentity(BaseModel):
    slot: int
    player_id: str | None = None
    display_name: str | None = None


class ReporterArtifactRef(BaseModel):
    uri: str
    media_type: str
    encoding: ArtifactEncoding = "identity"


class ReporterErrorInfo(BaseModel):
    error_type: str | None = None
    error: str | None = None
    failed_policy_index: int | None = None
    failed_agent_index: int | None = None


class ReporterEpisodeManifest(BaseModel):
    ereq_id: str
    status: EpisodeStatus
    include: list[BundleToken] = Field(default_factory=list)
    files: dict[BundleToken, str | dict[str, str]] = Field(default_factory=dict)


class ReporterEpisodeArtifacts(BaseModel):
    results: ReporterArtifactRef | None = None
    replay: ReporterArtifactRef | None = None
    game_logs: dict[str, ReporterArtifactRef] = Field(default_factory=dict)
    player_logs: dict[str, ReporterArtifactRef] = Field(default_factory=dict)
    player_artifact: dict[str, ReporterArtifactRef] = Field(default_factory=dict)


class ReporterEpisodeInlineJson(BaseModel):
    error_info: ReporterErrorInfo | None = None


class ReporterEpisodeInput(BaseModel):
    episode_request_id: str
    status: EpisodeStatus
    manifest: ReporterEpisodeManifest
    artifacts: ReporterEpisodeArtifacts
    inline_json: ReporterEpisodeInlineJson = Field(default_factory=ReporterEpisodeInlineJson)
    players: list[PlayerIdentity] = Field(default_factory=list)


class ReportRequest(BaseModel):
    type: Literal["report_request"] = "report_request"
    request_id: str
    episodes: list[ReporterEpisodeInput]
    report_uri: str

    @model_validator(mode="after")
    def exactly_one_episode(self) -> "ReportRequest":
        if len(self.episodes) != 1:
            raise ValueError("crewrift-event-reporter requires exactly one episode")
        return self

    def episode(self) -> ReporterEpisodeInput:
        return self.episodes[0]


class ReporterReady(BaseModel):
    type: Literal["reporter_ready"] = "reporter_ready"
    protocol_version: str = "crewrift-event-reporter/v2"


class ReportStarted(BaseModel):
    type: Literal["report_started"] = "report_started"
    request_id: str
    episode_count: int


class ReportFinished(BaseModel):
    type: Literal["report_finished"] = "report_finished"
    request_id: str
    report_uri: str
    episode_count: int
    players: int


class ReportFailed(BaseModel):
    type: Literal["report_failed"] = "report_failed"
    request_id: str
    stage: str
    error: str
