def _signal_html(signal, quiet=False):
    grade  = signal.get("grade", "?")
    pair   = signal.get("pair", "?")
    bias   = signal.get("bias", "?")
    engine = signal.get("engine", "?")
    entry  = signal.get("entry", "?")
    sl     = signal.get("sl", "?")
    tp     = signal.get("tp", "?")
    rr     = signal.get("rr", "?")
    conv   = signal.get("final_conviction", signal.get("conviction", "?"))
    regime = signal.get("regime", "?")
    tier   = signal.get("tier", "?")
    remi   = signal.get("remi_status", "CLEAN")
    gc = {"S": "#f5c518", "A": "#2dd4bf", "B": "#60a5fa", "C": "#94a3b8"}.get(grade, "#fff")
    bc = "#22c55e" if str(bias).upper() == "LONG" else "#ef4444"
    qtag = " [QUIET]" if quiet else ""
    ctag = " CAUTION" if remi == "CAUTION" else ""
    body = "<br>".join([
        "<b>Engine:</b> " + str(engine),
        "<b>Conviction:</b> " + str(conv),
        "<b>Entry:</b> " + str(entry),
        "<b>Stop:</b> " + str(sl),
        "<b>Target:</b> " + str(tp),
        "<b>R:R:</b> " + str(rr),
        "<b>Regime:</b> " + str(regime),
        "<b>Tier:</b> " + str(tier),
    ])
    return (
        "<div style='border-left:4px solid " + gc + ";padding:12px;margin:10px 0;background:#0d1117;color:#d6e0ef;font-family:monospace'>"
        + "<div style='font-size:18px;font-weight:700;color:" + gc + "'>" + grade + "-GRADE" + qtag + ctag + "</div>"
        + "<div style='font-size:16px;color:" + bc + ";font-weight:700;margin:4px 0'>" + pair + " " + bias + " &mdash; " + tier + "</div>"
        + "<div style='font-size:13px;margin-top:8px;line-height:1.8'>" + body + "</div>"
        + "</div>"
    )

def _signal_html(signal, quiet=False):
    grade  = signal.get("grade", "?")
    pair   = signal.get("pair", "?")
    bias   = signal.get("bias", "?")
    engine = signal.get("engine", "?")
    entry  = signal.get("entry", "?")
    sl     = signal.get("sl", "?")
    tp     = signal.get("tp", "?")
    rr     = signal.get("rr", "?")
    conv   = signal.get("final_conviction", signal.get("conviction", "?"))
    regime = signal.get("regime", "?")
    tier   = signal.get("tier", "?")
    remi   = signal.get("remi_status", "CLEAN")
    gc = {"S": "#f5c518", "A": "#2dd4bf", "B": "#60a5fa", "C": "#94a3b8"}.get(grade, "#fff")
    bc = "#22c55e" if str(bias).upper() == "LONG" else "#ef4444"
    qtag = " [QUIET]" if quiet else ""
    ctag = " CAUTION" if remi == "CAUTION" else ""
    body = "<br>".join([
        "<b>Engine:</b> " + str(engine),
        "<b>Conviction:</b> " + str(conv),
        "<b>Entry:</b> " + str(entry),
        "<b>Stop:</b> " + str(sl),
        "<b>Target:</b> " + str(tp),
        "<b>RR:</b> " + str(rr),
        "<b>Regime:</b> " + str(regime),
        "<b>Tier:</b> " + str(tier),
    ])
    s = chr(39)
    return (
        "<div style=" + s + "border-left:4px solid " + gc + ";padding:12px;margin:10px 0;background:#0d1117;color:#d6e0ef;font-family:monospace" + s + ">"
        + "<div style=" + s + "font-size:18px;font-weight:700;color:" + gc + s + ">" + grade + "-GRADE" + qtag + ctag + "</div>"
        + "<div style=" + s + "font-size:16px;color:" + bc + ";font-weight:700;margin:4px 0" + s + ">" + pair + " " + bias + " - " + tier + "</div>"
        + "<div style=" + s + "font-size:13px;margin-top:8px;line-height:1.8" + s + ">" + body + "</div>"
        + "</div>"
    )

