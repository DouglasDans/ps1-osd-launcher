#!/bin/bash
# Roda no Pi após git clone/pull

set -e

INSTALL_DIR="/home/douglasdans/ps1-osd-laucher"

echo ">>> Instalando dependências..."
sudo apt install -y python3-pygame python3-numpy python3-venv

echo ">>> Criando venv (reaproveita pygame do apt)..."
python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install "ffpyplayer>=4.5.3"

echo ">>> Copiando services..."
sudo cp "$INSTALL_DIR/systemd/ps1-osd-laucher.service" /etc/systemd/system/
sudo cp "$INSTALL_DIR/systemd/ps1-osd-update.service" /etc/systemd/system/

echo ">>> Permissões sudo (shutdown/reboot/restart do launcher)..."
echo "douglasdans ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot, /usr/bin/systemctl restart ps1-osd-laucher, /usr/bin/cp $INSTALL_DIR/systemd/ps1-osd-laucher.service /etc/systemd/system/ps1-osd-laucher.service, /usr/bin/cp $INSTALL_DIR/systemd/ps1-osd-update.service /etc/systemd/system/ps1-osd-update.service, /usr/bin/systemctl daemon-reload" | sudo tee /etc/sudoers.d/ps1-osd-laucher > /dev/null

echo ">>> Boot silencioso (firmware)..."
BOOT_CFG=/boot/firmware/config.txt
BOOT_CMD=/boot/firmware/cmdline.txt
if [ -f "$BOOT_CFG" ]; then
    grep -q "^disable_splash=1" "$BOOT_CFG" || echo "disable_splash=1" | sudo tee -a "$BOOT_CFG" > /dev/null
fi
if [ -f "$BOOT_CMD" ]; then
    [ -f "$BOOT_CMD.bak" ] || sudo cp "$BOOT_CMD" "$BOOT_CMD.bak"
    for flag in quiet loglevel=3 logo.nologo vt.global_cursor_default=0; do
        grep -qwF -- "$flag" "$BOOT_CMD" || sudo sed -i "s|\$| $flag|" "$BOOT_CMD"
    done
fi

echo ">>> Habilitando e iniciando services..."
sudo systemctl daemon-reload
sudo systemctl enable ps1-osd-laucher
sudo systemctl enable ps1-osd-update
sudo systemctl start ps1-osd-laucher

echo ">>> Pronto! Status:"
sudo systemctl status ps1-osd-laucher
