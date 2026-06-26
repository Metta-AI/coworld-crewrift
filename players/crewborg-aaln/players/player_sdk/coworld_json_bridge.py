"""Canonical websocket bridge for the ``coworld.player.v1`` JSON protocol.

This module hosts a registered mettagrid ``MultiAgentPolicy`` inside a process
that speaks the Coworld JSON player protocol. The policy is resolved at startup
from a URI (typically ``metta://policy/<short_name>``) using the standard
mettagrid loader, and each incoming ``observation`` message is dispatched to
the slot-specific ``AgentPolicy``.

It is the recommended entrypoint for any standalone player container whose
behaviour is implemented as a mettagrid policy class — e.g. all scripted
``cogsguard`` policies. Players that need a different transport (such as
BitWorld's ``bitscreen_v1`` binary protocol) should not use this bridge.

Environment variables (matching the Coworld example container contract):

- ``COGAMES_ENGINE_WS_URL`` — websocket URL including ``?slot=…&token=…``.
- ``COGAMES_POLICY_URI`` — policy URI passed to ``policy_spec_from_uri``.
- ``COGAMES_POLICY_DEVICE`` — torch device override; defaults to ``"cpu"``.
- ``COGAMES_POLICY_DISCOVERY_PACKAGES`` — optional comma-separated list of
  Python packages to walk for ``MultiAgentPolicy`` registration before URI
  resolution. mettagrid's resolver only auto-discovers a fixed set of
  packages (``mettagrid.policy``, ``cogames.policy``, ``cogames_agents.policy``,
  …); short-name URIs like ``metta://policy/baseline`` for classes that live
  outside that set will fail to resolve unless the owning package is listed
  here. Single-policy leaves typically set this to their own package
  (e.g. ``players.cogsguard.baseline``); multi-policy leaves to the leaf
  package that registers all relevant short_names.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Literal

import websockets
from pydantic import BaseModel

from mettagrid.policy.loader import discover_and_register_policies, initialize_or_load_policy
from mettagrid.policy.policy import MultiAgentPolicy
from mettagrid.policy.policy_env_interface import PolicyEnvInterface
from mettagrid.simulator import AgentObservation
from mettagrid.simulator.interface import ObservationToken
from mettagrid.util.uri_resolvers.schemes import policy_spec_from_uri

PROTOCOL_VERSION = "coworld.player.v1"
PADDING_FEATURE_ID = 0xFF

PolicyFactory = Callable[[str, PolicyEnvInterface, str], MultiAgentPolicy]


class PlayerConfigMessage(BaseModel):
    """First message sent by the engine; binds slot, action vocab, and env."""

    type: Literal["player_config"]
    protocol: Literal["coworld.player.v1"]
    slot: int
    connection_id: str
    action_names: list[str]
    policy_env: PolicyEnvInterface


class ObservationMessage(BaseModel):
    """Per-step observation message carrying packed feature tokens."""

    type: Literal["observation"]
    protocol: Literal["coworld.player.v1"]
    slot: int
    step: int
    observation: list[tuple[int, int, int]]


def _default_policy_factory(
    policy_uri: str, policy_env: PolicyEnvInterface, device: str
) -> MultiAgentPolicy:
    spec = policy_spec_from_uri(policy_uri, device=device)
    return initialize_or_load_policy(policy_env, spec, device_override=device)


def _decode_triplet_observation(
    raw_tokens: list[tuple[int, int, int]],
    policy_env: PolicyEnvInterface,
    slot: int,
) -> AgentObservation:
    features = {feature.id: feature for feature in policy_env.obs_features}
    tokens: list[ObservationToken] = []
    for location, feature_id, value in raw_tokens:
        if feature_id == PADDING_FEATURE_ID:
            break
        tokens.append(
            ObservationToken(
                feature=features[feature_id],
                value=value,
                raw_token=(location, feature_id, value),
            )
        )
    return AgentObservation(agent_id=slot, tokens=tokens)


class CoworldJsonBridge:
    """Stateful bridge between a websocket session and a MultiAgentPolicy."""

    def __init__(
        self,
        *,
        policy_uri: str,
        device: str = "cpu",
        policy_factory: PolicyFactory = _default_policy_factory,
    ) -> None:
        self.policy_uri = policy_uri
        self.device = device
        self._policy_factory = policy_factory
        self._config: PlayerConfigMessage | None = None
        self._policy: MultiAgentPolicy | None = None

    def configure(self, raw: dict[str, object]) -> None:
        config = PlayerConfigMessage.model_validate(raw)
        self._config = config
        self._policy = self._policy_factory(self.policy_uri, config.policy_env, self.device)

    def action_for_observation(self, raw: dict[str, object]) -> dict[str, object]:
        msg = ObservationMessage.model_validate(raw)
        assert self._config is not None, "configure() must be called before observations arrive"
        assert self._policy is not None
        agent_policy = self._policy.agent_policy(self._config.slot)
        observation = _decode_triplet_observation(
            msg.observation, self._config.policy_env, self._config.slot
        )
        action = agent_policy.step(observation)
        return {
            "type": "action",
            "action_name": action.name,
            "policy_infos": agent_policy.infos,
            "request_id": f"step-{msg.step}",
        }


async def run_bridge(
    *,
    engine_ws_url: str,
    policy_uri: str,
    device: str = "cpu",
    policy_factory: PolicyFactory = _default_policy_factory,
) -> None:
    """Connect to the engine and dispatch the protocol until the episode ends."""
    bridge = CoworldJsonBridge(
        policy_uri=policy_uri, device=device, policy_factory=policy_factory
    )
    async with websockets.connect(engine_ws_url) as websocket:
        async for raw_message in websocket:
            message = json.loads(raw_message)
            message_type = message["type"]
            if message_type == "player_config":
                bridge.configure(message)
            elif message_type == "observation":
                await websocket.send(json.dumps(bridge.action_for_observation(message)))
            elif message_type == "final":
                return


def _discover_extra_packages() -> None:
    """Walk packages listed in ``COGAMES_POLICY_DISCOVERY_PACKAGES``.

    Triggers ``PolicyRegistryMeta`` side effects so out-of-tree short_names
    (e.g. ``baseline``, ``role``, ``thinky``) become resolvable by mettagrid's
    URI resolver before ``policy_spec_from_uri`` is called.
    """
    raw = os.environ.get("COGAMES_POLICY_DISCOVERY_PACKAGES", "").strip()
    if not raw:
        return
    packages = [name.strip() for name in raw.split(",") if name.strip()]
    discover_and_register_policies(*packages)


def main() -> None:
    _discover_extra_packages()
    asyncio.run(
        run_bridge(
            engine_ws_url=os.environ["COGAMES_ENGINE_WS_URL"],
            policy_uri=os.environ["COGAMES_POLICY_URI"],
            device=os.environ.get("COGAMES_POLICY_DEVICE", "cpu"),
        )
    )


if __name__ == "__main__":
    main()
