#!/bin/bash
# textfox installation script

print_logo() {
  cat <<EOF
   __            __  ____          
  / /____  _  __/ /_/ __/___  _  __
 / __/ _ \| |/_/ __/ /_/ __ \| |/_/
/ /_/  __/>  </ /_/ __/ /_/ />  <  
\__/\___/_/|_|\__/_/  \____/_/|_|  
EOF
}

# The shipped user.js carries textfox's preferences inside these markers. An
# install merges that block into the profile's user.js instead of replacing the
# file, so preferences the user wrote there keep applying.
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

# Only the files textfox ships are moved aside. Moving the whole chrome directory
# would take chrome/config.css with it, and that file belongs to the user: a
# reinstall must leave the customization in place.
backup_existing_profile() {
  local fp="$1"
  fp="$(clean_path "${fp}")"
  local backup="" entry name
  for entry in chrome/*; do
    name="$(basename "${entry}")"
    [[ -e "${fp}/chrome/${name}" ]] || continue
    if [[ -z "${backup}" ]]; then
      backup="${fp}/chrome-$(date +%Y%m%d_%H%M%S).bak"
      mkdir -p "${backup}"
      echo "[!!] Backing up the textfox files this install replaces to ${backup}"
    fi
    mv -v "${fp}/chrome/${name}" "${backup}/${name}"
  done
}

copy_chrome() {
  local fp="$1"
  fp="$(clean_path "${fp}")"
  if [[ -d "${fp}" ]]; then
    echo "Copying textfox/chrome/ -> ${fp}/chrome/"
    # Merge into the existing directory rather than copying the directory itself,
    # which would create chrome/chrome on a reinstall.
    mkdir -p "${fp}/chrome"
    cp -R "chrome/." "${fp}/chrome/"
  else
    echo "The specified Firefox profile path does not exist: ${fp}"
    return 1
  fi
}

extract_pref_block() {
  awk -v b="$PREF_BEGIN" -v e="$PREF_END" '
    $0 == b { inside = 1 }
    inside { print }
    inside && $0 == e { exit }
  ' user.js
}

strip_pref_block() {
  awk -v b="$PREF_BEGIN" -v e="$PREF_END" '
    $0 == b { inside = 1; next }
    $0 == e { inside = 0; next }
    !inside { print }
  ' "$1"
}

# Trailing blank lines are dropped so a reinstall composes the same bytes and
# writes nothing; blank lines inside the user's own text are kept.
drop_trailing_blanks() {
  awk '
    /^[[:space:]]*$/ { held++; next }
    { while (held > 0) { print ""; held-- } print }
  ' "$1"
}

marker_count() {
  grep -cFx -- "$2" "$1" 2>/dev/null || true
}

# Merge the shipped preference block into the profile's user.js. Bytes outside
# the markers are preserved, a previous textfox block is replaced, and the old
# file is backed up only when the content actually changes.
merge_user_js() {
  local fp="$1"
  fp="$(clean_path "${fp}")"
  local target="${fp}/user.js" block tmp begins ends

  block="$(extract_pref_block)"
  if [[ -z "${block}" ]]; then
    echo "[!!] shipped user.js has no preference block; nothing merged" >&2
    return 1
  fi

  tmp="$(mktemp "${target}.textfox-XXXXXX")" || return 1
  if [[ -f "${target}" ]]; then
    begins="$(marker_count "${target}" "$PREF_BEGIN")"
    ends="$(marker_count "${target}" "$PREF_END")"
    if [[ "${begins}" != "${ends}" ]] || [[ "${begins}" -gt 1 ]]; then
      rm -f "${tmp}"
      echo "[!!] ${target} has unbalanced or repeated textfox markers; leaving it untouched" >&2
      return 1
    fi
    strip_pref_block "${target}" | drop_trailing_blanks > "${tmp}"
  fi

  # One blank line between the user's text and the managed block.
  if [[ -s "${tmp}" ]]; then printf '\n' >> "${tmp}"; fi
  printf '%s\n' "${block}" >> "${tmp}"

  if [[ -f "${target}" ]] && cmp -s "${tmp}" "${target}"; then
    rm -f "${tmp}"
    echo "textfox preferences already current in ${target}"
    return 0
  fi

  if [[ -f "${target}" ]]; then
    local bak="${target}-$(date +%Y%m%d_%H%M%S).bak"
    cp -v "${target}" "${bak}"
    echo "[!!] Previous user.js kept at ${bak}"
  fi
  mv -f "${tmp}" "${target}"
  echo "Merged textfox preferences into ${target}"
}

tf_install() {
  printf "\nInstalling textfox...\n"
  local fp
  if [[ "$#" -eq 1 ]]; then
    fp="$(clean_path "$1")"
  else
    read -rp "Path to Firefox profile: " fp
    fp="$(clean_path "${fp}")"
  fi

  ffpp="$(clean_path "${fp}")"
  if [[ ! -d "${ffpp}" ]]; then
    echo "[!!] Directory ${ffpp} does not exist"
    while [[ ! -d "${ffpp}" ]]; do
      read -rp "Path to Firefox profile: " fp
      ffpp="$(clean_path "${fp}")"
    done
  fi

  echo "Using Firefox Profile @ ${ffpp}"
  backup_existing_profile "${ffpp}"
  copy_chrome "${ffpp}"
  if ! merge_user_js "${ffpp}"; then
    echo "[!!] Installation aborted: the preference block was not merged" >&2
    return 1
  fi
  printf "✓ Installation completed\n"
}

print_logo
tf_install "$@"
