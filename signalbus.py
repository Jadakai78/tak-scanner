@@
 class SignalBus:
@@
     def _atomic_write(self, bus: Dict[str, Any]) -> None:
         try:
             payload = json.dumps(bus, indent=2, default=str)
@@
-            self.tmp_path.write_text(payload, encoding="utf-8")
-            os.replace(str(self.tmp_path), str(self.path))
-            logger.info(
-                "Signal bus written: %d signals, %d killed.",
-                len(bus.get("signals", [])),
-                len(bus.get("killedsignals", [])),
-            )
+            self.tmp_path.write_text(payload, encoding="utf-8")
+            os.replace(str(self.tmp_path), str(self.path))
+
+            # After successful replace, collect mtime and write a debug helper file
+            try:
+                mtime = None
+                try:
+                    mtime = datetime.utcfromtimestamp(self.path.stat().st_mtime).isoformat() + 'Z'
+                except Exception:
+                    mtime = None
+                debug = {
+                    "written_path": str(self.path.resolve()),
+                    "written_at": datetime.now(timezone.utc).isoformat(),
+                    "file_mtime": mtime,
+                    "signals_count": len(bus.get("signals", [])),
+                    "killed_count": len(bus.get("killedsignals", [])),
+                }
+                dbgpath = self.path.parent / "signal_bus.write_debug.json"
+                try:
+                    dbgpath.write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
+                except Exception:
+                    logger.debug("Failed to write debug helper file %s", dbgpath)
+            except Exception:
+                pass
+
+            logger.info(
+                "Signal bus written: %d signals, %d killed. path=%s",
+                len(bus.get("signals", [])),
+                len(bus.get("killedsignals", [])),
+                str(self.path),
+            )
         except OSError as exc:
             logger.error("Atomic write failed: %s", exc)
             raise
