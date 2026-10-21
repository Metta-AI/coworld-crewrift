## Source-owned social teacher. No role, map or engine authority enters its input.
import std/[json, strutils]
import crunchy
import socials

const TeacherSource = staticRead("scripted.nim")

proc teacherAuthoritySha256*(): string =
  sha256(TeacherSource).toHex().toLowerAscii()

proc scriptedSocialAction*(prompt: JsonNode, observationTick: int): SocialLlmResult =
  doAssert prompt.kind == JArray and prompt.len > 0
  doAssert prompt[^1]["role"].getStr() == "user"
  doAssert prompt[^1]["content"].getStr().len > 0
  doAssert observationTick >= 0
  const requests = ["What did everyone see?", "Please share where you were.",
    "Did anyone see the body?", "Who can describe what happened?"]
  result = SocialLlmResult(message: requests[observationTick mod requests.len], claims: @[])
