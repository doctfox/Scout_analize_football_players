#!/bin/sh
# Пароль управляющего порта берётся из переменной окружения и хэшируется при старте
set -e
PASS="${TOR_CONTROL_PASSWORD:-scout}"
HASH=$(tor --quiet --hash-password "$PASS" | tail -n 1)
exec tor -f /etc/tor/torrc --HashedControlPassword "$HASH"
