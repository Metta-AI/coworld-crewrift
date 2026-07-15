import
  std/[algorithm, json, options, os, strutils, times, uri],
  crunchy/sha256,
  curly

const
  BedrockVersion = "bedrock-2023-05-31"
  BedrockTransport = "invoke-model-sigv4"
  SidecarTransport = "invoke-model-sidecar"
  SidecarEndpointEnv = "AWS_ENDPOINT_URL_BEDROCK_RUNTIME"
  DefaultRegion = "us-east-1"
  DefaultModel = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
  DefaultPlayerName = "notsus"
  BedrockService = "bedrock"
  AwsRequestType = "aws4_request"
  StsVersion = "2011-06-15"
  StsAction = "AssumeRoleWithWebIdentity"
  MaxMetadataValueLen = 256
  BedrockTimeoutSeconds = 5.0'f32
  BedrockMaxTokens = 512
  BedrockTemperature = 0.2
  MaxErrorBodyLen = 600
  SidecarHealthAttempts = 5
  SidecarHealthDelayMs = 250

type
  BedrockError = object of CatchableError

  ConversationMessage* = object
    role*: string
    content*: string

  BedrockAsyncResult* = object
    ready*: bool
    tag*: string
    reply*: string
    usage*: string
    error*: string

  BedrockRequest = object
    url: string
    headers: HttpHeaders
    body: string
    tag: string

  AwsCredentials = object
    accessKeyId: string
    secretAccessKey: string
    sessionToken: string
    source: string

var
  bedrockRegion* = getEnv("AWS_REGION")
  bedrockModel* = getEnv("BEDROCK_MODEL")
  bedrockPlayerName* = getEnv("BEDROCK_PLAYER_NAME")
  lastUsage: string
  lastError: string
  cachedCredentials: AwsCredentials

let
  syncCurl = newCurlPool(3)
  asyncCurl = newCurly()

proc fail(message: string) {.raises: [BedrockError].} =
  ## Raises one Bedrock client error.
  raise newException(BedrockError, message)

proc truthy(value: string): bool =
  ## Returns true when an environment flag is enabled.
  case value.strip().toLowerAscii()
  of "1", "true", "yes", "y", "on":
    true
  else:
    false

proc region(): string =
  ## Returns the configured AWS Region for Bedrock requests.
  result = bedrockRegion.strip()
  if result.len == 0:
    result = getEnv("AWS_DEFAULT_REGION").strip()
  if result.len == 0:
    result = DefaultRegion

proc model(): string =
  ## Returns the configured Bedrock model ID for requests.
  result = bedrockModel.strip()
  if result.len == 0:
    result = getEnv("BEDROCK_CLAUDE_MODEL_ID").strip()
  if result.len == 0:
    result = DefaultModel

proc playerName(): string =
  ## Returns the player name attached to Bedrock request metadata.
  result = bedrockPlayerName.strip()
  if result.len == 0:
    result = getEnv("COWORLD_POLICY_NAME").strip()
  if result.len == 0:
    result = DefaultPlayerName

proc byteHex(value: uint8): string =
  ## Returns one lowercase two-digit hex byte.
  value.toHex(2).toLowerAscii()

proc hex(bytes: openArray[uint8]): string =
  ## Returns a lowercase hex string for bytes.
  for b in bytes:
    result.add b.byteHex()

proc sha256Hex(data: string): string =
  ## Returns the SHA-256 digest as lowercase hex.
  data.sha256().hex()

proc metadataSafe(value: string): string =
  ## Coerces one Bedrock metadata value to the safe character set.
  for ch in value:
    if result.len >= MaxMetadataValueLen:
      break
    if ch in {'A' .. 'Z', 'a' .. 'z', '0' .. '9',
        ' ', ':', '_', '@', '$', '#', '=', '/', '+', ',', '.', '-'}:
      result.add ch
    else:
      result.add '_'

proc metadataKeySafe(value: string): string =
  ## Coerces one Bedrock metadata key to a stable safe form.
  result = value.metadataSafe()
  if result.len == 0:
    result = "tag"

proc awsUriEncode(value: string): string =
  ## Percent-encodes one AWS URI path label.
  for ch in value:
    if ch in {'A' .. 'Z', 'a' .. 'z', '0' .. '9', '-', '.', '_', '~'}:
      result.add ch
    else:
      result.add '%'
      result.add ch.uint8.toHex(2)

proc bedrockHost(): string =
  ## Returns the Bedrock Runtime host for the selected Region.
  "bedrock-runtime." & region() & ".amazonaws.com"

proc endpointBase(): string =
  ## Returns the runner Bedrock sidecar endpoint if configured.
  getEnv(SidecarEndpointEnv).strip()

proc hasSidecarEndpoint(): bool =
  ## Returns true when the hosted Bedrock sidecar is configured.
  endpointBase().len > 0

proc sidecarConfigured*(): bool =
  ## Returns true when Bedrock calls should go through the runner sidecar.
  hasSidecarEndpoint()

proc bedrockPath*(): string =
  ## Returns the once-encoded HTTP request path used for both direct AWS and
  ## sidecar calls. Use bedrockCanonicalPath only while signing direct requests.
  "/model/" & model().awsUriEncode() & "/invoke"

proc bedrockCanonicalPath*(): string =
  ## Returns the SigV4 canonical URI used only when notsus signs a direct AWS
  ## request. The HTTP path already encodes ':' as '%3A'; canonicalization
  ## encodes that '%' as '%25', yielding '%253A'. AWS's standard SigV4 signer
  ## enables this double encoding for non-S3 services:
  ## https://docs.aws.amazon.com/java/api/latest/software/amazon/awssdk/auth/signer/params/Aws4SignerParams.Builder.html#doubleUrlEncode(java.lang.Boolean)
  "/model/" & model().awsUriEncode().awsUriEncode() & "/invoke"

proc joinUrl(base, path: string): string =
  ## Joins one base URL and absolute path.
  result = base.strip()
  while result.endsWith("/"):
    result.setLen(result.len - 1)
  if path.len > 0 and not path.startsWith("/"):
    result.add '/'
  result.add path

proc bedrockUrl(): string =
  ## Returns the Bedrock Runtime InvokeModel URL.
  let endpoint = endpointBase()
  if endpoint.len > 0:
    return endpoint.joinUrl(bedrockPath())
  "https://" & bedrockHost() & bedrockPath()

proc runtimeText*(): string =
  ## Returns the Bedrock request method and target model summary.
  if hasSidecarEndpoint():
    return "method=" & SidecarTransport & " model=" & model() &
      " endpoint_env=" & SidecarEndpointEnv
  "method=" & BedrockTransport & " model=" & model() &
    " region=" & region()

proc clippedBody(body: string): string =
  ## Returns a compact single-line response body for diagnostics.
  result = body.strip().replace("\n", " ")
  if result.len > MaxErrorBodyLen:
    result = result[0 ..< MaxErrorBodyLen] & "..."

proc sidecarHealthText*(): string =
  ## Returns the Bedrock sidecar health response summary.
  if not hasSidecarEndpoint():
    return "not-configured"
  var lastError = ""
  for attempt in 0 ..< SidecarHealthAttempts:
    try:
      let response = syncCurl.get(
        endpointBase().joinUrl("/healthz"),
        timeout = 1.0'f32
      )
      result = "http=" & $response.code
      let body = response.body.clippedBody()
      if body.len > 0:
        result.add " body="
        result.add body
      if response.code == 200:
        return
    except CatchableError as e:
      lastError = e.msg
    if attempt + 1 < SidecarHealthAttempts:
      sleep(SidecarHealthDelayMs)
  if result.len > 0:
    return
  fail(lastError)

proc intField(node: JsonNode, name: string): int =
  ## Reads one integer JSON field if present.
  if node.kind == JObject and node.hasKey(name):
    let child = node[name]
    if child.kind == JInt:
      return child.getInt()
  0

proc nodeText(node: JsonNode): string =
  ## Converts one JSON node to a metadata-safe string.
  if node.kind == JString:
    return node.getStr()
  $node

proc requestMetadata(): JsonNode =
  ## Builds the Bedrock request metadata for cost attribution.
  result = newJObject()
  let raw = getEnv("BEDROCK_REQUEST_METADATA").strip()
  if raw.len > 0:
    let parsed = parseJson(raw)
    if parsed.kind != JObject:
      fail("BEDROCK_REQUEST_METADATA must be a JSON object.")
    for key, value in parsed:
      result[key.metadataKeySafe()] = %value.nodeText().metadataSafe()
  let name = playerName().metadataSafe()
  result["player_name"] = %name
  result["bot"] = %name
  result["policy_name"] = %name

proc requestMetadataKeys*(): string =
  ## Returns the metadata keys that will be sent with Bedrock requests.
  if hasSidecarEndpoint():
    return "sidecar-owned"
  let metadata = requestMetadata()
  for key in metadata.keys:
    if result.len > 0:
      result.add ","
    result.add key

proc conversationBody(
  messages: openArray[ConversationMessage],
  includeMetadata: bool
): string =
  ## Builds one Anthropic Bedrock InvokeModel JSON request body.
  var
    systemText = ""
    chat = newJArray()
  for message in messages:
    if message.role == "system":
      if systemText.len > 0:
        systemText.add "\n\n"
      systemText.add message.content
    else:
      var item = newJObject()
      item["role"] = %message.role
      item["content"] = %message.content
      chat.add item
  var root = newJObject()
  root["anthropic_version"] = %BedrockVersion
  root["max_tokens"] = %BedrockMaxTokens
  root["temperature"] = %BedrockTemperature
  if systemText.len > 0:
    root["system"] = %systemText
  root["messages"] = chat
  if includeMetadata:
    root["requestMetadata"] = requestMetadata()
  $root

proc xmlTag(body, name: string): string =
  ## Extracts one simple XML tag value.
  let
    openTag = "<" & name & ">"
    closeTag = "</" & name & ">"
    start = body.find(openTag)
  if start < 0:
    return ""
  let valueStart = start + openTag.len
  let stop = body.find(closeTag, valueStart)
  if stop < valueStart:
    return ""
  body[valueStart ..< stop]

proc credentialsFromEnv(): AwsCredentials =
  ## Returns static AWS credentials from the process environment.
  result.accessKeyId = getEnv("AWS_ACCESS_KEY_ID").strip()
  result.secretAccessKey = getEnv("AWS_SECRET_ACCESS_KEY").strip()
  result.sessionToken = getEnv("AWS_SESSION_TOKEN").strip()
  if result.accessKeyId.len > 0 and result.secretAccessKey.len > 0:
    result.source = "env"

proc containerAuthorization(): string =
  ## Returns the container credential authorization token if configured.
  result = getEnv("AWS_CONTAINER_AUTHORIZATION_TOKEN").strip()
  if result.len > 0:
    return
  let path = getEnv("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE").strip()
  if path.len > 0 and fileExists(path):
    result = readFile(path).strip()

proc credentialsFromContainer(): AwsCredentials =
  ## Returns AWS credentials from the container credential endpoint.
  var url = getEnv("AWS_CONTAINER_CREDENTIALS_FULL_URI").strip()
  if url.len == 0:
    let relative = getEnv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI").strip()
    if relative.len > 0:
      url = "http://169.254.170.2" & relative
  if url.len == 0:
    return
  var headers: seq[(string, string)]
  let token = containerAuthorization()
  if token.len > 0:
    headers.add ("Authorization", token)
  let response = syncCurl.get(url, headers, timeout = 2.0'f32)
  if response.code != 200:
    fail("Container credentials returned HTTP " & $response.code & ".")
  let data = parseJson(response.body)
  result.accessKeyId = data{"AccessKeyId"}.getStr().strip()
  result.secretAccessKey = data{"SecretAccessKey"}.getStr().strip()
  result.sessionToken = data{"Token"}.getStr().strip()
  if result.accessKeyId.len > 0 and result.secretAccessKey.len > 0:
    result.source = "container"

proc stsBody(roleArn, sessionName, token: string): string =
  ## Builds one STS AssumeRoleWithWebIdentity form body.
  let pairs = [
    ("Action", StsAction),
    ("Version", StsVersion),
    ("RoleArn", roleArn),
    ("RoleSessionName", sessionName),
    ("WebIdentityToken", token),
    ("DurationSeconds", "3600")
  ]
  for pair in pairs:
    if result.len > 0:
      result.add '&'
    result.add encodeUrl(pair[0])
    result.add '='
    result.add encodeUrl(pair[1])

proc credentialsFromWebIdentity(): AwsCredentials =
  ## Returns AWS credentials from IRSA web identity.
  let
    roleArn = getEnv("AWS_ROLE_ARN").strip()
    tokenPath = getEnv("AWS_WEB_IDENTITY_TOKEN_FILE").strip()
  if roleArn.len == 0 or tokenPath.len == 0:
    return
  if not fileExists(tokenPath):
    fail("AWS_WEB_IDENTITY_TOKEN_FILE does not exist.")
  let sessionName =
    if getEnv("AWS_ROLE_SESSION_NAME").strip().len > 0:
      getEnv("AWS_ROLE_SESSION_NAME").strip()
    else:
      "notsus-bedrock"
  let body = stsBody(roleArn, sessionName, readFile(tokenPath).strip())
  let endpoint = "https://sts." & region() & ".amazonaws.com/"
  let response = syncCurl.post(
    endpoint,
    @[("Content-Type", "application/x-www-form-urlencoded")],
    body,
    timeout = 4.0'f32
  )
  if response.code != 200:
    fail("STS web identity returned HTTP " & $response.code & ".")
  result.accessKeyId = response.body.xmlTag("AccessKeyId").strip()
  result.secretAccessKey = response.body.xmlTag("SecretAccessKey").strip()
  result.sessionToken = response.body.xmlTag("SessionToken").strip()
  if result.accessKeyId.len > 0 and result.secretAccessKey.len > 0:
    result.source = "web-identity"

proc resolveCredentials(): AwsCredentials =
  ## Resolves AWS credentials through the hosted Bedrock credential chain.
  if cachedCredentials.accessKeyId.len > 0:
    return cachedCredentials
  result = credentialsFromEnv()
  if result.source.len == 0:
    result = credentialsFromContainer()
  if result.source.len == 0:
    result = credentialsFromWebIdentity()
  if result.source.len == 0:
    fail("No AWS credentials found for Bedrock.")
  cachedCredentials = result

proc credentialSignalText*(): string =
  ## Returns the first configured AWS credential source name.
  if hasSidecarEndpoint():
    return "sidecar-endpoint"
  if getEnv("AWS_ACCESS_KEY_ID").strip().len > 0 and
      getEnv("AWS_SECRET_ACCESS_KEY").strip().len > 0:
    return "env"
  if getEnv("AWS_CONTAINER_CREDENTIALS_FULL_URI").strip().len > 0 or
      getEnv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI").strip().len > 0:
    return "container"
  if getEnv("AWS_ROLE_ARN").strip().len > 0 and
      getEnv("AWS_WEB_IDENTITY_TOKEN_FILE").strip().len > 0:
    return "web-identity"
  if getEnv("USE_BEDROCK").truthy() or
      getEnv("CREWBORG_USE_BEDROCK").truthy() or
      getEnv("CLAUDE_CODE_USE_BEDROCK").truthy():
    return "use-bedrock"

proc hasAwsCredentialSignal*(): bool =
  ## Returns true if Bedrock is enabled or AWS credentials look available.
  credentialSignalText().len > 0

proc resolvedCredentialText*(): string =
  ## Resolves credentials and returns a redacted credential summary.
  if hasSidecarEndpoint():
    return "source=sidecar endpoint=present signing=runner"
  let credentials = resolveCredentials()
  result = "source=" & credentials.source & " access_key=present"
  if credentials.sessionToken.len > 0:
    result.add " session=present"
  else:
    result.add " session=missing"

proc canonicalHeaders(
  headers: openArray[(string, string)]
): tuple[text: string, signed: string] =
  ## Builds canonical headers and the signed header list.
  var sorted = @headers
  sorted.sort(
    proc(a, b: (string, string)): int =
      cmp(a[0], b[0])
  )
  for header in sorted:
    result.text.add header[0]
    result.text.add ':'
    result.text.add header[1].strip()
    result.text.add '\n'
    if result.signed.len > 0:
      result.signed.add ';'
    result.signed.add header[0]

proc signingKey(secretKey, dateStamp, regionName: string): array[32, uint8] =
  ## Returns the AWS SigV4 signing key.
  let dateKey = hmacSha256("AWS4" & secretKey, dateStamp)
  let regionKey = hmacSha256(dateKey, regionName)
  let serviceKey = hmacSha256(regionKey, BedrockService)
  hmacSha256(serviceKey, AwsRequestType)

proc signedHeaders(
  credentials: AwsCredentials,
  body: string,
  nowUtc: DateTime
): seq[(string, string)] =
  ## Builds AWS SigV4 headers for one Bedrock request.
  let
    dateStamp = nowUtc.format("yyyyMMdd")
    amzDate = nowUtc.format("yyyyMMdd'T'HHmmss'Z'")
    bodyHash = body.sha256Hex()
  var headers = @[
    ("accept", "application/json"),
    ("content-type", "application/json"),
    ("host", bedrockHost()),
    ("x-amz-content-sha256", bodyHash),
    ("x-amz-date", amzDate)
  ]
  if credentials.sessionToken.len > 0:
    headers.add ("x-amz-security-token", credentials.sessionToken)
  let canonical = canonicalHeaders(headers)
  let scope = dateStamp & "/" & region() & "/" & BedrockService & "/" &
    AwsRequestType
  let canonicalRequest = "POST\n" & bedrockCanonicalPath() & "\n\n" &
    canonical.text & "\n" & canonical.signed & "\n" & bodyHash
  let stringToSign = "AWS4-HMAC-SHA256\n" & amzDate & "\n" & scope &
    "\n" & canonicalRequest.sha256Hex()
  let signature = hmacSha256(
    signingKey(credentials.secretAccessKey, dateStamp, region()),
    stringToSign
  ).hex()
  let authorization = "AWS4-HMAC-SHA256 Credential=" &
    credentials.accessKeyId & "/" & scope &
    ", SignedHeaders=" & canonical.signed &
    ", Signature=" & signature
  headers.add ("authorization", authorization)
  result = headers

proc parseReply(body: string): string =
  ## Extracts output text from one Bedrock InvokeModel response body.
  let data = parseJson(body)
  if data.hasKey("usage"):
    let usage = data["usage"]
    let
      inputTokens = usage.intField("input_tokens") +
        usage.intField("inputTokens")
      outputTokens = usage.intField("output_tokens") +
        usage.intField("outputTokens")
      cacheRead = usage.intField("cache_read_input_tokens") +
        usage.intField("cacheReadInputTokens")
      cacheWrite = usage.intField("cache_creation_input_tokens") +
        usage.intField("cacheWriteInputTokens")
    lastUsage = "input=" & $inputTokens &
      " output=" & $outputTokens &
      " cache_read=" & $cacheRead &
      " cache_write=" & $cacheWrite
  else:
    lastUsage = ""
  if data.hasKey("content"):
    for part in data["content"]:
      if part.kind == JObject and part.hasKey("text"):
        result.add part["text"].getStr()
    return
  if data.hasKey("output") and
      data["output"].hasKey("message") and
      data["output"]["message"].hasKey("content"):
    let content = data["output"]["message"]["content"]
    for part in content:
      if part.kind == JObject and part.hasKey("text"):
        result.add part["text"].getStr()

proc lastUsageText*(): string =
  ## Returns compact usage text for the previous Bedrock call.
  lastUsage

proc lastErrorText*(): string =
  ## Returns the previous Bedrock call error if any.
  lastError

proc buildBedrockRequest(
  messages: openArray[ConversationMessage],
  tag: string
): BedrockRequest =
  ## Builds one Bedrock InvokeModel HTTP request.
  result.body = conversationBody(messages, not hasSidecarEndpoint())
  result.url = bedrockUrl()
  result.tag = tag
  result.headers =
    if hasSidecarEndpoint():
      @[
        ("Content-Type", "application/json"),
        ("Accept", "application/json")
      ]
    else:
      let credentials = resolveCredentials()
      signedHeaders(credentials, result.body, now().utc)

proc parseTalkResponse(response: Response): string =
  ## Parses one Bedrock InvokeModel HTTP response.
  if response.code != 200:
    var message = "Bedrock InvokeModel returned HTTP " & $response.code & "."
    let body = response.body.clippedBody()
    if body.len > 0:
      message.add " Body: "
      message.add body
    fail(message)
  response.body.parseReply()

proc talkToAI*(messages: var seq[ConversationMessage]): string =
  ## Sends messages to Bedrock InvokeModel and returns the reply text.
  lastUsage = ""
  lastError = ""
  try:
    let request = buildBedrockRequest(messages, "")
    let response = syncCurl.post(
      request.url,
      request.headers,
      request.body,
      timeout = BedrockTimeoutSeconds
    )
    result = response.parseTalkResponse()
    messages.add ConversationMessage(role: "assistant", content: result)
  except CatchableError as error:
    lastError = error.msg
    echo "ERROR: notsus Bedrock InvokeModel failed: ", error.msg

proc startTalkToAI*(
  messages: openArray[ConversationMessage],
  tag: string
) =
  ## Starts one nonblocking Bedrock InvokeModel request.
  lastUsage = ""
  lastError = ""
  try:
    let request = buildBedrockRequest(messages, tag)
    asyncCurl.startRequest(
      "POST",
      request.url,
      request.headers,
      request.body,
      timeout = max(1, BedrockTimeoutSeconds.int),
      tag = request.tag
    )
  except CatchableError as error:
    lastError = error.msg
    echo "ERROR: notsus Bedrock InvokeModel failed: ", error.msg

proc pollTalkToAI*(): BedrockAsyncResult =
  ## Polls for a completed nonblocking Bedrock InvokeModel request.
  let pulled = asyncCurl.pollForResponse()
  if pulled.isNone:
    return BedrockAsyncResult(ready: false)
  let item = pulled.get()
  result.ready = true
  result.tag = item.response.request.tag
  lastUsage = ""
  lastError = ""
  if item.error.len > 0:
    result.error = item.error
    lastError = item.error
    echo "ERROR: notsus Bedrock InvokeModel failed: ", item.error
    return
  try:
    result.reply = item.response.parseTalkResponse()
    result.usage = lastUsage
  except CatchableError as error:
    result.error = error.msg
    lastError = error.msg
    echo "ERROR: notsus Bedrock InvokeModel failed: ", error.msg
