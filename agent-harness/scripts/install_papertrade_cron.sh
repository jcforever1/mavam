#!/bin/bash
# Install the daily paper-trade logging job.
#
# On macOS this installs a launchd LaunchAgent (com.mavam.papertrade)
# that runs `mavam paper log` for SPY and KO at 16:05 ET on weekdays
# (just after US market close), independently and idempotently.
#
# launchd is preferred over crontab on macOS because:
#   * crontab is a wedged system daemon on some macOS versions — jobs
#     silently stop firing while `cron` keeps running (observed 2026-08-13).
#   * launchd survives sleep/wake and login, and catches up missed runs.
#   * the agent runs in the user GUI session with the right PATH.
#
# For non-macOS (Linux), pass --cron to install a crontab entry instead.
#
# Run:       ./scripts/install_papertrade_cron.sh
# Uninstall: ./scripts/install_papertrade_cron.sh --uninstall
# (cron mode: pass --cron / --cron --uninstall)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$HERE/com.mavam.papertrade.plist"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
PLIST_DST="$LAUNCH_DIR/com.mavam.papertrade.plist"
LABEL="com.mavam.papertrade"
UID_N="$(id -u)"
LOG="/Users/jcforever1/.local/state/mavam/paperlog/cron.log"

CRON_LINES=(
    '5 16 * * 1-5 cd /Users/jcforever1/.mavis/agents/mavis/workspace/rudra-intraday-engine/agent-harness && YF_FETCH_RETRIES=8 YF_FETCH_BACKOFF_SECONDS=5 /opt/homebrew/bin/mavam paper log examples/configs/spy-yfinance.toml >> /Users/jcforever1/.local/state/mavam/paperlog/cron.log 2>&1'
    '5 16 * * 1-5 cd /Users/jcforever1/.mavis/agents/mavis/workspace/rudra-intraday-engine/agent-harness && YF_FETCH_RETRIES=8 YF_FETCH_BACKOFF_SECONDS=5 /opt/homebrew/bin/mavam paper log examples/configs/ko-yfinance.toml >> /Users/jcforever1/.local/state/mavam/paperlog/cron.log 2>&1'
)

cron_uninstall() {
    crontab -l 2>/dev/null | grep -v "mavam paper log" | crontab - || true
    echo "paper-trade crontab entries removed"
}

cron_install() {
    existing=$(crontab -l 2>/dev/null || true)
    to_add=()
    for line in "${CRON_LINES[@]}"; do
        if ! echo "$existing" | grep -qF "$line"; then
            to_add+=("$line")
        fi
    done
    if [[ ${#to_add[@]} -eq 0 ]]; then
        echo "paper-trade crontab already installed; use --uninstall to remove"
        return
    fi
    (
        echo "$existing"
        printf '%s\n' "${to_add[@]}"
    ) | crontab -
    echo "paper-trade crontab installed:" >&2
    printf '  %s\n' "${to_add[@]}"
}

launchd_uninstall() {
    launchctl bootout "gui/$UID_N/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST_DST"
    echo "launchd agent $LABEL removed"
}

launchd_install() {
    if [[ ! -f "$PLIST_SRC" ]]; then
        echo "error: plist template not found at $PLIST_SRC" >&2
        exit 1
    fi
    mkdir -p "$LAUNCH_DIR"
    /usr/bin/plutil -lint "$PLIST_SRC" >/dev/null
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl bootout "gui/$UID_N/$LABEL" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$UID_N" "$PLIST_DST"
    launchctl enable "gui/$UID_N/$LABEL" >/dev/null 2>&1 || true
    echo "launchd agent installed: $LABEL"
    echo "  runs: 16:05 ET SPY + KO, Mon-Fri (independent, idempotent)"
    echo "  logs: $LOG"
    echo "  verify: launchctl list | grep $LABEL"
}

MODE="launchd"
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --cron)   MODE="cron" ;;
        --uninstall) UNINSTALL=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [[ "$(uname)" != "Darwin" && "$MODE" == "launchd" ]]; then
    echo "note: launchd is macOS-only; falling back to --cron" >&2
    MODE="cron"
fi

if [[ "$MODE" == "launchd" ]]; then
    # Always remove stale crontab lines so we never double-fire.
    cron_uninstall
    if [[ "$UNINSTALL" == "1" ]]; then
        launchd_uninstall
    else
        launchd_install
    fi
else
    if [[ "$UNINSTALL" == "1" ]]; then
        cron_uninstall
    else
        cron_install
    fi
fi