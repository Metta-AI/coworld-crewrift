import
  std/unittest,
  ../players/notsus/notsus/bedrocks

suite "notsus Bedrock signing":
  test "canonical URI double-encodes the HTTP model path":
    let previousModel = bedrockModel
    defer:
      bedrockModel = previousModel

    bedrockModel = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    check bedrockPath() ==
      "/model/us.anthropic.claude-haiku-4-5-20251001-v1%3A0/invoke"
    check bedrockCanonicalPath() ==
      "/model/us.anthropic.claude-haiku-4-5-20251001-v1%253A0/invoke"
