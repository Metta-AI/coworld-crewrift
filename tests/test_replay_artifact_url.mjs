import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const { replayArtifactUrl } = createRequire(import.meta.url)(
  "../replay_viewer/static_replay_adapter.js"
);

test("reads #replay= from the hash on the host mint URL", () => {
  assert.equal(
    replayArtifactUrl("index.html?v=2#replay=https://cdn.example/ep.bitreplay"),
    "https://cdn.example/ep.bitreplay"
  );
  assert.equal(
    replayArtifactUrl(
      "index.html?v=2#replay=" +
        encodeURIComponent("https://cdn.example/ep.bitreplay?sig=1&exp=2")
    ),
    "https://cdn.example/ep.bitreplay?sig=1&exp=2"
  );
});

test("prefers #replay= over query replay, replay_url, and uri", () => {
  assert.equal(
    replayArtifactUrl(
      "https://host.example/?replay=query.bitreplay&replay_url=alias.bitreplay&uri=uri.bitreplay#replay=https://cdn.example/hash.bitreplay"
    ),
    "https://cdn.example/hash.bitreplay"
  );
});

test("falls back to query replay", () => {
  assert.equal(
    replayArtifactUrl("https://host.example/?replay=https://cdn.example/q.bitreplay"),
    "https://cdn.example/q.bitreplay"
  );
});

test("falls back to query replay_url when replay is absent", () => {
  assert.equal(
    replayArtifactUrl(
      "https://host.example/?replay_url=https://cdn.example/a.bitreplay&uri=https://cdn.example/u.bitreplay"
    ),
    "https://cdn.example/a.bitreplay"
  );
});

test("falls back to query uri when replay and replay_url are absent", () => {
  assert.equal(
    replayArtifactUrl("https://host.example/?uri=https://cdn.example/u.bitreplay"),
    "https://cdn.example/u.bitreplay"
  );
});

test("returns null when hash and query have no replay artifact URL", () => {
  assert.equal(replayArtifactUrl("https://host.example/index.html?v=2"), null);
  assert.equal(replayArtifactUrl("index.html?v=2#replay="), null);
});
