using System;
using System.Linq;

class Sim {
    static int fails = 0;
    static void Check(bool ok, string what){ Console.WriteLine((ok?"  PASS  ":"  FAIL  ")+what); if(!ok) fails++; }

    static Bridge Run(Peer p, int maxSteps, bool reconnect = true){
        var b = new Bridge();
        b.StartConnection(p);
        for (int i = 0; i < maxSteps; i++){
            if (b.Scanning){
                if (!reconnect || b.Connections > 40) break;
                p.Anchor = b.machine.Now + 1650; p.AnchorEvent = 0;   // device re-advertises
                b.StartConnection(p);
            }
            if (!b.machine.Step()) break;
            b.DeliverPendingReply();          // reply lands ~150 us later, after connEventCounter++
        }
        return b;
    }

    static void Case(string name, double trueInterval, int steps, bool expectSurvive, double? expectAdopt){
        Console.WriteLine("\n" + name + "   (peripheral's real interval " + trueInterval.ToString("F0") + " us)");
        var p = new Peer { TrueInterval = trueInterval };
        var b = Run(p, steps);
        Console.WriteLine("      hits=" + p.Hits + " misses=" + p.Misses + " engaged=" + b.EverEngaged
                        + " adopted=" + b.AdoptedInterval.ToString("F0") + " abandons=" + b.Abandons);
        Console.WriteLine("      connections=" + b.Connections + "  CONVERGED AFTER " + (b.AdoptedAtUs/1e6).ToString("F2") + " s of emulated time");
        if (expectSurvive) {
            Check(p.Hits > steps / 8, "link converged and stayed up");
        }
        if (expectAdopt.HasValue)
            Check(Math.Abs(b.AdoptedInterval - expectAdopt.Value) <= 160,
                  "adopted interval within 160 us of " + expectAdopt.Value.ToString("F0"));
    }

    static void Main(){
        Console.WriteLine("=== INERTNESS ON A HEALTHY LINK (this is what the last patch broke) ===");
        var hp = new Peer { TrueInterval = 30000 };
        var h = Run(hp, 1200);
        var d = h.machine.ScheduledDelays.Skip(1).ToList();
        Check(d.All(x => x == 30000), "every scheduled delay == 30000 us (distinct: " + string.Join(",", d.Distinct()) + ")");
        Check(!h.EverEngaged, "interval search NEVER engaged");
        Check(h.AdoptedInterval == 30000.0, "currentIntervalUs never written (still 30000)");
        Check(h.MissStreak == 0, "missStreak stayed 0");
        Check(h.Abandons == 0, "no abandon");
        Check(h.Logs.Count == 0, "no log lines emitted (" + h.Logs.Count + ")");
        Check(hp.Misses == 0, "peer heard every single event (" + hp.Hits + " hits, " + hp.Misses + " misses)");

        Console.WriteLine("\n=== INERTNESS WITH SPORADIC SINGLE DROPS ===");
        // peripheral that randomly deafens for exactly one event now and then
        var b2 = new Bridge(); var p2 = new Peer { TrueInterval = 30000 };
        b2.StartConnection(p2);
        var rnd = new Random(7);
        for (int i = 0; i < 1200 && !b2.Scanning; i++){
            p2.Deaf = rnd.Next(50) == 0;
            if (!b2.machine.Step()) break;
            p2.Deaf = false;
            b2.DeliverPendingReply();
        }
        Check(!b2.EverEngaged, "search never engaged at 2% random single-event loss");
        Check(b2.Abandons == 0, "no abandon");
        Check(p2.Hits > 1100, "peer still heard ~every event (" + p2.Hits + ")");
        Check(b2.AdoptedInterval == 30000.0, "currentIntervalUs never written");

        Console.WriteLine("\n=== THE STALL CASES IT IS MEANT TO FIX ===");
        Case("HEIMAN 4619/4097", 30330, 3000, true, 30330);
        Case("HEIMAN, worst case +1500 us", 31500, 6000, true, 31500);
        Case("IKEA 4476/32774",  30330, 3000, true, 30330);
        Case("Tuya 4701/436",    31121, 3000, true, 31121);
        Case("Tuya 4701/79 on a 39 MHz board", 29538, 3000, true, 29538);

        Console.WriteLine("\n=== TRULY DEAF PEER (must still give up, once) ===");
        var dp = new Peer { TrueInterval = 30000, Deaf = true };
        var db = Run(dp, 3000, false);
        Check(db.Abandons == 1, "abandoned exactly once (" + db.Abandons + ")");
        Check(db.Scanning, "returned to Scanning");

        Console.WriteLine(fails == 0 ? "\nALL CHECKS PASSED" : "\n" + fails + " CHECK(S) FAILED");
        Environment.Exit(fails == 0 ? 0 : 1);
    }
}
