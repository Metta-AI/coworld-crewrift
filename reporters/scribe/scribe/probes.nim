import
  bitworld/spriteprotocol,
  ../../../src/crewrift/sim

type
  BodyReportProbe* = object
    callerIndex*: int
    bodyIndex*: int
    bodyColor*: uint8

proc previousAttack(prevInputs: seq[InputState], index: int): bool =
  index < prevInputs.len and prevInputs[index].attack

proc findBodyReport*(
  postStep: SimServer,
  inputs, prevInputs: seq[InputState],
  bodyLimit: int
): BodyReportProbe =
  result = BodyReportProbe(callerIndex: -1, bodyIndex: -1, bodyColor: 255'u8)
  let cappedBodyLimit = min(bodyLimit, postStep.bodies.len)
  if cappedBodyLimit <= 0:
    return

  for playerIndex in 0 ..< min(inputs.len, postStep.players.len):
    if not inputs[playerIndex].attack or prevInputs.previousAttack(playerIndex):
      continue
    for limit in 1 .. cappedBodyLimit:
      var clone = postStep
      clone.phase = Playing
      clone.tryReport(playerIndex, limit)
      if clone.phase == Voting:
        return BodyReportProbe(
          callerIndex: playerIndex,
          bodyIndex: limit - 1,
          bodyColor: postStep.bodies[limit - 1].color
        )
