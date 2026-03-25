# ☀️ Siseli Solar Cloud Home Assistant Bridge

[![Version](https://img.shields.io/badge/version-2.5.0-blue.svg)](CHANGELOG.md)
[![HA Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-green.svg)](https://www.home-assistant.io/)

> **Acknowledgment:** This project is an expanded and generalized fork of the excellent work originally created at [yuraantonov11/siseli-ha](https://github.com/yuraantonov11/siseli-ha). Huge thanks to the original author!

Integrate inverters compatible with the Siseli Solar Cloud (including RWB1 and similar models) into Home Assistant without external clouds. This bridge intercepts the MQTT traffic sent to the Siseli cloud, decodes it, and creates sensors via MQTT Auto-Discovery.

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
1. Copy this repository URL: `https://github.com/yuraantonov11/siseli-ha`
2. In Home Assistant, go to **Settings -> Add-ons -> Add-on Store**.
3. Click the three dots in the top right -> **Repositories**.
4. Paste the URL and click **Add**.

### Step 3: Install & Configure
1. Find **Siseli Inverter Bridge** in the store and click **Install**.
2. Go to the **Configuration** tab.
3. Fill in the required fields:
   * **INVERTER_IP**: The local IP of your inverter (e.g., `192.168.1.139`).
   * **ROUTER_IP**: The local IP of your router (e.g., `192.168.1.1`).
   * **AUTO_INTERCEPT**: Keep `true` to use ARP Spoofing (automatic interception).
4. Go to the **Info** tab, enable **Watchdog**, and click **Start**.

---

## 🛠 How it Works (Technical)

The add-on uses multiple methods for traffic interception. For the inverter to start sending data to this add-on, it needs to "think" it is sending it to the Siseli cloud:

### Option A: ARP Spoofing (Auto-Intercept, Recommended)
With `AUTO_INTERCEPT` enabled, the add-on sends special network packets every 2 seconds, convincing the inverter that your Home Assistant server is the router. The inverter starts sending data to HA instead of the real router. The bridge parses the data and transparently forwards it to the Siseli cloud, so the official mobile app continues to work.

### Option B: DNS Configuration
Configure your router so that requests to the Siseli cloud domain resolve to the local IP address of your Home Assistant.

### Option C: Manual Redirect / Static Route (Legacy)
Create a static route on your router that redirects traffic for the target IP `8.212.18.157` to the IP of your Home Assistant.

---

## 📊 Available Sensors
The following sensors will automatically appear in Home Assistant:
* Grid Voltage & Frequency
* Output (Load) Voltage & Frequency
* Active Load (W) & Percentage (%)
* Battery Voltage & Capacity (%)
* PV (Solar) Power & Voltage
* Inverter Temperature
* Charging Settings (Max charge, Bulk, Float, Cut-off voltage)

---

## 🇺🇦 Українською (Ukrainian)
Цей додаток дозволяє інтегрувати інвертори, сумісні з Siseli Cloud, у Home Assistant без використання зовнішніх хмар (підтримуються бренди Solar of Things, LUMINOUS NEO, PowMr, Taico та інші). Він перехоплює трафік, що йде до хмари Siseli, та автоматично створює сенсори. Повна інструкція з налаштування доступна в розділі README вище (англійською).

### Налаштування інвертора (Методи перехоплення)
Щоб інвертор почав надсилати дані в цей додаток, потрібно, щоб він "думав", що надсилає їх у хмару Siseli:
* **Варіант А: ARP Spoofing (Auto-Intercept)**: Залиште `AUTO_INTERCEPT: true` в налаштуваннях додатку, і він зробить все автоматично.
* **Варіант Б: Налаштування DNS**: Налаштуйте ваш роутер так, щоб запити до домену хмари Siseli резолвились на локальну IP-адресу вашого Home Assistant.
* **Варіант В: Static Route**: Створіть статичний маршрут на роутері, який перенаправляє трафік для IP `8.212.18.157` на IP вашого Home Assistant.

---

## 📄 License
MIT License. Free to use and modify.
