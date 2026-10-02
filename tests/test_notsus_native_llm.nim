include "../players/notsus/notsus/bedrocks"

putEnv("COWORLD_LLM_ENDPOINT", "http://127.0.0.1:19350/")
putEnv("COWORLD_LLM_MODEL", "anthropic/claude-haiku-4.5")
putEnv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "http://retired.invalid")
let request = buildBedrockRequest(@[ConversationMessage(role: "user", content: "state")], "test")
doAssert request.url == "http://127.0.0.1:19350/v1/messages"
let body = parseJson(request.body)
doAssert body["model"].getStr() == "anthropic/claude-haiku-4.5"
doAssert not body.hasKey("anthropic_version") and not body.hasKey("requestMetadata")
for (name, _) in request.headers:
  doAssert name.toLowerAscii() notin ["authorization", "x-api-key"]
