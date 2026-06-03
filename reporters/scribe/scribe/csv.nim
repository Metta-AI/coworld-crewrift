proc csvEscape*(value: string): string =
  ## Escapes one field for RFC-4180-style CSV output.
  var needsQuotes = false
  for ch in value:
    if ch in {',', '"', '\n', '\r'}:
      needsQuotes = true
      break
  if not needsQuotes:
    return value

  result.add('"')
  for ch in value:
    if ch == '"':
      result.add("\"\"")
    else:
      result.add(ch)
  result.add('"')

proc csvLine*(fields: openArray[string]): string =
  for i, field in fields:
    if i > 0:
      result.add(',')
    result.add field.csvEscape()
  result.add('\n')

proc csvHeader*(fields: openArray[string]): string =
  csvLine(fields)
