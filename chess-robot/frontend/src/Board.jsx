const GLYPHS = {
  K: '♔', Q: '♕', R: '♖', B: '♗', N: '♘', P: '♙',
  k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟',
}

// Parsea la parte de piezas de un FEN a una matriz [fila8..fila1][col a..h].
function parseFen(fen) {
  const rows = fen.split(' ')[0].split('/')
  return rows.map((row) => {
    const squares = []
    for (const ch of row) {
      if (/\d/.test(ch)) squares.push(...Array(Number(ch)).fill(null))
      else squares.push(ch)
    }
    return squares
  })
}

export default function Board({ fen, lastMove, mismatched = [] }) {
  const grid = parseFen(fen)
  const highlight = new Set()
  if (lastMove) {
    highlight.add(lastMove.slice(0, 2))
    highlight.add(lastMove.slice(2, 4))
  }
  const conflict = new Set(mismatched)
  const files = 'abcdefgh'

  return (
    <div className="board">
      {grid.map((row, r) =>
        row.map((piece, c) => {
          const name = files[c] + (8 - r)
          const dark = (r + c) % 2 === 1
          const classes = ['square', dark ? 'dark' : 'light']
          if (highlight.has(name)) classes.push('last-move')
          if (conflict.has(name)) classes.push('conflict')
          return (
            <div key={name} className={classes.join(' ')}>
              {piece && (
                <span className={/[A-Z]/.test(piece) ? 'piece white' : 'piece black'}>
                  {GLYPHS[piece]}
                </span>
              )}
            </div>
          )
        }),
      )}
    </div>
  )
}
