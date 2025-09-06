# Deployment Guide

This guide explains how to update and redeploy the LEGO Arm application on the Raspberry Pi. It covers three components:

- **Web UI** (`web/index.html`)
- **REST server** (`lego_arm_master.py`)
- **systemd service** (`legoarm.service`)

The instructions assume:

- The project resides at `/home/michiel/lego-arm` on the Pi.
- You have SSH access and `sudo` privileges on the device.
- The service runs under systemd as `legoarm.service`.

---

## General workflow

1. Copy or pull the new file(s) to the Pi.
2. Validate syntax where applicable.
3. Restart or reload the service.
4. Verify operation via `systemctl` and HTTP health checks.

---

## Updating the Web UI (`index.html`)

1. **Copy the file**
   ```bash
   scp web/index.html pi@<pi-host>:/home/michiel/lego-arm/web/index.html
   ```
   (Or edit directly on the Pi.)

2. **No restart needed** – `lego_arm_master.py` reads `index.html` from disk on every request.

3. **Test in the browser**
   ```bash
   curl http://<pi-host>:8000/
   ```
   or open the URL in a web browser or via your ngrok address.

4. **Revert if necessary**
   ```bash
   git checkout -- web/index.html
   ```

---

## Updating the REST server (`lego_arm_master.py`)

1. **Replace the file**
   - Copy from your workstation:
     ```bash
     scp lego_arm_master.py pi@<pi-host>:/home/michiel/lego-arm/lego_arm_master.py
     ```
   - Or edit directly on the Pi:
     ```bash
     cd /home/michiel/lego-arm
     rm lego_arm_master.py
     nano lego_arm_master.py   # paste new code, then Ctrl+O Ctrl+X to save
     ```

2. **Validate syntax on the Pi**
   ```bash
   python3 -m py_compile /home/michiel/lego-arm/lego_arm_master.py
   ```

3. **Restart the service**
   ```bash
   sudo systemctl restart legoarm
   ```

4. **Verify**
   ```bash
   sudo systemctl status legoarm --no-pager
   curl http://localhost:8000/v1/health
   ```

5. **Revert if necessary**
   ```bash
   git checkout -- lego_arm_master.py
   sudo systemctl restart legoarm
   ```

---

## Updating the systemd unit (`legoarm.service`)

1. **Copy the file**
   ```bash
   scp systemd/legoarm.service pi@<pi-host>:/tmp/legoarm.service
   sudo cp /tmp/legoarm.service /etc/systemd/system/legoarm.service
   ```

2. **Reload systemd and restart**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart legoarm
   sudo systemctl enable legoarm  # optional; ensures service starts on boot
   ```

3. **Verify**
   ```bash
   sudo systemctl status legoarm --no-pager
   sudo journalctl -u legoarm -n 100 --no-pager
   ```

4. **Revert if necessary**
   Restore the previous unit file and repeat the `daemon-reload` and `restart` steps.

---

## Resetting the entire deployment

1. Stop the service:
   ```bash
   sudo systemctl stop legoarm
   ```

2. Restore repository versions of core files:
   ```bash
   git checkout -- lego_arm_master.py web/index.html systemd/legoarm.service
   ```

3. Reload and restart:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start legoarm
   ```

4. Verify health:
   ```bash
   curl http://localhost:8000/v1/health
   ```

---

## Troubleshooting tips

- Follow logs live:
  ```bash
  sudo journalctl -u legoarm -f
  ```
- Run the server in the foreground for debugging:
  ```bash
  USE_FAKE_MOTORS=1 PORT=8000 python3 lego_arm_master.py
  ```
- Check port usage:
  ```bash
  sudo ss -ltnp | grep ':8000'
  ```

This document should allow you to update any component and bring the system back online quickly.
