import std/[json, monotimes, os, tempfiles, times]
import bitworld/[decision_trajectory, native_http, runtime_input]

proc readGameInput*(value, source: string): string =
  ## Own each configuration/replay transfer and retain private failure evidence.
  var control: NativeRequestControl
  var captures: seq[RuntimeInputCapture]
  let evidence = createTempDir("crewrift-input-", "") / "input.json"
  try:
    result = readRuntimeInput(value, source,
      getMonoTime() + initDuration(seconds = 60), control,
      128 * 1024 * 1024, 64 * 1024, captures)
  finally:
    writePrivate(evidence, $runtimeInputCapturesJson(captures))
