#!/bin/sh
# Здоров, когда Tor полностью подключился к сети (bootstrap 100%)
PASS="${TOR_CONTROL_PASSWORD:-scout}"
printf 'AUTHENTICATE "%s"\r\nGETINFO status/bootstrap-phase\r\nQUIT\r\n' "$PASS" \
  | nc -w 5 127.0.0.1 9051 | grep -q 'PROGRESS=100'
