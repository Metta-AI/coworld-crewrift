# Static replay viewer

Crewrift owns a self-contained static replay-viewer bundle. Its WASM module
parses existing `.bitreplay` bytes, resimulates them with the deterministic
Crewrift core from the same build, and emits the public Bitworld Sprite v1
presentation packets consumed by Bitworld's shared global/replay renderer.

Build it from the repository root:

```sh
tools/build_replay_viewer.sh build/static-replay-viewer
```

`coworld upload` invokes the executable `tools/build_replay_viewer.sh` hook with
`game.replay_viewer.bundle` as its first argument. The hook also defaults to
`build/static-replay-viewer/` for direct local use. It recreates that directory
on every build so removed or renamed assets cannot survive into an upload. The
build requires Emscripten and the `nimby.lock` dependencies. It refuses to build
if the installed Bitworld commit does not match the lock file. `index.html` is
the entrypoint inferred by Coworld; no bundle-internal files or ABI are part of
the platform contract.

At runtime, pass the browser-readable replay URL as `?replay=<url>`. The aliases
`replay_url` and `uri` are also accepted. With no URL the page offers a local
file picker and supports drag-and-drop. An embedding host may alternatively
post `{type: "coworld-replay", bytes: ArrayBuffer}` to the viewer window.

Serve the bundle over HTTP (WASM cannot be tested reliably from `file://`):

```sh
python3 -m http.server --directory build/static-replay-viewer 8000
```

Then open, for example:

```text
http://127.0.0.1:8000/?replay=/notsus.bitreplay
```

The browser smoke is `tests/smoke_static_replay_viewer.mjs`. Run it with a
Playwright installation and a URL that serves both the bundle and a replay:

```sh
node tests/smoke_static_replay_viewer.mjs \
  'http://127.0.0.1:8000/?replay=/notsus.bitreplay'
```

The authored manifest names only `build/static-replay-viewer`; Coworld owns
running the build hook and rewriting the uploaded bundle reference to its
immutable digest.
