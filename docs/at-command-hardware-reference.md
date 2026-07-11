# SerbiaTracker — Hardware AT Command Reference
# Cell Tower Measurement via USB Modem & Android

## USB Modem AT Commands (Essential for REAL RSSI/TA)

### Basic Signal & Registration
AT+CSQ          # Signal Quality: +CSQ: <rssi>,<ber>
                # RSSI: 0 (-113dBm) → 31 (-51dBm) → 99 (unknown)
                # Convert: dBm = -113 + (rssi * 2)
AT+CREG?        # Registration: +CREG: <n>,<stat>[,<lac>,<ci>,<act>]
                # Use AT+CREG=2 first for LAC+CI in response
AT+CREG=2       # Enable unsolicited + LAC+CellID in response
AT+CESQ         # Extended Signal: +CESQ: <rxlev>,<ber>,<rscp>,<ecno>,<rsrq>,<rsrp>
AT+COPS?        # Current operator: +COPS: <mode>,<format>,<oper>

### LTE Advanced (Quectel EC25/EG25 modems — sold in Serbia)
AT+QENG="servingcell"   # Serving cell: RSRP, RSRQ, SINR, band, PCI, TAC, eNB
AT+QNWINFO              # Network info: ACT, operator, band, channel
AT+QCAINFO              # CA info (carrier aggregation)

### Neighbor Cells (critical for triangulation)
AT+QENG="neighbourcell" # Neighbor cell list: PCI, RSRP, RSRQ, band, eNB
AT+CNETSCAN             # Network scan (SIMCom modems)

### Location
AT+QCELLLOC?            # Quectel: approximate location from serving cell
AT+CIPGSMLOC            # SIMCom: GSM location via cell ID

## Python Libraries
# python-gsmmodem: pip install python-gsmmodem-new
# Gammu: apt install gammu python3-gammu
# ModemManager: busctl/dbus, pymodemm (pip install pymodemm)
# pyserial: pip install pyserial (low-level AT command interface)

## Android Engineering Codes
# Samsung: *#0011# = Service Mode (RSRP, RSRQ, SINR, PCI, TAC, eNB, band)
# Samsung: *#0*#   = Hardware test menu
# All: *#*#4636#*#* = Phone info, signal strength, network type
# MTK:  *#*#3646633#*#* = Engineering mode (full radio access)
# Xiaomi: *#*#6484#*#* = CIT hardware test
# Huawei: *#*#2846579#*#* = Project menu

## Android Apps (raw RSSI/TA per tower)
# Network Cell Info Lite — RSRP/RSRQ/SINR per cell, tower map (PLAY STORE)
# CellMapper — uploads to OpenCellID, shows CELLID, LAC, TAC, PCI
# G-NetTrack Lite — RSRP, RSRQ, SINR, TA, neighbor list, export CSV
# NetMonster — cell info, dual-SIM, neighbor cells, band info
# ALL FOUR expose raw radio data that can be captured programmatically

## Serbian USB Modem Models
# Huawei E3372 (4G, Qualcomm, AT+CREG LAC+CI support, ~25€)
# Huawei E8372 (4G WiFi stick, Qualcomm, same AT support)
# ZTE MF79 (4G, sold by Yettel/A1, Qualcomm chipset)
# ZTE MF833 (4G USB stick)
# Quectel EC25 (mPCIe, full AT+QENG support, used in industrial routers)

## DIY Tower Survey Rig (<100€)
# Components:
# - Huawei E3372 USB modem: 25€ (AT+CREG + LAC+CI + CSQ)
# - Raspberry Pi Zero 2W: 15€ (or any Linux board)
# - Power bank: 10€
# - Python script: pyserial → AT commands → JSON log → upload to SerbiaTracker API
#
# Alternative: Old Android phone + CellMapper app = free tower survey device
#   (logs CELLID, LAC, RSRP, lat/lon, automatically uploads to OpenCellID)

## Key AT Command Flow for Tower Triangulation
# 1. AT+CREG=2 (enable LAC+CI in response)
# 2. AT+COPS? (get operator MNC)
# 3. AT+CSQ (get RSSI)
# 4. AT+QENG="servingcell" (LTE: PCI, eNB, band, RSRP, RSRQ, SINR, TAC)
# 5. AT+QENG="neighbourcell" (neighbor list: PCI, RSRP per neighbor)
# 6. Repeat every 5 seconds, log all cells with timestamps
# 7. Cross-reference LAC+CI with OpenCellID for GPS positions
# 8. Triangulate using RSSI → distance estimation (COST-231 Hata)
