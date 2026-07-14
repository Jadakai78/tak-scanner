c = open("alerts.py", encoding="utf-8").read()
q = chr(34)
sq = chr(39)
# Fix rows_html line - swap inner double quotes to single
old1 = q + "<tr><td style=" + q + "padding:2px 12px 2px 0;color:#94a3b8" + q + ">" + q
new1 = q + "<tr><td style=" + sq + "padding:2px 12px 2px 0;color:#94a3b8" + sq + ">" + q
old2 = q + "<td style=" + q + "color:#d6e0ef" + q + ">" + q
new2 = q + "<td style=" + sq + "color:#d6e0ef" + sq + ">" + q
old3 = q + "<div style=" + q + "font-family:monospace;border-left:4px solid " + q
new3 = q + "<div style=" + sq + "font-family:monospace;border-left:4px solid " + q
old4 = q + ";padding:12px 16px;margin:10px 0;background:#0d1117;color:#d6e0ef;" + q + ">"
new4 = q + ";padding:12px 16px;margin:10px 0;background:#0d1117;color:#d6e0ef;" + sq + ">"
old5 = q + "<div style=" + q + "font-size:18px;font-weight:700;color:"
new5 = q + "<div style=" + sq + "font-size:18px;font-weight:700;color:"
old6 = q + ">" + q + " + grade"
new6 = q + ">" + q + " + grade"
old7 = q + "<div style=" + q + "font-size:16px;margin:4px 0" + q + "><span style=" + q + "color:"
new7 = q + "<div style=" + sq + "font-size:16px;margin:4px 0" + sq + "><span style=" + sq + "color:"
old8 = q + ";font-weight:700" + q + ">"
new8 = q + ";font-weight:700" + sq + ">"
old9 = q + "<table style=" + q + "font-size:13px;margin-top:8px;border-collapse:collapse" + q + ">"
new9 = q + "<table style=" + sq + "font-size:13px;margin-top:8px;border-collapse:collapse" + sq + ">"
for old, new in [(old1,new1),(old2,new2),(old3,new3),(old4,new4),(old5,new5),(old7,new7),(old8,new8),(old9,new9)]:
    c = c.replace(old, new)
open("alerts.py", "w", encoding="utf-8").write(c)
print("done, len:", len(c))
