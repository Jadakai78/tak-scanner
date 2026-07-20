@@
 def load_signal_bus():
     """Load bus from local disk — fall back to CF KV seed if file missing."""
     if not SIGNAL_BUS.exists():
         _seed_from_kv()
     try:
         data = json.loads(SIGNAL_BUS.read_text())
     except (FileNotFoundError, json.JSONDecodeError):
         data = {"signals": [], "rts_signals": []}
+
+    # --- Debug: write read diagnostic for runtime inspection -----------------
+    try:
+        try:
+            mtime = datetime.utcfromtimestamp(SIGNAL_BUS.stat().st_mtime).isoformat() + 'Z'
+        except Exception:
+            mtime = None
+        read_debug = {
+            "read_path": str(SIGNAL_BUS.resolve()),
+            "read_at": datetime.now(timezone.utc).isoformat(),
+            "file_mtime": mtime,
+            "lastscan": data.get("lastscan") or data.get("last_scan") or (data.get("tak") or {}).get("lastscan"),
+            "signals_count": len(data.get("signals", []) or []),
+        }
+        dbgpath = SIGNAL_BUS.parent / "signal_bus.read_debug.json"
+        try:
+            dbgpath.write_text(json.dumps(read_debug, ensure_ascii=False, indent=2), encoding="utf-8")
+        except Exception:
+            _aging_logger.debug("Failed to write read debug file %s", dbgpath)
+    except Exception:
+        pass
+    # -------------------------------------------------------------------------
@@
 def push_to_cf():
@@
     if not bus_path:
@@
         return
 
     logger.info("push_to_cf: using signal bus file: %s", bus_path)
+    # --- Debug: record which candidate the scheduler chose ------------------
+    try:
+        try:
+            mtime_iso = datetime.utcfromtimestamp(bus_path.stat().st_mtime).isoformat() + 'Z'
+        except Exception:
+            mtime_iso = None
+        dbg = {
+            "selected_path": str(bus_path.resolve()),
+            "selected_at": datetime.now(timezone.utc).isoformat(),
+            "file_mtime": mtime_iso,
+        }
+        debug_path = MODULE_DIR / "signal_bus.push_debug.json"
+        try:
+            debug_path.write_text(json.dumps(dbg, ensure_ascii=False, indent=2), encoding="utf-8")
+        except Exception:
+            logger.debug("Failed to write scheduler push debug %s", debug_path)
+    except Exception:
+        pass
+    # -------------------------------------------------------------------------
     try:
         payload = bus_path.read_bytes()
         req = urllib.request.Request(
