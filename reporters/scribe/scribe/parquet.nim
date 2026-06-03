import
  event_log

const
  ParquetMagic = "PAR1"
  CreatedBy = "crewrift-scribe.parquet.v1"

  CtStop = 0'u8
  CtI32 = 5'u8
  CtI64 = 6'u8
  CtBinary = 8'u8
  CtList = 9'u8
  CtStruct = 12'u8

  TypeInt64 = 2
  TypeByteArray = 6
  RepetitionOptional = 1
  ConvertedUtf8 = 0
  EncodingPlain = 0
  EncodingRle = 3
  EncodingBitPacked = 4
  CompressionUncompressed = 0
  PageTypeDataPage = 0

type
  ColumnSpec = object
    name: string
    parquetType: int
    convertedType: int

  ColumnChunkInfo = object
    metadata: string
    offset: int64
    totalSize: int64

const Columns = [
  ColumnSpec(name: "ts", parquetType: TypeInt64, convertedType: -1),
  ColumnSpec(name: "player", parquetType: TypeInt64, convertedType: -1),
  ColumnSpec(name: "key", parquetType: TypeByteArray, convertedType: ConvertedUtf8),
  ColumnSpec(name: "value", parquetType: TypeByteArray, convertedType: ConvertedUtf8)
]

proc addByte(buffer: var string, value: uint64) =
  buffer.add char(value and 0xff'u64)

proc addVarUInt(buffer: var string, value: uint64) =
  var remaining = value
  while remaining >= 0x80'u64:
    buffer.addByte((remaining and 0x7f'u64) or 0x80'u64)
    remaining = remaining shr 7
  buffer.addByte(remaining)

proc addZigZag(buffer: var string, value: int64) =
  if value < 0:
    buffer.addVarUInt(uint64((-value * 2) - 1))
  else:
    buffer.addVarUInt(uint64(value * 2))

proc addFieldHeader(
  buffer: var string,
  lastFieldId: var int,
  fieldId: int,
  compactType: uint8
) =
  let delta = fieldId - lastFieldId
  if delta > 0 and delta <= 15:
    buffer.addByte(uint64((delta shl 4) or int(compactType)))
  else:
    buffer.addByte(compactType)
    buffer.addZigZag(int64(fieldId))
  lastFieldId = fieldId

proc addStop(buffer: var string) =
  buffer.addByte(CtStop)

proc addI32Field(buffer: var string, lastFieldId: var int, fieldId, value: int) =
  buffer.addFieldHeader(lastFieldId, fieldId, CtI32)
  buffer.addZigZag(int64(value))

proc addI64Field(buffer: var string, lastFieldId: var int, fieldId: int, value: int64) =
  buffer.addFieldHeader(lastFieldId, fieldId, CtI64)
  buffer.addZigZag(value)

proc addString(buffer: var string, value: string) =
  buffer.addVarUInt(uint64(value.len))
  buffer.add value

proc addStringField(
  buffer: var string,
  lastFieldId: var int,
  fieldId: int,
  value: string
) =
  buffer.addFieldHeader(lastFieldId, fieldId, CtBinary)
  buffer.addString(value)

proc addStructField(
  buffer: var string,
  lastFieldId: var int,
  fieldId: int,
  value: string
) =
  buffer.addFieldHeader(lastFieldId, fieldId, CtStruct)
  buffer.add value

proc addListHeader(buffer: var string, length: int, elementType: uint8) =
  if length < 15:
    buffer.addByte(uint64((length shl 4) or int(elementType)))
  else:
    buffer.addByte(0xf0'u64 or uint64(elementType))
    buffer.addVarUInt(uint64(length))

proc addI32ListField(
  buffer: var string,
  lastFieldId: var int,
  fieldId: int,
  values: openArray[int]
) =
  buffer.addFieldHeader(lastFieldId, fieldId, CtList)
  buffer.addListHeader(values.len, CtI32)
  for value in values:
    buffer.addZigZag(int64(value))

proc addStringListField(
  buffer: var string,
  lastFieldId: var int,
  fieldId: int,
  values: openArray[string]
) =
  buffer.addFieldHeader(lastFieldId, fieldId, CtList)
  buffer.addListHeader(values.len, CtBinary)
  for value in values:
    buffer.addString(value)

proc addStructListField(
  buffer: var string,
  lastFieldId: var int,
  fieldId: int,
  values: openArray[string]
) =
  buffer.addFieldHeader(lastFieldId, fieldId, CtList)
  buffer.addListHeader(values.len, CtStruct)
  for value in values:
    buffer.add value

proc addLe32(buffer: var string, value: int) =
  let raw = uint32(value)
  for shift in countup(0, 24, 8):
    buffer.addByte(uint64((raw shr shift) and 0xff'u32))

proc addLe64(buffer: var string, value: int64) =
  let raw = cast[uint64](value)
  for shift in countup(0, 56, 8):
    buffer.addByte((raw shr shift) and 0xff'u64)

proc schemaElement(
  name: string,
  parquetType = -1,
  numChildren = -1,
  convertedType = -1
): string =
  var lastFieldId = 0
  if parquetType >= 0:
    result.addI32Field(lastFieldId, 1, parquetType)
    result.addI32Field(lastFieldId, 3, RepetitionOptional)
  result.addStringField(lastFieldId, 4, name)
  if numChildren >= 0:
    result.addI32Field(lastFieldId, 5, numChildren)
  if convertedType >= 0:
    result.addI32Field(lastFieldId, 6, convertedType)
  result.addStop()

proc pageHeader(numValues: int, pageSize: int): string =
  var dataPageHeader: string
  var lastDataField = 0
  dataPageHeader.addI32Field(lastDataField, 1, numValues)
  dataPageHeader.addI32Field(lastDataField, 2, EncodingPlain)
  dataPageHeader.addI32Field(lastDataField, 3, EncodingRle)
  dataPageHeader.addI32Field(lastDataField, 4, EncodingRle)
  dataPageHeader.addStop()

  var lastFieldId = 0
  result.addI32Field(lastFieldId, 1, PageTypeDataPage)
  result.addI32Field(lastFieldId, 2, pageSize)
  result.addI32Field(lastFieldId, 3, pageSize)
  result.addStructField(lastFieldId, 5, dataPageHeader)
  result.addStop()

proc encodeInt64Column(rows: openArray[EventLogRow], columnIndex: int): string =
  for row in rows:
    let value =
      if columnIndex == 0:
        row.ts
      else:
        row.player
    result.addLe64(value)

proc encodeByteArrayColumn(rows: openArray[EventLogRow], columnIndex: int): string =
  for row in rows:
    let value =
      if columnIndex == 2:
        row.key
      else:
        row.value
    result.addLe32(value.len)
    result.add value

proc encodeDefinitionLevels(numValues: int): string =
  if numValues <= 0:
    return

  var run: string
  run.addVarUInt(uint64(numValues shl 1))
  run.addByte(1)
  result.addLe32(run.len)
  result.add run

proc columnMetaData(
  spec: ColumnSpec,
  rowCount: int,
  dataPageOffset: int64,
  totalSize: int64
): string =
  var lastFieldId = 0
  result.addI32Field(lastFieldId, 1, spec.parquetType)
  result.addI32ListField(
    lastFieldId,
    2,
    [EncodingPlain, EncodingRle, EncodingBitPacked]
  )
  result.addStringListField(lastFieldId, 3, [spec.name])
  result.addI32Field(lastFieldId, 4, CompressionUncompressed)
  result.addI64Field(lastFieldId, 5, int64(rowCount))
  result.addI64Field(lastFieldId, 6, totalSize)
  result.addI64Field(lastFieldId, 7, totalSize)
  result.addI64Field(lastFieldId, 9, dataPageOffset)
  result.addStop()

proc columnChunk(info: ColumnChunkInfo): string =
  var lastFieldId = 0
  result.addI64Field(lastFieldId, 2, info.offset)
  result.addStructField(lastFieldId, 3, info.metadata)
  result.addStop()

proc rowGroup(columnChunks: openArray[string], rowCount: int, totalSize: int64): string =
  var lastFieldId = 0
  result.addStructListField(lastFieldId, 1, columnChunks)
  result.addI64Field(lastFieldId, 2, totalSize)
  result.addI64Field(lastFieldId, 3, int64(rowCount))
  result.addI64Field(lastFieldId, 6, totalSize)
  result.addStop()

proc fileMetaData(rowGroups: openArray[string], rowCount: int): string =
  var schema: seq[string] = @[schemaElement("schema", numChildren = Columns.len)]
  for spec in Columns:
    schema.add schemaElement(
      spec.name,
      parquetType = spec.parquetType,
      convertedType = spec.convertedType
    )

  var lastFieldId = 0
  result.addI32Field(lastFieldId, 1, 1)
  result.addStructListField(lastFieldId, 2, schema)
  result.addI64Field(lastFieldId, 3, int64(rowCount))
  result.addStructListField(lastFieldId, 4, rowGroups)
  result.addStringField(lastFieldId, 6, CreatedBy)
  result.addStop()

proc renderEventLogParquet*(rows: openArray[EventLogRow]): string =
  ## Writes the Coworld event-log schema as a minimal Parquet v1 file.
  ##
  ## The writer is intentionally fixed to the reporter contract:
  ## `ts:int64`, `player:int64`, `key:string`, `value:string`,
  ## one uncompressed PLAIN-encoded data page per column, and a single row group.
  result.add ParquetMagic

  var rowGroups: seq[string]
  if rows.len > 0:
    var chunks: seq[ColumnChunkInfo]
    for columnIndex, spec in Columns:
      let columnData =
        if spec.parquetType == TypeInt64:
          encodeDefinitionLevels(rows.len) & rows.encodeInt64Column(columnIndex)
        else:
          encodeDefinitionLevels(rows.len) & rows.encodeByteArrayColumn(columnIndex)
      let header = pageHeader(rows.len, columnData.len)
      let offset = int64(result.len)
      result.add header
      result.add columnData
      let totalSize = int64(header.len + columnData.len)
      chunks.add ColumnChunkInfo(
        metadata: columnMetaData(spec, rows.len, offset, totalSize),
        offset: offset,
        totalSize: totalSize
      )

    var columnChunks: seq[string]
    var totalSize = 0'i64
    for chunk in chunks:
      columnChunks.add columnChunk(chunk)
      totalSize += chunk.totalSize
    rowGroups.add rowGroup(columnChunks, rows.len, totalSize)

  let metadata = fileMetaData(rowGroups, rows.len)
  result.add metadata
  result.addLe32(metadata.len)
  result.add ParquetMagic
