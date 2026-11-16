#!/bin/bash
# textfox installation script
set -o pipefail

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
  if [[ "${fp}" == *$'\n'* || "${fp}" == *$'\r'* ]]; then
    echo '[!!] Profile paths cannot contain line breaks' >&2
    return 1
  fi
  fp="${fp/#\~/$HOME}"
  [[ "${fp}" == / ]] || fp="${fp%/}"
  printf '%s\n' "$fp"
}

LOGO_LINE='\__/\___/_/|_|\__/_/  \____/_/|_|'

# Is textfox already installed here? Since the preference block exists, its
# markers say so; installs from before it left only the logo at the top of
# userChrome.css.
textfox_installed() {
  local fp="$1"
  grep -qFx -e "$PREF_BEGIN" -e "$PREF_END" "${fp}/user.js" 2>/dev/null && return 0
  grep -qF -- "$LOGO_LINE" "${fp}/chrome/userChrome.css" 2>/dev/null
}

# Whose is the profile's copy of a name textfox ships? Each file is judged on
# its own, so a userChrome.css the user wrote over a textfox install still counts
# as the user's. A copy identical to this checkout's is textfox's. The two
# entry sheets carry the textfox logo in every textfox version. Any other name
# follows the profile: textfox's when textfox is installed.
owned_by_textfox() {
  local fp="$1" name="$2" profile_installed="$3"
  local target="${fp}/chrome/${name}"
  if [[ -f "${target}" ]] && cmp -s "chrome/${name}" "${target}"; then return 0; fi
  if [[ -d "${target}" ]] && diff -rq "chrome/${name}" "${target}" >/dev/null 2>&1; then return 0; fi
  case "${name}" in
  userChrome.css | userContent.css)
    grep -qF -- "$LOGO_LINE" "${target}" 2>/dev/null
    ;;
  *)
    [[ "${profile_installed}" == "yes" ]]
    ;;
  esac
}

# A fresh backup directory for one kind. Two installs in one second must not
# merge into one backup.
new_backup_dir() {
  local fp="$1" kind="$2" stamp n=0 dir
  stamp="$(date +%Y%m%d_%H%M%S)"
  dir="${fp}/chrome-${kind}-${stamp}.bak"
  while [[ -e "${dir}" ]]; do
    n=$((n + 1))
    dir="${fp}/chrome-${kind}-${stamp}_${n}.bak"
  done
  mkdir -p "${dir}" || return 1
  echo "${dir}"
}

# Only the files textfox ships are moved aside. Moving the whole chrome directory
# would take chrome/config.css with it, and that file belongs to the user: a
# reinstall must leave the customization in place.
# The backup's name says whose files it holds: chrome-pre-textfox-* holds the
# user's files, chrome-textfox-* the previous textfox files. Uninstall offers
# only the first kind.
backup_existing_profile() {
  local fp="$1"
  local mine="" theirs="" entry name installed=no
  if textfox_installed "${fp}"; then installed=yes; fi
  for entry in chrome/* chrome/.[!.]* chrome/..?*; do
    [[ -e "${entry}" || -L "${entry}" ]] || continue
    name="${entry##*/}"
    [[ "${name}" == config.css ]] && continue
    [[ -e "${fp}/chrome/${name}" || -L "${fp}/chrome/${name}" ]] || continue
    if owned_by_textfox "${fp}" "${name}" "${installed}"; then
      if [[ -z "${theirs}" ]]; then
        theirs="$(new_backup_dir "${fp}" textfox)" || return 1
        echo "[!!] Backing up the previous textfox files this install replaces to ${theirs}"
      fi
      mv -v "${fp}/chrome/${name}" "${theirs}/${name}" || return 1
    else
      if [[ -z "${mine}" ]]; then
        mine="$(new_backup_dir "${fp}" pre-textfox)" || return 1
        echo "[!!] Backing up your files this install replaces to ${mine}"
      fi
      mv -v "${fp}/chrome/${name}" "${mine}/${name}" || return 1
    fi
  done
}

copy_chrome() {
  local fp="$1"
  if [[ -d "${fp}" ]]; then
    echo "Copying textfox/chrome/ -> ${fp}/chrome/"
    # Merge into the existing directory rather than copying the directory itself,
    # which would create chrome/chrome on a reinstall.
    mkdir -p "${fp}/chrome" || return 1
    local entry
    for entry in chrome/* chrome/.[!.]* chrome/..?*; do
      [[ -e "${entry}" || -L "${entry}" ]] || continue
      [[ "${entry##*/}" == config.css ]] && continue
      cp -R "${entry}" "${fp}/chrome/" || return 1
    done
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

# Merge the shipped preference block into the profile's user.js. Bytes outside
# the markers are preserved, a previous textfox block is replaced, and the old
# file is backed up only when the content actually changes.
merge_user_js() {
  local fp="$1"
  local target="${fp}/user.js" block tmp begins ends

  block="$(extract_pref_block)" || return 1
  if [[ -z "${block}" ]]; then
    echo "[!!] shipped user.js has no preference block; nothing merged" >&2
    return 1
  fi

  tmp="$(mktemp "${target}.textfox-XXXXXX")" || return 1
  if [[ -f "${target}" ]]; then
    begins="$(marker_count "${target}" "$PREF_BEGIN")"
    ends="$(marker_count "${target}" "$PREF_END")"
    if [[ "${begins}" != "${ends}" ]] || [[ "${begins}" -gt 1 ]] ||
      ! markers_ordered "${target}"; then
      rm -f "${tmp}"
      echo "[!!] ${target} has unbalanced, repeated, or reversed textfox markers; leaving it untouched" >&2
      return 1
    fi
    if ! strip_pref_block "${target}" | drop_trailing_blanks > "${tmp}"; then
      rm -f "${tmp}"
      return 1
    fi
  fi

  # One blank line between the user's text and the managed block.
  if [[ -s "${tmp}" ]]; then
    printf '\n' >> "${tmp}" || { rm -f "${tmp}"; return 1; }
  fi
  printf '%s\n' "${block}" >> "${tmp}" || { rm -f "${tmp}"; return 1; }

  if [[ -f "${target}" ]] && cmp -s "${tmp}" "${target}"; then
    rm -f "${tmp}"
    echo "textfox preferences already current in ${target}"
    return 0
  fi

  if [[ -f "${target}" ]]; then
    local bak="${target}-$(date +%Y%m%d_%H%M%S).bak"
    cp -v "${target}" "${bak}" || { rm -f "${tmp}"; return 1; }
    echo "[!!] Previous user.js kept at ${bak}"
  fi
  mv -f "${tmp}" "${target}" || { rm -f "${tmp}"; return 1; }
  echo "Merged textfox preferences into ${target}"
}

# Earlier textfox installs assigned browser.nova.enabled false, and Firefox kept
# that in prefs.js after the assignment left user.js. tools/clear-nova-pref.py
# removes it once (marker textfox.nova.cleared). It needs Python 3.8 or newer.
nova_python() {
  command -v python3 >/dev/null 2>&1 &&
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null
}

nova_manual_step() {
  echo "[!!] To give Firefox its default Nova setting back, open about:config in this"
  echo "     profile and reset browser.nova.enabled (or install python3 and run"
  echo "     bash tf-install.sh again)."
}

install_profile() {
  local ffpp="$1"
  echo "Using Firefox Profile @ ${ffpp}"
  # Decided before anything moves: the backup takes the logo-bearing CSS away.
  local clear_nova=no
  if textfox_installed "${ffpp}"; then clear_nova=yes; fi
  if [[ "${clear_nova}" == "yes" ]] && nova_python; then
    local lock_status=0
    python3 tools/clear-nova-pref.py --check-lock "${ffpp}" || lock_status=$?
    if [[ "${lock_status}" -eq 75 ]]; then
      echo "[!!] Installation aborted before any change: close Firefox, then run tf-install.sh again" >&2
      return 1
    elif [[ "${lock_status}" -ne 0 ]]; then
      # The tool printed why: for example a directory with no Firefox profile files,
      # or a Python without fcntl (Windows). Install anyway; the user resets by hand.
      echo "[!!] Skipping the browser.nova.enabled reset for this profile"
      nova_manual_step
      clear_nova=no
    fi
  fi
  backup_existing_profile "${ffpp}" || return 1
  copy_chrome "${ffpp}" || return 1
  if ! merge_user_js "${ffpp}"; then
    echo "[!!] Installation aborted: the preference block was not merged" >&2
    return 1
  fi
  if [[ "${clear_nova}" == "yes" ]]; then
    if ! nova_python; then
      nova_manual_step
    elif ! python3 tools/clear-nova-pref.py "${ffpp}"; then
      echo "[!!] textfox is installed, but browser.nova.enabled was not reset." >&2
      echo "     Close Firefox and run tf-install.sh again." >&2
      nova_manual_step
      return 1
    fi
  fi
}

usage() {
  printf '%s\n' \
    'Usage: bash tf-install.sh [--profile PATH | PATH] [--font-family FAMILY] [--font-size SIZE]' \
    '       bash tf-install.sh --help' \
    'Without a profile path, prompts interactively. Use -- before a positional path starting with -.' \
    'Font flags require Python 3.8 or newer. Other config.css bytes stay unchanged.'
}

tf_install() {
  local fp="" profile_source=interactive family_given=no size_given=no script
  local font_args=()
  if [[ "$#" -eq 1 && "$1" == --help ]]; then usage; return 0; fi
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
    --profile | --font-family | --font-size)
      if [[ "$#" -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "[!!] $1 requires a nonempty value" >&2
        return 2
      fi
      case "$1" in
      --profile)
        [[ "${profile_source}" == interactive ]] || { echo '[!!] Profile specified more than once' >&2; return 2; }
        profile_source=named
        fp="$2"
        ;;
      --font-family)
        [[ "${family_given}" == no ]] || { echo '[!!] Repeated --font-family' >&2; return 2; }
        family_given=yes
        font_args+=("--font-family=$2")
        ;;
      --font-size)
        [[ "${size_given}" == no ]] || { echo '[!!] Repeated --font-size' >&2; return 2; }
        size_given=yes
        font_args+=("--font-size=$2")
        ;;
      esac
      shift 2
      ;;
    --)
      shift
      if [[ "$#" -ne 1 || "${profile_source}" != interactive || -z "$1" ]]; then
        echo '[!!] Expected one unambiguous profile path after --' >&2
        return 2
      fi
      fp="$1"
      profile_source=positional
      shift
      ;;
    -*) echo "[!!] Unknown option $1. Use --help." >&2; return 2 ;;
    *)
      [[ "${profile_source}" == interactive && -n "$1" ]] || { echo '[!!] Expected one profile path' >&2; return 2; }
      profile_source=positional
      fp="$1"
      shift
      ;;
    esac
  done
  if [[ "${#font_args[@]}" -gt 0 ]] && ! nova_python; then
    echo '[!!] Font flags require Python 3.8 or newer. Install without font flags to leave config.css untouched.' >&2
    return 1
  fi
  print_logo
  printf '\nInstalling textfox...\n'
  fp="$(clean_path "${fp}")" || return 1
  if [[ "${profile_source}" == positional && "${#font_args[@]}" -eq 0 && ! -d "${fp}" ]]; then
    echo "[!!] Directory ${fp} does not exist" >&2
    profile_source=interactive
  fi
  if [[ "${profile_source}" == interactive ]]; then
    while :; do
      read -rp 'Path to Firefox profile: ' fp || return 1
      fp="$(clean_path "${fp}")" || return 1
      [[ -d "${fp}" ]] && break
      echo "[!!] Directory ${fp} does not exist" >&2
    done
  fi
  if [[ ! -d "${fp}" ]]; then
    echo "[!!] Directory ${fp} does not exist" >&2
    return 1
  fi
  fp="$(cd -- "${fp}" && pwd -P)" || return 1
  if [[ "${#font_args[@]}" -gt 0 ]]; then
    script="${BASH_SOURCE[0]}"
    [[ "${script}" == /* ]] || script="${PWD}/${script}"
    python3 tools/config-fonts.py "${font_args[@]}" "${fp}/chrome/config.css" \
      "${BASH}" -c 'source "$1"; install_profile "$2"' textfox-install "${script}" "${fp}" || return 1
  else
    install_profile "${fp}" || return 1
  fi
  printf '✓ Installation completed\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  tf_install "$@"
  result=$?
  if [[ "${result}" -eq 2 ]]; then usage >&2; fi
  exit "${result}"
fi
