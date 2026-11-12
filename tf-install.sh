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

install_user_js() {
  local fp="$1"
  fp="$(clean_path "${fp}")"

  # Optionally install user.js
  read -rp "Do you want to install the user.js file? (Y/N): " install_js

  case "$install_js" in
  [Yy]*)
    if [[ -f "$fp/user.js" ]]; then
      local bak="$fp/user.js-$(date +%Y%m%d_%H%M%S).bak"
      cp -v "$fp/user.js" "$bak"
      echo "[!!] Existing user.js kept at ${bak}"
    fi
    cp -v "user.js" "$fp/user.js"
    ;;
  *)
    echo "Skipping user.js installation."
    ;;
  esac
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
  install_user_js "${ffpp}"
  printf "✓ Installation completed\n"
}

print_logo
tf_install "$@"
