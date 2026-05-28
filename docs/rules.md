# Crewrift Rules

Crewrift is an eight-player social deduction Coworld. Most players are crew.
A smaller number are imposters. Crew players win by completing tasks or voting
out all imposters. Imposters win by eliminating enough crew players or by
surviving the vote.

Players move through the map, read Sprite v1 updates, and send input packets to
their assigned websocket. The runner assigns each policy one slot and one token.
Policies must use the `COGAMES_ENGINE_WS_URL` value exactly as supplied.

## Game Flow

1. Players join the lobby.
2. The game reveals each player's role.
3. Crew players complete tasks while imposters try to blend in.
4. Players may report bodies or call meetings.
5. During meetings, players can chat and vote.
6. The game ends when crew or imposters reach a win condition.

## Scoring

The game writes one score per player in `scores`. The result payload also
includes role, win, task, kill, and vote fields so tournaments and reporters can
inspect what happened in the episode.
