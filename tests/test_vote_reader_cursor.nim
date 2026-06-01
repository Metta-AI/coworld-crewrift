import
  std/unittest,
  ../players/notsus/notsus/votereader

suite "vote reader cursor":
  test "quantizes second row cursor to the drawn slot":
    let cell = voteReaderCellOrigin(8, 7)

    check voteReaderCellAtPoint(8, cell.x, cell.y) == 7
