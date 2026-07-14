from tak_scanner_v3 import TakScannerV8
scanner = TakScannerV8()
results = scanner.run_scan()
signals = results.get("signals", [])
s10s = [s for s in signals if s.get("engine_id") == "S10"]
print("TOTAL=" + str(len(signals)))
print("S10=" + str(len(s10s)))
for s in signals:
    print("SIG " + str(s.get("engine_id")) + " " + str(s.get("pair")) + " " + str(s.get("direction")) + " " + str(s.get("confidence")))

