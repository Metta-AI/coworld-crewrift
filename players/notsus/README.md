# notsus

Run the bot headless:

```sh
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:8080/player?slot=0&token=' \
nim r players/notsus/notsus.nim
```

Run one bot with the pathing debugger:

```sh
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:8080/player?slot=0&token=' \
nim r -d:notsusGui players/notsus/notsus.nim
```

The debugger shows the sprite viewport, the decompressed walkability mask, the
current viewport rectangle, player position, visible objects, current goal,
roam goal, A* path, selected path step, input mask, velocity, and stuck state.
It scales the Silky UI from the current Windy backing size each frame, so moving
the window between high-DPI and low-DPI screens keeps the layout readable.
