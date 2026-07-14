c = open("alerts.py", encoding="utf-8").read()
start = c.find("def _signal_html")
end = c.find("def fire_alerts")
new_fn = open("signal_html.py", encoding="utf-8").read()
final = c[:start] + new_fn + chr(10) + c[end:]
open("alerts.py", "w", encoding="utf-8").write(final)
print("done, len:", len(final))
