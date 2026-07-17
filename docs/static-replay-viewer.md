# Static replay viewer

Crewrift owns a self-contained static replay-viewer bundle. Its WASM module
parses existing `.bitreplay` bytes, resimulates them with the deterministic
Crewrift core from the same build, and emits the public Bitworld Sprite v1
presentation packets consumed by Bitworld's shared global/replay renderer.

Build it from the repository root:

```sh
tools/build_static_replay_viewer.sh
```

The default bundle directory is `build/static-replay-viewer/`; pass a directory
as the first argument to override it. The build requires Emscripten and the
`nimby.lock` dependencies. It refuses to build if the installed Bitworld commit
does not match the lock file. `index.html` is the entrypoint inferred by
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

The real production `coworld_manifest.json` is intentionally unchanged.
