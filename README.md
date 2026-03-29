# ☀️ Siseli Solar Cloud Home Assistant Bridge

[![Version](https://img.shields.io/badge/version-2.5.12-blue.svg)](CHANGELOG.md)
[![HA Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-green.svg)](https://www.home-assistant.io/)

> **⚠️ WARNING:** Do not use this project for your own setup. This repository is purely a testing environment created to experiment with improvements before submitting them back to the original project at [yuraantonov11/siseli-ha](https://github.com/yuraantonov11/siseli-ha).

> **Acknowledgment:** This project is an expanded and generalized fork of the excellent work originally created at [yuraantonov11/siseli-ha](https://github.com/yuraantonov11/siseli-ha). Huge thanks to the original author!

Unleash your Siseli-compatible inverter into Home Assistant — **100% locally and instantly** — without relying on external clouds for HA data. The bridge intercepts MQTT traffic to the Siseli Cloud, decodes it locally, and creates sensors via MQTT Auto-Discovery.

> **🔒 Privacy Note:** Your Home Assistant instance intercepts the data for local use, but it simultaneously transparently forwards the traffic to the Siseli Cloud. This ensures your official mobile app continues to work flawlessly.

---

## 🌟 Supported Brands

This add-on supports a wide range of inverter brands that utilize the Siseli IoT cloud platform, including but not limited to:

- Solar of Things
- LUMINOUS NEO
- SUN WISE
- Queen Tech
- LIB Life
- Sun house
- LeiLing
- SunSaviour
- ECOmenic
- HC solar
- 沐能低碳
- PowMr
- Taico

---

## 🚀 Quick Setup

### Step 1: Prepare Home Assistant

Ensure the official **Mosquitto Broker** add-on is installed and configured:

1. Go to **Settings -> Add-ons -> Add-on Store**.
2. Install **Mosquitto Broker**.
3. Start it and ensure you have an MQTT user created.

### Step 2: Add Repository

1. Copy this repository URL: `https://github.com/fadmaz/siseli-ha`
2. In Home Assistant, go to **Settings -> Add-ons -> Add-on Store**.
3. Click the three dots in the top right -> **Repositories**.
4. Paste the URL and click **Add**.

### Step 3: Install & Configure

1. Find **Siseli Inverter Bridge** in the store and click **Install**.
2. Go to the **Configuration** tab.
3. Fill in the required fields:
   - **INVERTER_IP**: The local IP of your inverter (e.g., `192.168.1.139`).
   - **ROUTER_IP**: The local IP of your router (e.g., `192.168.1.1`).
   - **AUTO_INTERCEPT**: Keep `true` to use ARP Spoofing (automatic interception).
4. Go to the **Info** tab, enable **Watchdog**, and click **Start**.

---

## 🛠 How it Works (Technical)

The add-on uses multiple methods for traffic interception. For the inverter to start sending data to this add-on, it needs to "think" it is sending it to the Siseli cloud:

### Option A: ARP Spoofing (Auto-Intercept, Recommended)

With `AUTO_INTERCEPT` enabled, the add-on tricks the inverter into sending its data to Home Assistant instead of the router. The bridge parses the data and transparently forwards it to the Siseli cloud.

> **⚠️ WARNING:** You are using ARP spoofing, which is a sensitive network technique. It can trigger security alerts on advanced network setups or enterprise routers (like UniFi or pfSense).

### Option B: DNS Configuration

Configure your router so that requests to the Siseli cloud domain resolve to the local IP address of your Home Assistant.

### Option C: Manual Redirect / Static Route (Legacy)

Create a static route on your router that redirects traffic for the target IP `8.212.18.157` to the IP of your Home Assistant.

---

## 📊 Available Sensors

This bridge dynamically extracts and exposes **almost every single sensor and data point available in the official Siseli app** (100+ entities) directly into Home Assistant via MQTT Auto-Discovery.

The exposed data includes:

- **🔋 Battery & BMS Status**
  - Overall Voltage, Capacity (%), Charge/Discharge Currents, Battery Type
  - Remaining Capacity (Ah), Nominal Capacity (Ah), Min/Max Cell Voltages, and individual cell voltages (1-16)
- **⚡ Grid & Load Status**
  - AC Input Voltage & Mains Frequency
  - Active Load (W), Apparent Power (VA), Output Voltage/Frequency, and Load Percentage
- **☀️ PV Panel Status**
  - Daily, Monthly, Yearly, and Total Electricity Generation (kWh)
  - PV1 & PV2 Voltages, Currents, Wattage, and PV Temperatures
- **⚙️ Advanced Device Settings ("More" tab)**
  - Dozens of configuration points including Working Mode (SBU, UTI, etc.), Charging Priority, Output Frequencies
  - Fan Speeds, Warning Lights, Hardware Switches (AC Charging, Main Output Relay)
  - Customizable thresholds (Float Charging Voltage, Low Battery Alarm, Overvoltage Shutdown)
  - Diagnostic booleans (Abnormal Fan Speed, EEPROM errors, Machine Over Temperature)

---

## ❓ Compatibility

Tested on:

- RWB1
- PowMr variants
- Taico variants

_Note: It may work out-of-the-box on other Siseli-based devices listed in the Supported Brands section._

---

## 🧪 Troubleshooting

**No data appearing in Home Assistant?**

- **Check MQTT Connection:** Ensure your Mosquitto broker is running and the add-on logs show a successful connection.
- **Verify Inverter IP:** Double-check that `INVERTER_IP` and `ROUTER_IP` are exactly correct in the configuration.
- **Disable AUTO_INTERCEPT:** If ARP spoofing is blocked by your router, set `AUTO_INTERCEPT` to `false` and try the **DNS Configuration** or **Static Route** methods instead.

---

## 🇺🇦 Українською (Ukrainian)

Цей додаток дозволяє інтегрувати інвертори, сумісні з Siseli Cloud, у Home Assistant без використання зовнішніх хмар (підтримуються бренди Solar of Things, LUMINOUS NEO, PowMr, Taico та інші). Він перехоплює трафік, що йде до хмари Siseli, та автоматично створює сенсори. Повна інструкція з налаштування доступна в розділі README вище (англійською).

---

## 📄 License

MIT License. Free to use and modify.
