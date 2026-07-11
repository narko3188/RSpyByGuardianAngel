#!/usr/bin/env python3
"""
SerbiaTracker — USB Modem Cell Tower Scanner
Connects to a 4G/LTE USB modem, reads AT commands for real RSSI/TA,
and sends tower data to the SerbiaTracker API for real geolocation.

Requirements: pip install pyserial requests
Usage: python3 modem_scanner.py --port /dev/ttyUSB0 --api http://localhost:8000

Supported modems: Huawei E3372/E8372, ZTE MF79/MF833, Quectel EC25
"""

import serial, time, json, re, sys, argparse

class ModemScanner:
    def __init__(self, port="/dev/ttyUSB0", baud=115200, timeout=3):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.cells = []
        time.sleep(1)
        # Clear any buffer
        self.ser.reset_input_buffer()
        
    def at(self, cmd, wait=0.5):
        """Send AT command, return response lines"""
        self.ser.write(f"{cmd}\r\n".encode())
        time.sleep(wait)
        lines = []
        while self.ser.in_waiting:
            try:
                line = self.ser.readline().decode().strip()
                if line:
                    lines.append(line)
            except:
                pass
        return lines
    
    def get_operator(self):
        """AT+COPS? → MNC"""
        lines = self.at("AT+COPS?")
        for l in lines:
            m = re.search(r'COPS:\s*\d+,(?:\d+,)?"(\d{5,6})"', l)
            if m:
                code = m.group(1)
                return {"mcc": code[:3], "mnc": code[3:]}
        return None
    
    def get_signal(self):
        """AT+CSQ → RSSI"""
        lines = self.at("AT+CSQ")
        for l in lines:
            m = re.search(r'CSQ:\s*(\d+)', l)
            if m:
                rssi_val = int(m.group(1))
                dbm = -113 + (rssi_val * 2) if rssi_val < 99 else None
                return {"rssi_raw": rssi_val, "rssi_dbm": dbm}
        return None
    
    def get_registration(self):
        """AT+CREG? → LAC + CellID + ACT"""
        self.at("AT+CREG=2")  # Enable LAC+CI
        lines = self.at("AT+CREG?")
        for l in lines:
            m = re.search(r'CREG:\s*\d+,(\d+)(?:,"([0-9A-F]+)","([0-9A-F]+)",(\d+))?', l)
            if m and m.group(2):
                lac = int(m.group(2), 16)
                cell_id = int(m.group(3), 16)
                act = int(m.group(4)) if m.group(4) else 0
                return {"lac": lac, "cell_id": cell_id, "act": act, "status": int(m.group(1))}
        return None
    
    def get_lte_serving(self):
        """AT+QENG='servingcell' → LTE detailed info"""
        lines = self.at('AT+QENG="servingcell"', wait=2)
        for l in lines:
            if "QENG:" in l:
                parts = l.split(",")
                if len(parts) >= 7:
                    return {
                        "state": parts[1].strip('"'),
                        "duplex": parts[2].strip('"'),
                        "band": parts[3].strip('"'),
                        "bandwidth": parts[4].strip('"'),
                        "earfcn": parts[5].strip('"'),
                        "pci": parts[6].strip('"'),
                        "rsrp": int(parts[7].strip('"')) if len(parts) > 7 and parts[7].strip('"').lstrip('-').isdigit() else None,
                        "rsrq": int(parts[8].strip('"')) if len(parts) > 8 and parts[8].strip('"').lstrip('-').isdigit() else None,
                        "tac": parts[-3].strip('"') if len(parts) > 9 else None,
                        "enb": parts[-2].strip('"') if len(parts) > 9 else None,
                    }
        return None
    
    def get_neighbors(self):
        """AT+QENG='neighbourcell' → neighbor list"""
        lines = self.at('AT+QENG="neighbourcell"', wait=3)
        neighbors = []
        for l in lines:
            if "QENG:" in l:
                parts = l.split(",")
                if len(parts) >= 5:
                    neighbors.append({
                        "pci": parts[2].strip('"'),
                        "earfcn": parts[3].strip('"'),
                        "rsrp": int(parts[4].strip('"')) if parts[4].strip('"').lstrip('-').isdigit() else None,
                        "rsrq": int(parts[5].strip('"')) if len(parts) > 5 and parts[5].strip('"').lstrip('-').isdigit() else None,
                    })
        return neighbors
    
    def full_scan(self, phone=None):
        """Complete scan: operator + serving cell + signal + neighbors"""
        operator = self.get_operator()
        signal = self.get_signal()
        reg = self.get_registration()
        lte = self.get_lte_serving()
        neighbors = self.get_neighbors()
        
        scan = {
            "timestamp": time.time(),
            "operator": operator,
            "signal": signal,
            "registration": reg,
            "lte_serving": lte,
            "neighbors": neighbors,
            "measured": True,  # REAL measurement!
        }
        
        # Build TowerInput for SerbiaTracker API
        towers = []
        if reg:
            towers.append({
                "radio": "LTE" if reg.get("act") == 7 else "GSM",
                "mcc": int(operator["mcc"]) if operator else 220,
                "mnc": int(operator["mnc"]) if operator else 0,
                "lac": reg.get("lac", 0),
                "cell_id": reg.get("cell_id", 0),
                "signal_dbm": signal.get("rssi_dbm") if signal else None,
                "ta": 0,  # Not available from basic AT
            })
        
        scan["towers"] = towers
        return scan


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Modem serial port")
    parser.add_argument("--api", default="http://localhost:8000", help="SerbiaTracker API")
    parser.add_argument("--phone", default=None, help="Target phone for API submit")
    parser.add_argument("--interval", type=int, default=5, help="Scan interval in seconds")
    args = parser.parse_args()
    
    scanner = ModemScanner(port=args.port)
    print(f"SerbiaTracker Modem Scanner — {args.port}")
    print(f"Operator: {scanner.get_operator()}")
    
    while True:
        scan = scanner.full_scan()
        print(json.dumps(scan, indent=2, ensure_ascii=False))
        
        if args.phone and args.api and scan["towers"]:
            import requests
            try:
                r = requests.post(f"{args.api}/api/v1/geolocate", json={
                    "phone": args.phone,
                    "towers": scan["towers"],
                    "mnc": scan["operator"]["mnc"] if scan["operator"] else None
                }, timeout=10)
                print(f"API: {r.status_code} — {r.json().get('location', {}).get('latitude', '?')}")
            except Exception as e:
                print(f"API error: {e}")
        
        time.sleep(args.interval)
