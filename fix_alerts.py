import re
content = open("alerts.py", encoding="utf-8").read()
new_fn = """
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
    lines = [
        "Engine: " + str(engine),
        "Conviction: " + str(conv),
        "Entry: " + str(entry),
        "Stop: " + str(sl),
        "Target: " + str(tp),
        "RR: " + str(rr),
        "Regime: " + str(regime),
        "Tier: " + str(tier),
    ]
    rows_html = "".join(
        "<tr><td style=chr34padding:2px 12px 2px 0;color:#94a3b8chr34>" + l.split(":")[0] + "</td>"
        + "<td style=chr34color:#d6e0efchr34>" + ":".join(l.split(":")[1:]).strip() + "</td></tr>"
        for l in lines
    )
    div  = "<div style=chr34font-family:monospace;border-left:4px solid " + gc + ";padding:12px 16px;margin:10px 0;background:#0d1117;color:#d6e0ef;chr34>"
    h1   = "<div style=chr34font-size:18px;font-weight:700;color:" + gc + "chr34>" + grade + "-GRADE" + qtag + ctag + "</div>"
    h2   = "<div style=chr34font-size:16px;margin:4px 0chr34><span style=chr34color:" + bc + ";font-weight:700chr34>" + pair + " " + bias + "</span> - " + tier + "</div>"
    tbl  = "<table style=chr34font-size:13px;margin-top:8px;border-collapse:collapsechr34>" + rows_html + "</table>"
    return div + h1 + h2 + tbl + "</div>"
"""
# Replace chr34 placeholder with actual double quotes
new_fn = new_fn.replace("chr34", chr(34))
base = content[:content.find("def _signal_html")]
rest = content[content.find("def fire_alerts"):]
final = base + new_fn + "\n" + rest
open("alerts.py", "w", encoding="utf-8").write(final)
print("Done, length:", len(final))
