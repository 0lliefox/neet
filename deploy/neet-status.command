#!/bin/zsh
# Double-click in Finder to see the NEET capture health report (install_launchd.sh puts a link on the Desktop).
"$HOME/neet/.venv/bin/neet" capture --status
echo
read -k1 "?Press any key to close "
