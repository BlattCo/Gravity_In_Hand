# Gravity In Hand (`Gravity_In_Hand`)
**By BlattCo**

Portable USB-C Thumbdrive ARM64 **Google Antigravity** + **Android Modding & Development Suite** engineered for the **Samsung Galaxy S23 Ultra** (`aarch64` / Snapdragon 8 Gen 2) using **Termux**, **Shizuku (`rish`)**, **Local Wireless ADB**, and a **Dual-Mode GUI** (Touchscreen Mobile Deck + Full **Samsung DeX** Linux Desktop).

---

## Architecture Overview

1. **50GB `exFAT` Thumbdrive Partition (`AGY_DRIVE`):**
   - Coexists cleanly alongside existing `ext4` Linux partitions (such as Kali Linux).
   - Stores `AGY_System/` (compressed ARM64 Ubuntu + Antigravity + Android Modding rootfs bundle and `boot-agy.sh` Termux bootstrap) and `AGY_Workspace/` (100% portable live workspace for code, APKs, `AGENT.md`, `PROJECT_STATE.json`, and Google OAuth session state).
2. **ARM64 Antigravity + Android Modding Stack:**
   - Official `linux-arm/Antigravity.tar.gz` & `google-antigravity` (`manylinux_2_17_aarch64`) SDK/CLI (`agy`).
   - Official Google `android` CLI (`linux_arm64`), `adb`, `fastboot`, `apktool`, `jadx` (CLI + GUI), `uber-apk-signer`, `aapt2`, and `smali`/`baksmali`.
3. **Shizuku (`rish`) + Local Wireless ADB Self-Modding Bridge:**
   - Operates on unrooted stock Samsung One UI via persistent Shizuku (`rish`) and localhost Wireless ADB (`UID 2000`).
   - Supports APK extraction (`pm path`), decompilation, patching, signing, installation/downgrade (`pm install -r -d`), carrier/Samsung bloatware disabling (`pm disable-user --user 0`), system settings tweaks, UI hierarchy inspection, and live `logcat`.
4. **Dual-Mode GUI on Top of Terminal:**
   - **BlattCo Mobile & DeX Deck (`src/gravity_gui.py`):** Dark-blueprint GUI with an interactive terminal, Antigravity Agent Console, live `PROJECT_STATE.json` HUD, and one-click Android Modding controls, protected by the mandatory Auto-Shutdown Heartbeat Protocol.
   - **Full Samsung DeX Desktop Mode:** Launches `Termux:X11` + `XFCE4` for a full multi-window PC desktop interface on external displays.
