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

# The files textfox ships, as listed in this checkout's chrome/ directory.
SOURCE_CHROME="$(cd "$(dirname "$0")" && pwd)/chrome"

# Only the files textfox ships are removed. Removing the whole chrome directory
# would take chrome/config.css and anything else the user put there with it.
# Directories are removed only once they are empty.
remove_chrome() {
  local fp="$1"
  fp="$(clean_path "${fp}")"
  local chrome="${fp}/chrome" rel
  if [[ ! -d "${chrome}" ]]; then
    echo "No chrome directory found at ${chrome}, skipping."
    return 0
  fi
  if [[ ! -d "${SOURCE_CHROME}" ]]; then
    echo "[!!] ${SOURCE_CHROME} not found; cannot tell textfox's files from yours" >&2
    return 1
  fi

  # config.css is never textfox's, even when a copy sits in this checkout.
  # Pipes, not process substitution: macOS runs `sh` as bash 3.2 in POSIX mode.
  echo "Removing textfox's files from ${chrome}/"
  (cd "${SOURCE_CHROME}" && find . -type f) | sed 's#^\./##' | grep -vx 'config.css' |
    while IFS= read -r rel; do
      rm -f "${chrome}/${rel}"
    done
  (cd "${SOURCE_CHROME}" && find . -mindepth 1 -type d) | sed 's#^\./##' | sort -r |
    while IFS= read -r rel; do
      rmdir "${chrome}/${rel}" 2>/dev/null || true
    done

  if rmdir "${chrome}" 2>/dev/null; then
    echo "✓ chrome directory removed; it held only textfox's files"
  else
    echo "✓ textfox's files removed; your own files in ${chrome}/ were kept"
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

# A managed block must open before it closes. With the markers reversed the
# strip would drop every line after the opening marker, so refuse instead.
markers_ordered() {
  awk -v b="$PREF_BEGIN" -v e="$PREF_END" '
    $0 == b { if (seen_end) bad = 1; seen_begin = 1 }
    $0 == e { if (!seen_begin) bad = 1; seen_end = 1 }
    END { exit bad }
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
  if [[ "${begins}" != "${ends}" ]] || [[ "${begins}" -gt 1 ]] ||
    ! markers_ordered "${target}"; then
    echo "[!!] ${target} has unbalanced, repeated, or reversed textfox markers; leaving it untouched" >&2
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
  # Offer the user's files from before textfox, never a backup of textfox's own
  # previous files (chrome-textfox-*), which would put textfox back. The newest
  # pre-textfox backup belongs to the current install; C collation keeps a
  # same-second _N suffix after its unsuffixed sibling. Older installers named
  # every backup chrome-<stamp>.bak; the oldest of those is the best guess.
  local latest_backup
  latest_backup="$(find "${fp}" -maxdepth 1 -name 'chrome-pre-textfox-*.bak' -type d 2>/dev/null | LC_ALL=C sort -r | head -n 1)"
  if [[ -z "${latest_backup}" ]]; then
    latest_backup="$(find "${fp}" -maxdepth 1 -name 'chrome-[0-9]*.bak' -type d 2>/dev/null | LC_ALL=C sort | head -n 1)"
    if [[ -n "${latest_backup}" ]]; then
      echo "[!!] ${latest_backup} is from an older installer; it may hold textfox's own files rather than yours."
    fi
  fi
  if find "${fp}" -maxdepth 1 -name 'chrome-textfox-*.bak' -type d 2>/dev/null | grep -q .; then
    echo "Backups of textfox's previous files (chrome-textfox-*.bak) are left in ${fp}."
  fi
  if [[ -n "${latest_backup}" ]]; then
    read -rp "Backup found: ${latest_backup}. Restore it? (Y/N): " do_restore
    case "$do_restore" in
    [Yy]*)
      # The backup holds the files an install replaced. Put each back beside
      # whatever the user kept in chrome/, never over it.
      local entry name kept=0
      mkdir -p "${fp}/chrome"
      for entry in "${latest_backup}"/* "${latest_backup}"/.[!.]*; do
        [[ -e "${entry}" ]] || continue
        name="$(basename "${entry}")"
        if [[ -e "${fp}/chrome/${name}" ]]; then
          echo "[!!] ${fp}/chrome/${name} exists; leaving the backup copy in ${latest_backup}"
          kept=1
        else
          mv -v "${entry}" "${fp}/chrome/${name}"
        fi
      done
      if [[ "${kept}" -eq 0 ]]; then
        rmdir "${latest_backup}"
        echo "✓ Backup restored to ${fp}/chrome"
      else
        echo "[!!] Backup partly restored; the rest stays in ${latest_backup}"
      fi
      ;;
    *)
      echo "Skipping backup restore."
      ;;
    esac
  else
    echo "No backup of your pre-textfox files found in ${fp}."
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
