# Manual Interop Tests

These scenarios exercise paths the automated suite cannot reach: real
emulators, real TLS handshakes, real TN3270E hosts. Run them before
shipping any release that touches the negotiation or session layer.

## Hercules + tn5250j (basic TN3270, no TLS)

1. Start Hercules with MVS 3.8j, telnet on port 3270
2. `python -m tn5250to3270 -c config.yaml` (upstream: 127.0.0.1:3270)
3. tn5250j → 127.0.0.1:2323, term-type IBM-3179-2
4. Verify: TSO logon screen renders, can type userid, Enter works, PF3 exits

## Hercules + ACS (basic TN3270)

1. Same Hercules
2. ACS → 127.0.0.1:2323, Display size 24x80
3. Verify: same as above. ACS is stricter — any disconnect = bug.

## stunnel + Hercules + ACS (TLS)

1. stunnel wrapping Hercules port 3270 → 992 with self-signed cert
2. config.yaml: tls.enabled=true, tls.verify=false, port=992
3. ACS → 127.0.0.1:2323
4. Verify: connects, screen renders. Check log for "TLS" line.

## TN3270E (if you have a real z/OS or TK4-/TK5 with E-mode)

1. config.yaml: upstream = your z/OS LPAR
2. ACS → proxy
3. Verify log shows "host accepted TN3270E", LU name assigned
4. Test SYSREQ if granted: ACS Attn key → check log for SYSREQ path
