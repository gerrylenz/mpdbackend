#!/usr/bin/env bash
#
# Install mpdbackend server from the current working directory.
#
# Usage:
#   cd /path/to/server && ./install/install.sh
#   cd /path/to/server && ./install/install.sh /other/path
#   cd /path/to/server && ./install/install.sh --systemd
#
set -euo pipefail

SOURCE_DIR="$(pwd)"
DEFAULT_INSTALL_DIR="${SOURCE_DIR}"
INSTALL_SYSTEMD=0
INSTALL_DIR=""

for arg in "$@"; do
    case "${arg}" in
        --systemd)
            INSTALL_SYSTEMD=1
            ;;
        -h | --help)
            sed -n '2,10p' "$0"
            exit 0
            ;;
        *)
            if [[ -z "${INSTALL_DIR}" ]]; then
                INSTALL_DIR="${arg}"
            else
                echo "Unexpected argument: ${arg}" >&2
                exit 1
            fi
            ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-${DEFAULT_INSTALL_DIR}}"
mkdir -p "${INSTALL_DIR}"
INSTALL_DIR="$(cd "${INSTALL_DIR}" && pwd)"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

log() {
    printf '==> %s\n' "$*"
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command not found: $1" >&2
        exit 1
    fi
}

validate_source_dir() {
    local missing=0
    for file in mpdbackend.py mpdbackend_mqtt.py mpdbackend_http.py mpdbackend_cover.py channels.json.example systemd/mpdbackend.service systemd/mpdbackend.env.example install/install.sh install/requirements.txt web/index.html web/style.css web/app.js; do
        if [[ ! -f "${SOURCE_DIR}/${file}" ]]; then
            echo "Missing in source directory (${SOURCE_DIR}): ${file}" >&2
            missing=1
        fi
    done
    if [[ "${missing}" -ne 0 ]]; then
        echo "Run this script from the mpdbackend server directory (cd .../server)." >&2
        exit 1
    fi
}

copy_server_files() {
    log "Copy files to ${INSTALL_DIR}"
    mkdir -p "${INSTALL_DIR}/install" "${INSTALL_DIR}/systemd" "${INSTALL_DIR}/data/covers" "${INSTALL_DIR}/data/logos" "${INSTALL_DIR}/web"

    install -m 644 "${SOURCE_DIR}/mpdbackend.py" "${INSTALL_DIR}/"
    install -m 644 "${SOURCE_DIR}/mpdbackend_mqtt.py" "${INSTALL_DIR}/"
    install -m 644 "${SOURCE_DIR}/mpdbackend_http.py" "${INSTALL_DIR}/"
    install -m 644 "${SOURCE_DIR}/mpdbackend_cover.py" "${INSTALL_DIR}/"
    install -m 644 "${SOURCE_DIR}/web/index.html" "${INSTALL_DIR}/web/"
    install -m 644 "${SOURCE_DIR}/web/style.css" "${INSTALL_DIR}/web/"
    install -m 644 "${SOURCE_DIR}/web/app.js" "${INSTALL_DIR}/web/"
    install -m 644 "${SOURCE_DIR}/channels.json.example" "${INSTALL_DIR}/"

    install -m 755 "${SOURCE_DIR}/install/install.sh" "${INSTALL_DIR}/install/"
    install -m 644 "${SOURCE_DIR}/install/requirements.txt" "${INSTALL_DIR}/install/"

    if [[ -f "${SOURCE_DIR}/install/setup-venv.sh" ]]; then
        install -m 755 "${SOURCE_DIR}/install/setup-venv.sh" "${INSTALL_DIR}/install/"
    fi

    install -m 644 "${SOURCE_DIR}/systemd/mpdbackend.service" "${INSTALL_DIR}/systemd/"
    install -m 644 "${SOURCE_DIR}/systemd/mpdbackend.env.example" "${INSTALL_DIR}/systemd/"

    if [[ ! -f "${INSTALL_DIR}/channels.json" ]]; then
        install -m 644 "${SOURCE_DIR}/channels.json.example" "${INSTALL_DIR}/channels.json"
        log "Created ${INSTALL_DIR}/channels.json from example"
    else
        log "Keeping existing ${INSTALL_DIR}/channels.json"
    fi
}

write_local_env() {
    local env_file="${INSTALL_DIR}/mpdbackend.env"
    log "Write ${env_file}"
    sed \
        -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
        "${SOURCE_DIR}/systemd/mpdbackend.env.example" >"${env_file}"
    chmod 600 "${env_file}"
}

setup_python_venv() {
    log "Create virtualenv and install Python dependencies"
    require_command python3

    if [[ ! -d "${INSTALL_DIR}/venv" ]]; then
        python3 -m venv "${INSTALL_DIR}/venv"
    fi

    "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip
    "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/install/requirements.txt"

    log "Python packages installed:"
    "${INSTALL_DIR}/venv/bin/pip" show Pillow python-mpd2 paho-mqtt | sed -n 's/^Name: /  - /p'
}

install_systemd_unit() {
    local unit_src="${INSTALL_DIR}/systemd/mpdbackend.service"
    local unit_dst="/etc/systemd/system/mpdbackend.service"
    local env_dst="/etc/mpdbackend.env"

    log "Install systemd unit to ${unit_dst} (sudo)"
    require_command sudo

    sed \
        -e "s|^User=.*|User=${RUN_USER}|" \
        -e "s|^Group=.*|Group=${RUN_GROUP}|" \
        -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
        "${unit_src}" | sudo tee "${unit_dst}" >/dev/null

    if [[ ! -f "${env_dst}" ]]; then
        sudo install -m 600 "${INSTALL_DIR}/mpdbackend.env" "${env_dst}"
        log "Created ${env_dst}"
    else
        log "Keeping existing ${env_dst}"
    fi

    sudo systemctl daemon-reload
    sudo systemctl enable mpdbackend.service
    log "Enable with: sudo systemctl start mpdbackend"
}

verify_install() {
    log "Verify imports in venv"
    "${INSTALL_DIR}/venv/bin/python" - <<'PY'
from PIL import Image
from mpd import MPDClient
from paho.mqtt import client as mqtt_client
print("OK: Pillow, python-mpd2, paho-mqtt")
PY
}

main() {
    require_command install
    require_command sed
    validate_source_dir

    log "Source:  ${SOURCE_DIR}"
    log "Target:  ${INSTALL_DIR}"
    log "User:    ${RUN_USER}"

    copy_server_files
    write_local_env
    setup_python_venv
    verify_install

    if [[ "${INSTALL_SYSTEMD}" -eq 1 ]]; then
        install_systemd_unit
    fi

    cat <<EOF

Installation complete.

  Start manually:
    ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/mpdbackend.py

  Health check:
    curl http://127.0.0.1:4533/health

  Web player:
    http://127.0.0.1:4533/

  Env file:
    ${INSTALL_DIR}/mpdbackend.env

  Next steps:
    1. Edit ${INSTALL_DIR}/channels.json
    2. Edit ${INSTALL_DIR}/mpdbackend.env (MQTT, MPD socket, public URL)
    3. Put logos in ${INSTALL_DIR}/data/logos/  (channel_0.png, channel_1.png, ...)
EOF

    if [[ "${INSTALL_SYSTEMD}" -eq 0 ]]; then
        cat <<EOF
    4. Optional systemd install:
         cd "${SOURCE_DIR}" && ./install/install.sh --systemd
EOF
    else
        cat <<EOF
    4. Start service:
         sudo systemctl restart mpdbackend
         sudo systemctl status mpdbackend
EOF
    fi
}

main "$@"
