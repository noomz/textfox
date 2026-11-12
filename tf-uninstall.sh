#!/bin/bash
# textfox uninstallation script

print_logo() {
  cat <<EOF
   __            __  ____          
  / /____  _  __/ /_/ __/___  _  __
 / __/ _ \| |/_/ __/ /_/ __ \| |/_/
/ /_/  __/>  </ /_/ __/ /_/ />  <  
\__/\___/_/|_|\__/_/  \____/_/|_|  
EOF
}

# textfox's preferences live between these markers in user.js. Uninstalling
# removes only that block: text outside it belongs to the user.
PREF_BEGIN='// >>> textfox managed prefs >>>'
PREF_END='// <<< textfox managed prefs <<<'

clean_path() {
  local fp="$1"
  fp="${fp/#\~/$HOME}" # Expand ~ to $HOME
  fp="${fp%/}"         # Remove trailing slash if exists
  fp="${fp/\/\//\/}"   # Remove double slashes
  fp="${fp//\'/}"      # Remove single quotes
  fp="${fp//\"/}"      # Remove double quotes
  echo "$fp"
}

remove_chrome() {
  local fp="$1"
  fp="$(clean_path "${fp}")"
  if [[ -d "${fp}/chrome" ]]; then
    echo "Removing ${fp}/chrome/"
    rm -rf "${fp}/chrome"
    echo "✓ chrome directory removed"
  else
    echo "No chrome directory found at ${fp}/chrome, skipping."
  fi
}

strip_pref_block() {
  awk -v b="$PREF_BEGIN" -v e="$PREF_END" '
    $0 == b { inside = 1; next }
    $0 == e { inside = 0; next }
    !inside { print }
  ' "$1"
}

drop_trailing_blanks() {
  awk '
    /^[[:space:]]*$/ { held++; next }
    { while (held > 0) { print ""; held-- } print }
  ' "$1"
}

marker_count() {
  grep -cFx -- "$2" "$1" 2>/dev/null || true
}

# Remove the managed block from user.js, keeping the user's own preferences. A
# file that held nothing else is deleted; anything else is left in place.
remove_textfox_prefs() {
  local fp="$1"
  fp="$(clean_path "${fp}")"
  local target="${fp}/user.js" tmp begins ends bak

  if [[ ! -f "${target}" ]]; then
    echo "No user.js found at ${target}, skipping."
    return 0
  fi

  begins="$(marker_count "${target}" "$PREF_BEGIN")"
  ends="$(marker_count "${target}" "$PREF_END")"
  if [[ "${begins}" -eq 0 ]] && [[ "${ends}" -eq 0 ]]; then
    echo "No textfox preferences in ${target}; leaving it untouched."
    return 0
  fi
  if [[ "${begins}" != "${ends}" ]] || [[ "${begins}" -gt 1 ]]; then
    echo "[!!] ${target} has unbalanced or repeated textfox markers; leaving it untouched" >&2
    return 1
  fi

  tmp="$(mktemp "${target}.textfox-XXXXXX")" || return 1
  strip_pref_block "${target}" | drop_trailing_blanks > "${tmp}"
  bak="${target}-$(date +%Y%m%d_%H%M%S).bak"
  cp -v "${target}" "${bak}"
  echo "[!!] Previous user.js kept at ${bak}"

  if [[ -s "${tmp}" ]]; then
    mv -f "${tmp}" "${target}"
    echo "✓ textfox preferences removed from ${target}"
  else
    rm -f "${tmp}" "${target}"
    echo "✓ ${target} removed; it held only textfox preferences"
  fi
}

restore_backup() {
  local fp="$1"
  fp="$(clean_path "${fp}")"
  local latest_backup
  latest_backup="$(find "${fp}" -maxdepth 1 -name 'chrome-*.bak' -type d 2>/dev/null | sort -r | head -n 1)"
  if [[ -n "${latest_backup}" ]]; then
    read -rp "Backup found: ${latest_backup}. Restore it? (Y/N): " do_restore
    case "$do_restore" in
    [Yy]*)
      mv -v "${latest_backup}" "${fp}/chrome"
      echo "✓ Backup restored to ${fp}/chrome"
      ;;
    *)
      echo "Skipping backup restore."
      ;;
    esac
  else
    echo "No textfox backup found in ${fp}."
  fi
}

tf_uninstall() {
  printf "\nUninstalling textfox...\n"
  local fp
  if [[ "$#" -eq 1 ]]; then
    fp="$(clean_path "$1")"
  else
    read -rp "Path to Firefox profile: " fp
    fp="$(clean_path "${fp}")"
  fi

  local ffpp="${fp}"
  if [[ ! -d "${ffpp}" ]]; then
    echo "[!!] Directory ${ffpp} does not exist"
    while [[ ! -d "${ffpp}" ]]; do
      read -rp "Path to Firefox profile: " fp
      ffpp="$(clean_path "${fp}")"
    done
  fi

  echo "Using Firefox Profile @ ${ffpp}"
  remove_chrome "${ffpp}"
  remove_textfox_prefs "${ffpp}"
  restore_backup "${ffpp}"
  printf "✓ Uninstallation completed\n"
}

print_logo
tf_uninstall "$@"
