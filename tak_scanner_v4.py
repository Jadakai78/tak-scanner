from scanner.orchestrator import TakScannerV4

if __name__ == "__main__":
    scanner = TakScannerV4()
    results = scanner.run_scan()
    print(
        f"Scan complete: {results['live']} live, "
        f"{results['caution']} caution, "
        f"{results['killed']} killed "
        f"({results['duration']}s)"
    )
