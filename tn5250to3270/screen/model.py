from dataclasses import dataclass, field
from .cell import Cell, Color, Hilite
from .field import Field, FieldAttrs
from . import ops


class VirtualScreen:
    """The intermediate representation. Both protocol layers read/write here.

    Linear addressing: pos = row * cols + col (0-based throughout).
    Buffer is circular: pos wraps mod size.
    """

    def __init__(self, rows: int, cols: int):
        self.rows = rows
        self.cols = cols
        self.size = rows * cols
        self.cells: list[Cell] = [Cell() for _ in range(self.size)]
        self.fields: list[Field] = []
        self.cursor: int = 0
        self.write_ptr: int = 0
        self.keyboard_locked: bool = True
        self.alarm: bool = False
        # Sticky character attrs from SA order
        self._sa_fg: Color = Color.DEFAULT
        self._sa_bg: Color = Color.DEFAULT
        self._sa_hilite: Hilite = Hilite.NONE

    # ── Address helpers ──────────────────────────────────────────────

    def wrap(self, pos: int) -> int:
        return pos % self.size

    def to_rowcol(self, pos: int) -> tuple[int, int]:
        """0-based row, col."""
        p = self.wrap(pos)
        return divmod(p, self.cols)

    def from_rowcol(self, row: int, col: int) -> int:
        return self.wrap(row * self.cols + col)

    # ── Op dispatch ──────────────────────────────────────────────────

    def apply(self, op: ops.ScreenOp) -> None:
        match op:
            case ops.EraseAll():
                self._erase_all()
            case ops.SetBufferAddr(pos=p):
                self.write_ptr = self.wrap(p)
            case ops.WriteText(data=d):
                self._write_text(d)
            case ops.DefineField(pos=p, attrs=a):
                self._define_field(p, a)
            case ops.SetCursor(pos=p):
                self.cursor = self.wrap(p)
            case ops.RepeatChar(to_pos=t, char=c):
                self._repeat_char(t, c)
            case ops.EraseUnprotected(to_pos=t):
                self._erase_unprotected(t)
            case ops.ProgramTab():
                self._program_tab()
            case ops.SetExtAttr(attr_type=t, value=v):
                self._set_ext_attr(t, v)
            case ops.WccFlags(reset_mdt=rm, unlock_kbd=uk, alarm=al, restore=_):
                if rm:
                    for f in self.fields:
                        f.attrs.mdt = False
                if uk:
                    self.keyboard_locked = False
                self.alarm = al
            case _:
                raise NotImplementedError(f"apply: {type(op).__name__}")

    def _erase_all(self) -> None:
        self.cells = [Cell() for _ in range(self.size)]
        self.fields = []
        self.cursor = 0
        self.write_ptr = 0
        self._sa_fg = Color.DEFAULT
        self._sa_bg = Color.DEFAULT
        self._sa_hilite = Hilite.NONE

    def _write_text(self, data: bytes) -> None:
        for b in data:
            self.cells[self.write_ptr] = Cell(
                char=b,
                fg=self._sa_fg,
                bg=self._sa_bg,
                hilite=self._sa_hilite,
                is_field_attr=False,
            )
            self.write_ptr = self.wrap(self.write_ptr + 1)

    def _define_field(self, pos: int, attrs: FieldAttrs) -> None:
        pos = self.wrap(pos)
        # Mark the attribute cell
        self.cells[pos] = Cell(is_field_attr=True)
        # Replace if a field already starts here, else insert
        self.fields = [f for f in self.fields if f.start != pos]
        self.fields.append(Field(start=pos, length=0, attrs=attrs))
        self._recompute_field_lengths()
        self.write_ptr = self.wrap(pos + 1)

    def _recompute_field_lengths(self) -> None:
        """Each field's length = distance to next field start (circular)."""
        if not self.fields:
            return
        self.fields.sort(key=lambda f: f.start)
        n = len(self.fields)
        for i, f in enumerate(self.fields):
            next_start = self.fields[(i + 1) % n].start
            if n == 1:
                f.length = self.size
            else:
                f.length = (next_start - f.start) % self.size
                if f.length == 0:  # only happens if duplicate start, shouldn't
                    f.length = self.size

    def field_at(self, pos: int) -> Field | None:
        """Which field contains pos? None if no fields defined."""
        if not self.fields:
            return None
        pos = self.wrap(pos)
        # fields are sorted; find the last one whose start <= pos,
        # but account for wrap (the last field wraps past 0)
        for i in range(len(self.fields) - 1, -1, -1):
            if self.fields[i].start <= pos:
                return self.fields[i]
        # pos is before all field starts → it's in the wrapping last field
        return self.fields[-1]

    def _repeat_char(self, to_pos: int, char: int) -> None:
        to_pos = self.wrap(to_pos)
        p = self.write_ptr
        while p != to_pos:
            if not self.cells[p].is_field_attr:
                self.cells[p] = Cell(char=char, fg=self._sa_fg,
                                     bg=self._sa_bg, hilite=self._sa_hilite)
            p = self.wrap(p + 1)
        self.write_ptr = to_pos

    def _erase_unprotected(self, to_pos: int) -> None:
        to_pos = self.wrap(to_pos)
        p = self.write_ptr
        while p != to_pos:
            f = self.field_at(p)
            if f and not f.attrs.protected and not self.cells[p].is_field_attr:
                self.cells[p] = Cell()
            p = self.wrap(p + 1)
        self.write_ptr = to_pos

    def _program_tab(self) -> None:
        """Advance to data_start of next unprotected field after write_ptr."""
        if not self.fields:
            self.write_ptr = 0
            return
        # Find the next unprotected field whose start is > write_ptr (circular)
        sorted_fields = sorted(self.fields, key=lambda f: f.start)
        candidates = [f for f in sorted_fields if f.start > self.write_ptr] + \
                     [f for f in sorted_fields if f.start <= self.write_ptr]
        for f in candidates:
            if not f.attrs.protected:
                self.write_ptr = self.wrap(f.start + 1)  # data_start
                return
        self.write_ptr = 0

    # 3270 SA attribute type codes (GA23-0059 §4.4.5)
    _SA_RESET   = 0x00
    _SA_HILITE  = 0x41
    _SA_FG      = 0x42
    _SA_CHARSET = 0x43  # ignored
    _SA_BG      = 0x45

    # 3270 color codes → canonical Color (GA23-0059 Table 4-3)
    _3270_COLOR = {
        0x00: Color.DEFAULT, 0xF0: Color.DEFAULT,  # neutral
        0xF1: Color.BLUE, 0xF2: Color.RED, 0xF3: Color.PINK,
        0xF4: Color.GREEN, 0xF5: Color.TURQ, 0xF6: Color.YELLOW,
        0xF7: Color.WHITE,
    }
    _3270_HILITE = {
        0x00: Hilite.NONE, 0xF0: Hilite.NONE,
        0xF1: Hilite.BLINK, 0xF2: Hilite.REVERSE, 0xF4: Hilite.UNDERLINE,
    }

    def _set_ext_attr(self, attr_type: int, value: int) -> None:
        if attr_type == self._SA_RESET:
            self._sa_fg = Color.DEFAULT
            self._sa_bg = Color.DEFAULT
            self._sa_hilite = Hilite.NONE
        elif attr_type == self._SA_FG:
            self._sa_fg = self._3270_COLOR.get(value, Color.DEFAULT)
        elif attr_type == self._SA_BG:
            self._sa_bg = self._3270_COLOR.get(value, Color.DEFAULT)
        elif attr_type == self._SA_HILITE:
            self._sa_hilite = self._3270_HILITE.get(value, Hilite.NONE)
        # _SA_CHARSET ignored — we don't do APL

    # ── Read-side methods (for emitters) ─────────────────────────────

    def get_modified_fields(self) -> list[tuple[int, bytes]]:
        """Read Modified semantics: return (data_start, data) for each
        field with MDT set. Per GA23-0059 §3.5.4: nulls (0x00) are
        suppressed entirely — host receives a compressed stream."""
        result = []
        for f in self.fields:
            if not f.attrs.mdt:
                continue
            data = bytearray()
            p = self.wrap(f.start + 1)
            for _ in range(f.length - 1):
                c = self.cells[p].char
                if c != 0x00:
                    data.append(c)
                p = self.wrap(p + 1)
            result.append((self.wrap(f.start + 1), bytes(data)))
        return result

    def set_field_data(self, pos: int, data: bytes) -> None:
        """Client wrote into the field at/containing pos. Set MDT.
        Clamps to field data_length — never overruns into next field."""
        f = self.field_at(pos)
        if f is None:
            return  # unformatted screen — just write at pos
        max_len = f.data_length
        p = self.wrap(f.start + 1)
        for i, b in enumerate(data):
            if i >= max_len:
                break
            self.cells[p] = Cell(char=b)
            p = self.wrap(p + 1)
        f.attrs.mdt = True

    def reset_mdt(self) -> None:
        for f in self.fields:
            f.attrs.mdt = False
