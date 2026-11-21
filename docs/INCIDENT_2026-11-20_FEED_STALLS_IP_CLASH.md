# Incident 2026-11-19/20 — Feed stalls on Dhivakar's Mac: an IP address clash with the TV

<!-- INCIDENT_IP_CLASH_20261120 -->

**Cause in one line:** the OnePlus TV "Viyan Tv" has a static IP of 192.168.1.6, which is
inside the Airtel router's DHCP pool. When the TV was off, the router leased .6 to Dhivakar's
Mac. When the TV came back on, both used .6, so part of the Mac's traffic (Zerodha ticks
included) went to the TV and every WebSocket on the Mac went silent.

Not an app bug, not Zerodha, not Wi-Fi signal, not Kaspersky (all suspected and ruled out, see below).

## What happened

| When | Event |
|---|---|
| 2, 5, 11 Nov | Watchdog reconnects on Dhivakar's Mac only (4, 6, 13); nobody connected them yet |
| 19 Nov 11:49–12:02 | Six stalls, 26 watchdog reconnects across TMA, TMA2, Velvet, SCALP_V1/V3/V5 and the index feeder. Telegram: "no NIFTY tick for 125s — forcing a WebSocket reconnect" |
| 19 Nov | Anbu's Mac on the same Wi-Fi: zero reconnects that day and on all of his bad days |
| 20 Nov 14:21–14:32 | Stalls again on 12.0.8 (watchdog now acts at 30 s). 42 reconnects by 14:32; feed took 40–116 s to return after each reconnect |
| 20 Nov ~14:30 | Anbu: "I think this is happening whenever I switch on my TV" |
| 20 Nov 14:32 | Dhivakar's Mac moved off .6 (manual 192.168.1.60). All feeds back at 14:32:51; no reconnects after |
| 20 Nov ~15:45 | Mac moved to 192.168.1.250 (router's DHCP page cannot be saved — Airtel-locked). Clean through the close |

## How it was found

1. Same Wi-Fi, two Macs, only one stalls → the shared internet link and Zerodha are fine; look at that one machine.
2. His Mac said its IP was 192.168.1.6 (System Settings → Wi-Fi → Details → TCP/IP).
3. From Anbu's Mac, `arp -an` showed 192.168.1.6 at **a0:91:a2:29:22:9b**, not his Wi-Fi address
   1e:bf:74:07:53:49. Two devices, one address.
4. Port 8008 on that device answered as a Chromecast: `curl http://192.168.1.6:8008/ssdp/device-desc.xml`
   → friendlyName "Viyan Tv", manufacturer OnePlus. The TV's IP was set by hand during the CleanMyTV work.
5. His Mac stayed reachable over IPv6 link-local (`ssh dhivakara@fe80::…%en0`, host key checked
   against the saved one), which bypasses the IPv4 clash.

## What was ruled out, and why we were misled

| Suspect | Why it looked likely | Why it was not |
|---|---|---|
| Zerodha WebSocket outage | Every socket on the Mac dropped at once | Anbu's Mac on the same network had no stall at the same minutes |
| Wi-Fi signal | Laptop on someone else's Wi-Fi | −49 dBm / 433 Mbps on 5 GHz; router ping 1.7 ms |
| Kaspersky Anti-Virus (19 Nov) | It proxies every connection; its error rate rose 16 → 40–107/min during the stalls | The errors were a symptom (connections dying), not the cause. Trusting Zerodha in Kaspersky changed nothing; the stalls stopped only when the IP clash ended |

## What changed in the app along the way

| Tag | Release | What |
|---|---|---|
| WS_WATCHDOG_FAST_20261119 | 12.0.6 | Silent Kite socket reconnected after 30 s (spot) / 45 s (option sockets), checked every 5 s, instead of 120 s / 30 s. Cuts each stall from ~2.5 min to under a minute, whatever the cause |
| MACHINE_ID_STABLE_20261119 | 12.0.8 | Licence lockout found during this incident: after a battery-empty standby the Mac's interface order changed, `uuid.getnode()` picked a random MAC, and the licence said "another machine". The id is now kept while its MAC is present |
| MACHINE_ID_HW_20261119 | 12.0.8 | Licence id moved to the hardware UUID (the T2 bridge MAC is the same on every Intel T2 Mac) |

## Learnings

1. **A static IP inside the router's DHCP pool is a time bomb.** The router does not know the
   address is taken and will lease it to the next device while the static one is off. Either
   reserve the address in the router (DHCP reservation) or pick a static IP outside the pool.
2. **One machine stalls, another on the same Wi-Fi does not → check for an IP clash before
   anything else.** It takes a minute (see RUNBOOK.md, "Feed stalls on one machine only").
3. **"Connected but no ticks" on every socket at once is a network-path symptom**, not a strategy
   or engine bug. The app's watchdog heals it; the cause is outside the app.
4. **Correlate with what changed in the room.** The user's observation (the TV) broke the case;
   logs alone pointed at Kaspersky.
5. **Trading machines should have a fixed, known address** on any network they trade from.

## Current state and open items

- TV: static 192.168.1.6 (kept; Anbu's phone remote uses it).
- Dhivakar's Mac: "Using DHCP with manual address" 192.168.1.250. The router hands out low
  addresses first, so .250 is unlikely to be leased; .6 can still be leased to another device
  while the TV is off (affects only that device and the TV).
- Router (Airtel AAP4221ZY, firmware AAP4221ZY_R_1.11): DHCP pool 192.168.1.2 + 253; the LAN
  page has no Save/Apply. Ask Airtel (121) to start the pool at 192.168.1.10 (size 245). → TD-068
- QA Register: DEF-069 (stalls), DEF-070 (licence lockout), TD-055 (closed), TD-068 (router).
