#!/bin/bash
# Install the daily paper-trade logging cron.
#
# This script appends a cron entry that runs the mavam paper log
# command at 4:05 PM ET (just after US market close) on weekdays.
# It uses the system crontab. macOS launchd is not used here for
# simplicity; launchd is recommended for production but crontab
# works fine for development.
#
# Run:  ./scripts/install_papertrade_cron.sh
# Remove:  ./scripts/install_papertrade_cron.sh --uninstall

set -e

CRON_LINE='5 16 * * 1-5 cd /Users/jcforever1/.mavis/agents/mavis/workspace/rudra-intraday-engine/agent-harness && /opt/homebrew/bin/mavam paper log examples/configs/spy-yfinance.toml >> /Users/jcforever1/.local/state/mavam/paperlog/cron.log 2>&1'

if [[ "$1" == "--uninstall" ]]; then
    crontab -l 2>/dev/null | grep -v "mavam paper log" | crontab -
    echo "paper-trade cron removed"
    exit 0
fi

# Add the cron line if not already present
if crontab -l 2>/dev/null | grep -q "mavam paper log"; then
    echo "paper-trade cron already installed; use --uninstall to remove"
    exit 0
fi

( crontab -l 2>/dev/null || true; echo "$CRON_LINE" ) | crontab -
echo "paper-trade cron installed:"
echo "  $CRON_LINE"
echo
echo "Logs: /Users/jcforever1/.local/state/mavam/paperlog/cron.log"
echo
echo "Verify with: crontab -l"
