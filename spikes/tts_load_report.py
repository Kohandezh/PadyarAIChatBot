"""Summarise a tts_load_varied.py result file + the monitor CSV into one table."""
import csv, json, statistics, sys

res = json.load(open(sys.argv[1], encoding="utf-8"))
rows = list(csv.DictReader(open(sys.argv[2], encoding="utf-8")))
reqs = res["requests"]

def pct(v):
    if not v: return None
    s = sorted(v); pick = lambda p: s[min(len(s)-1, int(len(s)*p))]
    return dict(min=s[0], p50=pick(.5), p90=pick(.9), p99=pick(.99), max=s[-1],
                mean=statistics.fmean(s))

def line(label, d):
    if not d: print(f"  {label:<22} —"); return
    print(f"  {label:<22} " + "  ".join(f"{k}={v:8.3f}" for k, v in d.items()))

L = res["report"]["sentence_chars"]
print(f"SENTENCES: {len(L)}  min={min(L)} p50={int(statistics.median(L))} "
      f"p90={sorted(L)[int(len(L)*.9)]} max={max(L)} mean={statistics.fmean(L):.0f} chars")
buckets = [(20,50),(50,100),(100,150),(150,200),(200,999)]
print("  length histogram: " + "  ".join(
    f"{a}-{b if b<999 else '+'}:{sum(1 for x in L if a<=x<b)}" for a,b in buckets))

print("\nPER PHASE")
for p in res["report"]["phases"]:
    print(f"\n{p['phase']}  ({p['requests']} req, conc {p['concurrency']})")
    print(f"  wall {p['wall']:.1f}s   ok {p['ok']}  failed {p['failed']}   "
          f"hits {p['hits']}  misses {p['misses']}   {p['throughput']:.2f} req/s")
    line("latency all (s)", p["latency"])
    line("latency hits (s)", p["hit_latency"])
    line("latency misses (s)", p["miss_latency"])

ok = [r for r in reqs if r["ok"]]
print(f"\nOVERALL: {len(reqs)} req, ok {len(ok)}, failed {len(reqs)-len(ok)}, "
      f"hits {sum(1 for r in ok if r['cache']=='hit')}, "
      f"misses {sum(1 for r in ok if r['cache']=='miss')}")
line("all ok latency (s)", pct([r["seconds"] for r in ok]))
line("hits only (s)", pct([r["seconds"] for r in ok if r["cache"]=="hit"]))
line("misses only (s)", pct([r["seconds"] for r in ok if r["cache"]=="miss"]))

miss = [(r["chars"], r["seconds"]) for r in ok if r["cache"]=="miss"]
if len(miss) > 2:
    xs=[m[0] for m in miss]; ys=[m[1] for m in miss]
    r_=statistics.correlation(xs,ys); sl,ic=statistics.linear_regression(xs,ys)
    print(f"\nLENGTH vs LATENCY (misses, n={len(miss)}): pearson r={r_:.3f}  "
          f"{sl*100:.2f}s per 100 chars  intercept {ic:.2f}s")
    print("  note: queued misses inherit wait time, so the burst phase inflates this.")
    for label, sel in (("serial-ish (conc<=1 phases)", lambda r: "CALIB" in r["phase"]),
                       ("mix phase", lambda r: r["phase"].startswith("MIX")),
                       ("burst phase", lambda r: r["phase"].startswith("BURST"))):
        sub=[(r["chars"],r["seconds"]) for r in ok if r["cache"]=="miss" and sel(r)]
        if len(sub)>2:
            x=[a for a,_ in sub]; y=[b for _,b in sub]
            try:
                print(f"    {label:<28} n={len(sub):3d}  r={statistics.correlation(x,y):+.3f}  "
                      f"{statistics.linear_regression(x,y)[0]*100:.2f}s/100ch")
            except Exception: pass

print("\nMACHINE (monitor.csv, %d samples @2s = %.1f min)" % (len(rows), len(rows)*2/60))
cols = [("gpu0_pct","GPU0 util %"),("gpu1_pct","GPU1 util %"),("cpu_pct","CPU %"),
        ("load1","load1"),("mem_used_mb","RAM used MB"),("gpu_temp","GPU temp C"),
        ("gpu0_mem_mb","GPU0 mem MB"),("gpu1_mem_mb","GPU1 mem MB")]
print(f"  {'metric':<14}{'min':>10}{'mean':>10}{'max':>10}")
for key,label in cols:
    v=[float(r[key]) for r in rows if r.get(key) not in (None,"")]
    if v: print(f"  {label:<14}{min(v):>10.1f}{statistics.fmean(v):>10.1f}{max(v):>10.1f}")
tot_mem = float(rows[0]["mem_total_mb"]) if rows else 0
if tot_mem:
    peak=max(float(r["mem_used_mb"]) for r in rows)
    print(f"  RAM peak {peak:.0f} / {tot_mem:.0f} MB = {peak/tot_mem*100:.1f}%")
g0=max(float(r["gpu0_mem_mb"]) for r in rows)
print(f"  GPU0 mem peak {g0:.0f} / 24576 MB = {g0/24576*100:.1f}%")
busy=[r for r in rows if float(r["gpu0_pct"])>0]
print(f"  GPU0 non-idle samples: {len(busy)}/{len(rows)} ({len(busy)/max(1,len(rows))*100:.0f}% of wall)")
