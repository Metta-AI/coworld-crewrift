# Static replay viewer

Crewrift owns a self-contained static replay-viewer bundle. Its WASM module
parses existing `.bitreplay` bytes, resimulates them with the deterministic
Crewrift core from the same build, and emits the public Bitworld Sprite v1
presentation packets consumed by Bitworld's shared global/replay renderer.

Build it from the repository root:

```sh
tools/build_replay_viewer.sh build/static-replay-viewer
```

During packaging, `coworld build` reads `game.replay_viewer.bundle` from the
source manifest and invokes the executable `tools/build_replay_viewer.sh` hook.
Its first argument is the absolute bundle path resolved relative to the
hydrated output manifest. For example, an output manifest at
`tmp/crewrift/coworld_manifest.json` receives a bundle at
`tmp/crewrift/build/static-replay-viewer/`. Coworld writes the hydrated manifest
only after that directory and its `index.html` exist.

The hook defaults to the repository-local `build/static-replay-viewer/` for
direct use. It recreates the requested directory on every build so removed or
renamed assets cannot survive into a package. The build requires Emscripten and
the `nimby.lock` dependencies. It refuses to build if the installed Bitworld
commit does not match the lock file. `index.html` is the entrypoint inferred by
Coworld; no bundle-internal files or ABI are part of the platform contract.

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

The source manifest names only the package-relative path
`build/static-replay-viewer`. `coworld upload-coworld` does not run the hook; it
validates, archives, and submits the bundle already produced by `coworld build`.
