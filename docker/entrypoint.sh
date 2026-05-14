#!/usr/bin/env sh
set -eu

if [ "${AISTUDIO_ENABLE_LOGIN_DESKTOP:-0}" = "1" ]; then
  export DISPLAY="${DISPLAY:-:99}"
  export AISTUDIO_CAMOUFOX_HEADLESS="${AISTUDIO_CAMOUFOX_HEADLESS:-0}"
  VNC_GEOMETRY="${AISTUDIO_LOGIN_DESKTOP_GEOMETRY:-1600x900x24}"
  VNC_PASSWORD="${AISTUDIO_LOGIN_VNC_PASSWORD:-}"
  NOVNC_PORT="${AISTUDIO_LOGIN_NOVNC_PORT:-6080}"
  NOVNC_BIND="${AISTUDIO_LOGIN_NOVNC_BIND:-127.0.0.1:6080}"
  BIND_HOST="${NOVNC_BIND%:*}"

  if [ -z "$VNC_PASSWORD" ] && [ "$BIND_HOST" != "127.0.0.1" ] && [ "$BIND_HOST" != "localhost" ]; then
    echo "AISTUDIO_LOGIN_VNC_PASSWORD is required when noVNC is not bound to localhost." >&2
    echo "Set AISTUDIO_LOGIN_VNC_PASSWORD, or bind AISTUDIO_LOGIN_NOVNC_BIND to 127.0.0.1 and expose it through VPN/reverse proxy auth." >&2
    exit 1
  fi

  Xvfb "$DISPLAY" -screen 0 "$VNC_GEOMETRY" -nolisten tcp &
  XVFB_PID="$!"

  DISPLAY_NUM="${DISPLAY#*:}"
  DISPLAY_NUM="${DISPLAY_NUM%%.*}"
  DISPLAY_SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM}"
  DISPLAY_READY=0
  for _ in $(seq 1 50); do
    if [ -S "$DISPLAY_SOCKET" ]; then
      DISPLAY_READY=1
      break
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
      echo "Xvfb exited before display $DISPLAY became ready." >&2
      exit 1
    fi
    sleep 0.2
  done
  if [ "$DISPLAY_READY" != "1" ]; then
    echo "Xvfb display $DISPLAY did not become ready." >&2
    exit 1
  fi

  # Clipboard sync is optional; never let it block API startup.
  if command -v autocutsel >/dev/null 2>&1; then
    autocutsel -selection CLIPBOARD >/tmp/autocutsel-clipboard.log 2>&1 &
    autocutsel -selection PRIMARY >/tmp/autocutsel-primary.log 2>&1 &
  fi

  if [ -n "$VNC_PASSWORD" ]; then
    mkdir -p /root/.vnc
    x11vnc -storepasswd "$VNC_PASSWORD" /root/.vnc/passwd >/dev/null
    x11vnc -display "$DISPLAY" -rfbauth /root/.vnc/passwd -forever -shared -localhost -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
  else
    x11vnc -display "$DISPLAY" -forever -shared -localhost -nopw -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
  fi
  X11VNC_PID="$!"

  websockify --web=/usr/share/novnc/ "$NOVNC_PORT" localhost:5900 >/tmp/novnc.log 2>&1 &
  WEBSOCKIFY_PID="$!"

  sleep 1
  for pid in "$XVFB_PID" "$X11VNC_PID" "$WEBSOCKIFY_PID"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Login desktop startup failed; process $pid exited." >&2
      echo "x11vnc log:" >&2
      cat /tmp/x11vnc.log >&2 2>/dev/null || true
      echo "noVNC log:" >&2
      cat /tmp/novnc.log >&2 2>/dev/null || true
      exit 1
    fi
  done

  if ! python - "$NOVNC_PORT" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 10
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            sys.exit(0)
    except OSError:
        time.sleep(0.5)
sys.exit(1)
PY
  then
    echo "noVNC did not start listening on 127.0.0.1:$NOVNC_PORT." >&2
    echo "x11vnc log:" >&2
    cat /tmp/x11vnc.log >&2 2>/dev/null || true
    echo "noVNC log:" >&2
    cat /tmp/novnc.log >&2 2>/dev/null || true
    exit 1
  fi
fi

exec "$@"
