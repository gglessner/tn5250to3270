"""RFC 854 + extensions. Hex values, not decimal — wire dumps are hex."""

# Command bytes (always preceded by IAC)
IAC  = 0xFF
SE   = 0xF0   # Subnegotiation End
NOP  = 0xF1
DM   = 0xF2   # Data Mark
BRK  = 0xF3   # Break
IP   = 0xF4   # Interrupt Process
AO   = 0xF5   # Abort Output
AYT  = 0xF6   # Are You There
EC   = 0xF7   # Erase Character
EL   = 0xF8   # Erase Line
GA   = 0xF9   # Go Ahead
SB   = 0xFA   # Subnegotiation Begin
WILL = 0xFB
WONT = 0xFC
DO   = 0xFD
DONT = 0xFE
EOR_CMD = 0xEF  # End Of Record (this IS the EOR mark, sent as IAC EF)

# Option numbers
OPT_BINARY        = 0     # RFC 856
OPT_ECHO          = 1
OPT_SGA           = 3     # Suppress Go Ahead
OPT_TTYPE         = 24    # Terminal Type, RFC 1091
OPT_EOR           = 25    # End Of Record, RFC 885
OPT_NAWS          = 31    # Window Size
OPT_NEW_ENVIRON   = 39    # RFC 1572 — TN5250 device naming
OPT_TN3270E       = 40    # RFC 2355

# TTYPE subnegotiation
TTYPE_IS   = 0
TTYPE_SEND = 1

# NEW-ENVIRON subnegotiation
NE_IS   = 0
NE_SEND = 1
NE_INFO = 2
NE_VAR  = 0
NE_VALUE = 1
NE_ESC  = 2
NE_USERVAR = 3
